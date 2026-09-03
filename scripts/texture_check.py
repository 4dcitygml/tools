#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Texture (appearance) reviewability check (R3, (a)) and reference aggregation.

Following the responsibility-sharing backbone, makes texture "Content (appearance)" changes reviewable at building granularity.
Given immutable operation (no in-place overwrite of existing images = R1, git check in CI), localization is
**guaranteed by R1 + R3 alone** (image bytes cannot be modified → no side effects to other buildings via shared images).
This tool covers:

- **R3 (no dangling refs)**: Check if .gml `app:imageURI` points to existing image files.
  Catches broken references (deletion breaking unchanged buildings, GC/revert accidents).
- **(a) appearance-changed buildings**: Does each building's appearance signature (imageURI + UV) change base↔head?
  Re-texturing is invisible to W1 (geometry/attribute diff), so count this as "changed buildings" and pass to scope judgment.
- **Reference aggregation**: imageURI reference set, image→building mapping (for reporting/GC).

Reverse index image→building is unnecessary for localization guarantee (R1 + R3 sufficient).

Usage:
    python scripts/texture_check.py --dangling FILE.gml [FILE2.gml ...]
    python scripts/texture_check.py --changed-buildings BASE.gml HEAD.gml
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.reconstruct_minimal import building_spans  # noqa: E402

_POLY_RE = re.compile(rb'<gml:Polygon[^>]*\bgml:id="([^"]+)"')
_PT_RE = re.compile(rb"<app:ParameterizedTexture\b.*?</app:ParameterizedTexture>", re.DOTALL)
_IMGURI_RE = re.compile(rb"<app:imageURI>([^<]+)</app:imageURI>")
_TARGET_RE = re.compile(rb"<app:target\b.*?</app:target>", re.DOTALL)
_TARGET_URI_RE = re.compile(rb'uri="#?([^"]+)"')
_TEXCOORD_RE = re.compile(rb"<app:textureCoordinates[^>]*>([^<]+)</app:textureCoordinates>")


def referenced_image_uris(raw: bytes) -> set[str]:
    """Set of imageURIs (relative path strings) referenced by the .gml."""
    return {m.group(1).decode("utf-8").strip() for m in _IMGURI_RE.finditer(raw)}


def _poly_to_building(raw: bytes) -> dict[str, str]:
    """polygon gml:id -> gml:id of the building containing it."""
    p2b: dict[str, str] = {}
    for bid, (s, e) in building_spans(raw).items():
        for m in _POLY_RE.finditer(raw, s, e):
            p2b[m.group(1).decode("utf-8")] = bid
    return p2b


def image_to_buildings(raw: bytes) -> dict[str, set[str]]:
    """imageURI -> set of buildings referencing it (for reports/GC; not needed for the localization guarantee)."""
    p2b = _poly_to_building(raw)
    img2b: dict[str, set[str]] = {}
    for pt in _PT_RE.finditer(raw):
        block = pt.group(0)
        mu = _IMGURI_RE.search(block)
        if not mu:
            continue
        img = mu.group(1).decode("utf-8").strip()
        for tgt in _TARGET_RE.finditer(block):
            mt = _TARGET_URI_RE.search(tgt.group(0))
            if mt and mt.group(1).decode("utf-8") in p2b:
                img2b.setdefault(img, set()).add(p2b[mt.group(1).decode("utf-8")])
    return img2b


def _building_appearance_sig(raw: bytes) -> dict[str, frozenset[tuple[str, str, str]]]:
    """Building -> {(polygon, imageURI, UV hash)} signature. The semantic state of the appearance."""
    p2b = _poly_to_building(raw)
    per_bldg: dict[str, set[tuple[str, str, str]]] = {}
    for pt in _PT_RE.finditer(raw):
        block = pt.group(0)
        mu = _IMGURI_RE.search(block)
        if not mu:
            continue
        img = mu.group(1).decode("utf-8").strip()
        for tgt in _TARGET_RE.finditer(block):
            tb = tgt.group(0)
            mt = _TARGET_URI_RE.search(tb)
            if not mt:
                continue
            poly = mt.group(1).decode("utf-8")
            bid = p2b.get(poly)
            if bid is None:
                continue
            uv = b"".join(m.group(1) for m in _TEXCOORD_RE.finditer(tb))
            uv_hash = hashlib.sha256(uv).hexdigest()[:16]
            per_bldg.setdefault(bid, set()).add((poly, img, uv_hash))
    return {b: frozenset(v) for b, v in per_bldg.items()}


def appearance_changed_buildings(base: bytes, head: bytes) -> list[str]:
    """gml:ids of buildings whose appearance signature changed between base and head (= retexturing etc., (a))."""
    bs = _building_appearance_sig(base)
    hs = _building_appearance_sig(head)
    changed = [b for b in set(bs) | set(hs) if bs.get(b) != hs.get(b)]
    return sorted(changed)


def dangling_image_uris(gml_path: Path) -> list[str]:
    """imageURIs in the .gml that do not point to an existing file (R3, relative resolution)."""
    raw = gml_path.read_bytes()
    missing: list[str] = []
    for uri in sorted(referenced_image_uris(raw)):
        # imageURI is relative to the .gml's parent directory (e.g. <mesh>_appearance/xxx.jpg).
        target = (gml_path.parent / uri).resolve()
        if not target.is_file():
            missing.append(uri)
    return missing


# Magic-byte signatures per extension. Extension says what the file claims to
# be; the leading bytes say what it actually is. A mismatch means the "image"
# is some other content smuggled in under an image name (never legitimate).
_IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
}


def image_magic_mismatches(paths: list[Path]) -> list[str]:
    """Paths whose leading bytes do not match their image extension (unknown extensions are ignored)."""
    bad: list[str] = []
    for p in paths:
        magics = _IMAGE_MAGIC.get(p.suffix.lower())
        if magics is None:
            continue
        try:
            with p.open("rb") as fh:
                head = fh.read(16)
        except OSError:
            bad.append(str(p))
            continue
        if not any(head.startswith(m) for m in magics):
            bad.append(str(p))
    return bad


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dangling", nargs="+", type=Path, metavar="GML",
                   help="R3: Check if imageURI in these .gml files exist (exit 1 if missing)")
    p.add_argument("--changed-buildings", nargs=2, type=Path, metavar=("BASE", "HEAD"),
                   help="(a): Output gml:ids of buildings with appearance changes, one per line")
    p.add_argument("--verify-images", nargs="+", type=Path, metavar="IMG",
                   help="Magic bytes: Check if image file magic matches extension (exit 1 if mismatch)")
    args = p.parse_args(argv)

    if args.verify_images:
        bad = image_magic_mismatches(args.verify_images)
        for path in bad:
            print(f"::error::not a valid image for its extension (magic bytes mismatch): {path}",
                  file=sys.stderr)
        if not bad:
            print("magic OK: all image files match their extension", file=sys.stderr)
        return 1 if bad else 0

    if args.changed_buildings:
        base, head = args.changed_buildings
        for bid in appearance_changed_buildings(base.read_bytes(), head.read_bytes()):
            print(bid)
        return 0

    if args.dangling:
        rc = 0
        for gml in args.dangling:
            miss = dangling_image_uris(gml)
            if miss:
                rc = 1
                for uri in miss:
                    print(f"::error::dangling imageURI in {gml}: {uri} (referenced image file not found)",
                          file=sys.stderr)
        if rc == 0:
            print("R3 OK: no dangling imageURIs", file=sys.stderr)
        return rc

    p.error("Specify one of: --dangling / --changed-buildings / --verify-images")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
