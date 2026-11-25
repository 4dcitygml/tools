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
  data      files under the data directories of 4dcitygml.json (a city that declares none:
            its *.gml files), provenance/**
  schema    the edition artifacts (code lists, schema profiles) in the folders the city's dialect
            pack names (the schema-update kind; none for plain CityGML)
  docs      docs/**, README/LICENSE/NOTICE/CONTRIBUTING/SUPPORT/CHANGELOG*, *.md at the root
  config    4dcitygml.json, theme.json (and their .example templates), the logo image,
            .gitignore, .gitattributes, .github/CODEOWNERS, .github/PULL_REQUEST_TEMPLATE.md,
            .github/ISSUE_TEMPLATE/**
  removed   any deleted file — removing code from a city repository is always accepted
  pin       4dcitygml.json when it changes `tools`: the new value is a commit that a `tools-v*`
            tag of 4dcitygml/tools points to (the tag is resolved when shown)
  tooling   any other change under .github/** — only when a maintainer applied the
            `tooling` label (A9); recorded in the report
Everything else (install/**, tools/**, scripts, executables anywhere) is rejected.

Usage:
  repo_scope.py --repo . --base-sha B --head-sha H [--event event.json]
                [--json-output F] [--tools-repo 4dcitygml/tools]
Exit 0 when every change is accepted, 1 otherwise. Environment for tests:
  CITYGML_TOOLS_GIT_URL    a git repository to read the tools-v* tags from instead of GitHub
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import city_declaration  # noqa: E402
from scripts.citygml_constants import tools_repo  # noqa: E402
from scripts.provenance_manifest import validate  # noqa: E402
from scripts.repo_git import blob, changed, git as _git  # noqa: E402

CODE_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".command", ".ps1", ".bat", ".cmd", ".exe", ".dll",
                 ".js", ".mjs", ".ts", ".rb", ".php", ".pl", ".jar", ".dylib", ".so", ".app"}
DOC_ROOT_NAMES = re.compile(r"^(README|LICENSE|NOTICE|CONTRIBUTING|SUPPORT|CHANGELOG|SECURITY)([.-].*)?$", re.I)
CONFIG_PATHS = {"4dcitygml.json", "theme.json", ".gitignore", ".gitattributes",
                ".github/CODEOWNERS", ".github/PULL_REQUEST_TEMPLATE.md"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico"}
TOOLING_LABEL = "tooling"


def changed_files(repo, base, head) -> list:
    """Every path the PR touches, a type change too (D-b7: --diff-filter=ACMRD missed a file
    turned into a symbolic link)."""
    return changed(repo, base, head)


def deleted_files(repo, base, head) -> set:
    """Paths removed by the change. A removed file cannot run: removal is always accepted (A11)."""
    out = _git(repo, "diff", "--name-only", "--diff-filter=D", base, head)
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def is_data(path: str, dirs: list) -> bool:
    """Data: under a declared data folder; a city that declares none has its CityGML files as data
    (v2.0.0: no layout is guessed); and the provenance manifests."""
    if dirs and any(path == d or path.startswith(d + "/") for d in dirs):
        return True
    if not dirs and path.endswith(".gml"):
        return True
    return path.startswith("provenance/")


def is_schema(path: str, declaration) -> bool:
    """An edition artifact: under a folder the city's dialect pack names (U11)."""
    return any(path.startswith(d + "/") for d in declaration.pack.schema_dirs)


def is_docs(path: str) -> bool:
    if path.startswith("docs/"):
        return True
    if "/" not in path and (DOC_ROOT_NAMES.match(path) or path.lower().endswith(".md")):
        return True
    return False


def is_config(path: str, declaration) -> bool:
    if path in CONFIG_PATHS or path.startswith(".github/ISSUE_TEMPLATE/"):
        return True
    if "/" not in path and path.endswith(".example") and path[:-len(".example")] in CONFIG_PATHS:
        return True   # the template's 4dcitygml.json.example / theme.json.example
    logo = declaration.logo or "logo.png"
    if path == logo or ("/" not in path and Path(path).suffix.lower() in IMAGE_SUFFIXES):
        return True
    return False


def pinned_tools(repo, base, declaration) -> "str | None":
    """The tools commit a PR pins (its declaration's `tools`, "" when it removes it), or None when
    the PR leaves the pin as the base has it."""
    old, new = city_declaration.read(repo, base).raw.get("tools"), declaration.raw.get("tools")
    return None if new == old else str(new or "")


def declaration_problems(repo, base, head) -> list:
    """What a changed declaration breaks in its schema (schemas/city/4dcitygml.schema.json) that the
    base's did not: what the PR writes must conform, what was there before is tolerated. A declaration
    the PR leaves as it is, or removes, is not judged; a changed one that is no JSON object is a
    violation. The readers stay tolerant (they ignore keys they do not know); this is the write side."""
    new, old = blob(repo, head, city_declaration.FILE), blob(repo, base, city_declaration.FILE)
    if new is None or new == old:
        return []

    def violations(raw):
        try:
            return validate(json.loads(raw), json.loads(city_declaration.SCHEMA.read_text(encoding="utf-8"))) if raw else []
        except ValueError:
            return ["$: not valid JSON"]
    return [v for v in violations(new) if v not in violations(old)]


def tools_tags(tools_repo: str) -> "dict | None":
    """The commits tools-v* tags point to -> the tag's name, from git itself (`git ls-remote
    --tags`: every tag, no API paging; an annotated tag counts by the commit it peels to). The
    tag of a pinned commit is resolved here when it is shown, never stored. None when git
    cannot read them."""
    url = os.environ.get("CITYGML_TOOLS_GIT_URL") or f"https://github.com/{tools_repo}.git"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    # A city that mirrors the tools privately reads them with CITYGML_TOOLS_TOKEN (a read-only
    # token for that mirror); otherwise the job's own token. It travels in git's environment
    # configuration, never on the command line.
    token = os.environ.get("CITYGML_TOOLS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://"):
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    try:
        listing = subprocess.run(["git", "ls-remote", "--tags", url, "refs/tags/tools-v*"], env=env,
                                 capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    direct, peeled = {}, {}
    for line in listing.splitlines():
        sha, _, ref = line.partition("\t")
        (peeled if ref.endswith("^{}") else direct)[ref.removesuffix("^{}")] = sha
    return {peeled.get(ref, sha): ref.removeprefix("refs/tags/") for ref, sha in sorted(direct.items())}


def pr_labels(event_path: "str | None") -> set:
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
        return {str(x.get("name")) for x in (event.get("pull_request") or {}).get("labels", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def classify(repo, base, head, files, declaration, labels, tools_repo="4dcitygml/tools", tag_commits=None, removed=None) -> list:
    dirs = declaration.data_dirs
    rows = []
    tag_cache = {"value": tag_commits, "loaded": tag_commits is not None}
    removed = set(removed or ())
    for path in files:
        row = {"path": path, "category": None, "ok": False, "note": ""}
        suffix = Path(path).suffix.lower()
        if path in removed:
            row.update(category="removed", ok=True, note="removed (a deleted file cannot run)")
        elif is_data(path, dirs) and suffix not in CODE_SUFFIXES:
            row.update(category="data", ok=True)
        elif is_schema(path, declaration) and suffix not in CODE_SUFFIXES:
            row.update(category="schema", ok=True)
        elif is_docs(path) and suffix not in CODE_SUFFIXES:
            row.update(category="docs", ok=True)
        elif path == city_declaration.FILE and (pin := pinned_tools(repo, base, declaration)) is not None:
            # the CI tools pin: the declaration's `tools`, a commit a tools-v tag points to (A11)
            if not tag_cache["loaded"]:
                tag_cache["value"] = tools_tags(tools_repo)
                tag_cache["loaded"] = True
            tags = tag_cache["value"]
            if not pin:
                row.update(category="pin", ok=False, note="the declaration must name the tools commit (`tools`)")
            elif tags is None:
                row.update(category="pin", ok=False, note="could not verify the tools pin against the tools tags")
            elif pin in tags:
                row.update(category="pin", ok=True, note=f"tools -> {pin[:12]} ({tags[pin]})")
            else:
                row.update(category="pin", ok=False, note=f"{pin[:12]} is not a commit any tools-v tag points to")
        elif is_config(path, declaration):
            row.update(category="config", ok=True)
        elif path.startswith(".github/") and TOOLING_LABEL in labels:
            row.update(category="tooling", ok=True, note=f"accepted under the maintainer label `{TOOLING_LABEL}`")
        elif path.startswith(".github/workflows/"):
            row.update(category="rejected", ok=False, note=f"workflow changes need the maintainer label `{TOOLING_LABEL}`; "
                                                          "the tools version is the `tools` key of 4dcitygml.json")
        else:
            reason = ("executable code" if suffix in CODE_SUFFIXES else "outside the data, docs and configuration paths")
            row.update(category="rejected", ok=False, note=reason)
        if path == city_declaration.FILE and row["ok"] and path not in removed and (bad := declaration_problems(repo, base, head)):
            row.update(ok=False, note="does not conform to schemas/city/4dcitygml.schema.json: " + "; ".join(bad))
        rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    ap.add_argument("--json-output", default=None)
    ap.add_argument("--tools-repo", default=tools_repo())
    args = ap.parse_args(argv)
    files = changed_files(args.repo, args.base_sha, args.head_sha)
    rows = classify(args.repo, args.base_sha, args.head_sha, files, city_declaration.read(args.repo), pr_labels(args.event), args.tools_repo,
                    removed=deleted_files(args.repo, args.base_sha, args.head_sha))
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
    from scripts.gate_result import guarded
    sys.exit(guarded(main))
