#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Exchange Contract A5: classification is decided by one table (scripts/pr_classification.py),
strictly (no silent default) and with guidance (the how-to-fix table)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import pr_classification as pc  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "pr_classification.py"


class TestTable(unittest.TestCase):
    def test_branch_prefix_wins_over_title(self):
        self.assertEqual(pc.classify_by_name("edit/13101-bldg-1-20260906", "Update textures (3 faces)"), "attribute")
        self.assertEqual(pc.classify_by_name("tex/13101-bldg-1-20260906", "Update attributes (Usage)"), "texture")
        self.assertEqual(pc.classify_by_name("geom/13101-bldg-1", "anything"), "geometry")
        self.assertEqual(pc.classify_by_name("geometry/x", "anything"), "geometry")

    def test_title_prefixes_are_front_anchored(self):
        for title, cls in (("Update attributes (Usage): 401 → 402", "attribute"),
                           ("Update building info: Tokyo Station", "attribute"),
                           ("属性修正: 地上階数", "attribute"),
                           ("Attributkorrektur: Geschosse", "attribute"),
                           ("Update textures (2 faces): 13101-bldg-1", "texture"),
                           ("Add textures (1 face): 13101-bldg-1", "texture"),
                           ("テクスチャ更新", "texture"),
                           ("Texturen ergänzt", "texture")):
            self.assertEqual(pc.classify_by_name("feature/x", title), cls, title)
        self.assertIsNone(pc.classify_by_name("feature/x", "Please Update attributes"))  # not front-anchored

    def test_geometry_keywords_anywhere(self):
        for title in ("Fix geometry of the annex", "Corrected building shape", "rebuild after demolition",
                      "幾何の修正", "建物形状の訂正", "建替後の形状", "建て替えに伴う修正"):
            self.assertEqual(pc.classify_by_name("feature/x", title), "geometry", title)

    def test_no_match_is_none(self):
        self.assertIsNone(pc.classify_by_name("feature/storeys", "Fix storeys for 13101-bldg-1"))
        self.assertIsNone(pc.classify_by_name("", ""))


class TestClassify(unittest.TestCase):
    def test_other_when_no_data_changed(self):
        self.assertEqual(pc.classify("docs/readme", "Improve README", data_changed=False), "other")

    def test_administrative_by_trailers(self):
        self.assertEqual(pc.classify("bulk/2026", "Source update 2026", data_changed=True, administrative=True),
                         "administrative")

    def test_unclassified_data_pr_has_no_silent_default(self):
        self.assertEqual(pc.classify("feature/storeys", "Fix storeys", data_changed=True), "unclassified")

    def test_named_class_wins_even_when_administrative(self):
        self.assertEqual(pc.classify("edit/x", "x", data_changed=True, administrative=True), "attribute")

    def test_kind_for_checks(self):
        self.assertEqual(pc.kind_for_checks("texture"), "texture")
        for cls in ("administrative", "other", "unclassified", None):
            self.assertEqual(pc.kind_for_checks(cls), "attribute")


class TestGuide(unittest.TestCase):
    def test_guide_lists_every_literal_and_both_fixes(self):
        text = pc.guide_markdown()
        for cls in pc.NAME_CLASSES:
            self.assertIn(f"`{cls}`", text)
            for lit in pc.BRANCH_PREFIXES[cls] + pc.TITLE_PREFIXES[cls] + pc.TITLE_KEYWORDS[cls]:
                self.assertIn(f"`{lit}`", text, lit)
        self.assertIn("git branch -m edit/", text)
        self.assertIn("Editing the title re-runs the checks", text)
        self.assertIn("section A5", text)
        self.assertTrue(text.startswith("### How to classify"))

    def test_guide_localizes_prose_but_not_literals(self):
        ja = pc.guide_markdown(pc.load_catalog("ja"))
        self.assertIn("この提案の分類のしかた", ja)
        self.assertIn("`edit/`", ja)
        self.assertIn("`Update attributes`", ja)
        de = pc.guide_markdown(pc.load_catalog("de"), advisory=True)
        self.assertTrue(de.startswith("### Hinweis"))
        self.assertIn("`tex/`", de)

    def test_catalogs_carry_every_guide_key(self):
        keys = {"ci.check_classification", "ci.classify_heading", "ci.classify_advisory_heading",
                "ci.classify_intro", "ci.classify_col_class", "ci.classify_col_branch",
                "ci.classify_col_title", "ci.classify_attribute", "ci.classify_texture",
                "ci.classify_geometry", "ci.classify_fix_branch", "ci.classify_fix_title",
                "ci.classify_other", "ci.classify_contract"}
        for lang in ("en", "ja", "de"):
            cat = json.loads((REPO_ROOT / "tools" / "i18n" / "catalogs" / "ci" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertTrue(keys <= set(cat), f"{lang}: missing {keys - set(cat)}")


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def test_exit_code_and_output(self):
        ok = self._run("--branch", "edit/x", "--title", "x", "--data-changed", "true")
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, "attribute"))
        bad = self._run("--branch", "feature/x", "--title", "Fix", "--data-changed", "true")
        self.assertEqual((bad.returncode, bad.stdout.strip()), (1, "unclassified"))
        other = self._run("--branch", "feature/x", "--title", "Docs", "--data-changed", "false")
        self.assertEqual((other.returncode, other.stdout.strip()), (0, "other"))
        kind = self._run("--branch", "feature/x", "--title", "Fix", "--data-changed", "true", "--print", "kind")
        self.assertEqual((kind.returncode, kind.stdout.strip()), (0, "attribute"))

    def test_guide_cli(self):
        out = self._run("--guide", "--lang", "ja", "--tools-dir", str(REPO_ROOT))
        self.assertEqual(out.returncode, 0)
        self.assertIn("この提案の分類のしかた", out.stdout)


class TestCiUsesTheTable(unittest.TestCase):
    """The shell drivers call the script instead of carrying their own literals."""

    def test_drivers_call_the_script(self):
        analysis = (REPO_ROOT / "ci" / "pr_analysis_main.sh").read_text(encoding="utf-8")
        summary = (REPO_ROOT / "ci" / "inspection_summary.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/pr_classification.py", analysis)
        self.assertIn("pr_classification", summary)
        self.assertIn("CLASSIFICATION_OUTCOME", analysis)
        self.assertIn('"classification"', summary)
        for literal in ("PR_TITLE\" == *geometry*", "PR_BRANCH\" == tex/*"):
            self.assertNotIn(literal, analysis, "classification literal duplicated in the shell driver")

    def test_shell_syntax(self):
        for name in ("pr_analysis_main.sh", "inspection_summary.sh"):
            proc = subprocess.run(["bash", "-n", str(REPO_ROOT / "ci" / name)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
