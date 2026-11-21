"""The radar tile proxy after IEM moved its tiles to one host, and alerts
that keep their identity across sources."""

import unittest
from unittest import mock

from tests import helpers  # noqa: F401
from isobar import net, notify, sources
from isobar.server import TILE_HOSTS, tile_redirect_ok


class FakeResponse:
    def __init__(self, status, body=b"", headers=None, url="https://x"):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.encoding = None

    def iter_content(self, size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


OLD = "https://mesonet1.agron.iastate.edu/c/tile.py/1.0.0/ridge::USCOMP-N0Q-202611201800/5/8/12.png"
NEW = "https://mesonet.agron.iastate.edu/c/tile.py/1.0.0/ridge::USCOMP-N0Q-202611201800/5/8/12.png"


class TileProxy(unittest.TestCase):
    def setUp(self):
        net._backoff.clear()

    def test_only_the_one_host_is_asked_for_directly(self):
        self.assertEqual(TILE_HOSTS, {"mesonet.agron.iastate.edu"})

    def test_redirects_are_followed_only_on_iems_domain(self):
        self.assertTrue(tile_redirect_ok(NEW))
        self.assertTrue(tile_redirect_ok("https://mesonet2.agron.iastate.edu/x.png"))
        self.assertFalse(tile_redirect_ok("https://evil.example/agron.iastate.edu/x.png"))
        self.assertFalse(tile_redirect_ok("https://agron.iastate.edu.evil.example/x.png"))
        self.assertFalse(tile_redirect_ok("http://mesonet.agron.iastate.edu/x.png"))

    def test_a_moved_tile_comes_through_the_proxy(self):
        answers = {
            OLD: FakeResponse(301, headers={"Location": NEW}, url=OLD),
            NEW: FakeResponse(200, b"PNG", {"Content-Type": "image/png"}, url=NEW),
        }
        with mock.patch.object(net._session, "get", side_effect=lambda url, **kw: answers[url]):
            body, ctype = net.get_bytes(OLD, may_follow=tile_redirect_ok)
        self.assertEqual((body, ctype), (b"PNG", "image/png"))

    def test_a_redirect_off_the_domain_is_refused(self):
        bad = FakeResponse(302, headers={"Location": "https://evil.example/x.png"},
                           url="https://mesonet.agron.iastate.edu/r.png")
        with mock.patch.object(net._session, "get", return_value=bad):
            with self.assertRaises(net.FetchError) as ctx:
                net.get_bytes("https://mesonet.agron.iastate.edu/r.png", may_follow=tile_redirect_ok)
        self.assertIn("refused", str(ctx.exception))


class SameEventAcrossSources(unittest.TestCase):
    def test_vtec_events(self):
        params = {"VTEC": ["/O.NEW.KFFC.TO.W.0042.261120T1800Z-261120T1830Z/"]}
        self.assertEqual(sources.vtec_events(params), ["KFFC.TO.W.0042"])

    def test_one_notice_when_the_id_changes_with_the_source(self):
        watcher = notify.AlertWatcher()
        nwws = {"id": "nwws-1", "event": "Tornado Warning", "events": ["KFFC.TO.W.0042"], "tier": "critical"}
        nws = {"id": "urn:oid:2.49.0.1", "event": "Tornado Warning", "events": ["KFFC.TO.W.0042"], "tier": "critical"}
        self.assertEqual(len(watcher.check([nwws], [], "warnings")), 1)
        self.assertEqual(watcher.check([nws], [], "warnings"), [], "same event, new id: stays quiet")

    def test_map_shapes_from_a_server(self):
        collection = {"features": [
            {"geometry": {"type": "Polygon", "coordinates": []},
             "properties": {"event": "Tornado Warning", "sent": "s", "ends": "e",
                            "parameters": {"VTEC": ["/O.NEW.KFFC.TO.W.0042.x/"]}}},
            {"geometry": None, "properties": {"event": "Special Weather Statement", "parameters": {}}},
        ]}
        out = sources.wwa_from_alerts(collection, "W")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["properties"]["phenom"], "TO")
        self.assertEqual(out[0]["properties"]["wfo"], "FFC")
        self.assertEqual(sources.wwa_from_alerts(collection, "A"), [])


if __name__ == "__main__":
    unittest.main()
