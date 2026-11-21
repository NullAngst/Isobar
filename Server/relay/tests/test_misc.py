"""Zone file picking, radar tile paths and NWWS-OI parsing."""

import os
import unittest
from datetime import date

from isobar_relay import radar as radar_mod
from isobar_relay.zones import file_date, pick_file, ugc_for
from tests.helpers import make_config

PAGE = """
<a href="/source/gis/Shapefiles/County/c_18mr25.zip">old</a>
<a href="/source/gis/Shapefiles/County/c_16ap26.zip">new</a>
<a href="/source/gis/Shapefiles/WSOM/mz18mr25.zip">marine</a>
<a href="/source/gis/Shapefiles/WSOM/z_18mr25.zip">zones</a>
"""


class ZoneFiles(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(file_date("c_16ap26.zip", "c"), date(2026, 4, 16))
        self.assertEqual(file_date("mz18mr25.zip", "mz"), date(2025, 3, 18))
        self.assertIsNone(file_date("mz18mr25.zip", "z"))
        self.assertIsNone(file_date("fz18mr25.zip", "z"))

    def test_newest_in_effect_wins(self):
        page = "https://www.weather.gov/gis/Counties"
        self.assertTrue(pick_file(PAGE, page, "c", today=date(2026, 11, 20)).endswith("c_16ap26.zip"))
        self.assertTrue(pick_file(PAGE, page, "c", today=date(2026, 1, 1)).endswith("c_18mr25.zip"))
        self.assertTrue(pick_file(PAGE, page, "z", today=date(2026, 11, 20)).endswith("z_18mr25.zip"))
        self.assertTrue(pick_file(PAGE, page, "z").startswith("https://www.weather.gov/source/"))

    def test_ugc_codes(self):
        self.assertEqual(ugc_for("county", {"STATE": "GA", "FIPS": "13139"}), "GAC139")
        self.assertEqual(ugc_for("public", {"STATE": "GA", "ZONE": "54"}), "GAZ054")
        self.assertEqual(ugc_for("marine", {"ID": "AMZ250"}), "AMZ250")


class RadarTiles(unittest.TestCase):
    def setUp(self):
        cfg = make_config(enable_radar=True)
        self.dir = cfg.tiles_dir
        frame = "20261120-183805"
        os.makedirs(os.path.join(self.dir, frame, "5", "8"), exist_ok=True)
        with open(os.path.join(self.dir, frame, "5", "8", "12.png"), "wb") as fh:
            fh.write(b"PNGDATA")
        with open(os.path.join(self.dir, "index.json"), "w", encoding="utf-8") as fh:
            fh.write('{"frames": [{"id": "20261120-183805", "time": "2026-11-20T18:38:05Z"}]}')
        self.radar = radar_mod.Radar(self.dir, True)

    def test_paths_are_checked(self):
        self.assertEqual(self.radar.tile("20261120-183805", "5", "8", "12"), b"PNGDATA")
        self.assertEqual(self.radar.tile("20261120-183805", "5", "9", "12"), radar_mod.BLANK)
        self.assertIsNone(self.radar.tile("20261120-183805", "9", "8", "12"), "zoom past 8")
        self.assertIsNone(self.radar.tile("20261120-183805", "5", "99", "12"), "x off the map")
        self.assertIsNone(self.radar.tile("20991231-000000", "5", "8", "12"), "not in the index")
        self.assertIsNone(self.radar.tile("../../etc", "5", "8", "12"))
        self.assertTrue(self.radar.available())
        self.assertTrue(radar_mod.BLANK.startswith(b"\x89PNG"))


class NwwsParsing(unittest.TestCase):
    SAMPLE = (
        "\x01\r\r\n594 \r\r\nWFUS53 KDMX 070436\r\r\nTORDMX\r\r\nIAC117-125-135-070515-\r\r\n"
        "/O.NEW.KDMX.TO.W.0057.110807T0436Z-110807T0515Z/\r\r\n\r\r\nBULLETIN - EAS ACTIVATION REQUESTED\r\r\n"
        "TORNADO WARNING\r\r\nNATIONAL WEATHER SERVICE DES MOINES IA\r\r\n1136 PM CDT SAT AUG 6 2011\r\r\n\r\r\n"
        "THE NATIONAL WEATHER SERVICE IN DES MOINES HAS ISSUED A\r\r\n\r\r\n* TORNADO WARNING FOR...\r\r\n"
        "  LUCAS COUNTY IN SOUTH CENTRAL IOWA...\r\r\n\r\r\n* UNTIL 1215 AM CDT\r\r\n\r\r\n"
        "LAT...LON 4126 9289 4108 9286 4094 9329 4110 9337\r\r\n"
        "TIME...MOT...LOC 0436Z 252DEG 31KT 4110 9327\r\r\n\r\r\nTORNADO...OBSERVED\r\r\n"
        "HAIL...2.75IN\r\r\n\r\r\n$$\r\r\n\r\r\n\x03"
    )

    def test_warning_becomes_an_alert(self):
        try:
            from isobar_relay.nwws import parse_text
        except ImportError:
            self.skipTest("pyIEM not installed")
        try:
            add, remove = parse_text(self.SAMPLE, now=0)
        except ImportError:
            self.skipTest("pyIEM not installed")
        self.assertEqual(list(add), ["KDMX.TO.W.0057"])
        props = add["KDMX.TO.W.0057"]["properties"]
        self.assertEqual(props["event"], "Tornado Warning")
        self.assertEqual(props["parameters"]["tornadoDetection"], ["OBSERVED"])
        self.assertEqual(props["geocode"]["UGC"], ["IAC117", "IAC125", "IAC135"])
        self.assertEqual(add["KDMX.TO.W.0057"]["geometry"]["type"], "Polygon")
        self.assertEqual(remove, [])


if __name__ == "__main__":
    unittest.main()
