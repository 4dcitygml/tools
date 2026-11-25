#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The hub's screens and their API answers, recorded (S13: the hub split moves code only, so
nothing a browser receives may change).

A real hub HTTP server over a fixture clone, GitHub stubbed: the setup screen without a clone,
then the dashboard, review and settings screens with an account and a clone, and every GET the
screens make. Machine-specific parts (the temporary HOME, git and Python, the port, local clock times) are normalised.
Regenerate after an intended change: CITYGML_SNAPSHOT_WRITE=1.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.support import TOKYO, TempHome, accounts, fresh_hub, load_app, runtime
from tests.test_hub_review import ci_checks, example_pr

hub = load_app("hub_screen_snapshot", "tools/hub/app.py")
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hub_screen_snapshot.json"
NWO = TOKYO
GML = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
       'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml" '
       'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">'
       '<core:cityObjectMember><bldg:Building gml:id="bldg-object-1">'
       '<bldg:measuredHeight>10</bldg:measuredHeight>'
       '<uro:buildingID>13101-bldg-1</uro:buildingID>'
       '</bldg:Building></core:cityObjectMember></core:CityModel>')


def github(path, token, method="GET", payload=None, timeout=30):
    """GitHub as the screens see it: one account, its fork, one open proposal with CI's checks."""
    pr = example_pr()
    pr["base"]["repo"] = {"full_name": NWO}
    routes = {
        "/user": {"login": "alice", "id": 1},
        f"/repos/{NWO}": {"full_name": NWO, "permissions": {"admin": False, "maintain": False, "push": True,
                                                           "triage": True, "pull": True}},
        "/repos/alice/sample-tokyo-station": {"full_name": "alice/sample-tokyo-station", "fork": True,
                                              "parent": {"full_name": NWO}},
        f"/repos/{NWO}/pulls?state=open&sort=updated&direction=desc&per_page=100&page=1": [pr],
        f"/repos/{NWO}/pulls/123": pr,
        f"/repos/{NWO}/pulls/123/commits?per_page=100&page=1": [{"sha": "abc123", "commit": {
            "message": "Update attributes (Storeys Above Ground): 2 → 3\n\nBuilding: 13101-bldg-1\n"}}],
        f"/repos/{NWO}/pulls/123/files?per_page=100&page=1": [{
            "filename": "city/udx/bldg/53394611_bldg_6697_op.gml", "status": "modified",
            "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@\n-2\n+3"}],
        f"/repos/{NWO}/issues/123/comments?per_page=100&page=1": [],
        f"/repos/{NWO}/pulls/123/reviews?per_page=100&page=1": [],
        f"/repos/{NWO}/commits/abc123/check-runs?per_page=100&page=1": {"check_runs": ci_checks(pr)},
    }
    if path in routes:
        return 200, routes[path]
    if "/actions/runs?status=action_required" in path:
        return 200, {"workflow_runs": []}
    if "/compare/main..." in path:
        return 200, {"status": "ahead", "ahead_by": 1, "behind_by": 0}
    if path.startswith("/search/issues?"):
        return 200, {"items": [pr]}
    if path == "/graphql":
        node = {"number": 123, "title": pr["title"], "state": "OPEN", "isDraft": False, "url": pr["html_url"],
                "updatedAt": "2026-10-01T00:00:00Z", "headRefName": pr["head"]["ref"],
                "reviews": {"totalCount": 0}, "comments": {"totalCount": 0},
                "commits": {"nodes": [{"commit": {"oid": "abc123", "checkSuites": {"nodes": []}}}]}}
        return 200, {"data": {"prs": {"nodes": [node]}, "issues": {"nodes": []}, "merged": {"issueCount": 4}}}
    return 404, {"message": "Not Found"}


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestScreenSnapshot(unittest.TestCase):
    def setUp(self):
        self._home = TempHome(city=TOKYO)
        self.home = self._home.__enter__()
        self._patches = [patch.object(runtime, "github_api", github),
                         patch.object(runtime, "fetch_latest_hub", lambda *a, **k: None),
                         patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v9.9.9"})]
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

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=20) as r:
                status, ctype, body = r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            status, ctype, body = e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8")
        for real, name in ((runtime.git_exe() or "-", "<git>"), (runtime.python_exe(), "<python>"),
                           (str(self.home.resolve()), "<home>"), (str(self.home), "<home>")):
            body = body.replace(json.dumps(real)[1:-1], name).replace(real, name)
        body = body.replace(str(self.port), "<port>")
        body = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}", "<local time>", body)   # when an account was saved
        return {"status": status, "type": ctype,
                "body": json.loads(body) if ctype.startswith("application/json") else body}

    def clone(self) -> Path:
        root = self.home / "Documents" / "sample-tokyo-station"
        (root / "city/udx/bldg").mkdir(parents=True)
        (root / "city/udx/bldg/53394611_bldg_6697_op.gml").write_text(GML, encoding="utf-8")
        (root / "4dcitygml.json").write_text(json.dumps({
            "repo": NWO, "dialect": "plateau", "data_dirs": ["city"], "lang": "en",
            "name": {"en": "Tokyo Station (practice)"}}), encoding="utf-8")
        git = ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.com"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        subprocess.run([*git, "remote", "add", "origin", "https://github.com/alice/sample-tokyo-station.git"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "initial"], check=True,
                       env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})
        return root

    def screens(self) -> dict:
        seen = {f"setup {p}": self.get(p) for p in ("/", "/api/setup/status")}
        accounts.save_account("alice", "tok-a", 1)
        accounts.bind_city_login(TOKYO, "alice")
        accounts.apply_clone_identity(root := self.clone(), "alice", 1)
        hub.SESSION.start(root, TOKYO, sync=False)
        for p in ("/", "/review.html", "/settings.html", "/api/status", "/api/update", "/api/settings",
                  "/api/tools", "/api/contributions", "/api/reviews/permission", "/api/reviews/pending-runs",
                  "/api/reviews", "/api/reviews/123", "/api/setup/status", "/no-such-page"):
            seen[p] = self.get(p)
        return seen

    def test_the_screens_are_as_recorded(self):
        seen = json.loads(json.dumps(self.screens(), ensure_ascii=False))
        if os.environ.get("CITYGML_SNAPSHOT_WRITE"):
            FIXTURE.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(seen), sorted(recorded))
        for key in recorded:
            with self.subTest(screen=key):
                self.assertEqual(seen[key], recorded[key])


if __name__ == "__main__":
    unittest.main()
