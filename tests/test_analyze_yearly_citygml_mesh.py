# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.analyze_yearly_citygml_mesh import compare, load_buildings


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
            return load_buildings(old_path), load_buildings(new_path)

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


if __name__ == "__main__":
    unittest.main()
