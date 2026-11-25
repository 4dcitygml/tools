#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The release tag grammar, once (W8: it was spelled in about ten places).

`tools-vX.Y.Z` and `hub-vX.Y.Z`; an optional `-suffix` (`hub-v2.0.0-rc.1`) marks a prerelease,
which is never GitHub's "latest" release. The installers (install/release/citygml.sh, .ps1)
keep their own copies of the hub form; tests/test_release_tags.py holds them equal to this one.

    python3 scripts/release_tags.py check <tools|hub> <tag>

prints `version=`, `prerelease=` and `latest=` lines (for $GITHUB_OUTPUT), or fails.
"""
from __future__ import annotations

import re
import sys

TAG_RE = re.compile(r"^(tools|hub)-v(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9.-]+))?$")


def parse(tag) -> "tuple[str, tuple[int, int, int], str] | None":
    """(series, (X, Y, Z), suffix) of a release tag ("" without a suffix), or None."""
    m = TAG_RE.match(tag) if isinstance(tag, str) else None
    return (m.group(1), tuple(int(x) for x in m.group(2, 3, 4)), m.group(5) or "") if m else None


def version_key(tag, series: str = "hub") -> "tuple[int, int, int] | None":
    """(X, Y, Z) of a tag of the series (a prerelease too), else None."""
    found = parse(tag)
    return found[1] if found and found[0] == series else None


def facts(series: str, tag: str) -> dict:
    """What the release workflows need: the version, whether it is a prerelease, and whether
    GitHub marks it latest (a final hub release only; tools releases never)."""
    found = parse(tag)
    if not found or found[0] != series:
        raise ValueError(f"{tag!r} is not a {series}-vX.Y.Z[-suffix] release tag")
    pre = bool(found[2])
    return {"version": tag[len(series) + 1:], "prerelease": str(pre).lower(), "latest": str(series == "hub" and not pre).lower()}


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[:1] != ["check"]:
        print("usage: release_tags.py check <tools|hub> <tag>", file=sys.stderr)
        return 2
    try:
        print("\n".join(f"{k}={v}" for k, v in facts(args[1], args[2]).items()))
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
