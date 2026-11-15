"""What the Android home screen widgets show, worked out in Python.

The widgets are drawn by Android (WidgetRender.kt), which can only place text,
pictures and colors. Everything else happens here, once, for both widget
sizes: picking the place, formatting numbers in the user's units and clock,
summing up the alerts, and choosing colors from the app's themes. The result
is a small JSON object the Kotlin side draws as-is.

The widgets read the same caches as the app, so a widget refresh right after
the app loaded the forecast costs no network request at all.
"""

from datetime import datetime, timedelta, timezone

WX_TEXT = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Severe thunderstorms with hail",
}

# The same colors as the app's themes in web/css/app.css.
THEMES = {
    "dark": {"bg": "#1C232B", "ink": "#E8EDF2", "ink2": "#A9B4C0", "accent": "#8FC3FF", "warn": "#EF5B5B"},
    "light": {"bg": "#E9EDF1", "ink": "#18212B", "ink2": "#4A5866", "accent": "#1F6FD1", "warn": "#C92A2A"},
    "midnight": {"bg": "#000000", "ink": "#DFE6EC", "ink2": "#9AA6B2", "accent": "#7FB6F5", "warn": "#EF5B5B"},
    "verdigris": {"bg": "#13211F", "ink": "#E2EEE8", "ink2": "#A5C1B8", "accent": "#5FC6A9", "warn": "#EF6B5B"},
    "kawaii-light": {"bg": "#FFF0F6", "ink": "#4A2A40", "ink2": "#7C5470", "accent": "#E45EA5", "warn": "#D6335C"},
    "kawaii-dark": {"bg": "#241A2C", "ink": "#FBE9F5", "ink2": "#D2B4C9", "accent": "#FF8DC7", "warn": "#FF6B8E"},
}
# Watches get one yellow everywhere, dark enough to read on the light themes.
WATCH_COLOR = {"dark": "#E8C547", "light": "#9A6B00"}
LIGHT_THEMES = {"light", "kawaii-light"}

BG_ALPHA = {"solid": 255, "translucent": 170, "clear": 0}
HOURS_SHOWN = 5


def icon_for(code, is_day=1):
    """Which drawable (wx_<name>) goes with a WMO weather code."""
    code = int(code) if isinstance(code, (int, float)) else -1
    if code in (0, 1):
        return "clear_day" if is_day else "clear_night"
    if code == 2:
        return "partly_day" if is_day else "partly_night"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (56, 57, 66, 67):
        return "sleet"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunder"
    if code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "rain"
    return "cloudy"


def condition(code, is_day=1):
    if code in (0, 1) and not is_day:
        return "Clear" if code == 0 else "Mostly clear"
    if code == 0:
        return "Sunny"
    return WX_TEXT.get(code, "")


def _temp(c, units):
    if c is None:
        return "--"
    value = c * 9 / 5 + 32 if units == "us" else c
    return f"{round(value)}°"


def _clock(dt, clock):
    if clock == "24":
        return dt.strftime("%H:%M")
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _hour_label(dt, clock):
    if clock == "24":
        return dt.strftime("%H:00")
    return f"{dt.hour % 12 or 12} {'AM' if dt.hour < 12 else 'PM'}"


def _wall(text):
    try:
        return datetime.strptime(str(text)[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _local(iso, offset_seconds):
    """An ISO instant as wall-clock time in the place's zone (naive datetime)."""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when.astimezone(timezone.utc) + timedelta(seconds=offset_seconds)).replace(tzinfo=None)


def _alert_level(alert):
    event = (alert.get("event") or "").lower()
    if alert.get("tier") == "critical":
        return "critical"
    if event.endswith("warning"):
        return "warning"
    if event.endswith("watch"):
        return "watch"
    return "advisory"


LEVEL_RANK = {"critical": 0, "warning": 1, "watch": 2, "advisory": 3}


def alert_summary(alerts, offset_seconds, clock):
    """The most serious active alert, how many there are, and until when."""
    if not alerts:
        return None
    ranked = sorted(alerts, key=lambda a: LEVEL_RANK[_alert_level(a)])
    top = ranked[0]
    flags = top.get("flags") or []
    name = next((f for f in flags if f.endswith("Emergency")), None) or top.get("event") or "Weather alert"
    until = _local(top.get("expires"), offset_seconds)
    return {
        "text": name,
        "until": f"until {_clock(until, clock)}" if until else "",
        "level": _alert_level(top),
        "more": len(alerts) - 1,
    }


def colors_for(theme, app_theme):
    """The palette to draw with. "system" can only be settled on the phone,
    so it comes back as both palettes and Kotlin picks by dark mode."""
    name = app_theme if theme == "app" else theme
    if name == "system":
        return {"light": _palette("light"), "dark": _palette("dark")}
    return {"fixed": _palette(name if name in THEMES else "dark")}


def _palette(name):
    colors = dict(THEMES[name])
    colors["watch"] = WATCH_COLOR["light" if name in LIGHT_THEMES else "dark"]
    return colors


def build(prefs, place, forecast, alerts, alerts_ok=True, now_wall=None):
    """The widget's JSON, from settings and already-fetched data. forecast is
    the app's trimmed Open-Meteo answer (sources.open_meteo); alerts is the
    list from sources.nws_alerts, or None when the place has no NWS coverage."""
    units = prefs.get("units", "us")
    clock = prefs.get("clock", "12")
    alpha = BG_ALPHA.get(prefs.get("widget_bg", "solid"), 255)
    colors = colors_for(prefs.get("widget_theme", "app"), prefs.get("theme", "system"))
    if alpha == 0:
        # No background: light text (the layout adds a faint shadow) reads on any wallpaper.
        for palette in colors.values():
            palette.update({"ink": "#FFFFFF", "ink2": "#E4E8EC"})
    out = {
        "place": place.get("name", "") if place else "",
        "colors": colors,
        "alpha": alpha,
        "details": bool(prefs.get("widget_details", True)),
        "show_hours": bool(prefs.get("widget_hours", True)),
        "alert": None,
        "note": "",
    }
    if not place:
        out["note"] = "Open Isobar and pick a place"
        return out
    if not forecast or not forecast.get("current"):
        out["note"] = "Forecast unavailable"
        return out

    cur = forecast["current"]
    daily = forecast.get("daily") or {}
    hourly = forecast.get("hourly") or {}
    offset = forecast.get("utc_offset_seconds") or 0
    is_day = cur.get("is_day", 1)
    code = cur.get("weather_code")

    def first(key):
        values = daily.get(key) or []
        return values[0] if values else None

    rain = first("precipitation_probability_max")
    out.update({
        "temp": _temp(cur.get("temperature_2m"), units),
        "feels": f"Feels like {_temp(cur.get('apparent_temperature'), units)}",
        "hilo": f"H {_temp(first('temperature_2m_max'), units)}  L {_temp(first('temperature_2m_min'), units)}",
        "rain": f"{round(rain)}% rain" if isinstance(rain, (int, float)) and rain >= 10 else "",
        "cond": condition(code, is_day),
        "icon": icon_for(code, is_day),
    })
    updated = _wall(cur.get("time"))
    out["updated"] = _clock(updated, clock) if updated else ""

    # The next few hours, starting with the hour after now.
    now = now_wall or updated
    hours = []
    times = hourly.get("time") or []
    for i, t in enumerate(times):
        wall = _wall(t)
        if wall is None or (now is not None and wall <= now):
            continue
        hours.append({
            "time": _hour_label(wall, clock),
            "temp": _temp((hourly.get("temperature_2m") or [None] * len(times))[i], units),
            "icon": icon_for((hourly.get("weather_code") or [None] * len(times))[i],
                             (hourly.get("is_day") or [1] * len(times))[i]),
        })
        if len(hours) == HOURS_SHOWN:
            break
    out["hours"] = hours

    if prefs.get("widget_alerts", True) and alerts is not None:
        out["alert"] = alert_summary(alerts, offset, clock)
        if not alerts_ok and out["alert"] is None:
            out["note"] = "Alerts unavailable"
    return out


def widget_place(prefs):
    return prefs.get("widget_place") or prefs.get("location")


def collect(prefs):
    """Fetch what the widget needs and build it. Fetches go through the same
    caches as the app, so right after the app refreshed, this makes no
    network request at all."""
    from . import sources
    from .net import FetchError

    place = widget_place(prefs)
    if not place:
        return build(prefs, None, None, None)
    lat, lon = place["lat"], place["lon"]
    try:
        forecast = sources.open_meteo(lat, lon)
    except FetchError:
        forecast = None
    alerts = None
    alerts_ok = True
    if sources.in_nws_area(lat, lon):
        try:
            alerts = sources.nws_alerts(lat, lon)
        except FetchError:
            alerts, alerts_ok = [], False
    return build(prefs, place, forecast, alerts, alerts_ok)
