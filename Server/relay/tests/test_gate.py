"""The gate, over real sockets: who gets an answer and who gets nothing."""

import asyncio
import json
import socket
import time
import unittest

from aiohttp.test_utils import TestServer

from isobar_relay.app import Relay
from tests.helpers import KEY_A, make_config

ISOBAR = "Isobar/1.8.0 (test)"


def raw_request(port, path="/v1/status", agent=ISOBAR, key=KEY_A, https=True, extra=None, timeout=10):
    """Send one request on a raw socket. Returns (bytes received, seconds until the close)."""
    lines = [f"GET {path} HTTP/1.1", "Host: weather.example.net"]
    if agent:
        lines.append(f"User-Agent: {agent}")
    if key:
        lines.append(f"Authorization: Bearer {key}")
    if https:
        lines.append("X-Forwarded-Proto: https")
    lines += list(extra or [])
    data = ("\r\n".join(lines) + "\r\n\r\n").encode()
    start = time.monotonic()
    got = b""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(data)
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                got += chunk
                if b"\r\n\r\n" in got and b"Content-Length" in got:
                    head, _, body = got.partition(b"\r\n\r\n")
                    length = int(head.split(b"Content-Length:")[1].split(b"\r\n")[0])
                    if len(body) >= length:
                        break
        except ConnectionResetError:
            pass
    return got, time.monotonic() - start


class GateTest(unittest.IsolatedAsyncioTestCase):
    config_changes = {}

    async def asyncSetUp(self):
        cfg = make_config(**self.config_changes)
        self.relay = Relay(cfg)
        self.relay.keys.load_initial()
        self.server = TestServer(self.relay.app(background=False), host="127.0.0.1")
        await self.server.start_server()
        self.port = self.server.port

    async def asyncTearDown(self):
        await self.server.close()

    async def call(self, *args, **kwargs):
        return await asyncio.to_thread(raw_request, self.port, *args, **kwargs)


class Silence(GateTest):
    async def test_ping_answers_isobar_without_a_key(self):
        got, _ = await self.call("/v1/ping", key=None)
        self.assertTrue(got.startswith(b"HTTP/1.1 200"))
        body = json.loads(got.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(body["name"], "Test Server")

    async def test_working_key_gets_status(self):
        got, _ = await self.call("/v1/status")
        self.assertTrue(got.startswith(b"HTTP/1.1 200"), got[:80])
        body = json.loads(got.split(b"\r\n\r\n", 1)[1])
        self.assertIn("capabilities", body)
        self.assertIn(b"Cache-Control: private", got)

    async def test_everything_else_gets_no_bytes_then_a_close(self):
        cases = [
            {"agent": "curl/8.0"},                       # not Isobar
            {"agent": None},                             # no User-Agent at all
            {"key": "wrong-key-wrong-key-wrong"},        # wrong key
            {"key": None},                               # missing key
            {"https": False},                            # plain HTTP
            {"path": "/wp-login.php"},                   # unknown path
            {"path": "/v1/ping", "https": False},        # even ping, over plain HTTP
        ]
        results = await asyncio.gather(*(self.call(**c) for c in cases))
        for case, (got, took) in zip(cases, results):
            self.assertEqual(got, b"", case)
            self.assertGreaterEqual(took, 0.9, case)

    async def test_alerts_without_a_snapshot_is_503_not_an_empty_list(self):
        got, _ = await self.call("/v1/alerts?lat=35&lon=-84")
        self.assertTrue(got.startswith(b"HTTP/1.1 503"), got[:80])


class HoldCap(GateTest):
    config_changes = {"silent_hold_max": 2, "silent_hold_seconds": 3}

    async def test_past_the_cap_new_silent_connections_close_at_once(self):
        held = [asyncio.ensure_future(self.call(agent="bot/1")) for _ in range(2)]
        await asyncio.sleep(0.5)
        got, took = await self.call(agent="bot/2")
        self.assertEqual(got, b"")
        self.assertLess(took, 1.0)
        # Keyed Isobar requests keep getting answers while the cap is full.
        got, _ = await self.call("/v1/ping", key=None)
        self.assertTrue(got.startswith(b"HTTP/1.1 200"))
        for got, took in await asyncio.gather(*held):
            self.assertEqual(got, b"")
            self.assertGreaterEqual(took, 2.5)


class WrongKeyLimit(GateTest):
    config_changes = {"auth_fail_limit": 3, "silent_hold_seconds": 0}

    async def test_too_many_wrong_keys_silences_the_address(self):
        ip = ["X-Real-IP: 203.0.113.9"]
        for _ in range(4):
            got, _ = await self.call(key="wrong-key-wrong-key-wrong", extra=ip)
            self.assertEqual(got, b"")
        got, _ = await self.call(extra=ip)
        self.assertEqual(got, b"", "the right key from a blocked address still gets nothing")
        got, _ = await self.call("/v1/ping", key=None, extra=ip)
        self.assertEqual(got, b"")
        got, _ = await self.call(extra=["X-Real-IP: 203.0.113.10"])
        self.assertTrue(got.startswith(b"HTTP/1.1 200"), "other addresses are unaffected")


class RateLimit(GateTest):
    config_changes = {"rate_limit": 10}

    async def test_429_only_for_a_working_key(self):
        statuses = []
        for _ in range(12):
            got, _ = await self.call("/v1/status")
            statuses.append(got[9:12])
        self.assertIn(b"429", statuses)
        got, _ = await self.call("/v1/status")
        self.assertIn(b"Retry-After:", got)


class NoHttps(GateTest):
    config_changes = {"require_https": False}

    async def test_lan_server_answers_plain_http(self):
        got, _ = await self.call("/v1/status", https=False)
        self.assertTrue(got.startswith(b"HTTP/1.1 200"))


if __name__ == "__main__":
    unittest.main()
