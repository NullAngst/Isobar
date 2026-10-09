"""Parsing of upstream answers, with the network replaced by canned responses."""

import unittest
from unittest import mock

from tests import helpers  # noqa: F401
from isobar import sources

SQUARE = {"type": "Polygon", "coordinates": [[[-85, 34], [-83, 34], [-83, 36], [-85, 36], [-85, 34]]]}


class Mesoscale(unittest.TestCase):
    def test_placeholder_dropped_and_point_checked(self):
        answer = {"features": [
            {"geometry": None, "properties": {"name": "NoArea"}},
            {"geometry": SQUARE, "properties": {"name": "MD 2134",
                                                "popupinfo": "http://www.spc.noaa.gov/products/md/md2134.html",
                                                "folderpath": "MD 2134 Active Till 0130 UTC",
                                                # 2026-10-09 23:15 UTC, so 0130 is the next day
                                                "idp_filedate": 1791587700000}},
        ]}
        with mock.patch.object(sources, "get_json", return_value=answer):
            fc = sources.spc_mcds(35.0, -84.0)
            here = sources.mcds_here(35.0, -84.0)
            away = sources.mcds_here(40.0, -100.0)
        self.assertEqual(len(fc["features"]), 1)
        p = fc["features"][0]["properties"]
        self.assertEqual(p["number"], 2134)
        self.assertEqual(p["link"], "https://www.spc.noaa.gov/products/md/md2134.html")
        self.assertEqual(p["expires"], "2026-10-10T01:30:00+00:00")
        self.assertTrue(p["here"])
        self.assertEqual(len(here), 1)
        self.assertIn("geometry", here[0])
        self.assertEqual(away, [])

    def test_service_failure_is_an_empty_list(self):
        with mock.patch.object(sources, "get_json", side_effect=sources.FetchError("down", 503)):
            self.assertEqual(sources.mcds_here(35, -84), [])


class Reports(unittest.TestCase):
    def test_hours_snap_and_fields_trim(self):
        feat = {"geometry": {"type": "Point", "coordinates": [-84.0, 35.0]},
                "properties": {"type": "T", "typetext": "TORNADO", "magnitude": "", "city": "2 N Toccoa",
                               "state": "GA", "remark": "x" * 1000, "valid": "2026-10-09T15:00:00Z"}}
        bad = {"geometry": {"type": "Point", "coordinates": []}, "properties": {}}
        with mock.patch.object(sources, "get_json", return_value={"features": [feat, bad]}) as fake:
            out = sources.storm_reports(7)
        self.assertEqual(fake.call_args[0][1], {"hours": 12})
        self.assertEqual(out["hours"], 12)
        self.assertEqual(len(out["features"]), 1)
        self.assertEqual(len(out["features"][0]["properties"]["remark"]), 400)
        with mock.patch.object(sources, "get_json", return_value={}):
            self.assertEqual(sources.storm_reports(999)["hours"], 48)


class Outlooks(unittest.TestCase):
    def test_strongest_intensity_wins(self):
        outlook = {
            "features": [{"geometry": SQUARE, "properties": {"label": "0.15"}}],
            "intensity": [
                {"geometry": SQUARE, "properties": {"label": "CIG2"}},
                {"geometry": SQUARE, "properties": {"label": "CIG1"}},
            ],
        }
        self.assertEqual(sources._prob_at(35, -84, outlook), {"pct": 15, "intensity": "CIG2"})


class Geocode(unittest.TestCase):
    def test_missing_region_and_coordinates(self):
        answer = {"results": [
            {"name": "Nowhere", "latitude": 1, "longitude": 2},
            {"name": "Broken"},
            {"name": "Toccoa", "admin1": "Georgia", "country_code": "US", "latitude": 34.58, "longitude": -83.33},
        ]}
        with mock.patch.object(sources, "get_json", return_value=answer):
            out = sources.geocode("toccoa")
        self.assertEqual([r["name"] for r in out], ["Nowhere", "Toccoa, Georgia"])

    def test_typed_coordinates_skip_the_network(self):
        with mock.patch.object(sources, "get_json") as fake:
            out = sources.geocode("34.58, -83.33")
        fake.assert_not_called()
        self.assertEqual(out[0]["lat"], 34.58)


class TextProducts(unittest.TestCase):
    def test_unknown_product_refused(self):
        with self.assertRaises(ValueError):
            sources.nws_discussion("FFC", "ZZZ")

    def test_hwo_link(self):
        listing = {"@graph": [{"@id": "https://api.weather.gov/products/abc-123"}]}
        product = {"issuanceTime": "2026-10-09T10:00:00Z", "productText": ".DAY ONE...Today.\nNo hazards.\n$$"}
        with mock.patch.object(sources, "get_json", side_effect=[listing, product]):
            out = sources.nws_discussion("FFC", "HWO")
        self.assertEqual(out["product"], "HWO")
        self.assertTrue(out["link"].endswith("product=HWO"))


if __name__ == "__main__":
    unittest.main()
