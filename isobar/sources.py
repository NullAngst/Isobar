"""Upstream data sources.

Forecast and current conditions come from Open-Meteo, which works worldwide.
Alerts, text forecasts, observations and forecast discussions come from the
NWS API (api.weather.gov), so those are US-only. SPC outlooks come straight
from spc.noaa.gov. Radar metadata comes from the Iowa Environmental Mesonet.
"""

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import geo
from .net import FetchError, get_json, polite_wait

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
NWS = "https://api.weather.gov"
NWS_HEADERS = {"Accept": "application/geo+json"}
SPC = "https://www.spc.noaa.gov/products"
IEM = "https://mesonet.agron.iastate.edu"
WWA_QUERY = (
    "https://mapservices.weather.noaa.gov/eventdriven/rest/services/"
    "WWA/watch_warn_adv/MapServer/0/query"
)
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
MCD_QUERY = (
    "https://mapservices.weather.noaa.gov/vector/rest/services/"
    "outlooks/spc_mesoscale_discussion/MapServer/0/query"
)

_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="isobar-fetch")

LATLON_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def _r(value, places=4):
    return round(float(value), places)


def _nws_url(url):
    """URLs taken out of NWS responses are only followed if they point back at the NWS API."""
    if isinstance(url, str) and url.startswith(NWS + "/"):
        return url
    return None


# ---------------------------------------------------------------- locations

def geocode(query):
    query = (query or "").strip()
    if not query:
        return []
    match = LATLON_RE.match(query)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            # No reverse lookup here: this runs as you type, and Nominatim's policy
            # rules out lookups per keystroke. The UI names the place once it's picked.
            return [{"name": f"{lat:.3f}, {lon:.3f}", "lat": lat, "lon": lon, "detail": "Coordinates", "reverse": True}]
    if len(query) < 2:
        return []
    params = {"name": query, "count": 8, "language": "en", "format": "json"}
    if re.fullmatch(r"\d{5}", query):
        params["countryCode"] = "US"
    data = get_json(OPEN_METEO_GEO, params, ttl=86400) or {}
    out = []
    for item in data.get("results") or []:
        if item.get("latitude") is None or item.get("longitude") is None:
            continue
        region = ", ".join(p for p in (item.get("admin1"), item.get("country_code")) if p)
        where = item.get("admin1") or item.get("country")
        out.append({
            "name": f"{item.get('name')}, {where}" if where else str(item.get("name") or ""),
            "detail": region,
            "lat": item.get("latitude"),
            "lon": item.get("longitude"),
        })
    return out


def reverse(lat, lon):
    point = nws_point(lat, lon)
    if point and point.get("city"):
        return f"{point['city']}, {point['state']}"
    try:
        # Rounded to about 1 km so nearby lookups share a cached answer, and
        # spaced at least a second apart per Nominatim's usage policy.
        data = get_json(
            NOMINATIM,
            {"lat": _r(lat, 2), "lon": _r(lon, 2), "format": "jsonv2", "zoom": 10},
            ttl=30 * 86400,
            before=polite_wait,
        ) or {}
        addr = data.get("address") or {}
        place = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
        region = addr.get("state") or addr.get("country")
        if place:
            return f"{place}, {region}" if region else place
    except FetchError:
        pass
    return f"{lat:.3f}, {lon:.3f}"


# ---------------------------------------------------------------- forecast

HOURLY_VARS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m",
    "precipitation_probability", "precipitation", "snowfall", "weather_code",
    "cloud_cover", "visibility", "wind_speed_10m", "wind_direction_10m",
    "wind_gusts_10m", "uv_index", "is_day", "pressure_msl",
]
DAILY_VARS = [
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "apparent_temperature_max", "apparent_temperature_min", "sunrise", "sunset",
    "uv_index_max", "precipitation_sum", "snowfall_sum",
    "precipitation_probability_max", "wind_speed_10m_max", "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
]
CURRENT_VARS = [
    "temperature_2m", "relative_humidity_2m", "apparent_temperature", "is_day",
    "precipitation", "weather_code", "cloud_cover", "pressure_msl",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
]


def open_meteo(lat, lon):
    params = {
        "latitude": _r(lat), "longitude": _r(lon),
        "current": ",".join(CURRENT_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
        "forecast_days": 10,
    }
    return get_json(OPEN_METEO, params, ttl=600)


def air_quality(lat, lon):
    params = {
        "latitude": _r(lat), "longitude": _r(lon),
        "current": "us_aqi,european_aqi,pm2_5,pm10,ozone",
        "timezone": "auto",
    }
    data = get_json(OPEN_METEO_AIR, params, ttl=1800) or {}
    return data.get("current")


# ---------------------------------------------------------------- NWS

def nws_point(lat, lon):
    """Metadata for a US point, or None outside NWS coverage."""
    url = f"{NWS}/points/{_r(lat)},{_r(lon)}"
    try:
        data = get_json(url, ttl=86400, headers=NWS_HEADERS, missing_ok=True)
    except FetchError:
        return None
    if not data:
        return None
    p = data.get("properties") or {}
    rel = ((p.get("relativeLocation") or {}).get("properties")) or {}
    radar = p.get("radarStation") or ""
    return {
        "office": p.get("gridId") or p.get("cwa"),
        "forecast": _nws_url(p.get("forecast")),
        "stations": _nws_url(p.get("observationStations")),
        "radar": radar[1:] if len(radar) == 4 else radar,
        "radar_full": radar,
        "city": rel.get("city"),
        "state": rel.get("state"),
        "tz": p.get("timeZone"),
    }


def nws_periods(url):
    if not _nws_url(url):
        return []
    data = get_json(url, ttl=1800, headers=NWS_HEADERS) or {}
    out = []
    for p in (data.get("properties") or {}).get("periods") or []:
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        out.append({
            "name": p.get("name"),
            "start": p.get("startTime"),
            "end": p.get("endTime"),
            "day": p.get("isDaytime"),
            "temp": p.get("temperature"),
            "unit": p.get("temperatureUnit"),
            "short": p.get("shortForecast"),
            "detail": p.get("detailedForecast"),
            "pop": pop,
        })
    return out


SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}


def nws_alerts(lat, lon):
    data = get_json(
        f"{NWS}/alerts/active",
        {"point": f"{_r(lat)},{_r(lon)}"},
        ttl=60,
        headers=NWS_HEADERS,
    ) or {}
    out = []
    for feat in data.get("features") or []:
        p = feat.get("properties") or {}
        out.append({
            "id": p.get("id") or feat.get("id"),
            "event": p.get("event"),
            "headline": p.get("headline"),
            "severity": p.get("severity"),
            "urgency": p.get("urgency"),
            "certainty": p.get("certainty"),
            "sent": p.get("sent"),
            "effective": p.get("effective"),
            "onset": p.get("onset"),
            "expires": p.get("ends") or p.get("expires"),
            "sender": p.get("senderName"),
            "area": p.get("areaDesc"),
            "description": p.get("description"),
            "instruction": p.get("instruction"),
            "type": p.get("messageType"),
            # Earlier versions of this same alert. Updates get a new id, so the
            # notifier uses these to tell an update from a new alert.
            "references": [r.get("identifier") for r in (p.get("references") or [])[:20]
                           if isinstance(r, dict) and r.get("identifier")],
            "geometry": feat.get("geometry"),
        })
    out.sort(key=lambda a: (SEVERITY_RANK.get(a["severity"], 5), a["event"] or ""))
    return out


def nws_observation(stations_url):
    if not _nws_url(stations_url):
        return None
    stations = get_json(stations_url, ttl=86400, headers=NWS_HEADERS) or {}
    features = stations.get("features") or []
    # Try the two closest stations: the nearest one is sometimes offline.
    for feat in features[:2]:
        sp = feat.get("properties") or {}
        sid = sp.get("stationIdentifier")
        if not sid or not re.fullmatch(r"[A-Z0-9]{3,5}", sid):
            continue
        try:
            data = get_json(f"{NWS}/stations/{sid}/observations/latest", ttl=300, headers=NWS_HEADERS)
        except FetchError:
            continue
        p = (data or {}).get("properties") or {}
        temp = (p.get("temperature") or {}).get("value")
        if temp is None:
            continue
        val = lambda key: (p.get(key) or {}).get("value")  # noqa: E731
        return {
            "station": sid,
            "name": sp.get("name"),
            "time": p.get("timestamp"),
            "text": p.get("textDescription"),
            "temp_c": temp,
            "dew_c": val("dewpoint"),
            "rh": val("relativeHumidity"),
            "wind_kmh": val("windSpeed"),
            "wind_dir": val("windDirection"),
            "gust_kmh": val("windGust"),
            "pressure_pa": val("barometricPressure"),
            "visibility_m": val("visibility"),
        }
    return None


# Text products the Discussion view can show. HWO is the Hazardous Weather
# Outlook, the office's plain summary of what could go wrong over the week.
TEXT_PRODUCTS = {"AFD", "HWO"}


def nws_discussion(office, product="AFD"):
    if not office or not re.fullmatch(r"[A-Z]{3}", office):
        return None
    if product not in TEXT_PRODUCTS:
        raise ValueError("unknown product")
    listing = get_json(f"{NWS}/products/types/{product}/locations/{office}", ttl=600, headers=NWS_HEADERS) or {}
    items = listing.get("@graph") or listing.get("features") or []
    if not items:
        return None
    first = items[0]
    url = _nws_url(first.get("@id"))
    if url is None and re.fullmatch(r"[A-Za-z0-9-]+", str(first.get("id") or "")):
        url = f"{NWS}/products/{first['id']}"
    if url is None:
        return None
    # A product never changes once issued, so it can be cached for a long time.
    data = get_json(url, ttl=86400, headers=NWS_HEADERS) or {}
    return {
        "office": office,
        "product": product,
        "issued": data.get("issuanceTime"),
        "text": data.get("productText"),
        "link": f"https://forecast.weather.gov/product.php?site={office}&issuedby={office}&product={product}",
    }


def weather_bundle(lat, lon):
    """Everything the forecast views need, fetched in parallel.
    Partial failure is fine: missing parts are reported in "errors"."""
    errors = []
    f_om = _pool.submit(open_meteo, lat, lon)
    f_air = _pool.submit(air_quality, lat, lon)
    f_pt = _pool.submit(nws_point, lat, lon)

    point = None
    try:
        point = f_pt.result()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"NWS point: {exc}")

    f_periods = f_alerts = f_obs = f_mcd = None
    if point:
        f_periods = _pool.submit(nws_periods, point["forecast"])
        f_alerts = _pool.submit(nws_alerts, lat, lon)
        f_obs = _pool.submit(nws_observation, point["stations"])
        f_mcd = _pool.submit(mcds_here, lat, lon)

    def take(future, label, default=None):
        if future is None:
            return default
        try:
            return future.result()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            return default

    return {
        "forecast": take(f_om, "Forecast"),
        "air": take(f_air, "Air quality"),
        "nws": point,
        "periods": take(f_periods, "NWS forecast", []),
        "alerts": take(f_alerts, "Alerts", []),
        "obs": take(f_obs, "Observation"),
        "mcds": take(f_mcd, "Mesoscale discussions", []),
        "errors": errors,
    }


# ---------------------------------------------------------------- SPC outlooks

CATEGORY = {
    "TSTM": (0, "General thunderstorms"),
    "MRGL": (1, "Marginal"),
    "SLGT": (2, "Slight"),
    "ENH": (3, "Enhanced"),
    "MDT": (4, "Moderate"),
    "HIGH": (5, "High"),
}


def _spc_urls(day, kind):
    """Candidate URLs for a day/kind. Intensity files have had two names
    (sig* and the newer cig*), so both are tried."""
    if day >= 4:
        return [f"{SPC}/exper/day4-8/day{day}prob.lyr.geojson"], []
    base = f"{SPC}/outlook/day{day}otlk_{kind}.lyr.geojson"
    if kind == "cat":
        return [base], []
    intensity = [
        f"{SPC}/outlook/day{day}otlk_cig{kind}.lyr.geojson",
        f"{SPC}/outlook/day{day}otlk_sig{kind}.lyr.geojson",
    ]
    return [base], intensity


def _spc_time(value):
    if not value or not re.fullmatch(r"\d{12}", str(value)):
        return value
    return datetime.strptime(str(value), "%Y%m%d%H%M").replace(tzinfo=timezone.utc).isoformat()


def _norm_spc(collection):
    feats = []
    for feat in (collection or {}).get("features") or []:
        p = feat.get("properties") or {}
        label = str(p.get("LABEL") or "").strip()
        feats.append({
            "type": "Feature",
            "geometry": feat.get("geometry"),
            "properties": {
                "label": label,
                "label2": p.get("LABEL2") or label,
                "dn": p.get("DN") or 0,
                "fill": p.get("fill"),
                "stroke": p.get("stroke"),
                "valid": _spc_time(p.get("VALID")),
                "expire": _spc_time(p.get("EXPIRE")),
                "issue": _spc_time(p.get("ISSUE")),
            },
        })
    feats.sort(key=lambda f: f["properties"]["dn"])
    return feats


def _is_intensity(label):
    return label.upper().startswith(("SIGN", "CIG"))


def spc_outlook(day, kind):
    day = int(day)
    if not 1 <= day <= 8:
        raise ValueError("day must be 1-8")
    allowed = {1: {"cat", "torn", "wind", "hail"}, 2: {"cat", "torn", "wind", "hail"}, 3: {"cat", "prob"}}
    if day <= 3 and kind not in allowed[day]:
        raise ValueError("unknown outlook kind")
    main_urls, intensity_urls = _spc_urls(day, kind)
    main = None
    for url in main_urls:
        main = get_json(url, ttl=600, missing_ok=True)
        if main:
            break
    features = _norm_spc(main)
    intensity = [f for f in features if _is_intensity(f["properties"]["label"])]
    features = [f for f in features if not _is_intensity(f["properties"]["label"])]
    for url in intensity_urls:
        try:
            extra = get_json(url, ttl=600, missing_ok=True)
        except FetchError:
            extra = None
        if extra:
            intensity += [f for f in _norm_spc(extra) if f["properties"]["label"]]
            break
    meta = features[0]["properties"] if features else {}
    return {
        "day": day,
        "kind": kind,
        "valid": meta.get("valid"),
        "expire": meta.get("expire"),
        "issue": meta.get("issue"),
        "features": features,
        "intensity": intensity,
    }


def _prob_at(lat, lon, outlook):
    best = 0.0
    for f in outlook["features"]:
        try:
            value = float(f["properties"]["label"])
        except ValueError:
            continue
        if value > best and geo.point_in_geometry(lat, lon, f["geometry"]):
            best = value
    # Intensity areas nest (SIGN, or CIG1 inside CIG2 inside CIG3), so keep
    # the strongest one the point falls in, not whichever came last.
    sig = None
    for f in outlook["intensity"]:
        label = f["properties"]["label"]
        if geo.point_in_geometry(lat, lon, f["geometry"]) and _intensity_rank(label) > _intensity_rank(sig):
            sig = label
    return {"pct": round(best * 100), "intensity": sig}


def _intensity_rank(label):
    if not label:
        return -1
    up = label.upper()
    if up.startswith("CIG") and up[3:].isdigit():
        return int(up[3:])
    return 1  # SIGN, the older single "significant" area


def _cat_at(lat, lon, outlook):
    hit = None
    for f in outlook["features"]:
        label = f["properties"]["label"].upper()
        if label in CATEGORY and geo.point_in_geometry(lat, lon, f["geometry"]):
            if hit is None or CATEGORY[label][0] > CATEGORY[hit][0]:
                hit = label
    if hit is None:
        return None
    level, name = CATEGORY[hit]
    return {"label": hit, "name": name, "level": level}


def spc_point(lat, lon):
    jobs = {}
    for day in (1, 2):
        for kind in ("cat", "torn", "wind", "hail"):
            jobs[(day, kind)] = _pool.submit(spc_outlook, day, kind)
    jobs[(3, "cat")] = _pool.submit(spc_outlook, 3, "cat")
    jobs[(3, "prob")] = _pool.submit(spc_outlook, 3, "prob")
    for day in range(4, 9):
        jobs[(day, "prob")] = _pool.submit(spc_outlook, day, "prob")

    results = {}
    for key, future in jobs.items():
        try:
            results[key] = future.result()
        except Exception:  # noqa: BLE001
            results[key] = None

    days = []
    for day in range(1, 9):
        entry = {"day": day, "category": None, "probs": {}}
        cat = results.get((day, "cat"))
        if cat:
            entry["category"] = _cat_at(lat, lon, cat)
            entry["valid"], entry["expire"] = cat["valid"], cat["expire"]
        for kind in ("torn", "wind", "hail", "prob"):
            outlook = results.get((day, kind))
            if outlook and outlook["features"]:
                entry["probs"][kind] = _prob_at(lat, lon, outlook)
                entry.setdefault("valid", outlook["valid"])
                entry.setdefault("expire", outlook["expire"])
        days.append(entry)
    return days


# ---------------------------------------------------------------- watches / warnings

def _offset_for_zoom(zoom):
    if zoom <= 5:
        return 0.04
    if zoom <= 7:
        return 0.015
    if zoom <= 9:
        return 0.004
    return 0.001


def _epoch_to_iso(value):
    if isinstance(value, (int, float)) and value > 1e11:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    return value


def _pick(props, *needles):
    for key, value in props.items():
        low = key.lower()
        if any(n in low for n in needles) and value not in (None, ""):
            return value
    return None


def wwa(sig, bbox, zoom):
    """Active watches (A), warnings (W) or advisories (Y) as GeoJSON for a box."""
    if sig not in ("W", "A", "Y"):
        raise ValueError("sig must be W, A or Y")
    south, west, north, east = (float(v) for v in bbox)
    # Snap outward to a coarse grid that grows with the view, so panning around
    # and zooming in and out land on the same few boxes and reuse the cache.
    span = max(north - south, east - west)
    grid = 2 if span <= 4 else 5 if span <= 12 else 10 if span <= 30 else 20
    south, west = math.floor(south / grid) * grid, math.floor(west / grid) * grid
    north, east = math.ceil(north / grid) * grid, math.ceil(east / grid) * grid
    south, north = max(-90, south), min(90, north)
    zoom = int(zoom)
    params = {
        "where": f"sig='{sig}'",
        "outFields": "*",
        "f": "geojson",
        "outSR": 4326,
        "inSR": 4326,
        "returnGeometry": "true",
        "geometryPrecision": 4,
        "maxAllowableOffset": _offset_for_zoom(zoom),
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "resultRecordCount": 2000,
    }
    features = []
    for page in range(5):
        params["resultOffset"] = page * 2000
        data = get_json(WWA_QUERY, dict(params), ttl=120) or {}
        batch = data.get("features") or []
        features += batch
        exceeded = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
        if not exceeded or not batch:
            break
    out = []
    for feat in features:
        p = feat.get("properties") or {}
        phenom = str(p.get("phenom") or p.get("PHENOM") or "")
        out.append({
            "type": "Feature",
            "geometry": feat.get("geometry"),
            "properties": {
                "phenom": phenom,
                "sig": sig,
                "event": _pick(p, "prod_type", "event") or "",
                "wfo": _pick(p, "wfo"),
                "issued": _epoch_to_iso(_pick(p, "issu")),
                "expires": _epoch_to_iso(_pick(p, "expir")),
                "url": _pick(p, "url"),
            },
        })
    return {"type": "FeatureCollection", "features": out}


# ---------------------------------------------------------------- radar

def radar_sites():
    """WSR-88D sites with coordinates. NWS first, IEM as a fallback."""
    sites = []
    try:
        data = get_json(f"{NWS}/radar/stations", ttl=86400, headers=NWS_HEADERS) or {}
        for feat in data.get("features") or []:
            p = feat.get("properties") or {}
            if p.get("stationType") not in (None, "WSR-88D"):
                continue
            sid = p.get("id") or ""
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if len(sid) == 4 and len(coords) >= 2:
                sites.append({"id": sid[1:], "icao": sid, "name": p.get("name"), "lat": coords[1], "lon": coords[0]})
    except FetchError:
        pass
    if sites:
        return sites
    data = get_json(f"{IEM}/geojson/network/NEXRAD.geojson", ttl=86400) or {}
    for feat in data.get("features") or []:
        p = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        sid = p.get("sid") or feat.get("id")
        if sid and len(coords) >= 2:
            sites.append({"id": sid, "icao": None, "name": p.get("sname"), "lat": coords[1], "lon": coords[0]})
    return sites


def _parse_scan_time(value):
    text = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def radar_frames(site, product, minutes):
    if not re.fullmatch(r"[A-Z0-9]{3}", site or "") or not re.fullmatch(r"[A-Z0-9]{3}", product or ""):
        raise ValueError("bad site or product")
    minutes = max(10, min(int(minutes), 180))
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = now - timedelta(minutes=now.minute % 5) + timedelta(minutes=10)
    start = end - timedelta(minutes=minutes + 10)
    params = {
        "operation": "list", "radar": site, "product": product,
        "start": start.strftime("%Y-%m-%dT%H:%MZ"), "end": end.strftime("%Y-%m-%dT%H:%MZ"),
    }
    data = get_json(f"{IEM}/json/radar.py", params, ttl=60) or {}
    items = []
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                items = value
                break
    stamps = []
    for item in items:
        raw = item.get("ts") or item.get("valid") if isinstance(item, dict) else item
        when = _parse_scan_time(raw) if raw else None
        if when:
            stamps.append(when)
    stamps = sorted(set(stamps))[-30:]
    return [{"stamp": t.strftime("%Y%m%d%H%M"), "iso": t.isoformat()} for t in stamps]


# ---------------------------------------------------------------- model grid

def model_grid(bbox, nx, ny):
    """Current temperature and wind on an nx by ny grid covering bbox."""
    south, west, north, east = (float(v) for v in bbox)
    south, north = max(-84.0, south), min(84.0, north)
    nx, ny = max(3, min(int(nx), 9)), max(3, min(int(ny), 7))
    # Fetch a third more on every side than is on screen, so small pans stay inside it.
    pad_lat, pad_lon = (north - south) / 3, (east - west) / 3
    south, north = max(-84.0, south - pad_lat), min(84.0, north + pad_lat)
    west, east = west - pad_lon, east + pad_lon
    # Fixed step sizes and a snapped origin, so small pans hit the cache
    # instead of costing another batch of Open-Meteo calls.
    nice = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10]
    pick = lambda raw: next((n for n in nice if n >= raw), 10)  # noqa: E731
    step_lat = pick((north - south) / (ny - 1))
    step_lon = pick((east - west) / (nx - 1))
    south = math.floor(south / step_lat) * step_lat
    west = math.floor(west / step_lon) * step_lon
    ny = min(math.ceil((north - south) / step_lat) + 1, 9)
    nx = min(math.ceil((east - west) / step_lon) + 1, 11)
    lats = [round(south + i * step_lat, 3) for i in range(ny)]
    lons = [round(west + j * step_lon, 3) for j in range(nx)]
    lat_list, lon_list = [], []
    for la in lats:
        for lo in lons:
            lat_list.append(str(la))
            lon_list.append(str(((lo + 180) % 360) - 180))
    params = {
        "latitude": ",".join(lat_list),
        "longitude": ",".join(lon_list),
        "current": "temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "timezone": "UTC",
    }
    # Open-Meteo's "current" values move every 15 minutes; 30 is plenty for a map overlay.
    data = get_json(OPEN_METEO, params, ttl=1800)
    if isinstance(data, dict):
        data = [data]
    temp, speed, direction, gust = [], [], [], []
    for item in data or []:
        cur = item.get("current") or {}
        temp.append(cur.get("temperature_2m"))
        speed.append(cur.get("wind_speed_10m"))
        direction.append(cur.get("wind_direction_10m"))
        gust.append(cur.get("wind_gusts_10m"))
    time = ((data or [{}])[0].get("current") or {}).get("time")
    return {"lats": lats, "lons": lons, "temp": temp, "speed": speed, "dir": direction, "gust": gust, "time": time}


# ---------------------------------------------------------------- SPC mesoscale discussions

_MCD_LINK = re.compile(r"https?://www\.spc\.noaa\.gov/products/md/[A-Za-z0-9/_.-]+\.html")
_MCD_TILL = re.compile(r"till\s+(\d{2})(\d{2})\s*UTC", re.IGNORECASE)


def _mcd_number(name):
    match = re.search(r"(\d{1,4})", str(name or ""))
    return int(match.group(1)) if match else None


def _mcd_expires(info, issued_ms):
    """The service says "MD 2348 Active Till 1745 UTC". Turn that into a time,
    on the issue date, or the next day if the hour has already wrapped past midnight."""
    match = _MCD_TILL.search(str(info or ""))
    if not match or not isinstance(issued_ms, (int, float)):
        return None
    issued = datetime.fromtimestamp(issued_ms / 1000, tz=timezone.utc)
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    until = issued.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if until < issued - timedelta(hours=1):
        until += timedelta(days=1)
    return until.isoformat()


def spc_mcds(lat=None, lon=None):
    """Active SPC mesoscale discussions as GeoJSON, from NOAA's map service.
    With a point, each one says whether the point is inside it."""
    data = get_json(MCD_QUERY, {
        "where": "1=1",
        "outFields": "name,popupinfo,folderpath,idp_filedate",
        "f": "geojson",
        "outSR": 4326,
        "returnGeometry": "true",
        "geometryPrecision": 3,
    }, ttl=120) or {}
    out = []
    for feat in data.get("features") or []:
        geometry = feat.get("geometry")
        p = feat.get("properties") or {}
        name = str(p.get("name") or "").strip()
        # With nothing active the service returns one empty "NoArea" placeholder.
        if not geometry or not name or name.lower() == "noarea":
            continue
        number = _mcd_number(name)
        link = _MCD_LINK.match(str(p.get("popupinfo") or "").strip())
        if link:
            link = link.group(0).replace("http://", "https://", 1)
        elif number:
            link = f"https://www.spc.noaa.gov/products/md/md{number:04d}.html"
        else:
            link = "https://www.spc.noaa.gov/products/md/"
        out.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "name": f"Mesoscale discussion {number}" if number else name,
                "number": number,
                "issued": _epoch_to_iso(p.get("idp_filedate")),
                "expires": _mcd_expires(p.get("folderpath"), p.get("idp_filedate")),
                "link": link,
                "here": bool(lat is not None and lon is not None and geo.point_in_geometry(lat, lon, geometry)),
            },
        })
    return {"type": "FeatureCollection", "features": out}


def mcds_here(lat, lon):
    """Properties of the active mesoscale discussions covering a point. A failing
    map service means an empty list here, never a failed alerts request."""
    try:
        return [{**f["properties"], "geometry": f["geometry"]}
                for f in spc_mcds(lat, lon)["features"] if f["properties"]["here"]]
    except FetchError:
        return []


# ---------------------------------------------------------------- local storm reports

REPORT_HOURS = (3, 6, 12, 24, 48)
MAX_REPORTS = 5000


def storm_reports(hours):
    """Local storm reports (tornadoes, hail, wind damage, flooding and so on)
    from the last few hours, nationwide, from IEM. The window snaps up to a
    fixed set so every caller shares the same few cached answers."""
    hours = next((h for h in REPORT_HOURS if h >= int(hours)), REPORT_HOURS[-1])
    data = get_json(f"{IEM}/geojson/lsr.geojson", {"hours": hours}, ttl=300) or {}
    out = []
    for feat in (data.get("features") or [])[:MAX_REPORTS]:
        p = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        out.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "type": str(p.get("type") or "")[:2],
                "text": str(p.get("typetext") or "")[:40],
                "magnitude": str(p.get("magnitude") or "")[:12],
                "unit": str(p.get("unit") or "")[:16],
                "city": str(p.get("city") or "")[:60],
                "county": str(p.get("county") or "")[:40],
                "state": str(p.get("state") or "")[:2],
                "source": str(p.get("source") or "")[:40],
                "remark": str(p.get("remark") or "")[:400],
                "valid": p.get("valid"),
            },
        })
    return {"hours": hours, "type": "FeatureCollection", "features": out}
