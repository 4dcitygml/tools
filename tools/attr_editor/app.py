#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""PLATEAU CityGML attribute editor — local server.

Runs a lightweight HTTP server on top of a local clone of sample-tokyo-station, and
lets the browser UI (index.html / viewer.html) browse/edit building attributes
and create PRs.

- GML parsing happens server-side; the browser only gets lightweight JSON (memory cache).
- Edits never re-serialize the XML: only leaf values in the original byte stream
  are replaced via string substitution (UTF-8 BOM, CRLF, indentation and element
  order preserved byte-for-byte; consistent with the W6 minimal-diff gate).
- Change proposals are created automatically via the GitHub API after
  branch → commit (Building: trailer) → push, reusing the OAuth connection saved
  by the hub. Standalone use without the hub falls back to gh, then a compare URL.

Usage:
    python app.py --repo ~/sample-tokyo-station [--port 8765] [--no-browser]
    # --repo may be omitted when placed inside the clone (tools/attr_editor/ etc.; auto-detected)
    # If no clone is found, the first-run setup screen (clone GUI) is shown

Distributed as plain .py with a bundled Python (PythonPortable) on Windows
(packaging/start-windows.bat; decision 2026-08-28 — no frozen executable). The
frozen-mode fallbacks (_MEIPASS / sys.frozen) are kept as harmless no-ops.
The clone location is remembered in ~/.citygml_attr_editor.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

APP_DIR = Path(__file__).resolve().parent
# With PyInstaller onefile, bundled data (index.html etc.) lives in the _MEIPASS extraction dir
RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
# For a frozen executable, its location (search base for the bundled PortableGit)
EXE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR
# The hub's distribution zip gathers bundled items the user should not see into a
# "program/" dir next to the launcher. For compatibility with the standalone
# attr-editor distribution, search both the base dir and the subdirectory
# (tex_editor also shares this module's git_cmd).
LIB_SUBDIR = "program"
BUNDLE_DIRS = list(dict.fromkeys(
    [d for base in (EXE_DIR, APP_DIR) for d in (base, base / LIB_SUBDIR)]
))
CONFIG_PATH = Path.home() / ".citygml_attr_editor.json"
GIT_CRED_PATH = Path.home() / ".citygml_git_credentials"
AUTH_PATH = Path.home() / ".citygml_auth.json"
UPSTREAM_URL = "https://github.com/4dcitygml/sample-tokyo-station"  # Default (demo city). The actual target is resolved by upstream_url()

_git_resolved: "tuple[str, bool] | None" = None

# ---- Theme pack (shares tools/themes/theme_loader.py; runs unthemed if absent) ----
_theme_mod = None


def theme_module():
    global _theme_mod
    if _theme_mod is not None:
        return _theme_mod or None
    import importlib.util
    for cand in (RES_DIR.parent / "themes" / "theme_loader.py",
                 APP_DIR.parent / "themes" / "theme_loader.py"):
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("theme_loader", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _theme_mod = mod
            return mod
    _theme_mod = False
    return None


def city_map_config(repo_root) -> dict:
    """Read map settings (tiles/center/zoom) from the clone's 4dcitygml.json.

    Values are fail-closed (only validated ones are adopted). If absent, an empty
    dict is returned and the frontend uses its defaults (GSI, fitBounds to tile bounds).
    """
    if repo_root is None:
        return {}
    try:
        meta = json.loads((Path(repo_root) / "4dcitygml.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    m = meta.get("map") if isinstance(meta, dict) else None
    if not isinstance(m, dict):
        return {}
    out: dict = {}
    if m.get("tiles") in ("gsi", "osm"):
        out["tiles"] = m["tiles"]
    c = m.get("center")
    if (isinstance(c, list) and len(c) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in c)
            and -90 <= c[0] <= 90 and -180 <= c[1] <= 180):
        out["center"] = [float(c[0]), float(c[1])]
    z = m.get("zoom")
    if isinstance(z, (int, float)) and not isinstance(z, bool) and 1 <= z <= 19:
        out["zoom"] = int(z)
    return out


def city_map_html(data: bytes, repo_root) -> bytes:
    """Inject the map settings into the page as window.CITY_MAP (pass through if unset)."""
    cfg = city_map_config(repo_root)
    if not cfg:
        return data
    payload = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    script = f"<script>window.CITY_MAP = {payload};</script>".encode("utf-8")
    i = data.find(b"</head>")
    if i < 0:
        return script + data
    return data[:i] + script + data[i:]


def themed_html(data: bytes, repo_root) -> bytes:
    """Apply the city repo's theme.json to the HTML. On invalid theme.json, serve unthemed and warn."""
    mod = theme_module()
    if mod is None or repo_root is None:
        return data
    try:
        tokens = mod.resolve_theme(repo_root)
        return mod.inject_theme(data, mod.theme_css(tokens))
    except Exception as e:  # never block display (themes are decoration, not functionality)
        print(f"Ignoring theme.json: {e}", file=sys.stderr)
        return data


# ---- Language pack (shares tools/i18n/i18n_loader.py; runs untranslated if absent) ----
_i18n_mod = None


def i18n_module():
    global _i18n_mod
    if _i18n_mod is not None:
        return _i18n_mod or None
    import importlib.util
    for cand in (RES_DIR.parent / "i18n" / "i18n_loader.py",
                 APP_DIR.parent / "i18n" / "i18n_loader.py"):
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("i18n_loader", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _i18n_mod = mod
            return mod
    _i18n_mod = False
    return None


def localized_html(data: bytes, app: str = "attr_editor") -> bytes:
    """Inject the selected language's catalog into the HTML. On failure, serve as-is and warn."""
    mod = i18n_module()
    if mod is None:
        return data
    try:
        cfg_lang = None
        try:
            cfg_lang = load_config().get("lang")
        except Exception:
            pass
        return mod.inject_i18n(data, app, mod.resolve_lang(cfg_lang))
    except Exception as e:
        print(f"Ignoring language pack: {e}", file=sys.stderr)
        return data


def tr(key: str, default: str, **params) -> str:
    """Translate server-side (Python) generated text (fail-open).

    Even when the i18n module is missing or broken, return the default (English
    source) with {name} placeholders applied, so display never stops.
    """
    mod = i18n_module()
    if mod is not None:
        try:
            return mod.translate("attr_editor", key, default, **params)
        except Exception:
            pass
    s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def tr_lang(lang: str, key: str, default: str, **params) -> str:
    """tr() with an explicit language, for repo-facing text (PR title/body).

    Repo-facing text follows the repository's working language, not the UI
    language of the person editing. Fail-open like tr()."""
    mod = i18n_module()
    if mod is not None:
        try:
            return mod.translate("attr_editor", key, default, lang=lang, **params)
        except Exception:
            pass
    s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def norm_repo_lang(value: object) -> str:
    """Normalize 4dcitygml.json "lang" (BCP 47) to a catalog language; en if absent."""
    primary = str(value or "").split("-")[0].strip().lower()
    return primary or "en"


def read_repo_lang(root: "Path | str") -> str:
    """Repo working language from <root>/4dcitygml.json (en when absent/broken)."""
    try:
        meta = json.loads((Path(root) / "4dcitygml.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    return norm_repo_lang(meta.get("lang"))


def created_by_trailer(root: "Path | str", app: str) -> str:
    """Client-identification commit trailer (exchange contract Part B, SHOULD).

    `Created-By: <app>/<version>` — the version is the clone's pinned tools
    release tag (install/tools-release.json), omitted while unset. Third-party
    clients emit their own name here; we emit ours for the same reason
    (reachability and ecosystem credit), so the convention is dogfooded."""
    tag = ""
    try:
        tag = str(json.loads(
            (Path(root) / "install" / "tools-release.json")
            .read_text(encoding="utf-8")).get("tag") or "")
    except (OSError, ValueError):
        pass
    return f"Created-By: {app}/{tag}" if tag else f"Created-By: {app}"


def load_hub_token() -> str:
    """Read the GitHub token connected via the hub (empty string if none)."""
    try:
        token = json.loads(AUTH_PATH.read_text(encoding="utf-8")).get("token", "")
        return str(token) if token else ""
    except (OSError, ValueError):
        return ""


def github_api(path: str, token: str, method: str = "GET",
               payload: "dict | None" = None, timeout: int = 30) -> "tuple[int, dict]":
    """Call the GitHub REST API with the saved OAuth connection (no gh command needed)."""
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "citygml-attr-editor")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8") or "{}"
            return res.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, {"message": tr("editor.api_conn_error", "Connection error: {reason}",
                                 reason=exc.reason if hasattr(exc, "reason") else exc)}




def _normalize_upstream(value: str) -> "str | None":
    """Normalize any of owner/repo, https URL, or ssh URL forms into an https URL."""
    v = (value or "").strip().removesuffix(".git")
    m = (re.fullmatch(r"[\w.-]+/[\w.-]+", v)
         or re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)", v)
         or re.fullmatch(r"git@github\.com:([\w.-]+/[\w.-]+)", v))
    if not m:
        return None
    nwo = m.group(1) if m.lastindex else v
    return f"https://github.com/{nwo}"


def upstream_url(root=None) -> str:
    """URL of the target city repo. Priority: CITYGML_UPSTREAM > the clone's
    4dcitygml.json > git remote upstream > default (demo city). Install scripts
    set the city via the env var; cloned users get it auto-determined from
    4dcitygml.json (plan document §5.1b)."""
    env = _normalize_upstream(os.environ.get("CITYGML_UPSTREAM", ""))
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
    return upstream_url(root).rstrip("/").split("github.com/")[-1].removesuffix(".git")


def _system_git_is_configured(exe: str) -> bool:
    """Whether the Git on PATH has the global config needed for commits."""
    try:
        for key in ("user.name", "user.email"):
            r = subprocess.run(
                [exe, "config", "--global", "--get", key],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def git_cmd() -> "tuple[str | None, bool]":
    """Return the git executable to use and whether it is the bundled Git.

    The Windows "all-in-one zip" distribution bundles Git at the top level or
    under `program/PortableGit/`. If the Git on PATH has user.name / user.email
    configured globally, prefer it (along with its existing auth environment);
    otherwise fall back to the bundled Git. When using the bundled Git,
    explicitly use the hub's dedicated store, or the bundled GCM if absent.
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
    """Leading args for git invocations. Only the bundled Git uses the dedicated store or GCM."""
    exe, bundled = git_cmd()
    args = [exe or "git"]
    if net and bundled and GIT_CRED_PATH.is_file():
        helper = f"store --file={shlex.quote(GIT_CRED_PATH.as_posix())}"
        args += ["-c", "credential.helper=",
                 "-c", f"credential.https://github.com.helper={helper}"]
    elif net and bundled:
        args += ["-c", "credential.helper=manager"]
    return args


FETCH_TIMEOUT = 15  # [s] upstream sync is fail-open: the network must never block startup


def sync_upstream_main(root) -> "str | None":
    """Bring the machine-managed local main in line with the upstream city repo.

    Local main is a mirror the tools maintain; user edits live only on the
    branches the tools create, which are never touched here. Fast-forwards when
    possible, hard-resets when histories diverged (the practice repo rewrites
    main daily). Skips with a console warning when tracked files are modified,
    and silently when offline or not a git clone (fail-open).
    Returns the new main commit when an update happened, else None.
    """
    root = Path(root).resolve()
    exe, _ = git_cmd()
    if not exe:
        return None

    def run(*args: str, net: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*git_base_args(net=net), "-C", str(root), *args],
            capture_output=True, text=True, timeout=FETCH_TIMEOUT,
        )

    try:
        if run("rev-parse", "--git-dir").returncode != 0:
            return None  # a bare data folder, not a clone
        if run("fetch", "--quiet", upstream_url(root), "main", net=True).returncode != 0:
            return None  # offline etc.
        new = run("rev-parse", "FETCH_HEAD").stdout.strip()
        if not new:
            return None
        branch = run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
            # main is not checked out: move only the ref; the working tree stays untouched
            cur = run("rev-parse", "refs/heads/main").stdout.strip()
            if new != cur and run("branch", "-f", "main", new).returncode == 0:
                print(f"Upstream update merged into main ({new[:12]})")
                return new
            return None
        cur = run("rev-parse", "HEAD").stdout.strip()
        if new == cur:
            return None
        dirty = [
            ln for ln in run("status", "--porcelain").stdout.splitlines()
            if ln.strip() and not ln.startswith("??")
        ]
        if dirty:
            print("Warning: skipped upstream sync due to local unsaved changes")
            return None
        if run("merge", "--ff-only", "FETCH_HEAD").returncode == 0:
            print(f"Upstream update merged (main → {new[:12]})")
            return new
        if run("reset", "--hard", "FETCH_HEAD").returncode == 0:
            print(f"Updated main to match upstream (was {cur[:12]}; old state remains in reflog)")
            return new
    except (OSError, subprocess.SubprocessError):
        pass
    return None


NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "app": "http://www.opengis.net/citygml/appearance/2.0",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
}


_GRS80_A = 6378137.0
_GRS80_F = 1 / 298.257222101
_US_FT = 1200.0 / 3937.0  # US survey foot [m]


def _tm_inverse(E: float, N: float, lon0_deg: float, *, k0: float = 0.9996,
                E0: float = 500000.0, N0: float = 0.0) -> "tuple[float, float]":
    """Inverse transverse Mercator (UTM) → (lat, lon) [deg]. GRS80 (practically equal to ETRS89/WGS84)."""
    a, f = _GRS80_A, _GRS80_F
    e2 = f * (2 - f)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    M = (N - N0) / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    ep2 = e2 / (1 - e2)
    C1 = ep2 * math.cos(phi1) ** 2
    T1 = math.tan(phi1) ** 2
    N1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = (E - E0) / (N1 * k0)
    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = math.radians(lon0_deg) + (
        D - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


def _lcc_inverse_2263(E_ft: float, N_ft: float) -> "tuple[float, float]":
    """Inverse EPSG:2263 (NAD83 / New York Long Island, US feet) → (lat, lon)."""
    a, f = _GRS80_A, _GRS80_F
    e = math.sqrt(f * (2 - f))
    lat1, lat2 = math.radians(41 + 2 / 60), math.radians(40 + 40 / 60)
    lat0, lon0 = math.radians(40 + 10 / 60), math.radians(-74.0)
    E0 = 984250.0 * _US_FT
    x, y = E_ft * _US_FT - E0, N_ft * _US_FT

    def m(phi):
        return math.cos(phi) / math.sqrt(1 - e ** 2 * math.sin(phi) ** 2)

    def t(phi):
        return (math.tan(math.pi / 4 - phi / 2)
                / ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2))

    n = (math.log(m(lat1)) - math.log(m(lat2))) / (math.log(t(lat1)) - math.log(t(lat2)))
    F = m(lat1) / (n * t(lat1) ** n)
    rho0 = a * F * t(lat0) ** n
    rho = math.copysign(math.hypot(x, rho0 - y), n)
    tp = (rho / (a * F)) ** (1 / n)
    theta = math.atan2(x, rho0 - y)
    phi = math.pi / 2 - 2 * math.atan(tp)
    for _ in range(6):
        phi = math.pi / 2 - 2 * math.atan(
            tp * ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2))
    return math.degrees(phi), math.degrees(theta / n + lon0)


def crs_transformer(srs_name: str):
    """srsName → (x, y) -> (lat, lon) transformer. Lat/lon systems (PLATEAU etc.) get None (no conversion needed).

    Formula implementation without external dependencies. Supports: UTM
    (ETRS89/WGS84, urn:adv notation and EPSG:258xx/326xx) and EPSG:2263
    (NY Long Island). Unknown projections fall back to None (previous behavior = no conversion).
    """
    s = str(srs_name or "")
    m = re.search(r"UTM[ _]?zone[ _]?(\d{1,2})|UTM(\d{1,2})", s)
    if m:
        zone = int(m.group(1) or m.group(2))
        if 1 <= zone <= 60:
            return lambda x, y: _tm_inverse(x, y, zone * 6 - 183)
    m = re.search(r"EPSG:+(\d+)", s)
    if m:
        code = int(m.group(1))
        if 25801 <= code <= 25860:  # ETRS89 / UTM
            zone = code - 25800
            return lambda x, y: _tm_inverse(x, y, zone * 6 - 183)
        if 32601 <= code <= 32660:  # WGS84 / UTM north
            zone = code - 32600
            return lambda x, y: _tm_inverse(x, y, zone * 6 - 183)
        if code == 2263:
            def tf(x, y):
                return _lcc_inverse_2263(x, y)
            tf.z_scale = _US_FT  # vertical is also US feet → the caller multiplies z by this
            return tf
    return None


def stable_building_id_from_span(
    span: bytes,
    gid: str,
    bid_type: str = "uro:buildingID",
    invalid_values: "set[str] | frozenset[str] | tuple[str, ...]" = (),
) -> str:
    """Resolve the stable ID from a building span (building_id.type in 4dcitygml.json).

    - "uro:buildingID" (default, PLATEAU): the value of <uro:buildingID>
    - "gml:id": the gml:id itself (munich etc.)
    - "gen:<NAME>": the value of generic attribute <NAME> (newyork's BIN etc.)
    Falls back to gid when not found or when the source value is listed in
    building_id.invalid_values (for example an upstream placeholder ID).
    """
    if bid_type == "gml:id":
        return gid
    if bid_type.startswith("gen:"):
        name = re.escape(bid_type[4:].encode("utf-8"))
        m = re.search(
            rb'<(?:\w+:)?stringAttribute\s+name="' + name
            + rb'"\s*>\s*<(?:\w+:)?value>([^<]+)</', span)
        value = m.group(1).decode("utf-8").strip() if m else ""
        return value if value and value not in invalid_values else gid
    hit = _BUILDINGID_RE.search(span)
    value = hit.group(1).decode("utf-8").strip() if hit else ""
    return value if value and value not in invalid_values else gid


def ns_for_root(root: "ET.Element") -> dict:
    """Return the namespace dict matching the file's CityGML version.

    PLATEAU is 2.0 (NS as-is). CityGML 1.0-family data (munich etc.) uses a
    root element namespace of `…/citygml/1.0`, so only the version part is
    rewritten (gml is shared by both versions).
    """
    m = re.match(r"\{(.+?)\}", root.tag or "")
    if m and m.group(1).endswith("/citygml/1.0"):
        return {k: v.replace("/2.0", "/1.0") for k, v in NS.items()}
    return NS

# For QName reconstruction (namespace URI → conventional prefix). Used for source-note keys (R2-2)
PREFIX_BY_URI = {
    "http://www.opengis.net/citygml/2.0": "core",
    "http://www.opengis.net/citygml/building/2.0": "bldg",
    "http://www.opengis.net/gml": "gml",
    "http://www.opengis.net/citygml/appearance/2.0": "app",
    "http://www.opengis.net/citygml/generics/2.0": "gen",
    "http://www.opengis.net/citygml/relief/2.0": "dem",
    "https://www.geospatial.jp/iur/uro/3.2": "uro",
    "https://www.geospatial.jp/iur/uro/3.1": "uro",
    "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0": "xAL",
}

# Source recording rules (docs/provenance-rules.md)
SRC_SET_NAME = "出典"  # R2-1: name of the gen:genericAttributeSet (at most 1 per building)
SRC_CODELIST = "DataQualityAttribute_thematicSrcDesc.xml"  # R2-3: notes use the same code table as the upper level
# Status codes not selectable as evidence for new attribute changes. Still used to display existing data.
NON_SOURCE_CODES = {"898", "999"}  # unknown / not created
_SRC_SET_RE = re.compile(
    ('<gen:genericAttributeSet name="' + SRC_SET_NAME + '"[^>]*>').encode("utf-8")
    + rb".*?</gen:genericAttributeSet>",
    re.S,
)
_THEMATIC_RE = re.compile(
    rb"<((?:\w+:)?)thematicSrcDesc\b([^>]*)>([^<]*)</(?:\w+:)?thematicSrcDesc>"
)
_CREATION_RE = re.compile(rb"<(?:\w+:)?creationDate>[^<]*</(?:\w+:)?creationDate>")

_COM_OPEN = b"<core:cityObjectMember>"
_COM_CLOSE = b"</core:cityObjectMember>"
_BUILDING_ID_RE = re.compile(rb'<(?:\w+:)?Building\b[^>]*?\sgml:id="([^"]+)"')
_BUILDINGID_RE = re.compile(rb"<(?:\w+:)?buildingID>([^<]+)</(?:\w+:)?buildingID>")

# Always read-only leaves (spec §3: gml:id, geometry, buildingID, creationDate)
READONLY_TAGS = {"buildingID", "creationDate"}
# Geometry tags (safety net rejected by the edit API; not shown in the UI)
GEOMETRY_TAGS = {"posList", "pos", "lowerCorner", "upperCorner"}


def _change_key(change: dict) -> str:
    """Stable edit key for an attribute leaf, shared between browser and server."""
    if change.get("key"):
        return str(change["key"])
    try:
        return f"{change['tag']}#{int(change['index'])}"
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(tr(
            "editor.err_change_ident",
            "The changed attribute could not be identified. Please reload the page",
        )) from exc


def validate_source_selections(
    changes: list[dict], source_selections: list[dict], code_table: dict[str, str]
) -> dict[str, dict[str, str]]:
    """Verify that every attribute value change has an explicitly selected, valid source."""
    leaf_changes = [c for c in changes if c.get("kind") != "src"]
    if not leaf_changes:
        return {}

    selected: dict[str, dict[str, str]] = {}
    for item in source_selections:
        key = str(item.get("key") or "")
        code = str(item.get("code") or "").strip()
        if not key:
            raise ValueError(tr(
                "editor.err_source_target_ident",
                "The attribute for this source could not be identified. Please reload the page",
            ))
        if key in selected:
            raise ValueError(tr(
                "editor.err_source_duplicate",
                "Multiple sources are specified for the same attribute",
            ))
        if not code or code not in code_table:
            raise ValueError(tr(
                "editor.err_source_not_in_list",
                "The selected source is not in the code list. Please reload the page",
            ))
        if code in NON_SOURCE_CODES:
            raise ValueError(tr(
                "editor.err_source_unknown_code",
                '"Unknown" or "Not created" cannot be chosen as the source of a changed attribute',
            ))
        selected[key] = {"code": code, "label": str(code_table[code])}

    missing = [str(c.get("label") or c.get("tag") or tr("editor.attr_fallback", "attribute"))
               for c in leaf_changes if _change_key(c) not in selected]
    if missing:
        raise ValueError(tr(
            "editor.err_source_missing",
            "Choose a source for the changed attributes: {list}",
            list=tr("editor.list_sep", ", ").join(dict.fromkeys(missing)),
        ))
    return selected


def _md_text(value: object) -> str:
    """Make user input safe as a single-line string embedded in normal PR text."""
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\r", " ").replace("\n", " ").strip()
    )


def _md_cell(value: object, blank: str = "(blank)") -> str:
    return _md_text(value).replace("|", "\\|") or blank


def _pr_change_label(lang: str, change: dict) -> str:
    """Repo-language label of a change row (known tags translate; data names pass through)."""
    tag = str(change.get("tag") or "")
    return _md_text(label_in(lang, tag, str(change.get("label") or tag)))


def pr_summary(
    leaf_changes: list[dict],
    selected_sources: dict[str, dict[str, str]],
    lang: str = "en",
) -> str:
    """Human summary paragraphs of the PR body.

    Shared by build_pr_body() and the send-dialog preview endpoint, so the
    preview is rendered by the same code as the posted PR and cannot drift."""
    blank = tr_lang(lang, "pr.blank", "(blank)")
    grouped: dict[str, dict] = {}
    for change in leaf_changes:
        src = selected_sources[_change_key(change)]
        group = grouped.setdefault(
            src["code"], {"label": src["label"], "changes": []}
        )
        group["changes"].append(change)

    summary_parts: list[str] = []
    for group in grouped.values():
        src_label = _md_text(group["label"])
        grouped_changes = group["changes"]
        if len(grouped_changes) == 1:
            change = grouped_changes[0]
            summary_parts.append(tr_lang(
                lang, "pr.checked_one",
                'Checked "{source}" and corrected "{label}" from "{old}" to "{new}".',
                source=src_label, label=_pr_change_label(lang, change),
                old=_md_text(change["old"]) or blank,
                new=_md_text(change["new"]) or blank,
            ))
        else:
            lines = [tr_lang(lang, "pr.checked_many",
                             'Checked "{source}" and corrected the following:',
                             source=src_label)]
            lines.extend(
                tr_lang(lang, "pr.checked_item", '- "{label}": from "{old}" to "{new}"',
                        label=_pr_change_label(lang, c),
                        old=_md_text(c["old"]) or blank,
                        new=_md_text(c["new"]) or blank)
                for c in grouped_changes
            )
            summary_parts.append("\n".join(lines))
    return "\n\n".join(summary_parts)


def build_pr_body(
    building_id: str,
    gid: str,
    leaf_changes: list[dict],
    selected_sources: dict[str, dict[str, str]],
    reason: str = "",
    r28_codes: list[str] | None = None,
    lang: str = "en",
) -> str:
    """Build a PR body readable by non-engineers from the changed values and sources.

    Repo-facing text: all prose and known attribute labels resolve in `lang`
    (the repository's working language), independent of the editor's UI
    language. The `<!--sec:reason-->` anchor is appended outside the translated
    heading so CI reason extraction stays language-independent."""

    def lbl(change: dict) -> str:
        return _pr_change_label(lang, change)

    blank = tr_lang(lang, "pr.blank", "(blank)")

    def cell(text: str) -> str:
        return text.replace("|", "\\|") or blank

    rows = "\n".join(
        "| " + cell(lbl(c)) + " | " + _md_cell(c["old"], blank) + " | "
        + _md_cell(c["new"], blank) + " | "
        + tr_lang(lang, "pr.source_cell", "{label} ({code})",
                  label=_md_cell(selected_sources[_change_key(c)]["label"], blank),
                  code=_md_cell(selected_sources[_change_key(c)]["code"], blank))
        + " |"
        for c in leaf_changes
    )
    source_note = ""
    if r28_codes:
        codes = ", ".join(dict.fromkeys(r28_codes))
        source_note = "\n" + tr_lang(
            lang, "pr.source_added",
            "Because the selected source was not in this building's source"
            " list, source code(s) {codes} were also added.", codes=codes) + "\n"
    supplement = _md_text(reason) or tr_lang(lang, "pr.no_notes", "No additional notes.")
    return (
        f"## {tr_lang(lang, 'pr.heading_summary', 'Summary of changes')} <!--sec:reason-->\n\n"
        + pr_summary(leaf_changes, selected_sources, lang)
        + source_note
        + f"\n\n## {tr_lang(lang, 'pr.heading_target', 'Target building')}\n\n"
        + f"- {tr_lang(lang, 'pr.label_building_id', 'Building ID')}: {_md_text(building_id)}\n"
        + f"- {tr_lang(lang, 'pr.label_internal_id', 'Internal data ID')}: `{_md_text(gid)}`\n\n"
        + f"## {tr_lang(lang, 'pr.heading_details', 'Details')}\n\n"
        + tr_lang(lang, "pr.details_columns", "| Item | Before | After | Confirmed source |")
        + "\n|---|---|---|---|\n"
        + rows
        + f"\n\n## {tr_lang(lang, 'pr.heading_notes', 'Additional notes and evidence')}\n\n"
        + supplement
        + "\n\n<sub>"
        + tr_lang(lang, "pr.cc0_footer",
                  "The data changes in this PR are provided under the"
                  " [Data Contribution Policy]({url}) (CC0 1.0).",
                  url="../blob/main/docs/data-contribution-policy.md")
        + "</sub>\n"
    )

# Display names (localname → English label). Undefined ones display the tag name as-is.
LABELS = {
    "class": "Classification",
    "usage": "Usage",
    "measuredHeight": "Measured Height",
    "storeysAboveGround": "Storeys Above Ground",
    "storeysBelowGround": "Storeys Below Ground",
    "roofType": "Roof Type",
    "yearOfConstruction": "Year of Construction",
    "creationDate": "Creation Date",
    "buildingID": "Building ID",
    "prefecture": "Prefecture",
    "city": "City/Ward",
    "branchID": "Branch ID",
    "buildingRoofEdgeArea": "Roof Edge Area",
    "buildingStructureType": "Structure Type",
    "fireproofStructureType": "Fireproof Structure Type",
    "detailedUsage": "Detailed Usage",
    "urbanPlanType": "Urban Plan Type",
    "areaClassificationType": "Area Classification Type",
    "districtsAndZonesType": "Districts and Zones Type",
    "landUseType": "Land Use Type",
    "specifiedBuildingCoverageRate": "Specified Building Coverage Rate",
    "specifiedFloorAreaRate": "Specified Floor Area Rate",
    "standardFloorAreaRate": "Standard Floor Area Rate",
    "surveyYear": "Survey Year",
    "vacancy": "Vacancy Type",
    "buildingFootprintArea": "Building Footprint Area",
    "totalFloorArea": "Total Floor Area",
    "description": "Area Name",
    "rank": "Flood Risk Rank",
    "rankOrg": "Flood Risk Rank (Custom)",
    "depth": "Estimated Flood Depth",
    "adminType": "Administrative Type",
    "scale": "Flood Scale",
    "duration": "Duration",
    "areaType": "Area Type",
    "key": "Key",
    "codeValue": "Value",
    "value": "Value",
    "thematicSrcDesc": "Thematic Attribute Source",
    "geometrySrcDescLod0": "Geometry Source LOD0",
    "geometrySrcDescLod1": "Geometry Source LOD1",
    "geometrySrcDescLod2": "Geometry Source LOD2",
    "geometrySrcDescLod3": "Geometry Source LOD3",
    "geometrySrcDescLod4": "Geometry Source LOD4",
    "appearanceSrcDescLod2": "Appearance Source LOD2",
    "appearanceSrcDescLod3": "Appearance Source LOD3",
    "appearanceSrcDescLod4": "Appearance Source LOD4",
    "srcScaleLod0": "Map Information Level LOD0",
    "srcScaleLod1": "Map Information Level LOD1",
    "srcScaleLod2": "Map Information Level LOD2",
    "publicSurveySrcDescLod0": "Public Survey Source LOD0",
    "publicSurveySrcDescLod1": "Public Survey Source LOD1",
    "publicSurveySrcDescLod2": "Public Survey Source LOD2",
    "lod1HeightType": "LOD1 Height Acquisition Method",
    "CountryName": "Country",
    "LocalityName": "Location",
    "name": "Name",
}

# localname directly under Building → attribute-card group
_GROUP_BY_TOPTAG = {
    "class": "basic",
    "usage": "basic",
    "measuredHeight": "basic",
    "storeysAboveGround": "basic",
    "storeysBelowGround": "basic",
    "roofType": "basic",
    "yearOfConstruction": "basic",
    "buildingDetailAttribute": "detail",
    "buildingIDAttribute": "ident",
    "creationDate": "ident",
    "stringAttribute": "addr",
    "genericAttributeSet": "addr",
    "address": "addr",
    "bldgDisasterRiskAttribute": "risk",
    "bldgKeyValuePairAttribute": "kv",
    "bldgDataQualityAttribute": "quality",
}

_RISK_TITLES = {
    "RiverFloodingRiskAttribute": "River Flood Risk",
    "HighTideRiskAttribute": "High Tide Flood Risk",
    "TsunamiRiskAttribute": "Tsunami Risk",
    "InlandFloodingRiskAttribute": "Inland Flood Risk",
    "LandSlideRiskAttribute": "Landslide Hazard Area",
}


def ui_label(tag: str) -> str:
    """Attribute display name in the user's UI language (label.* catalog keys)."""
    return tr(f"label.{tag}", LABELS.get(tag, tag))


def label_in(lang: str, tag: str, fallback: str = "") -> str:
    """Attribute display name in an explicit language.

    Used with lang="en" for commit messages (history stays greppable English)
    and with the repo language for PR text. Unknown tags keep the caller's
    fallback (data-derived names are not translated)."""
    if tag in LABELS:
        return tr_lang(lang, f"label.{tag}", LABELS[tag])
    return fallback or tag


# --------------------------------------------------------------------------
# Byte spans and leaf-value replacement (same approach as reconstruct_minimal.py)
# --------------------------------------------------------------------------
def _com_markers(raw: bytes) -> "tuple[bytes, bytes]":
    """Detect the actual spelling of this file's cityObjectMember open/close tags.

    PLATEAU uses `<core:cityObjectMember>`; CityGML 1.0-family international
    data (munich etc.) uses the default-namespace `<cityObjectMember>`. To keep
    byte search fast, the actual spelling is determined once (no regex) and
    scanning runs with find.
    """
    if raw.find(_COM_OPEN) >= 0:
        return _COM_OPEN, _COM_CLOSE
    m = re.search(rb"<((?:\w+:)?cityObjectMember)[ >]", raw)
    if m:
        tag = m.group(1)
        return b"<" + tag + b">", b"</" + tag + b">"
    return _COM_OPEN, _COM_CLOSE


def building_spans(raw: bytes) -> dict[str, tuple[int, int]]:
    """gml:id -> [start, end) of the cityObjectMember containing that building."""
    spans: dict[str, tuple[int, int]] = {}
    com_open, com_close = _com_markers(raw)
    pos = 0
    while True:
        start = raw.find(com_open, pos)
        if start < 0:
            break
        close = raw.find(com_close, start)
        if close < 0:
            break
        end = close + len(com_close)
        m = _BUILDING_ID_RE.search(raw, start, end)
        if m:
            spans[m.group(1).decode("utf-8")] = (start, end)
        pos = end
    return spans


def _leaf_pattern(tag_local: str) -> re.Pattern[bytes]:
    t = re.escape(tag_local.encode("utf-8"))
    return re.compile(
        rb"<(?:\w+:)?" + t + rb"\b[^>]*?>([^<]*)</(?:\w+:)?" + t + rb">"
    )


def _xml_escape(text: str) -> bytes:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ).encode("utf-8")


def _local(elem: ET.Element) -> str:
    tag = elem.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _qname(elem: ET.Element) -> str:
    """The element's QName (prefix:local). Used for source-note keys (R2-2)."""
    tag = elem.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        prefix = PREFIX_BY_URI.get(uri)
        return f"{prefix}:{local}" if prefix else local
    return tag


def _line_indent(data: bytes, pos: int) -> bytes:
    """Return the indentation (tabs/spaces) from line start up to pos (the tag-opening '<')."""
    line_start = data.rfind(b"\n", 0, pos) + 1
    ws = data[line_start:pos]
    return ws if not ws.strip() else b""


# --------------------------------------------------------------------------
# Mesh code → lat/lon bounds (JIS X 0410)
# --------------------------------------------------------------------------
def mesh_bounds(code: str) -> list[float] | None:
    """Mesh code (8 digits = level 3 / 9 digits = quarter subdivision) → [south, west, north, east]."""
    if not code.isdigit() or len(code) < 8:
        return None
    lat = int(code[0:2]) * 2 / 3
    lon = int(code[2:4]) + 100
    lat += int(code[4]) * 2 / 3 / 8
    lon += int(code[5]) / 8
    lat += int(code[6]) * 2 / 3 / 80
    lon += int(code[7]) / 80
    dlat, dlon = 2 / 3 / 80, 1 / 80
    if len(code) >= 9:
        d = int(code[8]) - 1  # 1=SW 2=SE 3=NW 4=NE
        dlat, dlon = dlat / 2, dlon / 2
        lat += (d // 2) * dlat
        lon += (d % 2) * dlon
    return [lat, lon, lat + dlat, lon + dlon]


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------
class Repo:
    def __init__(self, root: Path, data: str | None = None):
        self.root = root.resolve()
        candidates = sorted(self.root.glob("*/udx/bldg"))
        if data:
            candidates = [d for d in candidates if data in str(d.parent.parent.name)]
        if not candidates:
            # Without the PLATEAU layout (*/udx/bldg), use data_dirs from 4dcitygml.json
            # (supports international datasets such as munich=lod2_citygml, newyork=citygml)
            candidates = [
                d for d in self._declared_data_dirs()
                if (not data or data in d.name) and any(d.glob("*.gml"))
            ]
        if not candidates:
            raise RuntimeError(tr(
                "editor.err_no_bldg_data",
                "udx/bldg was not found in {root} (check the --data option)",
                root=self.root,
            ))
        # With multiple packages, use the one with the most data (total .gml bytes) (--data can override)
        self.bldg_dir = max(
            candidates,
            key=lambda d: sum(p.stat().st_size for p in d.glob("*.gml")),
        )
        if len(candidates) > 1:
            names = ", ".join(d.parent.parent.name for d in candidates)
            print(f"Data package candidates: {names}\n  → using {self.bldg_dir.parent.parent.name} (override with --data)")
        self.data_root = self.bldg_dir.parent.parent  # *_citygml_*_op
        # City metadata (4dcitygml.json): building ID type and display-language default
        try:
            meta = json.loads((self.root / "4dcitygml.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        bid_config = meta.get("building_id") or {}
        self._bid_type = str(bid_config.get("type") or "uro:buildingID")
        self._bid_invalid_values = frozenset(
            str(value) for value in (bid_config.get("invalid_values") or [])
        )
        # Repo working language (4dcitygml.json "lang"): the language of repo-facing
        # generated text (PR title/body). UI labels follow the user's language via tr().
        self._repo_lang = norm_repo_lang(meta.get("lang"))
        self.tex_override: "Path | None" = None  # --textures: for displaying swapped-in textures
        self.codelists_dir = self.data_root / "codelists"
        self._tile_cache: dict[str, dict] = {}
        self._tile_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._codelists: dict | None = None
        self._git_lock = threading.Lock()

    # ---- File listing ----
    def _declared_data_dirs(self) -> "list[Path]":
        """data_dirs declared by 4dcitygml.json at the clone root (empty if none)."""
        try:
            meta = json.loads((self.root / "4dcitygml.json").read_text(encoding="utf-8"))
            return [
                (self.root / str(rel)).resolve()
                for rel in (meta.get("data_dirs") or [])
                if (self.root / str(rel)).is_dir()
                and (self.root / str(rel)).resolve().is_relative_to(self.root)
            ]
        except (OSError, ValueError):
            return []

    def tile_files(self) -> dict[str, Path]:
        out = {}
        plateau = sorted(self.bldg_dir.glob("*_bldg_*_op.gml"))
        if plateau:
            for p in plateau:
                out[p.name.split("_", 1)[0]] = p
        else:
            # Non-PLATEAU naming (munich's 690_5335_1.gml etc.): use the filename stem as the code
            for p in sorted(self.bldg_dir.glob("*.gml")):
                out[p.stem] = p
        return out

    def _envelope_bounds(self, path: Path) -> "list[float] | None":
        """Compute [south, west, north, east] from the gml:Envelope at the top of the file.

        For tile-frame display of international data without mesh codes
        (munich/newyork etc.). Projected coordinate systems are converted to
        WGS84 via crs_transformer.
        """
        try:
            with path.open("rb") as f:
                head = f.read(8192).decode("utf-8", "replace")
        except OSError:
            return None
        low = re.search(r"<gml:lowerCorner>([-\d.eE ]+)</gml:lowerCorner>", head)
        up = re.search(r"<gml:upperCorner>([-\d.eE ]+)</gml:upperCorner>", head)
        if not (low and up):
            return None
        try:
            lo = [float(x) for x in low.group(1).split()]
            hi = [float(x) for x in up.group(1).split()]
        except ValueError:
            return None
        if len(lo) < 2 or len(hi) < 2:
            return None
        m = re.search(r'srsName="([^"]*)"', head)
        tf = crs_transformer(m.group(1) if m else "")
        if tf is not None:
            s, w = tf(lo[0], lo[1])
            n, e = tf(hi[0], hi[1])
        else:
            s, w, n, e = lo[0], lo[1], hi[0], hi[1]
        return [round(min(s, n), 7), round(min(w, e), 7),
                round(max(s, n), 7), round(max(w, e), 7)]

    def tiles_json(self) -> list[dict]:
        return [
            {
                "code": code,
                "file": p.name,
                "size": p.stat().st_size,
                "bounds": mesh_bounds(code) or self._envelope_bounds(p),
                "loaded": code in self._tile_cache,
            }
            for code, p in self.tile_files().items()
        ]

    def resources_json(self) -> list[dict]:
        """Primary documents (specification/ and metadata/) linked from the evidence card."""
        out = []
        for sub in ("specification", "metadata"):
            d = self.data_root / sub
            if d.is_dir():
                out += [
                    {"name": p.name, "path": f"/raw/{sub}/{p.name}"}
                    for p in sorted(d.iterdir())
                    if p.is_file() and not p.name.startswith(".")
                ]
        return out

    # ---- Code lists ----
    def codelists(self) -> dict:
        if self._codelists is None:
            out: dict = {}
            if self.codelists_dir.is_dir():
                for p in sorted(self.codelists_dir.glob("*.xml")):
                    try:
                        table = {}
                        root = ET.parse(str(p)).getroot()
                        for defn in root.iter():
                            if _local(defn) != "Definition":
                                continue
                            code = label = None
                            for c in defn:
                                ln = _local(c)
                                if ln == "name":
                                    code = (c.text or "").strip()
                                elif ln == "description":
                                    label = (c.text or "").strip()
                            if code is not None:
                                table[code] = label or ""
                        out[p.name] = table
                    except ET.ParseError:
                        continue
            self._codelists = out
        return self._codelists

    # ---- Tile loading (parse + cache) ----
    def tile(self, code: str) -> dict:
        cached = self._tile_cache.get(code)
        if cached is not None:
            return cached
        with self._locks_guard:
            lock = self._tile_locks.setdefault(code, threading.Lock())
        with lock:
            cached = self._tile_cache.get(code)
            if cached is None:
                cached = self._parse_tile(code)
                self._tile_cache[code] = cached
            return cached

    def _parse_tile(self, code: str) -> dict:
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        raw = path.read_bytes()
        spans = building_spans(raw)
        root = ET.fromstring(raw)
        ns = ns_for_root(root)
        # Files in projected systems (UTM, state plane, etc.) are converted to WGS84 lat/lon before returning
        env = root.find(f"{{{ns['gml']}}}boundedBy/{{{ns['gml']}}}Envelope")
        tf = crs_transformer(env.get("srsName") if env is not None else "")

        # appearance: poly_id -> {img, uv}
        texmap: dict[str, dict] = {}
        for ptex in root.iter(f"{{{ns['app']}}}ParameterizedTexture"):
            img_el = ptex.find(f"{{{ns['app']}}}imageURI")
            if img_el is None or not img_el.text:
                continue
            img = img_el.text.strip()
            for target in ptex.findall(f"{{{ns['app']}}}target"):
                pid = (target.get("uri") or "").lstrip("#")
                tc = target.find(f".//{{{ns['app']}}}textureCoordinates")
                if not pid or tc is None or not tc.text:
                    continue
                vals = tc.text.split()
                try:
                    uv = [
                        [round(float(vals[i]), 4), round(float(vals[i + 1]), 4)]
                        for i in range(0, len(vals) - 1, 2)
                    ]
                except ValueError:
                    continue
                texmap[pid] = {"img": img, "uv": uv}

        buildings: dict[str, dict] = {}
        order: list[str] = []
        for member in root.findall(f"{{{ns['core']}}}cityObjectMember"):
            bel = member.find(f"{{{ns['bldg']}}}Building")
            if bel is None:
                continue
            gid = bel.get(f"{{{ns['gml']}}}id", "")
            if not gid or gid not in spans:
                continue
            s, e = spans[gid]
            b = self._parse_building(bel, raw[s:e], ns, tf)
            buildings[gid] = b
            order.append(gid)

        return {
            "code": code,
            "file": path.name,
            "relpath": str(path.relative_to(self.root)),
            "bounds": mesh_bounds(code) or self._envelope_bounds(path),
            "order": order,
            "buildings": buildings,
            "texmap": texmap,
        }

    def _parse_building(self, bel: ET.Element, span: bytes, ns: dict = NS,
                        tf=None) -> dict:
        gid = bel.get(f"{{{ns['gml']}}}id", "")

        # ---- Geometry (same extraction as extract_building_preview.py) ----
        height_el = bel.find(f"{{{ns['bldg']}}}measuredHeight")
        height = 0.0
        if height_el is not None and height_el.text:
            try:
                height = float(height_el.text)
            except ValueError:
                pass

        zs = getattr(tf, "z_scale", 1.0) if tf is not None else 1.0
        pos_el = bel.find(
            f".//{{{ns['bldg']}}}lod0RoofEdge//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
        )
        if pos_el is None:
            pos_el = bel.find(
                f".//{{{ns['bldg']}}}lod1Solid//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
            )
        if pos_el is None:
            # LoD2-only data (munich/newyork etc.) uses the ground surface as the footprint
            pos_el = bel.find(
                f".//{{{ns['bldg']}}}GroundSurface//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
            )
        coords: list[list[float]] = []
        if pos_el is not None and pos_el.text:
            nums = [float(x) for x in pos_el.text.split()]
            if tf is not None:
                # Projected posList is in E N h order → convert to (lat, lon)
                coords = [
                    [round(v, 7) for v in tf(nums[i], nums[i + 1])]
                    for i in range(0, len(nums) - 2, 3)
                ]
            else:
                coords = [
                    [round(nums[i], 7), round(nums[i + 1], 7)]
                    for i in range(0, len(nums) - 2, 3)
                ]

        base = 0.0
        lod1_pos = bel.find(
            f".//{{{ns['bldg']}}}lod1Solid//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
        )
        if lod1_pos is None:
            # LoD2-only data uses the ground surface elevation as the base
            lod1_pos = bel.find(
                f".//{{{ns['bldg']}}}GroundSurface//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
            )
        if lod1_pos is not None and lod1_pos.text:
            parts = lod1_pos.text.split()
            if len(parts) >= 3:
                base = float(parts[2]) * zs

        lod1top = None
        lod1 = bel.find(f".//{{{ns['bldg']}}}lod1Solid")
        if lod1 is not None:
            pls = lod1.findall(f".//{{{ns['gml']}}}posList")
            if pls and pls[-1].text:
                parts = pls[-1].text.split()
                if len(parts) >= 3:
                    lod1top = round(float(parts[2]) * zs, 3)

        lod2: list[dict] = []
        # .//: with BuildingPart structure (newyork etc.), boundedBy is a descendant, not a direct child
        poly_n = 0
        for bounded in bel.findall(f".//{{{ns['bldg']}}}boundedBy"):
            for poly in bounded.findall(f".//{{{ns['gml']}}}Polygon"):
                pid = poly.get(f"{{{ns['gml']}}}id") or ""
                if not pid:
                    # Data without gml:id (newyork etc.): assign deterministic
                    # planned IDs from the order of appearance within boundedBy.
                    # On texture apply they are written to the real file with
                    # the same ordering rule (grant_polygon_ids)
                    pid = f"{gid}_p{poly_n}"
                poly_n += 1
                for pl in poly.findall(f".//{{{ns['gml']}}}posList"):
                    if not pl.text:
                        continue
                    nums = [float(x) for x in pl.text.split()]
                    if tf is not None:
                        pts = []
                        for i in range(0, len(nums) - 2, 3):
                            lat, lon = tf(nums[i], nums[i + 1])
                            pts.append([round(lon, 7), round(lat, 7),
                                        round(nums[i + 2] * zs, 3)])
                    else:
                        pts = []
                        for i in range(0, len(nums) - 2, 3):
                            pts.append([round(nums[i + 1], 7), round(nums[i], 7),
                                        round(nums[i + 2], 3)])
                    if len(pts) >= 3 and pts[0] == pts[-1]:
                        pts = pts[:-1]
                    if len(pts) >= 3:
                        lod2.append({"id": pid, "pts": pts})

        # ---- Attribute tree (enumerate non-geometry leaves) ----
        # Edit address = (tag localname, occurrence index of that tag within the span).
        # Occurrence order is matched against the regex match sequence on the original bytes (values verified too).
        tag_matches: dict[str, list] = {}
        tag_ptr: dict[str, int] = {}

        def leaf_index(tag: str, value: str) -> int | None:
            if tag not in tag_matches:
                tag_matches[tag] = list(_leaf_pattern(tag).finditer(span))
                tag_ptr[tag] = 0
            want = _xml_escape(value)
            ms = tag_matches[tag]
            i = tag_ptr[tag]
            while i < len(ms):
                if ms[i].group(1) == want:
                    tag_ptr[tag] = i + 1
                    return i
                i += 1
            return None

        codelists = self.codelists()
        items: list[dict] = []
        building_id = ""
        # Source info (resolution rule of docs/provenance-rules.md: note > upper thematicSrcDesc > unknown)
        src_upper: list[str] = []
        src_specific: dict[str, str] = {}
        src_codelist = SRC_CODELIST

        def collect_leaves(elem: ET.Element, out: list[dict]) -> None:
            nonlocal building_id
            for child in elem:
                ln = _local(child)
                if not ln or ln.startswith("lod") or ln == "boundedBy":
                    continue
                if len(child) > 0:
                    collect_leaves(child, out)
                    continue
                value = (child.text or "").strip()
                code_space = child.get("codeSpace")
                codelist = None
                if code_space:
                    codelist = code_space.rsplit("/", 1)[-1]
                    if codelist not in codelists:
                        codelist = None
                idx = leaf_index(ln, (child.text or ""))
                readonly = ln in READONLY_TAGS or ln in GEOMETRY_TAGS or idx is None
                if ln == "buildingID":
                    building_id = value
                out.append(
                    {
                        "tag": ln,
                        "label": ui_label(ln),
                        "value": value,
                        "index": idx,
                        "codelist": codelist,
                        "uom": child.get("uom"),
                        "readonly": readonly,
                        "qname": _qname(child),
                    }
                )

        for top in bel:
            ln = _local(top)
            if not ln or ln.startswith("lod") or ln == "boundedBy":
                continue
            if ln == "genericAttributeSet" and top.get("name") == SRC_SET_NAME:
                # Source-note set (R2-1): not shown on cards; feeds the resolution rule.
                # Consume leaf indexes (keeps edit addresses of later same-tag leaves correct)
                for entry in top:
                    name = entry.get("name") or ""
                    for v in entry:
                        leaf_index(_local(v), (v.text or ""))
                        if _local(v) == "value" and not name.startswith("根拠資料"):
                            src_specific[name] = (v.text or "").strip()
                continue
            if ln == "bldgDataQualityAttribute":
                for el in top.iter():
                    if _local(el) == "thematicSrcDesc":
                        code = (el.text or "").strip()
                        if code:
                            src_upper.append(code)
                        cs = el.get("codeSpace")
                        if cs:
                            cl = cs.rsplit("/", 1)[-1]
                            if cl in codelists:
                                src_codelist = cl
            group = _GROUP_BY_TOPTAG.get(ln, "other")
            title = ""
            if ln in ("stringAttribute", "genericAttributeSet"):
                title = top.get("name") or ""
            elif ln == "bldgDisasterRiskAttribute" and len(top) > 0:
                child_ln = _local(top[0])
                title = tr(f"label.risk_{child_ln}",
                           _RISK_TITLES.get(child_ln, child_ln))
            elif ln == "address":
                title = tr("editor.addr_group", "Address")
            leaves: list[dict] = []
            if len(top) == 0:
                # Direct leaves (class / usage / creationDate etc.)
                collect_leaves_single = {
                    "tag": ln,
                    "label": ui_label(ln),
                    "value": (top.text or "").strip(),
                    "index": leaf_index(ln, (top.text or "")),
                    "codelist": None,
                    "uom": top.get("uom"),
                    "readonly": ln in READONLY_TAGS,
                    "qname": _qname(top),
                }
                cs = top.get("codeSpace")
                if cs:
                    cl = cs.rsplit("/", 1)[-1]
                    collect_leaves_single["codelist"] = (
                        cl if cl in codelists else None
                    )
                if collect_leaves_single["index"] is None:
                    collect_leaves_single["readonly"] = True
                leaves.append(collect_leaves_single)
            else:
                collect_leaves(top, leaves)
            if group == "quality":
                for lf in leaves:
                    lf["readonly"] = True
            if leaves:
                items.append({"group": group, "title": title, "leaves": leaves})

        center = None
        if coords:
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            center = [
                round((min(lats) + max(lats)) / 2, 7),
                round((min(lons) + max(lons)) / 2, 7),
            ]

        if not building_id:
            building_id = stable_building_id_from_span(
                span,
                gid,
                getattr(self, "_bid_type", "uro:buildingID"),
                getattr(self, "_bid_invalid_values", ()),
            )
        return {
            "gid": gid,
            "buildingID": building_id,
            "footprint": coords,
            "center": center,
            "height": height,
            "base": base,
            "lod1top": lod1top,
            "lod2": lod2,
            "items": items,
            "src": {
                "upper": src_upper,
                "specific": src_specific,
                "codelist": src_codelist,
            },
        }

    # ---- Response shaping ----
    def tile_json(self, code: str) -> dict:
        t = self.tile(code)
        return {
            "code": t["code"],
            "file": t["file"],
            "relpath": t["relpath"],
            "bounds": t["bounds"],
            "buildings": [
                {k: b[k] for k in ("gid", "buildingID", "footprint", "center", "height", "items", "src")}
                for b in (t["buildings"][g] for g in t["order"])
            ],
        }

    def building_json(self, code: str, gid: str) -> dict:
        t = self.tile(code)
        b = t["buildings"].get(gid)
        if b is None:
            raise KeyError(gid)
        tex = {
            f["id"]: t["texmap"][f["id"]]
            for f in b["lod2"]
            if f["id"] in t["texmap"]
        }
        return {
            "tile": code,
            "gid": gid,
            "buildingID": b["buildingID"],
            "coords": b["footprint"],
            "center": b["center"],
            "height": b["height"],
            "base": b["base"],
            "lod1top": b["lod1top"],
            "lod2": b["lod2"],
            "tex": tex,
        }

    # ---- Editing (byte-preserving leaf replacement + source-note insert/update) ----
    def _edited_bytes(
        self,
        code: str,
        gid: str,
        changes: list[dict],
        source_selections: list[dict] | None = None,
    ) -> "tuple[Path, bytes, list[str]]":
        """Assemble the post-edit byte stream in memory (the file is not written yet)."""
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        raw = path.read_bytes()
        spans = building_spans(raw)
        if gid not in spans:
            raise KeyError(gid)
        s, e = spans[gid]
        span = raw[s:e]

        # Apply leaf replacements first (note insertion can shift same-tag leaf order, so it is batched later)
        leaf_changes = [c for c in changes if c.get("kind") != "src"]
        src_changes = [c for c in changes if c.get("kind") == "src"]

        for ch in leaf_changes:
            tag = str(ch["tag"])
            idx = int(ch["index"])
            old = str(ch["old"])
            new = str(ch["new"])
            if tag in READONLY_TAGS or tag in GEOMETRY_TAGS:
                raise ValueError(tr("editor.err_readonly", "{tag} is read-only", tag=tag))
            if new == old:
                continue
            matches = list(_leaf_pattern(tag).finditer(span))
            if idx >= len(matches):
                raise ValueError(tr(
                    "editor.err_leaf_not_found",
                    "{tag}[{idx}] was not found (the file may have been modified)",
                    tag=tag, idx=idx,
                ))
            m = matches[idx]
            if m.group(1) != _xml_escape(old):
                raise ValueError(tr(
                    "editor.err_leaf_mismatch",
                    "The current value of {tag}[{idx}] does not match (expected: {old})."
                    " Please reload the page",
                    tag=tag, idx=idx, old=repr(old),
                ))
            span = span[: m.start(1)] + _xml_escape(new) + span[m.end(1) :]

        r28: list[str] = []
        for ch in src_changes:
            span = self._apply_src_change(
                raw, span, str(ch["qname"]), str(ch.get("old") or ""), str(ch["new"])
            )
            span, synced = self._sync_upper_src(span, str(ch["new"]))
            if synced:
                r28.append(str(ch["new"]))

        # Attributes with duplicate QNames cannot get a per-item note, but the selected
        # code is always reflected in the building-level source list. The item mapping itself remains in the PR body.
        for selection in source_selections or []:
            code_value = str(selection.get("code") or "")
            if not code_value:
                continue
            span, synced = self._sync_upper_src(span, code_value)
            if synced:
                r28.append(code_value)

        new_raw = raw[:s] + span + raw[e:]
        # Self-check: the building span structure is not broken
        if set(building_spans(new_raw)) != set(spans):
            raise RuntimeError(tr(
                "editor.err_postedit_verify",
                "Verification after editing failed (apply aborted)",
            ))
        return path, new_raw, r28

    def apply_edits(
        self,
        code: str,
        gid: str,
        changes: list[dict],
        source_selections: list[dict] | None = None,
    ) -> dict:
        path, new_raw, r28 = self._edited_bytes(
            code, gid, changes, source_selections
        )
        try:
            ET.fromstring(new_raw)
        except ET.ParseError as exc:
            raise RuntimeError(tr(
                "editor.err_postedit_xml",
                "XML verification after editing failed (apply aborted): {exc}",
                exc=exc,
            ))
        path.write_bytes(new_raw)
        self._tile_cache.pop(code, None)  # invalidate cache (re-parse next time)
        return {
            "ok": True,
            "relpath": str(path.relative_to(self.root)),
            "applied": len(changes),
            "r28": list(dict.fromkeys(r28)),
        }

    @staticmethod
    def _reason_ready(reason: str) -> bool:
        placeholders = ("記入してください", "未記入", "TODO", "TBD")
        return len(reason.strip()) >= 5 and not any(
            marker.lower() in reason.lower() for marker in placeholders
        )

    def _other_udx_changes(self, rel: str) -> list[str]:
        return [
            line
            for line in self._git("status", "--porcelain").stdout.splitlines()
            if line.strip()
            and not line.startswith("??")
            and "/udx/" in line[3:]
            and line[3:].strip() != rel
        ]

    def _pretest(self, body: dict) -> dict:
        """Run safe pre-submission checks that the distributed build alone can execute."""
        code = str(body.get("tile") or "")
        gid = str(body.get("gid") or "")
        changes = body.get("changes") or []
        reason = str(body.get("reason") or "").strip()
        checks: list[dict] = []

        def add(key: str, label: str, passed: bool, detail: str) -> None:
            checks.append({
                "key": key,
                "label": label,
                "status": "pass" if passed else "fail",
                "detail": detail,
            })

        if reason:
            add(
                "reason", tr("editor.check_reason_label", "Notes (optional)"),
                self._reason_ready(reason),
                tr("editor.check_reason_pass",
                   "Your notes will be added to the explanation for the maintainer")
                if self._reason_ready(reason)
                else tr("editor.check_reason_fail",
                        "If you add notes, write at least 5 characters and be specific"),
            )
        else:
            checks.append({
                "key": "reason",
                "label": tr("editor.check_reason_label", "Notes (optional)"),
                "status": "na",
                "detail": tr(
                    "editor.check_reason_na",
                    "An explanation is generated automatically from the before/after"
                    " values and the selected sources",
                ),
            })
        add(
            "changes", tr("editor.check_changes_label", "Changes"), bool(changes),
            tr("editor.check_changes_pass", "There are {n} changed item(s)", n=len(changes))
            if changes else tr("editor.check_changes_fail", "There are no items to change"),
        )

        path = self.tile_files().get(code)
        if path is None:
            add("building", tr("editor.check_building_label", "Target building"), False,
                tr("editor.check_building_missing", "The target building data was not found"))
            return {"ok": True, "passed": False, "checks": checks}
        rel = str(path.relative_to(self.root))
        source_selections = [dict(s) for s in (body.get("sourceSelections") or [])]
        try:
            tile = self.tile(code)
            building = tile["buildings"].get(gid)
            if building is None:
                raise KeyError(gid)
            source_codelist = str(
                building["src"].get("codelist") or SRC_CODELIST
            )
            source_table = self.codelists().get(source_codelist) or {}
            validate_source_selections(changes, source_selections, source_table)
            checks.append({
                "key": "source",
                "label": tr("editor.th_source", "Source"),
                "status": "pass",
                "detail": tr(
                    "editor.check_source_pass",
                    "A document you checked is selected for every changed attribute",
                ),
            })
            _path, new_raw, r28 = self._edited_bytes(
                code, gid, changes, source_selections
            )
            spans = building_spans(new_raw)
            building_id = gid
            if gid in spans:
                start, end = spans[gid]
                hit = _BUILDINGID_RE.search(new_raw, start, end)
                if hit:
                    building_id = hit.group(1).decode("utf-8").strip()
            add("building", tr("editor.check_building_label", "Target building"),
                gid in spans,
                tr("editor.check_building_pass",
                   "Only the single building with building ID {id} is targeted",
                   id=building_id))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            if not any(item["key"] == "source" for item in checks):
                checks.append({
                    "key": "source",
                    "label": tr("editor.th_source", "Source"),
                    "status": "fail",
                    "detail": str(exc),
                })
            add("building", tr("editor.check_building_label", "Target building"),
                False, str(exc))
            return {"ok": True, "passed": False, "checks": checks}

        try:
            ET.fromstring(new_raw)
            add("xml", tr("editor.check_xml_label", "CityGML format"), True,
                tr("editor.check_xml_pass",
                   "The file still parses correctly as XML after the change"))
        except ET.ParseError as exc:
            add("xml", tr("editor.check_xml_label", "CityGML format"), False,
                tr("editor.check_xml_fail",
                   "The changed XML cannot be parsed: {exc}", exc=exc))

        others = self._other_udx_changes(rel)
        add(
            "scope", tr("editor.check_scope_label", "Changed file scope"), not others,
            tr("editor.check_scope_pass", "Only the target building's file will be sent")
            if not others
            else tr("editor.check_scope_fail",
                    "Other building data also has unorganized changes"),
        )
        if r28:
            checks.append({
                "key": "source-sync",
                "label": tr("editor.check_srcsync_label", "Source list sync"),
                "status": "pass",
                "detail": tr(
                    "editor.check_srcsync_pass",
                    "The item-specific source note and the building-level source code"
                    " are updated together",
                ),
            })
        else:
            checks.append({
                "key": "source-sync",
                "label": tr("editor.check_srcsync_label", "Source list sync"),
                "status": "na",
                "detail": tr("editor.check_srcsync_na",
                             "No source note needs to be added this time"),
            })
        passed = all(item["status"] in ("pass", "na") for item in checks)
        return {
            "ok": True,
            "passed": passed,
            "checks": checks,
            "buildingID": building_id,
            "note": tr(
                "editor.pretest_server_note",
                "Detailed schema checks and more run again in the automated checks"
                " after you send",
            ),
        }

    def pretest(self, body: dict) -> dict:
        with self._git_lock:
            return self._pretest(body)

    def _apply_src_change(self, raw: bytes, span: bytes, qname: str, old: str, new: str) -> bytes:
        """Replace the source-note (gen "出典" set) value, or insert one per convention if absent.

        - R2-1: the set has the fixed name="出典", at most 1 per building (append to an existing one)
        - R2-5: with no set, insert immediately after core:creationDate
        - Indentation and line endings are copied from surrounding lines (consistent with byte preservation)
        """
        if not re.fullmatch(r"[A-Za-z_][\w.-]*(:[\w.-]+)?", qname):
            raise ValueError(tr(
                "editor.err_src_qname",
                "The attribute name for the source note is invalid: {qname}",
                qname=repr(qname),
            ))
        eol = b"\r\n" if b"\r\n" in span else b"\n"
        qb = qname.encode("utf-8")
        newb = _xml_escape(new)
        m_set = _SRC_SET_RE.search(span)
        entry_re = re.compile(
            rb'(<gen:stringAttribute name="' + re.escape(qb) + rb'">\s*<gen:value>)'
            rb"([^<]*)(</gen:value>)",
            re.S,
        )
        if m_set:
            m = entry_re.search(span, m_set.start(), m_set.end())
            if m:
                if m.group(2) != _xml_escape(old):
                    raise ValueError(tr(
                        "editor.err_src_mismatch",
                        "The current value of the source note ({qname}) does not match"
                        " (expected: {old}). Please reload the page",
                        qname=qname, old=repr(old),
                    ))
                return span[: m.start(2)] + newb + span[m.end(2) :]
            if old:
                raise ValueError(tr(
                    "editor.err_src_not_found",
                    "The source note ({qname}) was not found"
                    " (the file may have been modified)",
                    qname=qname,
                ))
            # Insert the entry just before the existing set's closing tag (line start incl. indentation)
            set_indent = _line_indent(span, m_set.start())
            close_at = span.rfind(b"</gen:genericAttributeSet>", m_set.start(), m_set.end())
            ws_start = close_at
            while ws_start > 0 and span[ws_start - 1 : ws_start] in (b"\t", b" "):
                ws_start -= 1
            entry = (
                set_indent + b"\t<gen:stringAttribute name=\"" + qb + b"\">" + eol
                + set_indent + b"\t\t<gen:value>" + newb + b"</gen:value>" + eol
                + set_indent + b"\t</gen:stringAttribute>" + eol
            )
            return span[:ws_start] + entry + span[ws_start:]
        if old:
            raise ValueError(tr(
                "editor.err_src_not_found",
                "The source note ({qname}) was not found"
                " (the file may have been modified)",
                qname=qname,
            ))
        anchor = _CREATION_RE.search(span)
        if anchor is None:
            raise ValueError(tr(
                "editor.err_src_no_creation",
                "Adding a source note to a building without creationDate"
                " is not supported",
            ))
        indent = _line_indent(span, anchor.start())
        xmlns = (
            b""
            if b"xmlns:gen=" in raw[: raw.find(b"<core:cityObjectMember>")]
            else b' xmlns:gen="http://www.opengis.net/citygml/generics/2.0"'
        )
        set_name = SRC_SET_NAME.encode("utf-8")
        block = (
            indent + b'<gen:genericAttributeSet name="' + set_name + b'"' + xmlns + b">" + eol
            + indent + b"\t<gen:stringAttribute name=\"" + qb + b"\">" + eol
            + indent + b"\t\t<gen:value>" + newb + b"</gen:value>" + eol
            + indent + b"\t</gen:stringAttribute>" + eol
            + indent + b"</gen:genericAttributeSet>"
        )
        return span[: anchor.end()] + eol + block + span[anchor.end() :]

    def _sync_upper_src(self, span: bytes, code: str) -> "tuple[bytes, bool]":
        """R2-8: append the note code to the upper thematicSrcDesc if missing (within the same PR).

        Clones an existing thematicSrcDesc element (prefix, codeSpace) as the template.
        Does nothing for buildings lacking the container (DataQualityAttribute) itself.
        """
        ms = list(_THEMATIC_RE.finditer(span))
        if not ms:
            return span, False
        codeb = code.encode("utf-8")
        if any(m.group(3).strip() == codeb for m in ms):
            return span, False
        last = ms[-1]
        eol = b"\r\n" if b"\r\n" in span else b"\n"
        indent = _line_indent(span, last.start())
        new_el = (
            b"<" + last.group(1) + b"thematicSrcDesc" + last.group(2) + b">"
            + codeb
            + b"</" + last.group(1) + b"thematicSrcDesc>"
        )
        return span[: last.end()] + eol + indent + new_el + span[last.end() :], True

    # ---- git / PR ----
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        net = bool(args) and args[0] in ("push", "fetch", "pull")
        r = subprocess.run(
            [*git_base_args(net=net), "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if check and r.returncode != 0:
            raise RuntimeError(tr(
                "editor.err_git_failed", "git {args} failed:\n{stderr}",
                args=" ".join(args), stderr=r.stderr.strip(),
            ))
        return r

    def _fetch_upstream_main(self) -> "str | None":
        """Freshly fetched commit of the upstream city's main (None when offline)."""
        try:
            r = subprocess.run(
                [*git_base_args(net=True), "-C", str(self.root),
                 "fetch", "--quiet", upstream_url(self.root), "main"],
                capture_output=True, text=True, timeout=FETCH_TIMEOUT,
            )
            if r.returncode != 0:
                return None
            head = self._git("rev-parse", "FETCH_HEAD", check=False)
            return head.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    def _fresh_pr_base(self, rel: str) -> "str | None":
        """Commit to cut the edit branch from, so the PR base is never stale.

        None (offline etc.) falls back to branching from the local HEAD as
        before; the CI freshness comment then remains the after-the-fact net.
        When upstream moved the target file itself, the browser edit was made
        against old bytes: sync main so a page reload serves the latest data,
        and ask the user to redo the edit.
        """
        base = self._fetch_upstream_main()
        if base is None:
            return None
        diff = self._git("diff", "--name-only", "HEAD", base, "--", rel, check=False)
        if diff.stdout.strip():
            sync_upstream_main(self.root)
            self._tile_cache.clear()
            raise ValueError(tr(
                "editor.err_upstream_advanced",
                "This building's file has been updated in the city repository."
                " Please reload the page and redo the edit on the latest data.",
            ))
        return base

    def _checkout_pr_branch(self, branch: str, base: "str | None") -> None:
        """Create the edit branch from the fetched upstream main (fallback: HEAD).

        The working tree keeps the just-applied edit (the target file is known
        to be identical between HEAD and base). If unrelated local changes make
        git refuse the switch, cut from HEAD as before (fail-open).
        """
        if base:
            r = self._git("checkout", "-b", branch, base, check=False)
            if r.returncode == 0:
                return
        self._git("checkout", "-b", branch)

    def git_status(self) -> dict:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        dirty = [
            ln for ln in self._git("status", "--porcelain").stdout.splitlines() if ln.strip()
        ]
        return {"branch": branch, "dirty": dirty}

    def revert_file(self, code: str) -> dict:
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        rel = str(path.relative_to(self.root))
        self._git("checkout", "--", rel)
        self._tile_cache.pop(code, None)
        return {"ok": True, "relpath": rel}

    def _origin_nwo(self) -> "str | None":
        r = self._git("remote", "get-url", "origin", check=False)
        if r.returncode != 0:
            return None
        url = r.stdout.strip()
        m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url)
        m = m or re.match(r"https://github\.com/(.+?)(?:\.git)?$", url)
        return m.group(1) if m else None

    def _compare_url(self, branch: str) -> "str | None":
        """GitHub screen for proposing changes upstream. Never a fork-internal-only compare."""
        origin = self._origin_nwo()
        if not origin:
            return None
        owner = origin.split("/", 1)[0]
        return (
            f"https://github.com/{upstream_nwo(getattr(self, 'root', None))}/compare/main...{owner}:{branch}?expand=1"
        )

    def _create_pr_api(self, branch: str, title: str,
                       body: str) -> "tuple[str | None, str | None]":
        """Reuse the hub's OAuth connection to create the proposal without a GitHub screen."""
        token = load_hub_token()
        origin = self._origin_nwo()
        if not token or not origin:
            return None, None
        owner = origin.split("/", 1)[0]
        code, data = github_api(
            f"/repos/{upstream_nwo(getattr(self, 'root', None))}/pulls",
            token,
            method="POST",
            payload={
                "title": title,
                "head": f"{owner}:{branch}",
                "base": "main",
                "body": body,
            },
        )
        if code == 201 and data.get("html_url"):
            return str(data["html_url"]), None
        message = str(data.get("message") or f"HTTP {code}")
        return None, tr(
            "editor.err_pr_auto_failed",
            "Could not automatically create the change proposal for the maintainer"
            " ({message}).",
            message=message,
        )

    def preview_pr(self, body: dict) -> dict:
        """Dry-run of the auto-generated PR summary for the send dialog.

        Rendered by the same pr_summary() as the posted PR body (repo
        language), so the preview cannot drift from the real text. Lenient by
        design: changes without a selected source are skipped here; create_pr
        still enforces full validation before anything is sent."""
        code = str(body.get("tile") or "")
        gid = str(body.get("gid") or "")
        changes = [c for c in (body.get("changes") or []) if isinstance(c, dict)]
        selections = [s for s in (body.get("sourceSelections") or [])
                      if isinstance(s, dict)]
        rlang = getattr(self, "_repo_lang", "en")
        source_table: dict = {}
        try:
            building = self.tile(code)["buildings"].get(gid) or {}
            codelist = str((building.get("src") or {}).get("codelist") or SRC_CODELIST)
            source_table = self.codelists().get(codelist) or {}
        except Exception:
            pass  # preview only: missing context degrades to code-only source names
        by_key = {str(s.get("key") or ""): str(s.get("code") or "").strip()
                  for s in selections}
        leaf_changes: list[dict] = []
        selected_sources: dict[str, dict[str, str]] = {}
        for c in changes:
            if c.get("kind") == "src":
                continue
            try:
                key = _change_key(c)
            except (KeyError, TypeError, ValueError):
                continue
            source_code = by_key.get(key)
            if not source_code:
                continue
            leaf_changes.append(c)
            selected_sources[key] = {
                "code": source_code,
                "label": str(source_table.get(source_code) or source_code),
            }
        summary = (pr_summary(leaf_changes, selected_sources, rlang)
                   if leaf_changes else "")
        # City display name in the UI language, for the "written in <lang>" note
        city = ""
        try:
            meta = json.loads((self.root / "4dcitygml.json").read_text(encoding="utf-8"))
            names = meta.get("name") or {}
            mod = i18n_module()
            ui = mod.resolve_lang(load_config().get("lang")) if mod else "en"
            if isinstance(names, dict):
                city = str(names.get(ui) or names.get("en") or meta.get("id") or "")
            else:
                city = str(names or meta.get("id") or "")
        except Exception:
            pass
        return {"ok": True, "summary": summary, "repoLang": rlang, "city": city}

    def create_pr(self, body: dict) -> dict:
        code = body["tile"]
        gid = body["gid"]
        changes = [dict(c) for c in (body.get("changes") or [])]
        source_selections = [dict(s) for s in (body.get("sourceSelections") or [])]
        reason = (body.get("reason") or "").strip()
        if not changes:
            raise ValueError(tr("editor.err_no_changes", "There are no changes"))

        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        rel = str(path.relative_to(self.root))

        with self._git_lock:
            # The edit branch is cut from the freshly fetched upstream main, so the
            # PR base cannot be stale (the practice repo rewrites main every day).
            pr_base = self._fresh_pr_base(rel)
            pretest = self._pretest(body)
            if not pretest.get("passed"):
                failed = [
                    item.get("detail") or item.get("label")
                    or tr("editor.check_needed", "Needs attention")
                    for item in pretest.get("checks", [])
                    if item.get("status") == "fail"
                ]
                raise ValueError(tr(
                    "editor.err_pretest_not_passed",
                    "The pre-submission check has not passed: {list}",
                    list=tr("editor.fail_sep", " / ").join(failed),
                ))

            # Working-tree check: no tracked files under udx/ changed other than the target
            # (the commit adds only the target file, so non-udx/ and untracked changes are tolerated)
            others = self._other_udx_changes(rel)
            if others:
                raise RuntimeError(tr(
                    "editor.err_udx_dirty",
                    "There are changes to files other than the target under udx/."
                    " Please clean them up first:\n{list}",
                    list="\n".join(others[:10]),
                ))

            # Value changes require an explicitly selected source. Validate here too,
            # using real data leaves and the code table, not relying on the browser alone.
            tile = self.tile(code)
            building = tile["buildings"].get(gid)
            if building is None:
                raise KeyError(gid)
            source_codelist = str(building["src"].get("codelist") or SRC_CODELIST)
            source_table = self.codelists().get(source_codelist) or {}
            selected_sources = validate_source_selections(
                changes, source_selections, source_table
            )
            leaf_lookup: dict[str, dict] = {}
            qname_counts: dict[str, int] = {}
            for item in building["items"]:
                for leaf in item["leaves"]:
                    if leaf.get("index") is not None:
                        leaf_lookup[f"{leaf['tag']}#{leaf['index']}"] = leaf
                    qname = str(leaf.get("qname") or "")
                    if qname:
                        qname_counts[qname] = qname_counts.get(qname, 0) + 1

            leaf_changes = [c for c in changes if c.get("kind") != "src"]
            normalized_selections: list[dict] = []
            existing_src_changes = {
                str(c.get("qname") or ""): c
                for c in changes if c.get("kind") == "src"
            }
            for change in leaf_changes:
                key = _change_key(change)
                leaf = leaf_lookup.get(key)
                if leaf is None or leaf.get("readonly"):
                    raise ValueError(tr(
                        "editor.err_not_editable",
                        "{label} cannot be edited. Please reload the page",
                        label=change.get("label") or change.get("tag")
                        or tr("editor.attr_fallback", "attribute"),
                    ))
                # Display name and edit address are fixed from the current building, never trusting user input.
                change.update(
                    key=key,
                    tag=leaf["tag"],
                    index=leaf["index"],
                    label=leaf["label"],
                )
                source = selected_sources[key]
                qname = str(leaf.get("qname") or "")
                normalized_selections.append(
                    {"key": key, "code": source["code"], "qname": qname}
                )

                # For uniquely addressable attributes, the server also fills in the gen "出典" note.
                # With duplicate names, apply_edits reflects it in the upper source list, and the mapping stays in the PR.
                if qname and qname_counts.get(qname) == 1:
                    original = str(building["src"]["specific"].get(qname) or "")
                    present = existing_src_changes.get(qname)
                    if present and str(present.get("new") or "") != source["code"]:
                        raise ValueError(tr(
                            "editor.err_source_conflict",
                            'The source specified for "{label}" does not match',
                            label=leaf["label"],
                        ))
                    if original != source["code"] and present is None:
                        generated = {
                            "kind": "src",
                            "qname": qname,
                            "old": original,
                            "new": source["code"],
                            # English on purpose: this label surfaces in the commit body (history contract)
                            "label": f"Source ({label_in('en', leaf['tag'], leaf['label'])})",
                        }
                        changes.append(generated)
                        existing_src_changes[qname] = generated
            source_selections = normalized_selections

            # Apply the changes (preserving original bytes)
            applied = self.apply_edits(code, gid, changes, source_selections)

            # Resolve buildingID from the head file (same as suggest_commit.py)
            raw = path.read_bytes()
            spans = building_spans(raw)
            building_id = gid
            if gid in spans:
                s, e = spans[gid]
                building_id = stable_building_id_from_span(
                    raw[s:e],
                    gid,
                    getattr(self, "_bid_type", "uro:buildingID"),
                    getattr(self, "_bid_invalid_values", ()),
                )

            now = datetime.now()
            safe_bid = re.sub(r"[^A-Za-z0-9._-]", "-", building_id)
            branch = f"edit/{safe_bid}-{now:%Y%m%d-%H%M%S}"

            # Commit message: Update attributes (<attr name>): <old> → <new>, plus a Building: trailer.
            # History stays English (greppable, language-independent contract), so labels
            # resolve as "en" here even when the repo/UI language differs.
            def commit_label(c: dict) -> str:
                return label_in("en", str(c.get("tag") or ""),
                                str(c.get("label") or c.get("tag") or ""))

            described_changes = leaf_changes or changes
            first = described_changes[0]
            if len(described_changes) == 1:
                subject = (f"Update attributes ({commit_label(first)}):"
                           f" {first['old']} → {first['new']}")
            else:
                subject = (f"Update attributes ({commit_label(first)}"
                           f" and {len(described_changes) - 1} more)")
            lines = [subject, ""]
            if len(changes) > 1:
                lines += [f"- {commit_label(c)}: {c['old']} → {c['new']}" for c in changes]
                lines.append("")
            if reason:
                lines += [reason, ""]
            lines.append(f"Building: {building_id}")
            lines.append(created_by_trailer(self.root, "citygml-attr-editor"))
            message = "\n".join(lines)

            prev = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            try:
                self._checkout_pr_branch(branch, pr_base)
                self._git("add", rel)
                self._git("commit", "-m", message)
                commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            except RuntimeError:
                self._git("checkout", prev, check=False)
                self._git("checkout", "--", rel, check=False)
                self._tile_cache.pop(code, None)
                raise

            result: dict = {"ok": True, "branch": branch, "commit": commit, "buildingID": building_id}

            push = self._git("push", "-u", "origin", branch, check=False)
            if push.returncode != 0:
                self._git("checkout", prev, check=False)
                self._git("branch", "-D", branch, check=False)
                self._tile_cache.pop(code, None)
                raise RuntimeError(tr(
                    "editor.err_push_failed",
                    "Could not send to GitHub. Your edits remain on this screen."
                    " Check your internet connection and try again.\n{stderr}",
                    stderr=push.stderr.strip(),
                ))
            result["pushed"] = True

            # PR title and body are repo-facing: both resolve in the repository's
            # working language (4dcitygml.json "lang"), independent of the UI
            # language. Classification stays safe in any language via the edit/
            # branch prefix; the ja/de title prefixes also match hub/CI title
            # fallbacks for manual PRs. Commit subject stays English (above).
            rlang = getattr(self, "_repo_lang", "en")
            if leaf_changes:
                title_label = label_in(rlang, str(first.get("tag") or ""),
                                       str(first.get("label") or ""))
                if len(leaf_changes) > 1:
                    pr_title = tr_lang(rlang, "pr.title_attr_many",
                                       "Update building info: {label} and {n} more",
                                       label=title_label, n=len(leaf_changes) - 1)
                else:
                    pr_title = tr_lang(rlang, "pr.title_attr",
                                       "Update building info: {label}",
                                       label=title_label)
                pr_body = build_pr_body(
                    building_id,
                    gid,
                    leaf_changes,
                    selected_sources,
                    reason,
                    applied.get("r28") or [],
                    lang=rlang,
                )
            else:
                # Keep the legacy operation of only maintaining source notes without changing values.
                legacy_label = str(first.get("label") or "")
                if len(changes) == 1:
                    pr_title = tr_lang(rlang, "pr.title_source_only",
                                       "Update attributes ({label}): {old} → {new}",
                                       label=legacy_label,
                                       old=first["old"], new=first["new"])
                else:
                    pr_title = tr_lang(rlang, "pr.title_source_only_many",
                                       "Update attributes ({label} and {n} more)",
                                       label=legacy_label, n=len(changes) - 1)
                blank = tr_lang(rlang, "pr.blank", "(blank)")
                rows = "\n".join(
                    f"| {_md_cell(c['label'], blank)} | {_md_cell(c['old'], blank)} |"
                    f" {_md_cell(c['new'], blank)} |"
                    for c in changes
                )
                pr_body = (
                    f"## {tr_lang(rlang, 'pr.heading_source_update', 'Source information update')}"
                    f" ({_md_text(building_id)} / `{_md_text(gid)}`)\n\n"
                    + tr_lang(rlang, "pr.details_columns3", "| Item | Before | After |")
                    + f"\n|---|---|---|\n{rows}\n\n"
                    f"## {tr_lang(rlang, 'pr.heading_notes', 'Additional notes and evidence')}"
                    f" <!--sec:reason-->\n\n"
                    f"{_md_text(reason) or tr_lang(rlang, 'pr.no_notes', 'No additional notes.')}\n"
                )
            pr_url, api_note = self._create_pr_api(branch, pr_title, pr_body)
            if pr_url:
                result["prUrl"] = pr_url
            elif shutil.which("gh"):
                gh = subprocess.run(
                    [
                        "gh", "pr", "create",
                        "--head", branch,
                        "--title", pr_title,
                        "--body", pr_body,
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(self.root),
                )
                if gh.returncode == 0:
                    result["prUrl"] = gh.stdout.strip().splitlines()[-1]
                else:
                    result["compareUrl"] = self._compare_url(branch)
                    result["note"] = (
                        (api_note + "\n" if api_note else "")
                        + tr("editor.note_confirm_github",
                             "Complete the submission on the GitHub confirmation screen.")
                        + "\n"
                        + gh.stderr.strip()
                    )
            else:
                # Standalone use without the hub keeps a fallback of confirming via the GitHub screen.
                result["compareUrl"] = self._compare_url(branch)
                if api_note:
                    result["note"] = api_note

            # Return to main (the original branch)
            self._git("checkout", prev, check=False)
            self._tile_cache.pop(code, None)
            return result


# --------------------------------------------------------------------------
# First-run setup (GUI for when no clone exists; for executable distribution)
# --------------------------------------------------------------------------
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # The clone already finished, so do not fail first-run setup just because the config save failed.
        pass


class SetupManager:
    """Run git clone in the background and report progress to the polling API."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: str | None = None
        self.dest: str | None = None
        self.lines: list[str] = []

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
                raise RuntimeError(tr("setup.err_clone_running", "A clone is already running"))
            if git_cmd()[0] is None:
                raise RuntimeError(tr(
                    "setup.err_git_missing",
                    "git was not found. Install git by following the setup guide",
                ))
            url = url.strip()
            if not re.match(r"^(https://|git@|file://|/)", url):
                raise ValueError(tr("setup.err_bad_url",
                                    "The repository URL format is invalid"))
            dest_path = Path(dest).expanduser()
            if dest_path.exists() and any(dest_path.iterdir()):
                raise ValueError(tr("setup.err_dest_not_empty",
                                    "The destination is not empty: {dest}", dest=dest_path))
            self.running, self.done, self.error = True, False, None
            self.dest = str(dest_path)
            self.lines = [
                tr("setup.clone_start", "Clone started: {url}", url=url),
                tr("setup.clone_size_note",
                   "(The data is several GB, so this takes minutes to tens of minutes)"),
            ]
        threading.Thread(target=self._run, args=(url, str(dest_path)), daemon=True).start()

    def _run(self, url: str, dest: str) -> None:
        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                [*git_base_args(net=True), "clone", "--progress", url, dest],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                # git progress arrives \r-separated, so keep only the last segment
                seg = line.split("\r")[-1].strip()
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
                    self.lines.append(tr("setup.clone_done", "Clone finished"))
                else:
                    self.error = tr("setup.err_clone_failed",
                                    "git clone failed (exit {code})", code=code)
        except Exception as e:  # noqa: BLE001 — shown in the UI
            with self.lock:
                self.running = False
                self.error = f"{type(e).__name__}: {e}"


SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title data-i18n="setup.doc_title">Initial setup — CityGML Attribute Editor</title>
<style>
  body {{ font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; background: #f5f6f8;
         color: #1c2733; display: flex; justify-content: center; padding: 40px 16px; }}
  main {{ background: #fff; border: 1px solid #dde1e6; border-radius: 12px;
          padding: 28px 32px; max-width: 640px; width: 100%; }}
  h1 {{ font-size: 18px; margin: 0 0 6px; }}
  p.sub {{ color: #6b7785; font-size: 13px; margin-top: 0; }}
  ol {{ font-size: 13px; padding-left: 20px; line-height: 1.8; }}
  label {{ display: block; font-weight: 600; font-size: 13px; margin: 14px 0 4px; }}
  input {{ width: 100%; box-sizing: border-box; font: inherit; padding: 8px;
           border: 1px solid #dde1e6; border-radius: 6px; }}
  button {{ margin-top: 18px; font: inherit; font-weight: 600; color: #fff;
            background: #1f883d; border: none; border-radius: 6px;
            padding: 10px 22px; cursor: pointer; }}
  button:disabled {{ opacity: .5; cursor: default; }}
  #log {{ background: #24292f; color: #c9d1d9; border-radius: 6px; padding: 10px 12px;
          font: 12px ui-monospace, monospace; white-space: pre-wrap; margin-top: 16px;
          min-height: 90px; display: none; }}
  .warn {{ background: #fff8e5; border: 1px solid #eed888; border-radius: 6px;
           padding: 8px 12px; font-size: 12px; margin-top: 12px; }}
  a {{ color: #0969da; }}
</style></head><body><main>
  <h1 data-i18n="setup.title">Initial setup</h1>
  <p class="sub" data-i18n="setup.lead">There is no clone of the building data (sample-tokyo-station) yet. It will be fetched to this computer.</p>
  <ol>
    <li><span data-i18n="setup.step1_pre">Log in with your GitHub account and </span><a href="{upstream}/fork" target="_blank" rel="noopener" data-i18n="setup.step1_link">create a fork from here</a><span data-i18n="setup.step1_post"> (your own copy).</span></li>
    <li><span data-i18n="setup.step2_pre">Paste the URL of the created fork (</span><code data-i18n="setup.step2_code">https://github.com/YOUR-ID/sample-tokyo-station</code><span data-i18n="setup.step2_post">) below and press [Run clone].</span></li>
  </ol>
  <label data-i18n="setup.url_label">Repository URL (your fork)</label>
  <input id="url" data-i18n-placeholder="setup.url_placeholder" placeholder="https://github.com/<your-id>/sample-tokyo-station.git">
  <label data-i18n="setup.dest_label">Destination folder</label>
  <input id="dest" value="{default_dest}">
  <div class="warn" id="gitWarn" style="display:none" data-i18n="setup.git_warn">⚠ git was not found. First install git by following the steps in the setup guide.</div>
  <button id="go" data-i18n="setup.btn_clone">Run clone</button>
  <div id="log"></div>
<script>
const $ = id => document.getElementById(id);
async function poll() {{
  const s = await fetch('/api/setup/status').then(r => r.json());
  if (!s.gitAvailable) $('gitWarn').style.display = 'block';
  if (s.running || s.done || s.error) {{
    $('log').style.display = 'block';
    $('log').textContent = s.log.join('\\n') + (s.error ? '\\n\\n❌ ' + s.error : '');
    $('go').disabled = s.running;
  }}
  if (s.done) {{ location.href = '/'; return; }}
  if (s.running) setTimeout(poll, 800);
}}
$('go').onclick = async () => {{
  $('go').disabled = true;
  const r = await fetch('/api/setup', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{url: $('url').value, dest: $('dest').value}}),
  }}).then(r => r.json());
  if (r.ok === false) {{ alert(r.error); $('go').disabled = false; return; }}
  poll();
}};
poll();
</script></main></body></html>"""


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".gml": "application/xml; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv; charset=utf-8",
}


def write_launcher(repo_root: Path) -> Path | None:
    """Write a double-click launcher to the desktop (macOS).

    Locally generated files carry no quarantine attribute, so they launch on
    double-click without a Gatekeeper warning. An existing one is left untouched.
    """
    if sys.platform != "darwin":
        return None
    app_in_clone = repo_root / "tools" / "attr_editor" / "app.py"
    if not app_in_clone.is_file():
        return None
    desktop = Path.home() / "Desktop"
    target = (desktop if desktop.is_dir() else repo_root) / "Attribute Editor.command"
    if target.exists():
        return target
    try:
        target.write_text(
            "#!/bin/zsh\n"
            f'exec /usr/bin/env python3 "{app_in_clone}"\n',
            encoding="utf-8",
        )
        target.chmod(0o755)
        print(f"Launcher created: {target} (double-click to launch next time)")
        return target
    except OSError:
        return None


class Handler(BaseHTTPRequestHandler):
    APP_ID = "attr_editor"  # selects the language catalog (tex_editor overrides in its subclass)
    repo: Repo | None = None  # set at startup (None = first-run setup mode)
    setup_mgr = SetupManager()

    @classmethod
    def _try_activate(cls) -> None:
        """Activate the repository after cloning and remember the clone location in config."""
        st = cls.setup_mgr
        if cls.repo is not None or not st.done or st.dest is None:
            return
        try:
            cls.repo = Repo(Path(st.dest))
            save_config({"repo": st.dest})
            write_launcher(cls.repo.root)
        except RuntimeError as e:
            st.done = False
            st.error = tr("setup.err_verify_failed",
                          "Verification of the cloned destination failed: {error}", error=e)

    # ---- Response helpers ----
    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, msg: str, status: int = 400) -> None:
        self._json({"ok": False, "error": msg}, status)

    def _ui_path(self, name: str) -> Path:
        """Locate UI files: next to app.py (normal / PyInstaller bundle), then inside the clone.

        In the bootstrap style where only the single app.py is downloaded and
        run, files are served from tools/attr_editor/ inside the clone once
        cloning completes.
        """
        local = RES_DIR / name
        if local.is_file():
            return local
        if self.repo is not None:
            return self.repo.root / "tools" / "attr_editor" / name
        return local

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._error("not found", 404)
            return
        data = path.read_bytes()
        if path.suffix.lower() == ".html":
            root = self.repo.root if self.repo is not None else None
            data = themed_html(data, root)
            data = city_map_html(data, root)
            data = localized_html(data, self.APP_ID)
        self.send_response(200)
        self.send_header(
            "Content-Type",
            _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(data)))
        # UI (HTML) is never cached so updates reliably arrive. Images etc. cache for 1 hour
        cache = "no-cache" if path.suffix.lower() == ".html" else "max-age=3600"
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _city_logo(self) -> None:
        """Municipality logo (logo in the clone's 4dcitygml.json). Resolution is fail-closed.

        Validation (relative path, raster extension, under the root, ≤ 1 MiB) is
        done by theme_loader's resolve_logo(); if unmet, 404 (the page keeps it
        hidden via onerror).
        """
        mod = theme_module()
        got = None
        if mod is not None and self.repo is not None and hasattr(mod, "resolve_logo"):
            try:
                got = mod.resolve_logo(self.repo.root)
            except Exception:  # any resolution failure is 404 (the logo is decoration, not functionality)
                got = None
        if got is None:
            self._error("not found", 404)
            return
        path, ctype = got
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)  # fixed from the extension (no sniffing)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _safe_child(self, base: Path, rel: str) -> Path | None:
        p = (base / unquote(rel)).resolve()
        return p if p.is_relative_to(base.resolve()) else None

    def log_message(self, fmt, *args):  # quiet (errors only, to the standard stderr)
        pass

    # ---- Routing ----
    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if self.repo is None:
                self._try_activate()
            if path == "/api/setup/status":
                # Always respond so a polling setup screen can detect done even after activation
                self._json({**self.setup_mgr.state(), "active": self.repo is not None})
                return
            if self.repo is None:
                # First-run setup mode: every GET returns the setup screen
                html = localized_html(SETUP_HTML.format(
                    upstream=UPSTREAM_URL,
                    default_dest=str(Path.home() / "Documents" / "sample-tokyo-station"),
                ).encode("utf-8"), self.APP_ID)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if path in ("/", "/index.html"):
                self._file(self._ui_path("index.html"))
            elif path == "/viewer.html":
                self._file(self._ui_path("viewer.html"))
            elif path == "/city-logo":
                self._city_logo()
            elif path == "/api/tiles":
                self._json(
                    {
                        "ok": True,
                        "tiles": self.repo.tiles_json(),
                        "resources": self.repo.resources_json(),
                    }
                )
            elif path == "/api/codelists":
                self._json(self.repo.codelists())
            elif path == "/api/status":
                self._json({"ok": True, **self.repo.git_status()})
            elif path == "/api/repo":
                # Self-report so the hub can check which city the editor on this port serves
                self._json({"ok": True, "root": str(self.repo.root),
                            "app": self.APP_ID})
            elif path.startswith("/api/tile/"):
                code = path.rsplit("/", 1)[-1]
                self._json({"ok": True, "tile": self.repo.tile_json(code)})
            elif path.startswith("/api/building/"):
                parts = path.split("/")
                if len(parts) != 5:
                    self._error("bad path")
                    return
                self._json({"ok": True, "building": self.repo.building_json(parts[3], unquote(parts[4]))})
            elif path.startswith("/textures/"):
                rel = path[len("/textures/"):]
                p = None
                if self.repo.tex_override is not None:
                    cand = self._safe_child(self.repo.tex_override, rel)
                    if cand is not None and cand.is_file():
                        p = cand  # use the variant if present, else fall back to the original
                if p is None:
                    p = self._safe_child(self.repo.bldg_dir, rel)
                self._file(p) if p else self._error("forbidden", 403)
            elif path.startswith("/codelists/"):
                p = self._safe_child(self.repo.codelists_dir, path[len("/codelists/"):])
                self._file(p) if p else self._error("forbidden", 403)
            elif path.startswith("/raw/"):
                # /raw/specification/... /raw/metadata/... (primary evidence documents)
                p = self._safe_child(self.repo.data_root, path[len("/raw/"):])
                self._file(p) if p else self._error("forbidden", 403)
            else:
                self._error("not found", 404)
        except FileNotFoundError as e:
            self._error(tr("editor.err_tile_not_found", "Tile not found: {exc}", exc=e), 404)
        except KeyError as e:
            self._error(tr("editor.err_building_not_found",
                           "Building not found: {exc}", exc=e), 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 — returned as an API response
            self._error(f"{type(e).__name__}: {e}", 500)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/setup":
                if self.repo is not None:
                    self._error(tr("setup.err_setup_done", "Setup is already complete"), 409)
                    return
                self.setup_mgr.start(body["url"], body["dest"])
                self._json({"ok": True})
                return
            if self.repo is None:
                self._error(tr("setup.err_setup_incomplete", "Setup is not complete"), 409)
                return
            if path == "/api/edit":
                if any(c.get("kind") != "src" for c in (body.get("changes") or [])):
                    raise ValueError(tr(
                        "editor.err_edit_needs_pr",
                        "To change attribute values, choose a source for each attribute"
                        ' and submit via "Send changes"',
                    ))
                self._json(
                    self.repo.apply_edits(body["tile"], body["gid"], body["changes"])
                )
            elif path == "/api/revert":
                self._json(self.repo.revert_file(body["tile"]))
            elif path == "/api/pretest":
                self._json(self.repo.pretest(body))
            elif path == "/api/pr-preview":
                self._json(self.repo.preview_pr(body))
            elif path == "/api/pr":
                self._json(self.repo.create_pr(body))
            else:
                self._error("not found", 404)
        except (ValueError, RuntimeError) as e:
            self._error(str(e))
        except FileNotFoundError as e:
            self._error(tr("editor.err_tile_not_found", "Tile not found: {exc}", exc=e), 404)
        except KeyError as e:
            self._error(tr("editor.err_missing_field",
                           "A required field is missing: {exc}", exc=e), 400)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {e}", 500)


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


def detect_repo() -> Path | None:
    """When app.py sits inside a clone, search the ancestors for a root with building data."""
    for anc in [APP_DIR, *APP_DIR.parents]:
        try:
            if has_building_data(anc):
                return anc
        except OSError:
            # stat can raise EINVAL on special entries near the filesystem root (notably Python 3.9)
            continue
    return None


def create_server(repo_root, port: int, *, data: "str | None" = None,
                  textures=None) -> ThreadingHTTPServer:
    """Entry point for external callers (e.g. the integrated frontend) to assemble this server.

    Even in a frozen build (an exe without a Python runtime), the integrated
    frontend can start this editor via `create_server(...).serve_forever()`.
    Performs the same Handler.repo setup as main() and does not open a browser.
    """
    sync_upstream_main(repo_root)
    Handler.repo = Repo(Path(repo_root), data)
    if textures:
        Handler.repo.tex_override = Path(textures).resolve()
    return ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)


def _make_console_safe() -> None:
    """Never let console output crash the app on a narrow code page.

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


def main() -> None:
    _make_console_safe()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="local clone of sample-tokyo-station (can be omitted when run from inside clone)")
    parser.add_argument("--data", help="substring of data package name (to select if multiple exist; e.g., 13101)")
    parser.add_argument("--textures", type=Path,
                        help="texture replacement directory (for 3D tone variant comparison; missing images show originals)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo or detect_repo()
    if repo_root is None:
        cfg = load_config()
        saved = cfg.get("repo")
        if saved and has_building_data(Path(saved)):
            repo_root = Path(saved)

    if repo_root is not None:
        sync_upstream_main(repo_root)
        try:
            Handler.repo = Repo(repo_root, args.data)
        except RuntimeError as e:
            sys.exit(f"Error: {e}")
        if args.textures:
            Handler.repo.tex_override = args.textures.resolve()
            print(f"  Texture replacement: {Handler.repo.tex_override}")
        # Users who came via setup (config file present) always get a launcher
        saved = load_config().get("repo")
        if saved and Path(saved).resolve() == Handler.repo.root:
            write_launcher(Handler.repo.root)
    # Without repo_root, start in first-run setup mode (the clone runs from the browser)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}/"
    print(f"CityGML attribute editor: {url}")
    if Handler.repo is not None:
        print(f"  Data: {Handler.repo.bldg_dir}")
    else:
        print("  Clone not found → perform first-run setup in browser")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting")


if __name__ == "__main__":
    main()
