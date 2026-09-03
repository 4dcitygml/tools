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

from scripts.extract_building_preview import (  # noqa: E402
    _attach_tex,
    _extract_buildings,
    _extract_texmap,
    _finalize_url,
    _match_buildings,
)


def build_preview_url(old_gml: Path, new_gml: Path, base_url: str) -> str:
    """Extract the changed buildings from old/new .gml and return a preview URL.

    Textures (UV) are embedded in the fragment, gzip-compressed into the URL.
    Returns an empty string when there are no changed buildings (matching
    extract_building_preview's behavior).
    """
    old_bytes = old_gml.read_bytes() if old_gml.exists() else b""
    new_bytes = new_gml.read_bytes() if new_gml.exists() else b""

    old_bldgs: dict = {}
    new_bldgs: dict = {}
    if old_bytes:
        old_bldgs = _extract_buildings(old_bytes)
    if new_bytes:
        new_bldgs = _extract_buildings(new_bytes)

    pairs = _match_buildings(old_bldgs, new_bldgs)
    if not pairs:
        return ""

    old_tex: dict = {}
    new_tex: dict = {}
    if old_bytes:
        old_tex = _extract_texmap(old_bytes)
    if new_bytes:
        new_tex = _extract_texmap(new_bytes)
    for pair in pairs:
        _attach_tex(pair, old_tex, new_tex)

    return _finalize_url(pairs, base_url)


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
