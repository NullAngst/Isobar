"""User settings stored as JSON in the config dir."""

import json
import math
import os
import re
import threading

from .paths import config_dir

DEFAULTS = {
    "units": "us",              # us | metric
    "clock": "12",              # 12 | 24
    "theme": "system",          # system | dark | light | midnight
    "start_view": "now",        # now | forecast | radar | outlooks | alerts | discussion
    "basemap": "satellite",     # satellite | auto | dark | light | streets (all but satellite need a CARTO key)
    "carto_key": "",            # free key from carto.com/basemaps/apikey
    "radar_palette": "default",
    "radar_opacity": 0.85,
    "radar_min_dbz": 5,
    "radar_loop_minutes": 60,
    "radar_speed_ms": 450,
    "radar_product": "refl",    # refl | vel | srv | tops | rain | future | sat | wind | temp
    "radar_site": "mosaic",     # mosaic | auto | radar ID such as FFC
    "radar_options": {"rain": "q2-n1p", "sat": "13"},
    "radar_layers": {
        "warnings": True,
        "watches": True,
        "advisories": False,
        "outlook": False,
        "counties": False,
        "sites": False,
    },
    "notify": "warnings",       # off | warnings | all
    "tray_on_close": False,
    "location": None,           # {"name", "lat", "lon"}
    "saved": [],                # list of {"name", "lat", "lon"}
}

_lock = threading.Lock()


def _file():
    return config_dir() / "settings.json"


# Values that end up inside URLs or file names get a strict shape. Anything
# that doesn't fit is dropped and the previous value stays.
PATTERNS = {
    "radar_site": re.compile(r"mosaic|auto|[A-Z0-9]{3,4}"),
    "carto_key": re.compile(r"[A-Za-z0-9._~-]{0,200}"),
    "radar_product": re.compile(r"[a-z]{2,12}"),
    "radar_palette": re.compile(r"[a-z0-9_-]{1,24}"),
    "start_view": re.compile(r"[a-z]{2,16}"),
}
OPTION_VALUE = re.compile(r"[a-z0-9-]{1,16}")
MAX_SAVED = 50


def _place(value):
    """A {name, lat, lon} dict with sane values, or None."""
    if not isinstance(value, dict):
        return None
    try:
        lat, lon = float(value.get("lat")), float(value.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    name = str(value.get("name") or f"{lat:.3f}, {lon:.3f}")[:120]
    return {"name": name, "lat": lat, "lon": lon}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid(key, default, value):
    """Return the cleaned value, or raise ValueError if it doesn't fit."""
    if key == "location":
        if value is None:
            return None
        place = _place(value)
        if place is None:
            raise ValueError(key)
        return place
    if key == "saved":
        if not isinstance(value, list):
            raise ValueError(key)
        places = [p for p in (_place(v) for v in value[:MAX_SAVED]) if p]
        return places
    if key == "radar_options":
        if not isinstance(value, dict):
            raise ValueError(key)
        return {k: v for k, v in value.items() if k in default and isinstance(v, str) and OPTION_VALUE.fullmatch(v)}
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(key)
        return value
    if isinstance(default, (int, float)):
        if not _number(value):
            raise ValueError(key)
        return value
    if isinstance(default, str):
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError(key)
        pattern = PATTERNS.get(key)
        if pattern is not None and not pattern.fullmatch(value):
            raise ValueError(key)
        return value
    raise ValueError(key)


def _merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        if key not in base:
            continue
        if key == "radar_layers" and isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = _merge(base[key], value)
            continue
        try:
            cleaned = _valid(key, DEFAULTS.get(key, base[key]), value)
        except ValueError:
            continue
        if key == "radar_options":
            cleaned = {**base[key], **cleaned}
        out[key] = cleaned
    return out


def load() -> dict:
    with _lock:
        try:
            data = json.loads(_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return _merge(DEFAULTS, data)


def save(patch: dict) -> dict:
    current = load()
    merged = _merge(current, patch)
    with _lock:
        tmp = _file().with_suffix(".tmp")
        # Owner-only: the file can hold a CARTO key and your saved places.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(merged, indent=2))
        tmp.replace(_file())
    return merged
