#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Fetch the materials declared in a bulk-submission provenance manifest so CI
can reproduce the conversion (docs/bulk-submission-provenance.md §4).

Each material is a whole file (``uri`` = http(s) URL), a blob of the city
repository itself (``git:<sha>:<path>``, e.g. the file at the parent commit), or an
archive with ``members`` (``uri`` = http(s) URL of a ZIP; members are fetched
by HTTP Range, scripts/range_zip.py). Every fetched
byte string is checked against the declared sha256 before it is written.

Usage:
    python3 scripts/fetch_materials.py --manifest provenance/identity-baseline/X.json --outdir .materials
Prints one line per material: ``<name>\\t<local path>``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import provenance_manifest, repo_git  # noqa: E402
from scripts import range_zip  # noqa: E402


def inside(outdir: Path, rel: str) -> Path:
    """outdir / rel when that stays inside outdir (D-4a4: a manifest names its files, the PR that
    carries it chooses the names); SystemExit otherwise."""
    base = Path(outdir).resolve()
    target = (base / rel).resolve()
    if not rel or Path(rel).is_absolute() or "\\" in rel or not target.is_relative_to(base) or target == base:
        raise SystemExit(f"{rel!r}: a material may only write inside the materials folder")
    return target


def fetch_material(material: dict, outdir: Path, repo: Path | None = None) -> Path:
    uri = material["uri"]
    name = material.get("name") or os.path.basename(urllib.parse.urlparse(uri).path)
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise SystemExit(f"{name!r}: a material name is a plain file name (no folder, no ..)")
    parsed = urllib.parse.urlparse(uri)
    members = material.get("members") or []
    if uri.startswith("git:"):
        # git:<commit sha>:<path> — a blob of the city repository itself (the file at the parent commit)
        _scheme, sha, path = uri.split(":", 2)
        try:
            data = repo_git.git(repo or Path.cwd(), "show", f"{sha}:{path}", binary=True)
        except repo_git.GitError as e:
            raise SystemExit(f"{name}: git object {sha}:{path} not found ({e})")
        if provenance_manifest.sha256_hex(data) != material["sha256"]:
            raise SystemExit(f"{name}: digest mismatch for {uri}")
        target = inside(outdir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
    if parsed.scheme == "file":
        source = Path(urllib.request.url2pathname(parsed.path))
        data = source.read_bytes()
        if provenance_manifest.sha256_hex(data) != material["sha256"]:
            raise SystemExit(f"{name}: local file digest mismatch")
        return source
    if parsed.scheme not in ("http", "https"):
        raise SystemExit(f"{name}: unsupported URI scheme {parsed.scheme!r}")
    if not members:
        with urllib.request.urlopen(urllib.request.Request(uri), timeout=300) as response:
            data = response.read()
        if provenance_manifest.sha256_hex(data) != material["sha256"]:
            raise SystemExit(f"{name}: digest mismatch for {uri}")
        target = inside(outdir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
    # ZIP archive: fetch only the listed members by Range
    with range_zip.open_zip(uri) as archive:
        if material.get("bytes") not in (None, archive.size):
            raise SystemExit(f"{name}: archive size {archive.size} != declared {material.get('bytes')}")
        names = set(archive.namelist())
        for member in members:
            inside(outdir, member["path"])   # a member path is relative and stays inside the folder
            if member["path"] not in names:
                raise SystemExit(f"{name}: member {member['path']} not in archive")
        range_zip.extract(archive, [m["path"] for m in members], outdir)
    first = None
    for member in members:
        local = inside(outdir, member["path"])
        data = local.read_bytes()
        if provenance_manifest.sha256_hex(data) != member["sha256"]:
            raise SystemExit(f"{name}: member digest mismatch for {member['path']}")
        first = first or local
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=None, help="city repository for git:<sha>:<path> materials (default: cwd)")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    # every kind is checked against the schema before anything is fetched (D-4a4; before, only
    # the semantic correction validated first)
    errors = provenance_manifest.validate(manifest)
    if errors:
        raise SystemExit("manifest does not conform to the schema: " + "; ".join(errors[:5]))
    args.outdir.mkdir(parents=True, exist_ok=True)
    for material in manifest["materials"]:
        local = fetch_material(material, args.outdir, args.repo)
        print(f"{material.get('name')}\t{local}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
