"""Isobar Servers: optional relays someone runs, tried before the providers.

With no servers in Settings, nothing here runs and Isobar talks to the
providers directly, exactly as it always has. With servers set, each request
for a dataset a server carries walks the list in order:

1. Skip a server in cooldown, marked "key not answering", or waiting out a 429.
2. Give each server 5 seconds.
3. A fresh answer wins: use it and stop.
4. A stale answer is held, and the walk goes on to the next server.
5. A timeout, refused connection or 503 moves on. Three failures in a row put
   that server in a 5 minute cooldown.
6. After the servers, go direct to the providers, if Settings allows it.
7. If nothing fresh came back, use the freshest copy seen, Isobar's own
   cache included.

A server never says "wrong key": it goes silent instead, the same as when
it's down. So when a keyed request times out, Isobar asks /v1/ping (which
needs no key). If that answers, the server is up and the key is what's not
working, and the server is skipped until its key is edited. A bad key costs
one 5 second wait, not one per request.
"""

import hashlib
import json
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from . import __version__, settings
from .net import FetchError, _single_flight

log = logging.getLogger("isobar.servers")

TIMEOUT = (5, 5)            # connect, then silence while waiting: about 5 seconds a server
PING_TIMEOUT = (4, 4)
COOLDOWN = 5 * 60
FAILS_FOR_COOLDOWN = 3
STATUS_TTL = 30 * 60
MAX_BODY = 32 * 1024 * 1024


def platform_name():
    if os.environ.get("ISOBAR_PLATFORM") == "android":
        return "Android"
    system = platform.system()
    return {"Darwin": "macOS"}.get(system, system or "desktop")


def user_agent():
    # Servers answer only requests whose User-Agent starts with "Isobar/".
    return f"Isobar/{__version__} ({platform_name()})"


_session = requests.Session()
_session.headers.update({"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate", "Connection": "close"})


@dataclass
class Got:
    """An answer and where it came from."""
    value: object
    fetched: float          # when the source got it from upstream (unix time)
    via: str                # a server's label, or "direct"
    source: str = None      # X-Isobar-Source from a server: nws, iem, openmeteo-local...
    stale: bool = False
    down_since: float = None

    def info(self):
        return {"fetched": self.fetched, "via": self.via, "source": self.source, "stale": self.stale,
                "down_since": self.down_since}


class State:
    __slots__ = ("fails", "cooldown_until", "key_bad", "wait_until", "last_ok", "last_error", "status",
                 "status_at", "stale")

    def __init__(self):
        self.fails = 0
        self.cooldown_until = 0.0
        self.key_bad = False
        self.wait_until = 0.0
        self.last_ok = None
        self.last_error = None
        self.status = None
        self.status_at = 0.0
        self.stale = False


_states = {}
_lock = threading.Lock()
_memo = {}                  # (path, params) -> (Got, expires)
_memo_lock = threading.Lock()


def server_id(server):
    """Identifies a server by its address and key, so editing the key starts it fresh."""
    raw = f"{server.get('address')}\0{server.get('key')}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _state_file():
    from .paths import cache_dir  # late: Android sets the path at startup

    return cache_dir() / "servers-state.json"


def _load_bad_keys():
    try:
        return set(json.loads(_state_file().read_text(encoding="utf-8")).get("key_bad") or [])
    except (OSError, ValueError, AttributeError):
        return set()


def _save_bad_keys():
    bad = sorted(sid for sid, st in _states.items() if st.key_bad)
    try:
        path = _state_file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key_bad": bad}), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.info("could not save server state: %s", exc)


_bad_loaded = False


def _state(server):
    global _bad_loaded
    with _lock:
        if not _bad_loaded:
            _bad_loaded = True
            for sid in _load_bad_keys():
                _states.setdefault(sid, State()).key_bad = True
        return _states.setdefault(server_id(server), State())


def configured():
    """The enabled servers, in priority order."""
    prefs = settings.load()
    return [s for s in prefs.get("servers") or [] if s.get("enabled") and s.get("address")]


def direct_allowed():
    return settings.load().get("servers_direct", True) is not False


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


class _Silent(Exception):
    """No answer at all: a timeout or a dropped connection."""


def _get(server, path, params=None, keyed=True, timeout=TIMEOUT, raw=False):
    headers = {}
    if keyed:
        headers["Authorization"] = f"Bearer {server.get('key') or ''}"
    url = server["address"].rstrip("/") + path
    try:
        resp = _session.get(url, params=params, headers=headers, timeout=timeout, allow_redirects=False,
                            stream=True)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise _Silent(exc.__class__.__name__) from exc
    with resp:
        try:
            body = resp.raw.read(MAX_BODY + 1, decode_content=True)
        except (requests.RequestException, OSError) as exc:
            raise _Silent(exc.__class__.__name__) from exc
        if len(body) > MAX_BODY:
            raise FetchError(f"{url}: answer too large")
        return resp.status_code, resp.headers, body


def ping(server):
    """True if the server answers /v1/ping, which needs no key."""
    try:
        status, _, body = _get(server, "/v1/ping", keyed=False, timeout=PING_TIMEOUT)
        return status == 200 and json.loads(body).get("service") == "isobar-server"
    except (_Silent, ValueError, AttributeError, FetchError):
        return False


def _failed(server, state, why, silent=False):
    """A request to this server got nothing useful. Returns the state's new label."""
    with _lock:
        state.fails += 1
        state.last_error = why
    if silent and server.get("key") and ping(server):
        with _lock:
            state.key_bad = True
        log.warning("server %s is up but isn't answering this key; skipping it until the key is edited",
                    server["label"])
        _save_bad_keys()
        return
    with _lock:
        if state.fails >= FAILS_FOR_COOLDOWN:
            state.cooldown_until = time.time() + COOLDOWN
            state.fails = 0
            log.info("server %s: three failures in a row, cooling down for 5 minutes (%s)", server["label"], why)


def usable(server, state=None, now=None):
    state = state or _state(server)
    now = time.time() if now is None else now
    return not (state.key_bad or state.cooldown_until > now or state.wait_until > now)


def status_of(server, force=False):
    """The server's /v1/status, cached half an hour. None if it can't be had."""
    state = _state(server)
    if not force and state.status is not None and time.time() - state.status_at < STATUS_TTL:
        return state.status
    try:
        code, _, body = _get(server, "/v1/status")
        if code != 200:
            return state.status
        data = json.loads(body)
    except _Silent:
        _failed(server, state, "status timed out", silent=True)
        return None
    except (ValueError, FetchError):
        return state.status
    with _lock:
        state.status = data
        state.status_at = time.time()
    return data


def _try(server, path, params, need=None):
    """One server: a Got, or None for nothing usable."""
    state = _state(server)
    if not usable(server, state):
        return None
    if need:
        caps = (status_of(server) or {}).get("capabilities") or {}
        if not caps.get(need):
            return None
    try:
        code, headers, body = _get(server, path, params)
    except _Silent as exc:
        _failed(server, state, f"no answer ({exc})", silent=True)
        return None
    except FetchError as exc:
        _failed(server, state, str(exc))
        return None
    if code == 429:
        try:
            wait = int(headers.get("Retry-After") or 60)
        except ValueError:
            wait = 60
        with _lock:
            state.wait_until = time.time() + max(1, min(wait, 3600))
            state.last_error = "rate limited"
        return None
    if code != 200:
        _failed(server, state, f"HTTP {code}")
        return None
    try:
        value = json.loads(body)
    except ValueError:
        _failed(server, state, "not JSON")
        return None
    stale = headers.get("X-Isobar-Stale") == "1"
    fetched = _parse_time(headers.get("X-Isobar-Fetched-At")) or time.time()
    with _lock:
        state.fails = 0
        state.last_ok = time.time()
        state.last_error = None
        state.stale = stale
    return Got(value, fetched, server["label"], headers.get("X-Isobar-Source"), stale,
               _parse_time(headers.get("X-Isobar-Upstream-Down-Since")))


def fetch(path, params, direct=None, ttl=300, stale_after=None, need=None):
    """Walk the servers, then direct. direct() returns (value, fetched_at) or
    raises FetchError. With no servers set, this is direct() and nothing else.
    stale_after says when a direct answer counts as stale (None: never)."""
    servers = configured()
    if not servers:
        if direct is None:
            raise FetchError("no source for this data")
        value, fetched = direct()
        now = time.time()
        fetched = fetched or now
        return Got(value, fetched, "direct", None, stale_after is not None and now - fetched > stale_after)

    key = (path, tuple(sorted((k, str(v)) for k, v in (params or {}).items())))
    with _memo_lock:
        hit = _memo.get(key)
    if hit and hit[1] > time.time() and not hit[0].stale:
        return hit[0]

    def walk():
        held = []
        for server in servers:
            got = _try(server, path, params, need)
            if got is None:
                continue
            if not got.stale:
                return got
            held.append(got)
        if direct is not None and direct_allowed():
            try:
                value, fetched = direct()
                now = time.time()
                fetched = fetched or now
                got = Got(value, fetched, "direct", None, stale_after is not None and now - fetched > stale_after)
                if not got.stale:
                    return got
                held.append(got)
            except FetchError as exc:
                log.info("%s direct: %s", path, exc)
        if hit:
            held.append(hit[0])
        if held:
            return max(held, key=lambda g: g.fetched)
        raise FetchError(f"{path}: no server answered" + ("" if direct_allowed() else ", and going direct is off"))

    got = _single_flight(("servers",) + key, walk)
    with _memo_lock:
        if len(_memo) > 2000:
            _memo.clear()
        _memo[key] = (got, time.time() + ttl)
    return got


# ---------------------------------------------------------------- radar

def radar_server(index=None):
    """(index, server) for the first usable server whose status says it runs
    radar, or the one at index if it still does. None if there's none."""
    servers = configured()
    candidates = list(enumerate(servers))
    if index is not None and 0 <= index < len(servers):
        candidates = [(index, servers[index])] + [c for c in candidates if c[0] != index]
    for i, server in candidates:
        if not usable(server):
            continue
        caps = (status_of(server) or {}).get("capabilities") or {}
        if caps.get("radar"):
            return i, server
    return None


def radar_frames(index=None):
    found = radar_server(index)
    if found is None:
        return None
    i, server = found
    got = _try(server, "/v1/radar/frames", None)
    if got is None or not isinstance(got.value, dict):
        return None
    frames = [f for f in got.value.get("frames") or [] if isinstance(f, dict) and f.get("id")]
    return {"index": i, "label": server["label"], "frames": frames}


def radar_tile(index, frame, z, x, y):
    """PNG bytes for one MRMS tile from the server at index."""
    servers = configured()
    if not 0 <= index < len(servers):
        raise ValueError("no such server")
    server = servers[index]
    from .net import get_bytes

    url = f"{server['address'].rstrip('/')}/v1/radar/{frame}/{z}/{x}/{y}.png"
    return get_bytes(url, ttl=3600, headers={"Authorization": f"Bearer {server.get('key') or ''}",
                                             "User-Agent": user_agent()})


# ---------------------------------------------------------------- Settings

def test(address, key):
    """What the Test button reports. Works out "wrong key" from what answers,
    since a server never says it."""
    server = settings._server({"address": address, "key": key, "label": ""})
    if server is None:
        return {"ok": False, "message": "That address isn't a web address Isobar can use."}
    up = ping(server)
    try:
        code, _, body = _get(server, "/v1/status")
        status = json.loads(body) if code == 200 else None
    except (_Silent, ValueError, FetchError):
        status = None
    if status is not None:
        state = _state(server)
        with _lock:
            state.key_bad = False
            state.cooldown_until = 0
            state.fails = 0
            state.status = status
            state.status_at = time.time()
        _save_bad_keys()
        return {"ok": True, "name": status.get("name"), "version": status.get("version"),
                "capabilities": status.get("capabilities") or {}, "datasets": status.get("datasets") or {},
                "message": f"Connected to {status.get('name') or 'the server'}."}
    if up:
        return {"ok": False, "reachable": True,
                "message": "The server is up, but it isn't answering this key."}
    return {"ok": False, "reachable": False, "message": "No answer. The server is down, or the address is wrong."}


def states():
    """Each configured server's state, for Settings: ok, stale, unreachable,
    cooldown, key, waiting, off or unknown."""
    out = []
    now = time.time()
    for server in settings.load().get("servers") or []:
        state = _state(server)
        if not server.get("enabled"):
            label = "off"
        elif state.key_bad:
            label = "key"
        elif state.cooldown_until > now:
            label = "cooldown"
        elif state.wait_until > now:
            label = "waiting"
        elif state.last_error and (state.last_ok is None or state.fails):
            label = "unreachable"
        elif state.last_ok is None:
            label = "unknown"
        else:
            label = "stale" if state.stale else "ok"
        out.append({"address": server["address"], "state": label, "error": state.last_error,
                    "last_ok": state.last_ok})
    return out


def forget_memo():
    with _memo_lock:
        _memo.clear()
