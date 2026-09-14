# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""End-to-end account flows against a real hub HTTP server (hub-v1.2.1).

The hub is started in-process on a free port with GitHub stubbed out, and the
requests a browser would make are replayed: hand-over screen → account choice →
copy → dashboard; account switch with origin re-pointing; disconnect; revoked
token; forget. Files go to a temporary HOME-like directory only.
"""
from __future__ import annotations

import json
import shutil
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.support import TOKYO, FakeGitHub, TempHome, accounts, fresh_hub, load_app, make_clone, runtime

hub = load_app("hub_flows", "tools/hub/app.py")


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestAccountFlows(unittest.TestCase):
    def setUp(self):
        # account store, settings and the clone all live under a temporary HOME
        self._home = TempHome(city=TOKYO)
        self.home = self._home.__enter__()
        self.acc = accounts
        self.gh = FakeGitHub()
        self._patches = [
            self.gh.install(),
            patch.object(accounts, "machine_token", lambda run=None: "tok-m"),
            patch.object(hub, "oauth_client_id", lambda: "cid"),
            patch.object(hub.time, "sleep", lambda s: None),   # create_fork waits for the fork to materialise
        ]
        for p in self._patches:
            p.start()
        fresh_hub(hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in reversed(self._patches):
            p.stop()
        fresh_hub(hub)
        self._home.__exit__(None, None, None)

    # -- helpers --
    def get(self, path, raw=False):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            data = r.read()
        return data.decode("utf-8") if raw else json.loads(data)

    def post(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def make_clone(self, origin="https://github.com/alice/sample-tokyo-station.git"):
        return make_clone(self.home / "clone", TOKYO, origin=origin)

    def activate(self, root):
        hub.SESSION.start(root, TOKYO, sync=False)

    # -- setup mode: hand-over, choice, copy --
    def test_setup_mode_hand_over_then_choice_then_copy(self):
        runtime.legacy_credentials_path().write_text("https://x:y@github.com\n")
        hub.SESSION.start(None, TOKYO)
        page = self.get("/", raw=True)
        self.assertIn('"MODE": "setup"', page)
        st = self.get("/api/setup/status")
        self.assertEqual((st["mode"], st["chosen"], st["login"]), ("setup", False, None))
        self.assertTrue(st["legacy"]["plainStore"])
        code, r = self.post("/api/auth/legacy", {"remove": True})
        self.assertEqual(r["removed"], ["plainStore"])
        self.assertTrue(r["status"]["legacyReviewed"])
        # this computer's CLI sign-in, on request only
        code, r = self.post("/api/auth/machine")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["login"], "mach")
        self.assertEqual(self.acc.city_login("4dcitygml/sample-tokyo-station"), "mach")
        self.assertIsNone(r["status"]["fork"])           # mach has no copy yet
        code, r = self.post("/api/setup/fork")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["fork"], "mach/sample-tokyo-station")

    def test_unknown_and_revoked_choices_are_explained(self):
        hub.SESSION.start(None, TOKYO)
        code, r = self.post("/api/auth/use", {"login": "nobody"})
        self.assertFalse(r["ok"])
        self.assertIn("Choose one of the options", r["error"])
        self.acc.save_account("ghost", "dead-token", 9)
        code, r = self.post("/api/auth/use", {"login": "ghost"})
        self.assertFalse(r["ok"])
        self.assertIn("revoked", r["error"])
        self.assertEqual(self.acc.list_accounts(), [])
        self.gh.offline = True
        self.acc.save_account("alice", "tok-a", 1)
        code, r = self.post("/api/auth/use", {"login": "alice"})
        self.assertFalse(r["ok"])
        self.assertIn("press the same choice again", r["error"])
        self.assertEqual(self.acc.token_for("alice"), "tok-a")   # kept
        self.assertEqual(code, 200)                               # never a 500 offline
        self.gh.offline = False
        self.post("/api/auth/use", {"login": "alice"})
        self.gh.offline = True
        hub.SESSION.fork.clear()
        st = self.get("/api/setup/status")                        # bound, but GitHub cannot be asked
        self.assertEqual((st["chosen"], st["bound"], st["forkChecked"]), (True, "alice", False))

    # -- account mode: an existing clone without a recorded account --
    def test_account_mode_choice_repoints_origin_and_reaches_the_dashboard(self):
        root = self.make_clone(origin="https://github.com/olduser/sample-tokyo-station.git")
        self.acc.save_account("alice", "tok-a", 1)
        self.activate(root)
        self.assertIn('"MODE": "account"', self.get("/", raw=True))
        self.assertEqual(self.get("/api/status")["account"], None)
        code, r = self.post("/api/auth/use", {"login": "alice"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["fork"], "alice/sample-tokyo-station")
        # the dashboard is served now, origin follows the account, identity is local
        self.assertIn("<header>", self.get("/", raw=True))
        self.assertEqual(hub.SESSION.hub.nwo(), "alice/sample-tokyo-station")
        status = self.get("/api/status")
        self.assertEqual((status["account"], status["gitUser"], status["gitEmail"]),
                         ("alice", "alice", "1+alice@users.noreply.github.com"))
        # switch to bob (no copy yet): the copy step, then origin moves again
        code, r = self.post("/api/auth/disconnect")
        self.assertIn('"MODE": "account"', self.get("/", raw=True))
        self.acc.save_account("bob", "tok-b", 2)
        code, r = self.post("/api/auth/use", {"login": "bob"})
        self.assertTrue(r["ok"], r)
        self.assertIsNone(r["status"]["fork"])
        self.assertIn('"MODE": "account"', self.get("/", raw=True))   # bound, but no copy yet: the copy step, not the dashboard
        code, r = self.post("/api/setup/fork")
        self.assertEqual(r["status"]["fork"], "bob/sample-tokyo-station")
        self.assertEqual(hub.SESSION.hub.nwo(), "bob/sample-tokyo-station")
        self.assertEqual(self.get("/api/status")["gitEmail"], "2+bob@users.noreply.github.com")

    def test_settings_and_forget(self):
        root = self.make_clone()
        self.acc.save_account("alice", "tok-a", 1)
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", "alice")
        runtime.save_config({"repo": str(root)})
        self.activate(root)
        self.assertEqual(hub.SESSION.login, "alice")         # recorded account adopted at start
        s = self.get("/api/settings")
        self.assertEqual((s["login"], s["fork"], Path(s["clone"]).resolve()), ("alice", "alice/sample-tokyo-station", root.resolve()))
        self.assertEqual(s["identity"]["email"], "1+alice@users.noreply.github.com")
        self.assertIn("Removing the tools", self.get("/settings.html", raw=True))
        self.gh.offline = True
        hub.SESSION.account.forget_user()
        hub.SESSION.fork.clear()
        s = self.get("/api/settings")                          # cannot be confirmed: no error, account kept
        self.assertEqual((s["chosen"], s["login"], s["fork"]), (True, None, None))
        self.gh.offline = False
        code, r = self.post("/api/settings/forget")
        cfg = runtime.read_config()
        self.assertNotIn("4dcitygml/sample-tokyo-station", cfg.get("cities", {}))
        self.assertNotIn("repo", cfg)
        self.assertIn('"MODE": "account"', self.get("/", raw=True))

    def test_revoked_token_while_running_unbinds_and_explains(self):
        root = self.make_clone()
        self.acc.save_account("alice", "tok-a", 1)
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", "alice")
        self.activate(root)
        del self.gh.tokens["tok-a"]                          # revoked on GitHub
        st = self.get("/api/status")
        self.assertIsNone(st["github"]["login"])
        self.assertIn("no longer accepts", st["github"]["error"])
        self.assertEqual(self.acc.list_accounts(), [])
        self.assertIn('"MODE": "account"', self.get("/", raw=True))

    def test_delete_account_from_settings(self):
        self.acc.save_account("alice", "tok-a", 1)
        self.acc.save_account("bob", "tok-b", 2)
        hub.SESSION.start(None, TOKYO)
        self.post("/api/auth/use", {"login": "alice"})
        code, r = self.post("/api/accounts/delete", {"login": "bob"})
        self.assertEqual([a["login"] for a in self.acc.list_accounts()], ["alice"])
        code, r = self.post("/api/accounts/delete", {"login": "alice"})   # the bound one: unbinds too
        self.assertEqual(self.acc.list_accounts(), [])
        self.assertFalse(self.get("/api/setup/status")["chosen"])


if __name__ == "__main__":
    unittest.main()
