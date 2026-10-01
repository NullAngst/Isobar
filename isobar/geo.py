"""Small geometry helpers. No shapely, since a ray cast is all we need."""

import math


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _in_ring(lon, lat, ring):
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def _in_polygon(lon, lat, rings):
    if not rings or not _in_ring(lon, lat, rings[0]):
        return False
    return not any(_in_ring(lon, lat, hole) for hole in rings[1:])


def point_in_geometry(lat, lon, geometry):
    """True if the point falls inside a GeoJSON Polygon or MultiPolygon."""
    if not geometry:
        return False
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return _in_polygon(lon, lat, coords)
    if kind == "MultiPolygon":
        return any(_in_polygon(lon, lat, poly) for poly in coords)
    if kind == "GeometryCollection":
        return any(point_in_geometry(lat, lon, g) for g in geometry.get("geometries", []))
    return False
