#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Export a city's CityGML 2.0 / 1.0 buildings as CityGML 3.0 (REQ-dialect-packs section 7).

The core conversion is the pinned citygml-tools executable's `upgrade`, for every city. The
inputs are the city's declaration: its data folders (`data_dirs`) and, inside one, the folder
its dialect pack keeps the buildings in. A pack with an ADE converts it in its own hook
(Pack.citygml3); without a pack, generic attributes and appearances
pass through citygml-tools, and a namespace that is neither CityGML, GML, xAL nor XLink is
refused (fail closed: nothing is dropped unseen). Every output is validated by citygml-tools
against the CityGML 3.0 schemas and the pack's (Pack.citygml3_schemas).

    python3 scripts/export_citygml3.py export --repo CITY --outdir OUT --citygml-tools PATH [--accept-provisional]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dialects  # noqa: E402
from scripts import city_declaration  # noqa: E402
from scripts.citygml_xml import gml_id, qname  # noqa: E402
from scripts.safe_xml import safe_iterparse  # noqa: E402

# What plain CityGML may use: the CityGML modules (1.0, 2.0), GML, xAL addresses, XLink, XSI
UNDERSTOOD = ("http://www.opengis.net/citygml/", "http://www.opengis.net/gml", "urn:oasis:names:tc:ciq:xsdschema:xAL:",
              "http://www.w3.org/1999/xlink", "http://www.w3.org/2001/XMLSchema-instance")


class ConversionError(RuntimeError):
    """The data cannot be converted without losing something: nothing is written."""


@dataclass
class Scan:
    """What one pass over a source reads (W3: the converter read its source six times)."""
    qnames: set = field(default_factory=set)          # (namespace, local name) of every element
    appearance_ids: set = field(default_factory=set)  # the gml:id of every Appearance
    image_dirs: set = field(default_factory=set)      # the folders of its local texture images


def scan(source: Path, on_member=None) -> Scan:
    """One pass over a CityGML file. `on_member(element)` sees each member of the city model
    (a cityObjectMember, an appearanceMember) whole before it is released."""
    seen, depth, root = Scan(), 0, None
    for event, el in safe_iterparse(str(source), events=("start", "end"), huge_tree=True):
        if event == "start":
            root = el if root is None else root
            depth += 1
            continue
        depth -= 1
        if not isinstance(el.tag, str):
            continue
        ns, local = qname(el)
        seen.qnames.add((ns, local))
        if local == "Appearance":
            if gml_id(el):
                seen.appearance_ids.add(gml_id(el))
        elif local == "imageURI" and el.text:
            uri = el.text.strip()
            if uri and "://" not in uri and not uri.startswith("/"):
                seen.image_dirs.add(str(Path(uri).parent))
        if depth == 1:   # a member of the city model is complete
            if on_member is not None:
                on_member(el)
            el.clear()
            while el.getprevious() is not None:
                del root[0]
    return seen


def refuse_unknown(found: Scan, source: Path) -> None:
    """Plain CityGML only: an element of any other vocabulary would not survive the upgrade."""
    unknown = sorted({ns for ns, _local in found.qnames if ns and not ns.startswith(UNDERSTOOD)})
    if unknown:
        raise ConversionError(f"{source}: no converter for {', '.join(unknown)} (this city's dialect pack has none)")


def _env() -> dict:
    return {**os.environ, "TZ": "UTC", "LC_ALL": "C", "LANG": "C"}


def run_core_upgrade(source: Path, output: Path, executable: Path, found: Scan) -> "tuple[Path, str]":
    """citygml-tools `upgrade` of one file into output's folder: (the written file, the tool's version)."""
    if source.resolve() == output.resolve() or source.parent.resolve() == output.parent.resolve():
        raise ConversionError("output must be in a directory separate from the canonical source")
    if output.exists():
        raise ConversionError(f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    for folder in found.image_dirs:   # the tool writes the images below their own folders
        (output.parent / folder).mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(executable), "--version"], check=True, capture_output=True, text=True, env=_env()).stdout.strip()
    subprocess.run([str(executable), "upgrade", "--no-pretty-print", "-o", str(output.parent), str(source)], check=True, env=_env())
    generated = output.parent / source.name
    if not generated.exists():
        raise ConversionError(f"citygml-tools did not create expected file: {generated}")
    return generated, version


def validate(files: "list[Path]", executable: Path, schemas=()) -> None:
    """citygml-tools `validate` against the CityGML schemas and the pack's: one run per folder (the
    tool takes one path, a glob allowed)."""
    schema = [f"--schema={','.join(str(s) for s in schemas)}"] if schemas else []
    for folder in sorted({f.parent for f in files}):
        run = subprocess.run([str(executable), "validate", *schema, str(folder / "*.gml")],
                             capture_output=True, text=True, env=_env())
        if run.returncode != 0 or "All files are valid" not in (run.stdout + run.stderr):
            tail = "\n".join((run.stdout + run.stderr).strip().splitlines()[-6:])
            raise ConversionError(f"{folder}: the CityGML 3.0 output is not schema-valid:\n{tail}")


def convert_plain(source: Path, output: Path, executable: Path) -> None:
    """Plain CityGML: the upgrade alone, after the vocabulary check."""
    found = scan(source)
    refuse_unknown(found, source)
    generated, _version = run_core_upgrade(source, output, executable, found)
    if generated != output:
        os.replace(generated, output)


def export(repo: Path, outdir: Path, executable: Path, accept_provisional: bool = False) -> "list[Path]":
    """Every building file of the city's data folders, converted under outdir/<data folder>/..."""
    declaration = city_declaration.read(repo)
    pack = declaration.pack
    written: list[Path] = []
    for data_dir in declaration.data_dirs:
        folder = repo / data_dir
        buildings = dialects.buildings_dir(folder, pack)
        out_root = outdir / data_dir
        if (folder / "codelists").is_dir():   # codeSpace references are relative to the data
            shutil.copytree(folder / "codelists", out_root / "codelists", dirs_exist_ok=True)
        for source in sorted(buildings.glob("*.gml")):
            output = out_root / source.relative_to(folder)
            if pack.citygml3:
                pack.citygml3(source, output, out_root, executable, accept_provisional)
            else:
                convert_plain(source, output, executable)
            written.append(output)
    if not written:
        raise ConversionError(f"{repo}: no building files in the declared data folders")
    validate(written, executable, [ROOT / s for s in pack.citygml3_schemas])
    return written


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("export", help="every building file of a city clone")
    e.add_argument("--repo", type=Path, required=True)
    e.add_argument("--outdir", type=Path, required=True)
    e.add_argument("--citygml-tools", type=Path, required=True)
    e.add_argument("--accept-provisional", action="store_true",
                   help="a pack whose output uses a provisional namespace needs this consent")
    args = parser.parse_args(argv)
    try:
        written = export(args.repo, args.outdir, args.citygml_tools, args.accept_provisional)
    except (ConversionError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"{len(written)} file(s) converted and valid")
    return 0


if __name__ == "__main__":
    # the module by its package name: a pack's hook raises scripts.export_citygml3.ConversionError
    from scripts.export_citygml3 import main as package_main
    raise SystemExit(package_main())
