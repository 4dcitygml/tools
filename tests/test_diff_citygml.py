#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for diff_citygml (W1 / feature list A).

Clean-room tests written from the functional requirements (feature list A), not a copy of the old implementation.
Run: python -m unittest tests.test_diff_citygml
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.diff_citygml import (
    _extract_attributes,
    geometry_hash,
    _norm_num,
    diff_files,
    diff_sources,
)
from scripts.safe_xml import fromstring

FIXTURES = Path(__file__).resolve().parent / "fixtures"

NS = (
    'xmlns:core="http://www.opengis.net/citygml/2.0" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
    'xmlns:gen="http://www.opengis.net/citygml/generics/2.0" '
    'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" '
    'xmlns:uro="https://www.geospatial.jp/iur/uro/3.0" '
    'xmlns:gml="http://www.opengis.net/gml"'
)


def _building(bid: str, *, height: str = "10.0", storeys: str = "3",
              poslist: str = "0 0 0 1 0 0 1 1 0 0 0 0", extra: str = "") -> str:
    """Return a minimal bldg:Building for tests (attributes + LOD1 geometry)."""
    return f"""
    <core:cityObjectMember>
      <bldg:Building gml:id="{bid}">
        <bldg:measuredHeight uom="m">{height}</bldg:measuredHeight>
        <bldg:storeysAboveGround>{storeys}</bldg:storeysAboveGround>
        {extra}
        <bldg:lod1Solid>
          <gml:Solid>
            <gml:exterior><gml:CompositeSurface><gml:surfaceMember>
              <gml:Polygon><gml:exterior><gml:LinearRing>
                <gml:posList>{poslist}</gml:posList>
              </gml:LinearRing></gml:exterior></gml:Polygon>
            </gml:surfaceMember></gml:CompositeSurface></gml:exterior>
          </gml:Solid>
        </bldg:lod1Solid>
      </bldg:Building>
    </core:cityObjectMember>"""


def _model(*members: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel {NS}>{"".join(members)}</core:CityModel>'


class _TmpMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def write(self, name: str, content: str) -> Path:
        p = self.dir / name
        p.write_text(content, encoding="utf-8")
        return p

    def parse_building(self, xml: str):
        root = fromstring(_model(xml).encode("utf-8"))
        return root.find(".//{http://www.opengis.net/citygml/building/2.0}Building")


class TestClassification(_TmpMixin):
    """A-5 / A-6: change classification and bulk diff."""

    def test_attribute_only_modified(self) -> None:
        old = self.write("o.gml", _model(_building("b1", storeys="9999")))
        new = self.write("n.gml", _model(_building("b1", storeys="4")))
        r = diff_files(old, new)
        self.assertEqual(r["summary"], {"added": 0, "deleted": 0, "modified": 1, "unchanged": 0, "renamed": 0})
        (b,) = r["buildings"]
        self.assertEqual(b["status"], "modified")
        self.assertFalse(b["geometry_changed"])
        self.assertEqual(b["attribute_diffs"],
                         [{"path": "/storeysAboveGround", "old": "9999", "new": "4"}])

    def test_geometry_only_modified(self) -> None:
        old = self.write("o.gml", _model(_building("b1")))
        new = self.write("n.gml", _model(_building("b1", poslist="0 0 0 1 0 0 1 1 5 0 0 0")))
        r = diff_files(old, new)
        (b,) = r["buildings"]
        self.assertEqual(b["status"], "modified")
        self.assertTrue(b["geometry_changed"])
        self.assertEqual(b["attribute_diffs"], [])

    def test_added_and_deleted(self) -> None:
        # gone and fresh are "different buildings" (different geometry) = add/delete, not rename.
        old = self.write("o.gml", _model(
            _building("keep"), _building("gone", poslist="5 5 5 6 5 5 6 6 5 5 5 5")))
        new = self.write("n.gml", _model(
            _building("keep"), _building("fresh", poslist="9 9 9 8 9 9 8 8 9 9 9 9")))
        r = diff_files(old, new)
        self.assertEqual(r["summary"]["added"], 1)
        self.assertEqual(r["summary"]["deleted"], 1)
        self.assertEqual(r["summary"]["renamed"], 0)
        by = {b["id"]: b["status"] for b in r["buildings"]}
        self.assertEqual(by["fresh"], "added")
        self.assertEqual(by["gone"], "deleted")
        self.assertNotIn("keep", by)  # unchanged is excluded by default

    def test_rename_detected_on_id_only_change(self) -> None:
        # Identical content with only gml:id changed → rename (id-only, no content change).
        old = self.write("o.gml", _model(_building("keep"), _building("old_id", storeys="7")))
        new = self.write("n.gml", _model(_building("keep"), _building("new_id", storeys="7")))
        r = diff_files(old, new)
        self.assertEqual(r["summary"]["renamed"], 1)
        self.assertEqual(r["summary"]["added"], 0)
        self.assertEqual(r["summary"]["deleted"], 0)
        by = {b["id"]: b for b in r["buildings"]}
        self.assertEqual(by["new_id"]["status"], "renamed")
        self.assertEqual(by["new_id"]["old_id"], "old_id")

    def test_rebuild_not_treated_as_rename(self) -> None:
        # A rebuild (both id and geometry change) has different content, so add/delete, not rename.
        old = self.write("o.gml", _model(_building("old_id", poslist="0 0 0 1 0 0 1 1 0 0 0 0")))
        new = self.write("n.gml", _model(_building("new_id", poslist="0 0 0 1 0 5 1 1 5 0 0 0")))
        r = diff_files(old, new)
        self.assertEqual(r["summary"]["renamed"], 0)
        self.assertEqual(r["summary"]["added"], 1)
        self.assertEqual(r["summary"]["deleted"], 1)

    def test_unchanged_hidden_unless_requested(self) -> None:
        old = self.write("o.gml", _model(_building("b1")))
        new = self.write("n.gml", _model(_building("b1")))
        self.assertEqual(diff_files(old, new)["buildings"], [])
        r = diff_files(old, new, include_unchanged=True)
        self.assertEqual(r["buildings"][0]["status"], "unchanged")

    def test_single_id_filter(self) -> None:
        old = self.write("o.gml", _model(_building("a", storeys="1"), _building("b", storeys="1")))
        new = self.write("n.gml", _model(_building("a", storeys="2"), _building("b", storeys="2")))
        r = diff_files(old, new, only_id="b")
        self.assertEqual(len(r["buildings"]), 1)
        self.assertEqual(r["buildings"][0]["id"], "b")


class TestNumericNormalization(_TmpMixin):
    """A-4: numeric normalization of attribute values / A-3: numeric normalization of geometry."""

    def test_norm_num(self) -> None:
        self.assertEqual(_norm_num("13.80"), _norm_num("13.8"))
        self.assertEqual(_norm_num("9"), _norm_num("9.0"))
        self.assertEqual(_norm_num("abc"), "abc")  # non-numeric values pass through unchanged

    def test_attr_trailing_zero_is_not_a_change(self) -> None:
        old = self.write("o.gml", _model(_building("b1", height="13.8")))
        new = self.write("n.gml", _model(_building("b1", height="13.80")))
        self.assertEqual(diff_files(old, new)["buildings"], [])  # no change

    def test_geometry_trailing_zero_is_not_a_change(self) -> None:
        old = self.write("o.gml", _model(_building("b1", poslist="0 0 0 1 0 0 1 1 6 0 0 0")))
        new = self.write("n.gml", _model(_building("b1", poslist="0 0 0 1 0 0 1 1 6.00 0 0 0")))
        self.assertEqual(diff_files(old, new)["buildings"], [])


class TestGeometryHash(_TmpMixin):
    """A-3: geometry hash (set-based, order-independent)."""

    def test_hash_changes_on_real_change(self) -> None:
        a = self.parse_building(_building("b", poslist="0 0 0 1 0 0"))
        b = self.parse_building(_building("b", poslist="0 0 0 1 0 9"))
        self.assertNotEqual(geometry_hash(a), geometry_hash(b))

    def test_hash_is_face_order_independent(self) -> None:
        # Two buildings differing only in face order hash identically (set comparison)
        two_faces = ('<bldg:lod2MultiSurface><gml:MultiSurface>'
                     '<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>'
                     '<gml:posList>{a}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
                     '<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>'
                     '<gml:posList>{b}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
                     '</gml:MultiSurface></bldg:lod2MultiSurface>')
        f1 = "1 1 1 2 2 2"
        f2 = "3 3 3 4 4 4"
        e1 = self.parse_building(_building("b", extra=two_faces.format(a=f1, b=f2)))
        e2 = self.parse_building(_building("b", extra=two_faces.format(a=f2, b=f1)))
        self.assertEqual(geometry_hash(e1), geometry_hash(e2))

    def test_no_geometry_returns_none(self) -> None:
        root = fromstring(_model(
            '<core:cityObjectMember><bldg:Building gml:id="x">'
            '<bldg:measuredHeight>5</bldg:measuredHeight></bldg:Building></core:cityObjectMember>'
        ).encode("utf-8"))
        b = root.find(".//{http://www.opengis.net/citygml/building/2.0}Building")
        self.assertIsNone(geometry_hash(b))


class TestAttributeExtraction(_TmpMixin):
    """A-2: attribute extraction (geometry/appearance exclusion, paths, duplicates, name attribute)."""

    def test_geometry_and_appearance_excluded(self) -> None:
        extra = ('<app:appearance><app:Appearance><app:theme>rgbTexture</app:theme>'
                 '</app:Appearance></app:appearance>')
        attrs = _extract_attributes(self.parse_building(_building("b", extra=extra)))
        # Neither geometry (posList) nor appearance (theme) appears in attributes
        self.assertNotIn("/lod1Solid/Solid/exterior/CompositeSurface/surfaceMember/Polygon/exterior/LinearRing/posList", attrs)
        self.assertFalse(any("posList" in k for k in attrs))
        self.assertFalse(any("theme" in k or "appearance" in k for k in attrs))
        # Regular attributes are extracted
        self.assertEqual(attrs["/measuredHeight"], "10.0")
        self.assertEqual(attrs["/storeysAboveGround"], "3")

    def test_appearance_of_every_version_is_excluded(self) -> None:
        # D-4c1: a CityGML 1.0 building's local appearance was read as attributes
        b = fromstring(b'<bldg:Building xmlns:bldg="http://www.opengis.net/citygml/building/1.0" '
                       b'xmlns:app="http://www.opengis.net/citygml/appearance/1.0"><bldg:measuredHeight>9</bldg:measuredHeight>'
                       b'<app:appearance><app:Appearance><app:theme>rgb</app:theme></app:Appearance></app:appearance>'
                       b'</bldg:Building>')
        self.assertEqual(_extract_attributes(b), {"/measuredHeight": "9"})

    def test_repeated_path_indexing(self) -> None:
        extra = ("<uro:thematicSrcDesc>000</uro:thematicSrcDesc>"
                 "<uro:thematicSrcDesc>023</uro:thematicSrcDesc>")
        attrs = _extract_attributes(self.parse_building(_building("b", extra=extra)))
        self.assertEqual(attrs["/thematicSrcDesc[0]"], "000")
        self.assertEqual(attrs["/thematicSrcDesc[1]"], "023")

    def test_name_attribute_disambiguates(self) -> None:
        extra = ('<gen:stringAttribute name="用途"><gen:value>402</gen:value></gen:stringAttribute>'
                 '<gen:stringAttribute name="構造"><gen:value>1</gen:value></gen:stringAttribute>')
        attrs = _extract_attributes(self.parse_building(_building("b", extra=extra)))
        self.assertEqual(attrs["/stringAttribute[@name=用途]/value"], "402")
        self.assertEqual(attrs["/stringAttribute[@name=構造]/value"], "1")


class TestSources(unittest.TestCase):
    """diff_sources: bytes input and None (file added/deleted) — the general form used by CI."""

    def test_bytes_sources_modified(self) -> None:
        old = _model(_building("b1", storeys="9999")).encode("utf-8")
        new = _model(_building("b1", storeys="4")).encode("utf-8")
        r = diff_sources(old, new, "f.gml", "f.gml")
        (b,) = r["buildings"]
        self.assertEqual(b["status"], "modified")
        self.assertEqual(r["new"], "f.gml")

    def test_none_old_means_file_added(self) -> None:
        new = _model(_building("b1"), _building("b2")).encode("utf-8")
        r = diff_sources(None, new, "new.gml", "new.gml")
        self.assertEqual(r["summary"]["added"], 2)
        self.assertTrue(all(b["status"] == "added" for b in r["buildings"]))

    def test_none_new_means_file_deleted(self) -> None:
        old = _model(_building("b1")).encode("utf-8")
        r = diff_sources(old, None, "gone.gml", "gone.gml")
        self.assertEqual(r["summary"]["deleted"], 1)


class TestRealFixtures(unittest.TestCase):
    """PR-A / PR-B against fixtures derived from real data (one PLATEAU building)."""

    def test_prA_attribute_only(self) -> None:
        r = diff_files(FIXTURES / "base.gml", FIXTURES / "prA_attr.gml")
        (b,) = r["buildings"]
        self.assertEqual(b["status"], "modified")
        self.assertFalse(b["geometry_changed"])
        self.assertEqual(b["attribute_diffs"],
                         [{"path": "/storeysAboveGround", "old": "9999", "new": "4"}])

    def test_prB_geometry_change(self) -> None:
        r = diff_files(FIXTURES / "base.gml", FIXTURES / "prB_geom.gml")
        (b,) = r["buildings"]
        self.assertEqual(b["status"], "modified")
        self.assertTrue(b["geometry_changed"])
        # The height attribute has also changed consistently
        paths = {d["path"] for d in b["attribute_diffs"]}
        self.assertIn("/measuredHeight", paths)



class TestOneRenameRule(unittest.TestCase):
    """U6 / D-c1 (decided): one rename rule for every comment and gate: equal content after numeric
    normalisation, and only unambiguous 1:1 pairs. Before, diff_citygml compared raw values (a
    13.80 -> 13.8 id change read as a deletion and an addition) and reviewability_lint paired
    greedily (two identical buildings re-numbered read as renames)."""

    def test_numeric_normalised_and_one_to_one(self) -> None:
        from scripts.diff_citygml import load_buildings
        from scripts.reviewability_lint import analyze_file
        old = _model(_building("a1", height="13.80")).encode()
        new = _model(_building("a2", height="13.8")).encode()
        result = diff_sources(old, new, "o", "n")
        self.assertEqual([(b["status"], b.get("old_id")) for b in result["buildings"]], [("renamed", "a1")])
        lint = analyze_file(load_buildings(old), load_buildings(new), "t.gml")
        self.assertEqual([(w["type"], w.get("old_id")) for w in lint["warnings"]], [("id_change", "a1")])
        twins_old = _model(_building("x1"), _building("x2")).encode()
        twins_new = _model(_building("y1"), _building("y2")).encode()
        self.assertEqual(diff_sources(twins_old, twins_new, "o", "n")["summary"]["renamed"], 0)
        lint = analyze_file(load_buildings(twins_old), load_buildings(twins_new), "t.gml")
        self.assertEqual(sorted(w["type"] for w in lint["warnings"]), ["lifecycle_added"] * 2 + ["lifecycle_deleted"] * 2)


class TestOnlyGeometryIsExcluded(unittest.TestCase):
    """D-c9 (decided, dialect-neutral): only geometry is left out of the comparison. Before, every
    gml element was (gml:name and gml:description changes read as unchanged), and an attribute's
    uom or codeSpace was never read."""

    def status(self, old_extra: str, new_extra: str, **kw) -> list:
        old = _model(_building("b", extra=old_extra, **kw.get("old", {}))).encode()
        new = _model(_building("b", extra=new_extra, **kw.get("new", {}))).encode()
        return [(e["status"], [d["path"] for d in e.get("attribute_diffs", [])]) for e in diff_sources(old, new, "o", "n")["buildings"]]

    def test_name_description_uom_and_codespace_count(self) -> None:
        self.assertEqual(self.status("<gml:name>Old hall</gml:name>", "<gml:name>New hall</gml:name>"),
                         [("modified", ["/name"])])
        self.assertEqual(self.status("<gml:description>a</gml:description>", "<gml:description>b</gml:description>"),
                         [("modified", ["/description"])])
        self.assertEqual(self.status('<bldg:usage codeSpace="a.xml">1</bldg:usage>', '<bldg:usage codeSpace="b.xml">1</bldg:usage>'),
                         [("modified", ["/usage/@codeSpace"])])
        uom = self.status("", "", new={"height": "10.0"})
        self.assertEqual(uom, [])                                    # same value, same uom
        old = _model(_building("b")).encode()
        new = old.replace(b'uom="m"', b'uom="ft"')
        self.assertEqual([d["path"] for d in diff_sources(old, new, "o", "n")["buildings"][0]["attribute_diffs"]],
                         ["/measuredHeight/@uom"])
        geometry = _model(_building("b")).encode().replace(b"<gml:Solid>", b'<gml:Solid gml:id="s1">')
        self.assertEqual(diff_sources(_model(_building("b")).encode(), geometry, "o", "n")["buildings"], [])

    def test_the_minimal_diff_rebuilds_such_a_change(self) -> None:
        from scripts.reconstruct_minimal import reconstruct
        old = _model(_building("b")).encode()
        new = old.replace(b'uom="m"', b'uom="ft"')
        result = reconstruct(old, new)
        self.assertEqual(diff_sources(new, result.output, "h", "r")["buildings"], [])


class TestOneParsePerRevision(unittest.TestCase):
    """U6: the gates parsed each changed file's two revisions again and again (about 15 times a
    PR); in a run, buildings_at parses a blob once and the others read the result."""

    def test_a_blob_is_parsed_once_per_run(self) -> None:
        import os
        from unittest.mock import patch
        from scripts import diff_citygml
        from tests.test_commit_building_scope import GitRepo
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as run:
            repo = GitRepo(Path(tmp))
            sha = repo.commit_text(_model(_building("b1", height="12.5")), "one")
            self.assertEqual(diff_citygml.buildings_at(repo.root, sha, "missing.gml"), {})
            with patch.dict(os.environ, {"CITYGML_RUN": run}):
                first = diff_citygml.buildings_at(repo.root, sha, "tile.gml")
                with patch.object(diff_citygml, "_load", side_effect=AssertionError("parsed twice")):
                    self.assertEqual(diff_citygml.buildings_at(repo.root, sha, "tile.gml"), first)
                self.assertEqual(len(list((Path(run) / "buildings").iterdir())), 1)
            self.assertEqual(first["b1"][0]["/measuredHeight"], "12.5")
            with patch.dict(os.environ, {"CITYGML_RUN": ""}):   # outside a run nothing is written
                self.assertEqual(diff_citygml.buildings_at(repo.root, sha, "tile.gml"), first)

if __name__ == "__main__":
    unittest.main()
