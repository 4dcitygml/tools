#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""docs/client-runtime-contract.md (runtime-v1) stays in sync with the code that
implements it: the environment/CLI channel, the shared settings file, the
folder and zip layout, the discovery endpoints and the update endpoints. The
facts live in tools/runtime.py; the hub and the editors only use them."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import REPO_ROOT, TOKYO, TempHome, accounts, load_app, runtime

DOC = (REPO_ROOT / "docs" / "client-runtime-contract.md").read_text(encoding="utf-8")
hub = load_app("rt_hub", "tools/hub/app.py")


class TestDocumentedChannels(unittest.TestCase):
    def test_environment_and_flags(self):
        for token in ("`CITYGML_UPSTREAM=<owner/repo>`", "`CITYGML_HUB_TAG=<hub-vX.Y.Z>`", "`--repo <path>`",
                      "`--port <n>`", "`--no-browser`", "`CITYGML_LANG`"):
            self.assertIn(token, DOC, token)
        launcher = (REPO_ROOT / "install" / "citygml.sh").read_text(encoding="utf-8")
        for var in ("CITYGML_UPSTREAM", "CITYGML_HUB_TAG"):
            self.assertIn(var, launcher)
        source = (REPO_ROOT / "tools" / "runtime.py").read_text(encoding="utf-8")
        for var in ("CITYGML_UPSTREAM", "CITYGML_HUB_TAG", "CITYGML_TOOLS_DIR"):
            self.assertIn(var, source)

    def test_files_ports_and_endpoints(self):
        for token in ("~/.citygml_attr_editor.json", "citygml-hub/<hub-vX.Y.Z>/program/hub.py",
                      "`8760`", "`8765`", "`8766`", "`GET /api/status`", "`GET /api/repo`",
                      "`GET /api/update`", "`POST /api/update/fetch`", "`POST /api/shortcut`",
                      "citygml-hub-<ver>-macos.zip", "citygml-hub-<ver>-windows-full.zip", "`min_hub`",
                      "`runtime.py`"):
            self.assertIn(token, DOC, token)
        self.assertEqual(hub.DEFAULT_PORT, 8760)
        self.assertEqual({t["port"] for t in hub.TOOLS.values()}, {8765, 8766})
        with TempHome() as home:
            self.assertEqual(runtime.config_path(), home / ".citygml_attr_editor.json")
            self.assertEqual(runtime.auth_dir(), home / ".citygml" / "auth")
            self.assertEqual(runtime.tools_dir(), home / "Documents" / "citygml-tools")
            self.assertEqual(runtime.hubs_dir(), home / "Documents" / "citygml-tools" / "citygml-hub")
        for name, marker in (("citygml.sh", "-macos.zip"), ("citygml.ps1", "-windows-full.zip")):
            self.assertIn(marker, (REPO_ROOT / "install" / name).read_text(encoding="utf-8"), name)

    def test_runtime_symbols_exist(self):
        for name in ("city_key", "clone_city", "requested_city", "remember_clone", "saved_clone_for", "free_port",
                     "running_hub_tag", "hubs_dir", "version_tuple", "launcher_path", "fetch_latest_hub",
                     "read_config", "update_config", "git_exe", "git_args", "github_api", "upstream_url"):
            self.assertTrue(callable(getattr(runtime, name, None)), name)
        for name in ("running_hub_for", "latest_hub_release", "min_hub_of", "create_city_shortcut"):
            self.assertTrue(callable(getattr(hub, name, None)), name)

    def test_shared_modules_travel_in_both_zips(self):
        from tests.test_bundle import build_bundle
        for flavor in ("macos", "windows"):
            names = {arc for _, arc, _ in build_bundle.manifest(REPO_ROOT, flavor)}
            for name in ("runtime.py", "accounts.py", "git_sync.py", "shortcuts.py", "pr_classification.py"):
                self.assertIn(f"{build_bundle.LIB}/{name}", names, name)


class TestSharedSettingsFile(unittest.TestCase):
    """Writers merge; keys they do not own survive — one implementation for every tool."""

    def setUp(self):
        self._home = TempHome()
        self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_every_writer_keeps_the_other_writers_keys(self):
        accounts.save_account("tester", "tok", 1)
        accounts.bind_city_login(TOKYO, "tester")                         # the hub's account screen
        runtime.save_config({"repo": "/x/tokyo", "lang": "ja"})          # the editor after its own setup
        runtime.remember_clone(TOKYO, "/x/tokyo")                         # the hub at the next start
        cfg = runtime.read_config()
        self.assertEqual((cfg["cities"][TOKYO]["login"], cfg["cities"][TOKYO]["repo"], cfg["lang"]),
                         ("tester", "/x/tokyo", "ja"))
        runtime.save_config({"repo": "/x/munich"})
        self.assertEqual(runtime.read_config()["cities"][TOKYO]["login"], "tester")   # nothing dropped

    def test_documented_example_round_trips(self):
        example = json.loads(DOC[DOC.index("```json") + 7:DOC.index("```", DOC.index("```json") + 7)]
                             .replace("/…/", "/tmp/").replace("2026-09-06T…", "2026-09-06T00:00:00+0900"))
        self.assertEqual(set(example), {"repo", "lang", "cities", "legacyReviewed"})
        self.assertEqual(set(next(iter(example["cities"].values()))), {"repo", "last_used", "login"})

    def test_write_failure_never_raises(self):
        runtime.config_path().mkdir()                                     # a directory where the file should be
        runtime.save_config({"repo": "C:/data/sample-tokyo-station"})    # write_text would raise OSError


class TestStatusShape(unittest.TestCase):
    def test_status_carries_the_discovery_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "data_dirs": ["d"]}))
            (root / "d").mkdir(); (root / "d" / "a.gml").write_text("<x/>")
            with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.2.0"}):
                status = hub.Hub(root).status()
        for key in ("repo", "branch", "nwo", "hubTag", "sync", "runtime"):
            self.assertIn(key, status)
        self.assertEqual(Path(status["repo"]).resolve(), root.resolve())
        self.assertEqual(status["hubTag"], "hub-v1.2.0")


if __name__ == "__main__":
    unittest.main()


class TestGitHubApiKeepsLists(unittest.TestCase):
    """GitHub's list endpoints (pulls, files, comments, reviews, commits) answer with a JSON
    list. The shared helper must hand it through unchanged: hub-v1.3.0 turned every list
    into {} and the review queue died with "HTTP 200" (found on the real machine, 2026-09-08).
    The FakeGitHub seam sits above github_api, so this is tested below the seam."""

    def _api(self, body: bytes, status: int = 200):
        from unittest.mock import patch
        import runtime
        with patch.object(runtime, "request", lambda *a, **k: (status, body, "application/json")):
            return runtime.github_api("/repos/o/r/pulls?state=open", "t")

    def test_a_list_body_is_returned_as_a_list(self):
        code, data = self._api(b'[{"number": 2}, {"number": 3}]')
        self.assertEqual((code, data), (200, [{"number": 2}, {"number": 3}]))

    def test_an_object_body_is_returned_as_an_object_and_junk_as_empty(self):
        self.assertEqual(self._api(b'{"message": "Not Found"}', 404), (404, {"message": "Not Found"}))
        self.assertEqual(self._api(b'not json')[1], {})
        self.assertEqual(self._api(b'')[1], {})

    def test_review_queue_reads_open_pulls_through_the_real_helper(self):
        # The hub's review queue over the real github_api, with GitHub's answers faked one
        # level down (runtime.request): a list of open PRs; empty lists / objects elsewhere.
        import json
        from unittest.mock import patch
        import runtime
        from tests.support import TempHome, TOKYO, load_app, make_clone, bind_account, fresh_hub
        hub = load_app("hub_lists_app", "tools/hub/app.py")
        pr = {"number": 2, "title": "x", "state": "open", "draft": False, "user": {"login": "p"}, "labels": [],
              "head": {"sha": "a" * 40, "ref": "edit/b", "repo": {"full_name": "p/sample-tokyo-station"}},
              "base": {"sha": "b" * 40, "ref": "main"}, "updated_at": "2026-09-08T00:00:00Z", "created_at": "2026-09-08T00:00:00Z",
              "html_url": "https://github.com/4dcitygml/sample-tokyo-station/pull/2", "body": ""}
        def fake_request(url, method="GET", headers=None, body=None, timeout=30):
            if "/pulls?state=open" in url:
                return 200, json.dumps([pr] if url.endswith("page=1") else []).encode(), "application/json"
            if "/check-runs" in url:
                return 200, b'{"check_runs": []}', "application/json"
            if url.endswith("/pulls/2"):
                return 200, json.dumps(pr).encode(), "application/json"
            if url.endswith("/user"):
                return 200, b'{"login": "reviewer", "id": 1}', "application/json"
            if any(f"/{part}?" in url for part in ("files", "comments", "reviews", "commits")):
                return 200, b"[]", "application/json"            # list endpoints answer with lists
            return 200, b"{}", "application/json"                # everything else is an object
        with TempHome() as home:
            root = make_clone(home / "clone", TOKYO)
            session = fresh_hub(hub)
            bind_account(TOKYO, "reviewer", "t", 1)
            with patch.object(runtime, "request", fake_request):
                session.start(root, TOKYO, sync=False)
                queue = session.hub.review_queue()
        self.assertTrue(queue.get("ok"), queue)
        self.assertEqual([it["number"] for it in queue.get("items", [])], [2])
