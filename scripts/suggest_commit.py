#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Generate recommended commit message for building commits from changed buildings (W7 / traceability).

Backbone: normal update is 1 commit = 1 `uro:buildingID`. Multiple building commits are bundled into one PR,
but merge commits preserve individual commits to main. By **embedding changed building ID in `Building:` trailer**
in that commit, standard git history features **work at building granularity**:

- `git log --grep "Building: <id>"` … list of PRs/commits that changed that building
- `git blame` / `git bisect` / `git revert <sha>` … trace/revert at building granularity (W6 removes churn, so accurate)

**Key is stable ID `uro:buildingID`** (e.g. 13101-bldg-3728), prioritized over gml:id.
gml:id is per-file UUID regenerated at rebuild. Using stable ID allows continuous tracing across
rebuilds and renames (gml:id change, buildingID unchanged). Fall back to gml:id only if unresolvable.

Do **NOT** use building ID as labels (thousands of variants cause label noise, blame/bisect won't see them anyway).
Commit trailer is the standard approach.

Usage (CI: pass pre-calculated gml:id list and changed .gml):
    python scripts/suggest_commit.py --classification single \
        --modified /tmp/M --added /tmp/A --deleted /tmp/D --renamed /tmp/R \
        --sources changed_gml.txt
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.reconstruct_minimal import building_spans  # noqa: E402

MARKER = "<!-- citygml-suggested-commit -->"
_BUILDINGID_RE = re.compile(rb"<(?:\w+:)?buildingID>([^<]+)</(?:\w+:)?buildingID>")

# Classification -> commit type (Conventional Commits style).
_TYPE = {
    "single": "update",
    "multi-modified": "update",
    "lifecycle": "lifecycle",
    "rename": "chore",
    "none": "chore",
}


def _read_ids(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def building_id_map(gml_paths: list[Path]) -> dict[str, str]:
    """gml:id -> uro:buildingID (stable ID) mapping, resolved from the changed .gml files (head side)."""
    m: dict[str, str] = {}
    for p in gml_paths:
        if not p.exists():
            continue
        raw = p.read_bytes()
        for gid, (s, e) in building_spans(raw).items():
            hit = _BUILDINGID_RE.search(raw, s, e)
            if hit:
                m[gid] = hit.group(1).decode("utf-8").strip()
    return m


def build_message(
    modified: list[str],
    added: list[str],
    deleted: list[str],
    renamed: list[str],
    classification: str,
    id_map: dict[str, str] | None = None,
) -> str:
    """Return the raw body of the suggested commit message (subject + trailers). Resolved to stable IDs via id_map."""
    id_map = id_map or {}

    def disp(gid: str) -> str:  # prefer the stable ID, fall back to gml:id
        return id_map.get(gid, gid)

    ctype = _TYPE.get(classification, "update")
    all_ids = sorted(set(modified) | set(added) | set(deleted) | set(renamed))
    scope = disp(all_ids[0]) if len(all_ids) == 1 else f"{len(all_ids)} buildings"
    lines = [f"{ctype}({scope}): <fill in a summary of the change>", ""]
    if classification == "lifecycle":
        lines.append("<fill in why the addition/deletion, merge, split, or rebuild was done>")
        lines.append("")
    if renamed:
        lines.append("<gml:id-only change (content unchanged, rename). Fill in the intent>")
        lines.append("")
    # git trailers (one building per line -> easy to grep). Keyed by the stable ID.
    for bid in sorted(modified):
        lines.append(f"Building: {disp(bid)}")
    for bid in sorted(added):
        lines.append(f"Building-Added: {disp(bid)}")
    for bid in sorted(deleted):
        lines.append(f"Building-Deleted: {disp(bid)}")
    for bid in sorted(renamed):
        # A rename keeps buildingID unchanged, so the same key tracks it continuously (only gml:id changed).
        lines.append(f"Building: {disp(bid)}")
    return "\n".join(lines).rstrip() + "\n"


def render_comment(message: str, classification: str, n: int, resolved: bool) -> str:
    """PR comment (Markdown with marker)."""
    key = "uro:buildingID (stable ID)" if resolved else "gml:id"
    return (
        f"{MARKER}\n"
        f"## 🧾 Suggested commit message (for building commits)\n\n"
        f"Using the message below for building commits makes"
        f" `git log --grep`, `blame`, `bisect`, and `revert` work **per building**"
        f" (classification: `{classification}` / {n} target building(s) / key: {key}).\n\n"
        f"```\n{message}```\n\n"
        f"<sub>The building ID goes into the `Building:` trailer (not into labels, to avoid flooding)."
        f" Being a stable ID (buildingID), it can be tracked across rebuilds and renames."
        f" Only fill in the summary (and the reason for lifecycle changes).</sub>\n"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--classification", required=True)
    p.add_argument("--modified", type=Path, help="File listing modified building gml:ids")
    p.add_argument("--added", type=Path, help="File listing added building gml:ids")
    p.add_argument("--deleted", type=Path, help="File listing deleted building gml:ids")
    p.add_argument("--renamed", type=Path, help="File listing renamed (new gml:id)")
    p.add_argument("--sources", type=Path, help="File listing changed .gml paths (for buildingID resolution)")
    args = p.parse_args(argv)

    modified = _read_ids(args.modified)
    added = _read_ids(args.added)
    deleted = _read_ids(args.deleted)
    renamed = _read_ids(args.renamed)
    n = len(set(modified) | set(added) | set(deleted) | set(renamed))
    if n == 0:
        return 0  # no building changes -> no output (not a comment-posting target)

    id_map: dict[str, str] = {}
    if args.sources:
        gml_paths = [Path(x) for x in _read_ids(args.sources)]
        id_map = building_id_map(gml_paths)

    msg = build_message(modified, added, deleted, renamed, args.classification, id_map)
    sys.stdout.write(render_comment(msg, args.classification, n, resolved=bool(id_map)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
