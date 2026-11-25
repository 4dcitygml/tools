#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Generate recommended commit message for building commits from changed buildings (W7 / traceability).

Backbone: normal update is 1 commit = 1 building. Multiple building commits are bundled into one PR,
but merge commits preserve individual commits to main. By **embedding changed building ID in `Building:` trailer**
in that commit, standard git history features **work at building granularity**:

- `git log --grep "Building: <id>"` … list of PRs/commits that changed that building
- `git blame` / `git bisect` / `git revert <sha>` … trace/revert at building granularity (W6 removes churn, so accurate)

**The key is the building's stable ID under the city's rule** (4dcitygml.json `building_id`,
read at the PR's base: a code-list attribute, the gml:id or a generic attribute). The IDs are the
commit scope gate's own (its PR scope, U4), so a suggestion is what the gate then expects. A
gml:id may be regenerated at a rebuild; the stable ID carries the building across rebuilds and
renames.

Do **NOT** use building ID as labels (thousands of variants cause label noise, blame/bisect won't see them anyway).
Commit trailer is the standard approach.

Usage (CI: the commit scope gate's result):
    python scripts/suggest_commit.py --scope-json "$CITYGML_RUN/citygml_commit_scope.json"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MARKER = "<!-- citygml-suggested-commit -->"

# Classification -> commit type (Conventional Commits style).
_TYPE = {
    "single": "update",
    "multi-modified": "update",
    "lifecycle": "lifecycle",
    "rename": "chore",
    "none": "chore",
}


def build_message(
    modified: list[str],
    added: list[str],
    deleted: list[str],
    renamed: list[str],
    classification: str,
) -> str:
    """Return the raw body of the suggested commit message (subject + trailers), by stable ID."""
    ctype = _TYPE.get(classification, "update")
    all_ids = sorted(set(modified) | set(added) | set(deleted) | set(renamed))
    scope = all_ids[0] if len(all_ids) == 1 else f"{len(all_ids)} buildings"
    lines = [f"{ctype}({scope}): <fill in a summary of the change>", ""]
    if classification == "lifecycle":
        lines.append("<fill in why the addition/deletion, merge, split, or rebuild was done>")
        lines.append("")
    if renamed:
        lines.append("<gml:id-only change (content unchanged, rename). Fill in the intent>")
        lines.append("")
    # git trailers (one building per line -> easy to grep). Keyed by the stable ID.
    if classification == 'lifecycle' and len(all_ids) > 1:
        lines.append('Change-Type: lifecycle')
        lines.append('Lifecycle-Manifest: <paste the reference printed by lifecycle_manifest.py>')
    for bid in sorted(modified):
        lines.append(f"Building: {bid}")
    for bid in sorted(added):
        lines.append(f"Building-Added: {bid}")
    for bid in sorted(deleted):
        lines.append(f"Building-Deleted: {bid}")
    for bid in sorted(renamed):
        # A rename keeps the stable ID unchanged, so the same key tracks it continuously (only gml:id changed).
        lines.append(f"Building: {bid}")
    return "\n".join(lines).rstrip() + "\n"


def render_comment(message: str, classification: str, n: int, rule: str = "gml:id") -> str:
    """PR comment (Markdown with marker); rule names the city's stable ID."""
    key = f"{rule} (stable ID)"
    return (
        f"{MARKER}\n"
        f"## 🧾 Suggested commit message (for building commits)\n\n"
        f"Using the message below for building commits makes"
        f" `git log --grep`, `blame`, `bisect`, and `revert` work **per building**"
        f" (classification: `{classification}` / {n} target building(s) / key: {key}).\n\n"
        f"```\n{message}```\n\n"
        f"<sub>The building ID goes into the `Building:` trailer (not into labels, to avoid flooding)."
        f" Being the city's stable building ID, it can be tracked across rebuilds and renames."
        f" Fill in the summary. For a lifecycle event, record its evidence and old/new relation"
        f" in a Lifecycle-Manifest and paste its SHA-256 reference; CI does not infer the real-world relation.</sub>\n"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope-json", type=Path, required=True, help="the commit scope gate's result (its PR scope)")
    args = p.parse_args(argv)
    try:
        scope = json.loads(args.scope_json.read_text(encoding="utf-8")).get("scope") or {}
    except (OSError, ValueError):
        scope = {}
    ids = {kind: list(scope.get(kind) or []) for kind in ("modified", "added", "deleted", "renamed")}
    n = len(set().union(*ids.values()))
    if n == 0:
        return 0  # no building changes -> no output (not a comment-posting target)
    classification = str(scope.get("class") or "none")
    msg = build_message(**ids, classification=classification)
    sys.stdout.write(render_comment(msg, classification, n, str(scope.get("rule") or "gml:id")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
