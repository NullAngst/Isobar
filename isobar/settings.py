"""User settings stored as JSON in the config dir."""

import json
import threading

from .paths import config_dir

DEFAULTS = {
    "units": "us",              # us | metric
    "clock": "12",              # 12 | 24
    "theme": "system",          # system | dark | light | midnight
    "start_view": "now",        # now | hourly | radar | outlooks | alerts | discussion
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


def _merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = _merge(base[key], value)
        else:
            out[key] = value
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
        tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        tmp.replace(_file())
    return merged
