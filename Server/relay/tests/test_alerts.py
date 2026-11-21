"""The alert snapshot: point filtering, expiry, staleness, the IEM fallback and NWWS-OI."""

import json
import time
import unittest
from datetime import datetime, timedelta, timezone

from isobar_relay import datasets
from isobar_relay.alerts import NWS_ACTIVE, Alerts, Snapshot, iem_features
from isobar_relay.upstream import Answer, Health, UpstreamError
from tests.helpers import make_config

NOW = time.time()


def iso(offset):
    return datetime.fromtimestamp(NOW + offset, tz=timezone.utc).isoformat()


def alert(ident, event="Tornado Warning", polygon=None, ugcs=(), ends=3600, vtec=None):
    props = {"id": ident, "event": event, "ends": iso(ends), "expires": iso(ends),
             "geocode": {"UGC": list(ugcs)}, "parameters": {}}
    if vtec:
        props["parameters"]["VTEC"] = [vtec]
    return {"id": ident, "type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [polygon]} if polygon else None}


SQUARE = [[-85, 34], [-83, 34], [-83, 36], [-85, 36], [-85, 34]]


class PointFilter(unittest.TestCase):
    def test_polygon_wins_and_zone_only_alerts_match_by_zone(self):
        feats = [
            alert("poly-in", polygon=SQUARE, ugcs=["GAC999"]),
            alert("poly-out", polygon=[[-90, 40], [-89, 40], [-89, 41], [-90, 41], [-90, 40]], ugcs=["GAC139"]),
            alert("zone-hit", event="Winter Storm Warning", ugcs=["GAZ054"]),
            alert("zone-miss", event="Wind Advisory", ugcs=["ALZ001"]),
            alert("expired", polygon=SQUARE, ends=-60),
        ]
        snap = Snapshot(feats, NOW, "nws")
        got = [f["id"] for f in snap.at_point(35.0, -84.0, {"GAC139", "GAZ054"}, NOW)]
        # poly-out lists GAC139, but it has a polygon and the point isn't in it.
        self.assertEqual(got, ["poly-in", "zone-hit"])


class FakeUpstream:
    def __init__(self):
        self.health = Health()
        self.script = {}
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.script.get(url)
        if isinstance(result, Exception):
            self.health.fail(kwargs.get("name") or url, result)
            raise result
        self.health.ok(kwargs.get("name") or url)
        return result


def nws_answer(features, etag="e1"):
    return Answer(200, json.dumps({"features": features}).encode(), etag, None)


class Polling(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cfg = make_config()
        self.up = FakeUpstream()
        self.alerts = Alerts(self.up, datasets.Tracker(), cfg.data_dir)

    async def test_stale_after_five_minutes_then_iem(self):
        self.up.script[NWS_ACTIVE] = nws_answer([alert("a1", polygon=SQUARE, vtec="/O.NEW.KFFC.TO.W.0001.x/")])
        await self.alerts.poll_once(now=NOW)
        self.assertEqual(self.alerts.snapshot.source, "nws")
        self.assertFalse(self.alerts.stale(NOW + 60))
        # The NWS goes down. Within 5 minutes the last copy keeps serving, fresh.
        self.up.script[NWS_ACTIVE] = UpstreamError("down", 503)
        await self.alerts.poll_once(now=NOW + 120)
        self.assertEqual(self.alerts.snapshot.source, "nws")
        self.assertFalse(self.alerts.stale(NOW + 120))
        self.assertTrue(self.alerts.stale(NOW + 400))
        # Past 5 minutes it's stale, and the next failed poll tries IEM.
        called = []

        async def fake_iem(now):
            called.append(now)
        self.alerts._poll_iem = fake_iem
        await self.alerts.poll_once(now=NOW + 400)
        self.assertEqual(called, [NOW + 400])

    async def test_304_confirms_the_copy(self):
        self.up.script[NWS_ACTIVE] = nws_answer([alert("a1", polygon=SQUARE)])
        await self.alerts.poll_once(now=NOW)
        version = self.alerts.snapshot.version
        self.up.script[NWS_ACTIVE] = Answer(304, b"", "e1", None)
        await self.alerts.poll_once(now=NOW + 60)
        self.assertEqual(self.alerts.snapshot.version, version, "an unchanged feed doesn't rebuild")
        self.assertEqual(self.alerts.snapshot.fetched_at, NOW + 60)
        self.assertEqual(self.up.calls[-1][1]["etag"], "e1")

    async def test_stale_copy_drops_expired_alerts(self):
        self.up.script[NWS_ACTIVE] = nws_answer([alert("short", polygon=SQUARE, ends=120),
                                                 alert("long", polygon=SQUARE, ends=7200)])
        await self.alerts.poll_once(now=NOW)
        later = [f["id"] for f in self.alerts.for_point(35, -84, set(), now=NOW + 600)]
        self.assertEqual(later, ["long"])

    async def test_nwws_adds_until_the_feed_has_it(self):
        self.up.script[NWS_ACTIVE] = nws_answer([])
        await self.alerts.poll_once(now=NOW)
        pushed = alert("nwws-1", polygon=SQUARE, vtec="/O.NEW.KFFC.TO.W.0042.x/")
        self.alerts.nwws_update({"KFFC.TO.W.0042": pushed}, [])
        self.assertEqual([f["id"] for f in self.alerts.for_point(35, -84, set(), NOW)], ["nwws-1"])
        # The feed catches up with its own copy, which replaces the pushed one.
        self.up.script[NWS_ACTIVE] = nws_answer([alert("urn:nws-1", polygon=SQUARE,
                                                       vtec="/O.NEW.KFFC.TO.W.0042.x/")], etag="e2")
        await self.alerts.poll_once(now=NOW + 60)
        self.assertEqual([f["id"] for f in self.alerts.for_point(35, -84, set(), NOW)], ["urn:nws-1"])
        self.assertEqual(self.alerts.overlay, {})

    async def test_nwws_cancel_hides_the_feed_copy_until_the_feed_drops_it(self):
        self.up.script[NWS_ACTIVE] = nws_answer([alert("w", polygon=SQUARE, vtec="/O.NEW.KFFC.SV.W.0007.x/")])
        await self.alerts.poll_once(now=NOW)
        self.alerts.nwws_update({}, ["KFFC.SV.W.0007"])
        self.assertEqual(self.alerts.for_point(35, -84, set(), NOW), [])

    async def test_saved_snapshot_survives_a_restart(self):
        self.up.script[NWS_ACTIVE] = nws_answer([alert("kept", polygon=SQUARE)])
        await self.alerts.poll_once(now=NOW)
        again = Alerts(self.up, datasets.Tracker(), self.alerts.path.rsplit("/", 1)[0])
        again.load()
        self.assertEqual(again.snapshot.fetched_at, NOW)
        self.assertEqual([f["id"] for f in again.for_point(35, -84, set(), NOW)], ["kept"])


class IemFallback(unittest.TestCase):
    def test_rows_group_into_events(self):
        try:
            import pyiem  # noqa: F401
            from shapely.geometry import Polygon
        except ImportError:
            self.skipTest("pyIEM not installed")
        stamp = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y%m%d%H%M")
        rows = [
            {"WFO": "FFC", "PHENOM": "TO", "SIG": "W", "ETN": 12, "GTYPE": "P", "EXPIRED": stamp,
             "ISSUED": stamp, "geometry": Polygon(SQUARE)},
            {"WFO": "FFC", "PHENOM": "TO", "SIG": "W", "ETN": 12, "GTYPE": "C", "NWS_UGC": "GAC139",
             "EXPIRED": stamp, "ISSUED": stamp, "geometry": None},
            {"WFO": "FFC", "PHENOM": "WS", "SIG": "A", "ETN": 3, "GTYPE": "C", "NWS_UGC": "GAZ054",
             "EXPIRED": stamp, "ISSUED": stamp, "geometry": None},
        ]
        feats = {f["properties"]["event"]: f for f in iem_features(rows)}
        self.assertEqual(set(feats), {"Tornado Warning", "Winter Storm Watch"})
        tor = feats["Tornado Warning"]
        self.assertEqual(tor["geometry"]["type"], "Polygon")
        self.assertEqual(tor["properties"]["geocode"]["UGC"], ["GAC139"])
        self.assertEqual(tor["properties"]["severity"], "Extreme")
        self.assertIn("KFFC.TO.W.0012", tor["properties"]["parameters"]["VTEC"][0])


if __name__ == "__main__":
    unittest.main()
