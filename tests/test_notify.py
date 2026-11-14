import json
import os
import unittest
from unittest import mock

from tests import helpers
from isobar import notify
from isobar.notify import AlertWatcher, FORGET_AFTER, OUTAGE_AFTER, in_quiet_hours, places_to_watch, title_for


def alert(aid, event, refs=(), kind="Alert", severity="Severe", tags=None, flags=None):
    return {"id": aid, "event": event, "headline": f"{event} headline", "type": kind,
            "references": list(refs), "severity": severity, "tags": tags or {}, "flags": flags or []}


class Watcher(unittest.TestCase):
    def test_new_warning_notifies_once(self):
        w = AlertWatcher()
        first = w.check([alert("a1", "Tornado Warning")], [], "warnings", now=0)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["title"], "Tornado Warning")
        self.assertEqual(first[0]["tier"], "critical")
        self.assertEqual(w.check([alert("a1", "Tornado Warning")], [], "warnings", now=60), [])

    def test_update_of_same_event_is_quiet(self):
        w = AlertWatcher()
        w.check([alert("a1", "Severe Thunderstorm Warning")], [], "warnings", now=0)
        update = alert("a2", "Severe Thunderstorm Warning", refs=["a1"])
        self.assertEqual(w.check([update], [], "warnings", now=60), [])

    def test_update_that_gets_worse_notifies(self):
        w = AlertWatcher()
        w.check([alert("a1", "Tornado Warning")], [], "warnings", now=0)
        worse = alert("a2", "Tornado Warning", refs=["a1"], flags=["Tornado Emergency"])
        out = w.check([worse], [], "warnings", now=60)
        self.assertEqual([n["title"] for n in out], ["Tornado Emergency"])

    def test_cancel_is_quiet(self):
        w = AlertWatcher()
        self.assertEqual(w.check([alert("c1", "Tornado Warning", kind="Cancel")], [], "all", now=0), [])

    def test_modes(self):
        watch = alert("w1", "Tornado Watch")
        self.assertEqual(AlertWatcher().check([watch], [], "warnings", now=0), [])
        self.assertEqual(len(AlertWatcher().check([watch], [], "watches", now=0)), 1)
        self.assertEqual(AlertWatcher().check([watch], [], "off", now=0), [])

    def test_mesoscale_discussions_only_in_watch_modes(self):
        md = [{"number": 1234, "name": "Mesoscale discussion 1234"}]
        self.assertEqual(AlertWatcher().check([], md, "warnings", now=0), [])
        w = AlertWatcher()
        self.assertEqual(len(w.check([], md, "watches", "Toccoa, GA", now=0)), 1)
        self.assertEqual(w.check([], md, "watches", now=10), [])

    def test_old_ids_are_forgotten(self):
        w = AlertWatcher()
        w.check([alert("a1", "Flood Warning")], [], "warnings", now=0)
        w.check([], [], "warnings", now=FORGET_AFTER + 10)
        self.assertNotIn("a1", w.seen)

    def test_quiet_hours_let_only_critical_through(self):
        w = AlertWatcher()
        out = w.check([alert("f1", "Flood Warning"), alert("t1", "Tornado Warning")], [], "warnings", now=0, quiet=True)
        self.assertEqual([n["title"] for n in out], ["Tornado Warning"])
        # The quiet one is still marked seen, so it doesn't ping when quiet hours end.
        self.assertEqual(w.check([alert("f1", "Flood Warning")], [], "warnings", now=60), [])

    def test_seen_survives_a_restart(self):
        path = os.path.join(helpers.TMP, "seen-test.json")
        w = AlertWatcher(path=path)
        w.check([alert("a1", "Winter Storm Warning")], [], "warnings", now=1000)
        w._save()
        again = AlertWatcher(path=path)
        self.assertEqual(again.check([alert("a1", "Winter Storm Warning")], [], "warnings", now=1100), [])
        with open(path, encoding="utf-8") as fh:
            self.assertIn("a1", json.load(fh))

    def test_corrupt_state_file_is_ignored(self):
        path = os.path.join(helpers.TMP, "seen-bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(AlertWatcher(path=path).seen, {})

    def test_outage_notice_once(self):
        w = AlertWatcher()
        self.assertIsNone(w.fetch_failed(now=0))
        self.assertIsNotNone(w.fetch_failed(now=OUTAGE_AFTER + 1))
        self.assertIsNone(w.fetch_failed(now=OUTAGE_AFTER + 200))
        w.fetch_ok()
        self.assertIsNone(w.fetch_failed(now=10_000))


class Helpers(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(title_for(alert("x", "Tornado Warning", tags={"tornado": "Observed"})), "Tornado Warning, observed")
        self.assertEqual(title_for(alert("x", "Tornado Warning", flags=["PDS"])), "PDS Tornado Warning")
        self.assertEqual(title_for(alert("x", "Flash Flood Warning", flags=["Flash Flood Emergency"])), "Flash Flood Emergency")

    def test_quiet_hours(self):
        self.assertFalse(in_quiet_hours(-1, 7, 3))
        self.assertTrue(in_quiet_hours(22, 7, 23))
        self.assertTrue(in_quiet_hours(22, 7, 3))
        self.assertFalse(in_quiet_hours(22, 7, 12))
        self.assertTrue(in_quiet_hours(1, 5, 2))
        self.assertFalse(in_quiet_hours(1, 5, 5))

    def test_places(self):
        prefs = {"location": {"name": "A", "lat": 34.0, "lon": -84.0},
                 "saved": [{"name": "A again", "lat": 34.0001, "lon": -84.0, "notify": True},
                           {"name": "B", "lat": 35.0, "lon": -85.0, "notify": True},
                           {"name": "C", "lat": 36.0, "lon": -86.0, "notify": False}]}
        self.assertEqual([p["name"] for p in places_to_watch(prefs)], ["A", "B"])


class Collect(unittest.TestCase):
    def test_collect_checks_each_place_and_reports_outages(self):
        prefs = {"notify": "warnings", "quiet_start": -1, "quiet_end": 7,
                 "location": {"name": "Toccoa, Georgia", "lat": 34.58, "lon": -83.33},
                 "saved": [{"name": "Away", "lat": 40.0, "lon": -100.0, "notify": True}]}
        calls = []

        def fake_alerts(lat, lon):
            calls.append((lat, lon))
            if lat == 40.0:
                raise notify_fetch_error()
            return [alert("t1", "Tornado Warning")]

        from isobar.net import FetchError

        def notify_fetch_error():
            return FetchError("down", 503)

        w = AlertWatcher()
        with mock.patch("isobar.sources.nws_alerts", side_effect=fake_alerts):
            out = notify.collect(w, prefs, now=0, hour=12)
            later = notify.collect(w, prefs, now=OUTAGE_AFTER + 5, hour=12)
        self.assertEqual(len(calls), 4)
        self.assertEqual(out[0]["title"], "Tornado Warning")
        self.assertIn("Toccoa", out[0]["body"])
        self.assertEqual([n["id"] for n in later], ["outage"])


if __name__ == "__main__":
    unittest.main()
