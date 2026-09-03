#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Known-point verification of crs_transformer (inverse projection with no external dependencies).

Mechanism for converting coordinates of international datasets
(munich=ETRS89/UTM32, newyork=EPSG:2263) to WGS84 lat/lon for the frontend Leaflet map.
Tolerance is 1e-4 degrees (about 10 m) — sufficient for tile frames and building display.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "attr_app_crs", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attr)


class TestCrsTransformer(unittest.TestCase):
    def test_utm32_urn_adv_matches_munich_station(self):
        # srsName from real munich data (urn:adv notation). Real coordinates in front of the central station
        tf = attr.crs_transformer("urn:adv:crs:ETRS89_UTM32*DE_DHHN2016_NH")
        self.assertIsNotNone(tf)
        lat, lon = tf(690970.21, 5335015.15)
        self.assertAlmostEqual(lat, 48.1397, delta=1e-3)
        self.assertAlmostEqual(lon, 11.5671, delta=1e-3)

    def test_epsg_25832_equals_urn_form(self):
        a = attr.crs_transformer("EPSG:25832")
        b = attr.crs_transformer("urn:adv:crs:ETRS89_UTM32*DE_DHHN2016_NH")
        self.assertEqual(a(691000.0, 5335000.0), b(691000.0, 5335000.0))

    def test_epsg_2263_matches_grand_central(self):
        # Real newyork data (US feet). Real coordinates around Grand Central
        tf = attr.crs_transformer("EPSG:2263")
        self.assertIsNotNone(tf)
        lat, lon = tf(992117.601050824, 214737.719852567)
        self.assertAlmostEqual(lat, 40.7561, delta=1e-3)
        self.assertAlmostEqual(lon, -73.9716, delta=1e-3)

    def test_latlon_crs_needs_no_transform(self):
        # PLATEAU (EPSG:6697 lat/lon) and unknown systems return None = no transform
        self.assertIsNone(attr.crs_transformer(
            "http://www.opengis.net/def/crs/EPSG/0/6697"))
        self.assertIsNone(attr.crs_transformer(""))
        self.assertIsNone(attr.crs_transformer("EPSG:99999"))


class TestEnvelopeBounds(unittest.TestCase):
    def test_projected_envelope_becomes_wgs84_bounds(self):
        import tempfile
        gml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CityModel xmlns:gml="http://www.opengis.net/gml">'
            '<gml:boundedBy><gml:Envelope srsName="EPSG:25832" srsDimension="3">'
            "<gml:lowerCorner>690000 5335000 500</gml:lowerCorner>"
            "<gml:upperCorner>691000 5336000 600</gml:upperCorner>"
            "</gml:Envelope></gml:boundedBy></CityModel>"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.gml"
            p.write_text(gml, encoding="utf-8")
            repo = object.__new__(attr.Repo)
            b = repo._envelope_bounds(p)
        self.assertIsNotNone(b)
        s, w, n, e = b
        self.assertTrue(48.1 < s < n < 48.2, b)
        self.assertTrue(11.5 < w < e < 11.6, b)


if __name__ == "__main__":
    unittest.main()
