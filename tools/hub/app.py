#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""sample-tokyo-station integrated front-end (launcher) — local server.

From a single screen:
- **Launch by button** the attribute editor (attr_editor) / texture editor (tex_editor)
  (each tool's local server starts as a child process and its URL opens)
- Check the GitHub account / git status (entry point of the guided support; detailed tools are #59)
- **List of your own Pull Requests / Issues** (with reply/review indicators)
- **Merged PR count and achievement badges** (gamification)
- "I'm stuck / suggest" in-hub form (creates a UX feedback Issue with the connected token)

Depends only on the Python 3.9+ standard library. **The GitHub authentication for the
initial setup implements the OAuth device flow itself**, so neither gh nor a terminal
is needed (if gh is present, its token is reused).
The post-launch dashboard (PR / Issue lists) uses the `gh` CLI as before.

Usage:
    python3 tools/hub/app.py            # no arguments needed inside a clone → http://localhost:8760
    python3 tools/hub/app.py --repo ~/sample-tokyo-station [--port 8760] [--no-browser]
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import mmap
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

APP_DIR = Path(__file__).resolve().parent
RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
EXE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR
# Where to look for bundled items (PortableGit / PythonPortable / preset.json). In the
# distribution zip, so that users only see the "guide HTML and launcher file", the whole
# bundle goes into "program/" next to the launcher file. Both locations are checked
# so development (running directly in the repo) also works.
LIB_SUBDIR = "program"
BUNDLE_DIRS = list(dict.fromkeys(
    [d for base in (EXE_DIR, APP_DIR) for d in (base, base / LIB_SUBDIR)]
))
# The clone destination is shared across the ecosystem (same config file as the attribute editor)
CONFIG_PATH = Path.home() / ".citygml_attr_editor.json"
UPSTREAM_URL = "https://github.com/4dcitygml/sample-tokyo-station"  # Default (demo city). The actual target is resolved by upstream_url()
DEFAULT_PORT = 8760

_git_resolved: "tuple[str, bool] | None" = None


def _system_git_is_configured(exe: str) -> bool:
    """Whether the Git on PATH has the global settings needed for committing."""
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
    """Return the git executable to use and whether it is the bundled Git (same approach as attr_editor).

    The all-in-one Windows zip bundles MinGit under the compatibility name
    `program/PortableGit/`. If the Git on PATH has user.name / user.email set
    globally, prefer it along with its existing authentication environment;
    otherwise fall back to the bundled Git.
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
    exe, bundled = git_cmd()
    args = [exe or "git"]
    # With the bundled Git, when we hold a token from our own authentication
    # (device flow), use **only** that. Do not change the existing Git's credential
    # helpers. The leading empty helper (credential.helper=) resets the system's
    # default helper list. Without this, on macOS osxkeychain runs alongside and,
    # depending on sandboxing and permissions, a "keychain not found" dialog can
    # appear (observed in the 2026-07-31 end-to-end test).
    # The bundled PortableGit's Credential Manager (which shows a GUI) is the last resort.
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

    Same behavior as the editors' startup sync (attr_editor.sync_upstream_main;
    kept local because the hub is self-contained): fast-forward when possible,
    hard reset when histories diverged (the practice repo rewrites main daily),
    console warning + skip when tracked files are modified, silent skip when
    offline or not a clone. Tool-made edit branches are never touched.
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
            return None
        if run("fetch", "--quiet", upstream_url(root), "main", net=True).returncode != 0:
            return None
        new = run("rev-parse", "FETCH_HEAD").stdout.strip()
        if not new:
            return None
        branch = run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
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


_py_resolved: "tuple[str, bool] | None" = None


def python_cmd() -> "tuple[str, bool]":
    """Python executable used to launch child tools (.py editors) and whether it is the bundled portable one.

    Same approach as PortableGit: prefer `PythonPortable/` next to the exe/py if present.
    This lets the .py edition work on Windows without Python installed (if the
    launcher starts the main app with the bundled python, sys.executable also
    points to it, but detect it here too just in case, to reliably pass it to
    child tools). Falls back to sys.executable if not found.
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


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

# Child tools that can be launched (relative path to app.py, default port)
TOOLS = {
    "attr_editor": {
        "label": "Attribute Editor",
        "desc": "View and edit building attributes from the map and submit a PR",
        "path": "tools/attr_editor/app.py",
        "port": 8765,
        "icon": "🏢",
    },
    "tex_editor": {
        "label": "Texture Editor",
        "desc": "Replace facade photos (LOD2 textures) and submit a PR",
        "path": "tools/tex_editor/app.py",
        "port": 8766,
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


def feedback_categories() -> tuple:
    """Category choices for the in-hub form (in the selected language)."""
    return (
        tr("hub.fb_cat_operation", "The controls are hard to understand"),
        tr("hub.fb_cat_wording", "The explanations or terms are hard to understand"),
        tr("hub.fb_cat_error", "I cannot understand an error or how to fix it"),
        tr("hub.fb_cat_accessibility", "Accessibility"),
        tr("hub.fb_cat_feature", "A feature suggestion to make the work easier"),
        tr("hub.fb_cat_other", "Other"),
    )


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


def review_kind(pr: dict) -> str:
    """Decide the admin-screen display kind from the PR title/branch."""
    explicit = str(pr.get("review_kind") or "")
    if explicit in ("attribute", "texture", "geometry"):
        return explicit
    title = str(pr.get("title") or "")
    head = str((pr.get("head") or {}).get("ref") or pr.get("headRefName") or "")
    # Branch prefix (language-independent) comes first. Title fallbacks match the
    # generated repo-language titles and manual PRs in en / ja / de
    # (ja/de literals mirror the pr.title_* catalog values — contract-tested).
    if (head.startswith("tex/") or title.startswith(("Update textures", "Add textures"))
            or title.startswith("テクスチャ") or title.startswith("Textur")):
        return "texture"
    if (head.startswith("edit/") or title.startswith(("Update attributes", "Update building info"))
            or title.startswith("属性修正") or "属性" in title
            or title.startswith("Attributkorrektur")):
        return "attribute"
    if any(x in title for x in ("geometry", "building shape", "rebuild",
                                "幾何", "建物形状", "建替", "建て替")):
        return "geometry"
    return "other"


_BUILDING_ID_REVIEW_RE = re.compile(r"(?<![\w-])(\d{5}-bldg-[A-Za-z0-9_-]+)")

# Even in standalone distributions/tests without attr_editor, the main items always get readable English labels.
_CORE_ATTRIBUTE_LABELS = {
    "class": "Classification",
    "usage": "Usage",
    "measuredHeight": "Measured Height",
    "storeysAboveGround": "Storeys Above Ground",
    "storeysBelowGround": "Storeys Below Ground",
    "roofType": "Roof Type",
    "yearOfConstruction": "Year of Construction",
    "creationDate": "Creation Date",
    "buildingID": "Building ID",
    "buildingFootprintArea": "Building Footprint Area",
    "totalFloorArea": "Total Floor Area",
    "vacancy": "Vacancy Type",
}


def extract_building_id(*texts: object) -> str:
    """Extract a stable building ID from the title, body, branch name, etc."""
    for text in texts:
        hit = _BUILDING_ID_REVIEW_RE.search(str(text or ""))
        if hit:
            return hit.group(1)
    return ""


def load_attribute_labels(root: Path) -> dict[str, str]:
    """Read the attribute editor's Japanese-name dictionary without executing it, shared with the confirmation screen."""
    labels = dict(_CORE_ATTRIBUTE_LABELS)
    source = root / "tools" / "attr_editor" / "app.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id == "LABELS" for t in targets):
                continue
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                labels.update({str(k): str(v) for k, v in value.items()})
            break
    except (OSError, SyntaxError, ValueError):
        pass
    return labels


def attribute_local_name(path: str) -> str:
    """Get the trailing attribute name from a path like `/uro:x/bldg:y[1]`."""
    raw = str(path or "").strip().strip("`")
    tail = raw.rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"\[.*?\]", "", tail)
    return tail.rsplit(":", 1)[-1]


def attribute_label(path: str, labels: "dict[str, str] | None" = None) -> str:
    """Convert an attribute path to a Japanese name for municipal staff. Keep it if already Japanese."""
    raw = str(path or "").strip().strip("`")
    if re.search(r"[ぁ-んァ-ヶ一-龠々]", raw):
        return raw
    local = attribute_local_name(raw)
    known = (labels or _CORE_ATTRIBUTE_LABELS).get(local)
    if known:
        return known
    return f"Other attribute ({local or 'unknown item'})"


def _replace_attribute_terms(text: str, labels: "dict[str, str] | None" = None) -> str:
    out = str(text or "")
    for key, label in sorted((labels or _CORE_ATTRIBUTE_LABELS).items(), key=lambda x: len(x[0]), reverse=True):
        out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, out)
    return out


def human_proposal_title(title: str, labels: "dict[str, str] | None" = None) -> str:
    """Strip validation/GitHub markers and format as a building-ledger heading."""
    value = str(title or tr("hub.proposal_title_fallback", "Change record"))
    value = re.sub(r"^PR-[A-Z0-9]+\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\s*[—–-]\s*positive\s+validation\s+case.*$", "", value,
        flags=re.IGNORECASE,
    )
    value = value.replace("属性のみ変更", "属性の変更").replace("幾何変更", "建物形状の変更")
    value = _replace_attribute_terms(value, labels)
    value = value.strip()
    # Capitalize the first letter after stripping validation prefixes (English titles
    # arrive lowercase mid-sentence; no effect on Japanese titles).
    if value and value[0].islower():
        value = value[0].upper() + value[1:]
    return value or tr("hub.proposal_title_fallback", "Change record")


def human_reason(
    body: str, kind: str = "other", labels: "dict[str, str] | None" = None
) -> str:
    """Extract from the PR body only the explanation staff need for their decision."""
    reason = section_by_key(
        body, "reason",
        "Reason and supporting evidence", "Summary of changes",
        "編集理由・根拠資料", "変更の概要",
    )
    if not reason and kind in ("attribute", "geometry"):
        reason = body
    reason = re.sub(r"<!--.*?-->", "", reason, flags=re.DOTALL)
    reason = re.split(
        r"(?:#{1,6}\s*)?(?:期待\s*CI|Expected\s+CI|検証項目|確認項目|再現手順)\s*[:：]?",
        reason,
        maxsplit=1,
        flags=re.MULTILINE | re.IGNORECASE,
    )[0]
    useful: list[str] = []
    in_fence = False
    for line in reason.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith("|") or re.match(r"^- \[[ xX]\]", stripped):
            continue
        if stripped.startswith("#"):
            stripped = re.sub(r"^#+\s*", "", stripped)
        if stripped in ("変更内容", "対象建物", "変更種別",
                        "PR type", "Target buildings / scope", "Change type",
                        "Checklist", "Related issues", "Additional notes (optional)",
                        "Source / manifest (source-update / schema / layout etc.)"):
            continue
        useful.append(stripped)
    result = "\n".join(useful).strip()
    result = re.sub(
        r"\**positive\s+validation\s+case(?:（[^）]*）)?\**[。.]?",
        "", result, flags=re.IGNORECASE,
    )
    result = re.sub(
        r"`?bldg_[A-Za-z0-9_.…-]+`?\s*\((\d{5}-bldg-[A-Za-z0-9_-]+)\)",
        r"\1", result,
    )
    result = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2), result)
    result = result.replace("`", "").replace("幾何変更", "建物形状の変更")
    result = _replace_attribute_terms(result, labels)
    result = re.sub(r"^[\s。・]+", "", result)
    return result.strip()


def evidence_links(markdown: str) -> list[dict]:
    """Split links in the evidence section into human-facing names and URLs."""
    links: list[dict] = []
    seen: set[str] = set()
    for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", markdown or ""):
        if url not in seen:
            links.append({"label": label.strip() or tr("hub.evidence_link", "Supporting document"),
                          "url": url})
            seen.add(url)
    without_markdown = re.sub(r"\[[^\]]+\]\(https?://[^)\s]+\)", "", markdown or "")
    for url in re.findall(r"https?://[^\s<>)]+", without_markdown):
        url = url.rstrip(".,、。")
        if url not in seen:
            links.append({"label": tr("hub.evidence_link", "Supporting document"), "url": url})
            seen.add(url)
    return links


def check_display_name(name: str) -> str:
    """Convert internal CI/check-run names to display names (selected language) that convey what is judged."""
    key = str(name or "").strip().lower()
    rules = (
        (("preview", "cesium", "3d"),
         tr("hub.check_preview", "3D view necessity and generation check")),
        (("texture", "appearance"),
         tr("hub.check_texture", "Facade image and building surface consistency check")),
        (("reviewability", "lint"),
         tr("hub.check_reviewability", "Change readability check")),
        (("metadata", "history", "lifecycle"),
         tr("hub.check_metadata", "Change history record check")),
        (("citygml", "validation", "validate", "schema"),
         tr("hub.check_citygml", "CityGML format check")),
        (("analyze", "analysis", "scope"),
         tr("hub.check_analyze", "Change scope and data format check")),
    )
    for needles, label in rules:
        if any(n in key for n in needles):
            return label
    return tr("hub.check_generic", "Automated building data check")


def _overall_check_status(checks: list) -> str:
    """Summarize GitHub check-runs into pass/fail/pending for the screen."""
    if not checks or any(c.get("status") != "completed" for c in checks):
        return "pending"
    if any(c.get("conclusion") not in ("success", "neutral") for c in checks):
        return "fail"
    return "pass"


def ci_retry_info(check_status: str, comments: list) -> dict:
    """Explain, per cause, whether re-inspection on the same data is useful."""
    freshness = any(
        "<!-- citygml-base-freshness -->" in str(comment.get("body") or "")
        and "<!-- status:active -->" in str(comment.get("body") or "")
        for comment in comments
    )
    data_adjustment = any(
        "<!-- citygml-auto-resubmission -->" in str(comment.get("body") or "")
        and "<!-- status:active -->" in str(comment.get("body") or "")
        for comment in comments
    )
    if freshness:
        return {
            "available": False, "kind": "update",
            "label": tr("hub.retry_update_label", "Merge in the latest version and resubmit"),
            "reason": tr("hub.retry_update_reason",
                         "Another change was applied first, so re-running the checks "
                         "on the same content will not resolve this"),
        }
    if data_adjustment:
        return {
            "available": False, "kind": "fix",
            "label": tr("hub.retry_fix_label", "Fix the content and the checks re-run automatically"),
            "reason": tr("hub.retry_fix_reason",
                         "There are items to confirm in the data or the description. "
                         "Sending a fix re-runs the checks automatically"),
        }
    if check_status == "fail":
        return {
            "available": True, "kind": "system",
            "label": tr("hub.retry_system_label", "Re-run the automated checks"),
            "reason": tr("hub.retry_system_reason",
                         "No content issues were found; this may be a temporary "
                         "failure of the check process"),
        }
    if check_status == "pending":
        return {
            "available": False, "kind": "pending",
            "label": tr("hub.retry_pending_label", "Automated checks running"),
            "reason": tr("hub.retry_pending_reason", "Please wait until the checks finish"),
        }
    return {
        "available": False, "kind": "none", "label": "",
        "reason": tr("hub.retry_none_reason", "No re-run is needed"),
    }


def _marker_status(comments: list, marker: str, fallback: str) -> str:
    """Prefer the explicit result in CI comments; otherwise use the overall check-run state."""
    for comment in comments:
        body = str(comment.get("body") or "")
        if marker not in body:
            continue
        if "❌" in body or "⚠️" in body or "不合格" in body:
            return "fail"
        if "✅" in body:
            return "pass"
    return fallback


_CP_KEY_RE = re.compile(r"<!--\s*cp:([a-z0-9-]+)\s*-->")


def _inspection_summary_statuses(comments: list) -> dict[str, str]:
    """Convert the automatic-inspection list posted by trusted CI back to inspection key → screen status.

    If a line has a `<!--cp:key-->` anchor, store by key; otherwise store by the
    English display name (interchange format v2: matching uses key + emoji, the display wording is free).
    """
    result: dict[str, str] = {}
    for comment in comments:
        body = str(comment.get("body") or "")
        if "<!-- citygml-automatic-inspection -->" not in body:
            continue
        for line in body.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            raw = cells[1]
            if "−" in raw or "Not applicable" in raw:
                status = "na"
            elif "✅" in raw or raw.startswith("Pass"):
                status = "pass"
            elif "…" in raw or "Checking" in raw:
                status = "pending"
            elif "❌" in raw or "⚠️" in raw or "Needs" in raw or "Not run" in raw:
                status = "fail"
            else:
                continue
            key_match = _CP_KEY_RE.search(line)
            name = (key_match.group(1) if key_match
                    else re.sub(r"<!--.*?-->", "", cells[0]).strip())
            result[name] = status
    return result


def review_checkpoints(
    *, reason_ok: bool, unsafe_files: list, checks: list, model_available: bool,
    kind: str = "attribute", comments: "list | None" = None,
    changes_requested: bool = False, adjustment_owner: str = "ci",
) -> list[dict]:
    """List every inspection item — pass, not-applicable, and fail — for the person in charge.

    Matched via `match` (item names in the trusted CI inspection-list comment;
    Japanese = interchange format) and returned with `label` (display name in the
    selected language).
    """
    comments = comments or []
    check_status = _overall_check_status(checks)
    summary = _inspection_summary_statuses(comments)
    resubmit = tr("hub.cp_wait_proposer_ci",
                  "CI has posted items to confirm. Waiting for the proposer to respond")
    if changes_requested and adjustment_owner == "reviewer":
        resubmit = tr("hub.cp_wait_proposer_reviewer",
                      "The reviewer has sent a comment to confirm. "
                      "Waiting for the proposer to respond")

    def point(key: str, match: str, label: str, status: str, ok: str, ng: str) -> dict:
        # Matching prefers the key (<!--cp:key-->), falling back to the English display name
        status = summary.get(key, summary.get(match, status))
        reason = ok if status == "pass" else ng
        if status == "na":
            reason = tr("hub.cp_na_reason", "Not applicable to this change")
            action = tr("hub.cp_no_action", "No action is needed")
        elif status == "pending":
            reason = tr("hub.cp_pending_reason", "Automated checks are running")
            action = tr("hub.cp_pending_action",
                        "Automated checks handle this until completion. "
                        "No reviewer action is needed")
        elif status == "fail":
            action = resubmit
        else:
            action = tr("hub.cp_no_action", "No action is needed")
        return {"key": key, "label": label, "status": status, "reason": reason, "action": action}

    commit_scope = _marker_status(comments, "<!-- citygml-commit-scope -->", check_status)
    freshness = _marker_status(comments, "<!-- citygml-base-freshness -->", check_status)
    if freshness == "fail":
        summary["freshness"] = "fail"
    reviewability = _marker_status(comments, "<!-- citygml-reviewability-lint -->", check_status)
    structure = _marker_status(comments, "<!-- citygml-quality-lint -->", check_status)
    plausibility = _marker_status(comments, "<!-- plateau-quality-lint -->", check_status)
    topology = (
        _marker_status(comments, "<!-- val3dity-topology-gate -->", check_status)
        if kind == "geometry" else "na"
    )
    texture = check_status if kind == "texture" else "na"
    return [
        point("reason", "Description and evidence",
              tr("hub.cp_reason_label", "Description and evidence"),
              "pass" if reason_ok else "fail",
              tr("hub.cp_reason_ok",
                 "The reason for the change and the evidence can be confirmed"),
              tr("hub.cp_reason_ng",
                 "The reason for the change or the evidence is not filled in")),
        point("commit-scope", "One change = one building",
              tr("hub.cp_commit_scope_label", "One change = one building"), commit_scope,
              tr("hub.cp_commit_scope_ok", "The change is contained to one building"),
              tr("hub.cp_commit_scope_ng",
                 "The change touches multiple buildings, or the building IDs are inconsistent")),
        point("freshness", "Consistency with the latest version",
              tr("hub.cp_freshness_label", "Consistency with the latest version"), freshness,
              tr("hub.cp_freshness_ok", "The change is based on the latest published data"),
              tr("hub.cp_freshness_ng",
                 "Another change was applied first, so the latest version needs to be merged in")),
        point("file-scope", "Changed file scope",
              tr("hub.cp_file_scope_label", "Changed file scope"),
              "pass" if not unsafe_files else "fail",
              tr("hub.cp_file_scope_ok", "Only the target building data is changed"),
              tr("hub.cp_file_scope_ng", "Files other than building data are included")),
        point("schema", "CityGML format",
              tr("hub.cp_schema_label", "CityGML format"), check_status,
              tr("hub.cp_schema_ok", "The file conforms to the CityGML syntax and schema"),
              tr("hub.cp_schema_ng",
                 "There is an inconsistency in the CityGML syntax or schema")),
        point("minimal-diff", "Minimal diff",
              tr("hub.cp_minimal_diff_label", "Minimal diff"), reviewability,
              tr("hub.cp_minimal_diff_ok", "There is no large diff unnecessary for review"),
              tr("hub.cp_minimal_diff_ng",
                 "The change is large, or unnecessary ID changes are suspected")),
        point("texture", "Texture consistency",
              tr("hub.cp_texture_label", "Texture consistency"), texture,
              tr("hub.cp_texture_ok",
                 "The facade image references and update method are fine"),
              tr("hub.cp_texture_ng",
                 "There is a problem with the facade image references or update method")),
        point("structure", "Geometric structure",
              tr("hub.cp_structure_label", "Geometric structure"), structure,
              tr("hub.cp_structure_ok", "The surface and ring structures are fine"),
              tr("hub.cp_structure_ng",
                 "There is an inconsistency in the surface or ring structures")),
        point("plausibility", "Attribute value plausibility",
              tr("hub.cp_plausibility_label", "Attribute value plausibility"), plausibility,
              tr("hub.cp_plausibility_ok", "The attribute values and code values are fine"),
              tr("hub.cp_plausibility_ng",
                 "There are attribute or code values that need fixing")),
        point("topology", "Topological consistency",
              tr("hub.cp_topology_label", "Topological consistency"), topology,
              tr("hub.cp_topology_ok",
                 "The change introduces no new topological inconsistencies"),
              tr("hub.cp_topology_ng",
                 "The change introduces new topological inconsistencies")),
        point("model", "3D view",
              tr("hub.cp_model_label", "3D view"), "pass" if model_available else "fail",
              tr("hub.cp_model_ok", "The building's 3D model can be viewed"),
              tr("hub.cp_model_ng", "The building's 3D model cannot be prepared")),
    ]


def markdown_tables(text: str) -> list[dict]:
    """Structure the simple Markdown tables in the PR body / CI comments."""
    tables: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i + 1 < len(lines):
        head = lines[i].strip()
        sep = lines[i + 1].strip()
        if not (head.startswith("|") and sep.startswith("|") and "---" in sep):
            i += 1
            continue

        def cells(line: str) -> list[str]:
            return [c.strip().strip("`") for c in line.strip().strip("|").split("|")]

        headers = cells(head)
        rows: list[dict] = []
        i += 2
        while i < len(lines) and lines[i].strip().startswith("|"):
            vals = cells(lines[i])
            if len(vals) == len(headers):
                rows.append(dict(zip(headers, vals)))
            i += 1
        tables.append({"headers": headers, "rows": rows})
    return tables


def section_text(markdown: str, heading: str) -> str:
    """Extract from just below `## heading` up to the next same-level heading."""
    m = re.search(
        rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    return (m.group(1).strip() if m else "")


def section_by_key(markdown: str, key: str, *headings: str) -> str:
    """Extract the section whose heading has a `<!--sec:key-->` anchor (interchange format v2).

    For bodies without anchors (manual PRs etc.), search the headings' strings in order.
    """
    m = re.search(
        rf"^##[^\n]*<!--\s*sec:{re.escape(key)}\s*-->[^\n]*$\n(.*?)(?=^##\s+|\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    for heading in headings:
        text = section_text(markdown, heading)
        if text:
            return text
    return ""


def review_ready_reason(reason: str) -> bool:
    normalized = reason.strip()
    return bool(normalized and "記入してください" not in normalized
                and "please fill in" not in normalized.lower())


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


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


def detect_repo() -> "Path | None":
    """Search the ancestors for a repository root that has building data."""
    for anc in [APP_DIR, *APP_DIR.parents]:
        try:
            if has_building_data(anc):
                return anc
        except OSError:
            continue
    return None


def serve_tool(root: Path, key: str, port: int) -> None:
    """Load tools/<key>/app.py inside the clone and start its server (blocking).

    In the frozen distribution the hub exe itself takes this path via `--serve-tool`,
    so each editor launches without a python interpreter. The tools actually live in
    the clone (tools/…), so the same path resolves for both source and frozen runs.
    """
    t = TOOLS.get(key)
    if t is None:
        sys.exit(f"Unknown tool: {key}")
    app = root / t["path"]
    if not app.is_file():
        sys.exit(f"Tool not found: {app}")
    spec = importlib.util.spec_from_file_location(f"_tool_{key}", str(app))
    mod = importlib.util.module_from_spec(spec)
    # Put tools/ on the import path for relative-import compatibility (for tex, which inherits attr)
    tools_dir = str(root / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "create_server"):
        sys.exit(f"{key} has no create_server (update required)")
    server = mod.create_server(root, port)
    print(f"Launched {t['label']}: http://localhost:{port}/")
    server.serve_forever()



# ---- Theme pack (shares tools/themes/theme_loader.py; runs without themes if missing) ----
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


def themed_html(data: bytes, repo_root) -> bytes:
    """Apply the city repo's theme.json to the HTML. On an invalid theme.json, serve without a theme and warn."""
    mod = theme_module()
    if mod is None or repo_root is None:
        return data
    try:
        tokens = mod.resolve_theme(repo_root)
        return mod.inject_theme(data, mod.theme_css(tokens))
    except Exception as e:  # Do not block display (themes are decoration, not functionality)
        print(f"Ignoring theme.json: {e}", file=sys.stderr)
        return data


# ---- Language pack (shares tools/i18n/i18n_loader.py; runs with the original text if missing) ----
_i18n_mod = None


def i18n_module():
    global _i18n_mod
    if _i18n_mod is not None:
        return _i18n_mod or None
    import importlib.util
    for cand in (APP_DIR / "i18n" / "i18n_loader.py",  # distribution: program/i18n/
                 RES_DIR.parent / "i18n" / "i18n_loader.py",
                 APP_DIR.parent / "i18n" / "i18n_loader.py"):
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("i18n_loader", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _i18n_mod = mod
            return mod
    _i18n_mod = False
    return None


def localized_html(data: bytes, app: str = "hub") -> bytes:
    """Inject the selected language's catalog into the HTML. On failure, serve the original text and warn."""
    mod = i18n_module()
    if mod is None:
        return data
    try:
        return mod.inject_i18n(data, app, mod.resolve_lang(None))
    except Exception as e:
        print(f"Ignoring language pack: {e}", file=sys.stderr)
        return data


def tr(key: str, default: str, **params) -> str:
    """Translation of server-generated (Python) text (fail-open).

    Even when the i18n module is missing or broken, apply the {name} placeholders
    to default (the English original) and return it, never blocking display.
    """
    mod = i18n_module()
    if mod is not None:
        try:
            return mod.translate("hub", key, default, **params)
        except Exception:
            pass
    s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


class Hub:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._attribute_labels = load_attribute_labels(self.root)
        self._attr_repo = None
        self._attr_repo_lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._ports: dict[str, int] = {}   # tool ports used by this hub (city)
        self._repo_probe: dict[int, "tuple[float, bool]"] = {}  # port -> (timestamp, is our repo)
        self._contrib_cache: "tuple[float, dict] | None" = None
        self._cache_lock = threading.Lock()

    # ---- Repository / account status ----
    def _git(self, *args: str) -> str:
        exe, _ = git_cmd()
        if exe is None:
            return ""
        r = subprocess.run(
            [exe, "-C", str(self.root), *args], capture_output=True, text=True
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    def nwo(self) -> "str | None":
        """origin's owner/repo (for GitHub links and gh)."""
        url = self._git("remote", "get-url", "origin")
        m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url) or re.match(
            r"https://github\.com/(.+?)(?:\.git)?$", url
        )
        return m.group(1) if m else None

    def status(self) -> dict:
        git_exe, git_bundled = git_cmd()
        py_exe, py_bundled = python_cmd()
        return {
            "ok": True,
            "repo": str(self.root),
            "branch": self._git("rev-parse", "--abbrev-ref", "HEAD"),
            "gitUser": self._git("config", "user.name"),
            "gitEmail": self._git("config", "user.email"),
            "nwo": self.nwo(),
            "runtime": {
                "git": {"path": git_exe, "bundled": git_bundled},
                "python": {"path": py_exe, "bundled": py_bundled},
            },
        }

    def _tool_app(self, t: dict) -> "Path | None":
        """Actual tool app.py (resolved from the clone layout first, then the hub bundle).

        In the public layout the tools (tools repo) and the data (city repo) are
        separate, so when absent from the clone, use the copy next to the hub itself (tools/…).
        """
        cand = self.root / t["path"]
        if cand.is_file():
            return cand
        rel = Path(t["path"]).relative_to("tools")
        for cand in (APP_DIR / rel, APP_DIR.parent / rel):  # distribution: program/<editor>/app.py
            if cand.is_file():
                return cand
        return None

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
        allocated = self._ports.get(key)
        if allocated is not None:
            return allocated
        port = t["port"]
        for _ in range(20):
            if not port_open(port) or self._port_serves_our_repo(port):
                break
            port += 2
        self._ports[key] = port
        return port

    def tools(self) -> list[dict]:
        out = []
        for key, t in TOOLS.items():
            exists = self._tool_app(t) is not None
            port = self._ports.get(key)
            if port is None and port_open(t["port"]) and self._port_serves_our_repo(t["port"]):
                # Adopt a manually launched editor of our own city
                port = self._ports[key] = t["port"]
            display = port or t["port"]
            running = port is not None and port_open(port)
            out.append({
                "key": key, "label": tool_label(key, t), "desc": tool_desc(key, t),
                "icon": t["icon"],
                "port": display, "url": f"http://localhost:{display}/",
                "available": exists, "running": running,
            })
        return out

    def _launch_cmd(self, key: str, t: dict, port: "int | None" = None) -> list:
        """Child-tool launch command. In the frozen distribution (an exe without a
        python interpreter), re-invoke the hub exe itself with `--serve-tool`;
        when running from source, launch each tool's app.py directly."""
        port = port or t["port"]
        if getattr(sys, "frozen", False):
            return [sys.executable, "--serve-tool", key,
                    "--repo", str(self.root), "--port", str(port)]
        return [python_cmd()[0], str(self._tool_app(t) or (self.root / t["path"])),
                "--repo", str(self.root), "--port", str(port), "--no-browser"]

    def launch(self, key: str) -> dict:
        t = TOOLS.get(key)
        if t is None:
            raise ValueError(tr("hub.err_unknown_tool", "Unknown tool: {key}", key=key))
        app = self._tool_app(t)
        if app is None:
            raise FileNotFoundError(t["path"])
        port = self._tool_port(key, t)
        url = f"http://localhost:{port}/"
        if port_open(port):
            return {"ok": True, "url": url, "port": port, "already": True}
        proc = subprocess.Popen(self._launch_cmd(key, t, port), cwd=str(self.root))
        self._procs[key] = proc
        # Wait a bit until the server listens (up to ~6 seconds)
        for _ in range(30):
            if port_open(port):
                break
            if proc.poll() is not None:
                raise RuntimeError(tr(
                    "hub.err_launch_failed",
                    "{label} failed to start (process exited with code {code})",
                    label=tool_label(key, t), code=proc.returncode,
                ))
            time.sleep(0.2)
        if not port_open(port):
            raise RuntimeError(tr(
                "hub.err_launch_no_response", "{label} is not responding (port {port})",
                label=tool_label(key, t), port=port,
            ))
        return {"ok": True, "url": url, "port": port, "already": False}

    # ---- Contributions (PRs / Issues) ----
    def contributions(self, force: bool = False) -> dict:
        with self._cache_lock:
            if not force and self._contrib_cache and time.time() - self._contrib_cache[0] < 30:
                return self._contrib_cache[1]
        data = self._fetch_contributions()
        with self._cache_lock:
            self._contrib_cache = (time.time(), data)
        return data

    # Fetch your own PRs / Issues in one request (same GraphQL API and same fields
    # as gh pr list / gh issue list; no gh CLI dependency = works with the device-flow token).
    _CONTRIB_QUERY = """query($qPr: String!, $qIssue: String!) {
      prs: search(query: $qPr, type: ISSUE, first: 100) {
        nodes { ... on PullRequest {
          number title state url isDraft reviewDecision updatedAt
          commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
        } }
      }
      issues: search(query: $qIssue, type: ISSUE, first: 100) {
        nodes { ... on Issue {
          number title state url updatedAt comments { totalCount } } }
      }
    }"""

    def _fetch_contributions(self) -> dict:
        nwo = self.nwo()
        token = AUTH.token()
        login = ((AUTH.user() or {}) if token else {}).get("login")
        if not token or not login or not nwo:
            reason = (
                tr("hub.err_origin_not_github",
                   "The repository remote (origin) does not point to GitHub") if not nwo
                else tr("hub.err_gh_not_connected",
                        'GitHub is not connected (you can connect from "Connect to GitHub" '
                        "in the account section)"))
            return {"ok": False, "reason": reason, "login": None,
                    "prs": [], "issues": [], "merged": 0, "badge": badge_for(0)}
        code, data = gh_api("/graphql", token, method="POST", payload={
            "query": self._CONTRIB_QUERY,
            "variables": {"qPr": f"repo:{nwo} author:{login} type:pr sort:updated",
                          "qIssue": f"repo:{nwo} author:{login} type:issue sort:updated"},
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
            commit_nodes = ((p.get("commits") or {}).get("nodes") or [])
            rollup = (
                (((commit_nodes[-1] or {}).get("commit") or {}).get("statusCheckRollup") or {})
                if commit_nodes else {}
            )
            rollup_state = str(rollup.get("state") or "").upper()
            check_status = (
                "pass" if rollup_state == "SUCCESS"
                else "fail" if rollup_state in ("ERROR", "FAILURE")
                else "pending" if rollup_state in ("EXPECTED", "PENDING")
                else ""
            )
            retry = ci_retry_info(check_status, [])
            if p.get("state") == "OPEN" and check_status == "fail":
                c_code, c_data = gh_api(
                    f"/repos/{nwo}/issues/{p['number']}/comments?per_page=100", token
                )
                comments = c_data if c_code == 200 and isinstance(c_data, list) else []
                retry = ci_retry_info(check_status, comments)
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
        merged = sum(1 for p in prs if p["state"] == "MERGED")
        return {"ok": True, "login": login, "nwo": nwo,
                "prs": prs, "issues": issues, "merged": merged, "badge": badge_for(merged)}

    # ---- PR review for administrators ----
    def _review_identity(self) -> tuple[str, str, str]:
        token = AUTH.token()
        login = ((AUTH.user() or {}) if token else {}).get("login") or ""
        nwo = upstream_nwo(getattr(self, "root", None))
        if not token or not login:
            raise RuntimeError(tr("hub.err_connect_first", "Connect to GitHub first"))
        return token, login, nwo

    def reviewer_permission(self) -> dict:
        token, login, nwo = self._review_identity()
        code, data = gh_api(f"/repos/{nwo}/collaborators/{login}/permission", token)
        permission = str(data.get("permission") or "none") if code == 200 else "none"
        return {
            "login": login,
            "permission": permission,
            "canReview": permission in ("admin", "maintain", "push", "write"),
        }

    @staticmethod
    def _summary_gml_ids(comments: list) -> list[str]:
        """Get the gml:ids from the building headings of the change summary."""
        ids: list[str] = []
        for comment in comments:
            body = str(comment.get("body") or "")
            if "<!-- citygml-change-summary -->" not in body:
                continue
            for value in re.findall(r"^####\s+`([^`]+)`\s*$", body, flags=re.MULTILINE):
                if value not in ids:
                    ids.append(value)
        return ids

    def _building_ref_from_local_files(
        self, files: list, comments: list, stable_id: str = ""
    ) -> dict:
        """Match the change target against local CityGML and return the ID, position, and display references."""
        gml_ids = self._summary_gml_ids(comments)
        safe_gml = re.compile(r"^[^/]+/udx/bldg/[^/]+\.gml$", re.IGNORECASE)
        building_start = re.compile(rb"<(?:\w+:)?Building\b")
        gml_id_attr = re.compile(rb"\bgml:id=[\"']([^\"']+)[\"']")
        building_id_tag = re.compile(
            rb"<(?:\w+:)?buildingID>([^<]+)</(?:\w+:)?buildingID>"
        )
        pos_list_tag = re.compile(
            rb"<(?:\w+:)?posList\b[^>]*>([^<]+)</(?:\w+:)?posList>"
        )
        for file in files:
            rel = str(file.get("filename") or file.get("path") or "")
            if not safe_gml.match(rel) or ".." in Path(rel).parts:
                continue
            source = self.root / rel
            try:
                with source.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    candidates: list[tuple[str, int]] = []
                    for gml_id in gml_ids:
                        hit = data.find(gml_id.encode("utf-8"))
                        if hit >= 0:
                            candidates.append((gml_id, hit))
                    if stable_id and not candidates:
                        hit = data.find(stable_id.encode("utf-8"))
                        if hit < 0:
                            continue
                        start_match = None
                        for match in building_start.finditer(data, 0, hit):
                            start_match = match
                        if start_match:
                            id_match = gml_id_attr.search(data, start_match.start(), hit)
                            if id_match:
                                candidates.append((
                                    id_match.group(1).decode("utf-8", errors="replace"),
                                    start_match.start(),
                                ))
                    for gml_id, hit in candidates:
                        next_building = building_start.search(data, hit + len(gml_id))
                        end = next_building.start() if next_building else min(len(data), hit + 8_000_000)
                        stable_match = building_id_tag.search(data, hit, end)
                        resolved_id = (
                            stable_match.group(1).decode("utf-8", errors="replace").strip()
                            if stable_match else stable_id
                        )
                        center = None
                        pos_match = pos_list_tag.search(data, hit, end)
                        if pos_match:
                            try:
                                nums = [float(x) for x in pos_match.group(1).split()]
                                lats = nums[0::3]
                                lons = nums[1::3]
                                if lats and lons:
                                    center = [
                                        round((min(lats) + max(lats)) / 2, 7),
                                        round((min(lons) + max(lons)) / 2, 7),
                                    ]
                            except ValueError:
                                center = None
                        return {
                            "buildingId": resolved_id,
                            "gid": gml_id,
                            "tile": Path(rel).name.split("_", 1)[0],
                            "center": center,
                        }
            except (OSError, ValueError):
                continue
        return {}

    def _building_id_from_local_files(self, files: list, comments: list) -> str:
        """Match the summary's gml:id against local CityGML and convert it to a stable building ID."""
        return str(self._building_ref_from_local_files(files, comments).get("buildingId") or "")

    def _attribute_repo(self):
        """Lazily reuse the existing attribute editor's 3D-model loading for the confirmation screen too."""
        if self._attr_repo is not None:
            return self._attr_repo
        with self._attr_repo_lock:
            if self._attr_repo is not None:
                return self._attr_repo
            app = self.root / "tools" / "attr_editor" / "app.py"
            if not app.is_file():
                raise FileNotFoundError(
                    tr("hub.err_viewer_missing", "The 3D view component was not found"))
            spec = importlib.util.spec_from_file_location("_hub_attr_editor", str(app))
            if spec is None or spec.loader is None:
                raise RuntimeError(
                    tr("hub.err_viewer_load", "The 3D view component could not be loaded"))
            mod = importlib.util.module_from_spec(spec)
            tools_dir = str(self.root / "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
            spec.loader.exec_module(mod)
            self._attr_repo = mod.Repo(self.root)
        return self._attr_repo

    def review_building_model(self, tile: str, gid: str) -> dict:
        if not re.fullmatch(r"\d{8,9}", tile):
            raise ValueError(tr("hub.err_bad_mesh", "Invalid mesh number"))
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", gid):
            raise ValueError(tr("hub.err_bad_building_id", "Invalid building identifier"))
        return self._attribute_repo().building_json(tile, gid)

    def review_texture(self, path: str) -> tuple[bytes, str]:
        """Return only the local appearance images used by the 3D view."""
        if ".." in Path(path).parts or not re.fullmatch(
            r"[^/]+_appearance/[^/]+\.(?:jpe?g|png|tiff?)", path, re.IGNORECASE
        ):
            raise ValueError(tr("hub.err_not_appearance", "Only facade images can be fetched"))
        repo = self._attribute_repo()
        source = repo.bldg_dir / path
        if not source.is_file():
            raise FileNotFoundError(tr("hub.err_appearance_missing", "Facade image not found"))
        mime = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".tif": "image/tiff", ".tiff": "image/tiff",
        }.get(source.suffix.lower(), "application/octet-stream")
        return source.read_bytes(), mime

    def _queue_building_id(self, token: str, nwo: str, pr: dict) -> str:
        direct = extract_building_id(
            pr.get("title"), pr.get("body"), (pr.get("head") or {}).get("ref")
        )
        if direct:
            return direct
        number = int(pr.get("number") or 0)
        if number < 1:
            return ""
        c_status, comments = gh_api(f"/repos/{nwo}/issues/{number}/comments?per_page=100", token)
        f_status, files = gh_api(f"/repos/{nwo}/pulls/{number}/files?per_page=100", token)
        comments = comments if c_status == 200 and isinstance(comments, list) else []
        files = files if f_status == 200 and isinstance(files, list) else []
        return extract_building_id(*[c.get("body") for c in comments]) or self._building_id_from_local_files(
            files, comments
        )

    def _review_queue_item(self, token: str, nwo: str, pr: dict) -> "dict | None":
        """Build one list item, with two states based on who works on it next."""
        kind = review_kind(pr)
        if kind == "other" or str((pr.get("base") or {}).get("ref") or "") != "main":
            return None
        number = int(pr.get("number") or 0)
        body = str(pr.get("body") or "")
        reason_ok = review_ready_reason(human_reason(body, kind, self._attribute_labels))
        head_sha = str((pr.get("head") or {}).get("sha") or "")
        check_runs: list = []
        if head_sha:
            status, data = gh_api(
                f"/repos/{nwo}/commits/{head_sha}/check-runs?per_page=100", token
            )
            if status == 200 and isinstance(data, dict):
                check_runs = data.get("check_runs") or []
        check_status = _overall_check_status(check_runs)

        c_status, comment_data = gh_api(
            f"/repos/{nwo}/issues/{number}/comments?per_page=100", token
        )
        comments = comment_data if c_status == 200 and isinstance(comment_data, list) else []
        r_status, review_data = gh_api(
            f"/repos/{nwo}/pulls/{number}/reviews?per_page=100", token
        )
        reviews = review_data if r_status == 200 and isinstance(review_data, list) else []
        reviewer_feedback = any(
            r.get("state") == "CHANGES_REQUESTED"
            and str(r.get("commit_id") or "") == head_sha
            for r in reviews
        )
        freshness_feedback = any(
            "<!-- citygml-base-freshness -->" in str(c.get("body") or "")
            and "<!-- status:active -->" in str(c.get("body") or "")
            for c in comments
        )
        auto_resubmit = any(
            "<!-- citygml-auto-resubmission -->" in str(c.get("body") or "")
            and "<!-- status:active -->" in str(c.get("body") or "")
            for c in comments
        ) or freshness_feedback or not reason_ok or check_status == "fail"
        adjustment_reasons = []
        if not reason_ok:
            adjustment_reasons.append(
                tr("hub.adjust_reason_missing", "Reason and evidence not filled in"))
        if check_status == "fail":
            adjustment_reasons.append(
                tr("hub.adjust_checks_failing", "Automated checks have failing items"))
        elif check_status == "pending":
            adjustment_reasons.append(tr("hub.retry_pending_label", "Automated checks running"))
        elif auto_resubmit and reason_ok:
            adjustment_reasons.append(
                tr("hub.adjust_wait_latest", "Waiting for the latest version to be merged in")
                if freshness_feedback
                else tr("hub.adjust_ci_items", "CI has items to confirm")
            )
        if reviewer_feedback:
            adjustment_reasons.append(
                tr("hub.adjust_reviewer_comment", "The reviewer has posted a comment to confirm"))
        if pr.get("draft"):
            adjustment_reasons.append(tr("hub.adjust_draft", "The proposer is still drafting"))
        ready = (
            reason_ok and check_status == "pass" and not pr.get("draft")
            and not reviewer_feedback and not auto_resubmit
            and (str(pr.get("state") or "").lower() == "open" or pr.get("example"))
        )
        queue_status = "reviewer_waiting" if ready else "proposer_waiting"
        if ready:
            waiting_source = ""
        elif reviewer_feedback:
            waiting_source = "reviewer"
        elif freshness_feedback:
            waiting_source = "latest"
        elif check_status == "pending":
            waiting_source = "checking"
        elif pr.get("draft"):
            waiting_source = "draft"
        else:
            waiting_source = "ci"
        building_id = extract_building_id(
            pr.get("title"), body, (pr.get("head") or {}).get("ref"),
            *[c.get("body") for c in comments],
        )
        if not building_id:
            building_id = self._queue_building_id(token, nwo, pr)
        return {
            "number": number,
            "buildingId": building_id,
            "title": human_proposal_title(pr.get("title") or "", self._attribute_labels),
            "technicalTitle": pr.get("title") or "",
            "kind": kind,
            "author": ((pr.get("user") or {}).get("login") or ""),
            "draft": bool(pr.get("draft")),
            "state": pr.get("state") or "open",
            "updated": pr.get("updated_at") or "",
            "url": pr.get("html_url") or "",
            "example": bool(pr.get("example")),
            "queueStatus": queue_status,
            "waitingSource": waiting_source,
            "adjustmentReasons": adjustment_reasons,
            "autoResubmit": auto_resubmit,
            "reviewerFeedback": reviewer_feedback,
        }

    def review_queue(self, include_examples: bool = False) -> dict:
        token, login, nwo = self._review_identity()
        code, pulls = gh_api(
            f"/repos/{nwo}/pulls?state=open&sort=updated&direction=desc&per_page=50",
            token,
        )
        if code != 200 or not isinstance(pulls, list):
            raise RuntimeError(str(getattr(pulls, "get", lambda *_: "")("message") or f"HTTP {code}"))

        if include_examples and not any(int(p.get("number") or 0) == 6 for p in pulls):
            ex_code, example = gh_api(f"/repos/{nwo}/pulls/6", token)
            if ex_code == 200 and isinstance(example, dict):
                example = {**example, "example": True, "review_kind": "attribute"}
                pulls.append(example)

        candidates = [
            pr for pr in pulls
            if review_kind(pr) != "other"
            and str((pr.get("base") or {}).get("ref") or "") == "main"
        ]
        workers = min(8, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            items = [
                item for item in pool.map(
                    lambda pr: self._review_queue_item(token, nwo, pr), candidates
                ) if item is not None
            ]
        perm = self.reviewer_permission()
        return {"ok": True, "nwo": nwo, "login": login, **perm, "items": items}

    def _change_rows(self, body: str, comments: list, kind: str) -> list[dict]:
        sources = [body]
        sources += [
            str(c.get("body") or "") for c in comments
            if "<!-- citygml-change-summary -->" in str(c.get("body") or "")
        ]
        for source in sources:
            for table in markdown_tables(source):
                heads = table["headers"]
                before_key = next((h for h in heads if h in (
                    "Before", "Old", "Before image",
                    "変更前", "旧", "変更前画像")), None)
                after_key = next((h for h in heads if h in (
                    "After", "New", "After image (newly added)",
                    "変更後", "新", "変更後画像（新規追加）")), None)
                if not before_key or not after_key:
                    continue
                label_key = next(
                    (h for h in heads if h in ("Item", "Attribute", "path",
                                               "属性", "項目", "変更箇所")),
                    None,
                )
                rows = []
                for i, row in enumerate(table["rows"], 1):
                    raw_label = row.get(label_key, "") if label_key else ""
                    if kind == "texture":
                        label = tr("hub.change_texture_row", "Facade image {n}", n=i)
                    else:
                        label = (
                            attribute_label(raw_label, self._attribute_labels)
                            if raw_label
                            else tr("hub.change_row", "Changed item {n}", n=i)
                        )
                    rows.append({
                        "label": label,
                        "before": row.get(before_key, ""),
                        "after": row.get(after_key, ""),
                    })
                if rows:
                    return rows
        return []

    def building_history(self, building_id: str, current_number: int, current_pr: dict) -> list[dict]:
        """Return past and current change proposals containing the same building ID, newest first."""
        token, _login, nwo = self._review_identity()
        found: list[dict] = []
        if building_id:
            query = urlencode({
                "q": f'repo:{nwo} is:pr "{building_id}"',
                "sort": "updated", "order": "desc", "per_page": "20",
            })
            code, result = gh_api(f"/search/issues?{query}", token)
            if code == 200 and isinstance(result, dict):
                found = [x for x in (result.get("items") or []) if isinstance(x, dict)]
        if not any(int(x.get("number") or 0) == current_number for x in found):
            found.insert(0, current_pr)

        history: list[dict] = []
        for item in found:
            number = int(item.get("number") or 0)
            kind = review_kind(item)
            if number != current_number and kind == "other":
                continue
            pull_meta = item.get("pull_request") or {}
            merged = bool(item.get("merged_at") or pull_meta.get("merged_at"))
            state = str(item.get("state") or "").lower()
            if number == current_number and state == "open":
                status = tr("hub.history_current_open", "This proposal — awaiting review")
            elif number == current_number:
                status = tr("hub.history_current", "The proposal shown now")
            elif merged:
                status = tr("hub.history_merged", "Approved and applied")
            elif state == "open":
                status = tr("hub.history_open", "Awaiting review")
            else:
                status = tr("hub.history_closed", "Closed")
            history.append({
                "number": number,
                "title": human_proposal_title(
                    item.get("title") or tr("hub.proposal_title_fallback", "Change record"),
                    self._attribute_labels
                ),
                "kind": kind,
                "status": status,
                "current": number == current_number,
                "updated": item.get("updated_at") or item.get("created_at") or "",
                "url": item.get("html_url") or "",
            })
        history.sort(key=lambda x: (not x["current"], str(x["updated"])), reverse=False)
        if history:
            current = [x for x in history if x["current"]]
            older = sorted(
                [x for x in history if not x["current"]],
                key=lambda x: str(x["updated"]), reverse=True,
            )
            history = current + older
        return history

    def review_detail(self, number: int) -> dict:
        token, login, nwo = self._review_identity()
        if number < 1:
            raise ValueError(tr("hub.err_bad_pr_number", "Invalid PR number"))

        code, pr = gh_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(str(getattr(pr, "get", lambda *_: "")("message") or f"HTTP {code}"))
        endpoints = {
            "files": f"/repos/{nwo}/pulls/{number}/files?per_page=100",
            "comments": f"/repos/{nwo}/issues/{number}/comments?per_page=100",
            "reviews": f"/repos/{nwo}/pulls/{number}/reviews?per_page=100",
            "checks": f"/repos/{nwo}/commits/{(pr.get('head') or {}).get('sha', '')}/check-runs?per_page=100",
        }
        fetched: dict = {}
        for key, endpoint in endpoints.items():
            status, data = gh_api(endpoint, token)
            fetched[key] = data if status == 200 else ([] if key != "checks" else {})

        files = fetched["files"] if isinstance(fetched["files"], list) else []
        comments = fetched["comments"] if isinstance(fetched["comments"], list) else []
        reviews = fetched["reviews"] if isinstance(fetched["reviews"], list) else []
        check_runs = (fetched["checks"].get("check_runs") or []) if isinstance(fetched["checks"], dict) else []
        kind = review_kind(pr)
        body = str(pr.get("body") or "")

        reason_source = section_by_key(
            body, "reason",
            "Reason and supporting evidence", "Summary of changes",
            "編集理由・根拠資料", "変更の概要",
        ) or body
        reason = human_reason(body, kind, self._attribute_labels)
        reason_ok = review_ready_reason(reason)
        building_id = extract_building_id(
            pr.get("title"), body, (pr.get("head") or {}).get("ref"),
            *[c.get("body") for c in comments],
        )
        model_ref = self._building_ref_from_local_files(files, comments, building_id)
        if not building_id:
            building_id = str(model_ref.get("buildingId") or "")

        data_path = re.compile(
            r"^[^/]+/udx/bldg/(?:[^/]+\.gml|[^/]+_appearance/[^/]+\.(?:jpe?g|png|tiff?))$",
            re.IGNORECASE,
        )
        unsafe_files = [f.get("filename") or "" for f in files if not data_path.match(str(f.get("filename") or ""))]
        checks = [{
            "name": check_display_name(c.get("name") or ""),
            "technicalName": c.get("name") or "",
            "status": c.get("status") or "",
            "conclusion": c.get("conclusion") or "",
            "url": c.get("details_url") or "",
        } for c in check_runs]
        checks_ok = bool(checks) and all(
            c["status"] == "completed" and c["conclusion"] in ("success", "neutral")
            for c in checks
        )
        retry = ci_retry_info(_overall_check_status(checks), comments)
        preview_url = ""
        lint_ok = None
        for comment in comments:
            cbody = str(comment.get("body") or "")
            if "<!-- citygml-reviewability-lint -->" in cbody:
                lint_ok = "✅" in cbody and "警告はありません" in cbody
            if not preview_url:
                hit = re.search(r"\]\((https://[^)\s]+citygml-viewer[^)\s]*)\)", cbody)
                if hit:
                    preview_url = hit.group(1)

        local_model_url = ""
        center = model_ref.get("center")
        if model_ref.get("tile") and model_ref.get("gid"):
            local_model_url = (
                "/review-viewer.html?" + urlencode({
                    "tile": str(model_ref["tile"]), "bid": str(model_ref["gid"]),
                })
            )
        model_url = preview_url or local_model_url
        google_maps_url = ""
        google_maps_embed_url = ""
        if isinstance(center, list) and len(center) == 2:
            coordinates = f"{center[0]},{center[1]}"
            google_maps_url = "https://www.google.com/maps/@?" + urlencode({
                "api": "1", "map_action": "map", "center": coordinates,
                "zoom": "20", "basemap": "satellite",
            })
            # For location checks inside the walkthrough screen. Environments that cannot open it can use the official Maps URL above.
            google_maps_embed_url = "https://maps.google.com/maps?" + urlencode({
                "q": coordinates, "z": "20", "t": "k", "output": "embed",
            })

        change_rows = self._change_rows(body, comments, kind)
        if kind == "texture":
            gml_file = next(
                (str(f.get("filename") or "") for f in files
                 if str(f.get("filename") or "").lower().endswith(".gml")),
                "",
            )
            bldg_dir = gml_file.rsplit("/", 1)[0] if "/" in gml_file else ""
            for row in change_rows:
                for key, side in (("before", "base"), ("after", "head")):
                    rel = str(row.get(key) or "")
                    if bldg_dir and rel and not rel.startswith("（"):
                        full_path = f"{bldg_dir}/{rel}"
                        row[key + "Asset"] = (
                            f"/api/reviews/{number}/asset?"
                            + urlencode({"side": side, "path": full_path})
                        )

        permission = self.reviewer_permission()
        author = ((pr.get("user") or {}).get("login") or "")
        self_authored = author == login
        freshness_feedback = any(
            "<!-- citygml-base-freshness -->" in str(c.get("body") or "")
            and "<!-- status:active -->" in str(c.get("body") or "")
            for c in comments
        )
        auto_resubmit = any(
            "<!-- citygml-auto-resubmission -->" in str(c.get("body") or "")
            and "<!-- status:active -->" in str(c.get("body") or "")
            for c in comments
        ) or freshness_feedback
        manual_changes_requested = any(
            r.get("state") == "CHANGES_REQUESTED"
            and str(r.get("commit_id") or "") == str((pr.get("head") or {}).get("sha") or "")
            for r in reviews
        )
        changes_requested = (
            manual_changes_requested or auto_resubmit
        )
        checkpoints = review_checkpoints(
            reason_ok=reason_ok,
            unsafe_files=unsafe_files,
            checks=checks,
            model_available=bool(model_url),
            kind=kind,
            comments=comments,
            changes_requested=changes_requested,
            adjustment_owner="reviewer" if manual_changes_requested else "ci",
        )
        inspection_ready = all(c["status"] in ("pass", "na") for c in checkpoints)
        failed_inspections = [c["label"] for c in checkpoints if c["status"] == "fail"]
        blockers = []
        if str(pr.get("state") or "").lower() != "open":
            blockers.append(tr("hub.blocker_not_open", "This proposal is not open"))
        if pr.get("draft"):
            blockers.append(tr("hub.blocker_draft", "This proposal is a draft"))
        if not checks_ok:
            blockers.append(tr(
                "hub.blocker_checks",
                "Approval is not possible until the automated checks complete and all succeed"))
        if unsafe_files:
            blockers.append(tr(
                "hub.blocker_unsafe_files",
                "Changes to files other than building data are included"))
        if not reason_ok:
            blockers.append(tr(
                "hub.blocker_no_reason",
                "The reason for the change and supporting documents are not filled in"))
        if not model_url:
            blockers.append(tr(
                "hub.blocker_no_model", "The building's 3D model cannot be displayed"))
        if manual_changes_requested:
            blockers.append(tr(
                "hub.blocker_reviewer_wait",
                "Waiting for a response to the reviewer's comment"))
        # Do not repeat this when blockers for inspections, reason, or the 3D model are already shown
        if failed_inspections and checks_ok and reason_ok and model_url:
            blockers.append(tr(
                "hub.blocker_failed_inspections",
                "There are inspection items that have not passed ({items})",
                items=tr("hub.list_separator", ", ").join(failed_inspections)))
        if self_authored:
            blockers.append(tr(
                "hub.blocker_self", "You cannot approve a proposal you submitted yourself"))
        if not permission["canReview"]:
            blockers.append(tr(
                "hub.blocker_permission",
                "Sign in with an account that has approval permission"))

        return {
            "ok": True,
            "number": number,
            "buildingId": building_id,
            "title": human_proposal_title(pr.get("title") or "", self._attribute_labels),
            "technicalTitle": pr.get("title") or "",
            "body": body,
            "kind": kind,
            "author": author,
            "state": pr.get("state") or "",
            "draft": bool(pr.get("draft")),
            "url": pr.get("html_url") or "",
            "headSha": (pr.get("head") or {}).get("sha") or "",
            "updated": pr.get("updated_at") or "",
            "reason": reason,
            "reasonOk": reason_ok,
            "evidenceLinks": evidence_links(reason_source),
            "history": self.building_history(building_id, number, pr),
            "changes": change_rows,
            "files": [{
                "path": f.get("filename") or "",
                "status": f.get("status") or "",
                "additions": f.get("additions") or 0,
                "deletions": f.get("deletions") or 0,
                "patch": f.get("patch") or "",
            } for f in files],
            "unsafeFiles": unsafe_files,
            "checks": checks,
            "checksOk": checks_ok,
            "retry": retry,
            "checkpoints": checkpoints,
            "allGreen": inspection_ready,
            "inspectionReady": inspection_ready,
            "autoResubmit": auto_resubmit,
            "reviewerFeedback": manual_changes_requested,
            "changesRequested": changes_requested,
            "lintOk": lint_ok,
            "previewUrl": preview_url,
            "modelUrl": model_url,
            "modelIsComparison": bool(preview_url),
            "center": center,
            "googleMapsUrl": google_maps_url,
            "googleMapsEmbedUrl": google_maps_embed_url,
            "alreadyApproved": any(r.get("state") == "APPROVED" for r in reviews),
            "permission": permission["permission"],
            "selfAuthored": self_authored,
            "blockers": blockers,
            "canApprove": not blockers,
            # The walkthrough does not modify the real PR; the operation is experienced in the same screen.
            "canDemoApprove": inspection_ready,
        }

    def review_asset(self, number: int, side: str, path: str) -> tuple[bytes, str]:
        """Return the before/after images of a texture PR via the authenticated API."""
        if side not in ("base", "head"):
            raise ValueError(tr("hub.err_bad_side", "Invalid image comparison side"))
        asset_path = re.compile(
            r"^[^/]+/udx/bldg/[^/]+_appearance/[^/]+\.(?:jpe?g|png|tiff?)$",
            re.IGNORECASE,
        )
        if not asset_path.match(path) or ".." in Path(path).parts:
            raise ValueError(tr("hub.err_not_texture", "Only texture images can be fetched"))
        token, _login, nwo = self._review_identity()
        code, pr = gh_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(str(getattr(pr, "get", lambda *_: "")("message") or f"HTTP {code}"))
        ref = str((pr.get(side) or {}).get("sha") or (pr.get(side) or {}).get("ref") or "")
        if not ref:
            raise RuntimeError(tr("hub.err_no_image_ref", "Could not resolve the image reference"))
        endpoint = f"/repos/{nwo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"
        status, data, mime = gh_raw(endpoint, token)
        if status != 200:
            raise FileNotFoundError(tr(
                "hub.err_image_fetch", "Could not fetch the image (HTTP {status})",
                status=status))
        if not mime.startswith("image/"):
            suffix = Path(path).suffix.lower()
            mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".tif": "image/tiff", ".tiff": "image/tiff",
            }.get(suffix, "application/octet-stream")
        return data, mime

    def submit_review(self, number: int, *, demo: bool = False) -> dict:
        detail = self.review_detail(number)
        if demo:
            if not detail["canDemoApprove"]:
                raise RuntimeError(tr(
                    "hub.err_demo_not_ready",
                    "This proposal is not in a state that can be approved, "
                    "even in the walkthrough"))
            return {
                "ok": True,
                "demo": True,
                "number": number,
                "reviewer": self._review_identity()[1],
            }
        if not detail["canApprove"]:
            raise RuntimeError(tr(
                "hub.err_cannot_approve", "Cannot approve: {blockers}",
                blockers=tr("hub.blocker_separator", "; ").join(detail["blockers"])))
        token, login, nwo = self._review_identity()
        code, data = gh_api(
            f"/repos/{nwo}/pulls/{number}/reviews",
            token,
            method="POST",
            payload={
                "commit_id": detail["headSha"],
                "body": "Confirmed the changes, supporting evidence, and "
                        "automated inspection results in the review screen.",
                "event": "APPROVE",
            },
        )
        if code != 200:
            raise RuntimeError(str(data.get("message") or f"HTTP {code}"))
        return {"ok": True, "demo": False, "number": number, "reviewer": login}

    def submit_review_feedback(
        self, number: int, message: str, *, demo: bool = False
    ) -> dict:
        """Record the approver's confirmation comment as a change-request review on the current version."""
        message = str(message or "").strip()
        if len(message) < 5:
            raise ValueError(tr(
                "hub.err_feedback_short",
                "Enter what you want confirmed in at least 5 characters"))
        if len(message) > 1000:
            raise ValueError(tr(
                "hub.err_feedback_long", "Enter the comment in at most 1000 characters"))
        detail = self.review_detail(number)
        token, login, nwo = self._review_identity()
        if demo:
            return {
                "ok": True, "demo": True, "number": number,
                "reviewer": login, "message": message,
            }
        if str(detail.get("state") or "").lower() != "open":
            raise RuntimeError(tr(
                "hub.err_closed_comment", "You cannot comment on a closed proposal"))
        if detail.get("selfAuthored"):
            raise RuntimeError(tr(
                "hub.err_self_request",
                "You cannot request changes on a proposal you submitted yourself"))
        if detail.get("permission") not in ("admin", "maintain", "push", "write"):
            raise RuntimeError(tr(
                "hub.blocker_permission",
                "Sign in with an account that has approval permission"))
        code, data = gh_api(
            f"/repos/{nwo}/pulls/{number}/reviews",
            token,
            method="POST",
            payload={
                "commit_id": detail["headSha"],
                "body": "## 💬 Confirmation from the reviewer\n\n" + message,
                "event": "REQUEST_CHANGES",
            },
        )
        if code != 200:
            raise RuntimeError(str(data.get("message") or f"HTTP {code}"))
        return {
            "ok": True, "demo": False, "number": number,
            "reviewer": login, "message": message,
        }

    def request_ci_retry(self, number: int, *, demo: bool = False) -> dict:
        """Safely request a re-run, via a PR comment, for temporary inspection failures only."""
        token, login, nwo = self._review_identity()
        code, pr = gh_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(str(getattr(pr, "get", lambda *_: "")("message") or f"HTTP {code}"))
        if str(pr.get("state") or "").lower() != "open":
            raise RuntimeError(tr(
                "hub.err_closed_retry", "A closed proposal cannot be re-checked"))

        author = str((pr.get("user") or {}).get("login") or "")
        if login != author:
            permission = self.reviewer_permission().get("permission")
            if permission not in ("admin", "maintain", "push", "write"):
                raise RuntimeError(tr(
                    "hub.err_retry_permission",
                    "Only the proposer or a maintainer can request a re-run"))

        head_sha = str((pr.get("head") or {}).get("sha") or "")
        c_code, c_data = gh_api(
            f"/repos/{nwo}/issues/{number}/comments?per_page=100", token
        )
        comments = c_data if c_code == 200 and isinstance(c_data, list) else []
        r_code, r_data = gh_api(
            f"/repos/{nwo}/commits/{head_sha}/check-runs?per_page=100", token
        )
        check_runs = (
            r_data.get("check_runs") or []
            if r_code == 200 and isinstance(r_data, dict) else []
        )
        retry = ci_retry_info(_overall_check_status(check_runs), comments)
        if not retry["available"]:
            raise RuntimeError(retry["reason"])
        if demo:
            return {"ok": True, "demo": True, "number": number, "retry": retry}

        marker = "<!-- citygml-ci-retry-request -->"
        comment = (
            f"{marker}\n"
            "## 🔄 Re-run automated inspection\n\n"
            f"Received a re-inspection request from @{login} for the current"
            f" version (`{head_sha[:12]}`).\n"
            "The data is unchanged; only the inspection runs again."
        )
        post_code, data = gh_api(
            f"/repos/{nwo}/issues/{number}/comments",
            token,
            method="POST",
            payload={"body": comment},
        )
        if post_code != 201:
            raise RuntimeError(str(data.get("message") or f"HTTP {post_code}"))
        with self._cache_lock:
            self._contrib_cache = None
        return {
            "ok": True, "demo": False, "number": number,
            "message": tr("hub.retry_accepted_msg",
                          "The re-run request was accepted. It usually starts "
                          "within a few minutes"),
        }

    def feedback_url(self) -> "str | None":
        nwo = self.nwo()
        if not nwo:
            return None
        tmpl = self.root / ".github" / "ISSUE_TEMPLATE" / "ux_feedback.yml"
        if tmpl.is_file():
            return f"https://github.com/{nwo}/issues/new?template=ux_feedback.yml"
        return f"https://github.com/{nwo}/issues/new"

    def feedback_defaults(self) -> dict:
        """Return the defaults and submitter info for the in-hub feedback form."""
        token = AUTH.token()
        login = ((AUTH.user() or {}) if token else {}).get("login") or ""
        contributions = self.contributions()
        badge = contributions.get("badge") if contributions.get("ok") else None
        categories = feedback_categories()
        return {
            "ok": True,
            "connected": bool(token and login),
            "login": login,
            "categories": list(categories),
            "title": tr("hub.fb_default_title", "[UX] Problem or suggestion"),
            "category": categories[0],
            "goal": tr("hub.fb_default_goal",
                       "I was trying to view, edit, or propose building data "
                       "with the building data editing tools."),
            "context": tr("hub.fb_default_context", "Hub / dashboard"),
            "badge": badge,
            "merged": contributions.get("merged") if contributions.get("ok") else None,
        }

    def submit_feedback(self, fields: dict) -> dict:
        """Create a UX feedback Issue on the upstream repository with the hub's credentials."""
        token, login, nwo = self._review_identity()

        def value(key: str, label: str, *, minimum: int = 0, maximum: int) -> str:
            text = str(fields.get(key) or "").strip()
            if len(text) < minimum:
                raise ValueError(tr(
                    "hub.err_field_min", "Enter {label} with at least {n} characters",
                    label=label, n=minimum))
            if len(text) > maximum:
                raise ValueError(tr(
                    "hub.err_field_max", "Enter {label} with at most {n} characters",
                    label=label, n=maximum))
            return text

        title = value("title", tr("hub.fb_field_title", "the subject"), minimum=1, maximum=120)
        if not title.startswith("[UX]"):
            title = f"[UX] {title}"
        category = value("category", tr("hub.fb_field_category", "the type"),
                         minimum=1, maximum=80)
        if category not in feedback_categories():
            raise ValueError(tr(
                "hub.err_category_choice",
                "Choose the type of problem or suggestion again"))
        goal = value("goal", tr("hub.fb_field_goal", "what you were trying to do"),
                     minimum=1, maximum=2000)
        problem = value("problem", tr("hub.fb_field_problem", "the problem or suggestion"),
                        minimum=1, maximum=4000)
        expected = value("expected", tr("hub.fb_field_expected", "the improvement idea"),
                         maximum=3000)
        context = value("context", tr("hub.fb_field_context", "the environment"), maximum=1000)
        building = value("building", tr("hub.fb_field_building", "the target building / mesh"),
                         maximum=500)
        additional = value("additional", tr("hub.fb_field_additional", "the additional notes"),
                           maximum=3000)

        contributions = self.contributions()
        if contributions.get("ok"):
            badge = contributions.get("badge") or badge_for(0)
            badge_text = f"{badge['emoji']} {badge['name']}"
            merged_text = str(contributions.get("merged", 0))
        else:
            badge_text = "unavailable"
            merged_text = "unavailable"

        body = "\n".join([
            "## Type of suggestion or problem", category, "",
            "## What were you trying to do", goal, "",
            "## Where did you have trouble / what do you suggest", problem, "",
            "## What would make it easier to use", expected or "(not filled in)", "",
            "## Environment", context or "(not filled in)", "",
            "## Target building / mesh", building or "(not filled in)", "",
            "## Additional notes", additional or "(not filled in)", "",
            "## Submission info (filled in by the hub)",
            f"- Author: @{login}",
            f"- Achievement badge: {badge_text}",
            f"- Merged PRs: {merged_text}",
            "- Sent from: the hub's feedback form",
            "",
            "<sub>The achievement badge is background information about contribution"
            " experience; it does not decide issue priority.</sub>",
        ])
        code, data = gh_api(
            f"/repos/{nwo}/issues", token, method="POST",
            payload={"title": title, "body": body},
        )
        if code != 201:
            raise RuntimeError(str(data.get("message") or f"HTTP {code}"))
        with self._cache_lock:
            self._contrib_cache = None
        return {
            "ok": True,
            "number": data.get("number"),
            "title": data.get("title") or title,
            "url": data.get("html_url") or "",
            "repository": nwo,
        }


# --------------------------------------------------------------------------
# Initial setup (GUI shown when there is no clone; same approach as attr_editor)
# --------------------------------------------------------------------------
class SetupManager:
    """Run git clone in the background and report progress to the polling API."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: "str | None" = None
        self.dest: "str | None" = None
        self.lines: list = []

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
                raise RuntimeError(tr("hub.err_clone_running", "A clone is already running"))
            if git_cmd()[0] is None:
                raise RuntimeError(tr(
                    "hub.err_git_missing_setup",
                    "git was not found. Prepare git by following the setup guide"))
            url = url.strip()
            if not re.match(r"^(https://|git@|file://|/)", url):
                raise ValueError(tr(
                    "hub.err_bad_repo_url", "The repository URL format is invalid"))
            dest_path = Path(dest).expanduser()
            if dest_path.exists() and any(dest_path.iterdir()):
                raise ValueError(tr(
                    "hub.err_dest_not_empty", "The destination is not empty: {dest}",
                    dest=dest_path))
            self.running, self.done, self.error = True, False, None
            self.dest = str(dest_path)
            self.lines = [
                tr("hub.clone_start", "Clone started: {url}", url=url),
                tr("hub.clone_size_note",
                   "(The data is several GB, so this takes minutes to tens of minutes)"),
            ]
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
                seg = raw.rstrip("\r\n").split("\r")[-1].strip()
                if seg:
                    with self.lock:
                        if self.lines and self.lines[-1].split(":")[0] == seg.split(":")[0]:
                            self.lines[-1] = seg
                        else:
                            self.lines.append(seg)
            code = proc.wait()
            with self.lock:
                self.running = False
                if code == 0:
                    self.done = True
                    self.lines.append(tr("hub.clone_done", "Clone finished"))
                else:
                    self.error = tr(
                        "hub.err_clone_failed", "git clone failed (exit {code})", code=code)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.running = False
                self.error = f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Onboarding (#59 guided support): status diagnosis of the account/login/git config/fork.
# Live diagnosis when gh is present; degrades to static guidance without it (gh is an optional dependency).
# --------------------------------------------------------------------------
def _run(args: list, timeout: int = 10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def git_identity() -> dict:
    """git user.name / user.email (empty string when unset)."""
    exe, _ = git_cmd()
    if not exe:
        return {"name": "", "email": ""}

    def cfg(key: str) -> str:
        r = _run([exe, "config", "--get", key])
        return r.stdout.strip() if r and r.returncode == 0 else ""

    return {"name": cfg("user.name"), "email": cfg("user.email")}


def git_config_set(name: str, email: str) -> "str | None":
    """Set git user.name / user.email with --global (button-driven). Returns an error message or None."""
    exe, _ = git_cmd()
    if not exe:
        return tr("hub.err_git_missing", "git was not found")
    name, email = name.strip(), email.strip()
    if not name or not email:
        return tr("hub.err_git_identity_required", "Enter both a name and an email")
    for key, val in (("user.name", name), ("user.email", email)):
        r = _run([exe, "config", "--global", key, val])
        if not r or r.returncode != 0:
            return (r.stderr.strip() if r else "") or tr(
                "hub.err_git_config_failed", "Running git config failed")
    return None


def load_preset() -> dict:
    """preset.json next to the launcher (distribution defaults such as oauthClientId)."""
    for base in BUNDLE_DIRS:
        p = base / "preset.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return {}


# --------------------------------------------------------------------------
# GitHub authentication (OAuth device flow) — #86
#
# A beginner's Mac has neither gh nor git credentials. To finish authentication
# with **just a button and an 8-digit code**, without opening a terminal, the
# device flow is implemented with the standard library.
#   1. POST /login/device/code       → get the user_code (8 digits) and the verification URL
#   2. The user enters the code in the browser and approves (the only human step)
#   3. Poll POST /login/oauth/access_token every `interval` seconds → token
# client_id is public information (the device flow needs no client_secret). Provide
# it via oauthClientId in preset.json or the CITYGML_OAUTH_CLIENT_ID environment
# variable. If unset, fall back to gh.
# --------------------------------------------------------------------------
AUTH_PATH = Path.home() / ".citygml_auth.json"
# Where the credentials git uses are stored. Embedding the token in the origin URL
# leaves it in .git/config where it can leak, so use git's standard store helper with a dedicated file (0600).
GIT_CRED_PATH = Path.home() / ".citygml_git_credentials"
OAUTH_SCOPE = "public_repo"  # Necessary and sufficient for fork/push/PR on public repos (revert to "repo" when going private)
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


def oauth_client_id() -> str:
    return str(load_preset().get("oauthClientId") or os.environ.get("CITYGML_OAUTH_CLIENT_ID", ""))


def _post_form(url: str, fields: dict, timeout: int = 15) -> dict:
    data = urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def gh_api(path: str, token: str, method: str = "GET", payload: "dict | None" = None,
           timeout: int = 30) -> "tuple[int, dict]":
    """Call GitHub REST v3 (no gh dependency). Returns (HTTP status, JSON)."""
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "citygml-hub")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8") or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}


def gh_raw(path: str, token: str, timeout: int = 30) -> "tuple[int, bytes, str]":
    """Fetch a private image as bytes from the GitHub Contents API."""
    req = urllib.request.Request("https://api.github.com" + path, method="GET")
    req.add_header("Accept", "application/vnd.github.raw")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "citygml-hub")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        return exc.code, b"", "application/octet-stream"


def save_token(token: str) -> None:
    try:
        AUTH_PATH.write_text(json.dumps({"token": token}), encoding="utf-8")
        os.chmod(AUTH_PATH, 0o600)
    except OSError:
        pass


def load_token() -> str:
    """Look for a saved token first, then gh's token (empty string if neither exists)."""
    try:
        tok = json.loads(AUTH_PATH.read_text(encoding="utf-8")).get("token", "")
        if tok:
            return str(tok)
    except (OSError, ValueError):
        pass
    # CITYGML_HUB_NO_GH=1 disables reusing gh (to check the same "Connect" screen beginners see)
    if shutil.which("gh") and os.environ.get("CITYGML_HUB_NO_GH") != "1":
        r = _run(["gh", "auth", "token"])
        if r and r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""


def github_user(token: str) -> "dict | None":
    if not token:
        return None
    code, user = gh_api("/user", token)
    return user if code == 200 and user.get("login") else None


def write_git_credentials(token: str) -> None:
    """Write the credential store for the bundled Git.

    The origin URL can stay plain, so the token is not visible during screen
    sharing or in `git remote -v`. When the existing Git is chosen, the user's
    credential helpers are not changed.
    """
    exe, bundled = git_cmd()
    if not exe or not bundled:
        return
    try:
        GIT_CRED_PATH.write_text(f"https://x-access-token:{token}@github.com\n", encoding="utf-8")
        os.chmod(GIT_CRED_PATH, 0o600)
    except OSError:
        return


def apply_git_identity(user: dict) -> None:
    """Auto-configure the git name/email from the GitHub info (only when unset).

    This spares the user from typing. For accounts without a public email, build
    GitHub's noreply address (an official address that is fine to use in commits).
    """
    cur = git_identity()
    if cur["name"] and cur["email"]:
        return
    login = user.get("login") or ""
    name = cur["name"] or user.get("name") or login
    email = cur["email"] or user.get("email") or f"{user.get('id')}+{login}@users.noreply.github.com"
    if name and email:
        git_config_set(name, email)


class AuthManager:
    """Progress state of the device flow (one session per machine)."""

    # The screen polls the state every 2 seconds, so queries to GitHub are throttled
    # with a TTL (avoiding API rate limits and a sluggish screen).
    USER_TTL = 20

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.user_code = ""
        self.verify_url = ""
        self.waiting = False
        self.error: "str | None" = None
        self._token = ""
        self._reused_auth = False
        self._user: "dict | None" = None
        self._user_at = 0.0

    def user(self) -> "dict | None":
        """Authenticated user (TTL cache). Also fixes up the git config on first fetch."""
        with self.lock:
            if self._user_at > time.time() - self.USER_TTL:
                return self._user
        token = self.token()
        user = github_user(token) if token else None
        if user:
            apply_git_identity(user)
            write_git_credentials(token)
        with self.lock:
            self._user, self._user_at = user, time.time()
        return user

    def state(self) -> dict:
        user = self.user()
        with self.lock:
            return {
                "clientId": bool(oauth_client_id()),
                "waiting": self.waiting,
                "userCode": self.user_code,
                "verifyUrl": self.verify_url,
                "error": self.error,
                "login": (user or {}).get("login"),
                # Returned to explain that the 8-digit code screen was skipped by reusing a saved connection.
                "reusedAuth": bool(user and self._reused_auth),
            }

    def token(self) -> str:
        with self.lock:
            if self._token:
                return self._token
        token = load_token()
        if token:
            with self.lock:
                # If _poll put in a new token concurrently, prefer that one.
                if self._token:
                    return self._token
                self._reused_auth = True
        return token

    def forget_user(self) -> None:
        with self.lock:
            self._user_at = 0.0

    def start(self) -> dict:
        cid = oauth_client_id()
        if not cid:
            raise RuntimeError(tr(
                "hub.err_no_client_id",
                "This tool's GitHub connection setting (client_id) is not configured"))
        with self.lock:
            if self.waiting:
                return {"userCode": self.user_code, "verifyUrl": self.verify_url}
            r = _post_form(DEVICE_CODE_URL, {"client_id": cid, "scope": OAUTH_SCOPE})
            if "device_code" not in r:
                raise RuntimeError(r.get("error_description") or tr(
                    "hub.err_device_start", "Could not start connecting to GitHub"))
            self.user_code = r["user_code"]
            self.verify_url = r.get("verification_uri", "https://github.com/login/device")
            self.waiting, self.error = True, None
            args = (cid, r["device_code"], int(r.get("interval", 5)), int(r.get("expires_in", 900)))
        threading.Thread(target=self._poll, args=args, daemon=True).start()
        return {"userCode": self.user_code, "verifyUrl": self.verify_url}

    def _poll(self, cid: str, device_code: str, interval: int, expires_in: int) -> None:
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                r = _post_form(ACCESS_TOKEN_URL, {
                    "client_id": cid, "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                })
            except OSError:
                continue  # keep waiting through temporary network errors
            if r.get("access_token"):
                token = r["access_token"]
                save_token(token)
                write_git_credentials(token)
                user = github_user(token)
                if user:
                    apply_git_identity(user)
                with self.lock:
                    self._token, self.waiting, self.user_code = token, False, ""
                    self._reused_auth = False
                    self._user, self._user_at = user, time.time()
                return
            err = r.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = int(r.get("interval", interval + 5))
                continue
            with self.lock:
                self.waiting = False
                self.error = {
                    "expired_token": tr("hub.err_auth_expired",
                                        "The time limit expired. Please try again."),
                    "access_denied": tr("hub.err_auth_denied",
                                        "The authorization was canceled. Please try again."),
                }.get(err, r.get("error_description") or tr(
                    "hub.err_auth_failed", "Could not connect"))
            return
        with self.lock:
            self.waiting = False
            self.error = tr("hub.err_auth_expired", "The time limit expired. Please try again.")


# One authentication state per process (shared by the setup screen and the dashboard).
AUTH = AuthManager()




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


def upstream_url(root=None) -> str:
    """URL of the target city repository. Priority: CITYGML_UPSTREAM > the clone's
    4dcitygml.json > the git remote `upstream` > default (demo city). The city is
    decided automatically — via the environment variable for the install script,
    via 4dcitygml.json for users with a clone (project plan §5.1b)."""
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
    return upstream_url(root).rstrip("/").split("github.com/")[-1]  # e.g. 4dcitygml/sample-tokyo-station


def upstream_access(token: str) -> bool:
    """Whether this token can reach upstream (the source data). Always 200 for a public repo."""
    code, _ = gh_api(f"/repos/{upstream_nwo()}", token)
    return code == 200


def find_fork(token: str, login: str) -> "str | None":
    """The upstream fork owned by login (owner/repo), or None if absent."""
    repo = upstream_nwo().split("/")[-1]
    code, data = gh_api(f"/repos/{login}/{repo}", token)
    if code == 200 and data.get("fork"):
        return data.get("full_name")
    return None


def create_fork(token: str) -> "tuple[str | None, str | None]":
    """Create a fork of upstream (or return the existing one). Returns (owner/repo, error message).

    Fork creation is asynchronous, so wait briefly and confirm the fork materialized.
    """
    user = github_user(token)
    if not user:
        return None, tr("hub.err_connect_first", "Connect to GitHub first")
    login = user["login"]
    existing = find_fork(token, login)
    if existing:
        return existing, None
    code, data = gh_api(f"/repos/{upstream_nwo()}/forks", token, method="POST", payload={})
    if code not in (200, 201, 202):
        msg = data.get("message") or f"HTTP {code}"
        if code == 404:
            msg = tr("hub.err_upstream_unreachable",
                     "The source data repository was not found on GitHub. It may "
                     "not be published yet — contact the data maintainer")
        return None, tr("hub.err_fork_failed", "Could not create the copy: {reason}", reason=msg)
    for _ in range(20):  # wait up to 40 seconds for the fork to materialize
        time.sleep(2)
        nwo = find_fork(token, login)
        if nwo:
            return nwo, None
    return data.get("full_name"), None


# --------------------------------------------------------------------------
# Setup screen (#86 redesign from scratch)
#
# The principle is the same "one screen, one action" as the entry page (getting-started.html).
# In addition, there are **zero input fields**: neither the fork URL, name, email, nor
# save location is asked (all are information the tool determines).
# The only thing the user types is the 8-digit number entered on GitHub's browser page.
#   1 Connect (device flow) → 2 Create your own copy → 3 Import the data → done
# Approval and clone completion are auto-detected by polling and the screen advances
# (the user is never made to press "Next").
# To avoid JS/CSS braces, values are filled by %%TOKEN%% replacement, not .format.
# --------------------------------------------------------------------------
SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title data-i18n="hub.setup_doc_title">Initial setup — Building Data Editing Tools</title>
<style>
  :root {
    color-scheme: light dark;
    --bg:#eef1f5; --card:#fff; --border:#dde1e6; --text:#1c2733; --muted:#6b7785;
    --ok:#1f883d; --okbg:#eaf6ec; --okborder:#a9dfbb; --link:#0969da; --figbg:#f6f8fa; --err:#b32020;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#e6edf3; --muted:#9aa5b1;
      --ok:#3fb950; --okbg:#12261a; --okborder:#2ea04326; --link:#58a6ff; --figbg:#0d1117; --err:#ff7b72; }
  }
  * { box-sizing:border-box; }
  html { height:100%; }
  body { margin:0; min-height:100%; background:var(--bg); color:var(--text); font-size:16px;
    font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;
    display:flex; align-items:flex-start; justify-content:center; padding:12px 16px; }
  main { background:var(--card); border:1px solid var(--border); border-radius:16px;
    width:100%; max-width:600px; padding:24px 30px 20px; margin:auto 0; }
  .prog { display:flex; align-items:center; gap:8px; margin-bottom:14px; min-height:8px; }
  .prog i { flex:1; height:6px; border-radius:3px; background:var(--border); }
  .prog i.on { background:var(--ok); }
  .prog span { font-size:13px; color:var(--muted); margin-left:4px; white-space:nowrap; }
  h1 { font-size:23px; line-height:1.45; margin:0 0 8px; }
  p.lead { font-size:15.5px; line-height:1.75; color:var(--muted); margin:0 0 4px; }
  /* 8-digit code */
  .code { font-family:ui-monospace,Menlo,monospace; font-size:38px; font-weight:700;
    letter-spacing:.14em; text-align:center; background:var(--figbg); border:1px solid var(--border);
    border-radius:12px; padding:16px 10px; margin:14px 0 10px; user-select:all; }
  .safe { background:var(--okbg); border:1px solid var(--okborder); border-radius:10px;
    padding:11px 14px; font-size:14.5px; line-height:1.7; }
  .safe b { color:var(--ok); }
  .safe + .safe, .log + .safe { margin-top:10px; }
  .steps { margin:14px 0 12px; padding:10px 12px 10px 38px; background:var(--figbg);
    border:1px solid var(--border); border-radius:10px; font-size:14.5px; line-height:1.7; }
  .steps li + li { margin-top:6px; }
  .err { color:var(--err); font-size:14px; line-height:1.7; margin-top:10px; }
  /* progress log */
  .log { background:var(--figbg); border:1px solid var(--border); border-radius:10px;
    padding:10px 12px; font-size:12.5px; line-height:1.6; color:var(--muted);
    font-family:ui-monospace,Menlo,monospace; height:104px; overflow:hidden; margin-top:12px; }
  .bar { height:8px; background:var(--border); border-radius:5px; overflow:hidden; margin-top:12px; }
  .bar > i { display:block; height:100%; background:var(--ok); width:30%;
    animation:slide 1.6s ease-in-out infinite; }
  @keyframes slide { 0%{margin-left:-30%} 100%{margin-left:100%} }
  details { margin-top:14px; font-size:14px; line-height:1.8; color:var(--muted); }
  details summary { cursor:pointer; color:var(--link); font-size:14px; }
  details .in { padding:10px 2px 0; }
  input { font:inherit; font-size:14px; padding:8px 10px; border:1px solid var(--border);
    border-radius:8px; width:100%; margin-top:6px; background:var(--card); color:var(--text); }
  code { background:var(--figbg); border:1px solid var(--border); padding:1px 6px;
    border-radius:4px; font-size:13px; }
  .nav { display:flex; align-items:center; gap:12px; margin-top:18px; }
  button { font:inherit; font-weight:700; cursor:pointer; border-radius:10px; border:none;
    background:var(--ok); color:#fff; padding:11px 24px; font-size:16px; }
  button:disabled { background:#b7bdc4; cursor:default; }
  button.ghost { background:transparent; color:var(--muted); font-weight:500;
    border:1px solid var(--border); padding:10px 16px; font-size:14px; }
  .hint { font-size:13px; color:var(--muted); margin-left:auto; text-align:right; }
  a { color:var(--link); }
  .spin { display:inline-block; width:14px; height:14px; border:2px solid var(--border);
    border-top-color:var(--ok); border-radius:50%; animation:spin .8s linear infinite;
    vertical-align:-2px; margin-right:6px; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style></head><body><main>
  <div class="prog" id="prog"></div>
  <div id="screen"></div>
  <div class="nav" id="nav"></div>
<script>
'use strict';
const $ = id => document.getElementById(id);
// Fallback so the English originals work even without an injected language pack (no i18n module)
const t = window.t || ((k, d, p) => {
  let s = d;
  if (p) for (const key in p) s = s.split('{' + key + '}').join(p[key]);
  return s;
});
const UPSTREAM = "%%UPSTREAM%%";
const DEFAULT_DEST = "%%DEST%%";
const STEPS = [t('hub.setup_step_connect', 'Connect'),
  t('hub.setup_step_fork', 'Create a copy'),
  t('hub.setup_step_clone', 'Import')];

let st = null;        // state coming from the server
let busy = false;     // a button action is running
let dest = DEFAULT_DEST;
let returnTabTitle = false; // do not change the return-tab title even while the GitHub tab is being used
let pollError = '';

function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function api(path, body) {
  const r = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)});
  return r.json();
}

// Which screen we are on (0=connect / 1=copy / 2=import / 3=done)
function stepOf(s) {
  if (!s) return 0;
  if (s.active || (s.clone && s.clone.done)) return 3;
  if (!s.login) return 0;
  if (!s.fork) return 1;
  return 2;
}

function renderProg(i) {
  $('prog').innerHTML = i >= 3 ? '' :
    STEPS.map((_, k) => `<i class="${k <= i ? 'on' : ''}"></i>`).join('') +
    `<span>${i + 1} / ${STEPS.length}</span>`;
}

// ---- Screen 1: connect to GitHub ----
function screenConnect(s) {
  if (!s.clientId) {
    return {
      html: `<h1>${t('hub.setup_noclient_title', 'Connect to GitHub')}</h1>
        <p class="lead">${t('hub.setup_noclient_lead',
          "This tool's GitHub connection settings are not configured yet. Please contact the distributor.")}</p>
        <details open><summary>${t('hub.setup_noclient_diy_summary', 'If you configure it yourself')}</summary><div class="in">
          ${t('hub.setup_noclient_diy_body',
            'Place <code>preset.json</code> next to the launcher file (in the Program folder) and write <code>{"oauthClientId": "…"}</code>. On a machine already signed in with the GitHub CLI (<code>gh</code>), you can continue as is.')}
        </div></details>`,
      nav: `<button onclick="recheck()">${t('hub.setup_recheck', 'Check again')}</button>`,
    };
  }
  if (s.waiting && s.userCode) {
    return {
      html: `<h1>${t('hub.setup_device_title', 'Connect to GitHub in another tab')}</h1>
        <p class="lead">${t('hub.setup_device_lead',
          'First check the number below. Pressing the next button opens <b>a new GitHub tab</b>.')}</p>
        <div class="code" id="uc">${esc(s.userCode)}</div>
        <ol class="steps">
          <li>${t('hub.setup_device_step1', 'Enter the number on GitHub and press <b>Continue</b>.')}</li>
          <li>${t('hub.setup_device_step2', 'On the next screen, press <b>Authorize</b>.')}</li>
          <li>${t('hub.setup_device_step3',
            'When finished, <b>close the GitHub tab</b>. Return to the "← Return here" tab on the left.')}</li>
        </ol>
        <div class="safe"><span class="spin"></span>${t('hub.setup_device_return',
          '<b>This screen is where you return.</b> When you finish on GitHub, it advances automatically.')}</div>
        ${s.error ? `<div class="err">${esc(s.error)}</div>` : ''}`,
      nav: `<button onclick="openVerification()">${t('hub.setup_btn_open_github', 'Copy the number and open GitHub')}</button>
        <button class="ghost" onclick="copyCode()">${t('hub.setup_btn_copy_code', 'Copy the number only')}</button>`,
    };
  }
  return {
    html: `<h1>${t('hub.setup_connect_title', 'Connect this computer to GitHub')}</h1>
      <p class="lead">${t('hub.setup_connect_lead',
        'To fix and propose building data, you need a GitHub account. Pressing the button below first shows an <b>8-digit number</b>. Read the next screen, then open GitHub in another tab.')}</p>
      <div class="safe">${t('hub.setup_connect_scope',
        '<b>What you will authorize:</b> this tool asks GitHub for the <b>public_repo</b> permission — read and write access to the <b>public</b> repositories of your account. It is used only to create your own copy of the city data, upload your edits, and open change proposals (pull requests); private repositories are not covered. The connection key is stored only on this computer (in a file readable by your account alone) and you can revoke it anytime under GitHub → Settings → Applications.')}</div>
      <div class="safe">${t('hub.setup_connect_note',
        'If you do not have an account, you can <b>create one for free</b> from the page that opens (just an email address and a password). After finishing on GitHub, <b>return to this "Initial setup" tab</b>.')}</div>
      ${s.error ? `<div class="err">${esc(s.error)}</div>` : ''}`,
    nav: `<button id="go" onclick="doConnect()">${t('hub.setup_step_connect', 'Connect')}</button>`,
  };
}

// ---- Screen 2: create your own copy ----
function screenFork(s) {
  const reusedNotice = s.reusedAuth
    ? `<div class="safe">${t('hub.setup_reused_notice',
        '<b>Your previous GitHub connection was carried over.</b><br> The connection is saved on this computer, so the 8-digit code screen was skipped and setup advanced to here automatically. This is not a malfunction or a mistake.')}</div>`
    : '';
  const authNotice = `<div class="safe">${t('hub.setup_auth_notice',
    '<b>The GitHub connection is complete.</b><br> An email with the subject "[GitHub] A third-party OAuth application has been added to your account" is a normal notification of this connection. It is unrelated to any Mac prompt; no reply or action inside the email is needed.')}</div>`;
  // Guided support when invite-only and the source data is not reachable yet (#96).
  // The server auto-accepts pending invitations, so the only thing the user does is "tell the administrator their username".
  if (s.access === false) {
    return {
      html: `<h1>${t('hub.setup_noaccess_title', 'The source data cannot be reached')}</h1>
        <p class="lead">${t('hub.setup_noaccess_lead',
          'The GitHub connection is done, but the data repository <b>{url}</b> could not be reached. It may not be published yet, or the network may be down. Contact the data maintainer, or try again later.',
          {url: esc(UPSTREAM)})}</p>
        ${reusedNotice}
        ${authNotice}
        ${s.error ? `<div class="err">${esc(s.error)}</div>` : ''}`,
      nav: `<button onclick="poll()">${t('hub.setup_btn_recheck', 'Check again')}</button>`,
    };
  }
  return {
    html: `<h1>${t('hub.setup_fork_title', 'Create your own copy')}</h1>
      <p class="lead">${t('hub.setup_fork_lead',
        'The source data stays as is; <b>your own working copy</b> is created on GitHub. You will use it later when proposing "I fixed this".<br> Just press the button (usually a few seconds, up to about a minute).')}</p>
      ${reusedNotice}
      ${authNotice}
      <div class="safe">${t('hub.setup_fork_connected', 'Connected to GitHub as <b>{login}</b>.', {login: esc(s.login)})}</div>
      ${s.forkError ? `<div class="err">${t('hub.setup_fork_error',
        '<b>Could not create the copy.</b><br>{error}<br> Check your internet connection and try again with the button below.',
        {error: esc(s.forkError)})}</div>` : ''}
      `,
    nav: busy
      ? `<button disabled><span class="spin"></span>${t('hub.setup_btn_forking', 'Creating (up to 1 minute)…')}</button>`
      : `<button onclick="doFork()">${s.forkError ? t('hub.setup_btn_fork_retry', 'Create the copy again') : t('hub.setup_step_fork', 'Create a copy')}</button>`,
  };
}

// ---- Screen 3: import the data ----
function screenClone(s) {
  const c = s.clone || {};
  if (c.gitAvailable === false) {
    return {
      html: `<h1>${t('hub.setup_git_missing_title', 'A component needed for the import was not found')}</h1>
        <p class="lead">${t('hub.setup_git_missing_lead',
          'Close the browser and the terminal for now, check the guide in "READ-ME-FIRST.html" on installing the component, and then open the same launcher file again.')}</p>
        <div class="err">${t('hub.setup_git_missing_win',
          'If this screen appears on the Windows edition, the distributed files are incomplete; please contact the distributor.')}</div>`,
      nav: `<span class="hint">${t('hub.setup_git_missing_hint', 'After preparing, setup resumes where you left off')}</span>`,
    };
  }
  if (c.running) {
    return {
      html: `<h1>${t('hub.setup_cloning_title', 'Importing the data')}</h1>
        <p class="lead">${t('hub.setup_cloning_lead',
          'The building data is large, so this takes <b>minutes to tens of minutes</b>. Please keep this screen open and wait.')}</p>
        <div class="bar"><i></i></div>
        <div class="log">${(c.log || []).map(esc).join('<br>')}</div>
        <div class="safe">${t('hub.setup_cloning_note',
          'The progress text may not change for several minutes. This is not abnormal. Keep your internet connection and wait <b>without closing this screen or the terminal</b>.')}</div>`,
      nav: `<span class="hint">${t('hub.setup_cloning_hint', 'When it finishes, this advances to "Ready to go"')}</span>`,
    };
  }
  const retry = Boolean(c.error);
  const lastLog = (c.log || []).length
    ? `<details><summary>${t('hub.setup_last_log_summary', 'Check the last progress')}</summary><div class="in log">${c.log.map(esc).join('<br>')}</div></details>`
    : '';
  return {
    html: `<h1>${retry ? t('hub.setup_clone_retry_title', 'Try importing the data again')
        : t('hub.setup_clone_title', 'Import the data to this computer')}</h1>
      <p class="lead">${t('hub.setup_clone_lead_head',
        'Your working copy on GitHub was confirmed (<b>{fork}</b>).', {fork: esc(s.fork)})}<br>
        ${retry
          ? t('hub.setup_clone_lead_retry', 'The previous attempt stopped partway. Next time, the tool automatically uses an empty save location.')
          : t('hub.setup_clone_lead_first', 'Finally, the data is imported to this computer. The save location is decided automatically, so there is no need to change it.')}<br>
        ${t('hub.setup_clone_takes', 'It takes <b>minutes to tens of minutes</b>.')}</p>
      ${retry ? `<div class="err">${t('hub.setup_clone_error',
        '<b>The previous import could not be completed.</b><br>{error}<br> The partial data is kept, not deleted, and the import safely restarts in an empty location.',
        {error: esc(c.error)})}</div>` : ''}
      <div class="safe">${t('hub.setup_clone_keep_open',
        'While importing, keep your internet connection and do not close the browser or the terminal.')}</div>
      ${lastLog}
      <details><summary>${t('hub.setup_change_dest_summary', 'Change the save location')}</summary><div class="in">
        <input id="dest" value="${esc(dest)}">
      </div></details>`,
    nav: `<button onclick="doClone()">${retry ? t('hub.setup_btn_clone_retry', 'Import again to an empty location') : t('hub.setup_step_clone', 'Import')}</button>`,
  };
}

// ---- Screen 4: done ----
function screenDone() {
  return {
    html: `<h1>${t('hub.setup_done_title', 'Ready to go')}</h1>
      <p class="lead">${t('hub.setup_done_lead', 'Well done. Your working copy and the building data import are complete.')}</p>
      <div class="safe">${t('hub.setup_done_next',
        '<b>On the next screen, first press "Launch" on the Attribute Editor.</b><br> The editing screen opens in a new tab. The Texture Editor is for fixing facade photos of buildings.')}</div>
      <div class="safe">${t('hub.setup_done_reuse',
        '<b>From next time, just open the same launcher file</b> to use the tools without this preparation.')}</div>`,
    nav: `<button onclick="location.href='/?welcome=1'">${t('hub.setup_btn_start', 'Start')}</button>`,
  };
}

function render() {
  const i = stepOf(st);
  const c = (st && st.clone) || {};
  if (i === 2 && c.error && st.dest && st.dest !== c.dest) dest = st.dest;
  document.title = (i === 0 && st && st.waiting) ||
      (returnTabTitle && document.visibilityState === 'hidden')
    ? t('hub.setup_return_tab_title', '← Return here — Building Data Editing Tools')
    : t('hub.setup_doc_title', 'Initial setup — Building Data Editing Tools');
  renderProg(i);
  const v = [screenConnect, screenFork, screenClone, screenDone][i](st || {});
  $('screen').innerHTML = v.html + (pollError
    ? `<div class="err">${esc(pollError)}</div>` : '');
  $('nav').innerHTML = v.nav;
}

// ---- Actions ----
async function doConnect() {
  const b = $('go'); if (b) { b.disabled = true;
    b.innerHTML = '<span class="spin"></span>' + t('hub.setup_preparing', 'Preparing…'); }
  const r = await api('/api/auth/start', {});
  if (r.ok === false) { st = {...st, error: r.error}; render(); return; }
  st = {...st, waiting: true, userCode: r.userCode, verifyUrl: r.verifyUrl, error: null};
  render();
  await poll();
}
function copyCode() {
  const t = $('uc'); if (!t) return;
  navigator.clipboard && navigator.clipboard.writeText(t.textContent.trim());
}
function openVerification() {
  copyCode();
  returnTabTitle = true;
  if (st && st.verifyUrl) window.open(st.verifyUrl, '_blank', 'noopener');
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') returnTabTitle = false;
  render();
});
async function doFork() {
  busy = true; render();
  try {
    const r = await api('/api/setup/fork', {});
    busy = false;
    st = {...(r.status || st), forkError: r.ok === false ? r.error : null};
    render();
  } catch (e) {
    busy = false;
    st = {...st, forkError: t('hub.setup_conn_lost',
      'The connection to the tool was lost. Open the same launcher file again.')};
    render();
  }
}
async function doClone() {
  const el = $('dest'); if (el && el.value.trim()) dest = el.value.trim();
  try {
    const r = await api('/api/setup/clone', {dest});
    if (r.ok === false) { st = {...st, clone: {...(st.clone || {}), error: r.error}}; render(); return; }
    await poll();
  } catch (e) {
    st = {...st, clone: {...(st.clone || {}), error: t('hub.setup_conn_lost',
      'The connection to the tool was lost. Open the same launcher file again.')}};
    render();
  }
}
async function recheck() { await poll(); }

// ---- State polling (always on; auto-detects approval and clone completion and advances the screen) ----
let timer = null;
async function poll() {
  try {
    const s = await api('/api/setup/status');
    if (s.ok === false) throw new Error(s.error || t('hub.setup_status_unknown', 'Could not check the status'));
    const before = stepOf(st);
    st = {...s, forkError: st && st.forkError};
    pollError = '';
    if (stepOf(st) !== before) busy = false;
    render();
    if (st.active) { clearTimeout(timer); timer = null; return; }
  } catch (e) {
    pollError = t('hub.setup_poll_error',
      'Checking the status is temporarily delayed. Check your internet connection and wait on this screen.');
    if (st) {
      render();
    } else {
      $('prog').innerHTML = '';
      $('screen').innerHTML = `<h1>${t('hub.setup_checking_title', 'Checking the connection status')}</h1>
        <p class="lead">${t('hub.setup_checking_lead', 'Please wait without closing the screen. It checks again automatically.')}</p>
        <div class="err">${esc(pollError)}</div>`;
      $('nav').innerHTML = `<span class="hint"><span class="spin"></span>${t('hub.setup_rechecking', 'Checking again')}</span>`;
    }
  }
  clearTimeout(timer);
  timer = setTimeout(poll, 2000);
}
poll();
</script></main></body></html>"""


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
def next_available_dest(base: Path) -> Path:
    """Return the next candidate that avoids non-empty destinations and never overwrites partial data."""
    cand = base
    n = 2
    while cand.exists() and (not cand.is_dir() or any(cand.iterdir())):
        cand = base.with_name(f"{base.name}{n}")
        n += 1
    return cand


class Handler(BaseHTTPRequestHandler):
    hub: "Hub | None" = None
    setup_mgr = SetupManager()
    auth_mgr = AUTH

    @staticmethod
    def default_dest() -> str:
        """Clone destination (decided without asking). Only steps aside with a sequence number when it already exists."""
        return str(next_available_dest(Path.home() / "Documents" / "CityGML Data"))

    _fork_cache: "tuple[float, str | None]" = (0.0, None)
    _access_cache: "tuple[float, bool]" = (0.0, False)

    @classmethod
    def upstream_ok(cls, login: "str | None") -> "bool | None":
        """Whether upstream is reachable (None when not connected).

        Called from the 2-second polling, so throttled with a 5-second TTL. Once
        reachable, never asks again (an operation where access disappears is not expected)."""
        if not login:
            return None
        at, val = cls._access_cache
        if val:
            return True
        if at > time.time() - 5:
            return False
        token = cls.auth_mgr.token()
        ok = upstream_access(token)
        cls._access_cache = (time.time(), ok)
        return ok

    @classmethod
    def fork_nwo(cls, login: "str | None", *, fresh: bool = False) -> "str | None":
        """login's fork (TTL cache; re-fetch with fresh=True right after creation)."""
        if not login:
            return None
        at, val = cls._fork_cache
        if not fresh and at > time.time() - (60 if val else 10):
            return val
        val = find_fork(cls.auth_mgr.token(), login)
        cls._fork_cache = (time.time(), val)
        return val

    @classmethod
    def setup_state(cls) -> dict:
        """Return the whole setup-screen state (auth, fork, clone) as one payload."""
        auth = cls.auth_mgr.state()
        fork = cls.fork_nwo(auth["login"])
        # If the fork already exists, upstream is reachable too (avoid extra API calls)
        access = True if fork else cls.upstream_ok(auth["login"])
        return {
            "ok": True,
            **auth,
            "fork": fork,
            "access": access,
            "clone": cls.setup_mgr.state(),
            "dest": cls.default_dest(),
            "active": cls.hub is not None,
        }

    @classmethod
    def _try_activate(cls) -> None:
        """Activate the hub after the clone finishes and remember the clone destination in the config."""
        st = cls.setup_mgr
        if cls.hub is not None or not st.done or st.dest is None:
            return
        if has_building_data(Path(st.dest)):
            cls.hub = Hub(Path(st.dest))
            save_config({"repo": st.dest})
        else:
            st.done = False
            st.error = tr("hub.err_clone_no_bldg",
                          "No building data (udx/bldg) was found in the cloned destination")

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, msg: str, status: int = 400) -> None:
        self._json({"ok": False, "error": msg}, status)

    def _file(self, name: str) -> None:
        path = RES_DIR / name
        if not path.is_file() and self.hub is not None:
            path = self.hub.root / "tools" / "hub" / name
        if not path.is_file():
            self._error("not found", 404)
            return
        data = path.read_bytes()
        if path.suffix.lower() == ".html":
            data = themed_html(data, self.hub.root if self.hub is not None else None)
            data = localized_html(data)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _city_logo(self) -> None:
        """Municipal logo (the logo in the clone's 4dcitygml.json). Resolution is fail-closed.

        Validation (relative path, raster extension, under the root, at most 1 MiB)
        is done by theme_loader's resolve_logo(); when not satisfied, 404 (the
        screen side stays hidden via onerror).
        """
        mod = theme_module()
        got = None
        if mod is not None and self.hub is not None and hasattr(mod, "resolve_logo"):
            try:
                got = mod.resolve_logo(self.hub.root)
            except Exception:  # Any resolution failure is a 404 (the logo is decoration, not functionality)
                got = None
        if got is None:
            self._error("not found", 404)
            return
        path, ctype = got
        self._bytes(path.read_bytes(), ctype)  # Content-Type is fixed from the extension

    def _bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if self.hub is None:
                self._try_activate()
            if path == "/api/setup/status":
                self._json(self.setup_state())
                return
            if self.hub is None:
                # While there is no clone, return the setup screen for every GET.
                html = (SETUP_HTML
                        .replace("%%UPSTREAM%%", UPSTREAM_URL)
                        .replace("%%DEST%%", self.default_dest()))
                data = localized_html(html.encode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path in ("/", "/index.html"):
                self._file("index.html")
            elif path == "/review.html":
                self._file("review.html")
            elif path == "/city-logo":
                self._city_logo()
            elif path == "/review-viewer.html":
                viewer = self.hub.root / "tools" / "attr_editor" / "viewer.html"
                if not viewer.is_file():
                    self._error(tr("hub.err_viewer_missing",
                                   "The 3D view component was not found"), 404)
                else:
                    self._bytes(viewer.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/status":
                # Merge in the authentication progress (userCode / waiting / login)
                # so the dashboard can also show "Connect" (the device flow).
                self._json({**self.hub.status(), "github": self.auth_mgr.state()})
            elif path == "/api/tools":
                self._json({"ok": True, "tools": self.hub.tools()})
            elif path == "/api/feedback":
                self._json(self.hub.feedback_defaults())
            elif path == "/api/contributions":
                self._json({"ok": True, **self.hub.contributions()})
            elif path == "/api/reviews":
                self._json(self.hub.review_queue(include_examples=query.get("demo") == ["1"]))
            elif re.fullmatch(r"/api/reviews/\d+/asset", path):
                number = int(path.split("/")[3])
                side = (query.get("side") or [""])[0]
                asset_path = (query.get("path") or [""])[0]
                data, mime = self.hub.review_asset(number, side, asset_path)
                self._bytes(data, mime)
            elif re.fullmatch(r"/api/building/\d{8,9}/[^/]+", path):
                parts = path.split("/")
                self._json({
                    "ok": True,
                    "building": self.hub.review_building_model(parts[3], unquote(parts[4])),
                })
            elif path.startswith("/textures/"):
                data, mime = self.hub.review_texture(unquote(path[len("/textures/"):]))
                self._bytes(data, mime)
            elif re.fullmatch(r"/api/reviews/\d+", path):
                self._json(self.hub.review_detail(int(path.rsplit("/", 1)[1])))
            else:
                self._error("not found", 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {e}", 500)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            # Setup (pre-clone) button actions. Everything advances with just a press.
            if path == "/api/auth/start":
                self._json({"ok": True, **self.auth_mgr.start()})
                return
            if path == "/api/setup/fork":
                nwo, err = create_fork(self.auth_mgr.token())
                if nwo:
                    Handler._fork_cache = (time.time(), nwo)
                self._json({"ok": err is None, "error": err, "nwo": nwo,
                            "status": self.setup_state()})
                return
            if path == "/api/setup/clone":
                if self.hub is not None:
                    self._error(tr("hub.err_setup_done", "Setup is already complete"), 409)
                    return
                nwo = self.fork_nwo(self.auth_mgr.state().get("login"))
                if not nwo:
                    self._error(tr("hub.err_fork_first", "Create your own copy first"))
                    return
                dest = (body.get("dest") or "").strip() or self.default_dest()
                self.setup_mgr.start(f"https://github.com/{nwo}.git", dest)
                self._json({"ok": True})
                return
            if self.hub is None:
                self._error(tr("hub.err_setup_incomplete", "Setup is not complete"), 409)
                return
            if path == "/api/launch":
                self._json(self.hub.launch(body["tool"]))
            elif path == "/api/feedback":
                self._json(self.hub.submit_feedback(body))
            elif path == "/api/refresh":
                self._json({"ok": True, **self.hub.contributions(force=True)})
            elif re.fullmatch(r"/api/reviews/\d+/decision", path):
                number = int(path.split("/")[3])
                self._json(self.hub.submit_review(number, demo=bool(body.get("demo"))))
            elif re.fullmatch(r"/api/reviews/\d+/feedback", path):
                number = int(path.split("/")[3])
                self._json(self.hub.submit_review_feedback(
                    number, str(body.get("message") or ""), demo=bool(body.get("demo")),
                ))
            elif re.fullmatch(r"/api/reviews/\d+/retry", path):
                number = int(path.split("/")[3])
                self._json(self.hub.request_ci_retry(
                    number, demo=bool(body.get("demo")),
                ))
            else:
                self._error("not found", 404)
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            self._error(str(e))
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {e}", 500)


def setup_console_messages(saved_auth: bool) -> list[str]:
    """Make the pre-clone wait look like terminal guidance, not an abnormal stop.

    The setup screen quotes this wording, so emit it in the same language as the screen.
    """
    lines = ["  " + tr("hub.console_setup_running",
                       "Status: initial setup in progress (normal — not stopped)")]
    if saved_auth:
        lines.append("  " + tr(
            "hub.console_saved_auth",
            "The previous GitHub connection is reused. "
            "The 8-digit code screen may be skipped"))
    lines += [
        "  " + tr("hub.console_follow_browser", "Follow the instructions in the browser"),
        "  " + tr("hub.console_terminal_waiting",
                  "This terminal stays open to run the browser screen"),
        "  " + tr("hub.console_resume",
                  "To resume later, just open the same launcher file again"),
    ]
    return lines


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
    parser.add_argument("--repo", type=Path, help="local clone of sample-tokyo-station (auto-detected if omitted)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--serve-tool", dest="serve_tool", metavar="KEY",
                        help="internal: launch specified tool (attr_editor/tex_editor) in this process")
    args = parser.parse_args()

    # Clone location: explicit → ancestor detection → shared config (same as the attribute editor)
    repo_root = args.repo or detect_repo()
    if repo_root is None:
        saved = load_config().get("repo")
        if saved and has_building_data(Path(saved)):
            repo_root = Path(saved)

    # Internal dispatch: the path where the hub exe itself launches a child tool in the frozen distribution
    if args.serve_tool:
        if repo_root is None:
            sys.exit("Error: --serve-tool requires --repo")
        serve_tool(Path(repo_root), args.serve_tool, args.port)
        return

    if repo_root is not None and has_building_data(Path(repo_root)):
        sync_upstream_main(repo_root)
        Handler.hub = Hub(Path(repo_root))
        print(f"  Repository: {Handler.hub.root}")
    # Without a clone, start in initial-setup mode (the clone runs from the browser)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}/"
    print(f"citygml-hub: {url}")
    if Handler.hub is None:
        for line in setup_console_messages(bool(AUTH.token())):
            print(line)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting")


if __name__ == "__main__":
    main()
