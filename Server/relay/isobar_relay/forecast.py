"""Forecasts, air quality and place search from the stack's own services.

The local Open-Meteo only has the models its sync workers download, and the
public API's default blend of models from several countries isn't one of
them. So the relay names each model in every query and stitches the answers
itself:

    current conditions, hourly:  short-range (HRRR), then NBM, then GFS
    daily:                       NBM, then GFS, then short-range

A field one model leaves empty is filled from the next. HRRR covers the first
48 hours over the lower 48, NBM days 1 to 11, and GFS everything to day 16 and
every point outside the lower 48. Daily values prefer NBM, since a model that
only covers part of a day would give a part-day high or low.

The relay never calls the public Open-Meteo API. When the local instance fails
and the cache runs out, it answers 503 and the app moves to its next source.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import datasets
from .upstream import UpstreamError

log = logging.getLogger("isobar_relay.forecast")

GRID = 0.05                     # neighbors within about 5 km share one answer
MAX_PARAM = 4000
PASS_THROUGH = {
    "latitude", "longitude", "current", "hourly", "daily", "minutely_15", "timezone", "forecast_days",
    "past_days", "forecast_hours", "past_hours", "temperature_unit", "wind_speed_unit",
    "precipitation_unit", "timeformat", "cell_selection", "start_date", "end_date", "elevation",
    "domains", "forecast_minutely_15", "past_minutely_15",
}
SEARCH_PARAMS = {"name", "count", "language", "format", "countryCode"}
# How old a cached answer may be and still stand in while the local service is down.
FALLBACK_MAX = {"forecast": 12 * 3600, "current": 6 * 3600, "overlay": 6 * 3600, "air_quality": 12 * 3600}
PROBE_EVERY = 10 * 60
PROBE_POINT = (39.0, -98.0)


def _epoch(value):
    """Unix time from an Open-Meteo time, whether it came as a number or ISO text (UTC)."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


class Unavailable(Exception):
    """Nothing to answer with: the app should try its next source."""


class LRU:
    def __init__(self, size):
        self.size = size
        self.data = OrderedDict()

    def get(self, key):
        hit = self.data.get(key)
        if hit is not None:
            self.data.move_to_end(key)
        return hit

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


def _snap(value):
    return f"{round(float(value) / GRID) * GRID:.2f}"


def clean_params(query, allowed):
    out = {}
    for key, value in query.items():
        if key in allowed and isinstance(value, str) and len(value) <= MAX_PARAM:
            out[key] = value
    return out


def classify(params):
    """Which dataset a forecast query is: the map overlay (several points,
    current only), current conditions alone, or the full forecast."""
    multi = "," in params.get("latitude", "")
    only_current = "current" in params and not ({"hourly", "daily", "minutely_15"} & params.keys())
    if multi and only_current:
        return datasets.OVERLAY, 30 * 60
    if only_current:
        return datasets.CURRENT, 10 * 60
    return datasets.FORECAST, 10 * 60


def _align(values, times, base_times):
    if times == base_times or not isinstance(values, list):
        return values
    lookup = dict(zip(times or [], values))
    return [lookup.get(t) for t in base_times]


def merge_block(blocks):
    """Merge one block ("current", "hourly" or "daily") from several models,
    in priority order. Arrays are aligned on "time" and filled value by value."""
    blocks = [b for b in blocks if isinstance(b, dict)]
    if not blocks:
        return None
    base = blocks[0]
    times = base.get("time")
    out = {"time": times} if "time" in base else {}
    if "interval" in base:
        out["interval"] = base["interval"]
    fields = []
    for block in blocks:
        for key in block:
            if key not in ("time", "interval") and key not in fields:
                fields.append(key)
    for field in fields:
        merged = None
        for block in blocks:
            if field not in block:
                continue
            values = block[field]
            if isinstance(values, list):
                values = _align(values, block.get("time"), times)
                if merged is None:
                    merged = list(values)
                elif isinstance(merged, list):
                    merged = [m if m is not None else v for m, v in zip(merged, values)]
            elif merged is None:
                merged = values
        out[field] = merged
    return out


def stitch_one(by_model, order_now, order_daily):
    """One location's answer from each model's answer (None for a model that
    failed). Returns None if no model answered."""
    now_answers = [by_model.get(m) for m in order_now if by_model.get(m)]
    if not now_answers:
        return None
    daily_answers = [by_model.get(m) for m in order_daily if by_model.get(m)]
    base = now_answers[0]
    out = {k: v for k, v in base.items() if k not in (
        "current", "hourly", "daily", "minutely_15", "current_units", "hourly_units", "daily_units",
        "minutely_15_units")}
    for block, answers in (("current", now_answers), ("hourly", now_answers), ("minutely_15", now_answers),
                           ("daily", daily_answers)):
        merged = merge_block([a.get(block) for a in answers])
        if merged is not None:
            out[block] = merged
            units = {}
            for a in answers:
                for k, v in (a.get(f"{block}_units") or {}).items():
                    units.setdefault(k, v)
            out[f"{block}_units"] = units
    return out


def stitch(by_model, order_now, order_daily):
    """Stitch single-point (dict) or multi-point (list) answers."""
    sample = next((v for v in by_model.values() if v is not None), None)
    if sample is None:
        return None
    if isinstance(sample, list):
        count = max(len(v) for v in by_model.values() if isinstance(v, list))
        out = []
        for i in range(count):
            one = {m: (v[i] if isinstance(v, list) and i < len(v) else None) for m, v in by_model.items()}
            out.append(stitch_one(one, order_now, order_daily) or {})
        return out
    return stitch_one(by_model, order_now, order_daily)


class LocalWeather:
    def __init__(self, upstream, tracker, config):
        self.upstream = upstream
        self.tracker = tracker
        self.cfg = config
        self.order_now = [config.short_model, config.nbm_model, config.gfs_model]
        self.order_daily = [config.nbm_model, config.gfs_model, config.short_model]
        self.cache = LRU(5000)
        self.search_cache = LRU(5000)
        self.models = {}        # model -> {"horizon_hours", "checked", "error"}
        self.air_ok = None
        self.geocoder_ok = None
        self.ever_ready = False

    # ------------------------------------------------------------ queries

    async def _om(self, path, params):
        url = f"{self.cfg.open_meteo_url}{path}?{urlencode(params)}"
        answer = await self.upstream.get(url, timeout=25, backoff=False)
        return json.loads(answer.body)

    async def forecast(self, query):
        """(body bytes, fetched_at, dataset) for a forecast query."""
        params = clean_params(query, PASS_THROUGH)
        if "latitude" not in params or "longitude" not in params:
            raise ValueError("latitude and longitude are required")
        dataset, ttl = classify(params)
        if "," not in params["latitude"]:
            params["latitude"], params["longitude"] = _snap(params["latitude"]), _snap(params["longitude"])
        key = ("forecast", tuple(sorted(params.items())))
        return await self._cached(key, ttl, dataset, lambda: self._stitched(params))

    async def _stitched(self, params):
        models = list(dict.fromkeys(self.order_now + self.order_daily))
        results = await asyncio.gather(*(self._om("/v1/forecast", {**params, "models": m}) for m in models),
                                       return_exceptions=True)
        by_model = {}
        errors = []
        for model, result in zip(models, results):
            if isinstance(result, Exception):
                errors.append(f"{model}: {result}")
                by_model[model] = None
            elif isinstance(result, dict) and result.get("error"):
                errors.append(f"{model}: {result.get('reason')}")
                by_model[model] = None
            else:
                by_model[model] = result
        merged = stitch(by_model, self.order_now, self.order_daily)
        if merged is None:
            raise UpstreamError("; ".join(errors) or "no model answered")
        return json.dumps(merged, separators=(",", ":")).encode("utf-8")

    async def air_quality(self, query):
        params = clean_params(query, PASS_THROUGH)
        if "latitude" not in params or "longitude" not in params:
            raise ValueError("latitude and longitude are required")
        if "," not in params["latitude"]:
            params["latitude"], params["longitude"] = _snap(params["latitude"]), _snap(params["longitude"])
        params["models"] = self.cfg.air_model
        key = ("air", tuple(sorted(params.items())))

        async def fetch():
            data = await self._om("/v1/air-quality", params)
            if isinstance(data, dict) and data.get("error"):
                raise UpstreamError(str(data.get("reason")))
            return json.dumps(data, separators=(",", ":")).encode("utf-8")

        return await self._cached(key, 3600, datasets.AIR, fetch, health="local Open-Meteo air quality")

    async def _cached(self, key, ttl, dataset, fetch, health="local Open-Meteo"):
        now = time.time()
        hit = self.cache.get(key)
        if hit and now - hit[1] < ttl:
            return hit[0], hit[1], dataset
        try:
            body = await fetch()
        except (UpstreamError, ValueError) as exc:
            self.upstream.health.fail(health, exc)
            self.tracker.fail(dataset, exc)
            limit = FALLBACK_MAX.get(dataset.name, 6 * 3600)
            if hit and now - hit[1] < limit:
                return hit[0], hit[1], dataset
            raise Unavailable(str(exc)) from exc
        self.upstream.health.ok(health)
        self.cache.put(key, (body, now))
        self.tracker.ok(dataset, "openmeteo-local", now)
        return body, now, dataset

    async def search(self, query):
        params = clean_params(query, SEARCH_PARAMS)
        if not params.get("name"):
            raise ValueError("name is required")
        key = tuple(sorted(params.items()))
        now = time.time()
        hit = self.search_cache.get(key)
        if hit and now - hit[1] < 30 * 86400:
            return hit[0], hit[1]
        url = f"{self.cfg.geocoder_url}/v1/search?{urlencode(params)}"
        try:
            answer = await self.upstream.get(url, timeout=15, backoff=False, name="local geocoder")
            json.loads(answer.body)
        except (UpstreamError, ValueError) as exc:
            self.tracker.fail(datasets.SEARCH, exc)
            if hit:
                return hit[0], hit[1]
            raise Unavailable(str(exc)) from exc
        self.search_cache.put(key, (answer.body, now))
        self.tracker.ok(datasets.SEARCH, "geocoder-local", now)
        return answer.body, now

    # ------------------------------------------------------------ health probe

    async def probe_loop(self):
        while True:
            try:
                await self.probe()
            except Exception:  # noqa: BLE001
                log.exception("forecast probe crashed")
            # Every 2 minutes while warming up, so forecasts start as soon as the first model lands.
            await asyncio.sleep(PROBE_EVERY if self.ever_ready else 120)

    async def probe(self):
        """How far ahead each synced model reaches, at one point in the
        middle of the lower 48. A model whose reach keeps shrinking has a
        stalled sync worker."""
        lat, lon = PROBE_POINT
        now = time.time()
        for model in dict.fromkeys(self.order_now):
            params = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m", "forecast_days": 16,
                      "timezone": "GMT", "timeformat": "unixtime", "models": model}
            try:
                data = await self._om("/v1/forecast", params)
                hourly = data.get("hourly") or {}
                pairs = [(_epoch(t), v) for t, v in zip(hourly.get("time") or [], hourly.get("temperature_2m") or [])
                         if v is not None and _epoch(t) is not None]
                horizon = round((pairs[-1][0] - now) / 3600) if pairs else None
                if horizon and horizon > 0:
                    self.ever_ready = True
                self.models[model] = {"horizon_hours": horizon, "checked": datasets.iso(now), "error": None}
            except (UpstreamError, ValueError, AttributeError, IndexError, TypeError) as exc:
                self.models[model] = {"horizon_hours": None, "checked": datasets.iso(now), "error": str(exc)[:200]}
        try:
            data = await self._om("/v1/air-quality", {"latitude": lat, "longitude": lon, "current": "pm2_5",
                                                       "models": self.cfg.air_model})
            self.air_ok = (data.get("current") or {}).get("pm2_5") is not None
        except (UpstreamError, ValueError, AttributeError):
            self.air_ok = False
        try:
            answer = await self.upstream.get(f"{self.cfg.geocoder_url}/v1/search?name=Denver&count=1",
                                             timeout=15, backoff=False)
            self.geocoder_ok = bool((json.loads(answer.body) or {}).get("results"))
        except (UpstreamError, ValueError, AttributeError):
            self.geocoder_ok = False

    @property
    def warming_up(self):
        """True from startup until a model first shows forecast data. A new
        server's first model download takes a while; until then forecast
        requests get 503 and the app moves on to its next source."""
        return not self.ever_ready
