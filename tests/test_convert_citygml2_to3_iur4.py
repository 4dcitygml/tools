# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from lxml import etree


MODULE_PATH = Path(__file__).parents[1] / "scripts/convert_citygml2_to3_iur4.py"
SPEC = importlib.util.spec_from_file_location("convert_citygml2_to3_iur4", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SOURCE = """<?xml version="1.0"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:uro="https://www.geospatial.jp/iur/uro/3.0">
 <core:cityObjectMember><bldg:Building gml:id="b-1">
  <uro:buildingIDAttribute><uro:BuildingIDAttribute>
   <uro:buildingID>13101-bldg-1</uro:buildingID><uro:city>13101</uro:city>
  </uro:BuildingIDAttribute></uro:buildingIDAttribute>
  <uro:buildingDetailAttribute><uro:BuildingDetailAttribute>
   <uro:detailedUsage codeSpace="../../codelists/BuildingDetailAttribute_detailedUsage.xml">1210</uro:detailedUsage>
   <uro:surveyYear>2023</uro:surveyYear>
  </uro:BuildingDetailAttribute></uro:buildingDetailAttribute>
  <uro:buildingDisasterRiskAttribute><uro:BuildingRiverFloodingRiskAttribute>
   <uro:description codeSpace="../../codelists/old.xml">10</uro:description>
   <uro:rank>1</uro:rank><uro:adminType>2</uro:adminType><uro:scale>2</uro:scale>
  </uro:BuildingRiverFloodingRiskAttribute></uro:buildingDisasterRiskAttribute>
  <uro:buildingDataQualityAttribute><uro:BuildingDataQualityAttribute>
   <uro:srcScale codeSpace="../../codelists/src.xml">1</uro:srcScale>
   <uro:geometrySrcDesc>5</uro:geometrySrcDesc><uro:lodType>2.2</uro:lodType>
  </uro:BuildingDataQualityAttribute></uro:buildingDataQualityAttribute>
  <uro:keyValuePairAttribute><uro:KeyValuePairAttribute>
   <uro:key>106</uro:key><uro:codeValue>10</uro:codeValue>
  </uro:KeyValuePairAttribute></uro:keyValuePairAttribute>
 </bldg:Building></core:cityObjectMember>
</core:CityModel>
"""

TARGET = """<?xml version="1.0"?>
<CityModel xmlns="http://www.opengis.net/citygml/3.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/3.0"
 xmlns:app="http://www.opengis.net/citygml/appearance/3.0"
 xmlns:gml="http://www.opengis.net/gml/3.2"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <appearanceMember><app:Appearance gml:id="ID_random"><app:theme>rgbTexture</app:theme></app:Appearance></appearanceMember>
 <cityObjectMember><bldg:Building gml:id="b-1"><boundary/></bldg:Building></cityObjectMember>
</CityModel>
"""


class ConverterTest(unittest.TestCase):
    def write(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_restores_supported_ade_without_losing_legacy_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(root, "source.gml", SOURCE)
            target = self.write(root, "target.gml", TARGET)
            output = root / "output.gml"
            manifest = MODULE.restore_ade(source, target, output)

            tree = etree.parse(str(output))
            ns = {
                "core": MODULE.CORE3,
                "bldg": MODULE.BLDG3,
                "uro": MODULE.URO4,
                "urc": MODULE.URC4,
                "i4dur": MODULE.DEFAULT_I4DUR_NS,
            }
            self.assertEqual(
                tree.xpath("string(//uro:BuildingDetailAttribute/uro:surveyYear)", namespaces=ns),
                "2023-01-01",
            )
            self.assertEqual(
                tree.xpath("count(//i4dur:SurveyYearEncoding)", namespaces=ns), 1.0
            )
            self.assertEqual(tree.xpath("count(//urc:KeyValuePairAttribute)", namespaces=ns), 5.0)
            self.assertEqual(tree.xpath("count(//urc:RiverFloodingRiskAttribute)", namespaces=ns), 1.0)
            self.assertEqual(tree.xpath("count(//uro:BuildingIDAttribute)", namespaces=ns), 1.0)
            self.assertEqual(manifest["invariants"]["gml_id_set_equal"], True)
            self.assertEqual(manifest["invariants"]["generated_appearance_ids_stabilized"], 1)
            self.assertEqual(manifest["rules"]["i4d-required.surveyYear"], 1)
            self.assertNotIn("https://www.geospatial.jp/iur/uro/3.0", output.read_text())

    def test_unknown_iur_wrapper_fails_closed(self):
        broken = SOURCE.replace(
            "</bldg:Building>",
            "<uro:notSupported><uro:Anything/></uro:notSupported></bldg:Building>",
        )
        with tempfile.TemporaryDirectory() as directory:
            source = self.write(Path(directory), "source.gml", broken)
            with self.assertRaisesRegex(MODULE.ConversionError, "unsupported"):
                MODULE.read_source_records(source)

    def test_used_urf_element_fails_closed(self):
        broken = SOURCE.replace(
            "</core:CityModel>",
            '<urf:UrbanFunction xmlns:urf="https://www.geospatial.jp/iur/urf/3.0"/>'
            "</core:CityModel>",
        )
        with tempfile.TemporaryDirectory() as directory:
            source = self.write(Path(directory), "source.gml", broken)
            with self.assertRaisesRegex(MODULE.ConversionError, "used i-UR 3 QName"):
                MODULE.read_source_records(source)

    def test_target_id_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(root, "source.gml", SOURCE)
            target = self.write(root, "target.gml", TARGET.replace('gml:id="b-1"', 'gml:id="b-2"'))
            with self.assertRaisesRegex(MODULE.ConversionError, "sets differ"):
                MODULE.restore_ade(source, target, root / "output.gml")

    def test_gYear_timezone_is_preserved(self):
        self.assertEqual(MODULE._gYear_to_date("2023Z"), "2023-01-01Z")
        self.assertEqual(MODULE._gYear_to_date("2023+09:00"), "2023-01-01+09:00")

    def test_generated_appearance_id_does_not_depend_on_random_input_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(root, "source.gml", SOURCE)
            first = self.write(root, "first.gml", TARGET.replace("ID_random", "ID_first"))
            second = self.write(root, "second.gml", TARGET.replace("ID_random", "ID_second"))
            out_first, out_second = root / "out-first.gml", root / "out-second.gml"
            MODULE.restore_ade(source, first, out_first)
            MODULE.restore_ade(source, second, out_second)
            self.assertEqual(MODULE.sha256(out_first), MODULE.sha256(out_second))

    def test_provisional_schema_and_compatibility_dictionary_cover_converter(self):
        schema_root = Path(__file__).parents[1] / "schemas/i4dur/1.0"
        schema = etree.parse(str(schema_root / "i4dUR.xsd"))
        self.assertEqual(schema.getroot().get("targetNamespace"), MODULE.DEFAULT_I4DUR_NS)
        dictionary = etree.parse(
            str(schema_root / "codelists/LegacyIUR3Attribute_key.xml")
        )
        names = set(
            dictionary.xpath(
                "//gml:Definition/gml:name/text()",
                namespaces={"gml": MODULE.GML31},
            )
        )
        expected = {
            f"iur3.uro.BuildingDetailAttribute.{name}"
            for name in MODULE.LEGACY_DETAIL_CODES
        } | {
            f"iur3.uro.BuildingDataQualityAttribute.{name}"
            for name in MODULE.DATA_QUALITY_CHILDREN
        }
        self.assertEqual(names, expected)


if __name__ == "__main__":
    unittest.main()
