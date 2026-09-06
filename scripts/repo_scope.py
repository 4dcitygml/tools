#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""What a city repository accepts — Exchange Contract A11 (`file-scope` gate).

Cities distribute data, documents and configuration; every line of code that
runs on a contributor's or a resident's computer comes from 4dcitygml/tools
releases. This script classifies the files a PR changes and rejects anything
outside that scope, so that a maintainer never has to read shell or Python in a
city PR. Rejections come with the guidance where such a change belongs.

Accepted (A11):
  data      files under the data directories of 4dcitygml.json (or */udx/ in the
            PLATEAU layout), provenance/**
  docs      docs/**, README/LICENSE/NOTICE/CONTRIBUTING/SUPPORT/CHANGELOG*, *.md at the root
  config    4dcitygml.json, theme.json, the logo image, .gitignore, .gitattributes,
            .github/CODEOWNERS, .github/PULL_REQUEST_TEMPLATE.md, .github/ISSUE_TEMPLATE/**
  pin       .github/workflows/*.yml when the only changed lines are `CITYGML_TOOLS_REF:`
            values and the new value is a commit that a `tools-v*` tag of 4dcitygml/tools points to
  tooling   any other change under .github/** — only when a maintainer applied the
            `tooling` label (A9); recorded in the report
Everything else (install/**, tools/**, scripts, executables anywhere) is rejected.

Usage:
  repo_scope.py --repo . --base-sha B --head-sha H [--event event.json]
                [--json-output F] [--tools-repo 4dcitygml/tools]
Exit 0 when every change is accepted, 1 otherwise. Environment for tests:
  CITYGML_TOOLS_TAGS_JSON  a file with the tags API payload instead of the network
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

CODE_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".command", ".ps1", ".bat", ".cmd", ".exe", ".dll",
                 ".js", ".mjs", ".ts", ".rb", ".php", ".pl", ".jar", ".dylib", ".so", ".app"}
DOC_ROOT_NAMES = re.compile(r"^(README|LICENSE|NOTICE|CONTRIBUTING|SUPPORT|CHANGELOG|SECURITY)([.-].*)?$", re.I)
CONFIG_PATHS = {"4dcitygml.json", "theme.json", ".gitignore", ".gitattributes",
                ".github/CODEOWNERS", ".github/PULL_REQUEST_TEMPLATE.md"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico"}
PIN_LINE = re.compile(r"^[+-]\s*CITYGML_TOOLS_REF:\s*([0-9a-f]{40})\b.*$")
TOOLING_LABEL = "tooling"


def _git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def changed_files(repo, base, head) -> list:
    out = _git(repo, "diff", "--name-only", "--diff-filter=ACMRD", base, head)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def city_config(repo) -> dict:
    try:
        return json.loads((Path(repo) / "4dcitygml.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def data_dirs(cfg: dict) -> list:
    dirs = [str(d).strip("/") for d in (cfg.get("data_dirs") or []) if isinstance(d, str) and d.strip("/")]
    return dirs


def is_data(path: str, dirs: list) -> bool:
    parts = Path(path).parts
    if any(path == d or path.startswith(d + "/") for d in dirs):
        return True
    if len(parts) >= 3 and parts[1] == "udx":          # PLATEAU layout: <dataset>/udx/<theme>/...
        return True
    return path.startswith("provenance/")


def is_docs(path: str) -> bool:
    if path.startswith("docs/"):
        return True
    if "/" not in path and (DOC_ROOT_NAMES.match(path) or path.lower().endswith(".md")):
        return True
    return False


def is_config(path: str, cfg: dict) -> bool:
    if path in CONFIG_PATHS or path.startswith(".github/ISSUE_TEMPLATE/"):
        return True
    logo = str(cfg.get("logo") or "logo.png")
    if path == logo or ("/" not in path and Path(path).suffix.lower() in IMAGE_SUFFIXES):
        return True
    return False


def pin_only_diff(repo, base, head, path: str) -> "str | None":
    """The new CITYGML_TOOLS_REF when a workflow diff changes nothing else; None otherwise."""
    diff = _git(repo, "diff", "--unified=0", base, head, "--", path)
    new_sha = None
    for line in diff.splitlines():
        if not line or line.startswith(("---", "+++", "@@", "diff ", "index ")):
            continue
        if line[0] not in "+-":
            continue
        m = PIN_LINE.match(line)
        if not m:
            return None
        if line.startswith("+"):
            new_sha = m.group(1)
    return new_sha


def tools_tag_commits(tools_repo: str) -> "set | None":
    """Commits that tools-v* tags point to (None when the API is unavailable)."""
    try:
        override = os.environ.get("CITYGML_TOOLS_TAGS_JSON")
        if override:
            tags = json.loads(Path(override).read_text(encoding="utf-8"))
        else:
            req = urllib.request.Request(f"https://api.github.com/repos/{tools_repo}/tags?per_page=100",
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "citygml-ci"})
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=20) as r:
                tags = json.loads(r.read().decode("utf-8"))
        return {t["commit"]["sha"] for t in tags if str(t.get("name", "")).startswith("tools-v")}
    except Exception:
        return None


def pr_labels(event_path: "str | None") -> set:
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
        return {str(x.get("name")) for x in (event.get("pull_request") or {}).get("labels", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def classify(repo, base, head, files, cfg, labels, tools_repo="4dcitygml/tools", tag_commits=None) -> list:
    dirs = data_dirs(cfg)
    rows = []
    tag_cache = {"value": tag_commits, "loaded": tag_commits is not None}
    for path in files:
        row = {"path": path, "category": None, "ok": False, "note": ""}
        suffix = Path(path).suffix.lower()
        if is_data(path, dirs) and suffix not in CODE_SUFFIXES:
            row.update(category="data", ok=True)
        elif is_docs(path) and suffix not in CODE_SUFFIXES:
            row.update(category="docs", ok=True)
        elif is_config(path, cfg):
            row.update(category="config", ok=True)
        elif path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            sha = pin_only_diff(repo, base, head, path)
            if sha:
                if not tag_cache["loaded"]:
                    tag_cache["value"] = tools_tag_commits(tools_repo)
                    tag_cache["loaded"] = True
                commits = tag_cache["value"]
                if commits is None:
                    row.update(category="pin", ok=False, note="could not verify the tools pin against the tools tags")
                elif sha in commits:
                    row.update(category="pin", ok=True, note=f"CITYGML_TOOLS_REF → {sha[:12]} (a tools-v release)")
                else:
                    row.update(category="pin", ok=False, note=f"{sha[:12]} is not a commit any tools-v tag points to")
            elif TOOLING_LABEL in labels:
                row.update(category="tooling", ok=True, note=f"accepted under the maintainer label `{TOOLING_LABEL}`")
            else:
                row.update(category="rejected", ok=False, note="workflow changes are limited to the CITYGML_TOOLS_REF pin (or the maintainer label `tooling`)")
        elif path.startswith(".github/") and TOOLING_LABEL in labels:
            row.update(category="tooling", ok=True, note=f"accepted under the maintainer label `{TOOLING_LABEL}`")
        else:
            reason = ("executable code" if suffix in CODE_SUFFIXES else "outside the data, docs and configuration paths")
            row.update(category="rejected", ok=False, note=reason)
        rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    ap.add_argument("--json-output", default=None)
    ap.add_argument("--tools-repo", default=os.environ.get("CITYGML_TOOLS_REPO") or "4dcitygml/tools")
    args = ap.parse_args(argv)
    files = changed_files(args.repo, args.base_sha, args.head_sha)
    cfg = city_config(args.repo)
    rows = classify(args.repo, args.base_sha, args.head_sha, files, cfg, pr_labels(args.event), args.tools_repo)
    result = {"ok": all(r["ok"] for r in rows), "files": rows,
              "rejected": [r for r in rows if not r["ok"]], "label": TOOLING_LABEL in pr_labels(args.event)}
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in rows:
        mark = "OK " if r["ok"] else "NG "
        print(f"{mark}{r['category']:9} {r['path']}" + (f"  ({r['note']})" if r["note"] else ""))
    if not result["ok"]:
        print("::error::This city repository accepts data, documents and configuration only. "
              "Tool and script changes belong in 4dcitygml/tools (Exchange Contract A11).")
        return 1
    print("Repository scope: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
