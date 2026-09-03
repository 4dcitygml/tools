# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""carry-forward in a city repository: reviewed overrides resolve refined codes,
carried codes stage the old edition's code lists under codelists/<edition>/,
the per-building commits pass the commit scope gate, and the carried-codeSpace
report counts what is left for a reviewer."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import carry_forward_manifest as C
from scripts import carried_codespace_report as REPORT
from scripts.commit_building_scope import inspect_range
from tests.test_carry_forward_manifest import NS30, NS31, gml, q30, q31, st
from tests.test_commit_building_scope import GitRepo


class CarryForwardRepoTest(unittest.TestCase):
    def test_overrides_resolve_refined_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "overrides.json").write_text(json.dumps({"pairs": {"iur-3.0__iur-3.1": {"lists": {"quality.geometrySrcDesc": {"codes": {
                "5": {"to": ["000"], "relation": "refined", "rule": "all 2023 aerial photogrammetry in this city was a public survey"}}}}}}}), encoding="utf-8")
            cw = C.load_crosswalk("iur-3.0", "iur-3.1", None, str(t / "overrides.json"))
            self.assertEqual(cw["lists"]["quality.geometrySrcDesc"]["codes"]["5"]["confidence"], "reviewed")
            from scripts.codelist_crosswalk import resolve
            self.assertEqual(resolve(cw, "quality.geometrySrcDesc", "5"), "000")
            self.assertIn("overrides_sha256", cw)

    def test_commits_stage_code_lists_and_pass_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = GitRepo(t)
            base = gml(NS30, [("13101-bldg-1", st("9999") + q30("5", "6"))])
            current = gml(NS30, [("13101-bldg-1", st("4") + q30("77", "1"))])   # storeys + unmapped geometry code + exact thematic code
            new = gml(NS31, [("13101-bldg-1", st("9999") + q31("103", "103", "802"))])
            (t / "udx" / "bldg").mkdir(parents=True)
            (t / "udx/bldg/tile.gml").write_bytes(new)
            repo.run("add", "udx/bldg/tile.gml"); repo.run("commit", "-q", "-m", "Record the 3.1 edition\n\nChange-Type: source-baseline\n")
            base_sha = repo.run("rev-parse", "HEAD")
            work = t / "work"; work.mkdir()
            (work / "base.gml").write_bytes(base); (work / "current.gml").write_bytes(current); (work / "new.gml").write_bytes(new)
            lists = work / "lists30"; lists.mkdir()
            (lists / "BuildingDataQualityAttribute_geometrySrcDesc.xml").write_text("<gml:Dictionary/>", encoding="utf-8")
            rc = C.main(["generate", "--repository", "example/x", "--mesh", "53394611", "--municipality", "13101",
                         "--base", str(work / "base.gml"), "--current", str(work / "current.gml"), "--new", str(work / "new.gml"),
                         "--product", "udx/bldg/tile.gml", "--tools-commit", "0" * 40, "--plan-issue", "https://example.com/1",
                         "--output", str(t / "provenance/carry-forward/53394611-iur-3.0-iur-3.1.json")])
            self.assertEqual(rc, 0)
            manifest = json.loads((t / "provenance/carry-forward/53394611-iur-3.0-iur-3.1.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence"]["summary"]["carried_old_codespace"], 2)   # 77 -> lod0 + lod1
            self.assertEqual([c["new"] for c in manifest["evidence"]["changes"] if c["from_key"] == "quality.thematicSrcDesc"], ["201"])
            rc = C.main(["commits", "--repo", str(t), "--manifest", str(t / "provenance/carry-forward/53394611-iur-3.0-iur-3.1.json"), "--codelists-from", str(lists)])
            self.assertEqual(rc, 0)
            self.assertTrue((t / "codelists/iur-3.0/BuildingDataQualityAttribute_geometrySrcDesc.xml").is_file())
            head = repo.run("rev-parse", "HEAD")
            results = inspect_range(t, base_sha, head)
            self.assertEqual([r.errors for r in results], [[] for _ in results])
            self.assertEqual(len(results), 2)  # code lists commit + one building commit
            rows = REPORT.scan(t / "udx/bldg/tile.gml")
            self.assertEqual({(r["edition"], r["code"]) for r in rows}, {("iur-3.0", "77")})
            self.assertEqual(len(rows), 2)
            self.assertEqual(REPORT.main([str(t / "udx/bldg/tile.gml"), "--max", "1"]), 1)


if __name__ == "__main__":
    unittest.main()
