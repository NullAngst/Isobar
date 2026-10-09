"""Decides which alerts become desktop notifications.

Kept apart from the Qt code in app.py so it can be tested without a display.

The NWS gives every update of an alert a new id and lists the earlier ids
under "references". Without tracking those, a warning that gets extended or
re-worded every 20 minutes would ping you every 20 minutes. So:

- A new alert notifies once, if the notify mode wants that kind.
- An update of an alert already seen, for the same event, stays quiet.
- A cancellation never notifies.
- Ids that haven't been active for two days are forgotten, so the set doesn't
  grow forever on a machine that runs for months.
"""

import time

FORGET_AFTER = 2 * 86400


def worthy(alert, mode):
    """Whether this alert is the kind the notify mode asks for."""
    if mode == "all":
        return True
    event = (alert.get("event") or "").strip().lower()
    if event.endswith("warning") or alert.get("severity") == "Extreme":
        return True
    return mode == "watches" and event.endswith("watch")


class AlertWatcher:
    def __init__(self):
        self.seen = {}  # id -> (event, last time it was active)

    def check(self, alerts, mcds, mode, place="", now=None):
        """Return a list of (title, body) for whatever should notify now."""
        now = time.time() if now is None else now
        out = []
        if mode == "off":
            return out
        for alert in alerts or []:
            aid = alert.get("id")
            if not aid:
                continue
            event = alert.get("event") or "Weather alert"
            if aid in self.seen:
                self.seen[aid] = (event, now)
                continue
            earlier = {self.seen[r][0] for r in alert.get("references") or [] if r in self.seen}
            self.seen[aid] = (event, now)
            if alert.get("type") == "Cancel" or event in earlier:
                continue
            if worthy(alert, mode):
                out.append((event, alert.get("headline") or place))
        if mode in ("watches", "all"):
            for md in mcds or []:
                key = f"mcd:{md.get('number') or md.get('name')}"
                if key in self.seen:
                    self.seen[key] = (key, now)
                    continue
                self.seen[key] = (key, now)
                title = md.get("name") or "SPC mesoscale discussion"
                out.append((title, f"The Storm Prediction Center is watching {place or 'your area'}. A watch may follow."))
        cutoff = now - FORGET_AFTER
        self.seen = {k: v for k, v in self.seen.items() if v[1] >= cutoff}
        return out
