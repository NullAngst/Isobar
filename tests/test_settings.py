import threading
import unittest

from tests import helpers  # noqa: F401
from isobar import settings


class Settings(unittest.TestCase):
    def setUp(self):
        try:
            (settings._file()).unlink()
        except OSError:
            pass

    def test_defaults(self):
        s = settings.load()
        self.assertEqual(s["units"], "us")
        self.assertIn("reports", s["radar_options"])
        self.assertIn("mcd", s["radar_layers"])

    def test_bad_enum_is_dropped(self):
        s = settings.save({"theme": "chartreuse", "units": "metric"})
        self.assertEqual(s["theme"], "system")
        self.assertEqual(s["units"], "metric")

    def test_numbers_are_clamped(self):
        s = settings.save({"radar_speed_ms": 0, "radar_opacity": 7})
        self.assertEqual(s["radar_speed_ms"], 100)
        self.assertEqual(s["radar_opacity"], 1.0)

    def test_wrong_types_keep_old_value(self):
        s = settings.save({"radar_speed_ms": "fast", "tray_on_close": "yes", "radar_site": "../etc"})
        self.assertEqual(s["radar_speed_ms"], 450)
        self.assertFalse(s["tray_on_close"])
        self.assertEqual(s["radar_site"], "mosaic")

    def test_places_are_cleaned(self):
        s = settings.save({"location": {"name": "x" * 500, "lat": "34.5", "lon": -83.3},
                           "saved": [{"lat": 91, "lon": 0}, {"lat": 1, "lon": 2}]})
        self.assertEqual(len(s["location"]["name"]), 120)
        self.assertEqual(s["saved"], [{"name": "1.000, 2.000", "lat": 1.0, "lon": 2.0}])

    def test_nested_layers_merge(self):
        settings.save({"radar_layers": {"counties": True}})
        s = settings.save({"radar_layers": {"reports": True}})
        self.assertTrue(s["radar_layers"]["counties"])
        self.assertTrue(s["radar_layers"]["reports"])

    def test_concurrent_saves_keep_every_change(self):
        keys = ["warnings", "watches", "advisories", "outlook", "counties", "sites", "mcd", "reports"]
        start = {k: False for k in keys}
        settings.save({"radar_layers": start})
        threads = [threading.Thread(target=settings.save, args=({"radar_layers": {k: True}},)) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(settings.load()["radar_layers"][k] for k in keys))


if __name__ == "__main__":
    unittest.main()
