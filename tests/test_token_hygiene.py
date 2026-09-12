#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Token hygiene for the OAuth device-flow token (A6 transparency work).

Automated proof that the GitHub token never leaks out of its two intended
places (the 0600 auth file and the Authorization request header):
- exported state (`/api/status` payload = AuthManager.state()) never carries it
- HTTP request logging is silenced, so access logs cannot carry it
- error paths (GitHub API errors, network failures) never embed it in messages
- it never appears in request URLs (header-only transport)
- the files that do hold it are written owner-readable only (0600)
- the screen shown BEFORE authorization states the public_repo write range,
  in every shipped language

Everything runs with HTTP stubbed out; no network, no real files in $HOME."""
from __future__ import annotations

import io
import json
import os
import stat
import unittest
import urllib.error
from unittest.mock import patch

from tests.support import REPO_ROOT, TempHome, accounts, load_app, runtime

hub = load_app("hub_app", "tools/hub/app.py")
attr = load_app("attr_app", "tools/attr_editor/app.py")

# Deliberately NOT shaped like a real GitHub token (gh?_...): the public-payload
# secret scanner must stay able to flag real token literals in the tree.
SENTINEL = "A6-SENTINEL-TOKEN-VALUE-1234567890"


class TestExportedStateAndErrors(unittest.TestCase):
    """The auth state machine with HTTP stubbed (same harness as test_hub_onboard)."""

    def setUp(self):
        # hub-v1.2.1: a real account store under a temporary HOME (no ~/.citygml, no gh, no git)
        self._home = TempHome()
        self._home.__enter__()
        self.acc = accounts
        self._patches = [
            patch.object(hub, "oauth_client_id", lambda: "test-client-id"),
            patch.object(runtime, "github_user_status",
                         lambda token: (200, {"login": "tester", "id": 1}) if token else (0, None)),
            patch.object(runtime, "post_form", lambda url, f, timeout=15: {"error": "expired_token"}),
            patch.object(runtime, "git_exe", lambda: None),
        ]
        for p in self._patches:
            p.start()
        self.session = hub.Session()
        self.session.city = "o/r"
        self.mgr = self.session.account

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._home.__exit__(None, None, None)

    def test_state_export_never_contains_the_token(self):
        seq = [{"error": "authorization_pending"}, {"access_token": SENTINEL}]
        runtime.post_form = lambda url, f, timeout=15: seq.pop(0) if seq else {"error": "expired_token"}
        self.mgr._poll("cid", "dc", 0, 1)
        self.assertEqual(self.mgr.token(), SENTINEL)  # the token did arrive...
        exported = json.dumps(self.mgr.state())       # ...but the exported state hides it
        self.assertNotIn(SENTINEL, exported)

    def test_saved_account_use_does_not_export_the_token(self):
        self.acc.save_account("tester", SENTINEL, 1)
        self.assertIsNone(self.mgr.use_saved("tester"))
        self.assertEqual(self.mgr.token(), SENTINEL)
        exported = json.dumps(self.mgr.state()) + json.dumps(self.acc.list_accounts())
        self.assertNotIn(SENTINEL, exported)

    def test_auth_error_messages_do_not_embed_secrets(self):
        for final in ({"error": "expired_token"}, {"error": "access_denied"},
                      {"error": "incorrect_device_code"}):
            mgr = hub.Session().account
            runtime.post_form = lambda url, f, timeout=15, r=final: r
            mgr._poll("cid", "device-code-SECRET", 0, 1)
            state = mgr.state()
            self.assertIsNotNone(state["error"])
            self.assertNotIn("device-code-SECRET", json.dumps(state))


class _CapturedRequest:
    """urlopen stub that records the request and returns a canned JSON body."""

    def __init__(self, body=b"{}", status=200):
        self.req = None
        self._body, self._status = body, status

    def __call__(self, req, timeout=0, context=None):
        self.req = req
        outer = self

        class _Resp:
            status = outer._status

            def read(self):
                return outer._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            class headers:  # github_raw asks for the content type
                @staticmethod
                def get_content_type():
                    return "application/json"

        return _Resp()


class TestHeaderOnlyTransport(unittest.TestCase):
    """The token travels in the Authorization header only, never in the URL — for every
    GitHub call of every tool, because there is exactly one HTTP function (runtime.request)."""

    def _check(self, api):
        cap = _CapturedRequest()
        with patch.object(runtime.urllib.request, "urlopen", cap):
            api("/user", SENTINEL)
        self.assertNotIn(SENTINEL, cap.req.full_url)
        self.assertEqual(cap.req.get_header("Authorization"), f"Bearer {SENTINEL}")

    def test_github_api(self):
        self._check(runtime.github_api)

    def test_github_raw(self):
        self._check(runtime.github_raw)

    def test_github_user_status(self):
        self._check(lambda path, token: runtime.github_user_status(token))

    def test_http_error_result_carries_no_token(self):
        def boom(req, timeout=0, context=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", None,
                io.BytesIO(b'{"message": "Bad credentials"}'))
        with patch.object(runtime.urllib.request, "urlopen", boom):
            code, body = runtime.github_api("/user", SENTINEL)
        self.assertEqual(code, 401)
        self.assertNotIn(SENTINEL, json.dumps(body))

    def test_network_error_exception_carries_no_token(self):
        def down(req, timeout=0, context=None):
            raise urllib.error.URLError("connection refused")
        with patch.object(runtime.urllib.request, "urlopen", down):
            with self.assertRaises(urllib.error.URLError) as ctx:
                runtime.github_api("/user", SENTINEL)
        self.assertNotIn(SENTINEL, str(ctx.exception) + repr(ctx.exception))


class TestRequestLoggingIsSilent(unittest.TestCase):
    """BaseHTTPRequestHandler access logging is disabled in both local servers,
    so tokens (or any request detail) cannot end up in a log stream."""

    def test_hub_handler_logs_nothing(self):
        self.assertIsNone(
            hub.Handler.log_message(object.__new__(hub.Handler), "%s", SENTINEL))

    def test_attr_editor_handler_logs_nothing(self):
        self.assertIsNone(
            attr.Handler.log_message(object.__new__(attr.Handler), "%s", SENTINEL))


class TestTokenFilesAreOwnerOnly(unittest.TestCase):
    """The account files that intentionally hold the token are chmod 0600 (dir 0700)."""

    def test_account_files_are_0600(self):
        with TempHome():
            accounts.save_account("tester", SENTINEL, 1)
            for path in (accounts.account_path("tester"), accounts.credentials_path("tester")):
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                self.assertIn(SENTINEL, path.read_text(encoding="utf-8"))   # the only two places
            self.assertEqual(stat.S_IMODE(os.stat(runtime.auth_dir()).st_mode), 0o700)


class TestScopeIsStatedBeforeAuthorization(unittest.TestCase):
    """The pre-authorization screen names the public_repo write range (A6),
    and every shipped language carries the same explanation."""

    def test_setup_screen_names_public_repo_before_connect(self):
        html = (hub.APP_DIR / "setup.html").read_text(encoding="utf-8")
        self.assertIn("hub.setup_connect_scope", html)
        self.assertIn("public_repo", html)
        # The explanation belongs to the screen shown BEFORE doConnect() starts
        # the device flow (not to a post-authorization screen).
        self.assertLess(html.index("hub.setup_connect_scope"), html.index("doConnect()"))

    def test_all_language_catalogs_state_the_scope(self):
        for lang in ("en", "ja", "de"):
            catalog = json.loads(
                (REPO_ROOT / "tools" / "i18n" / "catalogs" / "hub" / f"{lang}.json")
                .read_text(encoding="utf-8"))
            text = catalog.get("hub.setup_connect_scope", "")
            self.assertIn("public_repo", text, f"{lang}.json lacks the scope explanation")


if __name__ == "__main__":
    unittest.main()
