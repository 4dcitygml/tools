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
from concurrent.futures import ThreadPoolExecutor
import json
import mmap
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
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

APP_DIR = Path(__file__).resolve().parent
# The shared runtime (program/runtime.py in the bundle, tools/runtime.py in the source
# tree) holds every fact the tools share and puts the other shared modules on sys.path.
_SHARED = next((d for d in (APP_DIR, APP_DIR.parent) if (d / "runtime.py").is_file()), None)
if _SHARED is None:
    sys.exit("runtime.py is missing next to the hub: install the tools again with the one-line command")
sys.path.insert(0, str(_SHARED))
import runtime  # noqa: E402
import accounts  # noqa: E402
import git_sync  # noqa: E402
import pr_classification  # noqa: E402
import shortcuts  # noqa: E402

DEFAULT_PORT = 8760


def tr(key: str, default: str, **params) -> str:
    """Server-generated text of the hub in the display language (fail-open)."""
    return runtime.tr("hub", key, default, **params)


def current_login() -> "str | None":
    """The account this hub process works as (the one bound to its city), or None."""
    return SESSION.login


# ---- One clone per city (hub-v1.2.0) ----
# The environment variable set by the city's start script only selects WHICH clone
# to use; the clone's own 4dcitygml.json defines the city (sync target, fork target,
# logo, theme). The two must agree, otherwise the clone is not adopted. Clones are
# remembered per city under `cities[<owner/repo>]`; the legacy single `repo` key is
# kept as "last used" for the standalone attribute editor.


# ---- Tool updates (hub-v1.2.0): check automatically, fetch on request, apply at the next start ----
# The version of the hub is decided by 4dcitygml/tools releases (cities distribute no
# code and pin no client version). At start the hub asks the releases API once; a
# newer version is announced in a banner. "Get it now" runs the launcher's fetch mode
# (runtime.fetch_latest_hub: download, SHA-256 digest check, unpack into the versioned
# folder next to the running one). Nothing is downloaded without that click, and the
# running hub is never modified; the launcher picks the newest installed version at
# the next start. A city may declare `min_hub` in 4dcitygml.json (data, advisory):
# the banner then says why the update matters.
HUB_RELEASES_API = "https://api.github.com/repos/4dcitygml/tools/releases?per_page=30"


def latest_hub_release(releases: list) -> "dict | None":
    """The newest published hub-v release from the releases API payload: {tag, notesUrl}, or None."""
    best = None
    for rel in releases if isinstance(releases, list) else []:
        key = runtime.version_tuple(rel.get("tag_name"))
        if not key or rel.get("draft") or rel.get("prerelease"):
            continue
        if best is None or key > runtime.version_tuple(best["tag_name"]):
            best = rel
    return {"tag": best["tag_name"], "notesUrl": best.get("html_url") or ""} if best else None


def fetch_releases(url: str = HUB_RELEASES_API) -> list:
    status, raw, _ = runtime.request(url, headers={"Accept": "application/vnd.github+json"}, timeout=20)
    if status != 200:
        raise RuntimeError(f"GitHub releases API answered HTTP {status}")   # rate limit, outage: named, not "up to date"
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, list) else []


def min_hub_of(clone_root) -> "str | None":
    try:
        value = json.loads((Path(clone_root) / "4dcitygml.json").read_text(encoding="utf-8")).get("min_hub")
    except (OSError, ValueError, AttributeError):
        return None
    return str(value) if runtime.version_tuple(value) else None


class UpdateManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "running": runtime.running_hub_tag(), "latest": None, "available": False, "checked": False,
            "notesUrl": "", "minHub": None, "minHubOk": True, "error": "",
            "fetch": {"state": "idle", "tag": None, "message": ""},
        }

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def check_async(self, min_hub: "str | None" = None, fetch_json=None) -> None:
        def work():
            try:
                latest = latest_hub_release((fetch_json or fetch_releases)())
            except Exception as e:  # offline etc.: silently keep the running version
                self._set(checked=True, error=str(e))
                return
            running = runtime.version_tuple(self._state["running"])
            newest = runtime.version_tuple(latest["tag"]) if latest else None
            available = bool(newest and (running is None or newest > running))
            min_ok = True
            if min_hub and runtime.version_tuple(min_hub) and running and runtime.version_tuple(min_hub) > running:
                min_ok = False
                available = available or bool(newest and newest >= runtime.version_tuple(min_hub))
            fetch = dict(self._state["fetch"])
            if latest and (runtime.hubs_dir() / latest["tag"] / "program" / "hub.py").is_file() and available:
                fetch = {"state": "done", "tag": latest["tag"], "message": ""}
            self._set(checked=True, latest=latest["tag"] if latest else None,
                      notesUrl=(latest or {}).get("notesUrl", ""), available=available,
                      minHub=min_hub, minHubOk=min_ok, fetch=fetch)
        threading.Thread(target=work, name="citygml-update-check", daemon=True).start()

    def fetch_async(self, fetch=None) -> dict:
        """Start the launcher's fetch of the newest version (runtime.fetch_latest_hub); the
        state reports running / done / failed. fetch is a test seam."""
        with self._lock:
            tag = self._state.get("latest")
            if self._state["fetch"]["state"] == "running":
                return dict(self._state["fetch"])
            if not tag:
                return {"state": "failed", "tag": None, "message": "No newer version is known"}
            self._state["fetch"] = {"state": "running", "tag": tag, "message": ""}

        def work():
            try:
                got = (fetch or runtime.fetch_latest_hub)()
                self._set(fetch={"state": "done", "tag": got or tag, "message": ""})
            except Exception as e:
                self._set(fetch={"state": "failed", "tag": tag, "message": runtime.public_message(e)})
        threading.Thread(target=work, name="citygml-update-fetch", daemon=True).start()
        return {"state": "running", "tag": tag, "message": ""}


def create_city_shortcut(clone_root: Path) -> dict:
    """A desktop launcher for the clone's city: only the city id is embedded (see tools/shortcuts.py)."""
    city = runtime.clone_city(clone_root)
    if not city:
        raise RuntimeError("The clone does not name its city (4dcitygml.json repo)")
    try:
        cfg = json.loads((Path(clone_root) / "4dcitygml.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    name = cfg.get("name")
    lang = str(cfg.get("lang") or "en").split("-")[0]
    if isinstance(name, dict):
        display = name.get(lang) or name.get("en") or next(iter(name.values()), city)
    else:
        display = name or city.split("/")[-1]
    logo = Path(clone_root) / str(cfg.get("logo") or "logo.png")
    shortcuts.ensure_launcher()
    path = shortcuts.create_shortcut(runtime.tools_dir(), str(display), city, logo if logo.is_file() else None)
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
        "port": 8765,
        "icon": "🏢",
    },
    "tex_editor": {
        "label": "Texture Editor",
        "desc": "Replace facade photos (LOD2 textures) and submit a PR",
        "path": "tex_editor/app.py",
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
    """The PR's class for the review screen — Exchange Contract A5, decided by the shared table.

    `other` is a class like any other: it changes how the item is rendered, never
    whether it is shown (A5: review clients show every open PR)."""
    explicit = str(pr.get("review_kind") or "")
    if explicit in ("attribute", "texture", "geometry"):
        return explicit
    title = str(pr.get("title") or "")
    head = str((pr.get("head") or {}).get("ref") or pr.get("headRefName") or "")
    return pr_classification.classify_by_name(head, title) or "other"


_TRAILER_ID_RE = re.compile(r"^(?:Building|Building-Added|Building-Deleted):[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def building_id_from_commit_messages(messages: "list[str]") -> str:
    """The building declared by the A2 trailers (the contract's canonical identity), or ''."""
    for message in messages:
        hit = _TRAILER_ID_RE.search(str(message or ""))
        if hit:
            return hit.group(1)
    return ""


def building_id_from_commits(token: str, nwo: str, number: int) -> str:
    code, commits = runtime.github_api(f"/repos/{nwo}/pulls/{number}/commits?per_page=100", token)
    if code != 200 or not isinstance(commits, list):
        return ""
    return building_id_from_commit_messages(
        [str((c.get("commit") or {}).get("message") or "") for c in commits])


def latest_reviews(reviews: list) -> dict:
    """One current opinion per account: a later APPROVED / CHANGES_REQUESTED / DISMISSED
    review replaces the earlier one; plain comments neither add nor erase a vote."""
    latest: dict = {}
    for index, review in enumerate(reviews):
        if review.get("state") not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            continue
        # GitHub always names the reviewer; a record without one cannot be merged with another.
        login = str((review.get("user") or {}).get("login") or "").lower() or f"#{review.get('id') or index}"
        if int(review.get("id") or 0) >= int(latest.get(login, {}).get("id") or 0):
            latest[login] = review
    return latest


_BUILDING_ID_REVIEW_RE = re.compile(r"(?<![\w-])(\d{5}-bldg-[A-Za-z0-9_-]+)")

# Even in tests without the attribute editor's dictionary, the main items get readable English labels.
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


def attr_editor_module():
    """The attribute editor, bundled next to the hub (imported on first use: the review
    screen borrows its attribute names and its 3D model of a building)."""
    from attr_editor import app
    return app


def attribute_labels() -> dict[str, str]:
    """Display names of attributes: the core set plus the attribute editor's dictionary."""
    labels = dict(_CORE_ATTRIBUTE_LABELS)
    try:
        labels.update({str(k): str(v) for k, v in attr_editor_module().LABELS.items()})
    except Exception:   # a broken editor module must not stop the hub
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
        point("classification", "Change classification",
              tr("hub.cp_classification_label", "Change classification"),
              summary.get("classification", "na"),
              tr("hub.cp_classification_ok",
                 "The kind of change is declared (branch prefix or title)"),
              tr("hub.cp_classification_ng",
                 "The kind of change is not declared; CI posted how to fix it")),
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


# ---- Theme pack (shares tools/themes/theme_loader.py; runs without themes if missing) ----


# ---- Language pack (shares tools/i18n/i18n_loader.py; runs with the original text if missing) ----


class Hub:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._attribute_labels = attribute_labels()
        self._attr_repo = None
        self._attr_repo_lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._ports: dict[str, int] = {}   # tool ports used by this hub (city)
        self._repo_probe: dict[int, "tuple[float, bool]"] = {}  # port -> (timestamp, is our repo)
        self._contrib_cache: "tuple[float, dict] | None" = None
        self._cache_lock = threading.Lock()

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
        r = runtime.git(self.root, "remote", "set-url", "origin", f"https://github.com/{fork_nwo}.git")
        if r.returncode == 0:
            print(f"  origin → {fork_nwo} (the account's copy)")
            return True
        return False

    def nwo(self) -> "str | None":
        """origin's owner/repo (for GitHub links and gh)."""
        url = self._git("remote", "get-url", "origin")
        m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url) or re.match(
            r"https://github\.com/(.+?)(?:\.git)?$", url
        )
        return m.group(1) if m else None

    def status(self) -> dict:
        git_exe, git_bundled = runtime.git_exe(), runtime.git_bundled()
        py_exe, py_bundled = runtime.python_exe(), runtime.python_bundled()
        return {
            "ok": True,
            "repo": str(self.root),
            "branch": self._git("rev-parse", "--abbrev-ref", "HEAD"),
            "gitUser": self._git("config", "user.name"),
            "gitEmail": self._git("config", "user.email"),
            "nwo": self.nwo(),
            "account": current_login(),
            "hubTag": runtime.running_hub_tag(),
            "sync": SESSION.sync.state if SESSION.sync is not None else None,
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
        allocated = self._ports.get(key)
        if allocated is not None:
            return allocated
        port = t["port"]
        for _ in range(20):
            if not runtime.port_open(port) or self._port_serves_our_repo(port):
                break
            port += 2
        self._ports[key] = port
        return port

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

    def _launch_cmd(self, key: str, t: dict, port: "int | None" = None) -> list:
        """Child-tool launch command: the bundled (or system) python runs the editor's app.py."""
        return [runtime.python_exe(), str(self._tool_app(t)), "--repo", str(self.root),
                "--port", str(port or t["port"]), "--no-browser"]

    def launch(self, key: str) -> dict:
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
        env = {**accounts.scrub_git_env(os.environ), "CITYGML_ACCOUNT": current_login() or ""}
        proc = subprocess.Popen(self._launch_cmd(key, t, port), cwd=str(self.root), env=env)
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
        # Proposals and issues live on the city repository (upstream); the clone's origin is
        # the person's copy, where no PR ever exists.
        nwo = runtime.upstream_nwo(self.root)
        token = SESSION.token()
        login = ((SESSION.account.user() or {}) if token else {}).get("login")
        if not token or not login:
            reason = tr("hub.err_gh_not_connected",
                        'GitHub is not connected (you can connect from "Connect to GitHub" '
                        "in the account section)")
            return {"ok": False, "reason": reason, "login": None,
                    "prs": [], "issues": [], "merged": 0, "badge": badge_for(0)}
        code, data = runtime.github_api("/graphql", token, method="POST", payload={
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
                c_code, c_data = runtime.github_api(
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
        token = SESSION.token()
        login = ((SESSION.account.user() or {}) if token else {}).get("login") or ""
        nwo = runtime.upstream_nwo(getattr(self, "root", None))
        if not token or not login:
            raise RuntimeError(tr("hub.err_connect_first", "Connect to GitHub first"))
        return token, login, nwo

    def reviewer_permission(self) -> dict:
        token, login, nwo = self._review_identity()
        code, data = runtime.github_api(f"/repos/{nwo}/collaborators/{login}/permission", token)
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
        """The attribute editor's model of the clone (the 3D view of the review screen), built once."""
        if self._attr_repo is None:
            with self._attr_repo_lock:
                if self._attr_repo is None:
                    self._attr_repo = attr_editor_module().Repo(self.root)
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
        c_status, comments = runtime.github_api(f"/repos/{nwo}/issues/{number}/comments?per_page=100", token)
        f_status, files = runtime.github_api(f"/repos/{nwo}/pulls/{number}/files?per_page=100", token)
        comments = comments if c_status == 200 and isinstance(comments, list) else []
        files = files if f_status == 200 and isinstance(files, list) else []
        return extract_building_id(*[c.get("body") for c in comments]) or self._building_id_from_local_files(
            files, comments
        )

    def _review_queue_item(self, token: str, nwo: str, pr: dict) -> "dict | None":
        """Build one list item, with two states based on who works on it next.

        Every open PR against main is listed whatever its class (A5); `other`
        and unclassified proposals are shown as plain items."""
        kind = review_kind(pr)
        if str((pr.get("base") or {}).get("ref") or "") != "main":
            return None
        number = int(pr.get("number") or 0)
        body = str(pr.get("body") or "")
        # Pre-CI preview only: as soon as CI has posted its `reason` row, that verdict wins (A6).
        reason_ok = review_ready_reason(human_reason(body, kind, self._attribute_labels))
        head_sha = str((pr.get("head") or {}).get("sha") or "")
        check_runs: list = []
        if head_sha:
            status, data = runtime.github_api(
                f"/repos/{nwo}/commits/{head_sha}/check-runs?per_page=100", token
            )
            if status == 200 and isinstance(data, dict):
                check_runs = data.get("check_runs") or []
        check_status = _overall_check_status(check_runs)

        c_status, comment_data = runtime.github_api(
            f"/repos/{nwo}/issues/{number}/comments?per_page=100", token
        )
        comments = comment_data if c_status == 200 and isinstance(comment_data, list) else []
        ci_rows = _inspection_summary_statuses(comments)
        if "reason" in ci_rows:
            reason_ok = ci_rows["reason"] == "pass"
        r_status, review_data = runtime.github_api(
            f"/repos/{nwo}/pulls/{number}/reviews?per_page=100", token
        )
        reviews = review_data if r_status == 200 and isinstance(review_data, list) else []
        reviewer_feedback = any(
            r.get("state") == "CHANGES_REQUESTED"
            and str(r.get("commit_id") or "") == head_sha
            for r in latest_reviews(reviews).values()
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
        # Identity: the A2 trailer first (tool-independent), then the title/body/comment patterns.
        building_id = building_id_from_commits(token, nwo, number) if number else ""
        if not building_id:
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
        # All open PRs, paged; a failing later page keeps what was read (fail-soft),
        # a failing first page is an error the screen shows.
        pulls: list = []
        for page in range(1, 11):
            code, data = runtime.github_api(
                f"/repos/{nwo}/pulls?state=open&sort=updated&direction=desc&per_page=100&page={page}",
                token,
            )
            if code != 200 or not isinstance(data, list):
                if page == 1:
                    raise RuntimeError(str(getattr(data, "get", lambda *_: "")("message") or f"HTTP {code}"))
                break
            pulls.extend(data)
            if len(data) < 100:
                break

        if include_examples and not any(int(p.get("number") or 0) == 6 for p in pulls):
            ex_code, example = runtime.github_api(f"/repos/{nwo}/pulls/6", token)
            if ex_code == 200 and isinstance(example, dict):
                example = {**example, "example": True, "review_kind": "attribute"}
                pulls.append(example)

        candidates = [
            pr for pr in pulls
            if str((pr.get("base") or {}).get("ref") or "") == "main"
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
            code, result = runtime.github_api(f"/search/issues?{query}", token)
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

        code, pr = runtime.github_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(str(getattr(pr, "get", lambda *_: "")("message") or f"HTTP {code}"))
        endpoints = {
            "files": f"/repos/{nwo}/pulls/{number}/files?per_page=100",
            "comments": f"/repos/{nwo}/issues/{number}/comments?per_page=100",
            "reviews": f"/repos/{nwo}/pulls/{number}/reviews?per_page=100",
            "commits": f"/repos/{nwo}/pulls/{number}/commits?per_page=100",
            "checks": f"/repos/{nwo}/commits/{(pr.get('head') or {}).get('sha', '')}/check-runs?per_page=100",
        }
        fetched: dict = {}
        for key, endpoint in endpoints.items():
            status, data = runtime.github_api(endpoint, token)
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
        ci_rows = _inspection_summary_statuses(comments)
        if "reason" in ci_rows:   # CI's verdict wins over the local preview (A6)
            reason_ok = ci_rows["reason"] == "pass"
        commits = fetched["commits"] if isinstance(fetched["commits"], list) else []
        building_id = building_id_from_commit_messages(
            [str((c.get("commit") or {}).get("message") or "") for c in commits]
        ) or extract_building_id(
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
            for r in latest_reviews(reviews).values()
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
        code, pr = runtime.github_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(str(getattr(pr, "get", lambda *_: "")("message") or f"HTTP {code}"))
        ref = str((pr.get(side) or {}).get("sha") or (pr.get(side) or {}).get("ref") or "")
        if not ref:
            raise RuntimeError(tr("hub.err_no_image_ref", "Could not resolve the image reference"))
        endpoint = f"/repos/{nwo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"
        status, data, mime = runtime.github_raw(endpoint, token)
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
        code, data = runtime.github_api(
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
        code, data = runtime.github_api(
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
        code, pr = runtime.github_api(f"/repos/{nwo}/pulls/{number}", token)
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
        c_code, c_data = runtime.github_api(
            f"/repos/{nwo}/issues/{number}/comments?per_page=100", token
        )
        comments = c_data if c_code == 200 and isinstance(c_data, list) else []
        r_code, r_data = runtime.github_api(
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
        post_code, data = runtime.github_api(
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
        token = SESSION.token()
        login = ((SESSION.account.user() or {}) if token else {}).get("login") or ""
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
        code, data = runtime.github_api(
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


def load_preset() -> dict:
    """preset.json next to the launcher (distribution defaults such as oauthClientId)."""
    return runtime.read_json(APP_DIR / "preset.json")


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
OAUTH_SCOPE = "public_repo"  # Necessary and sufficient for fork/push/PR on public repos (revert to "repo" when going private)
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


def oauth_client_id() -> str:
    return str(load_preset().get("oauthClientId") or os.environ.get("CITYGML_OAUTH_CLIENT_ID", ""))


class AuthManager:
    """The account of this hub process: the one its city is bound to (hub-v1.2.1).

    One process serves one city, so one bound account. Nothing is used before the
    person has chosen an account on the account screen (a saved one, this computer's
    GitHub CLI sign-in, or a new device-flow sign-in); the choice is remembered per
    city in the shared settings and is never made silently. The computer's own Git
    configuration and credential helpers are left alone.
    """

    # The screen polls the state every 2 seconds, so queries to GitHub are throttled
    # with a TTL (avoiding API rate limits and a sluggish screen).
    USER_TTL = 20
    MACHINE_TTL = 300

    def __init__(self, session: "Session") -> None:
        self.session = session
        self.lock = threading.RLock()
        self.user_code = ""
        self.verify_url = ""
        self.waiting = False
        self.error: "str | None" = None
        self.certificate_report: "str | None" = None
        self.login: "str | None" = None
        self._token = ""
        self._user: "dict | None" = None
        self._user_at = 0.0
        self._machine: "dict | None" = None
        self._machine_at = 0.0
        self._machine_busy = False
        self._legacy: "dict | None" = None
        self._migrated = False

    # ---- binding to the city ----
    @property
    def city(self) -> "str | None":
        return self.session.city

    @property
    def clone_root(self) -> "Path | None":
        return self.session.hub.root if self.session.hub is not None else None

    def attach(self) -> None:
        """Adopt the account recorded for the session's city (at start; nothing is chosen silently)."""
        city = self.city
        with self.lock:
            self.login = accounts.city_login(city) if city else None
            self._token = accounts.token_for(self.login) if self.login else ""
            self._user, self._user_at = None, 0.0
        if self.login:
            self._apply_identity()
        threading.Thread(target=self._migrate_legacy, name="citygml-auth-migrate", daemon=True).start()

    def _migrate_legacy(self) -> None:
        """Move the single token of earlier versions into an account file (needs one API call)."""
        if self._migrated:
            return
        self._migrated = True
        try:
            accounts.migrate_legacy_token()
        except Exception:
            pass

    def _apply_identity(self) -> None:
        root = self.clone_root
        if not root or not self.login:
            return
        rec = accounts.load_account(self.login) or {}
        accounts.apply_clone_identity(root, self.login, rec.get("id"))

    def clone_opened(self) -> None:
        """A clone is served from now on: record the binding when the account was chosen
        before the clone existed (setup), and write the identity into the clone."""
        if self.city and self.login:
            accounts.bind_city_login(self.city, self.login)
        self._apply_identity()

    def _bind(self, login: str, token: str) -> None:
        with self.lock:
            self.login, self._token = login, token
            self._user, self._user_at = None, 0.0
            self.waiting, self.user_code, self.error = False, "", None
            self.certificate_report = None
        if self.city:
            accounts.bind_city_login(self.city, login)
        self._apply_identity()
        self.session.account_changed()

    def token(self) -> str:
        with self.lock:
            return self._token

    def user(self) -> "dict | None":
        """Authenticated user (TTL cache). A token GitHub rejects unbinds the account."""
        with self.lock:
            if self._user_at > time.time() - self.USER_TTL:
                return self._user
            token, login = self._token, self.login
        code, user = runtime.github_user_status(token) if token else (0, None)
        with self.lock:
            if (self._token, self.login) != (token, login):
                return self._user   # the binding changed while GitHub was asked: that answer is stale
        if token and code == 401:
            self._drop_dead_account(login, token)
        with self.lock:
            # unchanged binding, or unbound meanwhile (the account was just dropped): remember the
            # answer so the screen's polling does not ask GitHub again within the TTL
            if (self._token, self.login) == (token, login) or self.login is None:
                self._user, self._user_at = user, time.time()
        return user

    def _drop_dead_account(self, login, token) -> None:
        """Forget the account whose token GitHub rejected — only if it is still the bound one."""
        with self.lock:
            if (self.login, self._token) != (login, token):
                return
            self.login, self._token = None, ""
            self.error = tr("hub.err_auth_revoked",
                            "GitHub no longer accepts the connection of @{login} (revoked or expired). "
                            "It was removed from this computer. Choose one of the options on this "
                            "screen: a new sign-in with the 8-digit code, or another account.",
                            login=login or "")
        if login:
            accounts.delete_account(login)
        if self.city:
            accounts.bind_city_login(self.city, None)

    def forget_user(self) -> None:
        with self.lock:
            self._user_at = 0.0

    @staticmethod
    def _save_failed(exc: Exception) -> str:
        where = str(runtime.auth_dir())
        return tr("hub.err_account_save_failed",
                  "The connection could not be saved on this computer ({where}: {reason}). "
                  "Check that the folder can be written to, then choose again.", where=where, reason=str(exc))

    # ---- the choices on the account screen ----
    def use_saved(self, login: str) -> "str | None":
        """Use a stored account for this city. Returns an error message or None."""
        try:
            login = accounts.safe_login(login)
        except ValueError:
            return tr("hub.err_account_unknown", "That account is not saved on this computer. Choose one of the options on this screen.")
        token = accounts.token_for(login)
        if not token:
            return tr("hub.err_account_unknown", "That account is not saved on this computer. Choose one of the options on this screen.")
        code, user = runtime.github_user_status(token)
        if code == 401:
            accounts.delete_account(login)
            return tr("hub.err_auth_revoked",
                      "GitHub no longer accepts the connection of @{login} (revoked or expired). "
                      "It was removed from this computer. Choose one of the options on this "
                      "screen: a new sign-in with the 8-digit code, or another account.", login=login)
        if not user:
            return tr("hub.err_auth_check_failed",
                      "GitHub could not be reached to check the connection; nothing was changed. "
                      "Check the internet connection and press the same choice again.")
        try:
            accounts.save_account(user["login"], token, user.get("id"), user.get("name") or "")
        except OSError as e:
            return self._save_failed(e)
        if user["login"] != login:
            # Renamed or differently cased: keep one file per account. On a case-insensitive
            # file system (macOS, Windows) the two names are already the same file.
            try:
                same = os.path.samefile(accounts.account_path(login), accounts.account_path(user["login"]))
            except OSError:
                same = False
            if not same:
                accounts.delete_account(login)
        self._bind(user["login"], token)
        return None

    def use_machine(self) -> "str | None":
        """Use this computer's GitHub CLI sign-in (only on an explicit click)."""
        token = accounts.machine_token()
        if not token:
            return tr("hub.err_machine_missing",
                      "This computer's GitHub CLI is not signed in (any more). Choose one of the other options.")
        code, user = runtime.github_user_status(token)
        if not user:
            return tr("hub.err_auth_check_failed",
                      "GitHub could not be reached to check the connection; nothing was changed. "
                      "Check the internet connection and press the same choice again.")
        try:
            accounts.save_account(user["login"], token, user.get("id"), user.get("name") or "")
        except OSError as e:
            return self._save_failed(e)
        self._bind(user["login"], token)
        return None

    def disconnect(self, delete: bool = False) -> None:
        """Unbind this city's account (and optionally delete the stored account)."""
        login = self.login
        with self.lock:
            self.login, self._token, self._user, self._user_at = None, "", None, 0.0
            self.waiting, self.user_code, self.error = False, "", None
            self.certificate_report = None
            root = self.clone_root
        if self.city:
            accounts.bind_city_login(self.city, None)
        if delete and login:
            accounts.delete_account(login)
        if root:
            accounts.clear_clone_identity(root)
        self.session.account_changed()

    def machine(self) -> "dict | None":
        """This computer's GitHub CLI login, read from the CLI's own config file.

        Nothing is sent to GitHub and no token is read until the person presses the
        button (use_machine). CITYGML_HUB_NO_GH=1 hides the option."""
        with self.lock:
            if self._machine_at > time.time() - self.MACHINE_TTL:
                return self._machine
        try:
            login = accounts.machine_login_from_config()
        except Exception:
            login = ""
        found = {"login": login} if login else None
        with self.lock:
            self._machine, self._machine_at, self._machine_busy = found, time.time(), False
        return found

    def legacy(self) -> dict:
        """What earlier versions left on this computer (found once per process)."""
        with self.lock:
            if self._legacy is not None:
                return self._legacy
        found = accounts.legacy_traces()
        with self.lock:
            self._legacy = found
        return found

    def review_legacy(self, remove: bool) -> list:
        """The hand-over screen was read; optionally remove what an earlier version wrote."""
        removed = []
        if remove:
            removed = accounts.remove_legacy_traces()
        accounts.acknowledge_legacy()
        with self.lock:
            self._legacy = None
        return removed

    def state(self) -> dict:
        user = self.user() if self.login else None
        legacy = self.legacy()
        if not self.login:
            self.machine()
        with self.lock:
            return {
                "clientId": bool(oauth_client_id()),
                "waiting": self.waiting,
                "userCode": self.user_code,
                "verifyUrl": self.verify_url,
                "error": self.error,
                "certificateReport": self.certificate_report,
                "login": (user or {}).get("login") if user else None,
                "chosen": bool(self.login),
                "bound": self.login,
                "city": self.city,
                "accounts": [a["login"] for a in accounts.list_accounts()],
                "machine": self._machine,
                "machineChecking": bool(self._machine_busy),
                "legacy": legacy if legacy.get("any") else None,
                "legacyReviewed": bool(accounts.is_legacy_acknowledged()),
            }

    # ---- a new sign-in (device flow) ----
    def start(self) -> dict:
        cid = oauth_client_id()
        if not cid:
            raise RuntimeError(tr(
                "hub.err_no_client_id",
                "This tool's GitHub connection setting (client_id) is not configured"))
        with self.lock:
            if self.waiting:
                return {"userCode": self.user_code, "verifyUrl": self.verify_url}
            self.certificate_report = None
            self.error = None
            try:
                r = runtime.post_form(DEVICE_CODE_URL, {"client_id": cid, "scope": OAUTH_SCOPE})
            except (urllib.error.URLError, OSError) as exc:
                if runtime.is_certificate_error(exc):
                    self.certificate_report = runtime.certificate_diagnostics()
                    self.error = tr(
                        "hub.err_auth_certificate",
                        "GitHub's HTTPS certificate could not be verified. The connection was stopped "
                        "before sign-in. Please report this message and the tool version to the distributor.")
                    raise RuntimeError(self.error) from None
                raise RuntimeError(tr(
                    "hub.err_auth_offline",
                    "GitHub could not be reached to start the sign-in. Check the internet "
                    "connection and press the button again."))
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
                r = runtime.post_form(ACCESS_TOKEN_URL, {
                    "client_id": cid, "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                })
            except OSError as exc:
                if runtime.is_certificate_error(exc):
                    with self.lock:
                        self.waiting = False
                        self.certificate_report = runtime.certificate_diagnostics()
                        self.error = tr(
                            "hub.err_auth_certificate",
                            "GitHub's HTTPS certificate could not be verified. The connection was stopped "
                            "before sign-in. Please report this message and the tool version to the distributor.")
                    return
                continue  # keep waiting through temporary network errors
            if r.get("access_token"):
                token = r["access_token"]
                user = runtime.github_user_status(token)[1]
                save_error = None
                if user:
                    try:
                        accounts.save_account(user["login"], token, user.get("id"), user.get("name") or "")
                    except ValueError:
                        user = None
                    except OSError as e:
                        save_error = self._save_failed(e)
                if save_error:
                    with self.lock:
                        self.waiting, self.error = False, save_error
                elif user:
                    self._bind(user["login"], token)
                else:
                    with self.lock:
                        self.waiting = False
                        self.error = tr("hub.err_auth_user_failed",
                                        "GitHub accepted the sign-in, but could not be asked who you are "
                                        "(offline?). Check the internet connection and start the sign-in again.")
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


class Session:
    """Everything this hub process knows about the one city it serves (contract §1): the
    city, its clone (once there is one), the account the city is bound to, the clone job
    of the setup screen, the background data sync and the version check. What GitHub said
    about the account's copy of the city (fork) and about reaching the city (access) is
    remembered until an event — account chosen or dropped, copy created — clears it."""

    def __init__(self) -> None:
        self.city: "str | None" = None
        self.hub: "Hub | None" = None
        self.account = AuthManager(self)
        self.setup = git_sync.CloneJob(clone_text, self.net_git_args, runtime.git_exe)
        self.sync = None
        self.updates = UpdateManager()
        self.fork = runtime.Memo()
        self.access = runtime.Memo()

    # ---- the account ----
    @property
    def login(self) -> "str | None":
        return self.account.login

    def token(self) -> str:
        return self.account.token()

    def net_git_args(self) -> list:
        """git arguments for a network command as the city's account (or anonymously)."""
        return runtime.git_args(net=True, store=accounts.store_for(self.login))

    def account_changed(self) -> None:
        """The account was chosen or dropped: what was known about its copy is stale. With
        a clone open, uploads must go to the new account's copy before anything else
        happens (an editor may be launched right after this click), so the lookup and
        the origin change run now, not in the background."""
        self.fork.clear()
        self.access.clear()
        if self.hub is not None and self.login:
            try:
                self.fork_nwo(fresh=True)
            except (urllib.error.URLError, OSError):
                pass   # offline: origin is checked again on the next lookup

    # ---- the clone ----
    def open_clone(self, root, *, sync: bool = False) -> "Hub":
        """Serve this clone from now on: remember it for its city, hand it to the account,
        and (at start) keep its main in line with the city and look for newer tools.
        Nothing is downloaded unasked."""
        root = Path(root).resolve()
        self.hub = Hub(root)
        self.city = runtime.clone_city(root) or self.city
        runtime.remember_clone(self.city, root)
        self.account.clone_opened()
        if sync:
            if runtime.git_exe():
                self.sync = git_sync.BackgroundSync(
                    root, lambda: runtime.upstream_url(root, ignore_env=True), self.net_git_args, log=print).start()
            self.updates.check_async(min_hub=min_hub_of(root))
        return self.hub

    def start(self, root, city: "str | None", *, sync: bool = True) -> None:
        """What main() knows: the city the launcher asked for and the clone found for it (or None)."""
        self.city = city
        if root is not None:
            self.open_clone(root, sync=sync)
        self.account.attach()
        if runtime.running_hub_tag():
            try:
                shortcuts.ensure_launcher()   # the person's launcher keeps up with the installed version
            except OSError:
                pass

    def try_activate(self) -> None:
        """After the setup screen's clone finished: serve it (the screen keeps polling until then)."""
        st = self.setup
        if self.hub is not None or not st.done or st.dest is None:
            return
        if runtime.has_building_data(Path(st.dest)):
            self.open_clone(st.dest)
        else:
            st.done = False
            st.error = tr("hub.err_clone_no_bldg",
                          "No building data (udx/bldg) was found in the cloned destination")

    def default_dest(self) -> str:
        """Clone destination (decided without asking). Named after the city so several
        cities' folders are told apart in the file manager; only steps aside with a
        sequence number when it already exists and is not empty."""
        name = f"CityGML Data ({self.city.split('/')[-1]})" if self.city else "CityGML Data"
        return str(next_available_dest(runtime.home() / "Documents" / name))

    # ---- what GitHub knows about the account and the city ----
    def upstream_ok(self) -> "bool | None":
        """Whether the city repository is reachable (None when no account is chosen). Asked
        from the setup screen's polling, so a "no" is kept for 5 seconds; a "yes" is kept
        until the account changes (access disappearing mid-way is not expected)."""
        if not self.login:
            return None
        fresh, val = self.access.get()
        if val:
            return True
        if fresh:
            return False
        return self.access.set(upstream_access(self.token()), 5)

    def fork_nwo(self, *, fresh: bool = False) -> "str | None":
        """The account's copy of the city (owner/repo), or None. Kept for a minute when
        found, 10 seconds when not (the person may create it on GitHub meanwhile);
        fresh=True asks again. A found copy becomes the clone's origin."""
        if not self.login:
            return None
        valid, val = self.fork.get()
        if valid and not fresh:
            return val
        found = find_fork(self.token(), self.login)
        self.fork.set(found, 60 if found else 10)
        if found and self.hub is not None:
            self.hub.ensure_origin(found)
        return found

    def fork_or_none(self) -> "str | None":
        """The account's copy, or None when GitHub cannot be asked right now (offline)."""
        try:
            return self.fork_nwo()
        except (urllib.error.URLError, OSError):
            return None

    def copy_missing(self) -> bool:
        """GitHub answered that the bound account has no copy of the city yet (offline: False,
        the dashboard stays usable). The account screen then offers to create it."""
        if not self.login or self.fork_or_none():
            return False
        fresh, val = self.fork.get()
        return bool(fresh and val is None)

    def setup_state(self) -> dict:
        """The whole setup-screen state (account, copy, clone job) as one payload."""
        auth = self.account.state()
        fork_checked = True
        try:
            # Both only for a verified sign-in (auth["login"]); a saved but unverified account
            # (offline) is shown as "bound", nothing is asked.
            fork = self.fork_nwo() if auth["login"] else None
            # If the fork already exists, upstream is reachable too (avoid extra API calls)
            access = True if fork else (self.upstream_ok() if auth["login"] else None)
        except (urllib.error.URLError, OSError):
            fork, access, fork_checked = None, None, False   # offline: the screen stays usable
        return {
            "ok": True,
            **auth,
            "fork": fork,
            "forkChecked": fork_checked,
            "access": access,
            "clone": self.setup.state(),
            "dest": self.default_dest(),
            "active": self.hub is not None,
            # "account": the clone exists, the city only needs its account chosen (hub-v1.2.1)
            "mode": "account" if self.hub is not None else "setup",
        }

    def settings_payload(self) -> dict:
        """Everything the settings screen shows (hub-v1.2.1): effective values, read-only paths."""
        auth = self.account.state()
        root = self.hub.root if self.hub is not None else None
        return {
            "ok": True,
            "city": self.city,
            "login": auth.get("login"),
            "chosen": auth.get("chosen"),
            "fork": self.fork_or_none() if auth.get("login") else None,
            "clone": str(root) if root else None,
            "identity": accounts.clone_identity(root) if root else {"name": "", "email": ""},
            "machineIdentity": accounts.machine_identity(),
            "accounts": accounts.list_accounts(),
            "hubTag": runtime.running_hub_tag(),
            "update": {k: v for k, v in self.updates.snapshot().items() if not k.startswith("_")},
            "paths": {
                "config": str(runtime.config_path()),
                "auth": str(runtime.auth_dir()),
                "tools": str(runtime.tools_dir()),
                "hubs": str(runtime.hubs_dir()),
            },
            "legacy": auth.get("legacy"),
        }



def upstream_access(token: str) -> bool:
    """Whether this token can reach upstream (the source data). Always 200 for a public repo."""
    code, _ = runtime.github_api(f"/repos/{runtime.upstream_nwo()}", token)
    return code == 200


def find_fork(token: str, login: str) -> "str | None":
    """The upstream fork owned by login (owner/repo), or None if absent."""
    repo = runtime.upstream_nwo().split("/")[-1]
    code, data = runtime.github_api(f"/repos/{login}/{repo}", token)
    if code == 200 and data.get("fork"):
        return data.get("full_name")
    return None


def create_fork(token: str) -> "tuple[str | None, str | None]":
    """Create a fork of upstream (or return the existing one). Returns (owner/repo, error message).

    Fork creation is asynchronous, so wait briefly and confirm the fork materialized.
    """
    user = runtime.github_user_status(token)[1]
    if not user:
        return None, tr("hub.err_connect_first", "Connect to GitHub first")
    login = user["login"]
    existing = find_fork(token, login)
    if existing:
        return existing, None
    code, data = runtime.github_api(f"/repos/{runtime.upstream_nwo()}/forks", token, method="POST", payload={})
    if code not in (200, 201, 202):
        msg = data.get("message") or f"HTTP {code}"
        if accounts.is_org_restriction(code, data):
            msg = tr("hub.err_org_restricted",
                     "The organization that hosts this city has not approved this tool yet. "
                     "Ask an organization owner to grant it access (GitHub → Settings → "
                     "Applications → this app → Organization access). You can keep editing "
                     "meanwhile; sending waits until then.")
        elif code == 404:
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


# Settings screen (hub-v1.2.1): the effective values of THIS city, item by item.
# There is no free-form editing of the settings file on purpose (each item has
# invariants: the clone must belong to the city, the account must have a stored
# connection); every change goes through an action that keeps them. The file's
# location is shown for people who want to look at it.


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


_CLONE_TEXTS = {
    "running": ("hub.err_clone_running", "A clone is already running"),
    "no_git": ("hub.err_git_missing_setup", "git was not found. Prepare git by following the setup guide"),
    "bad_url": ("hub.err_bad_repo_url", "The repository URL format is invalid"),
    "not_empty": ("hub.err_dest_not_empty", "The destination is not empty: {dest}"),
    "start": ("hub.clone_start", "Clone started: {url}"),
    "size_note": ("hub.clone_size_note", "(The data is several GB, so this takes minutes to tens of minutes)"),
    "done": ("hub.clone_done", "Clone finished"),
    "failed": ("hub.err_clone_failed", "git clone failed (exit {code})"),
}


def clone_text(event: str, **params) -> str:
    """The hub's wording for the clone job's events (git_sync.CloneJob)."""
    key, default = _CLONE_TEXTS[event]
    return tr(key, default, **params)


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
                self._file("setup.html", {"UPSTREAM": runtime.DEFAULT_CITY_URL, "DEFAULT_DEST": s.default_dest(),
                                          "MODE": "setup" if s.hub is None else "account"})
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
                    self._bytes(viewer.read_bytes(), "text/html; charset=utf-8")
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
            elif path == "/api/feedback":
                self._json(s.hub.feedback_defaults())
            elif path == "/api/contributions":
                self._json({"ok": True, **s.hub.contributions()})
            elif path == "/api/reviews":
                self._json(s.hub.review_queue(include_examples=query.get("demo") == ["1"]))
            elif re.fullmatch(r"/api/reviews/\d+/asset", path):
                number = int(path.split("/")[3])
                side = (query.get("side") or [""])[0]
                asset_path = (query.get("path") or [""])[0]
                data, mime = s.hub.review_asset(number, side, asset_path)
                self._bytes(data, mime)
            elif re.fullmatch(r"/api/building/\d{8,9}/[^/]+", path):
                parts = path.split("/")
                self._json({
                    "ok": True,
                    "building": s.hub.review_building_model(parts[3], unquote(parts[4])),
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
                clone = str(s.hub.root) if s.hub is not None else None

                def same_path(a, b) -> bool:
                    try:
                        return bool(a) and bool(b) and Path(a).expanduser().resolve() == Path(b).resolve()
                    except OSError:
                        return False

                def forget(cfg: dict) -> None:
                    cities = cfg.get("cities")
                    if isinstance(cities, dict):
                        cities.pop(city, None)
                    if same_path(cfg.get("repo"), clone):
                        cfg.pop("repo", None)   # the legacy "last used" pointer would re-adopt the clone
                if city:
                    runtime.update_config(forget)
                self._json({"ok": True})
                return
            if path == "/api/setup/fork":
                try:
                    nwo, err = create_fork(s.account.token())
                except (urllib.error.URLError, OSError):
                    nwo, err = None, tr("hub.err_github_offline",
                                        "GitHub could not be reached. Check the internet connection and try again.")
                if nwo:
                    s.fork.set(nwo, 60)
                    if s.hub is not None:
                        # account mode: the copy was just made for the chosen account — uploads go there
                        s.hub.ensure_origin(nwo)
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
                self._json(s.hub.launch(body["tool"]))
            elif path == "/api/feedback":
                self._json(s.hub.submit_feedback(body))
            elif path == "/api/update/fetch":
                self._json({"ok": True, "fetch": s.updates.fetch_async()})
            elif path == "/api/shortcut":
                try:
                    self._json(create_city_shortcut(s.hub.root))
                except Exception as e:
                    self._error(tr("hub.shortcut_failed", "Could not create the icon: {reason}", reason=str(e)))
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
SESSION = Session()


def setup_console_messages(saved_auth: bool) -> list[str]:
    """Make the pre-clone wait look like terminal guidance, not an abnormal stop.

    The setup screen quotes this wording, so emit it in the same language as the screen.
    """
    lines = ["  " + tr("hub.console_setup_running",
                       "Status: initial setup in progress (normal — not stopped)")]
    if saved_auth:
        lines.append("  " + tr(
            "hub.console_saved_auth",
            "The account recorded for this city is used. "
            "The account screen may be skipped"))
    lines += [
        "  " + tr("hub.console_follow_browser", "Follow the instructions in the browser"),
        "  " + tr("hub.console_terminal_waiting",
                  "This terminal stays open to run the browser screen"),
        "  " + tr("hub.console_resume",
                  "To resume later, just open the same launcher file again"),
    ]
    return lines


def main() -> None:
    runtime.console_safe()
    # Nothing from the person's shell may steer git (identity overrides, askpass programs,
    # repository routing): the clone's config and the city's account are the only sources.
    accounts.scrub_git_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="local clone of sample-tokyo-station (auto-detected if omitted)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # Clone location: explicit → ancestor detection → the remembered clone OF THIS CITY.
    # A remembered clone of another city is never adopted (the start script's city
    # selects the clone; the clone's 4dcitygml.json defines the city).
    city = runtime.requested_city()
    repo_root = args.repo or runtime.detect_repo()
    if repo_root is not None and args.repo is None and city and runtime.clone_city(repo_root) != city:
        repo_root = None
    if repo_root is None:
        if city:
            repo_root = runtime.saved_clone_for(city)
        else:
            saved = runtime.read_config().get("repo")
            if saved and runtime.has_building_data(Path(saved)):
                repo_root = Path(saved)

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
    banner = ["citygml-hub: {url}"] + (setup_console_messages(bool(SESSION.token())) if SESSION.hub is None else [])
    runtime.serve(Handler, port, banner, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
