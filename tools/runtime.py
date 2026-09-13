#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Runtime shared by the hub and the editors (docs/client-runtime-contract.md §5).

The one place that knows the installed layout, the person's files (settings,
credentials — all derived from HOME), the git and python to run, how a clone
names its city, and where the language and theme packs are. The hub and the
editors import it by name: it sits next to them in the installed tree
(`program/`) and one level up in the source tree (`tools/`).

Everything here is fail-open where the contract says so: a missing pack, a
missing sync module or an unreadable settings file never stops a tool.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# ---- Layout --------------------------------------------------------------
PROGRAM_DIR = Path(__file__).resolve().parent   # program/ when installed, tools/ in the source tree
LIB_SUBDIR = "program"
# Where bundled items live (PortableGit, PythonPortable, preset.json): next to this
# file, or in the hidden program/ folder next to a launcher.
BUNDLE_DIRS = [PROGRAM_DIR, PROGRAM_DIR / LIB_SUBDIR]

# ---- The person's files (runtime contract §3) ----------------------------
CONFIG_PATH = Path.home() / ".citygml_attr_editor.json"   # settings shared by every tool
AUTH_PATH = Path.home() / ".citygml_auth.json"             # the GitHub connection made in the hub
# The credentials the bundled Git uses. Embedding the token in the origin URL would
# leave it in .git/config where it can leak, so git's standard store helper reads a
# dedicated file (0600) instead.
GIT_CRED_PATH = Path.home() / ".citygml_git_credentials"

# Default (demo city). The actual target is resolved by upstream_url().
UPSTREAM_URL = "https://github.com/4dcitygml/sample-tokyo-station"


def load_module(name: str, path: Path):
    """Load a single-file module by path (the packs and the sync module are plain files)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- Git and Python to run -----------------------------------------------
_git_resolved: "tuple[str, bool] | None" = None


def _system_git_is_configured(exe: str) -> bool:
    """Whether the Git on PATH has the global settings needed for committing."""
    try:
        for key in ("user.name", "user.email"):
            r = subprocess.run(
                [exe, "config", "--global", "--get", key],
                capture_output=True, text=True, errors="replace", timeout=5,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def git_cmd() -> "tuple[str | None, bool]":
    """The git executable to use and whether it is the bundled Git.

    The Windows zip bundles MinGit under the compatibility name `PortableGit/`.
    If the Git on PATH has user.name / user.email set globally, prefer it along
    with its existing authentication environment; otherwise fall back to the
    bundled Git. The decision is made once per process.
    """
    global _git_resolved
    if _git_resolved is None:
        sys_git = shutil.which("git")
        found: "tuple[str, bool] | None" = (
            (sys_git, False) if sys_git and _system_git_is_configured(sys_git) else None
        )
        if found is None:
            for base in BUNDLE_DIRS:
                for name in ("git.exe", "git"):
                    cand = base / "PortableGit" / "cmd" / name
                    if cand.is_file():
                        found = (str(cand), True)
                        break
                if found:
                    break
        if found is None:
            found = (sys_git, False) if sys_git else ("", False)
        _git_resolved = found
    exe, bundled = _git_resolved
    return (exe or None), bundled


def git_base_args(*, net: bool = False) -> list:
    """Leading arguments of every git invocation.

    With the bundled Git and a token from our own authentication (device flow),
    use **only** that: the leading empty helper (credential.helper=) resets the
    system's default helper list, so no keychain or credential-manager dialog
    appears. The bundled Credential Manager (a GUI) is the last resort. The
    existing Git's own helpers are never changed.
    """
    exe, bundled = git_cmd()
    args = [exe or "git"]
    if net and bundled and GIT_CRED_PATH.is_file():
        helper = f"store --file={shlex.quote(GIT_CRED_PATH.as_posix())}"
        args += ["-c", "credential.helper=",
                 "-c", f"credential.https://github.com.helper={helper}"]
    elif net and bundled:
        args += ["-c", "credential.helper=manager"]
    return args


_py_resolved: "tuple[str, bool] | None" = None


def python_cmd() -> "tuple[str, bool]":
    """The python used to launch child tools and whether it is the bundled portable one.

    Same approach as PortableGit: prefer `PythonPortable/` in the bundle if present,
    so the .py edition works on Windows without an installed Python. Falls back to
    sys.executable.
    """
    global _py_resolved
    if _py_resolved is None:
        found: "str | None" = None
        for base in BUNDLE_DIRS:
            for rel in ("python.exe", "pythonw.exe", "bin/python3", "python3"):
                cand = base / "PythonPortable" / rel
                if cand.is_file():
                    found = str(cand)
                    break
            if found:
                break
        _py_resolved = (found or sys.executable, found is not None)
    return _py_resolved


# ---- Settings, presets, the clone ----------------------------------------
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    """Merge cfg into the shared settings file.

    The file is shared by the hub and the editors (the hub keeps one clone per city
    under `cities`): never replace the whole file, only the keys given. Writers merge
    and write atomically (temp file + rename), so a second tool writing at the same
    moment can never leave a torn or truncated file. Failure to save never stops a
    tool (the clone it records already exists)."""
    try:
        current = load_config()
        current.update(cfg)
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass


def load_preset(app_dir: "Path | None" = None) -> dict:
    """preset.json (distribution defaults such as oauthClientId): next to the app that
    owns it in the source tree, in the bundle folders when installed."""
    for base in ([app_dir] if app_dir else []) + BUNDLE_DIRS:
        p = base / "preset.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return {}


def has_building_data(root) -> bool:
    """Whether the clone has building data (PLATEAU layout or data_dirs in 4dcitygml.json)."""
    root = Path(root)
    try:
        if any(root.glob("*/udx/bldg")):
            return True
    except OSError:
        return False
    try:
        meta = json.loads((root / "4dcitygml.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(
        (root / str(rel)).is_dir() and any((root / str(rel)).glob("*.gml"))
        for rel in (meta.get("data_dirs") or [])
        if isinstance(rel, str)
    )


def detect_repo(start: Path) -> "Path | None":
    """When a tool runs inside a clone, the nearest ancestor of `start` with building data."""
    for anc in [start, *start.parents]:
        try:
            if has_building_data(anc):
                return anc
        except OSError:
            # stat can raise EINVAL on special entries near the filesystem root
            continue
    return None


# ---- The city a clone belongs to -----------------------------------------
def _normalize_upstream(value: str) -> "str | None":
    """Normalize any of owner/repo, https URL, or ssh URL to an https URL."""
    v = (value or "").strip().removesuffix(".git")
    m = (re.fullmatch(r"[\w.-]+/[\w.-]+", v)
         or re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)", v)
         or re.fullmatch(r"git@github\.com:([\w.-]+/[\w.-]+)", v))
    if not m:
        return None
    nwo = m.group(1) if m.lastindex else v
    return f"https://github.com/{nwo}"


def upstream_url(root=None, ignore_env: bool = False) -> str:
    """URL of the target city repository. Priority: CITYGML_UPSTREAM > the clone's
    4dcitygml.json > the git remote `upstream` > default (demo city). The install
    script sets the city through the environment; users with a clone get it from
    4dcitygml.json. ignore_env=True asks for the clone's own city only."""
    env = None if ignore_env else _normalize_upstream(os.environ.get("CITYGML_UPSTREAM", ""))
    if env:
        return env
    if root:
        cj = Path(root) / "4dcitygml.json"
        if cj.is_file():
            try:
                got = _normalize_upstream(json.loads(cj.read_text(encoding="utf-8")).get("repo", ""))
                if got:
                    return got
            except Exception:
                pass
        git, _ = git_cmd()
        if git:
            try:
                r = subprocess.run([git, "-C", str(root), "remote", "get-url", "upstream"],
                                   capture_output=True, text=True, timeout=10)
                got = _normalize_upstream(r.stdout.strip()) if r.returncode == 0 else None
                if got:
                    return got
            except Exception:
                pass
    return UPSTREAM_URL


def upstream_nwo(root=None) -> str:
    """owner/name of the target city repository, e.g. 4dcitygml/sample-tokyo-station."""
    return upstream_url(root).rstrip("/").split("github.com/")[-1].removesuffix(".git")


# ---- Keeping the clone's main in line with the city (git_sync) -----------
_git_sync_mod = None


def git_sync_module():
    """The shared sync implementation (git_sync.py next to this file), or None when absent."""
    global _git_sync_mod
    if _git_sync_mod is not None:
        return _git_sync_mod or None
    cand = PROGRAM_DIR / "git_sync.py"
    _git_sync_mod = load_module("git_sync", cand) if cand.is_file() else False
    return _git_sync_mod or None


def sync_upstream_main(root) -> "str | None":
    """Bring the machine-managed local main in line with the clone's own city.

    The target is the city the clone names (4dcitygml.json, then the `upstream`
    remote), never the environment: CITYGML_UPSTREAM only selects which clone to
    open (runtime contract), and a child process inherits it unchanged. One
    ls-remote round trip, then a fetch with git's progress-based abort (no
    wall-clock cut-off: a large annual update may take minutes). Fail-open:
    offline, not a clone, or no sync module leaves the data as it is. Returns
    the new main commit when an update happened, else None.
    """
    mod = git_sync_module()
    exe, _ = git_cmd()
    if mod is None or not exe:
        return None
    result = mod.sync_main(Path(root).resolve(), upstream_url(root, ignore_env=True),
                           git_base_args(net=True), log=print)
    return result.get("head") if result.get("state") in ("updated", "ref-moved") else None


# ---- Theme pack and language pack (both optional) ------------------------
_theme_mod = None
_i18n_mod = None


def theme_module():
    """themes/theme_loader.py next to this file, or None when absent."""
    global _theme_mod
    if _theme_mod is not None:
        return _theme_mod or None
    cand = PROGRAM_DIR / "themes" / "theme_loader.py"
    _theme_mod = load_module("theme_loader", cand) if cand.is_file() else False
    return _theme_mod or None


def i18n_module():
    """i18n/i18n_loader.py next to this file, or None when absent."""
    global _i18n_mod
    if _i18n_mod is not None:
        return _i18n_mod or None
    cand = PROGRAM_DIR / "i18n" / "i18n_loader.py"
    _i18n_mod = load_module("i18n_loader", cand) if cand.is_file() else False
    return _i18n_mod or None


def themed_html(data: bytes, repo_root) -> bytes:
    """Apply the city repo's theme.json to the HTML. On an invalid theme.json, serve unthemed and warn."""
    mod = theme_module()
    if mod is None or repo_root is None:
        return data
    try:
        tokens = mod.resolve_theme(repo_root)
        return mod.inject_theme(data, mod.theme_css(tokens))
    except Exception as e:  # never block display (themes are decoration, not functionality)
        print(f"Ignoring theme.json: {e}", file=sys.stderr)
        return data


def localized_html(data: bytes, app: str) -> bytes:
    """Inject the UI language's catalog into the HTML (CITYGML_LANG > the shared
    setting `lang` > OS locale > en). On failure, serve as-is and warn."""
    mod = i18n_module()
    if mod is None:
        return data
    try:
        return mod.inject_i18n(data, app, mod.resolve_lang(load_config().get("lang")))
    except Exception as e:
        print(f"Ignoring language pack: {e}", file=sys.stderr)
        return data


def translate(app: str, key: str, default: str, lang: "str | None" = None, **params) -> str:
    """Translation of server-generated text for one app's catalog (fail-open).

    Even when the language pack is missing or broken, the {name} placeholders are
    applied to the default (the English source) and it is returned, never blocking
    display. `lang` selects a language explicitly (repo-facing text follows the
    repository's working language, not the person's UI language)."""
    mod = i18n_module()
    if mod is not None:
        try:
            return mod.translate(app, key, default, lang=lang, **params)
        except Exception:
            pass
    s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


# ---- Console and first-run setup -----------------------------------------
def make_console_safe() -> None:
    """Never let console output crash a tool on a narrow code page.

    On Windows a redirected stdout/stderr uses the legacy code page (cp1252,
    cp932, ...), and the embeddable Python ignores PYTHONUTF8/PYTHONIOENCODING
    (._pth isolated mode). Help text and log lines contain characters such as
    "→", so unencodable characters are escaped instead of raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


class SetupManager:
    """Run git clone in the background and report progress to the polling API.

    `messages` maps the message names below to the app's own translated text,
    `name: lambda **params: tr(key, default, **params)`. Names: clone_running,
    git_missing, bad_url, dest_not_empty, clone_start, clone_size_note, clone_done,
    clone_failed.
    """

    def __init__(self, messages: "dict[str, Callable[..., str]]") -> None:
        self._messages = messages
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: "str | None" = None
        self.dest: "str | None" = None
        self.lines: list = []

    def _msg(self, name: str, **params) -> str:
        return self._messages[name](**params)

    def state(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "gitAvailable": git_cmd()[0] is not None,
                "running": self.running,
                "done": self.done,
                "error": self.error,
                "dest": self.dest,
                "log": self.lines[-8:],
            }

    def start(self, url: str, dest: str) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError(self._msg("clone_running"))
            if git_cmd()[0] is None:
                raise RuntimeError(self._msg("git_missing"))
            url = url.strip()
            if not re.match(r"^(https://|git@|file://|/)", url):
                raise ValueError(self._msg("bad_url"))
            dest_path = Path(dest).expanduser()
            if dest_path.exists() and any(dest_path.iterdir()):
                raise ValueError(self._msg("dest_not_empty", dest=dest_path))
            self.running, self.done, self.error = True, False, None
            self.dest = str(dest_path)
            self.lines = [self._msg("clone_start", url=url), self._msg("clone_size_note")]
        threading.Thread(target=self._run, args=(url, str(dest_path)), daemon=True).start()

    def _run(self, url: str, dest: str) -> None:
        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                [*git_base_args(net=True), "clone", "--progress", url, dest],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
            )
            assert proc.stdout is not None
            for raw in proc.stdout:
                # git progress arrives \r-separated, so keep only the last segment
                seg = raw.rstrip("\r\n").split("\r")[-1].strip()
                if seg:
                    with self.lock:
                        if self.lines and self.lines[-1].split(":")[0] == seg.split(":")[0]:
                            self.lines[-1] = seg  # overwrite same-kind progress lines
                        else:
                            self.lines.append(seg)
            code = proc.wait()
            with self.lock:
                self.running = False
                if code == 0:
                    self.done = True
                    self.lines.append(self._msg("clone_done"))
                else:
                    self.error = self._msg("clone_failed", code=code)
        except Exception as e:  # noqa: BLE001 — shown in the UI
            with self.lock:
                self.running = False
                self.error = f"{type(e).__name__}: {e}"
