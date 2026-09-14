# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""tools/accounts.py — one GitHub account per city, nothing global (hub-v1.2.1).

Every test redirects the module's file locations into a temporary directory, so
neither ~/.citygml nor ~/.citygml_attr_editor.json nor the real Git configuration
is touched.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import TempHome, runtime, accounts

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.tmp = self._home.__enter__()
        self.acc = accounts

    def tearDown(self):
        self._home.__exit__(None, None, None)


class TestAccountFiles(_Tmp):
    def test_login_is_validated_before_it_becomes_a_file_name(self):
        for bad in ("", "../x", "a/b", "x" * 40, "-lead", "sp ace", None):
            with self.assertRaises(ValueError):
                self.acc.safe_login(bad)
        self.assertEqual(self.acc.safe_login("City-Data-Walker"), "City-Data-Walker")

    def test_save_load_list_delete(self):
        rec = self.acc.save_account("tester", "tok-1", 42, "Tester")
        self.assertEqual(rec["login"], "tester")
        self.assertNotIn("token", rec)
        self.assertEqual(self.acc.token_for("tester"), "tok-1")
        self.assertEqual(self.acc.load_account("tester")["id"], 42)
        self.assertEqual([a["login"] for a in self.acc.list_accounts()], ["tester"])
        self.assertNotIn("token", json.dumps(self.acc.list_accounts()))
        self.assertTrue(self.acc.delete_account("tester"))
        self.assertEqual(self.acc.token_for("tester"), "")
        self.assertEqual(self.acc.list_accounts(), [])
        self.assertFalse(self.acc.credentials_path("tester").exists())

    def test_files_are_owner_only(self):
        self.acc.save_account("tester", "tok-1", 42)
        for path in (self.acc.account_path("tester"), self.acc.credentials_path("tester")):
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(runtime.auth_dir()).st_mode), 0o700)

    def test_credential_store_has_git_format_and_never_reaches_argv(self):
        self.acc.save_account("tester", "tok-1", 42)
        # bytes, not text: a text-mode read would hide a CR LF written on Windows
        self.assertEqual(self.acc.credentials_path("tester").read_bytes(),
                         b"https://x-access-token:tok-1@github.com\n")
        args = runtime.git_args(net=True, store=self.acc.store_for("tester"))
        self.assertEqual(args[1:3], ["-c", "credential.helper="])
        self.assertTrue(args[4].startswith("credential.https://github.com.helper=store --file="))
        self.assertNotIn("tok-1", " ".join(args))
        args_nobody = runtime.git_args(net=True, store=self.acc.store_for("nobody"))
        self.assertEqual(args_nobody[1:3], ["-c", "credential.helper="])
        args_none = runtime.git_args(net=True, store=self.acc.store_for(None))
        self.assertEqual(args_none[1:3], ["-c", "credential.helper="])

    def test_store_path_with_spaces_is_quoted(self):
        path_with_space = self.tmp / "with space" / "auth"
        path_with_space.mkdir(parents=True)
        with patch.object(runtime, "auth_dir", return_value=path_with_space):
            self.acc.save_account("tester", "tok-1", 42)
            args = runtime.git_args(net=True, store=self.acc.store_for("tester"))
            helper = args[4] if len(args) > 4 else ""
            self.assertIn("'", helper)


class TestCredentialStoreLineEndings(_Tmp):
    """The store must end its line with LF on every platform (2026-09-11 Windows report:
    hub-v1.3.1 wrote it in text mode, Windows turned the LF into CR LF, git's credential
    store ignored the line and a push failed with "could not read Username ... terminal
    prompts disabled"). Verified below against git itself, the way a push asks for it."""

    CRLF = b"https://x-access-token:tok-1@github.com\r\n"
    LF = b"https://x-access-token:tok-1@github.com\n"

    def _fill(self, store) -> subprocess.CompletedProcess:
        """What git would get for github.com with this store, through runtime.git_args."""
        if not runtime.git_exe():
            self.skipTest("git is not installed")
        return subprocess.run(
            [*runtime.git_args(net=True, store=store), "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True,
            env=accounts.scrub_git_env(os.environ), timeout=30)

    def test_save_account_writes_lf_bytes_that_git_accepts(self):
        self.acc.save_account("tester", "tok-1", 42)
        path = self.acc.credentials_path("tester")
        self.assertEqual(path.read_bytes(), self.LF)
        r = self._fill(self.acc.store_for("tester"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("username=x-access-token\n", r.stdout)
        self.assertIn("password=tok-1\n", r.stdout)

    def test_git_ignores_a_crlf_store(self):
        # the failure mode itself, so the repair below is known to matter
        path = self.tmp / "crlf.git-credentials"
        path.write_bytes(self.CRLF)
        r = self._fill(path)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("password=", r.stdout)
        self.assertIn("could not read Username", r.stderr)

    def test_store_for_repairs_a_crlf_store_from_the_account_token(self):
        self.acc.save_account("tester", "tok-1", 42)
        path = self.acc.credentials_path("tester")
        path.write_bytes(self.CRLF)          # what an earlier version left on Windows
        self.assertEqual(self.acc.store_for("tester"), path)
        self.assertEqual(path.read_bytes(), self.LF)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        r = self._fill(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("password=tok-1\n", r.stdout)

    def test_store_for_leaves_a_healthy_store_untouched(self):
        self.acc.save_account("tester", "tok-1", 42)
        with patch.object(self.acc, "_write_private") as write:
            self.assertEqual(self.acc.store_for("tester"), self.acc.credentials_path("tester"))
        write.assert_not_called()

    def test_store_for_without_a_token_returns_the_store_as_it_is(self):
        # a store whose account file is gone: nothing to rewrite from, git fails plainly
        path = self.acc.credentials_path("tester")
        path.parent.mkdir(parents=True)
        path.write_bytes(self.CRLF)
        self.assertEqual(self.acc.store_for("tester"), path)
        self.assertEqual(path.read_bytes(), self.CRLF)


class TestIdentity(_Tmp):
    def test_noreply_address(self):
        self.assertEqual(self.acc.noreply_email("tester", 42), "42+tester@users.noreply.github.com")

    def test_identity_goes_into_the_clone_not_the_global_config(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed")
        clone = self.tmp / "clone"
        clone.mkdir()
        subprocess.run([git, "-C", str(clone), "init", "-q"], check=True)
        gc = self.tmp / "gitconfig"
        old = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(gc)
        try:
            self.assertTrue(self.acc.apply_clone_identity(clone, "tester", 42))
            self.assertEqual(self.acc.clone_identity(clone),
                             {"name": "tester", "email": "42+tester@users.noreply.github.com"})
            self.assertFalse(gc.exists())   # nothing global was written
            self.assertEqual(self.acc.machine_identity(), {"name": "", "email": ""})
        finally:
            if old is None:
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = old


class TestCityBinding(_Tmp):
    def test_bind_and_lookup_survive_other_keys(self):
        runtime.save_config({"lang": "ja", "cities": {"4dcitygml/sample-tokyo-station": {"repo": "/x"}}})
        self.acc.save_account("tester", "tok", 1)
        self.acc.bind_city_login("4dcitygml/Sample-Tokyo-Station", "tester")
        cfg = runtime.read_config()
        self.assertEqual(cfg["lang"], "ja")
        self.assertEqual(cfg["cities"]["4dcitygml/sample-tokyo-station"], {"repo": "/x", "login": "tester"})
        self.assertEqual(self.acc.city_login("4dcitygml/sample-tokyo-station"), "tester")
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", None)
        self.assertIsNone(self.acc.city_login("4dcitygml/sample-tokyo-station"))

    def test_binding_without_a_stored_account_is_ignored(self):
        runtime.save_config({"cities": {"o/r": {"login": "ghost"}}})
        self.assertIsNone(self.acc.city_login("o/r"))

    def test_legacy_acknowledgement(self):
        self.assertFalse(self.acc.is_legacy_acknowledged())
        self.acc.acknowledge_legacy()
        self.assertTrue(self.acc.is_legacy_acknowledged())


class TestLegacy(_Tmp):
    def _with_global(self, fn):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed")
        gc = self.tmp / "gitconfig"
        old = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(gc)
        try:
            return fn(git, gc)
        finally:
            if old is None:
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = old

    def test_only_our_own_helper_line_is_recognised_and_removed(self):
        def run(git, gc):
            subprocess.run([git, "config", "--global", "credential.https://github.com.helper",
                            f"store --file={runtime.legacy_credentials_path()}"], check=True)
            subprocess.run([git, "config", "--global", "user.name", "Someone"], check=True)
            runtime.legacy_credentials_path().write_text("https://someone:secret@github.com\n")
            traces = self.acc.legacy_traces()
            self.assertTrue(traces["globalHelper"] and traces["plainStore"] and traces["any"])
            self.assertEqual(traces["machineIdentity"]["name"], "Someone")
            self.assertEqual(sorted(self.acc.remove_legacy_traces()), ["globalHelper", "plainStore"])
            self.assertFalse(runtime.legacy_credentials_path().exists())
            self.assertNotIn("credential", gc.read_text())
            self.assertIn("Someone", gc.read_text())   # the person's own identity stays
            self.assertFalse(self.acc.legacy_traces()["any"])
        self._with_global(run)

    def test_multi_valued_helper_only_our_value_is_removed(self):
        def run(git, gc):
            subprocess.run([git, "config", "--global", "--add", "credential.https://github.com.helper",
                            f"store --file={runtime.legacy_credentials_path()}"], check=True)
            subprocess.run([git, "config", "--global", "--add", "credential.https://github.com.helper", "osxkeychain"], check=True)
            runtime.legacy_credentials_path().write_text("https://someone:secret@github.com\n")
            self.assertTrue(self.acc.legacy_traces()["globalHelper"])      # seen although it is not the last value
            self.assertEqual(sorted(self.acc.remove_legacy_traces()), ["globalHelper", "plainStore"])
            values = subprocess.run([git, "config", "--global", "--get-all", "credential.https://github.com.helper"],
                                    capture_output=True, text=True).stdout.split()
            self.assertEqual(values, ["osxkeychain"])                            # the other helper stays
            self.assertFalse(runtime.legacy_credentials_path().exists())
        self._with_global(run)

    def test_store_file_stays_while_a_helper_still_points_at_it(self):
        def run(git, gc):
            gc.write_text("")
            runtime.legacy_credentials_path().write_text("x")
            orig = self.acc._global_helper_values
            self.acc._global_helper_values = lambda: [f"store --file={runtime.legacy_credentials_path()}"]   # unset failed
            try:
                removed = self.acc.remove_legacy_traces()
            finally:
                self.acc._global_helper_values = orig
            self.assertNotIn("plainStore", removed)
            self.assertTrue(runtime.legacy_credentials_path().exists())
        self._with_global(run)

    def test_a_foreign_helper_is_left_alone(self):
        def run(git, gc):
            subprocess.run([git, "config", "--global", "credential.https://github.com.helper", "osxkeychain"], check=True)
            self.assertFalse(self.acc.legacy_traces()["globalHelper"])
            self.assertEqual(self.acc.remove_legacy_traces(), [])
            self.assertIn("osxkeychain", gc.read_text())
        self._with_global(run)

    def test_single_token_is_moved_into_an_account_file(self):
        runtime.legacy_auth_path().write_text(json.dumps({"token": "old-tok"}))
        login = self.acc.migrate_legacy_token(lambda tok: (200, {"login": "tester", "id": 7}) if tok == "old-tok" else (401, None))
        self.assertEqual(login, "tester")
        self.assertEqual(self.acc.token_for("tester"), "old-tok")
        self.assertFalse(runtime.legacy_auth_path().exists())

    def test_dead_single_token_is_dropped(self):
        runtime.legacy_auth_path().write_text(json.dumps({"token": "dead"}))
        self.assertIsNone(self.acc.migrate_legacy_token(lambda tok: (401, None)))
        self.assertFalse(runtime.legacy_auth_path().exists())
        self.assertEqual(self.acc.list_accounts(), [])

    def test_offline_keeps_the_single_token_for_a_later_try(self):
        runtime.legacy_auth_path().write_text(json.dumps({"token": "maybe-alive"}))
        self.assertIsNone(self.acc.migrate_legacy_token(lambda tok: (0, None)))
        self.assertTrue(runtime.legacy_auth_path().exists())

    def test_no_legacy_file_is_a_no_op(self):
        self.assertIsNone(self.acc.migrate_legacy_token(lambda tok: self.fail("must not be called")))

    def test_city_lookup_is_case_insensitive_and_ignores_the_default_city(self):
        self.acc.save_account("tester", "tok", 1)
        self.acc.bind_city_login("4dcitygml/Sample-Munich-Station", "tester")
        self.assertEqual(self.acc.city_login("4dcitygml/sample-munich-station"), "tester")
        self.assertIsNone(self.acc.city_login("4dcitygml/sample-tokyo-station"))


class TestMachineAndErrors(_Tmp):
    def test_machine_token_only_through_the_cli(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="gho_x\n")
        old = shutil.which
        old_env = os.environ.pop("CITYGML_HUB_NO_GH", None)
        try:
            shutil.which = lambda name: "/usr/bin/gh" if name == "gh" else old(name)
            self.assertEqual(self.acc.machine_token(run), "gho_x")
            shutil.which = lambda name: None
            self.assertEqual(self.acc.machine_token(run), "")
        finally:
            shutil.which = old
            if old_env is not None:
                os.environ["CITYGML_HUB_NO_GH"] = old_env
        self.assertEqual(calls, [["gh", "auth", "token"]])

    def test_shell_git_overrides_are_scrubbed(self):
        env = {"PATH": "/usr/bin", "GIT_AUTHOR_EMAIL": "other@example.com", "GIT_COMMITTER_NAME": "Other",
               "EMAIL": "x@y", "GIT_ASKPASS": "/usr/bin/false", "SSH_ASKPASS": "/x", "GIT_DIR": "/elsewhere",
               "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.email", "GIT_CONFIG_VALUE_0": "z@z", "HOME": "/h"}
        out = self.acc.scrub_git_env(env)
        self.assertEqual(sorted(out), ["GIT_TERMINAL_PROMPT", "HOME", "PATH"])
        self.assertEqual(out["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("GIT_ASKPASS", env)   # a copy was scrubbed, the input is untouched

    def test_cli_login_comes_from_its_config_file_without_network(self):
        cfg = self.tmp / "gh"
        cfg.mkdir()
        (cfg / "hosts.yml").write_text("github.com:\n    git_protocol: https\n    users:\n        someone:\n    user: someone\nghe.example.com:\n    user: other\n")
        old = os.environ.get("GH_CONFIG_DIR"); old_no = os.environ.get("CITYGML_HUB_NO_GH")
        os.environ["GH_CONFIG_DIR"] = str(cfg)
        os.environ.pop("CITYGML_HUB_NO_GH", None)
        try:
            self.assertEqual(self.acc.machine_login_from_config(), "someone")
            os.environ["CITYGML_HUB_NO_GH"] = "1"
            self.assertEqual(self.acc.machine_login_from_config(), "")
            self.assertEqual(self.acc.machine_token(lambda *a, **k: self.fail("gh must not run")), "")
            os.environ.pop("CITYGML_HUB_NO_GH", None)
            (cfg / "hosts.yml").write_text("github.com:\n    user: ../evil\n")
            self.assertEqual(self.acc.machine_login_from_config(), "")
        finally:
            for k, v in (("GH_CONFIG_DIR", old), ("CITYGML_HUB_NO_GH", old_no)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_concurrent_writers_keep_each_others_keys(self):
        import threading
        errors = []
        def worker(i):
            try:
                for n in range(20):
                    runtime.update_config(lambda cfg, i=i, n=n: cfg.__setitem__(f"k{i}", n))
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for th in threads: th.start()
        for th in threads: th.join()
        cfg = runtime.read_config()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(cfg), ["k0", "k1", "k2", "k3"])
        self.assertEqual([cfg[k] for k in sorted(cfg)], [19, 19, 19, 19])

    def test_clear_clone_identity(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed")
        clone = self.tmp / "c"; clone.mkdir()
        subprocess.run([git, "-C", str(clone), "init", "-q"], check=True)
        self.acc.apply_clone_identity(clone, "tester", 1)
        self.acc.clear_clone_identity(clone)
        self.assertEqual(self.acc.clone_identity(clone), {"name": "", "email": ""})

    def test_ssh_transport_overrides_are_scrubbed_too(self):
        out = self.acc.scrub_git_env({"GIT_SSH_COMMAND": "ssh -i ~/.ssh/other", "GIT_SSH": "/x", "GIT_PROXY_COMMAND": "/p"})
        self.assertEqual(sorted(out), ["GIT_TERMINAL_PROMPT"])

    def test_org_restriction_is_recognised(self):
        msg = {"message": "Although you appear to have the correct authorization credentials, the x organization has enabled OAuth App access restrictions"}
        self.assertTrue(self.acc.is_org_restriction(403, msg))
        self.assertFalse(self.acc.is_org_restriction(403, {"message": "Forbidden"}))
        self.assertFalse(self.acc.is_org_restriction(404, msg))


if __name__ == "__main__":
    unittest.main()
