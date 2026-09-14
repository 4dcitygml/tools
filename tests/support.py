# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One knob for the tools' tests: a temporary HOME.

Every file the tools read or write on a computer is derived from HOME by
tools/runtime.py (settings file, account files, tools folder, legacy files), so a
test isolates itself completely by entering TempHome. GitHub is replaced at the
single seam runtime.github_api (FakeGitHub), git by runtime.git_exe. Nothing in a
test needs to know which module holds which path.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import runtime  # noqa: E402
import accounts  # noqa: E402

TOKYO = "4dcitygml/sample-tokyo-station"
MUNICH = "4dcitygml/sample-munich-station"

# Everything the tools read from the environment, cleared for the duration of a test.
ENV_KEYS = ("HOME", "USERPROFILE", "CITYGML_TOOLS_DIR", "CITYGML_UPSTREAM", "CITYGML_ACCOUNT",
            "CITYGML_HUB_TAG", "CITYGML_HUB_NO_GH", "CITYGML_LANG", "CITYGML_OAUTH_CLIENT_ID",
            "LC_ALL", "LC_MESSAGES", "LANG", "GH_CONFIG_DIR")


def load_app(name: str, rel: str):
    """Load one of the apps by path under its own module name (as the launchers run them)."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TempHome:
    """A temporary HOME for one test (English UI, no GitHub CLI, no city unless given).

        with TempHome() as home: ...
    or in unittest: self.home = self.enterContext(TempHome())  (3.11+), else
        self._home = TempHome(); self.home = self._home.__enter__()  /  self._home.__exit__()
    """

    def __init__(self, *, lang: "str | None" = "en", city: "str | None" = None):
        self._lang, self._city = lang, city
        self._tmp = None
        self._saved: dict = {}

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name).resolve()
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
        os.environ["CITYGML_HUB_NO_GH"] = "1"
        if self._lang:
            os.environ["CITYGML_LANG"] = self._lang
        if self._city:
            os.environ["CITYGML_UPSTREAM"] = self._city
        runtime.reset_caches()
        return home

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        runtime.reset_caches()
        self._tmp.cleanup()
        return False


def make_clone(root: Path, city: str = TOKYO, origin: "str | None" = None) -> Path:
    """A minimal clone: 4dcitygml.json naming the city and one building file; a git
    repository with the given origin when origin is not None (git must be installed)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "4dcitygml.json").write_text(json.dumps({"repo": city, "data_dirs": ["d"]}), encoding="utf-8")
    (root / "d").mkdir(exist_ok=True)
    (root / "d" / "a.gml").write_text("<x/>", encoding="utf-8")
    if origin is not None:
        git = shutil.which("git")
        subprocess.run([git, "-C", str(root), "init", "-q"], check=True)
        subprocess.run([git, "-C", str(root), "remote", "add", "origin", origin], check=True)
    return root


class FakeGitHub:
    """GitHub behind runtime.github_api: users by token, forks by login, an offline switch.

    tokens: {token: {"login", "id"}}; forks: logins that own a copy of the upstream city.
    Every call is recorded in `calls` as (method, path)."""

    def __init__(self, tokens=None, forks=(), upstream: str = TOKYO):
        self.tokens = dict(tokens or {"tok-a": {"login": "alice", "id": 1}, "tok-b": {"login": "bob", "id": 2},
                                      "tok-m": {"login": "mach", "id": 3}})
        self.forks = set(forks or {"alice"})
        self.upstream = upstream
        self.repo = upstream.split("/")[-1]
        self.offline = False
        self.calls: list = []

    def api(self, path, token, method="GET", payload=None, timeout=30):
        self.calls.append((method, path))
        if self.offline:
            raise urllib.error.URLError("offline")
        user = self.tokens.get(token)
        if path == "/user":
            return (200, dict(user)) if user else (401, {"message": "Bad credentials"})
        if path == f"/repos/{self.upstream}/forks" and method == "POST":
            self.forks.add(user["login"])
            return 202, {"full_name": f"{user['login']}/{self.repo}"}
        if path == f"/repos/{self.upstream}":
            return 200, {"full_name": self.upstream}
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "repos" and parts[3] == self.repo:
            if parts[2] in self.forks:
                return 200, {"fork": True, "full_name": f"{parts[2]}/{self.repo}"}
            return 404, {}
        return 404, {}

    def install(self):
        """Patch runtime.github_api with this fake (a context manager)."""
        return patch.object(runtime, "github_api", self.api)


@contextlib.contextmanager
def no_git():
    """As if git were not installed."""
    with patch.object(runtime, "git_exe", lambda: None):
        yield


@contextlib.contextmanager
def fake_git(exe: "str | None", bundled: bool = False):
    """A specific git executable (and whether it counts as the bundled one)."""
    with patch.object(runtime, "git_exe", lambda: exe), patch.object(runtime, "git_bundled", lambda: bundled):
        yield


def fresh_hub(hub):
    """A clean hub process state (one Session) for a test; returns it."""
    hub.SESSION = hub.Session()
    return hub.SESSION


def bind_account(city: str, login: str, token: str, user_id: int) -> None:
    """A stored account bound to a city (what the account screen does)."""
    accounts.save_account(login, token, user_id)
    accounts.bind_city_login(city, login)
