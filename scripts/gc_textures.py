#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Collection (GC) of unreferenced texture images = deletion of unused assets in standard software development (tree-shaking).

In immutable operation (texture change = new addition + imageURI update), old images become
orphaned on re-texturing. This tool detects **images unreferenced by any .gml** and optionally deletes them.

- Reference set is built by resolving `app:imageURI` from all .gml files (since mesh splitting
  means multiple .gml share appearance folders, **collecting references across all .gml** is key).
- Default is "detect and list". `--delete` performs actual deletion (recommended workflow: file GC PR).
- Deletion remains in git history, so revert is possible. Broken references are caught separately by
  texture_check R3 in CI.

Usage:
    python scripts/gc_textures.py --dir 13101_chiyoda-ku_pref_2023_citygml_1_op/udx/bldg
    python scripts/gc_textures.py --dir <DIR> --delete      # actual deletion
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.texture_check import referenced_image_uris  # noqa: E402

_IMG_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def referenced_files(bldg_dir: Path) -> set[Path]:
    """Set of absolute paths of images referenced by all .gml files under bldg_dir."""
    refs: set[Path] = set()
    for gml in bldg_dir.glob("*.gml"):
        raw = gml.read_bytes()
        for uri in referenced_image_uris(raw):
            refs.add((gml.parent / uri).resolve())
    return refs


def all_image_files(bldg_dir: Path) -> set[Path]:
    """Set of absolute paths of all image files in *_appearance/ under bldg_dir."""
    files: set[Path] = set()
    for app in bldg_dir.glob("*_appearance"):
        for f in app.iterdir():
            if f.is_file() and f.suffix.lower() in _IMG_SUFFIXES:
                files.add(f.resolve())
    return files


def orphans(bldg_dir: Path) -> list[Path]:
    """List of images not referenced by any .gml (orphans)."""
    refs = referenced_files(bldg_dir)
    return sorted(f for f in all_image_files(bldg_dir) if f not in refs)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", type=Path, required=True, help="Directory containing building .gml and *_appearance/")
    p.add_argument("--delete", action="store_true", help="Actually delete orphans (default: list only)")
    args = p.parse_args(argv)

    orph = orphans(args.dir)
    total = len(all_image_files(args.dir))
    print(f"{len(orph)} unreferenced (orphan) images out of {total} total", file=sys.stderr)
    for f in orph:
        rel = f.relative_to(args.dir.resolve()) if str(f).startswith(str(args.dir.resolve())) else f
        print(rel)
    if args.delete:
        for f in orph:
            f.unlink()
        print(f"Deleted {len(orph)} images (retained in git history for revert)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
