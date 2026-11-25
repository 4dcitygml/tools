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

from scripts import md_report  # noqa: E402
from scripts.md_report import changed_list  # noqa: E402
from scripts.citygml_constants import LARGE_CHANGE_THRESHOLD, LINT_MARKER  # noqa: E402
from scripts.diff_citygml import buildings_at, diff_maps, load_buildings  # noqa: E402

BuildingMap = dict  # gml:id -> (attrs, geom_hash)


def analyze_file(old_map: BuildingMap, new_map: BuildingMap, path: str) -> dict:
    """Return one file's change breakdown and lifecycle/id-change warnings, from diff_citygml's one
    classification and rename rule (U6). Large-change detection (D-1-a) is judged on the PR-wide
    total, so only counts are returned here (an id change counts as an addition and a deletion)."""
    entries = diff_maps(old_map, new_map)["buildings"]
    by = {s: [e for e in entries if e["status"] == s] for s in ("added", "deleted", "modified", "renamed")}
    # D-1-c unnecessary ID change; D-1-b lifecycle candidates: the other additions/deletions need a reason
    warnings = [{"type": "id_change", "file": path, "old_id": e["old_id"], "new_id": e["id"]} for e in by["renamed"]]
    warnings += [{"type": "lifecycle_added", "file": path, "id": e["id"]} for e in by["added"]]
    warnings += [{"type": "lifecycle_deleted", "file": path, "id": e["id"]} for e in by["deleted"]]
    n_renamed = len(by["renamed"])
    return {
        "file": path,
        "counts": {"added": len(by["added"]) + n_renamed, "deleted": len(by["deleted"]) + n_renamed,
                   "modified": len(by["modified"])},
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
        shown, rest = md_report.capped(warnings, MAX_LISTED_WARNINGS)
        lines += [_WARNING_RENDER[w["type"]](w) for w in shown]
        if rest:
            lines.append(
                f"… and {rest} more warnings not listed "
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

    return md_report.fit("\n".join(lines).rstrip() + "\n")


def collect_ci_files(
    repo: Path, base_sha: str, head_sha: str, gml_files: list[str]
) -> list[dict]:
    """Diff base/head for each changed .gml and analyze only the files with changes."""
    files: list[dict] = []
    for rel_path in gml_files:
        result = analyze_file(buildings_at(repo, base_sha, rel_path), buildings_at(repo, head_sha, rel_path), rel_path)
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
        gml_files = changed_list(args.file_list)
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
    # a finding (exit 1) exactly when the report warns: per-building warnings or a large change
    warns = any(f["warnings"] for f in files) or _changed_total(files) > args.threshold
    return 1 if warns else 0


if __name__ == "__main__":
    from scripts.gate_result import guarded
    raise SystemExit(guarded(main))
