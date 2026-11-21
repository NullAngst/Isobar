"""Isobar Servers from the app's side: the walk down the list, stale answers,
cooldowns, a key that isn't answering, and going direct."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from tests import helpers  # noqa: F401
from isobar import servers, settings
from isobar.net import FetchError


class FakeRelay:
    """A tiny stand-in for an Isobar Server. mode: ok, stale, silent (no
    answer to keyed requests, ping still answers), down (nothing answers), 429."""

    def __init__(self, name, key="right-key-right-key"):
        self.name = name
        self.key = key
        self.mode = "ok"
        self.hits = []
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                relay.hits.append(self.path)
                ua = self.headers.get("User-Agent") or ""
                if not ua.startswith("Isobar/") or relay.mode == "down":
                    time.sleep(2)
                    self.close_connection = True
                    return
                if self.path.startswith("/v1/ping"):
                    return self.reply({"service": "isobar-server", "name": relay.name})
                if self.headers.get("Authorization") != f"Bearer {relay.key}" or relay.mode == "silent":
                    time.sleep(2)
                    self.close_connection = True
                    return
                if relay.mode == "429":
                    self.send_response(429)
                    self.send_header("Retry-After", "120")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.startswith("/v1/status"):
                    return self.reply({"name": relay.name, "capabilities": {"radar": False}, "datasets": {}})
                self.reply({"from": relay.name}, stale=relay.mode == "stale")

            def reply(self, data, stale=False):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Isobar-Stale", "1" if stale else "0")
                self.send_header("X-Isobar-Fetched-At", "2026-11-20T12:00:00Z")
                self.send_header("X-Isobar-Source", "openmeteo-local")
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.address = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def entry(self, key=None):
        return {"label": self.name, "address": self.address, "key": key or self.key, "enabled": True}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Walk(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(servers, "TIMEOUT", (1, 1))
        self.patch.start()
        mock.patch.object(servers, "PING_TIMEOUT", (1, 1)).start()
        self.a = FakeRelay("VPS")
        self.b = FakeRelay("Home")
        servers._states.clear()
        servers.forget_memo()
        self.direct_calls = 0

    def tearDown(self):
        mock.patch.stopall()
        self.a.close()
        self.b.close()
        settings.save({"servers": [], "servers_direct": True})
        servers._states.clear()
        servers.forget_memo()

    def use(self, *entries, direct=True):
        settings.save({"servers": list(entries), "servers_direct": direct})
        servers.forget_memo()

    def direct(self):
        self.direct_calls += 1
        return {"from": "direct"}, time.time()

    def fetch(self, path="/v1/forecast", stale_after=None):
        servers.forget_memo()
        return servers.fetch(path, {"latitude": 35}, self.direct, ttl=60, stale_after=stale_after)

    def test_no_servers_is_direct_and_nothing_else(self):
        self.use()
        got = self.fetch()
        self.assertEqual((got.value, got.via), ({"from": "direct"}, "direct"))
        self.assertEqual(self.a.hits, [])

    def test_first_down_second_answers_then_cooldown(self):
        self.a.httpd.shutdown()
        self.a.httpd.server_close()   # refused connections
        self.use(self.a.entry(), self.b.entry())
        for _ in range(3):
            got = self.fetch()
            self.assertEqual(got.via, "Home")
        state = servers._state(self.a.entry())
        self.assertGreater(state.cooldown_until, time.time(), "three failures in a row: cooldown")
        self.assertEqual(servers.states()[0]["state"], "cooldown")

    def test_stale_first_fresh_second(self):
        self.a.mode = "stale"
        self.use(self.a.entry(), self.b.entry())
        got = self.fetch()
        self.assertEqual(got.via, "Home")
        self.assertFalse(got.stale)

    def test_both_down_direct_on_then_off(self):
        self.a.mode = self.b.mode = "down"
        self.use(self.a.entry(), self.b.entry())
        got = self.fetch()
        self.assertEqual(got.via, "direct")
        self.use(self.a.entry(), self.b.entry(), direct=False)
        servers._states.clear()
        with self.assertRaises(FetchError):
            servers.fetch("/v1/other", {}, self.direct, ttl=60)
        self.assertEqual(self.direct_calls, 1, "direct off means no direct call")

    def test_only_stale_answers_give_the_freshest(self):
        self.a.mode = self.b.mode = "stale"
        self.use(self.a.entry(), self.b.entry(), direct=False)
        got = self.fetch()
        self.assertTrue(got.stale)
        self.assertIn(got.via, ("VPS", "Home"))

    def test_wrong_key_is_marked_once_and_skipped(self):
        self.use(self.a.entry(key="wrong-key-wrong-key"), self.b.entry())
        started = time.monotonic()
        self.assertEqual(self.fetch().via, "Home")
        first = time.monotonic() - started
        self.assertTrue(servers._state(self.a.entry(key="wrong-key-wrong-key")).key_bad)
        self.assertEqual(servers.states()[0]["state"], "key")
        hits = len(self.a.hits)
        started = time.monotonic()
        self.assertEqual(self.fetch().via, "Home")
        self.assertLess(time.monotonic() - started, first, "no second wait on a bad key")
        self.assertEqual(len(self.a.hits), hits, "the bad-key server isn't asked again")

    def test_429_waits(self):
        self.a.mode = "429"
        self.use(self.a.entry(), self.b.entry())
        self.assertEqual(self.fetch().via, "Home")
        self.assertGreater(servers._state(self.a.entry()).wait_until, time.time() + 60)

    def test_test_button(self):
        res = servers.test(self.a.address, self.a.key)
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "VPS")
        res = servers.test(self.a.address, "wrong-key-wrong-key")
        self.assertFalse(res["ok"])
        self.assertIn("isn't answering this key", res["message"])
        self.a.mode = "down"
        res = servers.test(self.a.address, self.a.key)
        self.assertIn("No answer", res["message"])

    def test_user_agent_names_isobar_and_the_platform(self):
        self.assertRegex(servers.user_agent(), r"^Isobar/\d+\.\d+\.\d+ \(\w+\)$")


class SettingsShape(unittest.TestCase):
    def test_server_entries_are_cleaned(self):
        saved = settings.save({"servers": [
            {"label": "VPS", "address": "https://weather.example.net/", "key": "abc123", "enabled": True},
            {"address": "javascript:alert(1)", "key": "x"},
            {"address": "https://x.example/path", "key": "x"},
            {"address": "http://192.168.1.10:8787", "key": "with space"},
        ]})["servers"]
        self.assertEqual(saved, [{"label": "VPS", "address": "https://weather.example.net", "key": "abc123",
                                  "enabled": True}])
        settings.save({"servers": []})


if __name__ == "__main__":
    unittest.main()
