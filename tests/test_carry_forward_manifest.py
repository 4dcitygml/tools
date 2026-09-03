# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""carry-forward: three-way classification per building and semantic key across
an edition change (reapply / absorbed / conflict / unmappable / insert-needed),
including a container rename and a split attribute between i-UR 3.0 and 3.1."""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from scripts import carry_forward_manifest as C
from scripts.provenance_manifest import validate

NS30 = "https://www.geospatial.jp/iur/uro/3.0"
NS31 = "https://www.geospatial.jp/iur/uro/3.1"


def gml(ns: str, buildings: list[tuple[str, str]]) -> bytes:
    members = "".join(
        f'<core:cityObjectMember><bldg:Building gml:id="g{i}">'
        f'<uro:buildingIDAttribute><uro:BuildingIDAttribute><uro:buildingID>{stable}</uro:buildingID><uro:city>13101</uro:city></uro:BuildingIDAttribute></uro:buildingIDAttribute>'
        f'{body}</bldg:Building></core:cityObjectMember>' for i, (stable, body) in enumerate(buildings))
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            f'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml" xmlns:uro="{ns}">'
            + members + "</core:CityModel>\n").encode()


def q30(geom: str, them: str) -> str:
    return (f"<uro:buildingDataQualityAttribute><uro:BuildingDataQualityAttribute><uro:geometrySrcDesc>{geom}</uro:geometrySrcDesc>"
            f"<uro:thematicSrcDesc>{them}</uro:thematicSrcDesc></uro:BuildingDataQualityAttribute></uro:buildingDataQualityAttribute>")


def q31(lod0: str, lod1: str, them: str) -> str:
    return (f"<uro:bldgDataQualityAttribute><uro:DataQualityAttribute><uro:geometrySrcDescLod0>{lod0}</uro:geometrySrcDescLod0>"
            f"<uro:geometrySrcDescLod1>{lod1}</uro:geometrySrcDescLod1><uro:thematicSrcDesc>{them}</uro:thematicSrcDesc>"
            f"</uro:DataQualityAttribute></uro:bldgDataQualityAttribute>")


def st(v: str) -> str:
    return f"<bldg:storeysAboveGround>{v}</bldg:storeysAboveGround>"


class CarryForwardTest(unittest.TestCase):
    def _run(self, base: bytes, current: bytes, new: bytes):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "base.gml").write_bytes(base); (t / "current.gml").write_bytes(current); (t / "new.gml").write_bytes(new)
            args = argparse.Namespace(repository="example/13101-example", mesh="53394611", municipality="13101",
                                      base=str(t / "base.gml"), current=str(t / "current.gml"), new=str(t / "new.gml"),
                                      edition_from=None, edition_to=None, base_uri=None, current_uri=None, new_uri=None,
                                      product="udx/bldg/tile.gml", tools_repo="4dcitygml/tools", tools_commit="0" * 40,
                                      plan_issue="https://example.com/issues/4", seed=1, sample_size=30)
            return C.build_manifest(args)

    def test_four_way_classification_across_editions(self) -> None:
        base = gml(NS30, [("13101-bldg-1", st("9999") + q30("01", "02")), ("13101-bldg-2", st("3")), ("13101-bldg-3", st("5")),
                          ("13101-bldg-4", st("2") + q30("01", "02")), ("13101-bldg-5", st("7"))])
        current = gml(NS30, [("13101-bldg-1", st("4") + q30("01", "02")),    # local: storeys 9999 -> 4 ; official keeps 9999 -> reapply
                             ("13101-bldg-2", st("6")),                        # local: 3 -> 6 ; official also 6 -> absorbed
                             ("13101-bldg-3", st("8")),                        # local: 5 -> 8 ; official 9 -> conflict
                             ("13101-bldg-4", st("2") + q30("77", "88")),      # local: geometrySrcDesc (split in 3.1) + thematicSrcDesc (renamed)
                             ("13101-bldg-5", st("1"))])                       # local, but the new building has no storeys leaf -> insert-needed
        new = gml(NS31, [("13101-bldg-1", st("9999") + q31("01", "01", "02")), ("13101-bldg-2", st("6")), ("13101-bldg-3", st("9")),
                         ("13101-bldg-4", st("2") + q31("01", "01", "02")), ("13101-bldg-5", ""), ("13101-bldg-6", st("1"))])
        manifest, product = self._run(base, current, new)
        ev = manifest["evidence"]
        self.assertEqual(validate(manifest), [])
        self.assertEqual((ev["edition_from"], ev["edition_to"]), ("iur-3.0", "iur-3.1"))
        reapplied = {(c["id"], c["path"], c["new"], bool(c.get("carried"))) for c in ev["changes"]}
        self.assertEqual(reapplied, {
            ("13101-bldg-1", "/storeysAboveGround", "4", False),
            # coded values across an edition change: "77"/"88" are not in the 3.0->3.1 crosswalk,
            # so they are carried with the 3.0 codeSpace (split: one value to each LoD present; renamed container)
            ("13101-bldg-4", "/bldgDataQualityAttribute/DataQualityAttribute/geometrySrcDescLod0", "77", True),
            ("13101-bldg-4", "/bldgDataQualityAttribute/DataQualityAttribute/geometrySrcDescLod1", "77", True),
            ("13101-bldg-4", "/bldgDataQualityAttribute/DataQualityAttribute/thematicSrcDesc", "88", True),
        })
        self.assertEqual(len(ev["carried_old_codespace"]), 3)
        self.assertEqual(ev["summary"]["carried_old_codespace"], 3)
        self.assertEqual([a["id"] for a in ev["absorbed"]], ["13101-bldg-2"])
        self.assertEqual([(c["id"], c["new"]) for c in ev["conflicts"]], [("13101-bldg-3", "9")])
        self.assertEqual([i["id"] for i in ev["insert_needed"]], ["13101-bldg-5"])
        self.assertEqual(ev["lifecycle"]["only_new"], ["13101-bldg-6"])
        self.assertIn(b"<bldg:storeysAboveGround>4</bldg:storeysAboveGround>", product)
        self.assertIn(b'<uro:geometrySrcDescLod1 codeSpace="../../codelists/iur-3.0/BuildingDataQualityAttribute_geometrySrcDesc.xml">77</uro:geometrySrcDescLod1>', product)
        self.assertIn(b'<uro:thematicSrcDesc codeSpace="../../codelists/iur-3.0/BuildingDataQualityAttribute_thematicSrcDesc.xml">88</uro:thematicSrcDesc>', product)
        self.assertIn(b"<bldg:storeysAboveGround>9</bldg:storeysAboveGround>", product)  # conflict left as official

    def test_unmappable_key_is_held(self) -> None:
        # a 3.1-only attribute carried onto a 3.0 file (downgrade) has no path -> unmappable
        base = gml(NS31, [("13101-bldg-1", q31("01", "01", "02"))])
        current = gml(NS31, [("13101-bldg-1", q31("55", "01", "02"))])
        new = gml(NS30, [("13101-bldg-1", q30("01", "02"))])
        manifest, _product = self._run(base, current, new)
        self.assertEqual([u["key"] for u in manifest["evidence"]["unmappable"]], ["quality.geometrySrcDesc.lod0"])
        self.assertEqual(manifest["evidence"]["changes"], [])


    def test_exact_crosswalk_maps_the_code_instead_of_carrying_it(self) -> None:
        # 3.0 thematicSrcDesc "1" (都市計画基礎調査) is exact -> 3.1 "201"; "6" (写真判読) -> "802".
        # base 6, current 1 (our change), official 802 (= old 6 mapped) -> reapply as 201 without codeSpace
        base = gml(NS30, [("13101-bldg-1", q30("2", "6"))])
        current = gml(NS30, [("13101-bldg-1", q30("2", "1"))])
        new = gml(NS31, [("13101-bldg-1", q31("108", "108", "802"))])
        manifest, product = self._run(base, current, new)
        ev = manifest["evidence"]
        self.assertEqual([(c["path"], c["new"], c.get("carried", False)) for c in ev["changes"]],
                         [("/bldgDataQualityAttribute/DataQualityAttribute/thematicSrcDesc", "201", False)])
        self.assertIn(b"<uro:thematicSrcDesc>201</uro:thematicSrcDesc>", product)
        self.assertEqual(ev["carried_old_codespace"], [])
        self.assertEqual(ev["conflicts"], [])


if __name__ == "__main__":
    unittest.main()
