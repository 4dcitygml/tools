# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The PLATEAU pack (i-UR, the Japanese urban model profile): its answers to the pack interface.

The only place PLATEAU / i-UR knowledge may live (a test refuses new literals elsewhere; the
rest moves in with phases 3 and 4, REQ-dialect-packs.md section 5).
"""
from __future__ import annotations

import re

from dialects import Pack, Sources, lazy

# the municipality code of a building (uro:city, any prefix): the 5-digit local government code
CITY_RE = re.compile(rb"<(?:\w+:)?city(?:\s[^>]*)?>([^<]+)</(?:\w+:)?city>")
MUNICIPALITY_CODE = r"\d{5}"


def municipality_values(span: bytes) -> "set[str]":
    """Every non-empty municipality code (uro:city) of one member."""
    values = {m.group(1).decode("utf-8", errors="replace").strip() for m in CITY_RE.finditer(span)}
    values.discard("")
    return values


def municipality(span: bytes) -> "str | None":
    """The member's municipality code when it has exactly one and it is a code (a member naming
    two cities, or a value of another form, has no determinable municipality), else None."""
    values = municipality_values(span)
    value = next(iter(values)) if len(values) == 1 else ""
    return value if re.fullmatch(MUNICIPALITY_CODE, value) else None


def mesh_frame(code: str) -> "list[float] | None":
    """A JIS X 0410 mesh code (8 digits: level 3; 9 digits: its quarter) as [south, west, north, east]."""
    if not code.isdigit() or len(code) < 8:
        return None
    lat = int(code[0:2]) * 2 / 3
    lon = int(code[2:4]) + 100
    lat += int(code[4]) * 2 / 3 / 8
    lon += int(code[5]) / 8
    lat += int(code[6]) * 2 / 3 / 80
    lon += int(code[7]) / 80
    dlat, dlon = 2 / 3 / 80, 1 / 80
    if len(code) >= 9:
        d = int(code[8]) - 1  # 1=SW 2=SE 3=NW 4=NE
        dlat, dlon = dlat / 2, dlon / 2
        lat += (d // 2) * dlat
        lon += (d % 2) * dlon
    return [lat, lon, lat + dlat, lon + dlon]


PACK = Pack(
    name="plateau",
    building_id={"type": "uro:buildingID"},
    crs="EPSG:6697",
    # municipality codes and the i-UR code lists; the attribute manifests change i-UR attributes
    kinds=frozenset({"scope-extract", "schema-update"}),
    schema_dirs=("codelists", "schemas"),   # the i-UR code lists and XSD profile of an edition
    municipality=municipality,
    municipality_code=MUNICIPALITY_CODE,
    buildings_dir="udx/bldg",                # a delivery package keeps its buildings in udx/bldg
    tile_name=r"(\d+)_bldg_.*_op\.gml",      # <mesh code>_bldg_<crs>_op.gml
    tile_frame=mesh_frame,
    # the source notes: the set 出典 over the thematicSrcDesc code list; 根拠資料 entries cite documents;
    # 898 unknown and 999 not created are never the source of a change
    namespaces=(("https://www.geospatial.jp/iur/uro/3.2", "uro"), ("https://www.geospatial.jp/iur/uro/3.1", "uro")),
    building_id_text=r"(?<![\w-])(\d{5}-bldg-[A-Za-z0-9_-]+)",   # <municipality code>-bldg-<number>
    appearance_dir=lambda stem: stem.removesuffix("_op") + "_appearance",   # <mesh>_bldg_<crs>_appearance
    # the PLATEAU-only operator tools (maintainer, 10-03), listed by python3 -m scripts beside the generic ones
    tools=(
        ("Data preparation", "setup_city_data"), ("Data preparation", "split_4th_mesh"),
        ("Data preparation", "merge_to_3rd_mesh"), ("Data preparation", "analyze_yearly_citygml_mesh"),
        ("Data preparation", "plan_yearly_citygml_transition"),
        ("Maintenance and quality", "source_update_manifest"), ("Maintenance and quality", "carry_forward_manifest"),
        ("Maintenance and quality", "lod0_semantic_manifest"), ("Maintenance and quality", "codelist_crosswalk"),
        ("Maintenance and quality", "carried_codespace_report"), ("Maintenance and quality", "semantic_registry"),
        ("Maintenance and quality", "plateau_codelists"),
        ("Conversion and development", "generic_to_uro"), ("Conversion and development", "convert_citygml2_to3_iur4"),
    ),
    # its bulk manifest kinds, the tool that reproduces each and the gate's rule (the identity kinds are generic)
    bulk_kinds=(("source-update", "source_update_manifest", lazy("dialects.plateau.bulk_checks:source_update")),
                ("carry-forward", "carry_forward_manifest", lazy("dialects.plateau.bulk_checks:source_update")),
                ("semantic-correction", "lod0_semantic_manifest", lazy("dialects.plateau.bulk_checks:semantic_correction"))),
    # its semantics (the modules are imported when asked: the hub loads this file and ships none of them)
    edition_of=lazy("dialects.plateau.semantic_registry:detect_edition"),
    attribute_key=lazy("dialects.plateau.semantic_registry:attribute_key"),
    unknown_value=lazy("dialects.plateau.sentinels:is_sentinel"),
    unknown_label=lazy("dialects.plateau.plateau_codelists:is_unknown_label"),
    quality_axis=lazy("dialects.plateau.plateau_codelists:is_quality_axis"),
    quality_tier=lazy("dialects.plateau.plateau_codelists:quality_tier"),
    quality_tiers=("measured", "documented", "provisional", "missing", "other"),   # plateau_codelists.quality_tier's, in order
    citygml3=lazy("dialects.plateau.convert_citygml2_to3_iur4:export_file"),   # i-UR 3 -> i-UR 4 + the provisional i4d-UR (consent needed)
    citygml3_schemas=("dialects/plateau/i4dur/1.0/i4dUR.xsd",),
    default_codelists=(),                     # every coded value names its list (codeSpace)
    sources=Sources("出典", "DataQualityAttribute_thematicSrcDesc.xml", "根拠資料", frozenset({"898", "999"})),
)
