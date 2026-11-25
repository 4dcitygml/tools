# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The gates' Markdown reports: one shape, one limit, one list reader (U7)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import md_report
from scripts.citygml_lint import render_markdown


class Shape(unittest.TestCase):
    def test_a_clean_check_posts_nothing(self):
        self.assertEqual(md_report.report("<!-- m -->", "Title", []), "")
        self.assertEqual(md_report.report("<!-- m -->", "Title", ["- one"]), "<!-- m -->\n## Title\n\n- one\n")

    def test_capped(self):
        self.assertEqual(md_report.capped([1, 2, 3], 5), ([1, 2, 3], 0))
        self.assertEqual(md_report.capped([1, 2, 3], 2), ([1, 2], 1))

    def test_changed_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            listing = Path(tmp) / "changed.txt"
            listing.write_text("a.gml\n\n b.gml \nREADME.md\n", encoding="utf-8")
            self.assertEqual(md_report.changed_list(listing), ["a.gml", "b.gml"])
            self.assertEqual(md_report.changed_list(listing, ""), ["a.gml", "b.gml", "README.md"])


class Limit(unittest.TestCase):
    def test_a_report_fits_a_comment(self):
        # D-c2: the lint and plausibility comments were not capped; a bulk PR's comment passed
        # 65,536 characters and was never posted
        building = {"id": "b", "errors": [{"code": "ring_not_closed", "detail": "x" * 80}] * 2000, "warnings": []}
        text = render_markdown([{"file": "t.gml", "n_errors": 2000, "n_warnings": 0, "buildings": [building]}],
                               "<!-- citygml-quality-lint -->", "Quality")
        self.assertLessEqual(len(text.encode("utf-8")), md_report.COMMENT_LIMIT)
        self.assertTrue(text.startswith("<!-- citygml-quality-lint -->\n## Quality\n"))
        self.assertTrue(text.endswith(md_report.CUT_NOTE))
        self.assertTrue(all(line.startswith(("- ", "**", "##", "<!--", "###", "<sub>")) or not line
                            for line in text.split(md_report.CUT_NOTE)[0].splitlines()))   # whole lines only
        self.assertEqual(render_markdown([{"file": "t.gml", "n_errors": 0, "n_warnings": 0, "buildings": []}],
                                         "<!-- m -->", "Quality"), "")

    def test_multibyte_text_is_cut_on_a_line(self):
        text = "<!-- m -->\n" + "".join(f"- B\u00e4ude {i}\n" for i in range(30000))   # multibyte
        cut = md_report.fit(text)
        self.assertLessEqual(len(cut.encode("utf-8")), md_report.COMMENT_LIMIT)
        self.assertTrue(cut.split(md_report.CUT_NOTE)[0].endswith("\n"))


class LogComment(unittest.TestCase):
    # U3: the commit scope and reproduction comments were built by echo blocks in the driver
    def test_the_verdict_follows_the_exit_code(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "step.log"
            log.write_text("line one\n" + "x" * 50 + "\nlast line\n", encoding="utf-8")
            for rc, sign in ((0, "\u2705 fine"), (1, "\u274c broken"), (2, "\u274c broken")):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    md_report.main(["log", "--marker", "COMMIT_SCOPE", "--rc", str(rc), "--log", str(log),
                                    "--title", "Scope", "--ok", "fine", "--fail", "broken", "--tail", "10"])
                from scripts import pr_markers
                text = out.getvalue()
                self.assertTrue(text.startswith(pr_markers.COMMIT_SCOPE + "\n## Scope\n"), rc)
                self.assertIn(sign, text)
                self.assertIn("last line", text)
                self.assertNotIn("line one", text)           # only the tail
                self.assertTrue(text.endswith("```\n"))

    def test_a_missing_log_is_an_empty_block(self):
        import contextlib, io
        from scripts import pr_markers
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            md_report.main(["log", "--marker", "COMMIT_SCOPE", "--rc", "1", "--log", "/nonexistent.log",
                            "--title", "T", "--ok", "fine", "--fail", "failed"])
        self.assertEqual(out.getvalue(), pr_markers.COMMIT_SCOPE + "\n## T\n\n\u274c failed\n\n```text\n\n```\n")


if __name__ == "__main__":
    unittest.main()
