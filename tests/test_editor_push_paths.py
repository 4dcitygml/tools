# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The editor's send path with and without an account (hub-v1.2.1), on real git repos.

Reuses the upstream/clone fixture of test_upstream_sync: a local upstream, a clone,
and a proposal payload. GitHub's REST API is stubbed; git runs for real.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_upstream_sync as fx
from tests.support import REPO_ROOT, TOKYO, accounts, runtime

attr, git = fx.attr, fx.git


PAYLOAD = {
    "tile": "53394611", "gid": "gml-bldg-1",
    "reason": "現地調査票で地上階数を確認しました。",
    "changes": [{"key": "storeysAboveGround#0", "tag": "storeysAboveGround", "index": 0,
                 "old": "2", "new": "3", "label": "地上階数"}],
    "sourceSelections": [{"key": "storeysAboveGround#0", "code": "801"}],
}


class _PushFixture(fx._SyncFixture):
    """The clone under a temporary HOME (account store, settings; English UI); GitHub REST stubbed."""

    def setUp(self):
        super().setUp()
        self.acc = accounts
        self.api_calls = []
        self.api_response = (201, {"html_url": "https://github.com/4dcitygml/sample-tokyo-station/pull/7"})

        def api(path, token, method="GET", payload=None, timeout=30):
            self.api_calls.append((method, path, token))
            return self.api_response
        self._api = patch.object(runtime, "github_api", api)
        self._api.start()
        self.repo = attr.Repo(self.clone)
        self.payload = dict(PAYLOAD)

    def tearDown(self):
        self._api.stop()
        super().tearDown()


class TestPushWithoutAccount(_PushFixture):
    """No account: never the computer's credentials; a failed push explains the way out."""

    def test_failed_push_names_the_missing_account_and_rolls_back(self):
        git(self.clone, "remote", "set-url", "origin", str(Path(self.temp.name) / "nowhere.git"))
        head = git(self.clone, "rev-parse", "HEAD")
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"
        before = gml.read_text(encoding="utf-8")
        self.assertIsNone(accounts.login_for_clone(self.clone))
        with self.assertRaises(RuntimeError) as ctx:
            self.repo.create_pr(self.payload)
        self.assertIn("No GitHub account is connected", str(ctx.exception))
        self.assertIn("Settings", str(ctx.exception))
        # rolled back: on main, no edit branch, file untouched
        self.assertEqual(git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)
        self.assertEqual(gml.read_text(encoding="utf-8"), before)
        self.assertNotIn("edit/", git(self.clone, "branch", "--list"))

    def test_network_git_never_consults_the_computers_helpers(self):
        args = runtime.git_args(net=True, store=accounts.store_for(accounts.login_for_clone(self.clone)))
        self.assertIn("credential.helper=", args)
        self.assertNotIn("manager", " ".join(args))

    def test_confirmation_blocks_the_send_before_anything_happens(self):
        info = self.repo.submission_info()
        self.assertIsNone(info["account"])
        self.assertFalse(info["autoPr"])


class TestPushWithAccount(_PushFixture):
    """An account bound to the clone's city: identity in the clone, push with the account's store."""

    def setUp(self):
        super().setUp()
        self.acc.save_account("alice", "tok-a", 1)
        self.acc.bind_city_login(TOKYO, "alice")
        os.environ["CITYGML_ACCOUNT"] = "alice"
        self.acc.apply_clone_identity(self.clone, "alice", 1)
        # origin stays the local upstream fixture for the push; GitHub-facing names are the account's fork
        self.repo._origin_nwo = lambda: "alice/sample-tokyo-station"

    def test_send_uses_the_account_end_to_end(self):
        info = self.repo.submission_info()
        self.assertEqual((info["account"], info["autoPr"], info["origin"]), ("alice", True, "alice/sample-tokyo-station"))
        self.assertEqual(info["identity"], {"name": "alice", "email": "1+alice@users.noreply.github.com"})
        result = self.repo.create_pr(self.payload)
        self.assertTrue(result["ok"] and result["pushed"], result)
        self.assertEqual(result.get("prUrl"), "https://github.com/4dcitygml/sample-tokyo-station/pull/7")
        # the commit carries the account's identity, not the computer's
        author = git(self.clone, "log", "-1", "--format=%an <%ae>", result["branch"])
        self.assertEqual(author, "alice <1+alice@users.noreply.github.com>")
        # the proposal was opened with the account's token, from the fork's owner
        method, path, token = self.api_calls[-1]
        self.assertEqual((method, token), ("POST", "tok-a"))
        self.assertTrue(path.endswith("/pulls"))

    def test_shell_identity_overrides_do_not_reach_the_commit(self):
        # A terminal that exports GIT_AUTHOR_EMAIL (or VS Code's askpass) must not steer git:
        # the editors scrub these at start; here the scrubber is applied to the test process.
        saved = {k: os.environ.get(k) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "GIT_ASKPASS")}
        os.environ.update({"GIT_AUTHOR_NAME": "Other Person", "GIT_AUTHOR_EMAIL": "other@example.com",
                           "GIT_COMMITTER_EMAIL": "other@example.com", "GIT_ASKPASS": "/usr/bin/false"})
        try:
            self.acc.scrub_git_env()
            result = self.repo.create_pr(self.payload)
            author = git(self.clone, "log", "-1", "--format=%an <%ae> %cn <%ce>", result["branch"])
            self.assertEqual(author, "alice <1+alice@users.noreply.github.com> alice <1+alice@users.noreply.github.com>")
            self.assertEqual(os.environ.get("GIT_TERMINAL_PROMPT"), "0")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.environ.pop("GIT_TERMINAL_PROMPT", None)

    def test_no_gh_cli_fallback_ever(self):
        for path in ("tools/attr_editor/app.py", "tools/tex_editor/app.py"):
            src = (REPO_ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn('"gh", "pr", "create"', src, path)
            self.assertNotIn("gh pr create", src, path)

    def test_upload_target_must_be_the_accounts_copy(self):
        # origin = the city itself (or someone else's fork) → the confirmation blocks the send
        self.repo._origin_nwo = lambda: "4dcitygml/sample-tokyo-station"
        self.assertFalse(self.repo.submission_info()["originOk"])
        self.repo._origin_nwo = lambda: "alice/sample-tokyo-station"
        git(self.clone, "remote", "set-url", "origin", "git@github.com:alice/sample-tokyo-station.git")
        self.assertFalse(self.repo.submission_info()["originOk"])         # SSH: the account's store cannot apply
        git(self.clone, "remote", "set-url", "origin", "https://github.com/Alice/sample-tokyo-station.git")
        self.assertTrue(self.repo.submission_info()["originOk"])

    def test_city_binding_beats_the_launch_environment(self):
        import json as _json
        (self.clone / "4dcitygml.json").write_text(_json.dumps({"repo": "4dcitygml/sample-tokyo-station"}))
        self.acc.save_account("bob", "tok-b", 2)
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", "bob")   # the hub rebound the city
        self.assertEqual(accounts.login_for_clone(self.clone), "bob")                        # env still says alice
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", None)    # disconnected in the hub
        self.assertIsNone(accounts.login_for_clone(self.clone))                              # the launch value does not revive it

    def test_org_restriction_is_explained_and_the_upload_is_kept(self):
        self.api_response = (403, {"message": "Although you appear to have the correct authorization credentials, "
                                              "the city organization has enabled OAuth App access restrictions"})
        result = self.repo.create_pr(self.payload)
        self.assertTrue(result["ok"] and result["pushed"], result)
        self.assertIsNone(result.get("prUrl"))
        self.assertTrue(result.get("compareUrl"))
        self.assertIn("has not approved this tool", result.get("note") or "")
        self.assertIn("Organization access", result.get("note") or "")


if __name__ == "__main__":
    unittest.main()
