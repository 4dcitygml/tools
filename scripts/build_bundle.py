#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Assemble the hub's distribution zip — the payload of the one-line installer.

The one manifest of what travels to a resident's computer. The release workflow and
the tests call this; nothing else lists these files (see docs/client-runtime-contract.md §5).

    python3 scripts/build_bundle.py --flavor macos --tag hub-v1.3.0 --out tools/hub/dist
    python3 scripts/build_bundle.py --flavor windows --tag hub-v1.3.0 --portable tools/hub/dist --out tools/hub/dist

Layout: the only top-level item is citygml-hub/program/; the installer unpacks it into
citygml-tools/citygml-hub/<tag>/ and starts program/hub.py. program/ carries the hub and
its screens, the editors, the language and theme packs, the shared modules, the per-user
launchers, the licences and — in the Windows flavor — PortableGit/ (MinGit) and
PythonPortable/ (the python.org embeddable package), fetched and verified by the workflow
before this script runs. Cities distribute no code: nothing here comes from a city clone.

Python's zipfile is used so that non-ASCII names always get the UTF-8 flag and the
launcher keeps its execute bit. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = "citygml-hub"
LIB = f"{ROOT}/program"
FLAVORS = {"macos": "macos", "windows": "windows-full"}
BUNDLE_DIRS = ("PortableGit", "PythonPortable")        # Windows flavor only
PACK_SUFFIXES = {".py", ".html", ".json"}
WINDOWS_SIZE_LIMIT = 70 * 1024 * 1024
EXEC = 0o755
PLAIN = 0o644


def repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def version_from_tag(tag: str) -> str:
    """hub-v1.3.0 → v1.3.0; anything else (a PR build) → dev."""
    return tag.removeprefix("hub-") if re.fullmatch(r"hub-v\d+\.\d+\.\d+", tag or "") else "dev"


def artifact_name(flavor: str, version: str) -> str:
    return f"citygml-hub-{version}-{FLAVORS[flavor]}.zip"


def manifest(repo_root: Path, flavor: str) -> "list[tuple[Path, str, int]]":
    """(source file, archive name, mode) for everything that comes from the source tree."""
    repo_root = Path(repo_root)
    hub = repo_root / "tools" / "hub"
    entries: list[tuple[Path, str, int]] = [(hub / "app.py", f"{LIB}/hub.py", PLAIN)]
    entries += [(p, f"{LIB}/{p.name}", PLAIN) for p in sorted(hub.glob("*.html"))]   # every screen of the hub
    entries.append((hub / "preset.json", f"{LIB}/preset.json", PLAIN))
    if flavor == "windows":
        entries.append((hub / "packaging" / "start-windows.bat", f"{LIB}/start-windows.bat", PLAIN))
    # the shared modules (the A5 classification table is shared with CI)
    entries.append((repo_root / "scripts" / "pr_classification.py", f"{LIB}/pr_classification.py", PLAIN))
    for name in ("runtime.py", "accounts.py", "git_sync.py", "shortcuts.py"):
        entries.append((repo_root / "tools" / name, f"{LIB}/{name}", PLAIN))
    # the per-user launchers the installer copies into place (citygml.sh keeps its execute bit)
    entries.append((repo_root / "install" / "citygml.sh", f"{LIB}/citygml.sh", EXEC))
    entries.append((repo_root / "install" / "citygml.ps1", f"{LIB}/citygml.ps1", PLAIN))
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        entries.append((repo_root / name, f"{LIB}/{name}", PLAIN))
    # the editors, the language pack and the theme pack travel with the hub
    for sub in ("attr_editor", "tex_editor", "i18n", "themes"):
        base = repo_root / "tools" / sub
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in PACK_SUFFIXES and "__pycache__" not in p.parts:
                entries.append((p, f"{LIB}/{sub}/{p.relative_to(base).as_posix()}", PLAIN))
    return entries


def required(flavor: str) -> "list[str]":
    """Archive names a distribution zip must contain (checked after assembly and by tests)."""
    names = [f"{LIB}/hub.py", f"{LIB}/index.html", f"{LIB}/review.html", f"{LIB}/setup.html", f"{LIB}/settings.html",
             f"{LIB}/preset.json", f"{LIB}/runtime.py", f"{LIB}/accounts.py", f"{LIB}/git_sync.py",
             f"{LIB}/shortcuts.py", f"{LIB}/pr_classification.py", f"{LIB}/citygml.sh", f"{LIB}/citygml.ps1",
             f"{LIB}/LICENSE", f"{LIB}/NOTICE", f"{LIB}/THIRD_PARTY_NOTICES.md",
             f"{LIB}/attr_editor/app.py", f"{LIB}/attr_editor/index.html", f"{LIB}/attr_editor/setup.html",
             f"{LIB}/attr_editor/viewer.html", f"{LIB}/tex_editor/app.py", f"{LIB}/tex_editor/index.html",
             f"{LIB}/i18n/i18n_loader.py", f"{LIB}/themes/theme_loader.py"]
    names += [f"{LIB}/i18n/catalogs/{app}/{lang}.json" for app in ("hub", "attr_editor", "tex_editor")
              for lang in ("en", "ja", "de")]
    if flavor == "windows":
        names += [f"{LIB}/start-windows.bat", f"{LIB}/PythonPortable/python.exe", f"{LIB}/PythonPortable/LICENSE.txt",
                  f"{LIB}/PortableGit/cmd/git.exe", f"{LIB}/PortableGit/LICENSE.txt"]
    return names


def _add(z: zipfile.ZipFile, src: Path, arc: str, mode: int) -> None:
    info = zipfile.ZipInfo(arc)
    info.external_attr = (mode & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, src.read_bytes())


def build(repo_root, flavor: str, version: str, out_dir, portable_dir=None) -> Path:
    """Write the zip and return its path. portable_dir holds PortableGit/ and PythonPortable/
    (Windows flavor; required)."""
    if flavor not in FLAVORS:
        raise ValueError(f"unknown flavor {flavor!r}")
    repo_root, out_dir = Path(repo_root), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / artifact_name(flavor, version)
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc, mode in manifest(repo_root, flavor):
            if not src.is_file():
                raise FileNotFoundError(f"manifest source missing: {src}")
            _add(z, src, arc, mode)
        if flavor == "windows":
            if portable_dir is None:
                raise ValueError("the Windows flavor needs --portable (PortableGit/ and PythonPortable/)")
            for bundle in BUNDLE_DIRS:
                base = Path(portable_dir) / bundle
                if not base.is_dir():
                    raise FileNotFoundError(f"{bundle}/ not found under {portable_dir}")
                for p in sorted(base.rglob("*")):
                    if p.is_file():
                        _add(z, p, f"{LIB}/{bundle}/{p.relative_to(base).as_posix()}", PLAIN)
    if flavor == "windows" and artifact.stat().st_size > WINDOWS_SIZE_LIMIT:
        raise RuntimeError(f"Windows bundle exceeds 70 MiB: {artifact.stat().st_size / (1024 * 1024):.2f} MiB")
    return artifact


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flavor", choices=sorted(FLAVORS), required=True)
    ap.add_argument("--tag", default="", help="release tag (hub-vX.Y.Z); anything else builds 'dev'")
    ap.add_argument("--out", default=None, help="output folder (default: tools/hub/dist)")
    ap.add_argument("--portable", default=None, help="folder holding PortableGit/ and PythonPortable/ (Windows)")
    ap.add_argument("--repo", default=None, help="repository root (default: the parent of scripts/)")
    args = ap.parse_args(argv)
    repo_root = Path(args.repo) if args.repo else repo_root_default()
    out = Path(args.out) if args.out else repo_root / "tools" / "hub" / "dist"
    artifact = build(repo_root, args.flavor, version_from_tag(args.tag), out, args.portable)
    print(f"{artifact} ({artifact.stat().st_size / (1024 * 1024):.2f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
