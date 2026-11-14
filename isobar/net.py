"""Every outbound HTTP request goes through here.

The point of this module is to be a polite client to the free services Isobar
depends on (Open-Meteo, the NWS, SPC, the Iowa Environmental Mesonet, NOAA's
map service and OpenStreetMap's Nominatim):

- One identifying User-Agent on every request, which the NWS and Nominatim ask for.
- A cache with a lifetime per call site. Fresh entries never touch the network.
- Identical requests that arrive at the same time share one upstream call,
  including when that call fails.
- Expired entries are revalidated with If-None-Match / If-Modified-Since when
  the server gave an ETag or Last-Modified, so an unchanged answer costs a
  304 with no body.
- When a service errors, times out or says 429/503, Isobar backs off that
  service (honouring Retry-After) instead of retrying on every UI refresh, and
  serves the last good answer in the meantime if it has one. Backoff is kept
  per host and first path segment, so a failing NWS forecast endpoint
  (/gridpoints) never holds up NWS alerts (/alerts). Plain 500s from the NWS
  API don't trigger backoff at all, since that API throws them often for one
  grid point and answers fine on the next try.
- Every cached answer remembers when it was last confirmed upstream, so the
  UI can say how old the data really is when a service stops answering.
- Long-lived answers (place lookups, NWS grid points, station lists) are kept
  on disk, so restarting the app doesn't refetch them.
- Response bodies are size-capped and redirects are refused for the tile proxy.
"""

import email.utils
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from urllib.parse import urlparse

import requests

from . import REPO_URL, __version__

log = logging.getLogger("isobar.net")

USER_AGENT = f"Isobar/{__version__} ({REPO_URL})"
TIMEOUT = 15
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_TILE_BYTES = 8 * 1024 * 1024
DISK_MIN_TTL = 3600          # answers kept at least this long also go to disk
BACKOFF_FIRST = 20           # seconds; doubles per consecutive failure
BACKOFF_MAX = 15 * 60

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
_adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


class FetchError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- cache

class Entry:
    __slots__ = ("value", "expires", "etag", "modified", "size", "fetched")

    def __init__(self, value, expires, etag=None, modified=None, size=0, fetched=None):
        self.value = value
        self.expires = expires
        self.etag = etag
        self.modified = modified
        self.size = size
        # When upstream last confirmed this answer (a 200 or a 304).
        self.fetched = time.time() if fetched is None else fetched

    @property
    def fresh(self):
        return self.expires > time.time()


class Cache:
    """LRU keyed cache. Expired entries stay until evicted, for revalidation
    and as a fallback while a host is failing."""

    def __init__(self, max_items, max_bytes=None):
        self._data = OrderedDict()
        self._lock = threading.Lock()
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is not None:
                self._data.move_to_end(key)
            return entry

    def put(self, key, entry):
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old.size
            self._data[key] = entry
            self._bytes += entry.size
            while len(self._data) > self._max_items or (
                self._max_bytes and self._bytes > self._max_bytes and len(self._data) > 1
            ):
                _, dropped = self._data.popitem(last=False)
                self._bytes -= dropped.size


# Sizes are the raw response bodies. Parsed JSON takes more memory than that,
# so the JSON cap sits well under the tile cap.
_json_cache = Cache(max_items=1500, max_bytes=48 * 1024 * 1024)
_bytes_cache = Cache(max_items=4000, max_bytes=96 * 1024 * 1024)


# ---------------------------------------------------------------- disk cache

def _disk_dir():
    from .paths import cache_dir  # imported late: Android sets the path at startup

    path = cache_dir() / "api"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _disk_name(key):
    return hashlib.sha256(repr(key).encode("utf-8")).hexdigest() + ".json"


def _disk_get(key):
    try:
        raw = json.loads((_disk_dir() / _disk_name(key)).read_text(encoding="utf-8"))
        return Entry(raw["value"], raw["expires"], raw.get("etag"), raw.get("modified"),
                     fetched=raw.get("fetched") or raw["expires"])
    except (OSError, ValueError, KeyError):
        return None


def _disk_put(key, entry):
    try:
        folder = _disk_dir()
        tmp = folder / (_disk_name(key) + ".tmp")
        tmp.write_text(json.dumps({
            "value": entry.value, "expires": entry.expires,
            "etag": entry.etag, "modified": entry.modified, "fetched": entry.fetched,
        }), encoding="utf-8")
        tmp.replace(folder / _disk_name(key))
    except (OSError, TypeError, ValueError) as exc:
        log.debug("disk cache write failed: %s", exc)


def prune_disk(max_age_days=30):
    """Delete disk-cached answers that expired long ago. Called at startup."""
    cutoff = time.time() - max_age_days * 86400
    try:
        for path in _disk_dir().glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------- backoff

_backoff = {}
_backoff_lock = threading.Lock()


# Hosts whose plain 500s say nothing about the rest of the service.
SOFT_500_HOSTS = {"api.weather.gov"}


def _host(url):
    return (urlparse(url).hostname or "").lower()


def _scope(url):
    """What a backoff applies to: the host plus the first path segment.
    api.weather.gov/gridpoints and api.weather.gov/alerts back off separately."""
    parsed = urlparse(url)
    first = (parsed.path or "/").lstrip("/").split("/", 1)[0]
    return f"{(parsed.hostname or '').lower()}/{first}"


def _blocked_for(scope):
    with _backoff_lock:
        until, _ = _backoff.get(scope, (0, 0))
    return max(0.0, until - time.time())


def _retry_after(resp):
    value = (resp.headers.get("Retry-After") or "").strip() if resp is not None else ""
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0, int(when.timestamp() - time.time()))
    except (TypeError, ValueError):
        return None


def _record_failure(scope, resp=None):
    with _backoff_lock:
        _, failures = _backoff.get(scope, (0, 0))
        failures += 1
        delay = _retry_after(resp)
        if delay is None:
            delay = BACKOFF_FIRST * (2 ** (failures - 1))
        delay = min(BACKOFF_MAX, max(5, delay))
        _backoff[scope] = (time.time() + delay, failures)
    log.info("backing off %s for %ss (failure %s)", scope, delay, failures)


def _record_success(scope):
    with _backoff_lock:
        _backoff.pop(scope, None)


# ---------------------------------------------------------------- single flight

class _Call:
    __slots__ = ("event", "result", "error")

    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


_inflight = {}
_inflight_lock = threading.Lock()


def _single_flight(key, fn):
    """Concurrent identical requests share one upstream call and its outcome."""
    with _inflight_lock:
        call = _inflight.get(key)
        leader = call is None
        if leader:
            call = _Call()
            _inflight[key] = call
    if not leader:
        if not call.event.wait(TIMEOUT * 2 + 5):
            raise FetchError("timed out waiting for a shared request")
        if call.error is not None:
            raise call.error
        return call.result
    try:
        call.result = fn()
        return call.result
    except Exception as exc:
        call.error = exc
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        call.event.set()


# ---------------------------------------------------------------- the request itself

def _read_capped(resp, limit):
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > limit:
        raise FetchError(f"{resp.url}: response too large")
    chunks, total = [], 0
    for chunk in resp.iter_content(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise FetchError(f"{resp.url}: response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch(url, params, headers, entry, limit, allow_redirects=True):
    """One upstream GET. Returns (status, body, response). 304 returns body None."""
    scope = _scope(url)
    wait = _blocked_for(scope)
    if wait:
        raise FetchError(f"{scope} is backing off for {int(wait)}s", 503)

    req_headers = dict(headers or {})
    if entry is not None:
        if entry.etag:
            req_headers["If-None-Match"] = entry.etag
        if entry.modified:
            req_headers["If-Modified-Since"] = entry.modified

    try:
        resp = _session.get(url, params=params, headers=req_headers, timeout=TIMEOUT,
                            stream=True, allow_redirects=allow_redirects)
    except requests.RequestException as exc:
        _record_failure(scope)
        raise FetchError(f"{url}: {exc.__class__.__name__}") from exc

    with resp:
        status = resp.status_code
        if status == 304 and entry is not None:
            _record_success(scope)
            return 304, None, resp
        if status == 429 or status >= 500:
            soft = status not in (429, 503) and _host(url) in SOFT_500_HOSTS
            if not soft:
                _record_failure(scope, resp)
            raise FetchError(f"{url}: HTTP {status}", status)
        if status != 200:
            # 4xx other than 429 is an answer about this request, not about the host.
            raise FetchError(f"{url}: HTTP {status}", status)
        body = _read_capped(resp, limit)
        _record_success(scope)
        return 200, body, resp


def _stale_limit(ttl):
    # How long past expiry an old answer may stand in while a host is failing.
    return min(6 * 3600, max(600, ttl * 6))


def _cached_fetch(cache, key, url, params, headers, ttl, limit, decode, missing_ok=False,
                  allow_redirects=True, check=None, before=None):
    entry = cache.get(key)
    if entry is None and ttl >= DISK_MIN_TTL:
        entry = _disk_get(key)
        if entry is not None:
            cache.put(key, entry)
    if entry is not None and entry.fresh:
        return entry.value

    def refresh():
        try:
            if before is not None and not _blocked_for(_scope(url)):
                before()
            status, body, resp = _fetch(url, params, headers, entry, limit, allow_redirects)
        except FetchError as exc:
            if missing_ok and exc.status in (404, 410):
                miss = Entry(None, time.time() + min(ttl, 3600))
                cache.put(key, miss)
                return None
            if entry is not None and entry.value is not None and time.time() - entry.expires < _stale_limit(ttl):
                log.info("serving stale copy of %s: %s", url, exc)
                return entry.value
            raise
        if status == 304:
            entry.expires = time.time() + ttl
            entry.fetched = time.time()
            cache.put(key, entry)
            if ttl >= DISK_MIN_TTL:
                _disk_put(key, entry)
            return entry.value
        if check is not None:
            check(resp)
        value = decode(body, resp)
        new = Entry(value, time.time() + ttl, resp.headers.get("ETag"), resp.headers.get("Last-Modified"),
                    len(body))
        cache.put(key, new)
        if ttl >= DISK_MIN_TTL:
            _disk_put(key, new)
        return value

    return _single_flight(key, refresh)


def _decode_json(body, resp):
    try:
        return json.loads(body)
    except ValueError as exc:
        raise FetchError(f"{resp.url}: bad JSON") from exc


def _decode_text(body, resp):
    return body.decode(resp.encoding or "utf-8", errors="replace")


def _params_key(params):
    return tuple(sorted((params or {}).items()))


def fetched_at(url, params=None, kind="json"):
    """Unix time the cached answer for this request was last confirmed
    upstream, or None if there is none. Lets the UI flag data that's being
    served from an old copy because the service stopped answering."""
    entry = _json_cache.get((kind, url, _params_key(params)))
    return entry.fetched if entry is not None and entry.value is not None else None


def get_json(url, params=None, ttl=300, headers=None, missing_ok=False, before=None):
    """GET JSON through the cache. missing_ok returns None on 404 and remembers the miss.
    before runs right before a real network request (not on cache hits)."""
    key = ("json", url, _params_key(params))
    return _cached_fetch(_json_cache, key, url, params, headers, ttl, MAX_JSON_BYTES, _decode_json, missing_ok,
                         before=before)


def get_text(url, params=None, ttl=300, headers=None):
    key = ("text", url, _params_key(params))
    return _cached_fetch(_json_cache, key, url, params, headers, ttl, MAX_JSON_BYTES, _decode_text)


def _image_only(resp):
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        raise FetchError(f"{resp.url}: not an image ({ctype or 'no type'})")


def get_bytes(url, ttl=120):
    """Image fetch used by the tile proxy. Returns (bytes, content_type).
    Redirects are refused so an allowed host can't bounce the proxy elsewhere,
    and anything that isn't an image is rejected."""
    key = ("bytes", url)
    decode = lambda body, resp: (body, resp.headers.get("Content-Type", "image/png"))  # noqa: E731
    return _cached_fetch(_bytes_cache, key, url, None, None, ttl, MAX_TILE_BYTES, decode,
                         allow_redirects=False, check=_image_only)


# Nominatim allows at most one request per second per application.
_nominatim_lock = threading.Lock()
_nominatim_last = 0.0


def polite_wait(min_interval=1.1):
    """Space out calls to services with a strict per-second limit."""
    global _nominatim_last
    with _nominatim_lock:
        gap = time.time() - _nominatim_last
        if gap < min_interval:
            time.sleep(min_interval - gap)
        _nominatim_last = time.time()
