#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/repo_scope.py (Exchange Contract A11): a city repository accepts data,
documents and configuration; code is rejected with guidance; the CI tools pin is the
declaration's `tools` key, verified against the tools-v tags; workflow files change only
under the maintainer label `tooling`."""
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


def git(*args, cwd):
    return subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def tools_repository(path: Path) -> "tuple[str, str, str]":
    """A tools repository as git lists its tags (T11): an annotated tools-v tag, a lightweight one,
    and a hub-v tag that is no tools release. -> (annotated commit, lightweight commit, hub commit)."""
    git("init", "-q", cwd=path)
    git("config", "user.email", "t@example.com", cwd=path)
    git("config", "user.name", "t", cwd=path)
    shas = []
    for name in ("one", "two", "three"):
        git("commit", "-q", "--allow-empty", "-m", name, cwd=path)
        shas.append(git("rev-parse", "HEAD", cwd=path))
    git("tag", "-a", "tools-v1.2.0", shas[0], "-m", "release", cwd=path)
    git("tag", "tools-v1.2.1", shas[1], cwd=path)
    git("tag", "-a", "hub-v1.2.0", shas[2], "-m", "hub", cwd=path)
    return shas[0], shas[1], shas[2]


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
        self.ok, self.light, self.bad = tools_repository(Path(self._tmp2.name))
        os.environ["CITYGML_TOOLS_GIT_URL"] = self._tmp2.name

    def tearDown(self):
        os.environ.pop("CITYGML_TOOLS_GIT_URL", None)
        self._tmp.cleanup()
        self._tmp2.cleanup()

    def test_tools_tags_come_from_git(self):
        # T11: git ls-remote lists every tag (the API's /tags read one page of 100); an annotated
        # tag counts by its commit, a hub-v tag is no tools release, an unreadable remote is None
        self.assertEqual(rs.tools_tags("4dcitygml/tools"), {self.ok: "tools-v1.2.0", self.light: "tools-v1.2.1"})
        os.environ["CITYGML_TOOLS_GIT_URL"] = str(Path(self._tmp2.name) / "missing")
        self.assertIsNone(rs.tools_tags("4dcitygml/tools"))

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
        cfg = rs.city_declaration.read(self.repo)
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

    def test_no_layout_is_guessed(self):
        # v2.0.0 (1-2): data is what the city declares; PLATEAU's */udx/ layout is no default any
        # more (before, a udx/ folder outside the declared data_dirs counted as data)
        head = self._commit({"13101_x/udx/bldg/a.gml": "<z/>"})
        self.assertEqual(self._rows(head)["13101_x/udx/bldg/a.gml"]["category"], "rejected")
        self.assertTrue(rs.is_data("13101_x/udx/bldg/a.gml", []))      # a city that declares none: *.gml
        self.assertFalse(rs.is_data("13101_x/udx/bldg/a.jpg", []))     # ... and nothing else

    def test_edition_artifacts_are_the_schema_category_where_the_pack_has_them(self):
        # U11 / D-b2: the commit gate's schema-update accepted codelists/ and schemas/, which this
        # gate rejected (unless inside data_dirs): one owner now, the pack naming the folders
        files = {"codelists/Building_usage.xml": "<c/>", "schemas/iur.xsd": "<s/>"}
        rows = self._rows(self._commit(files))
        self.assertEqual({r["category"] for r in rows.values()}, {"rejected"})     # plain CityGML: none
        rows = self._rows(self._commit({**files, **self.declare(dialect="plateau")}))
        self.assertEqual((rows["codelists/Building_usage.xml"]["category"], rows["schemas/iur.xsd"]["category"]),
                         ("schema", "schema"))
        self.assertTrue(all(r["ok"] for r in rows.values()), rows)

    def test_a_type_change_is_a_change(self):
        # D-b7: --diff-filter=ACMRD left out a file turned into a symbolic link (type change T),
        # so the gate never saw it; every change is listed now
        target = self.repo / "lod2_citygml" / "a.gml"
        target.unlink()
        target.symlink_to("../install/start-mac.command")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "link", cwd=self.repo)
        head = git("rev-parse", "HEAD", cwd=self.repo)
        self.assertIn("lod2_citygml/a.gml", self._rows(head))

    def declare(self, **keys) -> dict:
        return {"4dcitygml.json":
                json.dumps({"repo": "o/r", "data_dirs": ["lod2_citygml"], "logo": "logo.png", **keys})}

    def test_the_pin_is_the_declarations_tools_key(self):
        # F2: the CI tools pin is `tools` in 4dcitygml.json, checked against the tools-v tags; the tag
        # is resolved when shown, never stored
        head = self._commit(self.declare(tools=self.ok))
        row = self._rows(head)["4dcitygml.json"]
        self.assertEqual((row["ok"], row["category"]), (True, "pin"))
        self.assertIn("(tools-v1.2.0)", row["note"])
        lightweight = self._commit(self.declare(tools=self.light))
        self.assertIn("(tools-v1.2.1)", rs.classify(self.repo, head, lightweight, ["4dcitygml.json"],
                                                     rs.city_declaration.read(self.repo), set())[0]["note"])

    def test_a_changed_declaration_must_conform_to_its_schema(self):
        # what the PR writes must be right; what was there before is tolerated; the readers still ignore unknown keys
        head = self._commit(self.declare(colour="red"))                 # changed and invalid: names the key
        row = self._rows(head)["4dcitygml.json"]
        self.assertFalse(row["ok"])
        self.assertIn("unexpected property colour", row["note"])
        self.assertEqual(rs.city_declaration.read(self.repo).data_dirs, ["lod2_citygml"])   # the reader ignores it
        fixed = self._commit(self.declare(min_hub="hub-v2.0.0-rc.1"))   # changed and valid (a prerelease pin conforms)
        self.assertTrue(self._rows(fixed)["4dcitygml.json"]["ok"])
        self.base = head                                                # the base already had `colour`:
        tolerated = self._commit(self.declare(colour="red", lang="ja"))   # a PR that changes another key
        self.assertTrue(self._rows(tolerated)["4dcitygml.json"]["ok"])
        worse = self._commit(self.declare(colour="red", crs="somewhere"))   # a new violation still fails
        self.assertIn("$.crs", self._rows(worse)["4dcitygml.json"]["note"])
        broken = self._commit({"4dcitygml.json": "{not json"})
        self.assertIn("not valid JSON", self._rows(broken)["4dcitygml.json"]["note"])

    def test_the_example_is_not_judged(self):
        head = self._commit({"4dcitygml.json.example": json.dumps({"repo": "<owner>/<name>", "id": "<code>"})})
        self.assertTrue(self._rows(head)["4dcitygml.json.example"]["ok"])

    def test_a_pin_that_is_no_release_or_missing_is_refused(self):
        head = self._commit(self.declare(tools=self.bad))                 # a hub-v tag is no tools release
        row = self._rows(head)["4dcitygml.json"]
        self.assertEqual((row["ok"], row["category"]), (False, "pin"))
        pinned = self._commit(self.declare(tools=self.ok))
        removed = self._commit(self.declare())
        row = rs.classify(self.repo, pinned, removed, ["4dcitygml.json"], rs.city_declaration.read(self.repo), set())[0]
        self.assertEqual((row["ok"], row["category"]), (False, "pin"))
        self.assertIn("must name the tools commit", row["note"])
        # a declaration change that leaves the pin as it is: configuration
        again = self._commit(self.declare(lang="de"))
        self.assertEqual(rs.classify(self.repo, removed, again, ["4dcitygml.json"],
                                     rs.city_declaration.read(self.repo), set())[0]["category"], "config")

    def test_a_workflow_pin_line_is_no_pin_any_more(self):
        # before F2 a workflow whose only change was its CITYGML_TOOLS_REF line was the pin
        head = self._commit({".github/workflows/pr-analysis.yml":
                             "name: x\nenv:\n  CITYGML_TOOLS_REF: " + self.ok + "   # tools-v1.2.0\njobs: {}\n"})
        row = self._rows(head)[".github/workflows/pr-analysis.yml"]
        self.assertEqual((row["ok"], row["category"]), (False, "rejected"))
        self.assertIn("`tools` key of 4dcitygml.json", row["note"])

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


class TestSharedGit(unittest.TestCase):
    """scripts/repo_git.py: the one git wrapper of the scripts."""

    def test_text_bytes_strip_and_error(self):
        from scripts.repo_git import GitError, git
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([GIT, "init", "-q", d], check=True)
            self.assertEqual(git(d, "rev-parse", "--is-inside-work-tree"), "true\n")
            self.assertEqual(git(d, "rev-parse", "--is-inside-work-tree", strip=True), "true")
            self.assertEqual(git(d, "rev-parse", "--is-inside-work-tree", binary=True), b"true\n")
            with self.assertRaises(GitError) as ctx:
                git(d, "rev-parse", "--verify", "no-such-ref")
            err = ctx.exception
            self.assertIsInstance(err, RuntimeError)                 # scripts that catch RuntimeError
            self.assertIsInstance(err, subprocess.CalledProcessError)  # scripts that catch CalledProcessError
            self.assertTrue(str(err).startswith("git rev-parse --verify no-such-ref:"), str(err))
