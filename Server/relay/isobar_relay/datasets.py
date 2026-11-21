"""What each dataset is, how stale it may get, and the headers that say so.

Freshness rides in headers so response bodies keep the upstream's own shape:

    X-Isobar-Fetched-At           when the relay got this copy from upstream
    X-Isobar-Source               nws, iem, nwws, openmeteo-local, geocoder-local, mrms
    X-Isobar-Stale                1 once the copy is older than the dataset allows
    X-Isobar-Upstream-Down-Since  only while the primary source is failing
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Dataset:
    name: str
    stale_after: float      # seconds; 0 means it never goes stale
    primary: str            # the Health name of its primary upstream


ALERTS = Dataset("alerts", 5 * 60, "NWS alerts")
FORECAST = Dataset("forecast", 3 * 3600, "local Open-Meteo")
CURRENT = Dataset("current", 2 * 3600, "local Open-Meteo")
OVERLAY = Dataset("overlay", 2 * 3600, "local Open-Meteo")
SEARCH = Dataset("search", 0, "local geocoder")
AIR = Dataset("air_quality", 6 * 3600, "local Open-Meteo air quality")
OBS = Dataset("observations", 90 * 60, "NWS observations")
RADAR = Dataset("radar", 6 * 60, "MRMS radar")

ALL = (ALERTS, FORECAST, OVERLAY, SEARCH, AIR, OBS, RADAR)


def iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_stale(dataset, fetched_at, now=None):
    if not dataset.stale_after or fetched_at is None:
        return False
    now = time.time() if now is None else now
    return now - fetched_at > dataset.stale_after


def headers(dataset, fetched_at, source, down_since=None, now=None):
    out = {
        "X-Isobar-Fetched-At": iso(fetched_at) or "",
        "X-Isobar-Source": source,
        "X-Isobar-Stale": "1" if is_stale(dataset, fetched_at, now) else "0",
        # Nothing here may be stored by a caching proxy and handed to someone without a key.
        "Cache-Control": "private",
    }
    if down_since:
        out["X-Isobar-Upstream-Down-Since"] = iso(down_since)
    return out


class Tracker:
    """The last good fetch of each dataset, for /v1/status."""

    def __init__(self):
        self.fetched = {}
        self.source = {}
        self.errors = {}

    def ok(self, dataset, source, fetched_at=None):
        self.fetched[dataset.name] = time.time() if fetched_at is None else fetched_at
        self.source[dataset.name] = source
        self.errors.pop(dataset.name, None)

    def fail(self, dataset, error):
        if dataset.name not in self.errors:
            self.errors[dataset.name] = {"error": str(error)[:300], "since": time.time()}
        else:
            self.errors[dataset.name]["error"] = str(error)[:300]

    def describe(self, dataset, health, now=None):
        fetched = self.fetched.get(dataset.name)
        err = self.errors.get(dataset.name)
        down = health.down_since(dataset.primary)
        return {
            "fetched_at": iso(fetched),
            "source": self.source.get(dataset.name),
            "stale": is_stale(dataset, fetched, now),
            "stale_after_seconds": dataset.stale_after or None,
            "last_error": (err or {}).get("error") or health.last_error.get(dataset.primary),
            "error_since": iso((err or {}).get("since")),
            "upstream_down_since": iso(down),
        }
