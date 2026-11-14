"""Decides which alerts become notifications, on the desktop and on Android.

Kept apart from the Qt code in app.py so it can be tested without a display,
and so the Android background check can use the same rules.

The NWS gives every update of an alert a new id and lists the earlier ids
under "references". Without tracking those, a warning that gets extended or
re-worded every 20 minutes would ping you every 20 minutes. So:

- A new alert notifies once, if the notify mode wants that kind.
- An update of an alert already seen, for the same event, stays quiet.
  An update that becomes more dangerous (a tornado warning that turns into a
  tornado emergency, say) notifies again.
- A cancellation never notifies.
- What has been seen is saved to disk, so restarting Isobar during a long
  winter storm warning doesn't ping you about it again.
- Ids that haven't been active for two days are forgotten, so the file
  doesn't grow forever.
- During quiet hours only critical alerts come through: tornado and extreme
  wind warnings, emergencies, PDS warnings and anything of Extreme severity.
- If the NWS can't be reached for 15 minutes, you get one notice saying so,
  since a quiet app would otherwise look like a quiet sky.
"""

import json
import logging
import os
import time
from datetime import datetime

log = logging.getLogger("isobar.notify")

FORGET_AFTER = 2 * 86400
OUTAGE_AFTER = 15 * 60
MAX_PLACES = 10


def worthy(alert, mode):
    """Whether this alert is the kind the notify mode asks for."""
    if mode == "all":
        return True
    event = (alert.get("event") or "").strip().lower()
    if event.endswith("warning") or alert.get("severity") == "Extreme" or alert.get("flags"):
        return True
    return mode == "watches" and event.endswith("watch")


def tier_of(alert):
    tier = alert.get("tier")
    if tier in ("critical", "high", "normal"):
        return tier
    event = alert.get("event") or ""
    if event in ("Tornado Warning", "Extreme Wind Warning") or alert.get("flags") or alert.get("severity") == "Extreme":
        return "critical"
    return "high" if event.lower().endswith("warning") else "normal"


def title_for(alert):
    """"Tornado Emergency", "PDS Tornado Warning", "Tornado Warning, observed"."""
    event = alert.get("event") or "Weather alert"
    flags = alert.get("flags") or []
    emergency = next((f for f in flags if f.endswith("Emergency")), None)
    if emergency:
        return emergency
    title = f"PDS {event}" if "PDS" in flags else event
    tags = alert.get("tags") or {}
    detection = tags.get("tornado") or tags.get("flood")
    if detection:
        title += f", {detection.lower()}"
    return title


def _rank(alert):
    return {"critical": 2, "high": 1, "normal": 0}[tier_of(alert)] + len(alert.get("flags") or [])


def in_quiet_hours(start, end, hour):
    """True if the hour falls in quiet hours. start -1 means no quiet hours.
    The range can wrap past midnight (22 to 7)."""
    if start is None or start < 0 or start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


class AlertWatcher:
    def __init__(self, path=None):
        self.path = path
        self.seen = {}  # id -> [event, last time active, rank]
        self.failing_since = None
        self.outage_told = False
        self._load()

    # ------------------------------------------------------------ persistence

    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], (int, float)):
                    rank = value[2] if len(value) > 2 and isinstance(value[2], int) else 0
                    self.seen[str(key)] = [str(value[0]), float(value[1]), rank]

    def _save(self):
        if not self.path:
            return
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.seen, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.info("could not save notification state: %s", exc)

    # ------------------------------------------------------------ the rules

    def check(self, alerts, mcds, mode, place="", now=None, quiet=False):
        """Return a list of notices (dicts with title, body, tier) for whatever
        should notify now."""
        now = time.time() if now is None else now
        out = []
        if mode == "off":
            return out
        for alert in alerts or []:
            aid = alert.get("id")
            if not aid:
                continue
            event = alert.get("event") or "Weather alert"
            rank = _rank(alert)
            if aid in self.seen:
                self.seen[aid][1] = now
                continue
            earlier = [self.seen[r] for r in alert.get("references") or [] if r in self.seen]
            self.seen[aid] = [event, now, rank]
            if alert.get("type") == "Cancel":
                continue
            same = [e for e in earlier if e[0] == event]
            # An update of something already seen stays quiet unless it got worse.
            if same and rank <= max(e[2] for e in same):
                continue
            if not worthy(alert, mode):
                continue
            tier = tier_of(alert)
            if quiet and tier != "critical":
                continue
            body = alert.get("headline") or place
            if place and place not in (body or ""):
                body = f"{place}: {body}"
            out.append({"title": title_for(alert), "body": body, "tier": tier, "id": aid})
        if mode in ("watches", "all"):
            for md in mcds or []:
                key = f"mcd:{md.get('number') or md.get('name')}"
                if key in self.seen:
                    self.seen[key][1] = now
                    continue
                self.seen[key] = [key, now, 0]
                if quiet:
                    continue
                title = md.get("name") or "SPC mesoscale discussion"
                out.append({
                    "title": title,
                    "body": f"The Storm Prediction Center is watching {place or 'your area'}. A watch may follow.",
                    "tier": "normal",
                    "id": key,
                })
        cutoff = now - FORGET_AFTER
        self.seen = {k: v for k, v in self.seen.items() if v[1] >= cutoff}
        return out

    def fetch_failed(self, now=None):
        """Call when alerts couldn't be fetched. Returns a notice once the
        outage has lasted OUTAGE_AFTER seconds, and only once per outage."""
        now = time.time() if now is None else now
        if self.failing_since is None:
            self.failing_since = now
        if not self.outage_told and now - self.failing_since >= OUTAGE_AFTER:
            self.outage_told = True
            return {
                "title": "Isobar can't reach the NWS",
                "body": "Weather alerts haven't loaded for 15 minutes. Check your connection, "
                        "or the NWS may be having trouble.",
                "tier": "high",
                "id": "outage",
            }
        return None

    def fetch_ok(self):
        self.failing_since = None
        self.outage_told = False


def places_to_watch(prefs):
    """The current place plus saved places marked for notifications, with
    near-duplicates dropped, at most MAX_PLACES."""
    out = []
    candidates = [prefs.get("location")] + [p for p in prefs.get("saved") or [] if p.get("notify")]
    for place in candidates:
        if not place:
            continue
        if any(abs(place["lat"] - q["lat"]) < 1e-3 and abs(place["lon"] - q["lon"]) < 1e-3 for q in out):
            continue
        out.append(place)
        if len(out) >= MAX_PLACES:
            break
    return out


def collect(watcher, prefs, now=None, hour=None):
    """Fetch alerts for every watched place and return the notices to show.
    Used by the desktop tray and the Android background check."""
    from . import sources
    from .net import FetchError

    mode = prefs.get("notify", "warnings")
    if mode == "off":
        return []
    places = places_to_watch(prefs)
    if not places:
        return []
    hour = datetime.now().hour if hour is None else hour
    quiet = in_quiet_hours(prefs.get("quiet_start", -1), prefs.get("quiet_end", 7), hour)
    notices = []
    failed = False
    many = len(places) > 1
    for place in places:
        if not sources.in_nws_area(place["lat"], place["lon"]):
            continue
        try:
            alerts = sources.nws_alerts(place["lat"], place["lon"])
        except FetchError as exc:
            log.info("alerts for %s failed: %s", place.get("name"), exc)
            failed = True
            continue
        mcds = sources.mcds_here(place["lat"], place["lon"]) if mode in ("watches", "all") else []
        name = place.get("name", "") if many else ""
        notices += watcher.check(alerts, mcds, mode, name or place.get("name", ""), now=now, quiet=quiet)
    if failed:
        outage = watcher.fetch_failed(now)
        if outage:
            notices.append(outage)
    else:
        watcher.fetch_ok()
    watcher._save()
    # Most urgent first, so a burst of notices leads with the one that matters.
    order = {"critical": 0, "high": 1, "normal": 2}
    notices.sort(key=lambda n: order.get(n["tier"], 3))
    return notices
