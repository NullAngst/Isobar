"""Every request the relay makes to someone else goes through here.

The same manners the Isobar app keeps with the free services:

- One User-Agent naming the relay and the operator's contact address, which
  the NWS requires.
- Conditional requests: callers pass the ETag and Last-Modified they got last
  time, and an unchanged answer comes back as a 304 with no body.
- Backoff per upstream (host plus first path segment): 20 seconds after a
  failure, doubling to 15 minutes, honoring Retry-After.
- One log line per change of state ("NWS alerts failing since 21:10",
  "NWS alerts recovered"), so an outage is easy to find later.
"""

import asyncio
import email.utils
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("isobar_relay.upstream")

BACKOFF_FIRST = 20
BACKOFF_MAX = 15 * 60
MAX_BODY = 64 * 1024 * 1024


class UpstreamError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class Answer:
    status: int
    body: bytes
    etag: str = None
    modified: str = None

    @property
    def not_modified(self):
        return self.status == 304


def _scope(url):
    parsed = urlparse(url)
    first = (parsed.path or "/").lstrip("/").split("/", 1)[0]
    return f"{(parsed.hostname or '').lower()}/{first}"


def _retry_after(headers):
    value = (headers.get("Retry-After") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        return max(0, int(email.utils.parsedate_to_datetime(value).timestamp() - time.time()))
    except (TypeError, ValueError):
        return None


def clock(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")


class Health:
    """Whether each named upstream is answering, for the log and /v1/status."""

    def __init__(self):
        self.failing_since = {}
        self.last_error = {}

    def ok(self, name):
        since = self.failing_since.pop(name, None)
        self.last_error.pop(name, None)
        if since is not None:
            minutes = max(1, round((time.time() - since) / 60))
            log.warning("%s recovered after %s min", name, minutes)

    def fail(self, name, error):
        self.last_error[name] = str(error)[:300]
        if name not in self.failing_since:
            self.failing_since[name] = time.time()
            log.warning("%s failing since %s: %s", name, clock(self.failing_since[name]), error)

    def down_since(self, name):
        return self.failing_since.get(name)


class Upstream:
    def __init__(self, agent, health=None):
        self.agent = agent
        self.health = health or Health()
        self._backoff = {}
        self._session = None

    async def start(self):
        timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=30)
        self._session = aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": self.agent})

    async def close(self):
        if self._session is not None:
            await self._session.close()

    def blocked_for(self, url):
        until, _ = self._backoff.get(_scope(url), (0, 0))
        return max(0.0, until - time.time())

    def _failed(self, scope, headers=None):
        _, count = self._backoff.get(scope, (0, 0))
        count += 1
        delay = _retry_after(headers) if headers is not None else None
        if delay is None:
            delay = BACKOFF_FIRST * (2 ** (count - 1))
        delay = min(BACKOFF_MAX, max(5, delay))
        self._backoff[scope] = (time.time() + delay, count)
        log.info("backing off %s for %ss (failure %s)", scope, delay, count)

    def _succeeded(self, scope):
        self._backoff.pop(scope, None)

    async def get(self, url, params=None, headers=None, etag=None, modified=None, timeout=None,
                  name=None, limit=MAX_BODY, backoff=True, ok_statuses=(200,)):
        """GET with backoff and conditional headers. Returns an Answer (status
        200 with a body, or 304). Anything else raises UpstreamError. With a
        name, the result is recorded in Health under that name."""
        scope = _scope(url)
        wait = self.blocked_for(url) if backoff else 0
        if wait:
            error = UpstreamError(f"{scope} is backing off for {int(wait)}s", 503)
            if name:
                self.health.fail(name, error)
            raise error
        req_headers = dict(headers or {})
        if etag:
            req_headers["If-None-Match"] = etag
        if modified:
            req_headers["If-Modified-Since"] = modified
        kwargs = {}
        if timeout:
            kwargs["timeout"] = aiohttp.ClientTimeout(total=timeout)
        try:
            async with self._session.get(url, params=params, headers=req_headers, **kwargs) as resp:
                if resp.status == 304 and (etag or modified):
                    self._succeeded(scope)
                    if name:
                        self.health.ok(name)
                    return Answer(304, b"", etag, modified)
                if resp.status == 429 or resp.status >= 500:
                    if backoff:
                        self._failed(scope, resp.headers)
                    raise UpstreamError(f"{url}: HTTP {resp.status}", resp.status)
                if resp.status not in ok_statuses:
                    raise UpstreamError(f"{url}: HTTP {resp.status}", resp.status)
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > limit:
                    raise UpstreamError(f"{url}: response too large")
                body = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    body += chunk
                    if len(body) > limit:
                        raise UpstreamError(f"{url}: response too large")
                self._succeeded(scope)
                if name:
                    self.health.ok(name)
                return Answer(resp.status, bytes(body), resp.headers.get("ETag"), resp.headers.get("Last-Modified"))
        except UpstreamError as exc:
            if name:
                self.health.fail(name, exc)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            if backoff:
                self._failed(scope)
            error = UpstreamError(f"{url}: {exc.__class__.__name__} {exc}".strip())
            if name:
                self.health.fail(name, error)
            raise error from exc
