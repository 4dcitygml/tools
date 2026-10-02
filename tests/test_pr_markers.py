# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Every comment marker written or read anywhere in the repository is an entry of
scripts/pr_markers.py: the CI shell scripts and the contract document carry the same
strings as literals, and this test keeps them equal to the table."""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pr_markers", REPO_ROOT / "scripts" / "pr_markers.py")
pr_markers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_markers)

LITERAL = re.compile(r"<!--\s*(?:citygml|plateau|val3dity|cesium|status|cp|sec)[^>]*?-->")
SCANNED = [*sorted((REPO_ROOT / "ci").glob("*.sh")),
           *sorted(p for p in (REPO_ROOT / "scripts").glob("*.py") if p.name != "pr_markers.py"),
           REPO_ROOT / "tools" / "hub" / "app.py", REPO_ROOT / "docs" / "exchange-contract.md"]
PLACEHOLDERS = {"<!-- status:... -->", "<!--sec:key-->", "<!--cp:key-->"}   # prose that names the form, not a marker


def literals(path: Path) -> set:
    text = path.read_text(encoding="utf-8")
    found = set()
    for m in LITERAL.finditer(text):
        lit = m.group(0)
        if "{" in lit or "$" in lit or "([" in lit or "\\" in lit or lit in PLACEHOLDERS:
            continue   # a format string, a regex or a placeholder in prose, not a literal marker
        found.add(lit)
    return found


class TestMarkersAreTheTable(unittest.TestCase):
    def test_every_literal_is_an_entry_of_the_table(self):
        unknown = {}
        for path in SCANNED:
            for lit in literals(path):
                if lit in pr_markers.KNOWN or any(p.fullmatch(lit) for p in pr_markers.KNOWN_PATTERNS):
                    continue
                unknown.setdefault(path.relative_to(REPO_ROOT).as_posix(), []).append(lit)
        self.assertEqual(unknown, {}, "markers outside scripts/pr_markers.py")

    def test_the_contract_names_every_comment_marker(self):
        doc = (REPO_ROOT / "docs" / "exchange-contract.md").read_text(encoding="utf-8")
        for marker in pr_markers.COMMENT_MARKERS:
            self.assertIn(marker, doc, marker)

    def test_the_report_marker_is_the_contract_masters(self):
        spec = importlib.util.spec_from_file_location("contract", REPO_ROOT / "tools" / "hub" / "operator_explanation.py")
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        self.assertEqual(pr_markers.REVIEW_REPORT, contract.REPORT_MARKER)

    def test_forms(self):
        self.assertEqual(pr_markers.status("active"), pr_markers.STATUS_ACTIVE)
        self.assertEqual(pr_markers.checkpoint("schema"), "<!--cp:schema-->")
        self.assertEqual(pr_markers.CHECKPOINT_RE.search("| x <!-- cp:file-scope --> |").group(1), "file-scope")
        comments = [{"body": f"{pr_markers.BASE_FRESHNESS}\n{pr_markers.STATUS_RESOLVED}"}]
        self.assertFalse(pr_markers.has_active(comments, pr_markers.BASE_FRESHNESS))
        comments.append({"body": f"{pr_markers.AUTO_RESUBMISSION}\n{pr_markers.STATUS_ACTIVE}\ntext"})
        self.assertTrue(pr_markers.has_active(comments, pr_markers.AUTO_RESUBMISSION))


if __name__ == "__main__":
    unittest.main()
