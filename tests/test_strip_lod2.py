#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for strip_lod2 (generating the LOD2-stripped variant and measuring size)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import strip_lod2  # noqa: E402

# One building containing attributes + LOD0/1 (kept) and LOD2/3 geometry, boundary surfaces, installations, appearance (removed).
BUILDING = """
  <core:cityObjectMember>
    <bldg:Building gml:id="bldg-1">
      <bldg:measuredHeight uom="m">10.0</bldg:measuredHeight>
      <bldg:usage codeSpace="../../codelists/Building_usage.xml">411</bldg:usage>
      <bldg:lod0RoofEdge><gml:MultiSurface/></bldg:lod0RoofEdge>
      <bldg:lod1Solid><gml:Solid/></bldg:lod1Solid>
      <bldg:lod2Solid><gml:Solid/></bldg:lod2Solid>
      <bldg:boundedBy><bldg:WallSurface gml:id="w1"><bldg:lod2MultiSurface/></bldg:WallSurface></bldg:boundedBy>
      <bldg:boundedBy><bldg:RoofSurface gml:id="r1"/></bldg:boundedBy>
      <bldg:outerBuildingInstallation><bldg:BuildingInstallation/></bldg:outerBuildingInstallation>
      <bldg:lod3Solid><gml:Solid/></bldg:lod3Solid>
      <bldg:address><xAL:Address/></bldg:address>
      <uro:buildingIDAttribute><uro:BuildingIDAttribute/></uro:buildingIDAttribute>
    </bldg:Building>
  </core:cityObjectMember>"""

APPEARANCE = """
  <app:appearanceMember>
    <app:Appearance><app:theme>rgbTexture</app:theme>
      <app:surfaceDataMember><app:ParameterizedTexture>
        <app:imageURI>53394611_bldg_6697_appearance/x.jpg</app:imageURI>
      </app:ParameterizedTexture></app:surfaceDataMember>
    </app:Appearance>
  </app:appearanceMember>"""


def model(*bodies) -> bytes:
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" '
            'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2" '
            'xmlns:xAL="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0" '
            'xmlns:gml="http://www.opengis.net/gml">'
            + "".join(bodies) + "</core:CityModel>").encode("utf-8")


def localnames(el):
    return [strip_lod2._localname(c) for c in el if isinstance(c.tag, str)]


class TestStrip(unittest.TestCase):
    def _stripped_building(self):
        tree = etree.ElementTree(etree.fromstring(model(BUILDING, APPEARANCE)))
        strip_lod2.strip_tree(tree)
        return tree.getroot().find(".//{http://www.opengis.net/citygml/building/2.0}Building")

    def test_keeps_attributes_and_lod01(self):
        b = self._stripped_building()
        kept = set(localnames(b))
        self.assertIn("measuredHeight", kept)
        self.assertIn("usage", kept)
        self.assertIn("lod0RoofEdge", kept)
        self.assertIn("lod1Solid", kept)
        self.assertIn("address", kept)
        self.assertIn("buildingIDAttribute", kept)

    def test_drops_lod2plus_and_installations(self):
        b = self._stripped_building()
        kept = set(localnames(b))
        for gone in ("lod2Solid", "boundedBy", "outerBuildingInstallation", "lod3Solid"):
            self.assertNotIn(gone, kept)

    def test_drops_appearance(self):
        tree = etree.ElementTree(etree.fromstring(model(BUILDING, APPEARANCE)))
        info = strip_lod2.strip_tree(tree)
        self.assertEqual(info["appearance_members"], 1)
        self.assertIsNone(tree.getroot().find(strip_lod2._APPEARANCE_MEMBER))

    def test_measure_breakdown_adds_up(self):
        info = strip_lod2.measure_tree(etree.ElementTree(etree.fromstring(model(BUILDING, APPEARANCE))))
        parts = info["stripped_gml_bytes"] + info["lod2plus_geometry_bytes"] + info["appearance_inline_bytes"]
        # Diff-based measurement, so full ≒ stripped + geom + appearance (only minor declaration/formatting differences)
        self.assertAlmostEqual(parts, info["serialized_full_bytes"], delta=200)
        self.assertGreater(info["lod2plus_geometry_bytes"], 0)
        self.assertGreater(info["appearance_inline_bytes"], 0)


class TestDatasetIO(unittest.TestCase):
    def _make_dataset(self, root: Path):
        bldg = root / "ds" / "udx" / "bldg"
        bldg.mkdir(parents=True)
        (bldg / "t.gml").write_bytes(model(BUILDING, APPEARANCE))
        # Texture image directory (excluded from the lite variant; counted separately in measurement)
        app = bldg / "53394611_bldg_6697_appearance"
        app.mkdir()
        (app / "x.jpg").write_bytes(b"\xff\xd8" + b"0" * 5000)
        (root / "ds" / "codelists").mkdir(parents=True)
        (root / "ds" / "codelists" / "Building_usage.xml").write_text("<x/>", encoding="utf-8")
        return root / "ds"

    def test_measure_counts_textures_separately(self):
        with tempfile.TemporaryDirectory() as d:
            ds = self._make_dataset(Path(d))
            r = strip_lod2.measure_dataset(ds)
            self.assertEqual(r["buildings"], 1)
            self.assertGreaterEqual(r["texture_image_bytes"], 5000)
            self.assertLess(r["lite_total_bytes"], r["full_total_bytes"])

    def test_write_lite_produces_valid_smaller_gml_and_no_textures(self):
        with tempfile.TemporaryDirectory() as d:
            ds = self._make_dataset(Path(d))
            out = Path(d) / "out"
            res = strip_lod2.write_lite(ds, out)
            self.assertEqual(res["gml_files"], 1)
            lite_gml = out / "ds" / "udx" / "bldg" / "t.gml"
            self.assertTrue(lite_gml.is_file())
            # The lite variant is smaller than the original and no texture image directory is produced
            self.assertLess(lite_gml.stat().st_size, (ds / "udx" / "bldg" / "t.gml").stat().st_size)
            self.assertEqual(list((out / "ds" / "udx" / "bldg").glob("*_appearance")), [])
            # codelists are copied for resolving attribute meanings
            self.assertTrue((out / "ds" / "codelists" / "Building_usage.xml").is_file())
            # Re-parses as valid XML
            etree.parse(str(lite_gml))


if __name__ == "__main__":
    unittest.main()
