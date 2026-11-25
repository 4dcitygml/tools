#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The pack interface check (v2.0.0 stage 5b, REQ-dialect-packs section 8): the ALKIS skeleton
(tests/alkis_skeleton.py) run through the generic paths on Munich-shaped data. Every answer goes
through the interface; a failure here is a generic-code assumption, not a skeleton gap."""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dialects
from scripts import building_compare as A, building_identity as BI, identity_manifest, provenance_manifest
from scripts.commit_building_scope import inspect_range
from tests import alkis_skeleton

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alkis"
TILE = FIXTURES / "690_5335_1.gml"
RULE = BI.IdentityRule("core:externalReference")
_dialect = dialects.dialect


@contextlib.contextmanager
def alkis():
    """The skeleton as the pack a declaration's `"dialect": "alkis"` names (test only)."""
    with mock.patch.object(dialects, "dialect", lambda name: alkis_skeleton.PACK if name == "alkis" else _dialect(name)):
        yield


class City:
    """A city repository with the fixture tile under lod2_citygml/ and an ALKIS declaration."""

    def __init__(self, case: unittest.TestCase):
        self.root = Path(tempfile.mkdtemp())
        case.addCleanup(shutil.rmtree, self.root)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.root / "4dcitygml.json").write_text(json.dumps(
            {"repo": "o/r", "lang": "de", "dialect": "alkis", "data_dirs": ["lod2_citygml"]}), encoding="utf-8")
        (self.root / "lod2_citygml").mkdir()
        shutil.copyfile(TILE, self.root / "lod2_citygml" / TILE.name)
        self.base = self.commit("Declare the city with its data")

    def git(self, *args) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class TestTheInterface(unittest.TestCase):
    """5b-2: one documented Pack, and every pack answers every field explicitly."""

    def test_every_field_is_documented(self):
        import dataclasses
        import re
        documented = set(re.findall(r"^    ([a-z_0-9]+) {2,}\S", dialects.Pack.__doc__, re.M))
        self.assertEqual({f.name for f in dataclasses.fields(dialects.Pack)} - documented, set())

    def test_both_packs_answer_every_field(self):
        import ast
        import dataclasses
        fields = {f.name for f in dataclasses.fields(dialects.Pack)}
        for path in (Path(dialects.__file__).parent / "plateau" / "pack.py", Path(alkis_skeleton.__file__)):
            call = next(n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Pack")
            self.assertEqual(fields ^ {k.arg for k in call.keywords}, set(), path.name)


class TestIdentity(unittest.TestCase):
    def test_the_alkis_identifier_is_the_stable_id(self):
        raw = TILE.read_bytes()
        spans = BI.building_spans(raw)
        found = {BI.stable_id(raw[s:e], gid, RULE) for gid, (s, e) in spans.items()}
        self.assertEqual(found, {"DEBYvAAAAAAA0001", "DEBYvAAAAAAA0002"})

    def test_the_identifier_is_replaced_in_place(self):
        raw = TILE.read_bytes()
        s, e = BI.building_spans(raw)["DEBY_LOD2_1001"]
        changed = BI.replace_stable_id(raw[s:e], "DEBYvAAAAAAA0001", "DEBYvAAAAAAA0009", RULE)
        self.assertEqual(BI.stable_id(changed, "DEBY_LOD2_1001", RULE), "DEBYvAAAAAAA0009")
        self.assertEqual(len(changed), e - s)

    def test_the_manifests_read_the_same_identifier(self):
        with alkis(), mock.patch.object(A.city_declaration, "read", lambda *_a: A.city_declaration.Declaration({"dialect": "alkis"})):
            edition = identity_manifest.load_edition(TILE, "09162000", RULE)
        self.assertEqual(sorted(edition), ["DEBYvAAAAAAA0001", "DEBYvAAAAAAA0002"])

    def test_free_text_names_the_building(self):
        from tests.support import load_app
        review = load_app("hub_skeleton_app", "tools/hub/app.py").review
        self.assertEqual(review.extract_building_id(alkis_skeleton.PACK.building_id_text, "Dachhoehe von DEBYvAAAAAAA0002"),
                         "DEBYvAAAAAAA0002")


class TestComparison(unittest.TestCase):
    def test_the_municipality_is_the_ags(self):
        buildings = A.load_buildings(TILE, pack=alkis_skeleton.PACK)
        self.assertEqual({b.city_code for b in buildings.values()}, {"09162000"})

    def test_without_lod0_the_footprint_is_the_ground_surface(self):
        buildings = A.load_buildings(TILE, pack=alkis_skeleton.PACK)
        self.assertEqual(sorted(round(b.geom.area) for b in buildings.values()), [96, 96])   # 12 m x 8 m each


    def test_a_bulk_manifest_takes_the_citys_tile_and_municipality(self):
        scope = json.loads(provenance_manifest.SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["scope"]["properties"]
        self.assertRegex("690_5335_1", f"^(?:{scope['mesh']['pattern']})$")
        self.assertRegex("09162000", f"^(?:{scope['municipality']['pattern']})$")


class TestTiles(unittest.TestCase):
    def test_tiles_and_their_frame(self):
        from tools import runtime
        tiles = runtime.tile_files(FIXTURES, alkis_skeleton.PACK)
        self.assertEqual(list(tiles), ["690_5335_1"])
        s, w, n, e = alkis_skeleton.PACK.tile_frame("690_5335_1")
        self.assertTrue(48.0 < s < n < 48.2 and 11.5 < w < e < 11.7, (s, w, n, e))


class TestAnalysis(unittest.TestCase):
    def test_an_attribute_edit_passes_the_commit_scope_gate(self):
        city = City(self)
        tile = city.root / "lod2_citygml" / TILE.name
        tile.write_text(tile.read_text(encoding="utf-8").replace("23.682", "24.100", 1), encoding="utf-8")
        head = city.commit("Correct the measured height\n\nBuilding: DEBYvAAAAAAA0001\n")
        with alkis():
            results = inspect_range(city.root, city.base, head)
        self.assertEqual([r.errors for r in results], [[]])
        self.assertEqual(sorted(results[0].changed_ids), ["DEBYvAAAAAAA0001"])

    def test_a_wrong_building_trailer_is_refused(self):
        city = City(self)
        tile = city.root / "lod2_citygml" / TILE.name
        tile.write_text(tile.read_text(encoding="utf-8").replace("23.682", "24.100", 1), encoding="utf-8")
        head = city.commit("Correct the measured height\n\nBuilding: DEBYvAAAAAAA0002\n")
        with alkis():
            results = inspect_range(city.root, city.base, head)
        self.assertTrue(results[0].errors)


    def test_a_kind_the_pack_does_not_have_is_refused(self):
        # no bulk kinds of its own: a scope extraction is a PLATEAU kind
        city = City(self)
        (city.root / "lod2_citygml" / TILE.name).unlink()
        head = city.commit("Extract the municipality\n\nChange-Type: scope-extract\nScope-Municipality: 09162000\n")
        with alkis():
            results = inspect_range(city.root, city.base, head)
        self.assertIn("comes with a dialect pack", " ".join(results[0].errors))


class TestCodeLists(unittest.TestCase):
    def test_a_value_without_code_space_finds_its_list(self):
        from scripts import data_stats
        records = [v for b in data_stats._iter_buildings(TILE.read_bytes()) for v in data_stats.coded_values(b, alkis_skeleton.PACK)]
        self.assertIn(("function", "Building_function.xml", "31001_1000"), records)


class TestEditor(unittest.TestCase):
    def items(self, city) -> dict:
        from tests.support import load_app
        attr = load_app("attr_skeleton_app", "tools/attr_editor/app.py")
        with alkis():
            groups = attr.Repo(city.root).tile("690_5335_1")["buildings"]["DEBY_LOD2_1001"]["items"]
        return {leaf["tag"]: leaf for group in groups for leaf in group["leaves"]}

    def test_a_carried_list_is_its_editions(self):
        # D-4b4 in the editor: it took the file name alone, so a list carried under codelists/<edition>/
        # was read as the current list of that name. The codeSpace is the path from the tile (the
        # editor reads it as CI does, codelists.resolve; a path that leads nowhere is no list).
        city = City(self)
        data = city.root / "lod2_citygml"
        (data / "codelists" / "2.1").mkdir(parents=True)
        shutil.copyfile(FIXTURES / "codelists" / "Building_function.xml", data / "codelists" / "2.1" / "Building_function.xml")
        shutil.copyfile(FIXTURES / "codelists" / "Building_function.xml", data / "codelists" / "Building_function.xml")
        tile = data / TILE.name
        tile.write_text(tile.read_text(encoding="utf-8").replace(
            "<bldg:function>", '<bldg:function codeSpace="codelists/2.1/Building_function.xml">'), encoding="utf-8")
        self.assertEqual(self.items(city)["function"]["codelist"], "2.1/Building_function.xml")

    def test_the_identifier_is_read_only(self):
        # the leaf of the city's stable ID, by its rule (the editor knew only uro:buildingID)
        city = City(self)
        items = self.items(city)
        self.assertTrue(items["name"]["readonly"])
        self.assertFalse(items["measuredHeight"]["readonly"])
        from tests.support import load_app
        attr = load_app("attr_skeleton_app", "tools/attr_editor/app.py")
        with alkis(), self.assertRaises(ValueError):
            attr.Repo(city.root)._edited_bytes("690_5335_1", "DEBY_LOD2_1001", [
                {"tag": "name", "index": 0, "old": "DEBYvAAAAAAA0001", "new": "DEBYvAAAAAAA0009"}])

    def test_a_value_without_code_space_has_the_packs_list(self):
        city = City(self)
        shutil.copytree(FIXTURES / "codelists", city.root / "lod2_citygml" / "codelists")
        self.assertEqual(self.items(city)["function"]["codelist"], "Building_function.xml")


class TestExport(unittest.TestCase):
    def test_a_plain_export_without_a_hook(self):
        from scripts import export_citygml3 as X
        from tests.test_export_citygml3 import FakeTools
        city = City(self)
        tools = FakeTools()
        with alkis(), mock.patch.object(X.subprocess, "run", tools):
            written = X.export(city.root, city.root / "out", Path("citygml-tools"))
        self.assertEqual(written, [city.root / "out" / "lod2_citygml" / TILE.name])


if __name__ == "__main__":
    unittest.main()
