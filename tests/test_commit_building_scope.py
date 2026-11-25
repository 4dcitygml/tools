#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 1 commit = 1 building ID gate."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.commit_building_scope import inspect_range, main, pr_facts, pr_scope, practice_reset_only, render


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


# The fixtures are PLATEAU data (uro:buildingID, i-UR): their city declares so (v2.0.0: an undeclared
# city has no dialect, its building ID is gml:id and the i-UR features are off).
PLATEAU_CITY = {"repo": "o/r", "building_id": {"type": "uro:buildingID"}, "dialect": "plateau", "crs": "EPSG:6697"}


class GitRepo:
    def __init__(self, root: Path, declaration: "dict | None" = PLATEAU_CITY):
        self.root = root
        self.run("init", "-q")
        self.run("config", "user.name", "Test")
        self.run("config", "user.email", "test@example.com")
        if declaration is not None:
            (root / "4dcitygml.json").write_text(json.dumps(declaration), encoding="utf-8")
            self.run("add", "4dcitygml.json")
            self.run("commit", "-q", "-m", "Declare the city")

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

    def update_with_the_button(self, other_branch: str = "main") -> "tuple[str, str]":
        """A PR on bldg-1, main moving on in another file, then GitHub's "Update branch" (default
        merge mode): a merge of `other_branch` into the PR. -> (the base it now has, the head)."""
        main = self.repo.run("rev-parse", "--abbrev-ref", "HEAD")
        self.repo.run("checkout", "-q", "-b", "edit/13101-bldg-1")
        self.repo.commit_gml(2, 1, "update one\n\nBuilding: 13101-bldg-1")
        self.repo.run("checkout", "-q", main)
        if other_branch != "main":
            self.repo.run("checkout", "-q", "-b", other_branch)
        (self.repo.root / "other.gml").write_text(_gml_members([("13102-bldg-9", "13102", 3)]), encoding="utf-8")
        self.repo.run("add", "other.gml")
        self.repo.run("commit", "-q", "-m", "another merged proposal\n\nBuilding: 13102-bldg-9")
        moved = self.repo.run("rev-parse", "HEAD")
        self.repo.run("checkout", "-q", "edit/13101-bldg-1")
        self.repo.run("merge", "-q", "--no-ff", "--no-edit", moved)
        return (moved if other_branch == "main" else self.base), self.repo.run("rev-parse", "HEAD")

    def test_a_pr_updated_with_githubs_button_passes(self):
        # T24: "Update branch" merges main into the PR; that merge brings only the base
        base, head = self.update_with_the_button()
        result = inspect_range(self.repo.root, base, head)
        self.assertEqual([r.ok for r in result], [True, True], [r.errors for r in result])
        self.assertEqual(result[0].changed_ids, {"13101-bldg-1"})
        self.assertEqual(result[1].change_type, "base-update")
        scope = pr_scope(self.repo.root, base, head)   # the PR changes one building; main's stays out
        self.assertEqual((scope["modified"], scope["added"], scope["deleted"]), (["13101-bldg-1"], [], []))
        self.assertIn("base-update", render(result))

    def test_a_merge_with_a_hand_made_change_is_refused(self):
        base, head = self.update_with_the_button()
        self.repo.run("reset", "-q", "--soft", "HEAD^")                    # undo, then merge again by hand
        self.repo.run("reset", "-q", "--hard", "HEAD")
        self.repo.run("merge", "-q", "--no-ff", "--no-commit", base)
        (self.repo.root / "tile.gml").write_text(_gml(5, 1), encoding="utf-8")   # a change inside the merge
        self.repo.run("add", "tile.gml")
        self.repo.run("commit", "-q", "--no-edit")
        result = inspect_range(self.repo.root, base, self.repo.run("rev-parse", "HEAD"))
        self.assertFalse(result[-1].ok)
        self.assertIn("Rebase onto main", " ".join(result[-1].errors))

    def test_a_merge_of_another_branch_is_refused(self):
        base, head = self.update_with_the_button(other_branch="side")
        result = inspect_range(self.repo.root, base, head)
        self.assertFalse(next(r for r in result if len(self.repo.run("show", "-s", "--format=%P", r.sha).split()) == 2).ok)

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

    def test_scope_extract_takes_only_the_packs_municipality_codes(self):
        # D-b8: the trailer's value was never checked (extract_municipality requires 5 digits), and
        # a building's 4-digit uro:city counted as its municipality; the pack owns the code's form
        source = _gml_members([("13101-bldg-1", "13101", 1), ("1310-bldg-2", "1310", 1)])
        base = self.repo.commit_text(source, "official boundary mesh")
        head = self.repo.commit_text(_gml_members([("13101-bldg-1", "13101", 1)]),
                                     "extract\n\nChange-Type: scope-extract\nScope-Municipality: 13101")
        errors = " ".join(inspect_range(self.repo.root, base, head)[0].errors)
        self.assertIn("no determinable municipality code: 1310-bldg-2", errors)
        head = self.repo.commit_text(_gml_members([("1310-bldg-2", "1310", 1)]),
                                     "extract\n\nChange-Type: scope-extract\nScope-Municipality: 1310")
        self.assertIn("1310 is not a municipality code", " ".join(inspect_range(self.repo.root, base, head)[-1].errors))

    def _city(self, building_id: dict, members: str) -> None:
        # a CityGML 1.0 city in the default namespace, identified as its 4dcitygml.json says
        (self.repo.root / "4dcitygml.json").write_text(
            json.dumps({"repo": "example-city/citygml", "data_dirs": ["data"], "building_id": building_id}),
            encoding="utf-8")
        (self.repo.root / "data").mkdir(exist_ok=True)
        (self.repo.root / "data" / "tile.gml").write_text(
            '<CityModel xmlns="http://www.opengis.net/citygml/1.0" xmlns:bldg="http://www.opengis.net/citygml/building/1.0"'
            ' xmlns:gen="http://www.opengis.net/citygml/generics/1.0" xmlns:gml="http://www.opengis.net/gml">\n'
            + members + "</CityModel>\n", encoding="utf-8")
        self.repo.run("add", "4dcitygml.json", "data/tile.gml")

    @staticmethod
    def _member10(gml_id: str, bin_value: str, storeys: int) -> str:
        return (f'<cityObjectMember><bldg:Building gml:id="{gml_id}"><gen:stringAttribute name="BIN">'
                f"<gen:value>{bin_value}</gen:value></gen:stringAttribute>"
                f"<bldg:storeysAboveGround>{storeys}</bldg:storeysAboveGround></bldg:Building></cityObjectMember>\n")

    def test_gml_id_city_counts_its_buildings(self):
        # Munich: no uro:buildingID anywhere, the gml:id is the stable ID (4dcitygml.json building_id)
        self._city({"type": "gml:id"}, self._member10("DEBY_LOD2_59812", "1", 6) + self._member10("DEBY_LOD2_2", "2", 4))
        base = self.repo.commit_text("", "baseline\n\nChange-Type: source-baseline")
        self._city({"type": "gml:id"}, self._member10("DEBY_LOD2_59812", "1", 7) + self._member10("DEBY_LOD2_2", "2", 4))
        head = self.repo.commit_text("", "Storeys 6 -> 7\n\nBuilding: DEBY_LOD2_59812")
        result = inspect_range(self.repo.root, base, head)
        self.assertTrue(result[0].ok, result[0].errors)
        self.assertEqual(result[0].changed_ids, {"DEBY_LOD2_59812"})

    def test_generic_attribute_city_uses_its_declared_id(self):
        # New York: the BIN generic attribute is the stable ID; a placeholder value falls back to the gml:id
        rule = {"type": "gen:BIN", "invalid_values": ["1000000"]}
        self._city(rule, self._member10("gml_A", "1036448", 6) + self._member10("gml_B", "1000000", 4))
        base = self.repo.commit_text("", "baseline\n\nChange-Type: source-baseline")
        self._city(rule, self._member10("gml_A", "1036448", 7) + self._member10("gml_B", "1000000", 4))
        head = self.repo.commit_text("", "Storeys 6 -> 7\n\nBuilding: 1036448")
        result = inspect_range(self.repo.root, base, head)
        self.assertTrue(result[0].ok, result[0].errors)
        self._city(rule, self._member10("gml_A", "1036448", 7) + self._member10("gml_B", "1000000", 5))
        head2 = self.repo.commit_text("", "Storeys 4 -> 5\n\nBuilding: gml_B")
        result = inspect_range(self.repo.root, head, head2)
        self.assertTrue(result[0].ok, result[0].errors)

    def test_practice_reset_restores_the_named_tree(self):
        practice = self.repo.commit_gml(5, 1, "practice edit\n\nBuilding: 13101-bldg-1")
        reset = self.repo.commit_gml(1, 1, f"Reset practice data to baseline\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        result = inspect_range(self.repo.root, practice, reset)
        self.assertTrue(result[0].ok, result[0].errors)
        self.assertEqual(result[0].change_type, "practice-reset")
        self.assertIn("practice-reset", render(result))   # the CI summary names the kind

    def test_practice_reset_rejects_a_tree_that_differs_from_reset_to(self):
        practice = self.repo.commit_gml(5, 1, "practice edit\n\nBuilding: 13101-bldg-1")
        partial = self.repo.commit_gml(1, 2, f"partial reset\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        result = inspect_range(self.repo.root, practice, partial)
        self.assertFalse(result[0].ok)
        self.assertTrue(any("restore the data directories exactly" in e for e in result[0].errors), result[0].errors)

    def test_practice_reset_changes_nothing_outside_the_data(self):
        practice = self.repo.commit_gml(5, 1, "practice edit\n\nBuilding: 13101-bldg-1")
        (self.repo.root / "README.md").write_text("changed on the way back\n", encoding="utf-8")
        self.repo.run("add", "README.md")
        mixed = self.repo.commit_gml(1, 1, f"reset\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        result = inspect_range(self.repo.root, practice, mixed)
        self.assertTrue(any("only the city's data directories" in e and "README.md" in e for e in result[0].errors),
                        result[0].errors)

    def test_practice_reset_requires_reset_to_and_no_building_trailers(self):
        practice = self.repo.commit_gml(5, 1, "practice edit\n\nBuilding: 13101-bldg-1")
        no_target = self.repo.commit_gml(1, 1, "reset\n\nChange-Type: practice-reset")
        result = inspect_range(self.repo.root, practice, no_target)
        self.assertTrue(any("Reset-To" in e for e in result[0].errors), result[0].errors)
        with_building = self.repo.commit_gml(5, 1, f"reset\n\nChange-Type: practice-reset\nReset-To: {self.base}\nBuilding: 13101-bldg-1")
        result = inspect_range(self.repo.root, no_target, with_building)
        self.assertTrue(any("per-building" in e for e in result[0].errors), result[0].errors)

    def test_a_reset_of_two_buildings_is_reported_as_reset_only(self):
        # the analysis driver reads practiceResetOnly to lift the one-building rule
        self.repo.commit_gml(5, 1, "practice edit\n\nBuilding: 13101-bldg-1")
        practice = self.repo.commit_gml(5, 6, "practice edit\n\nBuilding: 13101-bldg-2")
        reset = self.repo.commit_gml(1, 1, f"reset\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        out = Path(self.tmp.name) / "scope.json"
        self.assertEqual(main(["--repo", str(self.repo.root), "--base-sha", practice, "--head-sha", reset,
                               "--json-output", str(out)]), 0)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["practiceResetOnly"])

    def test_reset_only_needs_every_commit_to_be_an_accepted_reset(self):
        practice = self.repo.commit_gml(5, 6, "practice edit\n\nBuilding: 13101-bldg-1")
        reset = self.repo.commit_gml(1, 1, f"reset\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        after = self.repo.commit_gml(7, 1, "edit after the reset\n\nBuilding: 13101-bldg-1")
        self.assertFalse(practice_reset_only(inspect_range(self.repo.root, practice, after)))   # a reset plus an edit
        partial = self.repo.commit_gml(1, 2, f"partial reset\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        self.assertFalse(practice_reset_only(inspect_range(self.repo.root, after, partial)))    # a rejected reset
        self.assertFalse(practice_reset_only([]))
        edit = self.repo.commit_gml(3, 2, "edit\n\nBuilding: 13101-bldg-2")
        out = Path(self.tmp.name) / "scope.json"
        main(["--repo", str(self.repo.root), "--base-sha", partial, "--head-sha", edit, "--json-output", str(out)])
        self.assertFalse(json.loads(out.read_text(encoding="utf-8"))["practiceResetOnly"])

    def test_pr_facts_read_the_trailers_once_for_the_driver(self):
        practice = self.repo.commit_gml(5, 1, "practice edit\n\nBuilding: 13101-bldg-1")
        reset = self.repo.commit_gml(1, 1, f"reset\n\nChange-Type: practice-reset\nReset-To: {self.base}")
        facts = pr_facts(inspect_range(self.repo.root, practice, reset))
        self.assertEqual((facts["changeTypes"], facts["administrative"], facts["practiceReset"], facts["bulk"]),
                         (["practice-reset"], True, True, False))
        edit = self.repo.commit_gml(3, 1, "edit\n\nBuilding: 13101-bldg-1")
        facts = pr_facts(inspect_range(self.repo.root, reset, edit))
        self.assertEqual((facts["administrative"], facts["practiceReset"], facts["scopeMunicipalities"]), (False, False, []))
        # trailer values are read as the gate reads them: surrounding blanks do not matter
        spaced = self.repo.commit_gml(1, 1, f"reset\n\nChange-Type:  practice-reset  \nReset-To: {self.base}")
        self.assertTrue(pr_facts(inspect_range(self.repo.root, edit, spaced))["practiceReset"])

    def test_an_unknown_change_type_is_rejected_and_not_administrative(self):
        # D3: any Change-Type used to make the PR administrative and skip the classification gate
        head = self.repo.commit_gml(2, 1, "edit\n\nBuilding: 13101-bldg-1\nChange-Type: foo-bar")
        result = inspect_range(self.repo.root, self.base, head)
        self.assertTrue(any("Unknown Change-Type 'foo-bar'" in e for e in result[0].errors), result[0].errors)
        self.assertFalse(pr_facts(result)["administrative"])

    def scope_counts(self, base: str, head: str) -> tuple:
        got = pr_scope(self.repo.root, base, head)
        return (got["modified"], got["added"], got["deleted"], got["renamed"], got["class"])

    def two(self, a_gml="gml-13101-bldg-1", a_storeys=1, b_storeys=1) -> str:
        return _gml_members([("13101-bldg-1", "13101", a_storeys), ("13101-bldg-2", "13101", b_storeys)]) \
            .replace('gml:id="gml-13101-bldg-1"', f'gml:id="{a_gml}"')

    def test_pr_scope_counts_by_stable_id_and_meaning(self):
        # S1: the one count of the PR's buildings; each case is one the quality step's gml:id count got wrong
        base = self.base                                   # setUp's baseline holds self.two()
        one = self.repo.commit_text(self.two(a_storeys=2), "edit\n\nBuilding: 13101-bldg-1")
        self.assertEqual(self.scope_counts(base, one), (["13101-bldg-1"], [], [], [], "single"))
        renamed = self.repo.commit_text(self.two(a_gml="gml-new"), "id only\n\nBuilding: 13101-bldg-1")
        self.assertEqual(self.scope_counts(base, renamed), ([], [], [], ["13101-bldg-1"], "rename"))       # D14
        both = self.repo.commit_text(self.two(a_gml="gml-new2", a_storeys=3), "id and value\n\nBuilding: 13101-bldg-1")
        self.assertEqual(self.scope_counts(base, both), (["13101-bldg-1"], [], [], [], "single"))        # D21
        churn = self.repo.commit_text(self.two().replace("><bldg:", ">\n  <bldg:"), "format only")
        self.assertEqual(self.scope_counts(base, churn), ([], [], [], [], "none"))

    def test_pr_scope_reads_numbers_as_the_diff_does(self):
        # U6 / D-c1: the PR scope compared raw values, so 1 -> 1.0 was a modification here and no
        # change in diff_citygml; one rule now (numeric-normalised), renames 1:1 only
        base = self.base
        reformatted = self.repo.commit_text(self.two().replace(
            "<bldg:storeysAboveGround>1</bldg:storeysAboveGround>", "<bldg:storeysAboveGround>1.0</bldg:storeysAboveGround>"),
            "format only")
        self.assertEqual(self.scope_counts(base, reformatted), ([], [], [], [], "none"))

    def test_pr_scope_counts_the_buildings_of_whole_files(self):
        base = self.base
        (self.repo.root / "other.gml").write_text(_gml_members([("13101-bldg-3", "13101", 1), ("13101-bldg-4", "13101", 1)]),
                                                  encoding="utf-8")
        self.repo.run("add", "other.gml")
        self.repo.run("commit", "-q", "-m", "a new file")
        added = self.repo.run("rev-parse", "HEAD")
        self.assertEqual(self.scope_counts(base, added)[1:], (["13101-bldg-3", "13101-bldg-4"], [], [], "lifecycle"))   # D17
        self.repo.run("rm", "-q", "tile.gml")
        self.repo.run("commit", "-q", "-m", "a file removed")
        removed = self.repo.run("rev-parse", "HEAD")
        self.assertEqual(self.scope_counts(added, removed)[2], ["13101-bldg-1", "13101-bldg-2"])                        # D20

    def test_the_quality_step_reads_the_scope_instead_of_counting(self):
        driver = (Path(__file__).resolve().parents[1] / "ci/pr_analysis_main.sh").read_text(encoding="utf-8")
        self.assertNotIn("/^BLDG renamed/", driver)          # D14's field was the word "renamed"
        self.assertNotIn("--changed-buildings", driver)      # texture changes are in the scope now
        self.assertNotIn("NEWFILE", driver)                  # D17: a new file counted as one building
        self.assertIn('counts="$(jq -r \'.scope // empty', driver)        # U3: jq reads the scope file

    def test_a_malformed_file_makes_the_scope_a_finding(self):
        # snapshot broken-xml: the scope failed on a missing end tag and file-scope showed a system error
        from unittest.mock import patch
        from xml.etree import ElementTree
        from scripts import commit_building_scope as cbs
        with patch.object(cbs, "pr_scope", side_effect=ElementTree.ParseError("mismatched tag")):
            self.assertTrue(cbs._scope_or_error(Path("."), "a", "b")["scopeMalformed"])
        with patch.object(cbs, "pr_scope", side_effect=OSError("git failed")):
            self.assertFalse(cbs._scope_or_error(Path("."), "a", "b")["scopeMalformed"])
        driver = (Path(__file__).resolve().parents[1] / "ci/pr_analysis_main.sh").read_text(encoding="utf-8")
        step = driver[driver.index("jq -r '.scopeMalformed // false'"):]
        self.assertLess(step.index("exit 1"), step.index("exit 2"))    # a malformed file: a finding (1), else 2

    def test_the_driver_reads_the_facts_instead_of_grepping_messages(self):
        driver = (Path(__file__).resolve().parents[1] / "ci/pr_analysis_main.sh").read_text(encoding="utf-8")
        self.assertNotIn("pr-messages", driver)
        for key in ("administrative", "sourceBaseline", "practiceReset", "identity", "bulk", "scopeExtract"):
            self.assertIn(f"$(flag {key})", driver)
        self.assertIn('ref="$(fact manifestRef)"', driver)

    def test_the_analysis_driver_lifts_the_one_building_rule_only_for_reset_only(self):
        driver = (Path(__file__).resolve().parents[1] / "ci/pr_analysis_main.sh").read_text(encoding="utf-8")
        self.assertIn('RESET_ONLY="$(flag practiceResetOnly)"', driver)
        reads, rule = driver.index('RESET_ONLY="$('), driver.index("An ordinary PR changes")
        self.assertLess(reads, rule)
        self.assertIn('if [ "$CLASS" = "multi-modified" ] && [ "${RESET_ONLY:-false}" = "true" ]; then', driver)
        self.assertIn('elif [ "$CLASS" = "multi-modified" ] && [ "${BULK_ENABLED:-false}" != "true" ]; then', driver)

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
