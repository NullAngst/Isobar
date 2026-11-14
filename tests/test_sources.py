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


class Alerts(unittest.TestCase):
    def test_tags_flags_and_tiers(self):
        answer = {"features": [
            {"properties": {"id": "a", "event": "Tornado Warning", "severity": "Extreme", "messageType": "Alert",
                            "description": "...THIS IS A TORNADO EMERGENCY FOR TOCCOA...",
                            "parameters": {"tornadoDetection": ["OBSERVED"], "maxHailSize": ["1.75"],
                                           "tornadoDamageThreat": ["CATASTROPHIC"]}}},
            {"properties": {"id": "b", "event": "Severe Thunderstorm Warning", "severity": "Severe",
                            "parameters": {"maxWindGust": ["70 MPH"], "maxHailSize": ["Up to 1.00"]}}},
            {"properties": {"id": "c", "event": "Wind Advisory", "severity": "Minor"}},
        ]}
        with mock.patch.object(sources, "get_json", return_value=answer) as fake:
            out = sources.nws_alerts(34.5, -83.3)
        self.assertEqual(fake.call_args[0][1]["status"], "actual")
        self.assertEqual([a["tier"] for a in out], ["critical", "high", "normal"])
        self.assertEqual(out[0]["flags"], ["Tornado Emergency"])
        self.assertEqual(out[0]["tags"], {"tornado": "Observed", "hail_in": 1.75, "damage": "Catastrophic"})
        self.assertEqual(out[1]["tags"], {"wind_mph": 70, "hail_in": 1.0})

    def test_coverage_boxes(self):
        self.assertTrue(sources.in_nws_area(34.58, -83.33))
        self.assertTrue(sources.in_nws_area(61.2, -149.9))   # Anchorage
        self.assertTrue(sources.in_nws_area(21.3, -157.8))   # Honolulu
        self.assertTrue(sources.in_nws_area(13.4, 144.8))    # Guam
        self.assertFalse(sources.in_nws_area(51.5, -0.1))    # London


class Bundle(unittest.TestCase):
    def test_alerts_load_when_points_fails(self):
        def fake_point(lat, lon, strict=False):
            raise sources.FetchError("points down", 500)

        with mock.patch.object(sources, "open_meteo", return_value=None), \
             mock.patch.object(sources, "air_quality", return_value=None), \
             mock.patch.object(sources, "nowcast", return_value=None), \
             mock.patch.object(sources, "nws_point", side_effect=fake_point), \
             mock.patch.object(sources, "nws_alerts", return_value=[{"id": "x", "event": "Flood Warning"}]), \
             mock.patch.object(sources, "mcds_here", return_value=[]):
            out = sources.weather_bundle(34.58, -83.33)
        self.assertTrue(out["us"])
        self.assertIsNone(out["nws"])
        self.assertEqual(len(out["alerts"]), 1)
        self.assertTrue(out["alerts_ok"])
        self.assertTrue(any("NWS point" in e for e in out["errors"]))

    def test_alert_failure_is_flagged(self):
        with mock.patch.object(sources, "open_meteo", return_value=None), \
             mock.patch.object(sources, "air_quality", return_value=None), \
             mock.patch.object(sources, "nowcast", return_value=None), \
             mock.patch.object(sources, "nws_point", return_value=None), \
             mock.patch.object(sources, "nws_alerts", side_effect=sources.FetchError("down", 503)), \
             mock.patch.object(sources, "mcds_here", return_value=[]):
            out = sources.weather_bundle(34.58, -83.33)
        self.assertFalse(out["alerts_ok"])


class Yesterday(unittest.TestCase):
    def test_split(self):
        data = {
            "current": {"time": "2026-11-14T13:15", "temperature_2m": 10},
            "daily": {"time": ["2026-11-13", "2026-11-14", "2026-11-15"],
                      "temperature_2m_max": [20, 15, 12], "temperature_2m_min": [5, 4, 3]},
            "hourly": {"time": ["2026-11-13T12:00", "2026-11-13T13:00", "2026-11-14T00:00", "2026-11-14T13:00"],
                       "temperature_2m": [17, 18, 6, 10]},
        }
        out = sources.split_yesterday(data)
        self.assertEqual(out["yesterday"], {"max": 20, "min": 5, "temp": 18})
        self.assertEqual(out["daily"]["time"], ["2026-11-14", "2026-11-15"])
        self.assertEqual(out["hourly"]["time"], ["2026-11-14T00:00", "2026-11-14T13:00"])
        self.assertEqual(len(data["hourly"]["time"]), 4)  # the cached original is untouched


class Antimeridian(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(sources.lon_ranges(-100, -80), [(-100, -80)])
        self.assertEqual(sources.lon_ranges(-200, -150), [(160, 180.0), (-180.0, -150)])
        self.assertEqual(sources.lon_ranges(-400, 100), [(-180.0, 180.0)])

    def test_wwa_split_queries(self):
        with mock.patch.object(sources, "get_json", return_value={"features": []}) as fake:
            sources.wwa("W", [50, -200, 60, -150], 5)
        boxes = [c[0][1]["geometry"] for c in fake.call_args_list]
        self.assertEqual(len(boxes), 2)
        self.assertTrue(all(-180 <= float(v) <= 180 for b in boxes for v in b.split(",")[::2]))


class Feeds(unittest.TestCase):
    def test_storm_attrs_keep_notable_cells(self):
        answer = {"features": [
            {"geometry": {"type": "Point", "coordinates": [-84, 35]},
             "properties": {"NEXRAD": "FFC", "STORM_ID": "A1", "TVS": "TVS", "MESO": "4", "POH": 70, "POSH": 40,
                            "MAX_SIZE": 1.5, "DRCT": 240, "SKNT": 30}},
            {"geometry": {"type": "Point", "coordinates": [-85, 36]},
             "properties": {"nexrad": "FFC", "storm_id": "B2", "tvs": "NONE", "meso": "NONE", "poh": 10, "posh": 0, "max_size": 0}},
        ]}
        with mock.patch.object(sources, "get_json", return_value=answer):
            out = sources.storm_attrs()
        self.assertEqual(len(out["features"]), 1)
        self.assertEqual(out["features"][0]["properties"]["tvs"], "TVS")

    def test_hrrr_run(self):
        with mock.patch.object(sources, "get_json", return_value={"model_init_utc": "2026-11-14T17:00:00Z"}):
            run = sources.hrrr_run()
        self.assertEqual(run["stamp"], "202611141700")

    def test_sat_info(self):
        with mock.patch.object(sources, "get_json", return_value={"timeInfo": {"timeExtent": [1, 1763140800000]}}):
            info = sources.sat_info("13")
        self.assertEqual(info["end"], 1763140800000)
        self.assertTrue(info["url"].endswith("ABI13_Last_24hr/ImageServer/exportImage"))
        with self.assertRaises(ValueError):
            sources.sat_info("99")

    def test_previous_discussion(self):
        listing = {"@graph": [{"@id": "https://api.weather.gov/products/new-1"},
                              {"@id": "https://api.weather.gov/products/old-1"}]}
        new = {"issuanceTime": "2026-11-14T10:00:00Z", "productText": "new"}
        old = {"issuanceTime": "2026-11-14T04:00:00Z", "productText": "old"}
        with mock.patch.object(sources, "get_json", side_effect=[listing, new, old]):
            out = sources.nws_discussion("FFC", "AFD")
        self.assertEqual(out["previous"]["text"], "old")


if __name__ == "__main__":
    unittest.main()
