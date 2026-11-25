# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import dialects
from scripts import building_compare as C
from scripts.building_compare import compare

PLATEAU = dialects.dialect("plateau")
TOKYO_FRAME = C.Frame(C.LATLON.transform, 35.71)   # the fixtures: latitude, longitude, height


def citygml(buildings: list[tuple[str, str, list[tuple[float, float]], str]]) -> str:
    members = []
    for building_id, city_code, points, value in buildings:
        coordinates = " ".join(f"{lat} {lon} 0" for lat, lon in points)
        members.append(
            f"""
  <core:cityObjectMember>
    <bldg:Building gml:id="{building_id}">
      <bldg:class>{value}</bldg:class>
      <bldg:lod0RoofEdge>
        <gml:MultiSurface><gml:surfaceMember><gml:Polygon>
          <gml:exterior><gml:LinearRing><gml:posList>{coordinates}</gml:posList></gml:LinearRing></gml:exterior>
        </gml:Polygon></gml:surfaceMember></gml:MultiSurface>
      </bldg:lod0RoofEdge>
      <uro:buildingIDAttribute><uro:BuildingIDAttribute><uro:city>{city_code}</uro:city></uro:BuildingIDAttribute></uro:buildingIDAttribute>
    </bldg:Building>
  </core:cityObjectMember>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">
{''.join(members)}
</core:CityModel>
"""


class YearlyCityGMLMeshTest(unittest.TestCase):
    def load_pair(self, old_xml: str, new_xml: str):
        with tempfile.TemporaryDirectory() as directory:
            old_path = Path(directory) / "old.gml"
            new_path = Path(directory) / "new.gml"
            old_path.write_text(old_xml, encoding="utf-8")
            new_path.write_text(new_xml, encoding="utf-8")
            return C.load_buildings(old_path, TOKYO_FRAME, PLATEAU), C.load_buildings(new_path, TOKYO_FRAME, PLATEAU)

    def test_id_change_with_same_lod0_uses_fingerprint(self):
        ring = [(35.70, 139.70), (35.70, 139.701), (35.701, 139.701), (35.701, 139.70), (35.70, 139.70)]
        old, new = self.load_pair(
            citygml([("old-id", "13101", ring, "普通建物")]),
            citygml([("new-id", "13101", ring, "普通建物")]),
        )
        result = compare(old, new, 0.5, 2.0)
        self.assertEqual(result["summary"]["matched_one_to_one"], 1)
        self.assertEqual(result["summary"]["match_methods"], {"lod0_fingerprint_1mm": 1})
        self.assertEqual(result["summary"]["lod0_equal_1mm"], 1)
        self.assertEqual(result["summary"]["id_changed"], 1)

    def test_attribute_change_with_same_id_is_counted(self):
        ring = [(35.70, 139.70), (35.70, 139.701), (35.701, 139.701), (35.701, 139.70), (35.70, 139.70)]
        old, new = self.load_pair(
            citygml([("same-id", "13101", ring, "普通建物")]),
            citygml([("same-id", "13101", ring, "堅ろう建物")]),
        )
        result = compare(old, new, 0.5, 2.0)
        self.assertEqual(result["summary"]["attributes_changed"], 1)
        self.assertEqual(result["summary"]["lod0_geometry_changed"], 0)

    def test_split_is_reported_even_when_one_half_reaches_iou_threshold(self):
        old_ring = [(35.70, 139.70), (35.70, 139.702), (35.701, 139.702), (35.701, 139.70), (35.70, 139.70)]
        west = [(35.70, 139.70), (35.70, 139.701), (35.701, 139.701), (35.701, 139.70), (35.70, 139.70)]
        east = [(35.70, 139.701), (35.70, 139.702), (35.701, 139.702), (35.701, 139.701), (35.70, 139.701)]
        old, new = self.load_pair(
            citygml([("old", "13101", old_ring, "普通建物")]),
            citygml([("west", "13101", west, "普通建物"), ("east", "13101", east, "普通建物")]),
        )
        result = compare(old, new, 0.5, 2.0)
        self.assertEqual(result["summary"]["split_candidates"], 1)



class TestTheFrame(unittest.TestCase):
    """D-4b5: the comparison assumed JGD2011 latitude / longitude, three-dimensional posLists and a
    Tokyo reference latitude. It reads the file's own CRS and dimension now."""

    def write(self, d, srs, poslist, dimension="3", corners=("0 0", "1 1")):
        path = Path(d) / "t.gml"
        path.write_text(f'''<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml">
<gml:boundedBy><gml:Envelope srsName="{srs}" srsDimension="{dimension}"><gml:lowerCorner>{corners[0]}</gml:lowerCorner>
<gml:upperCorner>{corners[1]}</gml:upperCorner></gml:Envelope></gml:boundedBy>
<core:cityObjectMember><bldg:Building gml:id="b"><bldg:lod0FootPrint><gml:MultiSurface><gml:surfaceMember><gml:Polygon>
<gml:exterior><gml:LinearRing><gml:posList>{poslist}</gml:posList></gml:LinearRing></gml:exterior>
</gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod0FootPrint></bldg:Building></core:cityObjectMember>
</core:CityModel>''', encoding="utf-8")
        return path

    def test_a_utm_file_is_measured_in_metres(self):
        ring = "690000 5334000 690010 5334000 690010 5334010 690000 5334010 690000 5334000"
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "urn:adv:crs:ETRS89_UTM32*DE_DHHN2016_NH", ring, dimension="2",
                              corners=("690000 5334000", "690010 5334010"))   # the reference latitude: the data's
            b = C.load_buildings(path, pack=dialects.PLAIN)["b"]
        self.assertAlmostEqual(b.geom.area, 100.0, delta=1.5)   # 10 m x 10 m, through latitude / longitude

    def test_a_2d_poslist_is_read(self):
        ring = "35.7 139.7 35.7 139.701 35.701 139.701 35.701 139.7 35.7 139.7"
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "http://www.opengis.net/def/crs/EPSG/0/6668", ring, dimension="2")
            self.assertIsNotNone(C.load_buildings(path, pack=dialects.PLAIN)["b"].geom)

    def test_an_unknown_crs_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "urn:ogc:def:crs:EPSG::9999", "0 0 0 1 0 0 1 1 0 0 0 0")
            with self.assertRaises(ValueError):
                C.load_buildings(path, pack=dialects.PLAIN)

    def test_the_municipality_is_the_packs(self):
        self.assertEqual(PLATEAU.municipality(b"<uro:city>13101</uro:city>"), "13101")
        self.assertIsNone(dialects.PLAIN.municipality)

if __name__ == "__main__":
    unittest.main()
