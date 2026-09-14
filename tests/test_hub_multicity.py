#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""hub-v1.2.0: one clone per city, the start script's city only selects the clone,
the clone's own 4dcitygml.json defines the city; ports step aside; a running hub is
reused; the review screen follows the exchange contract (A2 trailers, A5 classes,
A6 CI verdicts)."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.support import TempHome, runtime

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_multicity_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)

TOKYO = "4dcitygml/sample-tokyo-station"
MUNICH = "4dcitygml/sample-munich-station"


def make_clone(root: Path, repo: str) -> Path:
    """A minimal clone: 4dcitygml.json naming the city and one building file."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "4dcitygml.json").write_text(json.dumps({"repo": repo, "data_dirs": ["d"]}), encoding="utf-8")
    (root / "d").mkdir(exist_ok=True)
    (root / "d" / "a.gml").write_text("<x/>", encoding="utf-8")
    return root


class _ConfigSandbox(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.tmp = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)


class TestConfigPerCity(_ConfigSandbox):
    def test_city_key_normalizes_every_form(self):
        for form in (TOKYO, "https://github.com/" + TOKYO, "git@github.com:" + TOKYO + ".git", TOKYO.upper()):
            self.assertEqual(runtime.city_key(form), TOKYO)
        self.assertIsNone(runtime.city_key("/Users/someone/clone"))
        self.assertIsNone(runtime.city_key(""))

    def test_save_config_merges_instead_of_overwriting(self):
        runtime.save_config({"lang": "ja"})
        runtime.save_config({"repo": "/x"})
        self.assertEqual(runtime.read_config(), {"lang": "ja", "repo": "/x"})

    def test_remember_and_find_clone_per_city(self):
        tokyo = make_clone(self.tmp / "tokyo", TOKYO)
        munich = make_clone(self.tmp / "munich", MUNICH)
        runtime.remember_clone(TOKYO, tokyo)
        runtime.remember_clone(MUNICH, munich)
        cfg = runtime.read_config()
        self.assertEqual(cfg["repo"], str(munich))                    # last used, for the standalone editor
        self.assertEqual(set(cfg["cities"]), {TOKYO, MUNICH})
        self.assertEqual(runtime.saved_clone_for(TOKYO), tokyo)
        self.assertEqual(runtime.saved_clone_for(MUNICH), munich)

    def test_another_citys_clone_is_never_adopted(self):
        tokyo = make_clone(self.tmp / "tokyo", TOKYO)
        runtime.save_config({"repo": str(tokyo)})                          # legacy single slot, tokyo
        self.assertIsNone(runtime.saved_clone_for(MUNICH))                 # munich must go to setup, not reuse tokyo
        runtime.save_config({"cities": {MUNICH: {"repo": str(tokyo)}}})    # even a wrong per-city entry is verified
        self.assertIsNone(runtime.saved_clone_for(MUNICH))

    def test_legacy_entry_is_migrated_when_it_is_this_city(self):
        tokyo = make_clone(self.tmp / "tokyo", TOKYO)
        runtime.save_config({"repo": str(tokyo), "lang": "de"})
        self.assertEqual(runtime.saved_clone_for(TOKYO), tokyo)
        cfg = runtime.read_config()
        self.assertEqual(cfg["cities"][TOKYO]["repo"], str(tokyo))
        self.assertEqual(cfg["lang"], "de")                            # nothing dropped

    def test_clone_city_reads_the_clone_not_the_environment(self):
        tokyo = make_clone(self.tmp / "tokyo", TOKYO)
        os.environ["CITYGML_UPSTREAM"] = MUNICH
        self.assertEqual(runtime.clone_city(tokyo), TOKYO)
        self.assertEqual(runtime.requested_city(), MUNICH)
        # The sync target is the clone's own city, whatever the start script asked for.
        self.assertEqual(runtime.upstream_url(tokyo, ignore_env=True), "https://github.com/" + TOKYO)
        self.assertEqual(runtime.upstream_url(tokyo), "https://github.com/" + MUNICH)

    def test_default_dest_is_named_after_the_city(self):
        session = hub.Session()                                    # HOME is the empty temp folder
        session.city = MUNICH
        self.assertTrue(session.default_dest().endswith("CityGML Data (sample-munich-station)"))
        session.city = None
        self.assertTrue(session.default_dest().endswith("CityGML Data"))


class TestPortsAndReuse(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.tmp = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_free_port_steps_past_a_listener(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            busy = s.getsockname()[1]
            self.assertEqual(runtime.free_port(busy), busy + 2)

    def test_running_hub_for_matches_only_the_same_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class H(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = json.dumps({"ok": True, "repo": str(root), "hubTag": "hub-v1.0.2"}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *a):
                    pass

            srv = HTTPServer(("127.0.0.1", 0), H)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                self.assertEqual(hub.running_hub_for(root, port, tries=1), (f"http://localhost:{port}/", "hub-v1.0.2"))
                self.assertIsNone(hub.running_hub_for(root / "other", port, tries=1))
            finally:
                srv.shutdown()


class TestStartupSelectsTheRightClone(_ConfigSandbox):
    """End to end: a Munich start with only a Tokyo clone remembered opens the setup
    screen (import) instead of adopting — and rewriting — the Tokyo clone."""

    def _start(self, env_city: str, port: int) -> str:
        env = dict(os.environ, CITYGML_UPSTREAM=env_city, HOME=str(self.tmp), CITYGML_LANG="en")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(REPO_ROOT / "tools" / "hub" / "app.py"), "--no-browser", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=str(self.tmp))
        lines = []
        watchdog = threading.Timer(20, proc.kill)   # the server never exits by itself
        watchdog.start()
        try:
            for line in proc.stdout:
                lines.append(line.rstrip())
                if line.startswith("citygml-hub"):
                    break
        finally:
            watchdog.cancel()
            proc.kill()
            proc.wait()
        return "\n".join(lines)

    def test_munich_start_does_not_touch_the_tokyo_clone(self):
        tokyo = make_clone(self.tmp / "tokyo", TOKYO)
        before = sorted(p.name for p in tokyo.iterdir())
        (self.tmp / ".citygml_hub.json").write_text(json.dumps({"repo": str(tokyo)}), encoding="utf-8")
        # The subprocess reads ~/.citygml_attr_editor.json under HOME=self.tmp
        (self.tmp / ".citygml_attr_editor.json").write_text(json.dumps({"repo": str(tokyo)}), encoding="utf-8")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        out = self._start(MUNICH, port)
        self.assertNotIn("Repository:", out, out)          # setup mode, no clone adopted
        self.assertIn("citygml-hub:", out, out)
        self.assertEqual(sorted(p.name for p in tokyo.iterdir()), before)
        self.assertEqual(json.loads((tokyo / "4dcitygml.json").read_text())["repo"], TOKYO)


class TestReviewFollowsTheContract(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.tmp = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_review_kind_uses_the_shared_table_and_keeps_other(self):
        self.assertEqual(hub.review_kind({"title": "x", "head": {"ref": "geom/13101-bldg-1"}}), "geometry")
        self.assertEqual(hub.review_kind({"title": "Attributkorrektur: Geschosse", "head": {"ref": "feature/x"}}), "attribute")
        self.assertEqual(hub.review_kind({"title": "Fix storeys", "head": {"ref": "feature/x"}}), "other")
        self.assertEqual(hub.review_kind({"title": "属性を直した", "head": {"ref": "feature/x"}}), "other")  # no loose matching

    def test_other_prs_are_listed_not_hidden(self):
        h = hub.Hub.__new__(hub.Hub)
        h._attribute_labels = {}
        responses = {
            "/commits?": (200, [{"commit": {"message": "Fix storeys\n\nBuilding: 13101-bldg-9\n"}}]),
            "/check-runs?": (200, {"check_runs": []}),
            "/comments?": (200, []),
            "/reviews?": (200, []),
        }

        def fake_api(endpoint, token, *a, **k):
            for key, value in responses.items():
                if key in endpoint:
                    return value
            return 404, {}

        pr = {"number": 7, "title": "Fix storeys", "body": "## Summary of changes <!--sec:reason-->\n\nsurvey sheet\n",
              "head": {"ref": "feature/x", "sha": "abc"}, "base": {"ref": "main"}, "user": {"login": "p"}, "state": "open"}
        with patch.object(runtime, "github_api", fake_api):
            item = hub.Hub._review_queue_item(h, "t", "o/r", pr)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "other")
        self.assertEqual(item["buildingId"], "13101-bldg-9")   # from the A2 trailer, not the title

    def test_ci_reason_verdict_wins_over_the_local_preview(self):
        h = hub.Hub.__new__(hub.Hub)
        h._attribute_labels = {}
        inspection = ("<!-- citygml-automatic-inspection -->\n| Check | Result |\n|---|---|\n"
                      "| Description and evidence <!--cp:reason--> | ❌ Needs attention |\n")
        responses = {
            "/commits?": (200, []),
            "/check-runs?": (200, {"check_runs": []}),
            "/comments?": (200, [{"body": inspection, "user": {"login": "github-actions[bot]"}}]),
            "/reviews?": (200, []),
        }

        def fake_api(endpoint, token, *a, **k):
            for key, value in responses.items():
                if key in endpoint:
                    return value
            return 404, {}

        pr = {"number": 8, "title": "Update attributes (Usage)", "body": "## Summary of changes <!--sec:reason-->\n\nlooks fine locally\n",
              "head": {"ref": "edit/13101-bldg-1", "sha": "abc"}, "base": {"ref": "main"}, "user": {"login": "p"}, "state": "open"}
        with patch.object(runtime, "github_api", fake_api):
            item = hub.Hub._review_queue_item(h, "t", "o/r", pr)
        self.assertIn("Reason and evidence not filled in", " ".join(item["adjustmentReasons"]))

    def test_latest_reviews_one_vote_per_account(self):
        reviews = [
            {"id": 1, "user": {"login": "A"}, "state": "CHANGES_REQUESTED", "commit_id": "abc"},
            {"id": 2, "user": {"login": "a"}, "state": "APPROVED", "commit_id": "abc"},
            {"id": 3, "user": {"login": "b"}, "state": "COMMENTED", "commit_id": "abc"},
        ]
        latest = hub.latest_reviews(reviews)
        self.assertEqual(set(latest), {"a"})
        self.assertEqual(latest["a"]["state"], "APPROVED")

    def test_building_id_from_trailers(self):
        self.assertEqual(hub.building_id_from_commit_messages(["Docs\n", "Add\n\nBuilding-Added: X-1\n"]), "X-1")
        self.assertEqual(hub.building_id_from_commit_messages(["no trailer"]), "")


if __name__ == "__main__":
    unittest.main()
