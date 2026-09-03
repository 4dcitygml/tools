# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Per-building history derived from git: baseline, a proposal commit, and an
identity commit are reported as one timeline for the building, following the
ID change and reading the manifest's entry for the building."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.building_history import aliases_of, history, write_index
from scripts.provenance_manifest import manifest_ref
from tests.test_bulk_manifest import EXAMPLE
from tests.test_commit_building_scope import GitRepo
from tests.test_commit_scope_identity import _gml_members


class BuildingHistoryTest(unittest.TestCase):
    def test_timeline_follows_the_building_across_an_id_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = GitRepo(t)
            repo.commit_text(_gml_members([("13101-bldg-1", "13101", 3), ("13101-bldg-2", "13101", 4)]),
                             "Record the official dataset\n\nChange-Type: source-baseline\n")
            repo.commit_text(_gml_members([("13101-bldg-1", "13101", 5), ("13101-bldg-2", "13101", 4)]),
                             "Correct storeys of 13101-bldg-1\n\nBuilding: 13101-bldg-1\n")
            repo.commit_text(_gml_members([("13101-bldg-1", "13101", 5), ("13101-bldg-2", "13101", 7)]),
                             "Correct storeys of 13101-bldg-2\n\nBuilding: 13101-bldg-2\n")
            m = json.loads(json.dumps(EXAMPLE)); m["evidence"]["links"] = [{**m["evidence"]["links"][0], "from": "13101-bldg-1", "to": "13101-bldg-21"}]
            (t / "provenance/identity-baseline").mkdir(parents=True)
            data = (json.dumps(m) + "\n").encode(); (t / "provenance/identity-baseline/x.json").write_bytes(data)
            repo.run("add", "provenance")
            repo.commit_text(_gml_members([("13101-bldg-21", "13101", 5), ("13101-bldg-2", "13101", 7)]),
                             "Unify buildingID\n\nChange-Type: identity-baseline\nBuilding-ID-From: 13101-bldg-1\nBuilding-ID-To: 13101-bldg-21\n"
                             f"Provenance-Manifest: {manifest_ref('provenance/identity-baseline/x.json', data)}\n")
            self.assertEqual(aliases_of(t, "HEAD", "13101-bldg-21"), ["13101-bldg-1", "13101-bldg-21"])
            rows = history(t, "13101-bldg-21")
            self.assertEqual([r["event"] for r in rows], ["first appearance", "changed", "id changed"])
            self.assertEqual(rows[1]["changes"]["building.storeysAboveGround"], {"old": "3", "new": "5"})
            self.assertEqual((rows[2]["id_from"], rows[2]["id"]), ("13101-bldg-1", "13101-bldg-21"))
            self.assertEqual(rows[2]["manifest"]["links"][0]["to"], "13101-bldg-21")
            # the other building's commit does not appear
            self.assertFalse(any("13101-bldg-2" in r["subject"] for r in rows))
            # the one-pass static index agrees with the per-building derivation
            out = t / "site"
            summary = write_index(t, out)
            self.assertEqual(summary["buildings"], 2)
            page = json.loads((out / "buildings" / "13101-bldg-21.json").read_text(encoding="utf-8"))
            self.assertEqual(page["aliases"], ["13101-bldg-1", "13101-bldg-21"])
            self.assertEqual([e["event"] for e in page["events"]], ["first appearance", "changed", "id changed"])
            other = json.loads((out / "buildings" / "13101-bldg-2.json").read_text(encoding="utf-8"))
            self.assertEqual([e["event"] for e in other["events"]], ["first appearance", "changed"])
            self.assertTrue((out / "index.html").is_file() and (out / "index.json").is_file())


if __name__ == "__main__":
    unittest.main()
