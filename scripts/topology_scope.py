#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Determine whether topology checking is needed for this PR.

Geometry changes are always checked. For attribute and texture changes, run baseline
checks only on the "first" occurrence where target buildingID hasn't yet appeared in
past merged history; subsequent occurrences are out of scope. History uses Building-family
trailers in commits, so no separate ledger needs maintenance.

Usage:
    python scripts/topology_scope.py --repo . --base-sha SHA --head-sha SHA \
      --kind attribute --github-output "$GITHUB_OUTPUT"
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.building_identity import trailer_buildings, trailers  # noqa: E402
from scripts.repo_git import git as _git  # noqa: E402


@dataclass(frozen=True)
class Decision:
    run: bool
    reason: str
    building_ids: tuple[str, ...]


def building_ids(messages: str) -> set[str]:
    """The buildings the A2 trailers of these commit messages name (the commit scope gate's parser)."""
    return {value for value in trailer_buildings(trailers(messages)) if value}


def decide(repo: Path, base_sha: str, head_sha: str, kind: str) -> Decision:
    current_messages = _git(repo, "log", "--format=%B%x00", f"{base_sha}..{head_sha}")
    current_ids = building_ids(current_messages)

    if kind == "geometry":
        return Decision(True, "Building geometry changed; checking every time.", tuple(sorted(current_ids)))
    if not current_ids:
        return Decision(
            True,
            "Cannot confirm building ID from history; checking for safety.",
            (),
        )

    base_messages = _git(repo, "log", "--format=%B%x00", base_sha)
    previous_ids = building_ids(base_messages)
    first_ids = sorted(current_ids - previous_ids)
    if first_ids:
        return Decision(
            True,
            "First occurrence of this building; checking baseline geometry: "
            + ", ".join(first_ids),
            tuple(sorted(current_ids)),
        )
    return Decision(
        False,
        "Building already checked in past; geometry not changed; out of scope.",
        tuple(sorted(current_ids)),
    )


def _write_github_output(path: Path, decision: Decision) -> None:
    # reason is a fixed sentence containing no newlines; safe to record as a single-line GitHub Actions output.
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"run={'true' if decision.run else 'false'}\n")
        stream.write(f"reason={decision.reason}\n")
        stream.write(f"building_ids={','.join(decision.building_ids)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--kind", choices=("attribute", "texture", "geometry"), required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    decision = decide(args.repo.resolve(), args.base_sha, args.head_sha, args.kind)
    print(f"run={'true' if decision.run else 'false'}")
    print(f"reason={decision.reason}")
    print(f"building_ids={','.join(decision.building_ids)}")
    if args.github_output:
        _write_github_output(args.github_output, decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
