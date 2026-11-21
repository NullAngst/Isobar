"""Who gets an answer, and what everyone else gets: nothing at all.

The relay never refuses out loud. A request that isn't from an Isobar app with
a working key gets no bytes back. The connection is held open for a while and
then closed, the same as a server that's down. Bots that get a 403 learn
something is there and keep probing; bots that get nothing move on.

The only visible refusal is 429, and only a working key can ever get one.
"""

import asyncio
import collections
import logging
import time

log = logging.getLogger("isobar_relay.gate")

UA_PREFIX = "Isobar/"
FAIL_WINDOW = 10 * 60
BLOCK_FOR = 60 * 60
MAX_TRACKED_IPS = 50000


def isobar_agent(request):
    return (request.headers.get("User-Agent") or "").startswith(UA_PREFIX)


def came_over_https(request):
    # The proxy in front terminates TLS and says so in X-Forwarded-Proto.
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    return proto == "https"


def client_ip(request):
    """The caller's address as the proxy in front saw it. Cloudflare's header
    first, then the one nginx proxies set, then the last X-Forwarded-For hop,
    then the socket. These headers can be forged by a caller who reaches the
    relay without the proxy, which is one more reason RELAY_PORT should only be
    reachable by the proxy. The key, not this, is the lock."""
    for header in ("CF-Connecting-IP", "X-Real-IP"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value[:64]
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last[:64]
    return request.remote or "unknown"


def bearer_key(request, allow_query=False):
    auth = request.headers.get("Authorization") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    if allow_query:
        return (request.query.get("key") or "").strip() or None
    return None


class Silence:
    """Holds connections without answering, up to a cap. Past the cap, new
    silent connections close at once, still without a byte, so a flood can't
    use up the relay's sockets."""

    def __init__(self, hold_seconds, hold_max):
        self.hold_seconds = hold_seconds
        self.hold_max = hold_max
        self.held = 0
        self.dropped_now = 0

    async def drop(self, request):
        transport = request.transport
        if transport is not None and self.held < self.hold_max and self.hold_seconds > 0:
            self.held += 1
            try:
                await asyncio.sleep(self.hold_seconds)
            finally:
                self.held -= 1
        else:
            self.dropped_now += 1
        transport = request.transport
        if transport is not None:
            transport.close()
        # Tells aiohttp the client is gone, so it writes nothing at all.
        raise asyncio.CancelledError()


class AuthFailures:
    """Wrong-key attempts per address. Past the limit inside ten minutes, that
    address gets silence for an hour, right key or not."""

    def __init__(self, limit):
        self.limit = limit
        self._fails = {}
        self._blocked = {}

    def blocked(self, ip, now=None):
        now = time.time() if now is None else now
        until = self._blocked.get(ip)
        if until is None:
            return False
        if until <= now:
            del self._blocked[ip]
            return False
        return True

    def failed(self, ip, now=None):
        """Record a wrong key. Returns True if this one tipped the address into a block."""
        now = time.time() if now is None else now
        recent = self._fails.get(ip)
        if recent is None:
            if len(self._fails) >= MAX_TRACKED_IPS:
                self.prune(now)
            recent = self._fails[ip] = collections.deque()
        recent.append(now)
        while recent and recent[0] < now - FAIL_WINDOW:
            recent.popleft()
        if len(recent) > self.limit:
            self._blocked[ip] = now + BLOCK_FOR
            del self._fails[ip]
            return True
        return False

    def prune(self, now=None):
        now = time.time() if now is None else now
        for ip in [ip for ip, q in self._fails.items() if not q or q[-1] < now - FAIL_WINDOW]:
            del self._fails[ip]
        for ip in [ip for ip, until in self._blocked.items() if until <= now]:
            del self._blocked[ip]
        if len(self._fails) >= MAX_TRACKED_IPS:
            # Still full of recent failures: forget the oldest half rather than grow.
            for ip in sorted(self._fails, key=lambda k: self._fails[k][-1])[: MAX_TRACKED_IPS // 2]:
                del self._fails[ip]


class RateLimiter:
    """A token bucket per key: the per-minute limit, with up to a minute's
    worth allowed in a burst, since one person may run Isobar on a phone and a
    desktop with the same key."""

    def __init__(self, per_minute):
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self._buckets = {}

    def take(self, label, now=None):
        """Returns 0 if the request may go ahead, or the seconds to wait."""
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.get(label, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        if tokens >= 1:
            self._buckets[label] = (tokens - 1, now)
            return 0
        self._buckets[label] = (tokens, now)
        return max(1, int((1 - tokens) / self.rate + 0.999))
