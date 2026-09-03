#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Fetch the materials declared in a bulk-submission provenance manifest so CI
can reproduce the conversion (docs/bulk-submission-provenance.md §4).

Each material is a whole file (``uri`` = http(s) URL), a blob of the city
repository itself (``git:<sha>:<path>``, e.g. the file at the parent commit), or an
archive with ``members`` (``uri`` = http(s) URL of a ZIP; members are fetched
by HTTP Range using the same code path as setup_city_data.py). Every fetched
byte string is checked against the declared sha256 before it is written.

Usage:
    python3 scripts/fetch_materials.py --manifest provenance/identity-baseline/X.json --outdir .materials
Prints one line per material: ``<name>\\t<local path>``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import setup_city_data as Z  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_material(material: dict, outdir: Path, repo: Path | None = None) -> Path:
    uri = material["uri"]
    name = material.get("name") or os.path.basename(urllib.parse.urlparse(uri).path)
    parsed = urllib.parse.urlparse(uri)
    members = material.get("members") or []
    if uri.startswith("git:"):
        # git:<commit sha>:<path> — a blob of the city repository itself (the file at the parent commit)
        _scheme, sha, path = uri.split(":", 2)
        proc = subprocess.run(["git", "-C", str(repo or Path.cwd()), "show", f"{sha}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            raise SystemExit(f"{name}: git object {sha}:{path} not found ({proc.stderr.decode(errors='replace').strip()})")
        data = proc.stdout
        if _sha256(data) != material["sha256"]:
            raise SystemExit(f"{name}: digest mismatch for {uri}")
        target = outdir / name
        target.write_bytes(data)
        return target
    if parsed.scheme == "file":
        source = Path(urllib.request.url2pathname(parsed.path))
        data = source.read_bytes()
        if _sha256(data) != material["sha256"]:
            raise SystemExit(f"{name}: local file digest mismatch")
        return source
    if parsed.scheme not in ("http", "https"):
        raise SystemExit(f"{name}: unsupported URI scheme {parsed.scheme!r}")
    if not members:
        with urllib.request.urlopen(urllib.request.Request(uri), timeout=300) as response:
            data = response.read()
        if _sha256(data) != material["sha256"]:
            raise SystemExit(f"{name}: digest mismatch for {uri}")
        target = outdir / name
        target.write_bytes(data)
        return target
    # ZIP archive: fetch only the listed members by Range
    size = Z.total_size(uri)
    if material.get("bytes") not in (None, size):
        raise SystemExit(f"{name}: archive size {size} != declared {material.get('bytes')}")
    entries = {e["name"]: e for e in Z.parse_entries(Z.central_directory(uri, size))}
    chosen = []
    for member in members:
        entry = entries.get(member["path"])
        if entry is None:
            raise SystemExit(f"{name}: member {member['path']} not in archive")
        chosen.append(entry)
    Z.extract(uri, chosen, str(outdir))
    first = None
    for member in members:
        local = outdir / member["path"]
        data = local.read_bytes()
        if _sha256(data) != member["sha256"]:
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
    args.outdir.mkdir(parents=True, exist_ok=True)
    for material in manifest["materials"]:
        local = fetch_material(material, args.outdir, args.repo)
        print(f"{material.get('name')}\t{local}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
