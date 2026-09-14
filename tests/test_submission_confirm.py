# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Confirmation before commit / push / PR (hub-v1.2.1).

The send is one action; before it runs, both editors show the account, the name
recorded with the change, the upload target (the person's copy) and the city the
change is proposed to, and require an explicit "Send as shown".
"""
from __future__ import annotations

import os
import shutil
import unittest

from tests.support import REPO_ROOT, TOKYO, TempHome, accounts, load_app, make_clone

attr = load_app("attr_confirm", "tools/attr_editor/app.py")


class TestSubmissionInfo(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        self._home = TempHome()
        home = self._home.__enter__()
        self.root = make_clone(home / "clone", TOKYO, origin="https://github.com/someone/sample-tokyo-station.git")

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_without_account_the_send_is_blocked_with_facts(self):
        info = attr.Repo(self.root).submission_info()
        self.assertIsNone(info["account"])
        self.assertFalse(info["autoPr"])
        self.assertEqual(info["identity"], {"name": "", "email": ""})
        self.assertEqual(info["origin"], "someone/sample-tokyo-station")
        self.assertEqual(info["upstream"], TOKYO)
        self.assertIn("edit/", info["branchPattern"])

    def test_with_the_citys_account_everything_is_named(self):
        accounts.save_account("tester", "tok", 42)
        accounts.bind_city_login(TOKYO, "tester")
        accounts.apply_clone_identity(self.root, "tester", 42)
        info = attr.Repo(self.root).submission_info()
        self.assertEqual(info["account"], "tester")
        self.assertTrue(info["autoPr"])
        self.assertEqual(info["identity"], {"name": "tester", "email": "42+tester@users.noreply.github.com"})

    def test_hub_handed_account_wins(self):
        accounts.save_account("fromhub", "tok2", 7)
        os.environ["CITYGML_ACCOUNT"] = "fromhub"
        self.assertEqual(attr.Repo(self.root).submission_info()["account"], "fromhub")

    def test_tex_editor_inherits_the_confirmation(self):
        tex = load_app("tex_confirm", "tools/tex_editor/app.py")
        self.assertTrue(issubclass(tex.TexHandler, tex.attr.Handler))  # /api/submission is served by the base
        self.assertIn("submission_info", attr.Handler.do_GET.__code__.co_names)


class TestConfirmationScreens(unittest.TestCase):
    """The editors' screens show the facts and require an explicit "Send as shown" (hub-v1.2.1)."""

    def test_attribute_editor_requires_review_then_send(self):
        h = (REPO_ROOT / "tools" / "attr_editor" / "index.html").read_text(encoding="utf-8")
        for token in ('id="btnReview"', 'id="submitConfirm"', "/api/submission", "editor.btn_review",
                      "editor.btn_send_confirmed", "editor.confirm_account", "editor.confirm_identity",
                      "editor.confirm_origin", "editor.confirm_upstream", "editor.confirm_blocked"):
            self.assertIn(token, h, token)
        # the send button is hidden until the review shows the facts; the review needs the pre-check
        self.assertIn('id="btnSubmit" class="primary" disabled style="display:none"', h)
        self.assertIn("$('btnReview').disabled = !pretestPassed;", h)
        self.assertIn("$('btnSubmit').disabled = blocked;", h)
        self.assertNotIn("editor.btn_send'", h)

    def test_texture_editor_shows_the_facts_before_consent(self):
        h = (REPO_ROOT / "tools" / "tex_editor" / "index.html").read_text(encoding="utf-8")
        for token in ('id="submitConfirm"', "/api/submission", "tex.btn_send_confirmed", "tex.confirm_account",
                      "tex.confirm_identity", "tex.confirm_origin", "tex.confirm_upstream", "tex.confirm_blocked"):
            self.assertIn(token, h, token)
        self.assertIn("submitBlocked || !$('ccAgree').checked", h)
        self.assertLess(h.index('id="submitConfirm"'), h.index('id="btnSubmit"'))


if __name__ == "__main__":
    unittest.main()
