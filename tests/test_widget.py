import json
import os
import unittest
from unittest import mock

from tests import helpers  # noqa: F401
from isobar import widget


def forecast():
    hours = [f"2026-11-15T{h:02d}:00" for h in range(24)]
    return {
        "utc_offset_seconds": -18000,
        "current": {"time": "2026-11-15T03:45", "temperature_2m": 0.0, "apparent_temperature": -3.1,
                    "is_day": 0, "weather_code": 0},
        "daily": {"temperature_2m_max": [13.0], "temperature_2m_min": [-1.7], "precipitation_probability_max": [40]},
        "hourly": {"time": hours, "temperature_2m": [float(h) for h in range(24)],
                   "weather_code": [61] * 24, "is_day": [0] * 7 + [1] * 11 + [0] * 6},
    }


PREFS = {"units": "us", "clock": "12", "theme": "dark", "widget_theme": "app", "widget_bg": "solid",
         "widget_alerts": True, "widget_details": True, "widget_hours": True}
PLACE = {"name": "Toccoa, Georgia", "lat": 34.58, "lon": -83.33}


class Build(unittest.TestCase):
    def test_basics(self):
        out = widget.build(PREFS, PLACE, forecast(), [])
        self.assertEqual(out["temp"], "32°")
        self.assertEqual(out["cond"], "Clear")
        self.assertEqual(out["icon"], "clear_night")
        self.assertEqual(out["hilo"], "H 55°  L 29°")
        self.assertEqual(out["feels"], "Feels like 26°")
        self.assertEqual(out["rain"], "40% rain")
        self.assertEqual(out["updated"], "3:45 AM")
        self.assertEqual([h["time"] for h in out["hours"]], ["4 AM", "5 AM", "6 AM", "7 AM", "8 AM"])
        self.assertEqual(out["hours"][0]["icon"], "rain")
        self.assertIsNone(out["alert"])
        self.assertEqual(out["colors"]["fixed"]["bg"], "#1C232B")
        json.dumps(out)  # must survive the trip to Kotlin

    def test_metric_and_24_hour(self):
        out = widget.build({**PREFS, "units": "metric", "clock": "24"}, PLACE, forecast(), [])
        self.assertEqual(out["temp"], "0°")
        self.assertEqual(out["updated"], "03:45")
        self.assertEqual(out["hours"][0]["time"], "04:00")

    def test_alert_summary_picks_the_worst(self):
        alerts = [
            {"event": "Wind Advisory", "expires": "2026-11-15T23:00:00Z"},
            {"event": "Tornado Watch", "expires": "2026-11-15T12:00:00Z"},
            {"event": "Tornado Warning", "tier": "critical", "flags": ["Tornado Emergency"],
             "expires": "2026-11-15T09:30:00Z"},
        ]
        out = widget.build(PREFS, PLACE, forecast(), alerts)
        self.assertEqual(out["alert"], {"text": "Tornado Emergency", "until": "until 4:30 AM",
                                        "level": "critical", "more": 2})

    def test_watch_level(self):
        out = widget.build(PREFS, PLACE, forecast(), [{"event": "Winter Storm Watch"}])
        self.assertEqual(out["alert"]["level"], "watch")
        self.assertEqual(out["alert"]["until"], "")

    def test_alerts_can_be_turned_off(self):
        out = widget.build({**PREFS, "widget_alerts": False}, PLACE, forecast(), [{"event": "Flood Warning"}])
        self.assertIsNone(out["alert"])

    def test_system_theme_sends_both_palettes(self):
        out = widget.build({**PREFS, "theme": "system"}, PLACE, forecast(), [])
        self.assertEqual(set(out["colors"]), {"light", "dark"})
        out = widget.build({**PREFS, "widget_theme": "kawaii-light"}, PLACE, forecast(), [])
        self.assertEqual(out["colors"]["fixed"]["bg"], "#FFF0F6")

    def test_clear_background_uses_light_text(self):
        out = widget.build({**PREFS, "widget_bg": "clear", "widget_theme": "light"}, PLACE, forecast(), [])
        self.assertEqual(out["alpha"], 0)
        self.assertEqual(out["colors"]["fixed"]["ink"], "#FFFFFF")

    def test_no_place_and_no_forecast(self):
        self.assertEqual(widget.build(PREFS, None, None, None)["note"], "Open Isobar and pick a place")
        self.assertEqual(widget.build(PREFS, PLACE, None, None)["note"], "Forecast unavailable")

    def test_icons(self):
        self.assertEqual(widget.icon_for(95, 1), "thunder")
        self.assertEqual(widget.icon_for(73, 1), "snow")
        self.assertEqual(widget.icon_for(66, 1), "sleet")
        self.assertEqual(widget.icon_for(2, 0), "partly_night")
        self.assertEqual(widget.icon_for(None, 1), "cloudy")


class Collect(unittest.TestCase):
    def test_widget_place_wins_and_alert_failure_is_noted(self):
        from isobar.net import FetchError
        prefs = {**PREFS, "location": PLACE, "widget_place": {"name": "Atlanta", "lat": 33.75, "lon": -84.39}}
        with mock.patch("isobar.sources.open_meteo", return_value=forecast()) as om, \
             mock.patch("isobar.sources.nws_alerts", side_effect=FetchError("down", 503)):
            out = widget.collect(prefs)
        self.assertEqual(om.call_args[0], (33.75, -84.39))
        self.assertEqual(out["place"], "Atlanta")
        self.assertEqual(out["note"], "Alerts unavailable")

    def test_outside_the_us_skips_alerts(self):
        prefs = {**PREFS, "location": {"name": "London", "lat": 51.5, "lon": -0.1}}
        with mock.patch("isobar.sources.open_meteo", return_value=forecast()), \
             mock.patch("isobar.sources.nws_alerts") as alerts:
            out = widget.collect(prefs)
        alerts.assert_not_called()
        self.assertIsNone(out["alert"])


class AndroidEntry(unittest.TestCase):
    @mock.patch.dict(os.environ)  # android._setup points XDG paths at the app; undo that after
    def test_widget_data_is_json(self):
        from isobar import android
        with mock.patch("isobar.widget.collect", return_value={"place": "X"}):
            self.assertEqual(json.loads(android.widget_data(helpers.TMP, helpers.TMP)), {"place": "X"})
        with mock.patch("isobar.widget.collect", side_effect=RuntimeError("boom")):
            self.assertEqual(json.loads(android.widget_data(helpers.TMP, helpers.TMP))["note"], "Could not update")


if __name__ == "__main__":
    unittest.main()
