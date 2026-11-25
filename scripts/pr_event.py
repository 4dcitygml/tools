#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The PR an analysis judges, read once (Exchange Contract A10, D23).

An event payload keeps the PR as it was sent: a re-run replays it, and a PR reopened after main
moved showed a base the PR no longer had (rehearsal, 2026-10-01). Every gate and the report's
context read the PR from one file instead: the payload's PR replaced by the PR as GitHub holds
it now, while its head is the commit under analysis (a newer head gets its own run); the
payload on any failure.

    EVENT="$(python3 scripts/pr_event.py "$GITHUB_EVENT_PATH")"   # writes the file, prints its path

The analysis driver writes it first and hands the path to the later steps as CITYGML_EVENT.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def live_pr(pr: dict, env=os.environ, fetch=None) -> dict:
    """The PR as GitHub holds it now (GET /repos/{repo}/pulls/{number}) while its head is the
    payload's; the payload otherwise."""
    repo, number = env.get("GITHUB_REPOSITORY", ""), pr.get("number")
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""
    if not repo or not number:
        return pr
    if fetch is None:
        def fetch(url):
            request = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {token}"} if token else {})})
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    try:
        live = fetch(f"{api}/repos/{repo}/pulls/{number}")
    except Exception:  # noqa: BLE001 - the payload is the fallback
        return pr
    if not isinstance(live, dict) or (live.get("head") or {}).get("sha") != (pr.get("head") or {}).get("sha"):
        return pr
    return live


def main(argv: list[str], env=os.environ, fetch=None) -> int:
    event = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    event["pull_request"] = live_pr(event.get("pull_request", {}), env, fetch)
    out = Path(env.get("CITYGML_RUN") or Path(env.get("RUNNER_TEMP", "/tmp")) / "citygml") / "event.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
