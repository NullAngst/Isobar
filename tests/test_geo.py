import unittest

from tests import helpers  # noqa: F401
from isobar import geo

SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]


class PointInGeometry(unittest.TestCase):
    def test_polygon(self):
        poly = {"type": "Polygon", "coordinates": [SQUARE]}
        self.assertTrue(geo.point_in_geometry(5, 5, poly))
        self.assertFalse(geo.point_in_geometry(15, 5, poly))

    def test_hole_is_outside(self):
        poly = {"type": "Polygon", "coordinates": [SQUARE, HOLE]}
        self.assertFalse(geo.point_in_geometry(5, 5, poly))
        self.assertTrue(geo.point_in_geometry(2, 2, poly))

    def test_multipolygon_and_empty(self):
        far = [[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]
        multi = {"type": "MultiPolygon", "coordinates": [[SQUARE], [far]]}
        self.assertTrue(geo.point_in_geometry(25, 25, multi))
        self.assertFalse(geo.point_in_geometry(15, 15, multi))
        self.assertFalse(geo.point_in_geometry(1, 1, None))

    def test_haversine(self):
        # Atlanta to Chattanooga is about 170 km.
        self.assertAlmostEqual(geo.haversine_km(33.749, -84.388, 35.046, -85.309), 167, delta=8)


if __name__ == "__main__":
    unittest.main()
