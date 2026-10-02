# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/citygml_dialect.py: a file is read in its own CityGML version and CRS, by the editors and
the CI preview alike (the preview read only CityGML 2.0 latitude/longitude and showed no building
of a CityGML 1.0 city in UTM or in US feet)."""
from __future__ import annotations

import unittest

from scripts import citygml_dialect as D
from scripts import extract_building_preview as preview


def _ring(pts, z):
    return " ".join(f"{x} {y} {z}" for x, y in [*pts, pts[0]])


def _doc(srs: str, square: list, ground: float, roof: float) -> bytes:
    surface = lambda kind, z: (
        f"<bldg:boundedBy><bldg:{kind}><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon>"
        f"<gml:exterior><gml:LinearRing><gml:posList>{_ring(square, z)}</gml:posList></gml:LinearRing></gml:exterior>"
        f"</gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface></bldg:{kind}></bldg:boundedBy>")
    return (
        '<CityModel xmlns="http://www.opengis.net/citygml/1.0" xmlns:gml="http://www.opengis.net/gml" '
        'xmlns:bldg="http://www.opengis.net/citygml/building/1.0">'
        f'<gml:boundedBy><gml:Envelope srsName="{srs}" srsDimension="3"><gml:lowerCorner>0 0 0</gml:lowerCorner>'
        '<gml:upperCorner>1 1 1</gml:upperCorner></gml:Envelope></gml:boundedBy>'
        '<cityObjectMember><bldg:Building gml:id="B1">'
        + surface("RoofSurface", roof) + surface("GroundSurface", ground)
        + "</bldg:Building></cityObjectMember></CityModel>").encode()


class TestDialect(unittest.TestCase):
    def test_namespaces_follow_the_file(self):
        root = preview.safe_fromstring(_doc("EPSG:25832", [(0, 0), (1, 0), (1, 1)], 0, 1))
        self.assertEqual(D.ns_for_root(root)["bldg"], "http://www.opengis.net/citygml/building/1.0")
        self.assertIsNotNone(D.file_transformer(root, D.ns_for_root(root)))

    def test_preview_of_a_utm_city_with_lod2_only(self):
        # Munich: ETRS89 / UTM 32N, no LOD0/LOD1, no measuredHeight
        square = [(691000, 5334000), (691010, 5334000), (691010, 5334010), (691000, 5334010)]
        b = preview._extract_buildings(_doc("EPSG:25832", square, 518.0, 540.0))["B1"]
        lat, lon = b["coords"][0]
        self.assertAlmostEqual(lat, 48.13, delta=0.05)
        self.assertAlmostEqual(lon, 11.58, delta=0.05)
        self.assertEqual((b["base"], b["height"]), (518.0, 22.0))   # the ground face is the footprint
        self.assertEqual(len(b["lod2"]), 2)

    def test_preview_of_a_us_feet_city(self):
        # New York: NAD83 / Long Island in US feet, heights in feet too
        square = [(990000, 213000), (990100, 213000), (990100, 213100), (990000, 213100)]
        b = preview._extract_buildings(_doc("EPSG:2263", square, 50.0, 150.0))["B1"]
        lat, lon = b["coords"][0]
        self.assertAlmostEqual(lat, 40.75, delta=0.05)
        self.assertAlmostEqual(lon, -73.98, delta=0.05)
        self.assertAlmostEqual(b["height"], 100 * 1200 / 3937, places=2)


if __name__ == "__main__":
    unittest.main()
