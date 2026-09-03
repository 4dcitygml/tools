# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""schema-update commits: edition artifacts only (code lists, schema profiles),
never CityGML data."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.commit_building_scope import inspect_range
from tests.test_commit_building_scope import GitRepo, _gml_members


class SchemaUpdateGateTest(unittest.TestCase):
    def test_artifacts_only_passes_and_data_change_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = GitRepo(t)
            base = repo.commit_text(_gml_members([("13101-bldg-1", "13101", 3)]), "baseline")
            (t / "codelists" / "iur-3.2").mkdir(parents=True)
            (t / "codelists/iur-3.2/Building_usage.xml").write_text("<gml:Dictionary/>", encoding="utf-8")
            repo.run("add", "codelists")
            repo.run("commit", "-q", "-m", "Add the iur-3.2 code lists\n\nChange-Type: schema-update\n")
            head = repo.run("rev-parse", "HEAD")
            self.assertEqual([r.errors for r in inspect_range(t, base, head)], [[]])
            # a schema-update touching data is rejected
            head2 = repo.commit_text(_gml_members([("13101-bldg-1", "13101", 4)]), "Bump storeys\n\nChange-Type: schema-update\n")
            errors = inspect_range(t, base, head2)[-1].errors
            self.assertTrue(any("must not change CityGML data" in e for e in errors), errors)
            # a schema-update touching a file outside the artifact folders is rejected
            (t / "README.md").write_text("x", encoding="utf-8"); repo.run("add", "README.md")
            repo.run("commit", "-q", "-m", "Edit readme\n\nChange-Type: schema-update\n")
            head3 = repo.run("rev-parse", "HEAD")
            errors = inspect_range(t, base, head3)[-1].errors
            self.assertTrue(any("only edition artifacts" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
