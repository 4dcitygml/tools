#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Verify a hub distribution zip: layout, required files, and a smoke test of the
extracted program (the hub imports, resolves the bundled editors and packs from program/).

    python3 scripts/verify_bundle.py tools/hub/dist/citygml-hub-v1.3.0-macos.zip --flavor macos
    python3 scripts/verify_bundle.py <zip> --flavor windows --git-smoke <a git work tree>

The release workflow runs this after assembling (and, for the Windows zip, once more
under the bundled python.exe with --git-smoke so the ._pth isolated Python and MinGit are
exercised); the tests run it against a zip built from the source tree. Standard library
only, importable under the embeddable Python (no site-packages)."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import types
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_bundle  # noqa: E402


def check_archive(archive, flavor: str) -> "set[str]":
    """Required files present, only program/ at the top, the launcher executable. Returns the names."""
    archive = pathlib.Path(archive)
    zf = zipfile.ZipFile(archive)
    names = set(zf.namelist())
    missing = [n for n in build_bundle.required(flavor) if n not in names]
    if missing:
        raise RuntimeError(f"distribution zip missing required files: {missing}")
    top = {n.split("/", 2)[1] for n in names if n.startswith(f"{build_bundle.ROOT}/") and "/" in n[len(build_bundle.ROOT) + 1:]}
    stray = {n for n in names if not n.startswith(f"{build_bundle.ROOT}/")}
    if top - {"program"} or stray:
        raise RuntimeError(f"distribution zip has unexpected items at the top level: {sorted((top - {'program'}) | stray)}")
    if not (zf.getinfo(f"{build_bundle.LIB}/citygml.sh").external_attr >> 16) & 0o100:
        raise RuntimeError("program/citygml.sh does not have the execute bit")
    return names


def smoke(program: pathlib.Path, flavor: str, git_smoke=None) -> None:
    """Import the extracted hub and check it finds everything next to itself."""
    program = program.resolve()   # compare resolved paths (macOS /var → /private/var)
    spec = importlib.util.spec_from_file_location("hub_smoke", program / "hub.py")
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    fake = types.SimpleNamespace(root=program.parent / "no-clone")
    for key in ("attr_editor", "tex_editor"):
        found = hub.Hub._tool_app(fake, hub.TOOLS[key])
        if not found or program not in found.resolve().parents:
            raise RuntimeError(f"{key} does not resolve inside program/: {found}")
    for pack, mod in (("i18n", hub.runtime.i18n_loader), ("themes", hub.runtime.theme_loader)):
        if program not in pathlib.Path(mod.__file__).resolve().parents:
            raise RuntimeError(f"{pack} pack not loaded from program/: {mod.__file__}")
    if pathlib.Path(hub.runtime.SHARED_DIR).resolve() != program:
        raise RuntimeError(f"runtime.SHARED_DIR is {hub.runtime.SHARED_DIR}, not program/")
    for app in ("hub", "attr_editor", "tex_editor"):
        catalogs = sorted(p.name for p in (program / "i18n" / "catalogs" / app).glob("*.json"))
        if catalogs != ["de.json", "en.json", "ja.json"]:
            raise RuntimeError(f"{app} catalogs incomplete: {catalogs}")
    if git_smoke is not None:
        # under the bundled Python on Windows: the bundled MinGit and PythonPortable are the ones used
        expected_git = (program / "PortableGit" / "cmd" / "git.exe").resolve()
        if pathlib.Path(hub.runtime.git_exe() or "").resolve() != expected_git or not hub.runtime.git_bundled():
            raise RuntimeError(f"hub does not use the bundled MinGit: {hub.runtime.git_exe()}")
        if not hub.runtime.python_bundled() or pathlib.Path(hub.runtime.python_exe()).resolve().parent != (program / "PythonPortable").resolve():
            raise RuntimeError(f"hub does not use the bundled PythonPortable: {hub.runtime.python_exe()}")
        if hub.Hub(pathlib.Path(git_smoke))._git("rev-parse", "--is-inside-work-tree") != "true":
            raise RuntimeError("hub cannot read a repository with the bundled MinGit")


def verify(archive, flavor: str, git_smoke=None) -> str:
    """Everything above; returns the zip's SHA-256. The smoke test runs in a fresh
    interpreter (this one, or the bundled python.exe when run under it), so the extracted
    program is imported on its own and never served from modules already loaded here."""
    archive = pathlib.Path(archive)
    check_archive(archive, flavor)
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(archive).extractall(tmp)
        cmd = [sys.executable, str(pathlib.Path(__file__).resolve()), "--smoke",
               str(pathlib.Path(tmp, build_bundle.ROOT, "program")), "--flavor", flavor]
        if git_smoke is not None:
            cmd += ["--git-smoke", str(git_smoke)]
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stderr.strip() or r.stdout.strip()).splitlines()[-1])
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", nargs="?", help="the zip to verify")
    ap.add_argument("--flavor", choices=sorted(build_bundle.FLAVORS), required=True)
    ap.add_argument("--git-smoke", default=None, metavar="WORKTREE",
                    help="also prove the bundled git/python are used (run under the bundled python.exe)")
    ap.add_argument("--smoke", default=None, metavar="PROGRAM_DIR", help=argparse.SUPPRESS)   # internal: the fresh-interpreter half
    args = ap.parse_args(argv)
    if args.smoke:
        smoke(pathlib.Path(args.smoke), args.flavor, args.git_smoke)
        return 0
    if not args.archive:
        ap.error("the zip to verify is required")
    digest = verify(args.archive, args.flavor, args.git_smoke)
    print(f"{pathlib.Path(args.archive).name} verified; sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
