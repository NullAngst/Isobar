"""Backoff and cache bookkeeping in net.py, with the session replaced."""

import time
import unittest
from unittest import mock

import requests

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


class Stalling:
    """A response whose body stops arriving partway."""
    status_code = 200
    headers = {}
    url = "https://x"

    def iter_content(self, size):
        yield b'{"par'
        raise requests.exceptions.ChunkedEncodingError("connection broken")

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
        with mock.patch.object(net._fresh_session, "get", side_effect=lambda url, **kw: responses[url]):
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.weather.gov/gridpoints/FFC/1,2/forecast")
            self.assertEqual(net.get_json("https://api.weather.gov/alerts/active", {"point": "1,2"}), {"features": []})
        self.assertIn("api.weather.gov/gridpoints", net._backoff)
        self.assertNotIn("api.weather.gov/alerts", net._backoff)

    def test_plain_nws_500_does_not_back_off(self):
        with mock.patch.object(net._fresh_session, "get", return_value=FakeResponse(500)):
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.weather.gov/gridpoints/FFC/1,2/forecast")
        self.assertEqual(net._backoff, {})

    def test_other_hosts_back_off_on_500(self):
        with mock.patch.object(net._fresh_session, "get", return_value=FakeResponse(500)):
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.open-meteo.com/v1/forecast", {"a": 1})
        self.assertIn("api.open-meteo.com/v1", net._backoff)

    def test_fetched_at_is_recorded(self):
        with mock.patch.object(net._fresh_session, "get", return_value=FakeResponse(200, b'{"ok": 1}')):
            net.get_json("https://example.org/a", {"q": 1})
        self.assertIsNotNone(net.fetched_at("https://example.org/a", {"q": 1}))
        self.assertIsNone(net.fetched_at("https://example.org/a", {"q": 2}))

    def test_json_cache_counts_bytes(self):
        cache = net.Cache(max_items=100, max_bytes=10)
        cache.put("a", net.Entry(1, 0, size=6))
        cache.put("b", net.Entry(2, 0, size=6))
        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("b"))


class Retry(unittest.TestCase):
    def setUp(self):
        net._backoff.clear()
        net._json_cache._data.clear()

    def test_one_timeout_is_retried_without_backoff(self):
        answers = [requests.exceptions.ReadTimeout("stalled"), FakeResponse(200, b'{"ok": 1}')]

        def get(url, **kw):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(net._fresh_session, "get", side_effect=get) as fake:
            self.assertEqual(net.get_json("https://api.open-meteo.com/v1/forecast", {"a": 1}), {"ok": 1})
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(net._backoff, {})

    def test_two_failures_in_a_row_back_off(self):
        with mock.patch.object(net._fresh_session, "get", side_effect=requests.exceptions.ConnectTimeout("no route")) as fake:
            with self.assertRaises(net.FetchError):
                net.get_json("https://api.open-meteo.com/v1/forecast", {"a": 2})
        self.assertEqual(fake.call_count, 2)
        self.assertIn("api.open-meteo.com/v1", net._backoff)

    def test_body_that_stops_partway_is_retried(self):
        answers = [Stalling(), FakeResponse(200, b'{"ok": 2}')]
        with mock.patch.object(net._fresh_session, "get", side_effect=lambda url, **kw: answers.pop(0)):
            self.assertEqual(net.get_json("https://example.org/b"), {"ok": 2})

    def test_http_errors_are_not_retried(self):
        with mock.patch.object(net._fresh_session, "get", return_value=FakeResponse(404)) as fake:
            with self.assertRaises(net.FetchError):
                net.get_json("https://example.org/missing")
        self.assertEqual(fake.call_count, 1)

    def test_stale_copy_survives_a_double_failure(self):
        with mock.patch.object(net._fresh_session, "get", return_value=FakeResponse(200, b'{"v": 1}')):
            net.get_json("https://example.org/c", ttl=1)
        key = ("json", "https://example.org/c", net._params_key(None))
        net._json_cache.get(key).expires = time.time() - 10  # expired, but inside the stale window
        with mock.patch.object(net._fresh_session, "get", side_effect=requests.exceptions.ReadTimeout("x")):
            self.assertEqual(net.get_json("https://example.org/c", ttl=1), {"v": 1})

    def test_sessions(self):
        self.assertEqual(net._fresh_session.headers.get("Connection"), "close")
        self.assertNotEqual(net._session.headers.get("Connection"), "close")
        self.assertEqual(net.TIMEOUT, (net.CONNECT_TIMEOUT, net.READ_TIMEOUT))


if __name__ == "__main__":
    unittest.main()
