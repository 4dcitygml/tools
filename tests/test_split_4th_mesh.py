#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for split_4th_mesh quadrant assignment (area apportionment).

Following the official PLATEAU rule "a feature straddling a boundary is stored whole in
the mesh with the larger area", verify assignment to the quadrant with the largest footprint area.
Run: python -m unittest tests.test_split_4th_mesh
"""
from __future__ import annotations

import unittest

from lxml import etree

from scripts.split_4th_mesh import GML, assign_quadrant, quadrant

# Simple cell: lat[0,1] x lon[0,1], midpoint (0.5,0.5). Quadrants 1=SW 2=SE 3=NW 4=NE
CELL = dict(lat_min=0.0, lon_min=0.0, dlat=1.0, dlon=1.0)


def _building(*rings: str) -> etree._Element:
    """Return a minimal bldg:Building whose lod0RoofEdge holds multiple posList rings."""
    members = "".join(
        "<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>"
        f"<gml:posList>{r}</gml:posList>"
        "</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>"
        for r in rings
    )
    xml = (
        f'<bldg:Building xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
        f'xmlns:gml="{GML}" gml:id="b">'
        f"<bldg:lod0RoofEdge><gml:MultiSurface>{members}</gml:MultiSurface></bldg:lod0RoofEdge>"
        f"</bldg:Building>"
    )
    return etree.fromstring(xml.encode("utf-8"))


def _rect(lat0, lat1, lon0, lon1) -> str:
    # posList is a sequence of (lat lon z)
    pts = [(lat0, lon0), (lat1, lon0), (lat1, lon1), (lat0, lon1)]
    return " ".join(f"{la} {lo} 0" for la, lo in pts)


class TestAssignQuadrant(unittest.TestCase):
    def test_footprint_fully_in_sw(self) -> None:
        b = _building(_rect(0.1, 0.4, 0.1, 0.4))  # entirely southwest
        self.assertEqual(assign_quadrant(b, **CELL), 1)

    def test_footprint_fully_in_ne(self) -> None:
        b = _building(_rect(0.6, 0.9, 0.6, 0.9))  # entirely northeast
        self.assertEqual(assign_quadrant(b, **CELL), 4)

    def test_area_majority_across_mid_lon(self) -> None:
        # lat[0.1,0.4] (south), lon[0.1,0.7] (straddles the 0.5 midline). West width 0.4 > east width 0.2 -> southwest (1)
        b = _building(_rect(0.1, 0.4, 0.1, 0.7))
        self.assertEqual(assign_quadrant(b, **CELL), 1)

    def test_area_rule_differs_from_bbox_center(self) -> None:
        # Large block in the west (area 0.06) plus small block in the east (area 0.02).
        # Area apportionment -> southwest (1). The bbox center is lon=0.6 (east) -> southeast (2), which disagrees.
        west = _rect(0.1, 0.4, 0.3, 0.5)   # 0.3 x 0.2 = 0.06
        east = _rect(0.35, 0.4, 0.5, 0.9)  # 0.05 x 0.4 = 0.02
        b = _building(west, east)
        self.assertEqual(assign_quadrant(b, **CELL), 1)      # area apportionment = southwest
        # Confirm the bbox center would give southeast (i.e. the case where this replacement changes behavior)
        self.assertEqual(quadrant(0.25, 0.6, **CELL), 2)

    def test_no_footprint_returns_none(self) -> None:
        xml = (
            f'<bldg:Building xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            f'xmlns:gml="{GML}" gml:id="b">'
            f"<bldg:measuredHeight>5</bldg:measuredHeight></bldg:Building>"
        )
        b = etree.fromstring(xml.encode("utf-8"))
        self.assertIsNone(assign_quadrant(b, **CELL))


if __name__ == "__main__":
    unittest.main()
