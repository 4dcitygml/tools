#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Reviewability lint (W3 / feature list D-1).

Analyzes PR changes with W1 (`diff_citygml`) and formats **operational warnings within a single PR**
to PR comment Markdown. Not multi-party concurrent conflict resolution (paper explicitly marks as future work).

Detected warnings:
- D-1-a Large-scale changes … changed building count exceeds threshold (default 5), recommend PR split (demo: PR-E)
- D-1-b Lifecycle candidates … prompt explicit reason for added/deleted buildings (demo: PR-C)
- D-1-c Unnecessary ID changes … if deleted and added IDs have identical attribute/geometry hash, suspect ID change (demo: PR-D)
- D-1-d Reviewability Markdown … format above as warning list + change summary table

Posting is independently upserted by workflow (W5b) with separate marker from preview/summary.

Usage:
    python scripts/reviewability_lint.py OLD.gml NEW.gml
    python scripts/reviewability_lint.py --repo R --base-sha B --head-sha H --file-list L
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_constants import LARGE_CHANGE_THRESHOLD, LINT_MARKER  # noqa: E402
from scripts.diff_citygml import attribute_diffs, load_buildings  # noqa: E402
from scripts.extract_building_preview import _get_file_at_sha  # noqa: E402  (shared git show helper / X-2)

BuildingMap = dict  # gml:id -> (attrs, geom_hash)


def _same_building(a: tuple, b: tuple) -> bool:
    """True when attributes (numerically normalized) and geometry hash are identical (suspected-ID-change check)."""
    (a_attrs, a_geom), (b_attrs, b_geom) = a, b
    return a_geom == b_geom and not attribute_diffs(a_attrs, b_attrs)


def analyze_file(old_map: BuildingMap, new_map: BuildingMap, path: str) -> dict:
    """Return one file's change breakdown and lifecycle/id-change warnings.

    Large-change detection (D-1-a) is judged on the PR-wide total, so only counts are returned here.
    """
    old_ids, new_ids = set(old_map), set(new_map)
    added = sorted(new_ids - old_ids)
    deleted = sorted(old_ids - new_ids)
    modified = sorted(
        bid
        for bid in (old_ids & new_ids)
        if old_map[bid][1] != new_map[bid][1]
        or attribute_diffs(old_map[bid][0], new_map[bid][0])
    )

    warnings: list[dict] = []

    # D-1-c unnecessary ID change: a deleted ID and an added ID with identical attributes and geometry
    id_pairs: list[tuple[str, str]] = []
    used_added: set[str] = set()
    for d in deleted:
        for a in added:
            if a in used_added:
                continue
            if _same_building(old_map[d], new_map[a]):
                id_pairs.append((d, a))
                used_added.add(a)
                break
    for old_id, new_id in id_pairs:
        warnings.append({"type": "id_change", "file": path, "old_id": old_id, "new_id": new_id})

    # D-1-b lifecycle candidates: additions/deletions outside ID-change pairs need a reason
    paired = {d for d, _ in id_pairs} | {a for _, a in id_pairs}
    for a in added:
        if a not in paired:
            warnings.append({"type": "lifecycle_added", "file": path, "id": a})
    for d in deleted:
        if d not in paired:
            warnings.append({"type": "lifecycle_deleted", "file": path, "id": d})

    return {
        "file": path,
        "counts": {"added": len(added), "deleted": len(deleted), "modified": len(modified)},
        "warnings": warnings,
    }


def _changed_total(files: list[dict]) -> int:
    return sum(sum(f["counts"].values()) for f in files)


_WARNING_RENDER = {
    "id_change": lambda w: (
        f"🆔 **Suspected unnecessary ID change** (`{w['file']}`): `{w['old_id']}` → `{w['new_id']}` "
        f"— attributes and geometry are identical. Please confirm the ID change is needed."
    ),
    "lifecycle_added": lambda w: (
        f"♻️ **Reason required for added building** (`{w['file']}`): `{w['id']}` "
        f"— please state the reason for the addition in the PR body."
    ),
    "lifecycle_deleted": lambda w: (
        f"♻️ **Reason required for deleted building** (`{w['file']}`): `{w['id']}` "
        f"— please state the reason for the deletion in the PR body."
    ),
}


# Upper bound on individually listed warnings in the comment. Bulk PRs (annual
# source updates, baselines) can carry thousands of per-building warnings; the
# full list would exceed the GitHub comment size limit (65,536 characters) and
# the comment would never be posted. The count in the heading stays exact.
MAX_LISTED_WARNINGS = 50


def render_markdown(files: list[dict], threshold: int = LARGE_CHANGE_THRESHOLD) -> str:
    """Render the change-breakdown table + warning list as Markdown (D-1-d, with marker)."""
    warnings: list[dict] = [w for f in files for w in f["warnings"]]
    total = _changed_total(files)

    lines: list[str] = [LINT_MARKER, "## 🔎 Reviewability check", ""]

    # D-1-a large change (judged on the PR-wide total)
    big = total > threshold

    if not warnings and not big:
        lines.append("✅ No operational warnings.")
        lines.append("")
    else:
        lines.append(f"### ⚠️ Warnings ({len(warnings) + (1 if big else 0)})")
        lines.append("")
        if big:
            lines.append(
                f"🔶 **Large change**: {total} changed buildings exceed the threshold of {threshold}."
                f" Splitting the PR is recommended to keep reviews manageable."
            )
        for w in warnings[:MAX_LISTED_WARNINGS]:
            lines.append(_WARNING_RENDER[w["type"]](w))
        if len(warnings) > MAX_LISTED_WARNINGS:
            lines.append(
                f"… and {len(warnings) - MAX_LISTED_WARNINGS} more warnings not listed "
                f"(only the first {MAX_LISTED_WARNINGS} are shown to keep this comment postable)."
            )
        lines.append("")

    if files:
        lines.append("### Change breakdown")
        lines.append("")
        lines.append("| File | Added | Deleted | Modified |")
        lines.append("|---|---|---|---|")
        for f in files:
            c = f["counts"]
            lines.append(f"| `{f['file']}` | {c['added']} | {c['deleted']} | {c['modified']} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def collect_ci_files(
    repo: Path, base_sha: str, head_sha: str, gml_files: list[str]
) -> list[dict]:
    """Diff base/head for each changed .gml and analyze only the files with changes."""
    files: list[dict] = []
    for rel_path in gml_files:
        old_map = load_buildings(_get_file_at_sha(repo, base_sha, rel_path))
        new_map = load_buildings(_get_file_at_sha(repo, head_sha, rel_path))
        result = analyze_file(old_map, new_map, rel_path)
        if sum(result["counts"].values()) > 0:
            files.append(result)
    return files


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("old_gml", type=Path, nargs="?")
    p.add_argument("new_gml", type=Path, nargs="?")
    p.add_argument("--threshold", type=int, default=LARGE_CHANGE_THRESHOLD)
    p.add_argument("--repo", type=Path, default=None)
    p.add_argument("--base-sha", default=None)
    p.add_argument("--head-sha", default=None)
    p.add_argument("--file-list", type=Path, default=None)
    args = p.parse_args(argv)

    if args.file_list is not None:
        if not (args.base_sha and args.head_sha):
            p.error("--base-sha and --head-sha required when using --file-list.")
        listed = (ln.strip() for ln in args.file_list.read_text(encoding="utf-8").splitlines())
        gml_files = [p for p in listed if p.endswith(".gml")]
        repo = args.repo or REPO_ROOT
        files = collect_ci_files(repo, args.base_sha, args.head_sha, gml_files)
    elif args.old_gml is not None and args.new_gml is not None:
        old_map = load_buildings(args.old_gml)
        new_map = load_buildings(args.new_gml)
        files = [analyze_file(old_map, new_map, str(args.new_gml))]
        if sum(files[0]["counts"].values()) == 0:
            files = []
    else:
        p.error("Specify OLD.gml NEW.gml or --file-list (CI).")

    # If there are no changed files, output nothing (do not post)
    if not files:
        return 0
    sys.stdout.write(render_markdown(files, threshold=args.threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
