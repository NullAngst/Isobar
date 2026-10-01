"""HTTP fetching with a small in-memory TTL cache.

Every outbound request goes through here so the User-Agent is consistent.
The NWS API asks for an identifying User-Agent and will throttle generic ones.
"""

import threading
import time
from collections import OrderedDict

import requests

from . import REPO_URL, __version__

USER_AGENT = f"Isobar/{__version__} ({REPO_URL})"
TIMEOUT = 15

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


class FetchError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class TTLCache:
    def __init__(self, max_items=512, max_bytes=None):
        self._data = OrderedDict()
        self._lock = threading.Lock()
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value, size = item
            if expires < time.time():
                del self._data[key]
                self._bytes -= size
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key, value, ttl, size=0):
        with self._lock:
            old = self._data.pop(key, None)
            if old:
                self._bytes -= old[2]
            self._data[key] = (time.time() + ttl, value, size)
            self._bytes += size
            while len(self._data) > self._max_items or (
                self._max_bytes and self._bytes > self._max_bytes and len(self._data) > 1
            ):
                _, (_, _, dropped) = self._data.popitem(last=False)
                self._bytes -= dropped


_json_cache = TTLCache(max_items=800)
_bytes_cache = TTLCache(max_items=4000, max_bytes=96 * 1024 * 1024)
_inflight = {}
_inflight_lock = threading.Lock()


def _single_flight(key, fn):
    """Collapse identical concurrent requests into one upstream call."""
    with _inflight_lock:
        event = _inflight.get(key)
        if event is None:
            event = threading.Event()
            _inflight[key] = event
            leader = True
        else:
            leader = False
    if not leader:
        event.wait(TIMEOUT + 5)
        return None
    try:
        return fn()
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        event.set()


def _request(url, params=None, headers=None):
    try:
        resp = _session.get(url, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise FetchError(f"{url}: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise FetchError(f"{url}: HTTP {resp.status_code}", resp.status_code)
    return resp


def get_json(url, params=None, ttl=300, headers=None, missing_ok=False):
    """GET JSON with caching. missing_ok returns None on 404 and caches that miss."""
    key = ("json", url, tuple(sorted((params or {}).items())))
    cached = _json_cache.get(key)
    if cached is not None:
        return cached[0]

    def fetch():
        try:
            data = _request(url, params, headers).json()
        except FetchError as exc:
            if missing_ok and exc.status in (404, 410):
                _json_cache.put(key, (None,), min(ttl, 600))
                return (None,)
            raise
        except ValueError as exc:
            raise FetchError(f"{url}: bad JSON") from exc
        _json_cache.put(key, (data,), ttl)
        return (data,)

    result = _single_flight(key, fetch)
    if result is None:
        cached = _json_cache.get(key)
        if cached is None:
            result = fetch()
        else:
            result = cached
    return result[0]


def get_text(url, params=None, ttl=300, headers=None):
    key = ("text", url, tuple(sorted((params or {}).items())))
    cached = _json_cache.get(key)
    if cached is not None:
        return cached
    text = _request(url, params, headers).text
    _json_cache.put(key, text, ttl)
    return text


def get_bytes(url, ttl=120):
    """Binary fetch used by the tile proxy. Returns (bytes, content_type)."""
    key = ("bytes", url)
    cached = _bytes_cache.get(key)
    if cached is not None:
        return cached
    resp = _request(url)
    value = (resp.content, resp.headers.get("Content-Type", "application/octet-stream"))
    _bytes_cache.put(key, value, ttl, size=len(resp.content))
    return value
