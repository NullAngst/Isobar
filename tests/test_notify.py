import unittest

from tests import helpers  # noqa: F401
from isobar.notify import AlertWatcher, FORGET_AFTER


def alert(aid, event, refs=(), kind="Alert", severity="Severe"):
    return {"id": aid, "event": event, "headline": f"{event} headline", "type": kind,
            "references": list(refs), "severity": severity}


class Watcher(unittest.TestCase):
    def test_new_warning_notifies_once(self):
        w = AlertWatcher()
        first = w.check([alert("a1", "Tornado Warning")], [], "warnings", now=0)
        self.assertEqual(first, [("Tornado Warning", "Tornado Warning headline")])
        self.assertEqual(w.check([alert("a1", "Tornado Warning")], [], "warnings", now=60), [])

    def test_update_of_same_event_is_quiet(self):
        w = AlertWatcher()
        w.check([alert("a1", "Severe Thunderstorm Warning")], [], "warnings", now=0)
        update = alert("a2", "Severe Thunderstorm Warning", refs=["a1"])
        self.assertEqual(w.check([update], [], "warnings", now=60), [])

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


if __name__ == "__main__":
    unittest.main()
