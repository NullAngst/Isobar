"""NWS point lookups and the latest station observation for a point.

/points answers (grid, nearest stations, zones) are cached 30 days per
rounded coordinate, station lists 30 days, observations 10 minutes. When the
NWS observation endpoint fails, IEM's current observations for the same
stations stand in, reshaped into the NWS layout so the app reads them the same.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from . import datasets
from .upstream import UpstreamError

log = logging.getLogger("isobar_relay.obs")

NWS = "https://api.weather.gov"
IEM_CURRENTS = "https://mesonet.agron.iastate.edu/api/1/currents.json"
NWS_HEADERS = {"Accept": "application/geo+json"}
POINT_TTL = 30 * 86400
OBS_TTL = 10 * 60
STATION_RE = re.compile(r"[A-Z0-9]{3,5}")


def _last_segment(url):
    if isinstance(url, str) and url.startswith(NWS + "/"):
        return url.rstrip("/").rsplit("/", 1)[-1]
    return None


def _value(number, unit):
    return {"unitCode": f"wmoUnit:{unit}", "value": number, "qualityControl": "V"}


def _f2c(f):
    return None if f is None else round((float(f) - 32) * 5 / 9, 1)


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN check


def iem_station(sid):
    """IEM's id for an NWS station: K-prefixed lower-48 stations drop the K."""
    if len(sid) == 4 and sid.startswith("K"):
        return sid[1:]
    return sid


def from_iem(row, sid, name):
    """An NWS-shaped latest observation from one row of IEM's currents API."""
    tmpf, dwpf = _num(row.get("tmpf")), _num(row.get("dwpf"))
    sknt, gust = _num(row.get("sknt")), _num(row.get("gust"))
    mslp, alti = _num(row.get("mslp")), _num(row.get("alti"))
    vsby = _num(row.get("vsby"))
    pressure = mslp * 100 if mslp else (alti * 3386.39 if alti else None)
    valid = row.get("utc_valid")
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [_num(row.get("lon")), _num(row.get("lat"))]},
        "properties": {
            "station": f"{NWS}/stations/{sid}",
            "stationId": sid,
            "stationName": name or row.get("name"),
            "timestamp": str(valid).replace("Z", "+00:00") if valid else None,
            "textDescription": " ".join(str(row.get("wxcodes") or "").split()) or None,
            "temperature": _value(_f2c(tmpf), "degC"),
            "dewpoint": _value(_f2c(dwpf), "degC"),
            "relativeHumidity": _value(_num(row.get("relh")), "percent"),
            "windSpeed": _value(None if sknt is None else round(sknt * 1.852, 1), "km_h-1"),
            "windDirection": _value(_num(row.get("drct")), "degree_(angle)"),
            "windGust": _value(None if gust is None else round(gust * 1.852, 1), "km_h-1"),
            "barometricPressure": _value(pressure, "Pa"),
            "visibility": _value(None if vsby is None else round(vsby * 1609.344), "m"),
        },
    }


class Observations:
    def __init__(self, upstream, tracker):
        self.upstream = upstream
        self.tracker = tracker
        self._points = {}
        self._stations = {}
        self._obs = {}          # station id -> (feature, fetched_at, source)

    async def _json(self, url, name, params=None):
        answer = await self.upstream.get(url, params=params, headers=NWS_HEADERS, timeout=20, name=name)
        return json.loads(answer.body)

    async def point(self, lat, lon):
        """{"zones": [...], "stations": url} for a point, or None outside NWS coverage."""
        key = (round(lat, 2), round(lon, 2))
        hit = self._points.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            data = await self._json(f"{NWS}/points/{key[0]:.4f},{key[1]:.4f}", "NWS points")
        except UpstreamError as exc:
            if exc.status == 404:
                self._points[key] = (time.time() + 86400, None)
                return None
            if hit:
                return hit[1]
            raise
        props = data.get("properties") or {}
        zones = [z for z in (_last_segment(props.get(k)) for k in ("county", "forecastZone", "fireWeatherZone")) if z]
        info = {"zones": zones, "stations": props.get("observationStations")}
        if len(self._points) > 20000:
            self._points.clear()
        self._points[key] = (time.time() + POINT_TTL, info)
        return info

    async def stations(self, url):
        if not (isinstance(url, str) and url.startswith(NWS + "/")):
            return []
        hit = self._stations.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            data = await self._json(url, "NWS stations")
        except UpstreamError:
            if hit:
                return hit[1]
            raise
        out = []
        for feat in (data.get("features") or [])[:4]:
            p = feat.get("properties") or {}
            sid = str(p.get("stationIdentifier") or "")
            if STATION_RE.fullmatch(sid):
                out.append((sid, p.get("name")))
        if len(self._stations) > 20000:
            self._stations.clear()
        self._stations[url] = (time.time() + POINT_TTL, out)
        return out

    async def latest(self, lat, lon):
        """(feature, fetched_at, source) for the nearest station that reports a
        temperature, or None if there's none. Raises UpstreamError when nothing
        can be had at all."""
        info = await self.point(lat, lon)
        if not info:
            return None
        stations = (await self.stations(info["stations"]))[:2]
        if not stations:
            return None
        now = time.time()
        held = None
        answered = False
        for sid, name in stations:
            cached = self._obs.get(sid)
            if cached and now - cached[1] < OBS_TTL:
                return cached
            try:
                data = await self._json(f"{NWS}/stations/{sid}/observations/latest", "NWS observations")
            except UpstreamError as exc:
                self.tracker.fail(datasets.OBS, exc)
                if cached and held is None:
                    held = cached
                continue
            answered = True
            props = data.get("properties") or {}
            if (props.get("temperature") or {}).get("value") is None:
                continue
            props.setdefault("stationId", sid)
            props.setdefault("stationName", name)
            result = (data, now, "nws")
            self._remember(sid, result)
            self.tracker.ok(datasets.OBS, "nws", now)
            return result
        # The NWS didn't give one: IEM's current observations for the same stations.
        if self.upstream.health.down_since("NWS observations"):
            for sid, name in stations:
                try:
                    answer = await self.upstream.get(IEM_CURRENTS, params={"station": iem_station(sid)},
                                                     timeout=20, name="IEM observations")
                    rows = (json.loads(answer.body) or {}).get("data") or []
                except (UpstreamError, ValueError) as exc:
                    log.info("IEM observations: %s", exc)
                    break
                row = rows[0] if rows else None
                if row and _num(row.get("tmpf")) is not None:
                    result = (from_iem(row, sid, name), now, "iem")
                    self._remember(sid, result)
                    self.tracker.ok(datasets.OBS, "iem", now)
                    return result
        if held:
            return held
        if answered:
            return None
        raise UpstreamError("no observation available")

    def _remember(self, sid, result):
        if len(self._obs) > 20000:
            self._obs.clear()
        self._obs[sid] = result


def obs_time(feature):
    stamp = ((feature or {}).get("properties") or {}).get("timestamp")
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None
