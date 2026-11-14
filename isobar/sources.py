"""Upstream data sources.

Forecast and current conditions come from Open-Meteo, which works worldwide.
Alerts, text forecasts, observations and forecast discussions come from the
NWS API (api.weather.gov), so those are US-only. SPC outlooks come straight
from spc.noaa.gov. Radar metadata, storm attributes and HRRR run times come
from the Iowa Environmental Mesonet. Tropical cones come from NOAA's map
service and the satellite loop from NESDIS.
"""

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import geo
from .net import FetchError, fetched_at, get_json, polite_wait

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
# NHC's summary service: one layer for every active storm's cone, one for tracks.
TROPICAL = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather_summary/MapServer"
TROPICAL_CONE = 7
TROPICAL_TRACK = 6
# GOES East, 24 hour rolling archives that take a time in exportImage.
NESDIS = "https://satellitemaps.nesdis.noaa.gov/arcgis/rest/services"
SAT_LAYERS = {"13": "ABI13_Last_24hr", "02": "ABIGC_Last_24hr", "09": "ABI10_Last_24hr"}

# Rough boxes around everywhere the NWS issues alerts: the lower 48, Alaska
# (both sides of the antimeridian), Hawaii, Puerto Rico and the Virgin Islands,
# Guam and the Northern Marianas, and American Samoa. A point inside one gets
# its alerts asked for even when the NWS /points lookup fails.
NWS_BOXES = (
    (24.0, 50.0, -125.5, -66.0),
    (51.0, 72.0, -180.0, -129.0),
    (51.0, 53.5, 172.0, 180.0),
    (18.5, 22.5, -161.0, -154.5),
    (17.5, 18.8, -68.0, -64.3),
    (13.0, 21.0, 144.0, 146.5),
    (-14.8, -10.8, -171.5, -168.0),
)

_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="isobar-fetch")

LATLON_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def _r(value, places=4):
    return round(float(value), places)


def in_nws_area(lat, lon):
    """True if the NWS covers this point (roughly: inside one of NWS_BOXES)."""
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in NWS_BOXES)


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


def _forecast_params(lat, lon):
    return {
        "latitude": _r(lat), "longitude": _r(lon),
        "current": ",".join(CURRENT_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
        "forecast_days": 10,
        # Yesterday comes along so Now can say "6 degrees colder than yesterday".
        # It's trimmed off before the UI sees the arrays.
        "past_days": 1,
    }


def open_meteo(lat, lon):
    data = get_json(OPEN_METEO, _forecast_params(lat, lon), ttl=600)
    return split_yesterday(data)


def forecast_age(lat, lon):
    return fetched_at(OPEN_METEO, _forecast_params(lat, lon))


def split_yesterday(data):
    """Take yesterday off the front of the hourly and daily arrays and sum it
    up as {"temp" (same hour yesterday), "max", "min"}. Returns a new dict;
    the cached original is left alone."""
    if not isinstance(data, dict) or not data.get("daily") or not data.get("hourly"):
        return data
    daily = data["daily"]
    hourly = data["hourly"]
    days = daily.get("time") or []
    if len(days) < 2:
        return data
    today = days[1]
    cut = next((i for i, t in enumerate(hourly.get("time") or []) if str(t)[:10] >= today), 0)
    yesterday = {
        "max": (daily.get("temperature_2m_max") or [None])[0],
        "min": (daily.get("temperature_2m_min") or [None])[0],
        "temp": None,
    }
    current_time = str((data.get("current") or {}).get("time") or "")
    if len(current_time) >= 13:
        same_hour = f"{days[0]}T{current_time[11:13]}:00"
        times = hourly.get("time") or []
        if same_hour in times[:cut]:
            yesterday["temp"] = hourly["temperature_2m"][times.index(same_hour)]
    out = dict(data)
    out["hourly"] = {k: (v[cut:] if isinstance(v, list) else v) for k, v in hourly.items()}
    out["daily"] = {k: (v[1:] if isinstance(v, list) else v) for k, v in daily.items()}
    out["yesterday"] = yesterday
    return out


def nowcast(lat, lon):
    """Precipitation in 15 minute steps for the next 3 hours, for the
    "rain starting around" line on Now. Wall-clock times in the place's zone."""
    params = {
        "latitude": _r(lat), "longitude": _r(lon),
        "minutely_15": "precipitation,snowfall",
        "forecast_minutely_15": 12,
        "timezone": "auto",
    }
    data = get_json(OPEN_METEO, params, ttl=600) or {}
    m = data.get("minutely_15") or {}
    times = m.get("time") or []
    if not times:
        return None
    return {"time": times, "precip": m.get("precipitation") or [], "snow": m.get("snowfall") or []}


def air_quality(lat, lon):
    params = {
        "latitude": _r(lat), "longitude": _r(lon),
        "current": "us_aqi,european_aqi,pm2_5,pm10,ozone",
        "timezone": "auto",
    }
    data = get_json(OPEN_METEO_AIR, params, ttl=1800) or {}
    return data.get("current")


# ---------------------------------------------------------------- NWS

def nws_point(lat, lon, strict=False):
    """Metadata for a US point, or None outside NWS coverage. With strict, a
    failed lookup raises FetchError instead of looking like "not covered"."""
    url = f"{NWS}/points/{_r(lat)},{_r(lon)}"
    try:
        data = get_json(url, ttl=86400, headers=NWS_HEADERS, missing_ok=True)
    except FetchError:
        if strict:
            raise
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
CRITICAL_EVENTS = {"Tornado Warning", "Extreme Wind Warning"}
EMERGENCY_RE = re.compile(r"\b(TORNADO|FLASH FLOOD) EMERGENCY\b")
PDS_RE = re.compile(r"PARTICULARLY DANGEROUS SITUATION")


def alerts_params(lat, lon):
    """Query for the NWS alerts endpoint. Test and exercise messages are left out."""
    return {"point": f"{_r(lat)},{_r(lon)}", "status": "actual"}


def _param(params, key):
    values = (params or {}).get(key)
    if isinstance(values, list) and values:
        value = values[0]
    else:
        value = values
    return str(value).strip()[:40] if value not in (None, "") else None


def alert_tags(params):
    """The impact-based warning tags NWS puts in an alert's parameters: how a
    tornado was detected, hail size, peak gust and the damage threat."""
    tags = {}
    tornado = _param(params, "tornadoDetection")
    if tornado:
        tags["tornado"] = tornado.title()
    flood = _param(params, "flashFloodDetection")
    if flood:
        tags["flood"] = flood.title()
    hail = _param(params, "maxHailSize")
    if hail:
        try:
            tags["hail_in"] = float(re.sub(r"[^0-9.]", "", hail))
        except ValueError:
            pass
    gust = _param(params, "maxWindGust")
    if gust:
        match = re.search(r"(\d+)", gust)
        if match:
            tags["wind_mph"] = int(match.group(1))
    for key in ("tornadoDamageThreat", "thunderstormDamageThreat", "flashFloodDamageThreat"):
        threat = _param(params, key)
        if threat:
            tags["damage"] = threat.title()
            break
    return tags


def alert_flags(event, headline, description):
    text = f"{headline or ''}\n{description or ''}".upper()
    flags = []
    emergency = EMERGENCY_RE.search(text)
    if emergency:
        flags.append(f"{emergency.group(1).title()} Emergency")
    if PDS_RE.search(text):
        flags.append("PDS")
    return flags


def alert_tier(alert):
    """critical: tornado and extreme wind warnings, emergencies, PDS and Extreme
    severity. high: other warnings. normal: everything else."""
    event = alert.get("event") or ""
    if event in CRITICAL_EVENTS or alert.get("flags") or alert.get("severity") == "Extreme":
        return "critical"
    if event.lower().endswith("warning"):
        return "high"
    return "normal"


def nws_alerts(lat, lon):
    data = get_json(
        f"{NWS}/alerts/active",
        alerts_params(lat, lon),
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
            "tags": alert_tags(p.get("parameters")),
            "flags": alert_flags(p.get("event"), p.get("headline"), p.get("description")),
        })
    for alert in out:
        alert["tier"] = alert_tier(alert)
    tier_rank = {"critical": 0, "high": 1, "normal": 2}
    out.sort(key=lambda a: (tier_rank[a["tier"]], SEVERITY_RANK.get(a["severity"], 5), a["event"] or ""))
    return out


def alerts_age(lat, lon):
    """When the cached alerts for this point were last confirmed by the NWS."""
    return fetched_at(f"{NWS}/alerts/active", alerts_params(lat, lon))


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
    url = _product_url(items[0])
    if url is None:
        return None
    # A product never changes once issued, so it can be cached for a long time.
    data = get_json(url, ttl=86400, headers=NWS_HEADERS) or {}
    previous = None
    prev_url = _product_url(items[1]) if len(items) > 1 else None
    if prev_url:
        # Only used to mark what changed, so a failure here costs nothing.
        try:
            old = get_json(prev_url, ttl=86400, headers=NWS_HEADERS) or {}
            previous = {"issued": old.get("issuanceTime"), "text": old.get("productText")}
        except FetchError:
            previous = None
    return {
        "office": office,
        "product": product,
        "issued": data.get("issuanceTime"),
        "text": data.get("productText"),
        "previous": previous,
        "link": f"https://forecast.weather.gov/product.php?site={office}&issuedby={office}&product={product}",
    }


def _product_url(item):
    url = _nws_url(item.get("@id"))
    if url is None and re.fullmatch(r"[A-Za-z0-9-]+", str(item.get("id") or "")):
        url = f"{NWS}/products/{item['id']}"
    return url


def weather_bundle(lat, lon):
    """Everything the forecast views need, fetched in parallel.
    Partial failure is fine: missing parts are reported in "errors"."""
    errors = []
    covered = in_nws_area(lat, lon)
    f_om = _pool.submit(open_meteo, lat, lon)
    f_air = _pool.submit(air_quality, lat, lon)
    f_now = _pool.submit(nowcast, lat, lon)
    f_pt = _pool.submit(nws_point, lat, lon, True)
    # Alerts don't depend on the /points lookup, so a failing one can't hide them.
    f_alerts = _pool.submit(nws_alerts, lat, lon) if covered else None
    f_mcd = _pool.submit(mcds_here, lat, lon) if covered else None

    point = None
    try:
        point = f_pt.result()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"NWS point lookup: {exc}")

    f_periods = f_obs = None
    if point:
        f_periods = _pool.submit(nws_periods, point["forecast"])
        f_obs = _pool.submit(nws_observation, point["stations"])
        if f_alerts is None:
            f_alerts = _pool.submit(nws_alerts, lat, lon)
            f_mcd = _pool.submit(mcds_here, lat, lon)

    def take(future, label, default=None):
        if future is None:
            return default
        try:
            return future.result()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            return default

    forecast = take(f_om, "Forecast")
    alerts_ok = f_alerts is not None
    alerts = take(f_alerts, "Alerts", [])
    if alerts_ok and any(e.startswith("Alerts:") for e in errors):
        alerts_ok = False
    try:
        now = f_now.result()
    except Exception:  # noqa: BLE001
        now = None  # the rain timing line is a nicety; no error for it
    return {
        "forecast": forecast,
        "nowcast": now,
        "air": take(f_air, "Air quality"),
        "nws": point,
        # True for any point the NWS covers, whether or not /points answered.
        "us": covered or bool(point),
        "periods": take(f_periods, "NWS forecast", []),
        "alerts": alerts,
        "alerts_ok": alerts_ok,
        "obs": take(f_obs, "Observation"),
        "mcds": take(f_mcd, "Mesoscale discussions", []),
        "ages": {
            "forecast": forecast_age(lat, lon),
            "alerts": alerts_age(lat, lon) if f_alerts is not None else None,
        },
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


def lon_ranges(west, east):
    """Split a west-east span into pieces inside -180..180. A map panned past
    the antimeridian gives spans like -200..-150, which the map services refuse."""
    if east - west >= 360:
        return [(-180.0, 180.0)]
    while west < -180:
        west, east = west + 360, east + 360
    while west >= 180:
        west, east = west - 360, east - 360
    if east <= 180:
        return [(west, east)]
    return [(west, 180.0), (-180.0, east - 360)]


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
    features = []
    for w, e in lon_ranges(west, east):
        features += _wwa_box(sig, south, w, north, e, zoom)
    return {"type": "FeatureCollection", "features": features}


def _wwa_box(sig, south, west, north, east, zoom):
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
    return out


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
    nx, ny = max(3, min(int(nx), 7)), max(3, min(int(ny), 5))
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
    # Open-Meteo counts every point in a request against its free daily limit,
    # so the grid stays at 48 points or fewer.
    ny = min(math.ceil((north - south) / step_lat) + 1, 6)
    nx = min(math.ceil((east - west) / step_lon) + 1, 8)
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


# ---------------------------------------------------------------- NEXRAD storm attributes

def _lower(props):
    return {str(k).lower(): v for k, v in (props or {}).items()}


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def storm_attrs():
    """Storm cells the radars are tracking (Level III storm attribute table):
    tornado vortex and mesocyclone flags, hail odds and size, and motion.
    Only cells worth a marker are kept: a TVS, a mesocyclone, or hail."""
    data = get_json(f"{IEM}/geojson/nexrad_attr.py", ttl=120) or {}
    out = []
    for feat in (data.get("features") or [])[:6000]:
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = _num(coords[0]), _num(coords[1])
        if lon is None or lat is None:
            continue
        p = _lower(feat.get("properties"))
        tvs = str(p.get("tvs") or "").strip().upper()
        meso = str(p.get("meso") or "").strip().upper()
        poh = _num(p.get("poh")) or 0
        posh = _num(p.get("posh")) or 0
        size = _num(p.get("max_size")) or 0
        has_tvs = tvs not in ("", "NONE", "0")
        has_meso = meso not in ("", "NONE", "0")
        if not (has_tvs or has_meso or posh >= 30 or size >= 1.0):
            continue
        out.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "radar": str(p.get("nexrad") or "")[:4],
                "id": str(p.get("storm_id") or "")[:4],
                "tvs": tvs[:8] if has_tvs else "",
                "meso": meso[:8] if has_meso else "",
                "poh": poh,
                "posh": posh,
                "size": size,
                "dbz": _num(p.get("max_dbz")),
                "top": _num(p.get("top")),
                "drct": _num(p.get("drct")),
                "sknt": _num(p.get("sknt")),
                "valid": p.get("valid"),
            },
        })
    return {"type": "FeatureCollection", "features": out}


# ---------------------------------------------------------------- HRRR run time

def hrrr_run():
    """The latest HRRR run IEM has finished, as a UTC stamp (YYYYMMDDHHMI) and
    ISO time, so future radar frames can name their valid times."""
    data = get_json(f"{IEM}/data/gis/images/4326/hrrr/refd_1080.json", ttl=600) or {}
    raw = str(data.get("model_init_utc") or "")
    when = _parse_scan_time(raw)
    if when is None:
        return None
    return {"stamp": when.strftime("%Y%m%d%H%M"), "iso": when.isoformat()}


# ---------------------------------------------------------------- satellite loop

def sat_info(band):
    """The newest image time in NESDIS's 24 hour GOES East archive for a band."""
    layer = SAT_LAYERS.get(str(band))
    if layer is None:
        raise ValueError("unknown band")
    data = get_json(f"{NESDIS}/{layer}/ImageServer", {"f": "json"}, ttl=300) or {}
    extent = (data.get("timeInfo") or {}).get("timeExtent") or []
    end = extent[1] if len(extent) > 1 and isinstance(extent[1], (int, float)) else None
    return {"layer": layer, "end": end, "url": f"{NESDIS}/{layer}/ImageServer/exportImage"}


# ---------------------------------------------------------------- tropical

def _tropical_layer(layer):
    params = {"where": "1=1", "outFields": "*", "f": "geojson", "outSR": 4326, "returnGeometry": "true"}
    data = get_json(f"{TROPICAL}/{layer}/query", params, ttl=600) or {}
    out = []
    for feat in data.get("features") or []:
        if not feat.get("geometry"):
            continue
        p = _lower(feat.get("properties"))
        out.append({
            "type": "Feature",
            "geometry": feat.get("geometry"),
            "properties": {
                "name": str(p.get("stormname") or p.get("storm_name") or "").strip().title()[:40],
                "kind": str(p.get("stormtype") or "").strip()[:4],
                "advisory": str(p.get("advisnum") or p.get("advnum") or "").strip()[:8],
                "basin": str(p.get("basin") or "").strip()[:4],
            },
        })
    return out


def tropical():
    """Forecast cones and tracks for every active Atlantic and Pacific storm.
    Outside hurricane season both lists are usually empty."""
    return {
        "cones": {"type": "FeatureCollection", "features": _tropical_layer(TROPICAL_CONE)},
        "tracks": {"type": "FeatureCollection", "features": _tropical_layer(TROPICAL_TRACK)},
    }
