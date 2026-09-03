#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for texture_check / gc_textures (R3, (a), image-to-building, GC).

Verifies localization under immutable operation (R1+R3), retexture detection ((a)), and unreferenced-image collection (GC).
Run: python -m unittest tests.test_texture_check
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.texture_check import (
    appearance_changed_buildings,
    dangling_image_uris,
    image_to_buildings,
    referenced_image_uris,
)
from scripts.gc_textures import orphans

NS = (
    'xmlns:core="http://www.opengis.net/citygml/2.0" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
    'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" '
    'xmlns:gml="http://www.opengis.net/gml"'
)


def _building(bid: str, poly: str) -> str:
    return f"""
  <core:cityObjectMember><bldg:Building gml:id="{bid}">
    <bldg:lod2Solid><gml:Solid><gml:exterior><gml:CompositeSurface><gml:surfaceMember>
      <gml:Polygon gml:id="{poly}"><gml:exterior><gml:LinearRing>
        <gml:posList>0 0 0 1 0 0 1 1 0 0 0 0</gml:posList>
      </gml:LinearRing></gml:exterior></gml:Polygon>
    </gml:surfaceMember></gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod2Solid>
  </bldg:Building></core:cityObjectMember>"""


def _texture(img: str, poly: str, uv: str = "0 0 1 0 1 1 0 0") -> str:
    return f"""
    <app:surfaceDataMember><app:ParameterizedTexture>
      <app:imageURI>{img}</app:imageURI>
      <app:target uri="#{poly}"><app:TexCoordList>
        <app:textureCoordinates ring="#r_{poly}">{uv}</app:textureCoordinates>
      </app:TexCoordList></app:target>
    </app:ParameterizedTexture></app:surfaceDataMember>"""


def _model(buildings: str, textures: str) -> bytes:
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel {NS}>'
        f"{buildings}"
        f"<app:appearanceMember><app:Appearance>{textures}</app:Appearance></app:appearanceMember>"
        f"</core:CityModel>"
    )
    return xml.encode("utf-8")


APP = "tile_appearance"


class TestReferences(unittest.TestCase):
    def test_referenced_uris_and_image_to_buildings(self):
        raw = _model(
            _building("A", "polyA") + _building("B", "polyB"),
            _texture(f"{APP}/img1.jpg", "polyA") + _texture(f"{APP}/img2.jpg", "polyB"),
        )
        self.assertEqual(referenced_image_uris(raw), {f"{APP}/img1.jpg", f"{APP}/img2.jpg"})
        i2b = image_to_buildings(raw)
        self.assertEqual(i2b[f"{APP}/img1.jpg"], {"A"})
        self.assertEqual(i2b[f"{APP}/img2.jpg"], {"B"})


class TestAppearanceChanged(unittest.TestCase):
    def test_imageuri_change_flags_only_that_building(self):
        base = _model(
            _building("A", "polyA") + _building("B", "polyB"),
            _texture(f"{APP}/img1.jpg", "polyA") + _texture(f"{APP}/img2.jpg", "polyB"),
        )
        # Point A's imageURI at a new image (simulates immutable add-new)
        head = _model(
            _building("A", "polyA") + _building("B", "polyB"),
            _texture(f"{APP}/img1_new.jpg", "polyA") + _texture(f"{APP}/img2.jpg", "polyB"),
        )
        self.assertEqual(appearance_changed_buildings(base, head), ["A"])

    def test_uv_change_is_detected(self):
        base = _model(_building("A", "polyA"), _texture(f"{APP}/img1.jpg", "polyA", "0 0 1 0 1 1 0 0"))
        head = _model(_building("A", "polyA"), _texture(f"{APP}/img1.jpg", "polyA", "0 0 1 0 1 1 0 1"))
        self.assertEqual(appearance_changed_buildings(base, head), ["A"])

    def test_no_change_no_flag(self):
        raw = _model(_building("A", "polyA"), _texture(f"{APP}/img1.jpg", "polyA"))
        self.assertEqual(appearance_changed_buildings(raw, raw), [])


class TestDanglingAndGC(unittest.TestCase):
    def _write_tile(self, d: Path, images: list[str], referenced: list[str]) -> Path:
        """Create tile.gml in d (with imageURI references to `referenced`) plus tile_appearance/ (actual `images` files)."""
        app = d / APP
        app.mkdir()
        for img in images:
            (app / img).write_bytes(b"\xff\xd8\xff\xe0jpegbytes")  # dummy image
        textures = "".join(_texture(f"{APP}/{r}", f"poly{i}") for i, r in enumerate(referenced))
        buildings = "".join(_building(f"B{i}", f"poly{i}") for i in range(len(referenced)))
        gml = d / "tile.gml"
        gml.write_bytes(_model(buildings, textures))
        return gml

    def test_dangling_detected(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # img1 exists; img_missing is referenced but the file is absent
            gml = self._write_tile(d, images=["img1.jpg"], referenced=["img1.jpg", "img_missing.jpg"])
            miss = dangling_image_uris(gml)
            self.assertEqual(miss, [f"{APP}/img_missing.jpg"])

    def test_no_dangling_when_all_present(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            gml = self._write_tile(d, images=["img1.jpg", "img2.jpg"], referenced=["img1.jpg", "img2.jpg"])
            self.assertEqual(dangling_image_uris(gml), [])

    def test_gc_finds_orphan(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # img_orphan.jpg exists but nothing references it
            self._write_tile(d, images=["img1.jpg", "img_orphan.jpg"], referenced=["img1.jpg"])
            orph = orphans(d)
            self.assertEqual([p.name for p in orph], ["img_orphan.jpg"])


if __name__ == "__main__":
    unittest.main()
