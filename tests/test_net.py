"""Backoff and cache bookkeeping in net.py, with the session replaced."""

import unittest
from unittest import mock

from tests import helpers  # noqa: F401
from isobar import net


class FakeResponse:
    def __init__(self, status, body=b"{}", headers=None, url="https://x"):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.encoding = "utf-8"

    def iter_content(self, size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Backoff(unittest.TestCase):
    def setUp(self):
        net._backoff.clear()
        net._json_cache._data.clear()

    def test_scope_is_host_and_first_segment(self):
        self.assertEqual(net._scope("https://api.weather.gov/gridpoints/FFC/1,2/forecast"), "api.weather.gov/gridpoints")
        self.assertEqual(net._scope("https://api.weather.gov/alerts/active?point=1,2"), "api.weather.gov/alerts")

    def test_failing_forecast_does_not_block_alerts(self):
        responses = {
            "https://api.weather.gov/gridpoints/FFC/1,2/forecast": FakeResponse(503),
            "https://api.weather.gov/alerts/active": FakeResponse(200, b'{"features": []}'),
        }
        with mock.patch.object(net._session, "get", side_effect=lambda url, **kw: responses[url]):
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.weather.gov/gridpoints/FFC/1,2/forecast")
            self.assertEqual(net.get_json("https://api.weather.gov/alerts/active", {"point": "1,2"}), {"features": []})
        self.assertIn("api.weather.gov/gridpoints", net._backoff)
        self.assertNotIn("api.weather.gov/alerts", net._backoff)

    def test_plain_nws_500_does_not_back_off(self):
        with mock.patch.object(net._session, "get", return_value=FakeResponse(500)):
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.weather.gov/gridpoints/FFC/1,2/forecast")
        self.assertEqual(net._backoff, {})

    def test_other_hosts_back_off_on_500(self):
        with mock.patch.object(net._session, "get", return_value=FakeResponse(500)):
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.open-meteo.com/v1/forecast", {"a": 1})
        self.assertIn("api.open-meteo.com/v1", net._backoff)

    def test_fetched_at_is_recorded(self):
        with mock.patch.object(net._session, "get", return_value=FakeResponse(200, b'{"ok": 1}')):
            net.get_json("https://example.org/a", {"q": 1})
        self.assertIsNotNone(net.fetched_at("https://example.org/a", {"q": 1}))
        self.assertIsNone(net.fetched_at("https://example.org/a", {"q": 2}))

    def test_json_cache_counts_bytes(self):
        cache = net.Cache(max_items=100, max_bytes=10)
        cache.put("a", net.Entry(1, 0, size=6))
        cache.put("b", net.Entry(2, 0, size=6))
        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("b"))


if __name__ == "__main__":
    unittest.main()
