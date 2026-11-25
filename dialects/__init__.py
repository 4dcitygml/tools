# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Dialect packs: what is true for one national CityGML profile only (v2.0.0, X1).

Generic code knows CityGML, GML, git and GitHub. A city names its pack in 4dcitygml.json
(`dialect`); the declaration reader asks dialect(name) once, and generic code asks the Pack it
returns, never a pack module. A city without one is plain CityGML: PLAIN has no answers.

Standard library only: the hub bundle ships this package next to the hub.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class Sources:
    """How a profile records where a value came from (docs/provenance-rules.md): one named set of
    notes per building, coded with the code list of the building's own quality record."""
    set_name: str                          # the gen:genericAttributeSet of the notes (at most one per building)
    codelist: str                          # the code list of the notes and of the quality record
    evidence_prefix: str = ""              # entries named so cite documents; they hold no source code
    not_sources: frozenset = frozenset()   # codes never chosen as the source of a change (unknown, ...)


@dataclass(frozen=True)
class Pack:
    """The pack interface: everything generic code may ask a national profile (REQ-dialect-packs
    section 3). Every pack answers every field in its Pack(...) (an empty or None answer is the
    generic behaviour; tests/test_pack_skeleton.py checks); PLAIN answers none. One line per field:

    name               the folder under dialects/ (`dialect` in 4dcitygml.json)
    building_id        the identity rule unless the city declares its own (building_identity)
    crs                the CRS of its data unless the city declares one
    kinds              the Change-Type kinds it adds to the generic ones (the commit scope gate)
    bulk_kinds         (kind, tool, check): its manifest kinds, the tool that reproduces each, the gate's rule
    schema_dirs        the folders of its edition artifacts: code lists, schema profiles (repo scope, schema-update)
    municipality       a member's bytes -> its municipality code, or None
    municipality_code  the form of a municipality code (a regular expression; Scope-Municipality)
    buildings_dir      where a data folder keeps its buildings ("": the folder itself)
    tile_name          its tile file names (a regular expression; group 1: the tile code)
    tile_frame         a tile code -> its frame [S, W, N, E] in latitude / longitude, or None
    appearance_dir     a tile file's stem -> the folder of its new images
    namespaces         its own namespaces as (URI, conventional prefix) pairs (the editor's prefixes)
    building_id_text   its building IDs as they appear in free text (a regular expression; the hub)
    sources            its source-note convention (Sources; without one the editors record no sources)
    default_codelists  (element local name, list file): the list of a coded value without codeSpace
    edition_of         a file's bytes -> the edition it is in (the building history)
    attribute_key      (attribute path, edition) -> its edition-independent key (the building history)
    unknown_value      (local name, value) -> whether it means "unknown" (a sentinel; the statistics, plausibility)
    unknown_label      a code list label -> whether it means "unknown" (the statistics)
    quality_axis       a code list's file name -> whether it grades data quality (the statistics)
    quality_tier       a quality code's label -> its tier (the statistics)
    quality_tiers      the tiers, in order
    citygml3           its CityGML 3 export hook (source, output, data output folder, citygml-tools,
                       consent to a provisional namespace); None: the plain upgrade (export_citygml3)
    citygml3_schemas   the schemas its CityGML 3 output is validated with besides CityGML 3.0's
    tools              its operator tools: (group, tool) pairs, run as python3 -m scripts <tool>
    """
    name: str = ""
    building_id: Optional[dict] = None
    crs: Optional[str] = None
    kinds: frozenset = frozenset()
    bulk_kinds: tuple = ()
    schema_dirs: tuple = ()
    municipality: Optional[Callable[[bytes], Optional[str]]] = None
    municipality_code: Optional[str] = None
    buildings_dir: str = ""
    tile_name: Optional[str] = None
    tile_frame: Optional[Callable[[str], Optional[list]]] = None
    appearance_dir: Optional[Callable[[str], str]] = None
    namespaces: tuple = ()
    building_id_text: Optional[str] = None
    sources: Optional[Sources] = None
    default_codelists: tuple = ()
    edition_of: Optional[Callable[[bytes], Optional[str]]] = None
    attribute_key: Optional[Callable[[str, str], Optional[str]]] = None
    unknown_value: Optional[Callable[[str, object], bool]] = None
    unknown_label: Optional[Callable[[str], bool]] = None
    quality_axis: Optional[Callable[[str], bool]] = None
    quality_tier: Optional[Callable[[str], str]] = None
    quality_tiers: tuple = ()
    citygml3: Optional[Callable] = None
    citygml3_schemas: tuple = ()
    tools: tuple = ()


PLAIN = Pack()


def lazy(target: str) -> Callable:
    """A pack's function ("module:function"), imported when it is first called: the hub loads
    each pack.py and ships none of a pack's other modules."""
    module, name = target.split(":")
    return lambda *args: getattr(importlib.import_module(module), name)(*args)


def dialect(name) -> Pack:
    """The pack dialects/<name>/pack.py defines; PLAIN for no name or one without a pack."""
    if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
            or not (Path(__file__).parent / name / "pack.py").is_file()):
        return PLAIN
    return importlib.import_module(f"{__name__}.{name}.pack").PACK


def packs() -> "list[Pack]":
    """Every pack under dialects/ (the tools that read a delivery package before any city declares it)."""
    return [dialect(p.parent.name) for p in sorted(Path(__file__).parent.glob("*/pack.py"))]


def buildings_dir(folder, pack: "Pack | None" = None) -> Path:
    """Where a data package keeps its buildings: the folder its pack (else any pack) names inside
    it, when it has one, else the package folder itself (W2: each tool looked for its own)."""
    folder = Path(folder)
    inner = next((folder / p.buildings_dir for p in ([pack] if pack else packs())
                  if p.buildings_dir and (folder / p.buildings_dir).is_dir()), None)
    return inner or folder
