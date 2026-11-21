"""The relay's HTTP side: one handler in front of every path.

Order of checks, each one ending in silence when it fails:

1. The address isn't serving an hour for too many wrong keys.
2. The User-Agent starts with Isobar/.
3. It came over HTTPS (X-Forwarded-Proto from the proxy), unless REQUIRE_HTTPS is off.
4. It's a GET for a path the relay serves.
5. /v1/ping answers here, with no key.
6. Everything else needs a working key (Authorization: Bearer, or ?key= on radar tiles).

Then the per-key rate limit, the only refusal anyone ever sees (429).
"""

import asyncio
import json
import logging
import re
import time

from aiohttp import web

from . import __version__, datasets
from .alerts import Alerts
from .forecast import LocalWeather, Unavailable
from .gate import AuthFailures, RateLimiter, Silence, bearer_key, came_over_https, client_ip, isobar_agent
from .keys import KeyRing
from .obs import Observations
from .radar import Radar
from .upstream import Health, Upstream, UpstreamError
from .zones import Zones

log = logging.getLogger("isobar_relay")

ROUTES = [
    ("ping", re.compile(r"/v1/ping")),
    ("status", re.compile(r"/v1/status")),
    ("alerts", re.compile(r"/v1/alerts")),
    ("alerts_map", re.compile(r"/v1/alerts/map")),
    ("forecast", re.compile(r"/v1/forecast")),
    ("search", re.compile(r"/v1/search")),
    ("air", re.compile(r"/v1/air-quality")),
    ("obs", re.compile(r"/v1/obs")),
    ("radar_frames", re.compile(r"/v1/radar/frames")),
    ("radar_tile", re.compile(r"/v1/radar/(\d{8}-\d{6})/(\d{1,2})/(\d{1,4})/(\d{1,4})\.png")),
]
KEY_RELOAD_EVERY = 10
JSON = "application/json"


def _match(path):
    for name, pattern in ROUTES:
        found = pattern.fullmatch(path)
        if found:
            return name, found.groups()
    return None, None


def _float(query, key, lo, hi):
    try:
        value = float(query[key])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"missing or bad {key}") from exc
    if not lo <= value <= hi:
        raise ValueError(f"{key} out of range")
    return value


def _json_response(body, extra=None, status=200, compress=True):
    if not isinstance(body, (bytes, bytearray)):
        body = json.dumps(body, separators=(",", ":")).encode("utf-8")
    resp = web.Response(body=body, status=status, content_type=JSON, headers=extra or {})
    if compress and len(body) > 1024:
        resp.enable_compression()
    return resp


class Relay:
    def __init__(self, config, keys=None):
        self.cfg = config
        self.started = time.time()
        self.keys = keys or KeyRing(config.keys_file)
        self.silence = Silence(config.silent_hold_seconds, config.silent_hold_max)
        self.fails = AuthFailures(config.auth_fail_limit)
        self.limiter = RateLimiter(config.rate_limit)
        self.health = Health()
        self.upstream = Upstream(config.upstream_agent, self.health)
        self.tracker = datasets.Tracker()
        self.alerts = Alerts(self.upstream, self.tracker, config.data_dir)
        self.zones = Zones(config.data_dir, self.upstream)
        self.alerts.zones = self.zones
        self.weather = LocalWeather(self.upstream, self.tracker, config)
        self.obs = Observations(self.upstream, self.tracker)
        self.radar = Radar(config.tiles_dir, config.enable_radar)
        self.nwws = None
        self._tasks = []

    # ------------------------------------------------------------ lifecycle

    def app(self, background=True):
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_route("*", "/{tail:.*}", self.handle)
        if background:
            app.on_startup.append(self._start)
        else:
            app.on_startup.append(self._start_session)
        app.on_cleanup.append(self._stop)
        return app

    async def _start_session(self, app):
        await self.upstream.start()

    async def _start(self, app):
        await self.upstream.start()
        self.alerts.load()
        spawn = lambda coro, name: self._tasks.append(asyncio.create_task(coro, name=name))  # noqa: E731
        spawn(self._reload_keys(), "keys")
        spawn(self.alerts.run(), "alerts")
        spawn(self.zones.run(), "zones")
        spawn(self.weather.probe_loop(), "probe")
        spawn(self._prune(), "prune")
        if self.cfg.nwws_enabled:
            from .nwws import Ingester

            self.nwws = Ingester(self.cfg, self.alerts, self.health)
            spawn(self.nwws.run(), "nwws")
            log.info("NWWS-OI ingest on for user %s", self.cfg.nwws_user)
        else:
            log.info("NWWS-OI ingest off (no NWWS_USER and NWWS_PASS)")
        log.info("Isobar Server relay %s ready as %r, HTTPS %s, radar %s", __version__, self.cfg.server_label,
                 "required" if self.cfg.require_https else "not required",
                 "on" if self.cfg.enable_radar else "off")

    async def _stop(self, app):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.upstream.close()

    async def _reload_keys(self):
        while True:
            await asyncio.sleep(KEY_RELOAD_EVERY)
            try:
                self.keys.reload_if_changed()
            except Exception:  # noqa: BLE001
                log.exception("key reload crashed")

    async def _prune(self):
        while True:
            await asyncio.sleep(600)
            self.fails.prune()

    # ------------------------------------------------------------ the gate

    async def handle(self, request):
        ip = client_ip(request)
        if self.fails.blocked(ip):
            await self.silence.drop(request)
        if not isobar_agent(request):
            await self.silence.drop(request)
        if self.cfg.require_https and not came_over_https(request):
            await self.silence.drop(request)
        if request.method != "GET":
            await self.silence.drop(request)
        name, groups = _match(request.path)
        if name is None:
            await self.silence.drop(request)
        if name == "ping":
            return self.ping()
        key = bearer_key(request, allow_query=(name == "radar_tile"))
        if not key:
            await self.silence.drop(request)
        label = self.keys.check(key)
        if label is None:
            if self.fails.failed(ip):
                log.warning("wrong-key limit reached: %s gets no answer for an hour", ip)
            else:
                log.info("wrong key from %s", ip)
            await self.silence.drop(request)
        wait = self.limiter.take(label)
        if wait:
            log.info("rate limit: %s, retry in %ss", label, wait)
            return web.Response(status=429, headers={"Retry-After": str(wait), "Cache-Control": "private"})
        try:
            resp = await getattr(self, f"route_{name}")(request, groups)
        except ValueError as exc:
            resp = _json_response({"error": str(exc)}, {"Cache-Control": "private"}, status=400)
        except (Unavailable, UpstreamError) as exc:
            resp = _json_response({"error": str(exc)[:300]}, {"Cache-Control": "private", "Retry-After": "60"},
                                  status=503)
        log.info("%s %s %s", label, request.path, resp.status)
        return resp

    # ------------------------------------------------------------ routes

    def ping(self):
        return _json_response({"service": "isobar-server", "version": __version__, "name": self.cfg.server_label},
                              {"Cache-Control": "no-store"}, compress=False)

    def capabilities(self):
        return {
            "radar": self.radar.available(),
            "nwws": bool(self.cfg.nwws_enabled),
            "air_quality": bool(self.weather.air_ok),
        }

    async def route_status(self, request, groups):
        described = {}
        for dataset in datasets.ALL:
            described[dataset.name] = self.tracker.describe(dataset, self.health)
        described["forecast"]["warming_up"] = self.weather.warming_up
        described["forecast"]["models"] = self.weather.models
        described["search"]["geocoder_ok"] = self.weather.geocoder_ok
        described["radar"].update(self.radar.describe())
        described["radar"]["stale"] = self.cfg.enable_radar and not self.radar.available()
        described["alerts"]["count"] = len(self.alerts.snapshot.features) if self.alerts.snapshot else 0
        described["alerts"]["zones_loaded"] = self.zones.ready
        if self.nwws is not None:
            described["alerts"]["nwws"] = {
                "connected": self.nwws.connected,
                "last_product": datasets.iso(self.nwws.last_product),
                "products": self.nwws.products,
            }
        return _json_response({
            "name": self.cfg.server_label,
            "version": __version__,
            "started": datasets.iso(self.started),
            "capabilities": self.capabilities(),
            "datasets": described,
            "upstreams": {name: {"failing_since": datasets.iso(since), "last_error": self.health.last_error.get(name)}
                          for name, since in self.health.failing_since.items()},
        }, {"Cache-Control": "private"})

    async def _zones_for(self, lat, lon):
        zones = self.zones.zones_at(lat, lon)
        if zones is not None:
            return zones
        # Shapes not downloaded yet: the NWS /points answer names the point's zones.
        try:
            info = await self.obs.point(lat, lon)
        except UpstreamError:
            return frozenset()
        return frozenset((info or {}).get("zones") or ())

    def _alert_headers(self):
        snap = self.alerts.snapshot
        down = self.health.down_since("NWS alerts")
        return datasets.headers(datasets.ALERTS, snap.fetched_at, snap.source, down)

    async def route_alerts(self, request, groups):
        lat = _float(request.query, "lat", -90, 90)
        lon = _float(request.query, "lon", -180, 180)
        if self.alerts.snapshot is None:
            raise Unavailable("no alert snapshot yet")
        zones = await self._zones_for(lat, lon)
        features = self.alerts.for_point(lat, lon, zones)
        body = {"type": "FeatureCollection", "features": features,
                "title": f"Active alerts at {lat:.4f},{lon:.4f}"}
        return _json_response(body, self._alert_headers())

    async def route_alerts_map(self, request, groups):
        raw = (request.query.get("bbox") or "").split(",")
        if len(raw) != 4:
            raise ValueError("bbox must be west,south,east,north")
        try:
            west, south, east, north = (float(v) for v in raw)
        except ValueError as exc:
            raise ValueError("bbox must be four numbers") from exc
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox out of range")
        if self.alerts.snapshot is None:
            raise Unavailable("no alert snapshot yet")
        body = await asyncio.to_thread(self.alerts.for_map, west, south, east, north)
        return _json_response(body, self._alert_headers())

    async def route_forecast(self, request, groups):
        if self.weather.warming_up:
            raise Unavailable("forecast data is still downloading (warming up)")
        body, fetched, dataset = await self.weather.forecast(request.query)
        down = self.health.down_since("local Open-Meteo")
        return _json_response(body, datasets.headers(dataset, fetched, "openmeteo-local", down))

    async def route_search(self, request, groups):
        body, fetched = await self.weather.search(request.query)
        down = self.health.down_since("local geocoder")
        return _json_response(body, datasets.headers(datasets.SEARCH, fetched, "geocoder-local", down))

    async def route_air(self, request, groups):
        if not self.weather.air_ok:
            raise Unavailable("this server does not serve air quality")
        body, fetched, dataset = await self.weather.air_quality(request.query)
        down = self.health.down_since("local Open-Meteo air quality")
        return _json_response(body, datasets.headers(dataset, fetched, "openmeteo-local", down))

    async def route_obs(self, request, groups):
        lat = _float(request.query, "lat", -90, 90)
        lon = _float(request.query, "lon", -180, 180)
        result = await self.obs.latest(lat, lon)
        if result is None:
            return _json_response(b"null", {"Cache-Control": "private"})
        feature, fetched, source = result
        down = self.health.down_since("NWS observations")
        return _json_response(feature, datasets.headers(datasets.OBS, fetched, source, down))

    async def route_radar_frames(self, request, groups):
        if not self.radar.available():
            raise Unavailable("this server is not serving radar right now")
        fetched = self.radar.newest_time()
        return _json_response({"frames": self.radar.frames(), "label": "Derived from NOAA MRMS"},
                              datasets.headers(datasets.RADAR, fetched, "mrms"))

    async def route_radar_tile(self, request, groups):
        if not self.cfg.enable_radar:
            raise Unavailable("radar is off on this server")
        body = await asyncio.to_thread(self.radar.tile, *groups)
        if body is None:
            raise Unavailable("no such radar frame")
        return web.Response(body=body, content_type="image/png",
                            headers={"Cache-Control": "private, max-age=3600", "X-Isobar-Source": "mrms"})
