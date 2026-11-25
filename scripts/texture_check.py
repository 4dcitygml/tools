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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import citygml_dialect, citygml_faces  # noqa: E402
from scripts.safe_xml import fromstring  # noqa: E402


def _tree(raw: bytes):
    """(root, the file's namespaces): the appearance is read namespace-aware, any prefix and either
    CityGML version, with entities unescaped (U8, D-c5: byte regexes knew only gml: and app:)."""
    root = fromstring(raw)
    return root, citygml_dialect.ns_for_root(root)


def referenced_image_uris(raw: bytes) -> set[str]:
    """Set of imageURIs (relative path strings) referenced by the .gml."""
    root, ns = _tree(raw)
    return {(e.text or "").strip() for e in root.iter(f"{{{ns['app']}}}imageURI") if (e.text or "").strip()}


def _poly_to_building(root, ns: dict) -> dict[str, str]:
    """polygon gml:id -> gml:id of the member's (outermost) building containing it."""
    gml_id = f"{{{ns['gml']}}}id"
    p2b: dict[str, str] = {}
    for member in root.iter(f"{{{ns['core']}}}cityObjectMember"):
        building = next(member.iter(f"{{{ns['bldg']}}}Building"), None)
        if building is None or not building.get(gml_id):
            continue
        for poly in building.iter(f"{{{ns['gml']}}}Polygon"):
            if poly.get(gml_id):
                p2b[poly.get(gml_id)] = building.get(gml_id)
    return p2b


def _appearance(raw: bytes):
    """(image, building, polygon, target) of every texture target that belongs to a building."""
    root, ns = _tree(raw)
    p2b = _poly_to_building(root, ns)
    for img, poly, target in citygml_faces.textures(root, ns):
        if poly in p2b:
            yield img, p2b[poly], poly, target, ns


def image_to_buildings(raw: bytes) -> dict[str, set[str]]:
    """imageURI -> set of buildings referencing it (for reports/GC; not needed for the localization guarantee)."""
    img2b: dict[str, set[str]] = {}
    for img, bid, _poly, _target, _ns in _appearance(raw):
        img2b.setdefault(img, set()).add(bid)
    return img2b


def building_appearance_sig(raw: bytes) -> dict[str, frozenset[tuple[str, str, str]]]:
    """Building -> {(polygon, imageURI, UV hash)} signature. The semantic state of the appearance."""
    per_bldg: dict[str, set[tuple[str, str, str]]] = {}
    for img, bid, poly, target, ns in _appearance(raw):
        uv = "".join(c.text or "" for c in target.iter(f"{{{ns['app']}}}textureCoordinates"))
        per_bldg.setdefault(bid, set()).add((poly, img, hashlib.sha256(uv.encode()).hexdigest()[:16]))
    return {b: frozenset(v) for b, v in per_bldg.items()}


def appearance_changed_buildings(base: bytes, head: bytes) -> list[str]:
    """gml:ids of buildings whose appearance signature changed between base and head (= retexturing etc., (a))."""
    bs = building_appearance_sig(base)
    hs = building_appearance_sig(head)
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
IMAGE_SUFFIXES = tuple(_IMAGE_MAGIC)   # the texture images: the one list (gc_textures, the driver's texture row)


def texture_images(bldg_dir: Path) -> list[Path]:
    """The texture images of a buildings folder: the image files of its *_appearance folders (the
    one walk, W2: gc_textures and strip_lod2 each had theirs)."""
    return sorted(f for app in bldg_dir.glob("*_appearance") if app.is_dir()
                  for f in app.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES)


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
    from scripts.gate_result import guarded
    raise SystemExit(guarded(main))
