# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The one form of an inspection gate's result (S17)."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts import gate_result as g
from tests.support import REPO_ROOT


class TestGateResult(unittest.TestCase):
    def test_exit_codes_map_to_statuses_by_severity(self):
        self.assertEqual([g.status_for("schema", c) for c in (0, 1, 2, 3)], ["pass", "fail", "error", "error"])
        self.assertEqual([g.status_for("plausibility", c) for c in (0, 1, 2)], ["pass", "warn", "error"])
        self.assertEqual(g.status_for("schema", None), "pending")
        self.assertEqual(g.status_for("schema", 1, applicable=False), "na")

    def test_maintainer_severity_table(self):
        # decision 2026-09-30: these four are advisory, every other row blocks
        advisory = {r.key for r in g.ROWS if r.severity == g.ADVISORY}
        self.assertEqual(advisory, {"plausibility", "minimal-diff", "topology", "model"})
        self.assertEqual(g.BLOCKS, {"fail", "error", "pending"})

    def test_rows_are_the_contract_rows_in_order(self):
        # the <!--cp:key--> anchors of the inspection comment; the ci catalogs name every row
        self.assertEqual([r.key for r in g.ROWS][:3], ["reason", "classification", "commit-scope"])
        catalog = json.loads((REPO_ROOT / "tools/i18n/catalogs/ci/en.json").read_text(encoding="utf-8"))
        for row in g.ROWS:
            self.assertEqual(catalog.get(row.label_key), row.label, row.key)

    def test_warnings_of_a_clean_run_show_without_blocking(self):
        self.assertEqual(g.status_for("structure", 0, warnings=3), "warn")      # blocking gate, warnings only
        self.assertEqual(g.status_for("structure", 1, warnings=3), "fail")
        self.assertEqual(g.status_for("plausibility", 0, warnings=1), "warn")
        self.assertNotIn("warn", g.BLOCKS)

    def test_lints_exit_2_when_they_crash(self):
        # D16: an exception exited 1, read as findings; the lints now exit 2
        import subprocess, sys
        for script in ("scripts/citygml_lint.py", "scripts/plausibility_lint.py"):
            with tempfile.TemporaryDirectory() as t:
                unreadable = Path(t) / "dir.gml"
                unreadable.mkdir()
                r = subprocess.run([sys.executable, str(REPO_ROOT / script), str(unreadable)], capture_output=True,
                                   text=True, cwd=REPO_ROOT)
            self.assertEqual(r.returncode, 2, (script, r.stderr[-300:]))

    def test_a_malformed_file_is_a_finding_not_a_system_error(self):
        # snapshot broken-xml: a missing end tag made four rows say "not your data"
        import subprocess, sys
        from xml.etree import ElementTree
        self.assertTrue(g.malformed_input(ElementTree.ParseError("x")))
        self.assertFalse(g.malformed_input(OSError("x")))
        self.assertEqual(g.guarded(lambda: ElementTree.fromstring("<a><b></a>")), 1)
        self.assertEqual(g.guarded(lambda: 1 / 0), 2)
        for script in ("scripts/citygml_lint.py", "scripts/plausibility_lint.py"):
            with tempfile.TemporaryDirectory() as t:
                bad = Path(t) / "bad.gml"
                bad.write_text("<core:CityModel><not xml", encoding="utf-8")
                r = subprocess.run([sys.executable, str(REPO_ROOT / script), str(bad)], capture_output=True, text=True,
                                   cwd=REPO_ROOT)
            self.assertEqual(r.returncode, 1, (script, r.stderr[-300:]))

    def test_ran_means_a_result(self):
        self.assertEqual([g.result("schema", c)["ran"] for c in (0, 1, 2, None)], [True, True, False, False])
        self.assertFalse(g.result("model", 0, applicable=False)["ran"])

    def test_cli_writes_the_file_the_summary_reads(self):
        with tempfile.TemporaryDirectory() as t:
            g.main(["write", "--key", "plausibility", "--exit-code", "1", "--summary", "1 warning", "--out", t])
            entry = g.read(Path(t), "plausibility")
            self.assertEqual((entry["status"], entry["severity"], entry["ran"], entry["summary"]),
                             ("warn", "advisory", True, "1 warning"))
            g.main(["write", "--key", "model", "--not-applicable", "--out", t])
            self.assertEqual(g.read(Path(t), "model")["status"], "na")
            self.assertIsNone(g.read(Path(t), "schema"))


if __name__ == "__main__":
    unittest.main()
