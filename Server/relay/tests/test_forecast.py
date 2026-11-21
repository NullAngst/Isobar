"""Stitching the three models, the cache standing in, and never the public API."""

import json
import time
import unittest

from isobar_relay import datasets
from isobar_relay.forecast import LocalWeather, Unavailable, classify, merge_block, stitch
from isobar_relay.upstream import Answer, Health, UpstreamError
from tests.helpers import make_config

HRRR, NBM, GFS = "ncep_hrrr_conus", "ncep_nbm_conus", "ncep_gfs_global"
TIMES = ["2026-11-20T00:00", "2026-11-20T01:00", "2026-11-20T02:00"]


def answer(temps, gusts=None, daily_max=None, current_temp=None, uv=None):
    out = {"latitude": 35.0, "longitude": -84.0, "timezone": "America/New_York",
           "hourly": {"time": TIMES, "temperature_2m": temps},
           "hourly_units": {"temperature_2m": "°C"},
           "current": {"time": TIMES[0], "interval": 900, "temperature_2m": current_temp},
           "daily": {"time": ["2026-11-20"], "temperature_2m_max": [daily_max]}}
    if gusts is not None:
        out["hourly"]["wind_gusts_10m"] = gusts
    if uv is not None:
        out["hourly"]["uv_index"] = uv
    return out


class Stitching(unittest.TestCase):
    order_now = [HRRR, NBM, GFS]
    order_daily = [NBM, GFS, HRRR]

    def test_priority_and_fill(self):
        by_model = {
            HRRR: answer([10, None, None], gusts=[30, 31, None], daily_max=99, current_temp=11),
            NBM: answer([20, 21, None], gusts=[40, 41, 42], daily_max=25, current_temp=21),
            GFS: answer([30, 31, 32], daily_max=26, current_temp=31, uv=[1, 2, 3]),
        }
        out = stitch(by_model, self.order_now, self.order_daily)
        self.assertEqual(out["hourly"]["temperature_2m"], [10, 21, 32])
        self.assertEqual(out["hourly"]["wind_gusts_10m"], [30, 31, 42])
        self.assertEqual(out["hourly"]["uv_index"], [1, 2, 3], "a field only GFS has still comes through")
        self.assertEqual(out["current"]["temperature_2m"], 11)
        self.assertEqual(out["daily"]["temperature_2m_max"], [25], "daily prefers NBM over a part-day HRRR")
        self.assertEqual(out["hourly_units"]["temperature_2m"], "°C")

    def test_outside_conus_only_gfs_answers(self):
        out = stitch({HRRR: None, NBM: None, GFS: answer([1, 2, 3], daily_max=4)}, self.order_now, self.order_daily)
        self.assertEqual(out["hourly"]["temperature_2m"], [1, 2, 3])
        self.assertEqual(out["daily"]["temperature_2m_max"], [4])

    def test_multi_point_overlay(self):
        def cur(t):
            return [{"current": {"time": "x", "temperature_2m": t}}, {"current": {"time": "x", "temperature_2m": None}}]
        out = stitch({HRRR: cur(None), NBM: cur(5), GFS: [{"current": {"time": "x", "temperature_2m": 1}},
                                                         {"current": {"time": "x", "temperature_2m": 2}}]},
                     self.order_now, self.order_daily)
        self.assertEqual([p["current"]["temperature_2m"] for p in out], [5, 2])

    def test_misaligned_times_line_up(self):
        a = {"time": TIMES, "t": [1, None, None]}
        b = {"time": TIMES[1:], "t": [2, 3]}
        self.assertEqual(merge_block([a, b])["t"], [1, 2, 3])

    def test_classify(self):
        self.assertEqual(classify({"latitude": "1,2", "current": "x"})[0], datasets.OVERLAY)
        self.assertEqual(classify({"latitude": "1", "current": "x"})[0], datasets.CURRENT)
        self.assertEqual(classify({"latitude": "1", "current": "x", "hourly": "y"})[0], datasets.FORECAST)


class FakeUpstream:
    def __init__(self):
        self.health = Health()
        self.urls = []
        self.fail = False

    async def get(self, url, **kwargs):
        self.urls.append(url)
        if self.fail:
            raise UpstreamError("connection refused")
        return Answer(200, json.dumps(answer([1, 2, 3], daily_max=4)).encode())


class Cache(unittest.IsolatedAsyncioTestCase):
    async def test_cache_stands_in_then_503_and_never_the_public_api(self):
        cfg = make_config()
        up = FakeUpstream()
        weather = LocalWeather(up, datasets.Tracker(), cfg)
        query = {"latitude": "35.0123", "longitude": "-84.0456", "hourly": "temperature_2m", "past_days": "1"}
        body, fetched, dataset = await weather.forecast(query)
        self.assertEqual(json.loads(body)["hourly"]["temperature_2m"], [1, 2, 3])
        self.assertTrue(all(u.startswith(cfg.open_meteo_url) for u in up.urls))
        self.assertFalse(any("api.open-meteo.com" in u for u in up.urls))
        self.assertTrue(any("latitude=35.00" in u and "models=ncep_hrrr_conus" in u for u in up.urls))
        # Local Open-Meteo stops: the cached copy keeps serving with its old fetch time.
        up.fail = True
        for k, v in list(weather.cache.data.items()):
            weather.cache.data[k] = (v[0], v[1] - 3600)
        again, fetched2, _ = await weather.forecast(query)
        self.assertEqual(again, body)
        self.assertLess(fetched2, time.time() - 3000)
        # Once the cache runs out, 503.
        for k, v in list(weather.cache.data.items()):
            weather.cache.data[k] = (v[0], v[1] - 24 * 3600)
        with self.assertRaises(Unavailable):
            await weather.forecast(query)


if __name__ == "__main__":
    unittest.main()
