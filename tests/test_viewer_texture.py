# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The 3D view keeps each face's texture paired with its vertices.

An entity polygon with perPositionHeight is built by Cesium as a CoplanarPolygonGeometry, which
reverses the positions and the texture coordinates as two separate rings; on walls the positions'
test is decided by rounding, so some walls showed their texture upside down or mirrored. Textured
faces are therefore drawn as their own triangles (faceGeometry), vertex i with UV i. What the
browser really draws was measured in the development copy (walls facing four directions and roofs
of 0-80 degrees, before and after); here the page is checked for the parts that make it so, and
uvTexCoords is run as the page defines it (node, with a stand-in for Cesium.Cartesian2).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest

from tests.support import REPO_ROOT

VIEWER = REPO_ROOT / "tools" / "attr_editor" / "viewer.html"


def viewer_source() -> str:
    return VIEWER.read_text(encoding="utf-8")


def run_uv(face: dict, tex: dict) -> list:
    fn = re.search(r"function uvTexCoords\(face\) \{.*?\n\}", viewer_source(), re.S).group(0)
    script = (
        "const Cesium = { Cartesian2: function (x, y) { this.x = x; this.y = y; } };\n"
        f"const bldg = {{ tex: {json.dumps(tex)} }};\n{fn}\n"
        f"const tcs = uvTexCoords({json.dumps(face)});\n"
        "console.log(JSON.stringify(tcs ? tcs.map(c => [c.x, c.y]) : null));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


class TestViewerTexturedFaces(unittest.TestCase):
    def test_textured_faces_are_drawn_as_their_own_triangles(self):
        src = viewer_source()
        self.assertNotIn("textureCoordinates:", src)   # Cesium's ring reversal no longer sees the UVs
        self.assertIn("Cesium.PolygonPipeline.triangulate(flat, holeIndices", src)   # holes stay holes
        self.assertIn("values: new Float32Array(tcs.flatMap(c => [c.x, c.y]))", src)   # vertex i keeps UV i
        self.assertIn("new Cesium.GeometryInstance({ geometry, id: entity })", src)   # picks as the face
        self.assertIn("showFaceTexture(entity, fp, tcs, cropUV(img, texInfo.uv), gen)", src)
        self.assertIn("showFaceTexture(entity, fp, tcs, previewTex[face.id], gen)", src)
        # the photo is the face: white edges stay only on highlighted faces
        self.assertIn("if (!highlightPids.has(entity._facePid)) entity.polygon.outline = false;", src)

    def test_every_render_removes_the_previous_textures(self):
        src = viewer_source()
        body = src[src.index("function render() {"):]
        self.assertLess(body.index("texPrimitives.forEach(p => viewer.scene.primitives.remove(p))"),
                        body.index("viewer.entities.add("))
        self.assertIn("if (gen !== renderGen) return;", src)   # a late image does not land on an old render


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestViewerUvOrientation(unittest.TestCase):
    def test_uvs_keep_citygml_orientation_within_the_face_region(self):
        # CityGML v grows upward; Cesium samples the image with t = 1 at its top row
        face = {"id": "w", "pts": [[0, 0, 0], [1, 0, 0], [1, 0, 50], [0, 0, 50]]}
        tex = {"w": {"img": "a.jpg", "uv": [[0.0, 0.0], [0.125, 0.0], [0.125, 1.0], [0.0, 1.0], [0.0, 0.0]]}}
        self.assertEqual(run_uv(face, tex), [[0, 0], [1, 0], [1, 1], [0, 1]])
        face = {"id": "p", "pts": [[0, 0, 0], [1, 0, 0], [1, 0, 5]]}
        tex = {"p": {"img": "a.jpg", "uv": [[0.5, 0.25], [0.75, 0.25], [0.75, 0.5], [0.5, 0.25]]}}
        self.assertEqual(run_uv(face, tex), [[0, 0], [1, 0], [1, 1]])

    def test_hole_uvs_follow_the_outer_ring_in_its_frame(self):
        # positions are the outer ring then each hole (facePositions); the UVs come in that order,
        # normalized by the outer ring's region of the atlas
        face = {"id": "r", "pts": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                "holes": [[[0.4, 0.4, 0], [0.6, 0.4, 0], [0.6, 0.6, 0]]]}
        tex = {"r": {"img": "a.jpg", "uv": [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1], [0.5, 0.5]],
                     "holes": [[[0.6, 0.6], [0.8, 0.6], [0.8, 0.8], [0.6, 0.6]]]}}
        out = run_uv(face, tex)
        self.assertEqual(out[:4], [[0, 0], [1, 0], [1, 1], [0, 1]])
        self.assertEqual([[round(u, 6), round(v, 6)] for u, v in out[4:]], [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6]])

    def test_a_hole_without_coordinates_leaves_the_face_untextured(self):
        face = {"id": "r", "pts": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "holes": [[[0.4, 0.4, 0], [0.6, 0.4, 0], [0.5, 0.6, 0]]]}
        tex = {"r": {"img": "a.jpg", "uv": [[0, 0], [1, 0], [1, 1], [0, 0]]}}
        self.assertIsNone(run_uv(face, tex))


if __name__ == "__main__":
    unittest.main()
