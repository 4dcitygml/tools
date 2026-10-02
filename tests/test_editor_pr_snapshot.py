# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""What the editors send, recorded (v1.5.0 safety net for the client batch).

Both editors create a proposal the same way in outline (fresh base, branch, commit with the
Building trailer, push to the account's fork, POST /pulls). v1.5.0 merges the two pipelines
into one; this test pins what each sends today so that the merge changes nothing a city or
a reviewer sees, unless a difference is decided on purpose. The texture editor's image
baking is stubbed: only the proposal it builds is compared.

Refresh the recording deliberately with UPDATE_SNAPSHOT=1.
"""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_editor_push_paths as pp
from tests.support import REPO_ROOT, TOKYO, load_app, runtime

GOLDEN = REPO_ROOT / "tests" / "fixtures" / "editor_pr_snapshot.json"
git = pp.git


def normalized(text: str) -> str:
    """Timestamps in branch names and trailers vary per run."""
    return re.sub(r"\d{8}-\d{6}", "<time>", text)


class TestEditorPrSnapshot(pp._PushFixture):
    def setUp(self):   # as TestPushWithAccount, without inheriting its tests
        super().setUp()
        self.acc.save_account("alice", "tok-a", 1)
        self.acc.bind_city_login(TOKYO, "alice")
        os.environ["CITYGML_ACCOUNT"] = "alice"
        self.acc.apply_clone_identity(self.clone, "alice", 1)
        self.repo._origin_nwo = lambda: "alice/sample-tokyo-station"

    def tearDown(self):
        os.environ.pop("CITYGML_ACCOUNT", None)
        super().tearDown()

    def capture(self, send) -> dict:
        calls = []

        def api(path, token, method="GET", payload=None, timeout=30):
            # the fixture's upstream is a local directory standing in for the city repository
            calls.append({"method": method, "path": path.replace(str(self.up), "<city>"), "payload": payload})
            return 201, {"html_url": "https://github.com/4dcitygml/sample-tokyo-station/pull/7"}
        with patch.object(runtime, "github_api", api):
            result = send()
        branch = result["branch"]
        return {
            "resultKeys": sorted(result),
            "branch": normalized(branch),
            "commitMessage": normalized(git(self.clone, "log", "-1", "--format=%B", branch)),
            "changedFiles": git(self.clone, "diff", "--name-only", f"{branch}~1", branch).splitlines(),
            "requests": [{**c, "payload": json.loads(normalized(json.dumps(c["payload"], ensure_ascii=False)))
                          if c["payload"] is not None else None} for c in calls if c["method"] != "GET"],
        }

    def send_attribute(self) -> dict:
        return self.capture(lambda: self.repo.create_pr(self.payload))

    def send_texture(self) -> dict:
        tex = load_app("tex_pr_snapshot", "tools/tex_editor/app.py")
        repo = tex.TexRepo(self.clone)
        repo._origin_nwo = lambda: "alice/sample-tokyo-station"
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"
        image = "53394611_bldg_6697_appearance/tex_snapshot.jpg"

        def apply_textures(self, code, gid, images):   # the baking itself is not under test
            gml.write_text(gml.read_text(encoding="utf-8") + "<!-- textured -->", encoding="utf-8")
            target = self.bldg_dir / image
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\xff\xd8\xff\xd9")
            return {"added": [image], "replaced": [{"orig": "(none)", "new": image}], "new": True}
        head = git(self.clone, "rev-parse", "HEAD")
        with patch.object(tex.TexRepo, "_fresh_pr_base", lambda self, rel: head), \
                patch.object(tex.TexRepo, "apply_textures", apply_textures):
            return self.capture(lambda: repo.create_pr({
                "tile": "53394611", "gid": "gml-bldg-1", "images": [], "faceCount": 1,
                "consentCC0": True, "reason": "Photographed from the street on 2026-09-01."}))

    def test_what_the_editors_send_is_unchanged(self):
        got = {"attribute": self.send_attribute()}
        git(self.clone, "checkout", "-q", "main")
        got["texture"] = self.send_texture()
        if os.environ.get("UPDATE_SNAPSHOT") == "1":
            GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(got, json.loads(GOLDEN.read_text(encoding="utf-8")))


    def test_a_send_commits_only_its_own_files(self):
        # D18/S10: the attribute editor committed without a pathspec, so a file another editor had
        # staged in the same clone went into the proposal; the shared send is path-limited
        other = self.clone / "README-draft.md"
        other.write_text("staged by someone else\n", encoding="utf-8")
        git(self.clone, "add", "README-draft.md")
        got = self.send_attribute()
        self.assertEqual(got["changedFiles"], ["city/udx/bldg/53394611_bldg_6697_op.gml"])
        self.assertIn("README-draft.md", git(self.clone, "diff", "--cached", "--name-only"))   # left as it was


    def test_no_send_without_the_clones_own_author(self):
        # git would take the computer's global name and e-mail into a public commit
        git(self.clone, "config", "--local", "--unset", "user.email")
        head = git(self.clone, "rev-parse", "HEAD")
        with self.assertRaisesRegex(RuntimeError, "no author set"):
            self.send_attribute()
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.clone, "status", "--porcelain", "--untracked-files=no"), "")


if __name__ == "__main__":
    unittest.main()
