#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Granting new textures to polygons without IDs (#16, newyork-style data).

For LOD2 data whose polygons have no gml:id, verify with a small synthetic
box-shaped building GML that (1) parse assigns deterministic planned IDs
(<gid>_p<n>) and faces/newAtlas work, (2) on apply the gml:id values are
materialized and appearance target/ring references resolve, and (3) the result is well-formed.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import unittest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "tex_app_idgrant", REPO_ROOT / "tools" / "tex_editor" / "app.py")
tex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tex)


def _ring(pts) -> str:
    """Sequence of (lat, lon, z) -> posList string of a closed ring."""
    closed = list(pts) + [pts[0]]
    return " ".join(f"{a} {b} {c}" for a, b, c in closed)


def _wall(p1, p2, z0, z1) -> str:
    """One vertical wall face between two points (no gml:id)."""
    (a1, b1), (a2, b2) = p1, p2
    pts = [(a1, b1, z0), (a2, b2, z0), (a2, b2, z1), (a1, b1, z1)]
    return (
        "<bldg:boundedBy><bldg:WallSurface><bldg:lod2MultiSurface>"
        "<gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior>"
        f"<gml:LinearRing><gml:posList>{_ring(pts)}</gml:posList></gml:LinearRing>"
        "</gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>"
        "</bldg:lod2MultiSurface></bldg:WallSurface></bldg:boundedBy>"
    )


def _idless_gml(gid: str = "gml_TESTBLDG1") -> str:
    """One box-shaped building (10m square, 10m tall), CityGML 2.0 with no polygon IDs."""
    s, w, n, e = 35.0, 135.0, 35.00009, 135.00011
    ground = [(s, w, 0.0), (s, e, 0.0), (n, e, 0.0), (n, w, 0.0)]
    walls = (
        _wall((s, w), (s, e), 0.0, 10.0)
        + _wall((s, e), (n, e), 0.0, 10.0)
        + _wall((n, e), (n, w), 0.0, 10.0)
        + _wall((n, w), (s, w), 0.0, 10.0)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gml="http://www.opengis.net/gml">
<gml:boundedBy><gml:Envelope srsDimension="3">
<gml:lowerCorner>{s} {w} 0</gml:lowerCorner>
<gml:upperCorner>{n} {e} 10</gml:upperCorner>
</gml:Envelope></gml:boundedBy>
<core:cityObjectMember>
<bldg:Building gml:id="{gid}">
<bldg:measuredHeight uom="m">10.0</bldg:measuredHeight>
<bldg:boundedBy><bldg:GroundSurface><bldg:lod2MultiSurface>
<gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior>
<gml:LinearRing><gml:posList>{_ring(ground)}</gml:posList></gml:LinearRing>
</gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>
</bldg:lod2MultiSurface></bldg:GroundSurface></bldg:boundedBy>
{walls}
</bldg:Building>
</core:cityObjectMember>
</core:CityModel>
"""


_JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q==")


class TestIdGrantNewTexture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "citygml").mkdir()
        (root / "citygml" / "box.gml").write_text(_idless_gml(), encoding="utf-8")
        (root / "4dcitygml.json").write_text(json.dumps({
            "id": "test-city", "building_id": {"type": "gml:id"},
            "lang": "en", "data_dirs": ["citygml"],
        }), encoding="utf-8")
        self.repo = tex.TexRepo(root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_faces_json_synthesizes_ids_and_new_atlas(self):
        res = self.repo.faces_json("box", "gml_TESTBLDG1")
        self.assertTrue(res["faces"], "wall surfaces are enumerated")
        self.assertIsNotNone(res["newAtlas"])
        for f in res["faces"]:
            self.assertRegex(f["pid"], r"^gml_TESTBLDG1_p\d+$")
            self.assertTrue(f["editable"])

    def test_apply_grants_ids_and_inserts_consistent_appearance(self):
        res = self.repo.faces_json("box", "gml_TESTBLDG1")
        out = self.repo.apply_textures(
            "box", "gml_TESTBLDG1",
            [{"orig": res["newAtlas"]["img"],
              "data": base64.b64encode(_JPEG_1PX).decode()}])
        self.assertTrue(out["ok"] and out.get("new"))
        self.assertTrue(out["idsAdded"], "gml:id is assigned")

        raw = (Path(self._tmp.name) / "citygml" / "box.gml").read_bytes()
        root = ET.fromstring(raw)  # well-formed
        text = raw.decode("utf-8")
        # Granted IDs actually exist and all appearance references resolve
        for pid in out["idsAdded"]:
            self.assertIn(f'gml:id="{pid}"', text)
            self.assertIn(f'<app:target uri="#{pid}">', text)
            self.assertIn(f'gml:id="{pid}_r0"', text)
            self.assertIn(f'ring="#{pid}_r0"', text)
        # Image was also added (R1: new addition)
        self.assertTrue(any(
            p.name.startswith("tex_") for p in
            (Path(self._tmp.name) / "citygml").rglob("*.jpg")))
        # On re-parse it appears as textured (replace mode)
        res2 = self.repo.faces_json("box", "gml_TESTBLDG1")
        self.assertIsNone(res2["newAtlas"])
        self.assertTrue(all(f["img"] != res["newAtlas"]["img"] for f in res2["faces"]))


if __name__ == "__main__":
    unittest.main()
