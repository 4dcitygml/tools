#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Refuse to push a release tag unless the production approval gate is in place.

A release workflow publishes from one job that runs in the `production`
environment (docs/production-protection.md). The environment's rules are what
make the deployment approval happen; created too late, or with the wrong
settings, the release would publish unapproved or never publish. This script
reads the repository through the GitHub API and pushes the tag only when every
condition holds:

1. the tag name belongs to a release series (hub-v, tools-v: scripts/release_tags.py)
   and does not exist yet;
2. the commit is on `main` (reachable from it);
3. optionally, the commit's tree id equals the tree that was rehearsed
   (`--expect-tree`): the port to production is then byte-identical;
4. the workflow of that series at that commit calls the one publishing workflow
   (release-publish.yml), whose job declares `environment: production`;
5. the `production` environment exists with at least one required reviewer,
   self-review allowed (one maintainer account), and a deployment policy that
   admits the tag; a policy for `main` is only advised (manual re-publishing
   from the branch).

Usage:
    release_guard.py --repo 4dcitygml/tools --tag tools-v1.3.0 --commit <sha> [--expect-tree <sha>] [--push]

Without --push nothing is written; with --push the tag is created on the
commit and pushed to `origin` of the current checkout. Uses the `gh` CLI for
the API (the operator's own authentication).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import release_tags  # noqa: E402

SERIES = {"hub": "release-hub.yml", "tools": "release-tools.yml"}
PUBLISH = "release-publish.yml"   # the one publishing job of both series
ENVIRONMENT = "production"


def gh_api(path: str):
    """GET through gh; returns the decoded JSON, or None on 404."""
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if proc.returncode != 0:
        if "404" in proc.stderr or "Not Found" in proc.stdout:
            return None
        raise RuntimeError(f"gh api {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def workflow_at(repo: str, name: str, commit: str) -> "str | None":
    import base64
    content = gh_api(f"repos/{repo}/contents/.github/workflows/{name}?ref={commit}")
    return base64.b64decode(content["content"]).decode("utf-8") if content and content.get("content") else None


def check_tag_name(tag: str) -> list[str]:
    return [] if release_tags.parse(tag) else [f"{tag} is not a hub-v / tools-v release tag"]


def check_environment(env: dict | None, policies: list | None, tag: str) -> list[str]:
    """The rules the environment must carry (pure function over API payloads)."""
    problems = []
    if not env:
        return [f"environment `{ENVIRONMENT}` does not exist: create it with its rules before tagging"]
    reviewers = [r for r in env.get("protection_rules", []) if r.get("type") == "required_reviewers"]
    if not reviewers or not any(r.get("reviewers") for r in reviewers):
        problems.append("no required reviewer on the environment: the release would publish unapproved")
    if any(r.get("prevent_self_review") for r in reviewers):
        problems.append("self-review is prevented: the single maintainer account could never approve")
    policy = env.get("deployment_branch_policy")
    if not policy:
        problems.append("no deployment branch/tag policy: restrict deployments to the release tags")
    elif policy.get("custom_branch_policies"):
        names = [(p.get("name"), p.get("type", "branch")) for p in (policies or [])]
        if not any(t == "tag" and fnmatch.fnmatchcase(tag, n or "") for n, t in names):
            problems.append(f"no deployment tag policy admits {tag} (policies: {names or 'none'})")
        if not any(t == "branch" and n == "main" for n, t in names):
            print("note: no policy for branch `main`: a manual re-publish must be dispatched from the tag, not from main")
    return problems


def check_workflow(text: "str | None", publish: "str | None", series: str) -> list[str]:
    """The series workflow's release job calls the one publishing workflow, whose job runs in the
    production environment (the approval)."""
    if text is None:
        return [f"{SERIES[series]} not found at the commit"]
    job = text[text.find("\n  release:\n"):] if "\n  release:\n" in text else ""
    if f"uses: ./.github/workflows/{PUBLISH}" not in job:
        return [f"{SERIES[series]} at the commit: its release job does not call {PUBLISH}"]
    if publish is None or f"environment: {ENVIRONMENT}" not in publish:
        return [f"{PUBLISH} at the commit has no `environment: {ENVIRONMENT}` on its job"]
    return []


def notes(tag: str) -> str:
    """The release notes, carried by the annotated tag (the release is created --notes-from-tag)."""
    version = tag.split("-", 1)[1]
    if tag.startswith("hub-"):
        return (f"citygml-hub {version}: install or update with the one-line command in your city's README (it runs "
                "citygml.sh / citygml.ps1 of the latest hub release), or from the hub's update banner. The zips are their "
                "payload; GitHub publishes each asset's SHA-256 digest, which the installers check.")
    return (f"4dcitygml tools {version}: city processing, CI, schemas, semantic definitions and documentation "
            "(dependencies: requirements-ci.txt). A city's CI pins the tools commit in its 4dcitygml.json.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, help="owner/name of the production repository")
    p.add_argument("--tag", required=True)
    p.add_argument("--commit", required=True, help="the commit the tag will point to (full sha)")
    p.add_argument("--expect-tree", help="tree id the commit must have (the rehearsed tree)")
    p.add_argument("--push", action="store_true", help="create and push the tag when every check passes")
    a = p.parse_args(argv)
    problems = check_tag_name(a.tag)
    series = a.tag.split("-v")[0]
    if gh_api(f"repos/{a.repo}/git/ref/tags/{a.tag}") is not None:
        problems.append(f"tag {a.tag} already exists")
    commit = gh_api(f"repos/{a.repo}/commits/{a.commit}")
    if not commit:
        problems.append(f"commit {a.commit} not found in {a.repo}")
    else:
        cmp = gh_api(f"repos/{a.repo}/compare/main...{a.commit}") or {}
        if cmp.get("status") not in ("identical", "behind"):
            problems.append(f"commit {a.commit[:12]} is not on main (compare status: {cmp.get('status')})")
        if a.expect_tree and commit["commit"]["tree"]["sha"] != a.expect_tree:
            problems.append(f"tree {commit['commit']['tree']['sha'][:12]} differs from the rehearsed tree {a.expect_tree[:12]}")
        if series in SERIES:
            problems += check_workflow(workflow_at(a.repo, SERIES[series], a.commit), workflow_at(a.repo, PUBLISH, a.commit), series)
    env = gh_api(f"repos/{a.repo}/environments/{ENVIRONMENT}")
    policies = (gh_api(f"repos/{a.repo}/environments/{ENVIRONMENT}/deployment-branch-policies") or {}).get("branch_policies") if env else None
    problems += check_environment(env, policies, a.tag)
    for line in problems:
        print(f"FAIL {line}")
    if problems:
        print(f"{len(problems)} problem(s): the tag is not pushed")
        return 1
    print(f"OK   {a.repo}: {a.tag} -> {a.commit[:12]} may be released; the release job will wait for the approval")
    if a.push:
        subprocess.run(["git", "tag", "-a", a.tag, a.commit, "-m", notes(a.tag)], check=True)
        subprocess.run(["git", "push", "origin", a.tag], check=True)
        print(f"pushed {a.tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
