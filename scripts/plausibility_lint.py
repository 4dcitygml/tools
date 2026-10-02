#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Data plausibility lint (convention-dependent layer): unknown-value sentinels, plausible ranges, code lists.

The generic geometric-structure checks are separated out into `citygml_lint.py`
(data-agnostic). This module is the **domain layer** on top of it and handles
only **checks that depend on data conventions**; each check applies only where the
data uses that convention, so it says nothing about data that does not:

- Validity after **excluding** unknown-value sentinels (±9999 etc.) via the
  single definition in `scripts/sentinels.py`.
- Plausible ranges for measuredHeight / storeys (warnings for implausible values); a height is
  compared in metres, converted from its own unit (uom: feet in US data).
- Codelist consistency: whether the code value of an attribute with a `codeSpace`
  exists in the referenced codelist (`codelists/*.xml`). "Unknown" codes
  (`plateau_codelists.unknown_codes`) are officially listed in the codelists,
  so the existence check naturally accepts them (not treated as anomalies).

Check rules (all warnings = implausible but may pre-exist in base; non-blocking = human review C):
    - nonpositive_height    measuredHeight ≤ 0 (sentinels excluded)
    - height_out_of_range   measuredHeight above the limit (default 300m)
    - storeys_out_of_range  storey count above the limit (default 200)
    - invalid_code          code value absent from the codelist referenced by codeSpace

codeSpace is a relative path based on the gml file's location (e.g.
`../../codelists/Building_usage.xml`), so codelist consistency is only enabled
when the file path is known (via CLI / CI, `check_fn_for`). When the reference
cannot be resolved (external URL, missing file, broken XML), it is out of scope
= no warning (avoids false positives).

The engine (scanning, CI collection, Markdown rendering) and geometry/attribute
parsing are reused from `citygml_lint.py`. In CI, citygml_lint (geometric
structure, error=fail) and this module (plausibility, warning) post separate comments.

Usage:
    python scripts/plausibility_lint.py FILE.gml [FILE2.gml ...]
    python scripts/plausibility_lint.py --file-list L --base-sha B --head-sha H --repo R  # CI: changed buildings only
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_constants import (  # noqa: E402
    MAX_MEASURED_HEIGHT_M,
    MAX_STOREYS,
    PLAUSIBILITY_LINT_LEGACY_MARKER,
    PLAUSIBILITY_LINT_MARKER,
)
from scripts.citygml_lint import guarded, number_by_localname, run_main  # noqa: E402
from scripts.plateau_codelists import load_codelist  # noqa: E402
from scripts.sentinels import is_sentinel  # noqa: E402

_LABEL = {
    "nonpositive_height": "Height ≤ 0",
    "height_out_of_range": "Height above limit",
    "storeys_out_of_range": "Storey count above limit",
    "invalid_code": "Invalid code",
}


@lru_cache(maxsize=None)
def _codelist(resolved_path: str) -> Optional[dict]:
    """Resolved codelist path -> {code: label}. Missing file or broken XML gives None (out of scope)."""
    p = Path(resolved_path)
    if not p.is_file():
        return None
    try:
        return load_codelist(p)
    except etree.XMLSyntaxError:
        return None


_FOOT_M = {"ft": 0.3048, "ftUS": 1200 / 3937}


def _metres_per_unit(building, localname: str) -> float:
    """Metres per unit of the element's uom (feet in US data); metres when it names none."""
    for el in building.iter():
        if isinstance(el.tag, str) and etree.QName(el).localname == localname:
            uom = (el.get("uom") or "").rsplit(":", 1)[-1].rsplit("#", 1)[-1]
            return _FOOT_M.get(uom, 0.3048 if uom.lower() in ("foot", "feet", "[ft_i]") else 1.0)
    return 1.0


def check_building(
    building, geom_index: Optional[dict] = None, gml_dir: Optional[Path] = None
) -> dict:
    """Check one building's convention-dependent plausibility (all warnings).

    Unknown-value sentinels (±9999 etc., sentinels.py) are the data's own
    "unknown" representation and are excluded. Codelist consistency is checked
    only when gml_dir (the gml file's directory) is available (needed to resolve
    codeSpace relative paths).
    """
    warnings: list = []

    def warn(code, detail):
        warnings.append({"code": code, "detail": detail})

    h = number_by_localname(building, "measuredHeight")
    if h is not None and not is_sentinel("measuredHeight", h):
        if h <= 0:
            warn("nonpositive_height", f"measuredHeight = {h} ≤ 0 (implausible)")
        elif h * _metres_per_unit(building, "measuredHeight") > MAX_MEASURED_HEIGHT_M:
            warn("height_out_of_range", f"measuredHeight = {h} exceeds the limit of {MAX_MEASURED_HEIGHT_M} m")

    for local in ("storeysAboveGround", "storeysBelowGround"):
        s = number_by_localname(building, local)
        if s is None or is_sentinel(local, s):
            continue
        if s > MAX_STOREYS:
            warn("storeys_out_of_range", f"{local} = {int(s)} exceeds the limit of {MAX_STOREYS}")

    if gml_dir is not None:
        seen: set = set()
        for el in building.iter():
            if not isinstance(el.tag, str):  # skip comments/PIs
                continue
            codespace = el.get("codeSpace")
            if not codespace or "://" in codespace:  # external URLs cannot be resolved = out of scope
                continue
            value = (el.text or "").strip()
            if not value:
                continue
            table = _codelist(str((Path(gml_dir) / codespace).resolve()))
            if table is None or value in table:
                continue
            name = etree.QName(el).localname
            key = (name, codespace, value)
            if key in seen:
                continue
            seen.add(key)
            warn("invalid_code", f"{name} = {value} does not exist in {Path(codespace).name}")

    return {"errors": [], "warnings": warnings}


def check_fn_for(gml_path):
    """Return a check function that binds the codelist resolution base to the gml file path (for run_main's check_fn_for)."""
    gml_dir = Path(gml_path).resolve().parent

    def check(building, geom_index: Optional[dict] = None) -> dict:
        return check_building(building, geom_index, gml_dir=gml_dir)

    return check


def main(argv: Optional[list] = None) -> int:
    return run_main(
        # the deprecated marker too, until v4.0.0 (Exchange Contract A10)
        argv, check_building, f"{PLAUSIBILITY_LINT_MARKER}\n{PLAUSIBILITY_LINT_LEGACY_MARKER}",
        "🏙️ Data plausibility check (sentinels, ranges, code lists)", _LABEL,
        check_fn_for=check_fn_for,
    )


if __name__ == "__main__":
    raise SystemExit(guarded(main))
