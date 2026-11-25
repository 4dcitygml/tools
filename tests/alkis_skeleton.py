# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""A skeleton ALKIS pack (Bavarian LoD2 / AdV), for the v2.0.0 interface check (stage 5b).

Test only: not under dialects/, not in any bundle, not selectable in a declaration (`dialect`
stays plateau). It is written against the Pack interface alone and answers every question for
data shaped like the Munich practice city (tests/fixtures/alkis/). A question it cannot answer
through the interface is a generic-code assumption, fixed in generic code (REQ-dialect-packs 8).
"""
from __future__ import annotations

import re

from dialects import Pack
from scripts.citygml_dialect import crs_transformer

CRS = "EPSG:25832"   # ETRS89 / UTM zone 32N
# the Amtlicher Gemeindeschluessel (8 digits) in the generic attribute the AdV export writes
AGS_RE = re.compile(rb'<(?:\w+:)?stringAttribute\s+name="Gemeindeschluessel"\s*>\s*<(?:\w+:)?value>\s*(\d{8})\s*<')


def municipality(span: bytes) -> "str | None":
    """The member's AGS when it names exactly one."""
    values = {m.group(1).decode("ascii") for m in AGS_RE.finditer(span)}
    return values.pop() if len(values) == 1 else None


def tile_frame(code: str) -> "list[float] | None":
    """A 1 km tile (`<E km>_<N km>_<part>`, EPSG:25832) as [south, west, north, east]."""
    m = re.fullmatch(r"(\d{3})_(\d{4})(?:_\d+)?", code)
    if not m:
        return None
    to_latlon = crs_transformer(CRS)
    e, n = int(m.group(1)) * 1000, int(m.group(2)) * 1000
    corners = [to_latlon(x, y) for x in (e, e + 1000) for y in (n, n + 1000)]
    return [min(c[0] for c in corners), min(c[1] for c in corners),
            max(c[0] for c in corners), max(c[1] for c in corners)]


PACK = Pack(
    name="alkis",
    building_id={"type": "core:externalReference"},   # the ALKIS object identifier
    crs=CRS,
    kinds=frozenset(),                  # no bulk kinds of its own (the identity kinds are generic)
    bulk_kinds=(),
    schema_dirs=("codelists",),         # the AdV code lists a city keeps beside its data
    municipality=municipality,
    municipality_code=r"\d{8}",          # the AGS
    buildings_dir="",                   # the data folder itself
    tile_name=r"(\d{3}_\d{4}_\d+)\.gml",  # 690_5335_1.gml: the 1 km tile and its part
    tile_frame=tile_frame,
    appearance_dir=None,                # the generic <stem>_appearance
    namespaces=(),                      # plain CityGML 1.0 / 2.0 modules only
    building_id_text=r"(?<![\w-])(DE[A-Z]{2}[A-Za-z0-9]{12})(?![\w-])",   # DEBYvAAAAABTzIbC
    sources=None,                       # no source-note convention
    default_codelists=(("function", "Building_function.xml"),),   # the AdV export writes no codeSpace
    edition_of=None,                    # one edition
    attribute_key=None,
    unknown_value=None,                 # no sentinels
    unknown_label=None,
    quality_axis=None,                  # no graded data quality (the Datenquelle codes are a next-release question)
    quality_tier=None,
    quality_tiers=(),
    citygml3=None,                      # the plain CityGML 3 upgrade (no ADE)
    citygml3_schemas=(),
    tools=(),
)
