#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Preview driver for local testing (N-3).

Generates a Cesium preview URL from two local old and new .gml files
without going through git history.

A thin layer that directly reuses extract_building_preview.py's extraction,
matching, and URL-generation logic, replacing only the input with "two local files".

Usage:
    python scripts/preview_local.py OLD.gml NEW.gml [--base-url URL]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.extract_building_preview import preview_url, preview_pairs  # noqa: E402


def build_preview_url(old_gml: Path, new_gml: Path, base_url: str) -> str:
    """Extract the changed buildings from old/new .gml and return a preview URL.

    Textures (UV) are embedded in the fragment, gzip-compressed into the URL.
    Returns an empty string when there are no changed buildings (matching
    extract_building_preview's behavior).
    """
    pairs = preview_pairs(old_gml.read_bytes() if old_gml.exists() else None,
                          new_gml.read_bytes() if new_gml.exists() else None)
    if not pairs:
        return ""
    return preview_url(pairs, base_url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_gml", type=Path, help="old version .gml")
    parser.add_argument("new_gml", type=Path, help="new version .gml")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()

    url = build_preview_url(args.old_gml, args.new_gml, args.base_url)
    if not url:
        print("No changed buildings found.", file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
