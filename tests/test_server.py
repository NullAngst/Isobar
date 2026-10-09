"""The local server's guard rails, exercised over a real socket."""

import http.client
import json
import unittest

from tests import helpers  # noqa: F401
from isobar.server import Server


class LocalServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(0)
        cls.server.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None, conn=None):
        if conn is None:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            self.addCleanup(conn.close)
        hdrs = {"X-Isobar-Token": self.server.token}
        hdrs.update(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, resp.read(), conn

    def test_token_required(self):
        status, _, _ = self.request("GET", "/api/version", headers={"X-Isobar-Token": "nope"})
        self.assertEqual(status, 401)
        status, body, _ = self.request("GET", "/api/version")
        self.assertEqual(status, 200)
        self.assertIn("version", json.loads(body))

    def test_host_and_origin_checked(self):
        self.assertEqual(self.request("GET", "/api/version", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/version", headers={"Origin": "https://evil.example"})[0], 403)

    def test_static_cannot_escape(self):
        self.assertEqual(self.request("GET", "/../../isobar/settings.py")[0], 404)
        self.assertEqual(self.request("GET", "/index.html")[0], 200)

    def test_rejected_post_does_not_poison_keepalive(self):
        # A POST turned away for a bad token still has its body read, so the
        # next request on the same connection parses cleanly.
        body = json.dumps({"units": "metric"})
        status, _, conn = self.request("POST", "/api/settings", body=body,
                                       headers={"X-Isobar-Token": "nope", "Content-Type": "application/json"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("GET", "/api/version", conn=conn)
        self.assertEqual(status, 200)

    def test_oversized_post_refused(self):
        status, _, _ = self.request("POST", "/api/settings", body=b"x" * 300_000)
        self.assertEqual(status, 413)

    def test_settings_roundtrip(self):
        status, body, _ = self.request("POST", "/api/settings", body=json.dumps({"clock": "24"}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["clock"], "24")

    def test_open_only_web_links(self):
        opened = []
        self.server.open_url = opened.append
        status, _, _ = self.request("POST", "/api/open", body=json.dumps({"url": "file:///etc/passwd"}))
        self.assertEqual(status, 400)
        self.assertEqual(opened, [])

    def test_tile_proxy_host_allowlist(self):
        status, _, _ = self.request("GET", "/proxy/tile?u=https://example.com/a.png")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
