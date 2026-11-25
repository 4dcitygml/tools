#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""4dcitygml hub — the local screen a contributor starts from.

One process serves one city clone: it keeps the clone's main in line with the city
repository (git_sync), launches the attribute and texture editors as child processes,
holds the GitHub account the city is bound to (accounts), lists the person's own
proposals, offers the review screen, and announces newer tool versions (applied at
the next start). Everything it needs travels in its own folder (runtime.py and the
other shared modules); a city clone carries no code.

Standard library only. GitHub is reached over HTTPS with the account's token (OAuth
device flow for a new sign-in); the GitHub CLI is consulted only when the person
explicitly chooses "use this computer's GitHub".

Usage:
    python3 hub.py                                  # started by the launcher (CITYGML_UPSTREAM selects the clone)
    python3 tools/hub/app.py --repo <clone> [--port 8760] [--no-browser]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

APP_DIR = Path(__file__).resolve().parent
# The shared runtime (program/runtime.py in the bundle, tools/runtime.py in the source
# tree) holds every fact the tools share and puts the other shared modules on sys.path.
_SHARED = next((d for d in (APP_DIR, APP_DIR.parent) if (d / "runtime.py").is_file()), None)
if _SHARED is None:
    sys.exit("runtime.py is missing next to the hub: install the tools again with the one-line command")
sys.path.insert(0, str(_SHARED))
sys.path.insert(1, str(APP_DIR))   # the hub's own modules (review, the report contract): one folder in the bundle
import runtime  # noqa: E402
import accounts  # noqa: E402
import git_sync  # noqa: E402
import shortcuts  # noqa: E402
import review  # noqa: E402  (the review screen, next to the hub)
import session  # noqa: E402  (the account side and the process state, next to the hub)
from review import attr_editor_module, attribute_labels, ci_retry_info, ci_verdict, graphql_check_runs, graphql_pr  # noqa: E402

DEFAULT_PORT = 8760


tr = runtime.translator("hub")   # server-generated text of the hub in the display language


# ---- One clone per city (hub-v1.2.0) ----
# The environment variable set by the city's start script only selects WHICH clone
# to use; the clone's own 4dcitygml.json defines the city (sync target, fork target,
# logo, theme). The two must agree, otherwise the clone is not adopted. Clones are
# remembered per city under `cities[<owner/repo>]` (runtime.city_entry).


def create_city_shortcut(clone_root: Path) -> dict:
    """A desktop launcher for the clone's city: only the city id is embedded (see tools/shortcuts.py)."""
    city = runtime.clone_city(clone_root)
    if not city:
        raise RuntimeError("The clone does not name its city (4dcitygml.json repo)")
    cfg = runtime.city_meta(clone_root)
    name = cfg.get("name")
    lang = runtime.norm_lang(cfg.get("lang"))
    if isinstance(name, dict):
        display = name.get(lang) or name.get("en") or next(iter(name.values()), city)
    else:
        display = name or city.split("/")[-1]
    logo = runtime.city_logo(clone_root)   # validated: inside the clone, an image (D-3c3)
    shortcuts.ensure_launcher()
    path = shortcuts.create_shortcut(runtime.tools_dir(), str(display), city, logo[0] if logo else None)
    shortcuts.reveal(path)
    return {"ok": True, "path": str(path), "name": path.name}


def running_hub_for(root, start: int = DEFAULT_PORT, step: int = 2, tries: int = 20) -> "tuple[str, str | None] | None":
    """(URL, version tag) of a hub already serving this clone, or None. The caller decides
    whether to open it (same version) or to start anyway (another version left running)."""
    root = Path(root).resolve()
    port = start
    for _ in range(tries):
        if runtime.port_open(port):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if Path(str(data.get("repo") or "")).resolve() == root:
                    return f"http://localhost:{port}/", data.get("hubTag") or None
            except Exception:
                pass
        port += step
    return None

# Child tools that can be launched (relative path to app.py, default port)
TOOLS = {
    "attr_editor": {
        "label": "Attribute Editor",
        "desc": "View and edit building attributes from the map and submit a PR",
        "path": "attr_editor/app.py",
        "port": runtime.EDITOR_PORTS["attr_editor"],
        "icon": "🏢",
    },
    "tex_editor": {
        "label": "Texture Editor",
        "desc": "Replace facade photos (LOD2 textures) and submit a PR",
        "path": "tex_editor/app.py",
        "port": runtime.EDITOR_PORTS["tex_editor"],
        "icon": "🖼️",
    },
}


def tool_label(key: str, t: dict) -> str:
    """Display name of a child tool (in the selected language)."""
    if key == "attr_editor":
        return tr("hub.tool_attr_label", t["label"])
    if key == "tex_editor":
        return tr("hub.tool_tex_label", t["label"])
    return t["label"]


def tool_desc(key: str, t: dict) -> str:
    """Description of a child tool (in the selected language)."""
    if key == "attr_editor":
        return tr("hub.tool_attr_desc", t["desc"])
    if key == "tex_editor":
        return tr("hub.tool_tex_desc", t["desc"])
    return t["desc"]


# Merged PR count → achievement badge (threshold, emoji, name). Evaluated in descending order
BADGES = [
    (30, "🏛️", "Maintainer class"),
    (10, "🌳", "Veteran"),
    (3, "🌿", "Regular contributor"),
    (1, "🌱", "First commit achieved"),
    (0, "✨", "Getting started (try sending a PR)"),
]


def _badge_names() -> dict:
    """Display names of achievement badges (threshold → name in the selected language)."""
    return {
        30: tr("hub.badge_maintainer", "Maintainer class"),
        10: tr("hub.badge_veteran", "Veteran"),
        3: tr("hub.badge_regular", "Regular contributor"),
        1: tr("hub.badge_first", "First commit achieved"),
        0: tr("hub.badge_newcomer", "Getting started (try sending a PR)"),
    }


def badge_for(merged: int) -> dict:
    names = _badge_names()
    for i, (lo, emoji, name) in enumerate(BADGES):
        if merged >= lo:
            nxt = BADGES[i - 1] if i > 0 else None
            return {
                "emoji": emoji,
                "name": names.get(lo, name),
                "merged": merged,
                "next": ({"at": nxt[0], "emoji": nxt[1],
                          "name": names.get(nxt[0], nxt[2])} if nxt else None),
            }
    return {"emoji": "✨", "name": tr("hub.badge_newcomer_short", "Getting started"),
            "merged": merged, "next": None}




class Hub(review.Reviews):
    """One city's dashboard over its clone. `session` is who it works as (S13: the global SESSION
    was read here): `login`, `github_login()` (token, login) and the clone's `sync`."""

    def __init__(self, root: Path, session):
        self.root = root.resolve()
        self._session = session
        self._attribute_labels = attribute_labels()
        self._attr_repo = None
        self._attr_repo_lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._ports: dict[str, int] = {}   # tool ports used by this hub (city)
        self._repo_probe: dict[int, "tuple[float, bool]"] = {}  # port -> (timestamp, is our repo)
        self._contributions = runtime.Memo()   # the account's PRs / issues, for that account (S18, F4)

    # ---- Repository / account status ----
    def _git(self, *args: str) -> str:
        return runtime.git_output(self.root, *args)

    def ensure_origin(self, fork_nwo: str) -> bool:
        """Point `origin` at the account's fork (hub-v1.2.1: the account may change after the clone).

        The upstream city is never touched (sync uses the clone's 4dcitygml.json);
        only where edits are uploaded follows the chosen account. Returns True when changed."""
        current = (self.nwo() or "").lower()
        if not fork_nwo or not current or current == fork_nwo.lower():
            return False   # unknown / non-GitHub remote (e.g. an SSH alias): never touched
        if current.split("/")[-1] != fork_nwo.lower().split("/")[-1]:
            return False   # another repository: not a copy of this city
        # A clone made straight from the city (origin = the city, any transport) is repointed
        # as well: with an account chosen, uploads go to that account's copy over HTTPS only.
        if not runtime.git_exe():
            return False
        with git_sync.clone_lock(self.root):   # not while an editor pushes to origin (D18)
            r = runtime.git(self.root, "remote", "set-url", "origin", f"https://github.com/{fork_nwo}.git")
        if r.returncode == 0:
            print(f"  origin → {fork_nwo} (the account's copy)")
            return True
        return False

    def nwo(self) -> "str | None":
        """origin's owner/repo (for GitHub links and the API)."""
        return runtime.github_nwo(self._git("remote", "get-url", "origin"))

    def status(self) -> dict:
        git_exe, git_bundled = runtime.git_exe(), runtime.git_bundled()
        py_exe, py_bundled = runtime.python_exe(), runtime.python_bundled()
        return {
            "ok": True,
            "repo": str(self.root),
            "branch": self._git("rev-parse", "--abbrev-ref", "HEAD"),
            # the clone's own identity, as the editors' confirmation shows it (C5)
            "gitUser": accounts.clone_identity(self.root)["name"],
            "gitEmail": accounts.clone_identity(self.root)["email"],
            "nwo": self.nwo(),
            # V4: the city's own "new issue" page (its issue forms, when the organisation has them)
            "issuesUrl": f"https://github.com/{city}/issues/new/choose" if (city := runtime.upstream_nwo(self.root)) else "",
            "account": self._session.login,
            "hubTag": runtime.running_hub_tag(),
            "sync": self._session.sync.state if self._session.sync is not None else None,
            "runtime": {
                "git": {"path": git_exe, "bundled": git_bundled},
                "python": {"path": py_exe, "bundled": py_bundled},
            },
        }

    def _tool_app(self, t: dict) -> "Path | None":
        """The editor's app.py next to the hub (program/<editor>/ in the bundle, tools/<editor>/
        in the source tree). Cities distribute no code: nothing is ever run from a clone."""
        rel = Path(t["path"])
        cand = runtime.SHARED_DIR / rel
        return cand if cand.is_file() else None

    def _port_serves_our_repo(self, port: int) -> bool:
        """Whether the editor on that port has our own city (repo) open (5-second TTL)."""
        at, val = self._repo_probe.get(port, (0.0, False))
        if at > time.time() - 5:
            return val
        ok = False
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/repo", timeout=1) as r:
                data = json.loads(r.read().decode("utf-8"))
            ok = Path(str(data.get("root") or "")).resolve() == self.root
        except Exception:
            ok = False
        self._repo_probe[port] = (time.time(), ok)
        return ok

    def _tool_port(self, key: str, t: dict) -> int:
        """Tool port for this city. Searches for a free port from the default (+2 steps).

        Even if the default port is in use, reuse it if it is our own city's editor.
        If it is another city's editor, shift to the next port — supporting
        simultaneous editing of multiple cities."""
        if key not in self._ports:
            self._ports[key] = runtime.free_port(t["port"], accept=self._port_serves_our_repo)
        return self._ports[key]

    def tools(self) -> list[dict]:
        out = []
        for key, t in TOOLS.items():
            exists = self._tool_app(t) is not None
            port = self._ports.get(key)
            if port is None and runtime.port_open(t["port"]) and self._port_serves_our_repo(t["port"]):
                # Adopt a manually launched editor of our own city
                port = self._ports[key] = t["port"]
            display = port or t["port"]
            running = port is not None and runtime.port_open(port)
            out.append({
                "key": key, "label": tool_label(key, t), "desc": tool_desc(key, t),
                "icon": t["icon"],
                "port": display, "url": f"http://localhost:{display}/",
                "available": exists, "running": running,
            })
        return out

    def _launch_cmd(self, key: str, t: dict, port: "int | None" = None, hub: "str | None" = None) -> list:
        """Child-tool launch command: the bundled (or system) python runs the editor's app.py;
        --hub is this hub's address, for the editor's "back to the hub" link."""
        return [runtime.python_exe(), str(self._tool_app(t)), "--repo", str(self.root),
                "--port", str(port or t["port"]), "--no-browser", *(["--hub", hub] if hub else [])]

    def launch(self, key: str, hub: "str | None" = None) -> dict:
        t = TOOLS.get(key)
        if t is None:
            raise ValueError(tr("hub.err_unknown_tool", "Unknown tool: {key}", key=key))
        app = self._tool_app(t)
        if app is None:
            raise FileNotFoundError(t["path"])
        port = self._tool_port(key, t)
        url = f"http://localhost:{port}/"
        if runtime.port_open(port):
            return {"ok": True, "url": url, "port": port, "already": True}
        # The editors work as the same account as this hub (token and credential store by login).
        # The city is the clone's own; the start script's city variable does not reach the editors (F7).
        env = {k: v for k, v in accounts.scrub_git_env(os.environ).items() if k != "CITYGML_UPSTREAM"}
        proc = subprocess.Popen(self._launch_cmd(key, t, port, hub), cwd=str(self.root), env=env)
        self._procs[key] = proc
        # Wait a bit until the server listens (up to ~6 seconds)
        for _ in range(30):
            if runtime.port_open(port):
                break
            if proc.poll() is not None:
                raise RuntimeError(tr(
                    "hub.err_launch_failed",
                    "{label} failed to start (process exited with code {code})",
                    label=tool_label(key, t), code=proc.returncode,
                ))
            time.sleep(0.2)
        if not runtime.port_open(port):
            raise RuntimeError(tr(
                "hub.err_launch_no_response", "{label} is not responding (port {port})",
                label=tool_label(key, t), port=port,
            ))
        return {"ok": True, "url": url, "port": port, "already": False}

    # ---- Contributions (PRs / Issues) ----
    def contributions(self, force: bool = False) -> dict:
        login = self._session.login
        fresh, data = self._contributions.get(login)
        if fresh and not force:
            return data
        return self._contributions.set(self._fetch_contributions(), 30, key=login)

    def city_data_changed(self) -> None:
        """The sync brought new city data: models read from the old files are dropped (S18, F3:
        the review screen's 3D view kept the files as they were before the sync)."""
        with self._attr_repo_lock:
            self._attr_repo = None

    # Fetch your own PRs / Issues in one request (same GraphQL API and same fields
    # as gh pr list / gh issue list; no gh CLI dependency = works with the device-flow token).
    _CONTRIB_QUERY = """query($qPr: String!, $qIssue: String!, $qMerged: String!) {
      prs: search(query: $qPr, type: ISSUE, first: 100) {
        nodes { ... on PullRequest {
          number title body state url isDraft reviewDecision updatedAt baseRefName baseRefOid headRefOid
          labels(first: 100) { nodes { name } }
          commits(last: 1) { nodes { commit { checkSuites(first: 20) { nodes { app { slug }
            report: checkRuns(last: 1, filterBy: {checkName: "ci-report"}) {
              nodes { databaseId name status conclusion text } }
            analysis: checkRuns(last: 1, filterBy: {checkName: "analyze"}) {
              nodes { databaseId name status conclusion } } } } } } }
        } }
      }
      issues: search(query: $qIssue, type: ISSUE, first: 100) {
        nodes { ... on Issue {
          number title state url updatedAt comments { totalCount } } }
      }
      merged: search(query: $qMerged, type: ISSUE, first: 1) { issueCount }
    }"""

    def _fetch_contributions(self) -> dict:
        # Proposals and issues live on the city repository (upstream); the clone's origin is
        # the person's copy, where no PR ever exists.
        nwo = runtime.upstream_nwo(self.root)
        token, login = self._session.github_login()
        if not nwo:   # a clone that names no city has no proposals anywhere (V1)
            return {"ok": False, "reason": tr("hub.err_no_city", "This copy of the data does not name its city (4dcitygml.json repo). "
                               "Open the city's copy, or make a new one from the setup screen."), "login": login or None,
                    "prs": [], "issues": [], "merged": 0, "badge": badge_for(0)}
        if not token or not login:
            reason = tr("hub.err_gh_not_connected",
                        'GitHub is not connected (you can connect from "Connect to GitHub" '
                        "in the account section)")
            return {"ok": False, "reason": reason, "login": None,
                    "prs": [], "issues": [], "merged": 0, "badge": badge_for(0)}
        code, data = runtime.github_api("/graphql", token, method="POST", payload={
            "query": self._CONTRIB_QUERY,
            "variables": {"qPr": f"repo:{nwo} author:{login} type:pr sort:updated",
                          "qIssue": f"repo:{nwo} author:{login} type:issue sort:updated",
                          # the count GitHub's search knows, whatever the list shows (D-3a8: the
                          # badge counted the merged PRs among the latest 100 only)
                          "qMerged": f"repo:{nwo} author:{login} type:pr is:merged"},
        })
        if code != 200 or "data" not in data:
            errs = data.get("errors") or [{}]
            reason = errs[0].get("message") or data.get("message") or f"HTTP {code}"
            return {"ok": False,
                    "reason": tr("hub.err_fetch_failed", "Fetch failed: {reason}", reason=reason),
                    "login": login,
                    "prs": [], "issues": [], "merged": 0, "badge": badge_for(0)}
        prs = []
        for p in data["data"]["prs"]["nodes"]:
            if not p:
                continue
            # the verdict is the current judgement of an open proposal; a merged or closed one
            # is history: GitHub's own state says it, and no report is read for it (A10)
            verdict = ci_verdict(graphql_check_runs(p), graphql_pr(p)) if p["state"] == "OPEN" else None
            check_status = verdict["status"] if verdict else ""
            retry = ci_retry_info(verdict) if verdict else {}
            prs.append({
                "number": p["number"], "title": p["title"], "state": p["state"],
                "url": p["url"], "draft": p.get("isDraft", False),
                "review": p.get("reviewDecision") or "",
                "updated": p.get("updatedAt", ""),
                "reacted": bool(p.get("reviewDecision")),
                "checkStatus": check_status,
                "retry": retry,
            })
        issues = []
        for i in data["data"]["issues"]["nodes"]:
            if not i:
                continue
            n = (i.get("comments") or {}).get("totalCount", 0)
            issues.append({
                "number": i["number"], "title": i["title"], "state": i["state"],
                "url": i["url"], "comments": n,
                "updated": i.get("updatedAt", ""), "reacted": n > 0,
            })
        merged = int((data["data"].get("merged") or {}).get("issueCount") or 0)
        return {"ok": True, "login": login, "nwo": nwo,
                "prs": prs, "issues": issues, "merged": merged, "badge": badge_for(merged)}


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------


class Handler(runtime.LocalHandler):
    APP_ID = "hub"

    @property
    def root(self) -> "Path | None":
        return SESSION.hub.root if SESSION.hub is not None else None

    def _file(self, name: str, values: "dict | None" = None) -> None:
        """One of the hub's screens (a file next to hub.py)."""
        self.serve_file(APP_DIR / name, values)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            s = SESSION
            if s.hub is None:
                s.try_activate()
            if path == "/api/setup/status":
                self._json(s.setup_state())
                return
            if s.hub is None or (path in ("/", "/index.html") and (not s.login or s.copy_missing())):
                # While there is no clone, return the setup screen for every GET. With a clone
                # but no account chosen for this city — or an account without its copy yet —
                # the same page shows the account screen (and its "create your copy" step).
                # the city this session is for (D-3a5 / D-3c2: always the practice city before)
                city = s.upstream()
                self._file("setup.html", {"UPSTREAM": f"https://github.com/{city}" if city else "", "DEFAULT_DEST": s.default_dest(),
                                          "MODE": "setup" if s.hub is None else "account",
                                          "START": shortcuts.start_command(city) if city else ""})
                return
            if path in ("/", "/index.html"):
                self._file("index.html")
            elif path == "/review.html":
                self._file("review.html")
            elif path == "/city-logo":
                self._city_logo()
            elif path == "/review-viewer.html":
                viewer = runtime.SHARED_DIR / "attr_editor" / "viewer.html"
                if not viewer.is_file():
                    self._error(tr("hub.err_viewer_missing",
                                   "The 3D view component was not found"), 404)
                else:
                    # Served as the attribute editor serves it: the city's map settings, the theme
                    # and the viewer's language pack (its t() and viewer.* keys); raw bytes stop
                    # the page before it loads the building.
                    values = {"map": attr_editor_module().city_map_config(self.root)}
                    self._bytes(runtime.page(viewer.read_bytes(), "attr_editor", self.root, values),
                                "text/html; charset=utf-8", cache="no-cache")
            elif path == "/api/status":
                # Merge in the authentication progress (userCode / waiting / login)
                # so the dashboard can also show "Connect" (the device flow).
                self._json({**s.hub.status(), "github": s.account.state()})
            elif path == "/api/update":
                self._json({"ok": True, **s.updates.snapshot()})
            elif path == "/settings.html":
                self._file("settings.html")
            elif path == "/api/settings":
                self._json(s.settings_payload())
            elif path == "/api/tools":
                self._json({"ok": True, "tools": s.hub.tools()})
            elif path == "/api/contributions":
                self._json({"ok": True, **s.hub.contributions()})
            elif path == "/api/reviews/permission":
                self._json(s.hub.review_entry())
            elif path == "/api/reviews/pending-runs":
                self._json(s.hub.pending_runs())
            elif path == "/api/reviews":
                self._json(s.hub.review_queue())
            elif re.fullmatch(r"/api/reviews/\d+/asset", path):
                number = int(path.split("/")[3])
                side = (query.get("side") or [""])[0]
                asset_path = (query.get("path") or [""])[0]
                data, mime = s.hub.review_asset(number, side, asset_path)
                self._bytes(data, mime)
            elif re.fullmatch(r"/api/building/[^/]+/[^/]+", path):   # any tile code; checked against the clone (D-3a2)
                parts = path.split("/")
                self._json({
                    "ok": True,
                    "building": s.hub.review_building_model(unquote(parts[3]), unquote(parts[4])),
                })
            elif path.startswith("/textures/"):
                data, mime = s.hub.review_texture(unquote(path[len("/textures/"):]))
                self._bytes(data, mime)
            elif re.fullmatch(r"/api/reviews/\d+", path):
                self._json(s.hub.review_detail(int(path.rsplit("/", 1)[1])))
            else:
                self._error("not found", 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {runtime.public_message(e)}", 500)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            s = SESSION
            # Setup (pre-clone) button actions. Everything advances with just a press.
            if path == "/api/auth/start":
                try:
                    self._json({"ok": True, **s.account.start()})
                except RuntimeError as e:
                    self._json({"ok": False, "error": str(e),
                                "certificateReport": s.account.certificate_report})
                return
            # Account screen (hub-v1.2.1): every choice is an explicit click.
            if path == "/api/auth/use":
                err = s.account.use_saved(str(body.get("login") or ""))
                self._json({"ok": err is None, "error": err, "status": s.setup_state()})
                return
            if path == "/api/auth/machine":
                err = s.account.use_machine()
                self._json({"ok": err is None, "error": err, "status": s.setup_state()})
                return
            if path == "/api/auth/legacy":
                removed = s.account.review_legacy(bool(body.get("remove")))
                self._json({"ok": True, "removed": removed, "status": s.setup_state()})
                return
            if path == "/api/auth/disconnect":
                s.account.disconnect(delete=bool(body.get("delete")))
                self._json({"ok": True})
                return
            if path == "/api/accounts/delete":
                login = str(body.get("login") or "")
                if not login:
                    self._error(tr("hub.err_account_unknown", "That account is not saved on this computer. Choose one of the options on this screen."))
                    return
                if login == s.account.login:
                    s.account.disconnect(delete=True)
                else:
                    accounts.delete_account(login)
                self._json({"ok": True})
                return
            if path == "/api/settings/forget":
                # Forget this city's binding (account and clone registration). The clone folder stays.
                city = s.city
                s.account.disconnect(delete=False)
                runtime.city_entry(city, forget=True)
                self._json({"ok": True})
                return
            if path == "/api/setup/fork":
                try:
                    nwo, err = s.create_copy()
                except (urllib.error.URLError, OSError):
                    nwo, err = None, tr("hub.err_github_offline",
                                        "GitHub could not be reached. Check the internet connection and try again.")
                self._json({"ok": err is None, "error": err, "nwo": nwo, "status": s.setup_state()})
                return
            if path == "/api/setup/clone":
                if s.hub is not None:
                    self._error(tr("hub.err_setup_done", "Setup is already complete"), 409)
                    return
                nwo = s.fork_nwo() if s.account.state().get("login") else None
                if not nwo:
                    self._error(tr("hub.err_fork_first", "Create your own copy first"))
                    return
                dest = (body.get("dest") or "").strip() or s.default_dest()
                s.setup.start(f"https://github.com/{nwo}.git", dest)
                self._json({"ok": True})
                return
            if s.hub is None:
                self._error(tr("hub.err_setup_incomplete", "Setup is not complete"), 409)
                return
            if path == "/api/launch":
                self._json(s.hub.launch(body["tool"], f"http://localhost:{self.server.server_address[1]}/"))
            elif path == "/api/update/fetch":
                self._json({"ok": True, "fetch": s.updates.fetch_async()})
            elif path == "/api/shortcut":
                try:
                    self._json(create_city_shortcut(s.hub.root))
                except Exception as e:
                    self._error(str(e))   # the screen says what failed (common.shortcut_failed)
            elif path == "/api/refresh":
                self._json({"ok": True, **s.hub.contributions(force=True)})
            elif re.fullmatch(r"/api/reviews/\d+/decision", path):
                number = int(path.split("/")[3])
                self._json(s.hub.submit_review(number, demo=bool(body.get("demo"))))
            elif re.fullmatch(r"/api/reviews/\d+/feedback", path):
                number = int(path.split("/")[3])
                self._json(s.hub.submit_review_feedback(
                    number, str(body.get("message") or ""), demo=bool(body.get("demo")),
                ))
            elif re.fullmatch(r"/api/reviews/\d+/retry", path):
                number = int(path.split("/")[3])
                self._json(s.hub.request_ci_retry(
                    number, demo=bool(body.get("demo")),
                ))
            else:
                self._error("not found", 404)
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            self._error(str(e))
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {runtime.public_message(e)}", 500)


# One session per process (the setup screen, the dashboard and the editors it launches share it).

class Session(session.Session):
    hub_class = Hub   # the session opens this module's dashboard on its clone


SESSION = Session()


def console_messages(city: "str | None", setup: bool, saved_auth: bool) -> list[str]:
    """What the terminal says under the hub's address, in the screen's language: in every start,
    that closing this window stops the tools and how to start them again (the window is the
    tools' only process, #57); during the first setup, that the wait is normal."""
    lines = ["  " + tr("common.keep_terminal_open", "Keep this window open: closing it stops the tools.")]
    if city:
        lines.append("  " + tr("hub.console_next_time", "To start the tools again later: {command}",
                               command=shortcuts.start_command(city)))
    if setup:
        lines.append("  " + tr("hub.console_setup_running", "Status: initial setup in progress (normal — not stopped)"))
        if saved_auth:
            lines.append("  " + tr("hub.console_saved_auth", "The account recorded for this city is used. "
                                   "The account screen may be skipped"))
        lines.append("  " + tr("hub.console_follow_browser", "Follow the instructions in the browser"))
    return lines


def main() -> None:
    runtime.console_safe()
    # Nothing from the person's shell may steer git (identity overrides, askpass programs,
    # repository routing): the clone's config and the city's account are the only sources.
    accounts.scrub_git_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="local clone of the city repository (auto-detected if omitted)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    runtime.migrate_config()   # once, before anything reads the settings (S18); not for --help
    accounts.migrate_tokens()  # TEMPORARY (V2): remove in v1.7.0, once every hub has started once on v1.6

    # Clone location: explicit → ancestor detection → the remembered clone OF THIS CITY.
    # A remembered clone of another city is never adopted (the start script's city
    # selects the clone; the clone's 4dcitygml.json defines the city).
    city = runtime.requested_city()
    repo_root = args.repo or runtime.detect_repo()
    if repo_root is not None and args.repo is None and city and runtime.clone_city(repo_root) != city:
        repo_root = None
    if repo_root is None:
        repo_root = runtime.saved_clone_for(city) if city else runtime.last_clone()

    if repo_root is not None and runtime.has_building_data(Path(repo_root)):
        # Already running for this clone (the same starter double-clicked twice)? Open it instead —
        # but only the same version: an older hub left running (an update, or hub-v1.0.x without a
        # tag) must not swallow the start, or the person never sees the new version.
        running = running_hub_for(repo_root, args.port)
        if running and running[1] == runtime.running_hub_tag():
            print(f"citygml-hub already running for this city: {running[0]}")
            if not args.no_browser:
                webbrowser.open(running[0])
            return
        if running:
            print(f"Another version of the hub ({running[1] or 'before hub-v1.2.0'}) is still serving this city at "
                  f"{running[0]}. Close it when you are done there; this version starts on another port.")
    else:
        repo_root = None   # start in initial-setup mode (the clone runs from the browser)

    # One session: the clone (data sync in the background, one version check) and the
    # account recorded for the city — chosen on the account screen when none is recorded.
    SESSION.start(repo_root, city)
    if SESSION.hub is not None:
        print(f"  Repository: {SESSION.hub.root}")

    # Another city's hub may hold the default port: step to a free one (+2, like the child tools).
    port = runtime.free_port(args.port)
    if port != args.port:
        print(f"Port {args.port} is in use (another city?); using {port}")
    banner = ["citygml-hub: {url}", *console_messages(SESSION.city, SESSION.hub is None, bool(SESSION.token()))]
    runtime.serve(Handler, port, banner, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
