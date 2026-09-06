#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/repo_scope.py (Exchange Contract A11): a city repository accepts data,
documents and configuration; code is rejected with guidance; workflow files may
change only their verified CITYGML_TOOLS_REF pin; maintainers can override with the
`tooling` label."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import repo_scope as rs  # noqa: E402

GIT = shutil.which("git")
TOOLS_SHA_OK = "a" * 40
TOOLS_SHA_BAD = "b" * 40


def git(*args, cwd):
    return subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipUnless(GIT, "git not available")
class TestRepoScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git("init", "-q", "-b", "main", str(self.repo), cwd=self.repo)
        git("config", "user.email", "t@example.invalid", cwd=self.repo)
        git("config", "user.name", "t", cwd=self.repo)
        (self.repo / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "data_dirs": ["lod2_citygml"], "logo": "logo.png"}))
        (self.repo / "lod2_citygml").mkdir()
        (self.repo / "lod2_citygml" / "a.gml").write_text("<x/>")
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        (self.repo / ".github" / "workflows" / "pr-analysis.yml").write_text(
            "name: x\nenv:\n  CITYGML_TOOLS_REF: " + "0" * 40 + "   # tools-v1.1.0\njobs: {}\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "guide.md").write_text("hi")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "base", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo)
        self._tmp2 = tempfile.TemporaryDirectory()
        self.tags = Path(self._tmp2.name) / "tags.json"
        self.tags.write_text(json.dumps([{"name": "tools-v1.2.0", "commit": {"sha": TOOLS_SHA_OK}},
                                         {"name": "hub-v1.2.0", "commit": {"sha": TOOLS_SHA_BAD}}]))
        os.environ["CITYGML_TOOLS_TAGS_JSON"] = str(self.tags)

    def tearDown(self):
        os.environ.pop("CITYGML_TOOLS_TAGS_JSON", None)
        self._tmp.cleanup()
        self._tmp2.cleanup()

    def _commit(self, changes: dict, message="change"):
        for rel, text in changes.items():
            p = self.repo / rel
            if text is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", message, cwd=self.repo)
        return git("rev-parse", "HEAD", cwd=self.repo)

    def _rows(self, head, labels=()):
        files = rs.changed_files(self.repo, self.base, head)
        cfg = rs.city_config(self.repo)
        removed = rs.deleted_files(self.repo, self.base, head)
        return {r["path"]: r for r in rs.classify(self.repo, self.base, head, files, cfg, set(labels), removed=removed)}

    def test_data_docs_config_accepted(self):
        head = self._commit({"lod2_citygml/a.gml": "<y/>", "docs/guide.md": "hello", "theme.json": "{}",
                             "README.md": "r", "logo.png": "x", ".github/CODEOWNERS": "* @x", "provenance/x.json": "{}"})
        rows = self._rows(head)
        self.assertTrue(all(r["ok"] for r in rows.values()), rows)
        self.assertEqual(rows["lod2_citygml/a.gml"]["category"], "data")
        self.assertEqual(rows["provenance/x.json"]["category"], "data")
        self.assertEqual(rows["theme.json"]["category"], "config")
        self.assertEqual(rows["README.md"]["category"], "docs")

    def test_code_anywhere_is_rejected_with_reason(self):
        head = self._commit({"install/start-mac.command": "#!/bin/bash", "tools/x.py": "print()",
                             "docs/run.sh": "x", "lod2_citygml/helper.py": "x"})
        rows = self._rows(head)
        for path in rows:
            self.assertFalse(rows[path]["ok"], path)
            self.assertEqual(rows[path]["category"], "rejected")
        self.assertIn("executable code", rows["docs/run.sh"]["note"])

    def test_plateau_layout_counts_as_data(self):
        head = self._commit({"13101_x/udx/bldg/a.gml": "<z/>"})
        self.assertEqual(self._rows(head)["13101_x/udx/bldg/a.gml"]["category"], "data")

    def test_pin_only_workflow_change_verified_against_tools_tags(self):
        head = self._commit({".github/workflows/pr-analysis.yml":
                             "name: x\nenv:\n  CITYGML_TOOLS_REF: " + TOOLS_SHA_OK + "   # tools-v1.2.0\njobs: {}\n"})
        row = self._rows(head)[".github/workflows/pr-analysis.yml"]
        self.assertTrue(row["ok"]); self.assertEqual(row["category"], "pin"); self.assertIn("tools-v", row["note"])

    def test_pin_to_unknown_commit_is_rejected(self):
        head = self._commit({".github/workflows/pr-analysis.yml":
                             "name: x\nenv:\n  CITYGML_TOOLS_REF: " + TOOLS_SHA_BAD + "\njobs: {}\n"})
        row = self._rows(head)[".github/workflows/pr-analysis.yml"]
        self.assertFalse(row["ok"]); self.assertEqual(row["category"], "pin")

    def test_other_workflow_edits_need_the_tooling_label(self):
        head = self._commit({".github/workflows/pr-analysis.yml": "name: y\nenv:\n  CITYGML_TOOLS_REF: " + "0" * 40 + "\njobs: {}\n",
                             ".github/scripts/publish.py": "print()"})
        rows = self._rows(head)
        self.assertFalse(rows[".github/workflows/pr-analysis.yml"]["ok"])
        self.assertFalse(rows[".github/scripts/publish.py"]["ok"])
        rows = self._rows(head, labels=["tooling"])
        self.assertTrue(rows[".github/workflows/pr-analysis.yml"]["ok"])
        self.assertEqual(rows[".github/scripts/publish.py"]["category"], "tooling")

    def test_removing_files_is_always_accepted(self):
        # A11: a deleted file cannot run — retiring install/ scripts or a workflow needs no label.
        (self.repo / "install").mkdir(); (self.repo / "install" / "start-mac.command").write_text("#!/bin/bash")
        git("add", "-A", cwd=self.repo); git("commit", "-qm", "add install", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo)
        head = self._commit({".github/workflows/pr-analysis.yml": None, "install/start-mac.command": None})
        rows = self._rows(head)
        self.assertTrue(rows[".github/workflows/pr-analysis.yml"]["ok"])
        self.assertEqual(rows["install/start-mac.command"]["category"], "removed")

    def test_config_templates_are_configuration(self):
        head = self._commit({"4dcitygml.json.example": "{}", "theme.json.example": "{}"})
        rows = self._rows(head)
        self.assertEqual(rows["4dcitygml.json.example"]["category"], "config")
        self.assertEqual(rows["theme.json.example"]["category"], "config")

    def test_cli_exit_codes_and_json(self):
        head = self._commit({"docs/x.md": "ok"})
        out = self.repo / "scope.json"
        proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "repo_scope.py"), "--repo", str(self.repo),
                               "--base-sha", self.base, "--head-sha", head, "--json-output", str(out)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(out.read_text())["ok"])
        head2 = self._commit({"tools/x.sh": "x"})
        proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "repo_scope.py"), "--repo", str(self.repo),
                               "--base-sha", self.base, "--head-sha", head2], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("A11", proc.stdout)


if __name__ == "__main__":
    unittest.main()
