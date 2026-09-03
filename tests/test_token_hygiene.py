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

import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)

_attr_spec = importlib.util.spec_from_file_location(
    "attr_app", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_attr_spec)
_attr_spec.loader.exec_module(attr)

# Deliberately NOT shaped like a real GitHub token (gh?_...): the public-payload
# secret scanner must stay able to flag real token literals in the tree.
SENTINEL = "A6-SENTINEL-TOKEN-VALUE-1234567890"


class TestExportedStateAndErrors(unittest.TestCase):
    """The auth state machine with HTTP stubbed (same harness as test_hub_onboard)."""

    def setUp(self):
        self.mgr = hub.AuthManager()
        self._post, self._user = hub._post_form, hub.github_user
        self._cid = hub.oauth_client_id
        hub.oauth_client_id = lambda: "test-client-id"
        hub.github_user = lambda token: {"login": "tester", "id": 1} if token else None
        self._save, self._cred, self._ident = (
            hub.save_token, hub.write_git_credentials, hub.apply_git_identity)
        hub.save_token = lambda t: None
        hub.write_git_credentials = lambda t: None
        hub.apply_git_identity = lambda u: None
        self._load = hub.load_token
        hub.load_token = lambda: ""

    def tearDown(self):
        (hub._post_form, hub.github_user, hub.oauth_client_id) = (self._post, self._user, self._cid)
        (hub.save_token, hub.write_git_credentials, hub.apply_git_identity) = (
            self._save, self._cred, self._ident)
        hub.load_token = self._load

    def test_state_export_never_contains_the_token(self):
        seq = [{"error": "authorization_pending"}, {"access_token": SENTINEL}]
        hub._post_form = lambda url, f, timeout=15: seq.pop(0) if seq else {"error": "expired_token"}
        self.mgr._poll("cid", "dc", 0, 1)
        self.assertEqual(self.mgr.token(), SENTINEL)  # the token did arrive...
        exported = json.dumps(self.mgr.state())       # ...but the exported state hides it
        self.assertNotIn(SENTINEL, exported)

    def test_saved_token_reuse_does_not_export_the_token(self):
        hub.load_token = lambda: SENTINEL
        exported = json.dumps(self.mgr.state())
        self.assertNotIn(SENTINEL, exported)

    def test_auth_error_messages_do_not_embed_secrets(self):
        for final in ({"error": "expired_token"}, {"error": "access_denied"},
                      {"error": "incorrect_device_code"}):
            mgr = hub.AuthManager()
            hub._post_form = lambda url, f, timeout=15, r=final: r
            mgr._poll("cid", "device-code-SECRET", 0, 1)
            state = mgr.state()
            self.assertIsNotNone(state["error"])
            self.assertNotIn("device-code-SECRET", json.dumps(state))


class _CapturedRequest:
    """urlopen stub that records the request and returns a canned JSON body."""

    def __init__(self, body=b"{}", status=200):
        self.req = None
        self._body, self._status = body, status

    def __call__(self, req, timeout=0):
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

            class headers:  # gh_raw asks for the content type
                @staticmethod
                def get_content_type():
                    return "application/json"

        return _Resp()


class TestHeaderOnlyTransport(unittest.TestCase):
    """The token travels in the Authorization header only, never in the URL."""

    def _check(self, module, api):
        cap = _CapturedRequest()
        orig = module.urllib.request.urlopen
        module.urllib.request.urlopen = cap
        try:
            api("/user", SENTINEL)
        finally:
            module.urllib.request.urlopen = orig
        self.assertNotIn(SENTINEL, cap.req.full_url)
        self.assertEqual(cap.req.get_header("Authorization"), f"Bearer {SENTINEL}")

    def test_hub_gh_api(self):
        self._check(hub, hub.gh_api)

    def test_hub_gh_raw(self):
        self._check(hub, hub.gh_raw)

    def test_attr_editor_github_api(self):
        self._check(attr, attr.github_api)

    def test_http_error_result_carries_no_token(self):
        def boom(req, timeout=0):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", None,
                io.BytesIO(b'{"message": "Bad credentials"}'))
        orig = hub.urllib.request.urlopen
        hub.urllib.request.urlopen = boom
        try:
            code, body = hub.gh_api("/user", SENTINEL)
        finally:
            hub.urllib.request.urlopen = orig
        self.assertEqual(code, 401)
        self.assertNotIn(SENTINEL, json.dumps(body))

    def test_network_error_exception_carries_no_token(self):
        def down(req, timeout=0):
            raise urllib.error.URLError("connection refused")
        orig = hub.urllib.request.urlopen
        hub.urllib.request.urlopen = down
        try:
            with self.assertRaises(urllib.error.URLError) as ctx:
                hub.gh_api("/user", SENTINEL)
        finally:
            hub.urllib.request.urlopen = orig
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
    """The two files that intentionally hold the token are chmod 0600."""

    def test_auth_file_is_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = hub.AUTH_PATH
            hub.AUTH_PATH = Path(tmp) / "auth.json"
            try:
                hub.save_token(SENTINEL)
                mode = stat.S_IMODE(os.stat(hub.AUTH_PATH).st_mode)
            finally:
                hub.AUTH_PATH = orig
            self.assertEqual(mode, 0o600)

    def test_git_credentials_file_is_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig_path, orig_git = hub.GIT_CRED_PATH, hub.git_cmd
            hub.GIT_CRED_PATH = Path(tmp) / "credentials"
            hub.git_cmd = lambda: ("/usr/bin/git", True)  # pretend the bundled git is active
            try:
                hub.write_git_credentials(SENTINEL)
                mode = stat.S_IMODE(os.stat(hub.GIT_CRED_PATH).st_mode)
            finally:
                hub.GIT_CRED_PATH, hub.git_cmd = orig_path, orig_git
            self.assertEqual(mode, 0o600)


class TestScopeIsStatedBeforeAuthorization(unittest.TestCase):
    """The pre-authorization screen names the public_repo write range (A6),
    and every shipped language carries the same explanation."""

    def test_setup_screen_names_public_repo_before_connect(self):
        html = hub.SETUP_HTML
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
