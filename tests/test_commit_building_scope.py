#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 1 commit = 1 buildingID gate."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.commit_building_scope import inspect_range, main


def _gml(a: int, b: int) -> str:
    return _gml_members([
        ("13101-bldg-1", "13101", a),
        ("13101-bldg-2", "13101", b),
    ])


def _gml_members(members: list[tuple[str, str, int]]) -> str:
    def building(stable: str, municipality: str, value: int) -> str:
        return (
            '<core:cityObjectMember><bldg:Building gml:id="gml-' + stable + '">'
            '<uro:buildingIDAttribute><uro:BuildingIDAttribute>'
            f'<uro:buildingID>{stable}</uro:buildingID>'
            f'<uro:city>{municipality}</uro:city>'
            '</uro:BuildingIDAttribute></uro:buildingIDAttribute>'
            f'<bldg:storeysAboveGround>{value}</bldg:storeysAboveGround>'
            '</bldg:Building></core:cityObjectMember>'
        )

    return (
        '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
        'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
        'xmlns:gml="http://www.opengis.net/gml" '
        'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">'
        + "".join(building(*member) for member in members)
        +
        '</core:CityModel>'
    )


class GitRepo:
    def __init__(self, root: Path):
        self.root = root
        self.run("init", "-q")
        self.run("config", "user.name", "Test")
        self.run("config", "user.email", "test@example.com")

    def run(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True
        ).strip()

    def commit_gml(self, a: int, b: int, message: str) -> str:
        return self.commit_text(_gml(a, b), message)

    def commit_text(self, content: str, message: str) -> str:
        path = self.root / "tile.gml"
        path.write_text(content, encoding="utf-8")
        self.run("add", "tile.gml")
        self.run("commit", "-q", "-m", message)
        return self.run("rev-parse", "HEAD")


class TestCommitBuildingScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = GitRepo(Path(self.tmp.name))
        self.base = self.repo.commit_gml(1, 1, "baseline\n\nChange-Type: source-baseline")

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_building_commit_passes(self):
        head = self.repo.commit_gml(2, 1, "update one\n\nBuilding: 13101-bldg-1")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].ok, result[0].errors)

    def test_two_buildings_in_separate_commits_pass(self):
        self.repo.commit_gml(2, 1, "update one\n\nBuilding: 13101-bldg-1")
        head = self.repo.commit_gml(2, 2, "update two\n\nBuilding: 13101-bldg-2")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item.ok for item in result), result)

    def test_two_buildings_in_one_commit_fails(self):
        head = self.repo.commit_gml(2, 2, "update batch\n\nBuilding: 13101-bldg-1")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertFalse(result[0].ok)
        self.assertIn("actual: 2", " ".join(result[0].errors))

    def test_trailer_mismatch_fails(self):
        head = self.repo.commit_gml(2, 1, "update wrong\n\nBuilding: 13101-bldg-2")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertFalse(result[0].ok)
        self.assertIn("does not match the actual change", " ".join(result[0].errors))

    def test_one_failed_commit_rejects_range(self):
        self.repo.commit_gml(2, 1, "update one\n\nBuilding: 13101-bldg-1")
        head = self.repo.commit_gml(3, 2, "invalid batch\n\nBuilding: 13101-bldg-1")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertTrue(result[0].ok)
        self.assertFalse(result[1].ok)
        self.assertTrue(any(not item.ok for item in result))
        self.assertEqual(main([
            "--repo", str(self.repo.root), "--base-sha", self.base, "--head-sha", head
        ]), 1)

    def test_same_building_in_two_commits_fails(self):
        self.repo.commit_gml(2, 1, "update one\n\nBuilding: 13101-bldg-1")
        head = self.repo.commit_gml(3, 1, "fixup one\n\nBuilding: 13101-bldg-1")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertTrue(result[0].ok)
        self.assertFalse(result[1].ok)
        self.assertIn("multiple commits", " ".join(result[1].errors))

    def test_scope_extract_keeps_only_target_municipality(self):
        source = _gml_members([
            ("13101-bldg-1", "13101", 1),
            ("13105-bldg-2", "13105", 1),
        ])
        base = self.repo.commit_text(source, "official boundary mesh")
        extracted = _gml_members([("13101-bldg-1", "13101", 1)])
        head = self.repo.commit_text(
            extracted,
            "extract Chiyoda\n\nChange-Type: scope-extract\nScope-Municipality: 13101",
        )
        result = inspect_range(self.repo.root, base, head)
        self.assertTrue(result[0].ok, result[0].errors)

    def test_scope_extract_rejects_target_omission_and_other_city(self):
        source = _gml_members([
            ("13101-bldg-1", "13101", 1),
            ("13105-bldg-2", "13105", 1),
        ])
        base = self.repo.commit_text(source, "official boundary mesh")
        wrong = _gml_members([("13105-bldg-2", "13105", 1)])
        head = self.repo.commit_text(
            wrong,
            "bad extract\n\nChange-Type: scope-extract\nScope-Municipality: 13101",
        )
        result = inspect_range(self.repo.root, base, head)
        self.assertFalse(result[0].ok)
        errors = " ".join(result[0].errors)
        self.assertIn("were missed", errors)
        self.assertIn("remain that are outside", errors)

    def test_scope_extract_rejects_retained_building_change(self):
        source = _gml_members([
            ("13101-bldg-1", "13101", 1),
            ("13105-bldg-2", "13105", 1),
        ])
        base = self.repo.commit_text(source, "official boundary mesh")
        changed = _gml_members([("13101-bldg-1", "13101", 2)])
        head = self.repo.commit_text(
            changed,
            "bad extract\n\nChange-Type: scope-extract\nScope-Municipality: 13101",
        )
        result = inspect_range(self.repo.root, base, head)
        self.assertFalse(result[0].ok)
        self.assertIn("Appearance", " ".join(result[0].errors))

    def test_scope_extract_requires_municipality_trailer(self):
        source = _gml_members([
            ("13101-bldg-1", "13101", 1),
            ("13105-bldg-2", "13105", 1),
        ])
        base = self.repo.commit_text(source, "official boundary mesh")
        extracted = _gml_members([("13101-bldg-1", "13101", 1)])
        head = self.repo.commit_text(
            extracted, "bad extract\n\nChange-Type: scope-extract"
        )
        result = inspect_range(self.repo.root, base, head)
        self.assertFalse(result[0].ok)
        self.assertIn("Scope-Municipality", " ".join(result[0].errors))

    def test_scope_extract_must_be_a_dedicated_pr(self):
        source = _gml_members([
            ("13101-bldg-1", "13101", 1),
            ("13105-bldg-2", "13105", 1),
        ])
        base = self.repo.commit_text(source, "official boundary mesh")
        extracted = _gml_members([("13101-bldg-1", "13101", 1)])
        self.repo.commit_text(
            extracted,
            "extract Chiyoda\n\nChange-Type: scope-extract\nScope-Municipality: 13101",
        )
        head = self.repo.commit_text(
            _gml_members([("13101-bldg-1", "13101", 2)]),
            "update\n\nBuilding: 13101-bldg-1",
        )
        result = inspect_range(self.repo.root, base, head)
        self.assertFalse(result[0].ok)
        self.assertIn("dedicated commit/PR", " ".join(result[0].errors))


if __name__ == "__main__":
    unittest.main()
