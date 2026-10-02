# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One rule for the reason section: CI's `reason` row, the hub preview, the editor pretest (S5)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import pr_reason
from tests.support import REPO_ROOT


class TestPrReason(unittest.TestCase):
    def test_the_anchor_comes_first_then_the_headings(self):
        body = "## Summary of changes\n\nold\n\n## Grund <!--sec:reason-->\nBauakte 2026 <!-- hidden -->\n## Next\nx"
        self.assertEqual(pr_reason.section(body), "Bauakte 2026")
        self.assertEqual(pr_reason.section("## 変更の概要\n\n現地調査 2026\n"), "現地調査 2026")
        self.assertEqual(pr_reason.section("no sections at all"), "")

    def test_placeholders_and_short_texts_are_not_a_reason(self):
        # the editor accepted the English placeholders, the hub any text without "please fill in"
        for text in ("(please fill in)", "Not filled in", "TODO", "記入してください", "abc", "   "):
            self.assertFalse(pr_reason.filled(text), text)
        self.assertTrue(pr_reason.filled("2026 field survey sheet"))

    def test_a_german_heading_is_found(self):
        # CI knew no German heading; the hub found it through the catalogs
        self.assertTrue(pr_reason.acceptable("## Begründung und Belege\n\nBauakte 2026, Blatt 3\n"))

    def test_the_headings_cover_the_editors_catalogs(self):
        for path in (REPO_ROOT / "tools/i18n/catalogs").glob("*/*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("pr.heading_reason", "pr.heading_summary"):
                if data.get(key):
                    self.assertIn(data[key], pr_reason.HEADINGS, f"{path.relative_to(REPO_ROOT)} {key}")

    def test_cli_follows_the_gate_exit_convention(self):
        def run(event):
            with tempfile.TemporaryDirectory() as t:
                path = Path(t) / "event.json"
                path.write_text(event, encoding="utf-8")
                return subprocess.run([sys.executable, str(REPO_ROOT / "scripts/pr_reason.py"), str(path)],
                                      capture_output=True, text=True).returncode
        body = lambda text: json.dumps({"pull_request": {"body": f"## Reason <!--sec:reason-->\n{text}\n"}})
        self.assertEqual(run(body("2026 field survey sheet")), 0)
        self.assertEqual(run(body("TODO")), 1)
        self.assertEqual(run("{not json"), 2)        # it crashed: an error, not the proposer's text

    def test_hub_and_editor_use_it(self):
        for path in ("tools/hub/app.py", "tools/attr_editor/app.py"):
            self.assertIn("pr_reason.filled(", (REPO_ROOT / path).read_text(encoding="utf-8"), path)


if __name__ == "__main__":
    unittest.main()
