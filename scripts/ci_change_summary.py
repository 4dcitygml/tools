#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Format building diffs to PR comment Markdown (W2 / feature list D-2, A-8).

Takes per-building diffs from W1 (`diff_citygml`) and renders per-building
"change type, attribute diffs, geometry change flag" as Markdown. Outputs with marker;
CI (W5a) upserts it as a **separate comment** from the existing preview comment
(per work plan D4 decision).

This module focuses solely on Markdown generation (pure function). Comment posting
is done by the workflow side (W5a) using the same `gh api` upsert as `preview.yml`.
Therefore the summary marker uses a distinct name from preview (`<!-- cesium-building-preview -->`).

Corresponding feature list items:
- D-2-a Markdown rendering of base..head diff summary (with marker)
- A-8   Markdown rendering of change overview table + per-building details

Usage:
    python scripts/ci_change_summary.py OLD.gml NEW.gml [--id GMLID] [--preview-url URL]
    python scripts/ci_change_summary.py --from-json diff.json [--preview-url URL]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.diff_citygml import diff_files, diff_sources  # noqa: E402
from scripts.extract_building_preview import _get_file_at_sha  # noqa: E402  (shared git show helper / X-2)
from scripts.citygml_constants import SUMMARY_MARKER  # noqa: E402,F401  (marker distinct from preview's)

# Safety caps so a single comment does not bloat (GitHub comments are 65,536 chars).
# Tier1-minimum PRs touch one building so these normally never trigger; when they do, the omission is made explicit.
MAX_BUILDINGS_PER_SECTION = 100
MAX_ATTR_ROWS_PER_BUILDING = 100

_LABEL = {"added": "🟢 Added", "deleted": "🔴 Deleted", "modified": "🟡 Modified"}


def _cell(value: Optional[str]) -> str:
    """Table cell. None (attribute added/deleted) renders as an em dash, everything else as code."""
    if value is None:
        return "—"
    return f"`{value}`"


def _truncate(items: list, cap: int) -> tuple[list, int]:
    """Return the first cap items and the number of items over the cap."""
    if len(items) <= cap:
        return items, 0
    return items[:cap], len(items) - cap


def _render_modified(building: dict, lines: list[str]) -> None:
    lines.append(f"#### `{building['id']}`")
    lines.append(
        f"- **Geometry**: {'changed ⚠️' if building.get('geometry_changed') else 'unchanged'}"
    )
    diffs = building.get("attribute_diffs", [])
    if not diffs:
        note = "none (geometry-only change)" if building.get("geometry_changed") else "none"
        lines.append(f"- **Attribute diffs**: {note}")
        lines.append("")
        return
    shown, rest = _truncate(diffs, MAX_ATTR_ROWS_PER_BUILDING)
    lines.append(f"- **Attribute diffs** ({len(diffs)}):")
    lines.append("")
    lines.append("| path | Old | New |")
    lines.append("|---|---|---|")
    for d in shown:
        lines.append(f"| `{d['path']}` | {_cell(d['old'])} | {_cell(d['new'])} |")
    if rest:
        lines.append(f"| … | {rest} more omitted | |")
    lines.append("")


def _render_id_list(buildings: list[dict], lines: list[str]) -> None:
    shown, rest = _truncate(buildings, MAX_BUILDINGS_PER_SECTION)
    for b in shown:
        lines.append(f"- `{b['id']}`")
    if rest:
        lines.append(f"- … {rest} more buildings omitted")
    lines.append("")


def _has_changes(diff_result: dict) -> bool:
    s = diff_result["summary"]
    return (s["added"] + s["deleted"] + s["modified"]) > 0


def _render_sections(diff_result: dict, lines: list[str]) -> None:
    """Append the body for one file (target file, counts, per-kind sections)."""
    summary = diff_result["summary"]
    by_status: dict[str, list[dict]] = {"modified": [], "added": [], "deleted": []}
    for b in diff_result["buildings"]:
        by_status.get(b["status"], []).append(b)

    lines.append(f"**File**: `{diff_result['new']}`")
    lines.append(
        f"**Changed buildings**: {_LABEL['added']} {summary['added']} / "
        f"{_LABEL['deleted']} {summary['deleted']} / "
        f"{_LABEL['modified']} {summary['modified']}"
    )
    lines.append("")

    if not _has_changes(diff_result):
        lines.append("No buildings were changed.")
        lines.append("")
        return

    if by_status["modified"]:
        lines.append(f"### {_LABEL['modified']} ({summary['modified']} building(s))")
        lines.append("")
        for b in by_status["modified"]:
            _render_modified(b, lines)
    if by_status["added"]:
        lines.append(f"### {_LABEL['added']} ({summary['added']} building(s))")
        lines.append("")
        _render_id_list(by_status["added"], lines)
    if by_status["deleted"]:
        lines.append(f"### {_LABEL['deleted']} ({summary['deleted']} building(s))")
        lines.append("")
        _render_id_list(by_status["deleted"], lines)


def _render_footer(preview_url: Optional[str], lines: list[str]) -> str:
    if preview_url:
        lines.append("---")
        lines.append(f"🔗 3D preview: [Open in the Cesium viewer]({preview_url})")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(diff_result: dict, preview_url: Optional[str] = None) -> str:
    """Format W1's single-file diff result as Markdown for a PR comment (with marker)."""
    lines: list[str] = [SUMMARY_MARKER, "## 📝 CityGML change summary", ""]
    _render_sections(diff_result, lines)
    return _render_footer(preview_url, lines)


def render_ci(results: list[dict], preview_url: Optional[str] = None) -> str:
    """Combine multiple files' diff results into one PR-comment Markdown (for W5a).

    Expected to receive only files with changes. Returns an empty string when
    empty (= do not post).
    """
    if not results:
        return ""
    lines: list[str] = [SUMMARY_MARKER, "## 📝 CityGML change summary", ""]
    if len(results) > 1:
        lines.append(f"Changed files: {len(results)}")
        lines.append("")
    for result in results:
        _render_sections(result, lines)
        lines.append("")
    return _render_footer(preview_url, lines)


def collect_ci_results(
    repo: Path, base_sha: str, head_sha: str, gml_files: list[str]
) -> list[dict]:
    """Diff base (git show) against head (git show) for each changed .gml; return only those with changes."""
    results: list[dict] = []
    for rel_path in gml_files:
        old_bytes = _get_file_at_sha(repo, base_sha, rel_path)
        new_bytes = _get_file_at_sha(repo, head_sha, rel_path)
        result = diff_sources(old_bytes, new_bytes, rel_path, rel_path)
        if _has_changes(result):
            results.append(result)
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_gml", type=Path, nargs="?", help="old .gml")
    parser.add_argument("new_gml", type=Path, nargs="?", help="new .gml")
    parser.add_argument("--id", dest="only_id", default=None, help="filter to this gml:id only")
    parser.add_argument("--preview-url", default=None, help="existing preview URL (optional)")
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="render from W1 diff JSON (do not re-parse old/new)",
    )
    # CI mode (W5a): compare base..head via git show
    parser.add_argument("--repo", type=Path, default=None, help="[CI] repository path")
    parser.add_argument("--base-sha", default=None, help="[CI] base commit SHA")
    parser.add_argument("--head-sha", default=None, help="[CI] head commit SHA")
    parser.add_argument(
        "--file-list", type=Path, default=None, help="[CI] list of changed .gml files"
    )
    args = parser.parse_args(argv)

    if args.file_list is not None:
        # CI mode: compare the changed-file list over base..head and combine into one comment
        if not (args.base_sha and args.head_sha):
            parser.error("--base-sha and --head-sha are required when using --file-list.")
        listed = (ln.strip() for ln in args.file_list.read_text(encoding="utf-8").splitlines())
        gml_files = [p for p in listed if p.endswith(".gml")]
        repo = args.repo or REPO_ROOT
        results = collect_ci_results(repo, args.base_sha, args.head_sha, gml_files)
        sys.stdout.write(render_ci(results, preview_url=args.preview_url))
        return 0

    if args.from_json is not None:
        diff_result = json.loads(args.from_json.read_text(encoding="utf-8"))
    elif args.old_gml is not None and args.new_gml is not None:
        diff_result = diff_files(args.old_gml, args.new_gml, only_id=args.only_id)
    else:
        parser.error(
            "Specify one of: OLD.gml NEW.gml / --from-json / --file-list (CI)."
        )

    sys.stdout.write(render_markdown(diff_result, preview_url=args.preview_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
