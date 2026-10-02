# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One reader for LOD2 faces and their textures (editors, review 3D view, CI preview)."""
from __future__ import annotations

import unittest
from xml.etree import ElementTree as ET

from scripts import citygml_faces as cf
from scripts import extract_building_preview as preview

NS = {"gml": "http://www.opengis.net/gml", "bldg": "http://www.opengis.net/citygml/building/2.0",
      "app": "http://www.opengis.net/citygml/appearance/2.0", "core": "http://www.opengis.net/citygml/2.0"}


def ring(rid, pts):
    flat = " ".join(f"{lat} {lon} {z}" for lon, lat, z in [*pts, pts[0]])
    attr = f' gml:id="{rid}"' if rid else ""
    return f"<gml:LinearRing{attr}><gml:posList>{flat}</gml:posList></gml:LinearRing>"


SQUARE = [(139.0, 35.0, 0), (139.001, 35.0, 0), (139.001, 35.001, 0), (139.0, 35.001, 0)]
HOLE = [(139.0004, 35.0004, 0), (139.0006, 35.0004, 0), (139.0006, 35.0006, 0), (139.0004, 35.0006, 0)]
DOC = f"""<core:CityModel xmlns:core="{NS['core']}" xmlns:bldg="{NS['bldg']}" xmlns:gml="{NS['gml']}" xmlns:app="{NS['app']}">
<core:cityObjectMember><bldg:Building gml:id="b1">
 <bldg:measuredHeight uom="m">10</bldg:measuredHeight>
 <bldg:lod0RoofEdge><gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior>{ring("", SQUARE)}</gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod0RoofEdge>
 <bldg:boundedBy><bldg:RoofSurface><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember>
  <gml:Polygon gml:id="poly-roof"><gml:exterior>{ring("r-ext", SQUARE)}</gml:exterior><gml:interior>{ring("r-hole", HOLE)}</gml:interior></gml:Polygon>
 </gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface></bldg:RoofSurface></bldg:boundedBy>
 <bldg:consistsOfBuildingPart><bldg:BuildingPart gml:id="part1"><bldg:boundedBy><bldg:WallSurface><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember>
  <gml:Polygon><gml:exterior>{ring("", SQUARE)}</gml:exterior></gml:Polygon>
 </gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface></bldg:WallSurface></bldg:boundedBy></bldg:BuildingPart></bldg:consistsOfBuildingPart>
</bldg:Building></core:cityObjectMember>
<app:appearanceMember><app:Appearance><app:surfaceDataMember><app:ParameterizedTexture>
 <app:imageURI>a/roof.jpg</app:imageURI>
 <app:target uri="#poly-roof"><app:TexCoordList>
  <app:textureCoordinates ring="#r-hole">0.4 0.4 0.6 0.4 0.6 0.6 0.4 0.6 0.4 0.4</app:textureCoordinates>
  <app:textureCoordinates ring="#r-ext">0 0 1 0 1 1 0 1 0 0</app:textureCoordinates>
 </app:TexCoordList></app:target>
</app:ParameterizedTexture></app:surfaceDataMember></app:Appearance></app:appearanceMember>
</core:CityModel>"""


class TestFaces(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(DOC)
        self.building = self.root.find(f".//{{{NS['bldg']}}}Building")

    def test_a_hole_is_a_hole_not_a_face(self):
        # every posList used to become a face of the same id: the hole was drawn filled
        faces = cf.lod2_faces(self.building, NS, gid="b1")
        roof = [f for f in faces if f["id"] == "poly-roof"]
        self.assertEqual(len(roof), 1)
        self.assertEqual(len(roof[0]["pts"]), 4)
        self.assertEqual(len(roof[0]["holes"]), 1)

    def test_coordinates_are_the_rings_own(self):
        # the first <textureCoordinates> here belongs to the hole; it was taken for the face
        by_ring, by_poly = cf.texture_rings(self.root, NS)
        roof = next(f for f in cf.lod2_faces(self.building, NS, gid="b1") if f["id"] == "poly-roof")
        tex = cf.face_texture(roof, by_ring, by_poly)
        self.assertEqual(tex["uv"][:4], [[0, 0], [1, 0], [1, 1], [0, 1]])
        self.assertEqual(tex["holes"][0][0], [0.4, 0.4])

    def test_building_parts_and_planned_ids(self):
        faces = cf.lod2_faces(self.building, NS, gid="b1")
        self.assertEqual([f["id"] for f in faces], ["poly-roof", "b1_p1"])   # the part's wall, id planned

    def test_clients_get_no_ring_ids(self):
        face = cf.lod2_faces(self.building, NS, gid="b1")[0]
        self.assertNotIn("ring", cf.public_face(face))
        self.assertNotIn("holeRings", cf.public_face(face))

    def test_the_ci_preview_reads_the_same(self):
        buildings = preview._extract_buildings(DOC.encode())
        texmap = preview._extract_texmap(DOC.encode())
        faces = buildings["b1"]["lod2"]
        self.assertEqual([f["id"] for f in faces], ["poly-roof", "b1_p1"])   # BuildingParts were missed
        self.assertEqual(texmap["poly-roof"]["uv"][0], [0, 0])
        self.assertIn("holes", faces[0])


if __name__ == "__main__":
    unittest.main()
