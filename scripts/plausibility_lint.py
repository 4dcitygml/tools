#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Data plausibility lint (convention-dependent layer): unknown-value sentinels, plausible ranges, code lists.

The generic geometric-structure checks are separated out into `citygml_lint.py`
(data-agnostic). This module is the **domain layer** on top of it and handles
only **checks that depend on data conventions**; each check applies only where the
data uses that convention, so it says nothing about data that does not:

- Validity after **excluding** the values the city's dialect pack says mean "unknown"
  (Pack.unknown_value; a city without one has none).
- Plausible ranges for measuredHeight / storeys (warnings for implausible values); a height is
  compared in metres, converted from its own unit (uom: feet in US data).
- Codelist consistency: whether the code value of an attribute with a `codeSpace`
  exists in the referenced codelist (`codelists/*.xml`). "Unknown" codes are
  officially listed in the codelists, so the existence check naturally accepts
  them (not treated as anomalies).

Check rules (warnings = implausible but may pre-exist in base; non-blocking = human review C):
    - nonpositive_height    measuredHeight ≤ 0 (sentinels excluded)
    - height_out_of_range   measuredHeight above the limit (default 300m)
    - storeys_out_of_range  storey count above the limit (default 200)
    - invalid_code          code value absent from the codelist referenced by codeSpace
    - invalid_code_changed  the same, but the pull request wrote the value (the building's name,
                            codeSpace and value are not in base): an ERROR, the `code-list` row

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
from typing import Callable, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_constants import (  # noqa: E402
    MAX_MEASURED_HEIGHT_M,
    MAX_STOREYS,
    PLAUSIBILITY_LINT_MARKER,
)
from scripts.citygml_lint import guarded, number_by_localname, run_main  # noqa: E402
from scripts import city_declaration  # noqa: E402
from scripts import codelists  # noqa: E402
from scripts.citygml_xml import buildings as _buildings, gml_id as _gml_id, localname as _localname  # noqa: E402

_LABEL = {
    "nonpositive_height": "Height ≤ 0",
    "height_out_of_range": "Height above limit",
    "storeys_out_of_range": "Storey count above limit",
    "invalid_code": "Invalid code",
    "invalid_code_changed": "Invalid code (changed by this pull request)",
}


@lru_cache(maxsize=None)
def _codelist(path: Path) -> Optional[dict]:
    """A resolved list file -> {code: label}; a broken file gives None (out of scope)."""
    return codelists.read(path)


_FOOT_M = {"ft": 0.3048, "ftUS": 1200 / 3937}


def _metres_per_unit(building, localname: str) -> float:
    """Metres per unit of the element's uom (feet in US data); metres when it names none."""
    for el in building.iter():
        if _localname(el) == localname:
            uom = (el.get("uom") or "").rsplit(":", 1)[-1].rsplit("#", 1)[-1]
            return _FOOT_M.get(uom, 0.3048 if uom.lower() in ("foot", "feet", "[ft_i]") else 1.0)
    return 1.0


def _coded_values(building) -> set:
    """The (element name, codeSpace, value) of every coded value the building writes."""
    return {(_localname(el), el.get("codeSpace"), (el.text or "").strip())
            for el in building.iter() if isinstance(el.tag, str) and el.get("codeSpace")}


def base_coded_values(base_bytes: bytes) -> dict:
    """gml:id -> the coded values of that building in the file at base (b"": the PR adds the file)."""
    return {_gml_id(b): _coded_values(b) for b in _buildings(base_bytes)} if base_bytes else {}


def check_building(
    building, geom_index: Optional[dict] = None, gml_dir: Optional[Path] = None, unknown_value=None,
    base_codes: Optional[Callable[[], dict]] = None,
) -> dict:
    """Check one building's convention-dependent plausibility (warnings; one error, below).

    The values `unknown_value` says mean "unknown" (the city's dialect pack's sentinels) are the
    data's own representation and are excluded. Codelist consistency is checked only when
    gml_dir (the gml file's directory) is available (needed to resolve codeSpace relative paths).
    A code absent from its list is an error when this pull request wrote it (the building's
    (name, codeSpace, value) is not in base) and a warning when base already had it. base_codes
    (CI only) returns gml:id -> base's coded values, read when first needed; without it every
    invalid code is a warning (a file-level run has no base).
    """
    warnings: list = []
    errors: list = []

    def warn(code, detail):
        warnings.append({"code": code, "detail": detail})

    def unknown(local, value) -> bool:   # a pack's sentinels mean "unknown" only in its data
        return bool(unknown_value and unknown_value(local, value))

    h = number_by_localname(building, "measuredHeight")
    if h is not None and not unknown("measuredHeight", h):
        if h <= 0:
            warn("nonpositive_height", f"measuredHeight = {h} ≤ 0 (implausible)")
        elif h * _metres_per_unit(building, "measuredHeight") > MAX_MEASURED_HEIGHT_M:
            warn("height_out_of_range", f"measuredHeight = {h} exceeds the limit of {MAX_MEASURED_HEIGHT_M} m")

    for local in ("storeysAboveGround", "storeysBelowGround"):
        s = number_by_localname(building, local)
        if s is None or unknown(local, s):
            continue
        if s > MAX_STOREYS:
            warn("storeys_out_of_range", f"{local} = {int(s)} exceeds the limit of {MAX_STOREYS}")

    if gml_dir is not None:
        seen: set = set()
        for el in building.iter():
            if not isinstance(el.tag, str):  # skip comments/PIs
                continue
            codespace = el.get("codeSpace")
            value = (el.text or "").strip()
            path = codelists.resolve(codespace, gml_dir) if value else None   # external or missing: out of scope
            table = _codelist(path) if path else None
            if table is None or value in table:
                continue
            name = _localname(el)
            key = (name, codespace, value)
            if key in seen:
                continue
            seen.add(key)
            if base_codes is not None and key not in base_codes().get(_gml_id(building), ()):
                errors.append({"code": "invalid_code_changed", "detail":
                               f"{name} = {value} does not exist in {Path(codespace).name}; "
                               "this pull request changed the value"})
            else:
                warn("invalid_code", f"{name} = {value} does not exist in {Path(codespace).name}")

    return {"errors": errors, "warnings": warnings}


def check_fn_for(gml_path, base_bytes: Optional[bytes] = None):
    """Return a check function that binds the codelist resolution base to the gml file path (for run_main's
    check_fn_for). base_bytes (CI: the file at base) turns the invalid codes this PR wrote into errors."""
    gml_dir = Path(gml_path).resolve().parent
    unknown_value = city_declaration.read(".").pack.unknown_value   # the city's dialect (run in its checkout)

    base = lru_cache(maxsize=1)(lambda: base_coded_values(base_bytes))   # parsed only when a code is invalid

    def check(building, geom_index: Optional[dict] = None) -> dict:
        return check_building(building, geom_index, gml_dir=gml_dir, unknown_value=unknown_value,
                              base_codes=None if base_bytes is None else base)

    return check


def main(argv: Optional[list] = None) -> int:
    return run_main(
        argv, check_building, PLAUSIBILITY_LINT_MARKER,
        "🏙️ Data plausibility check (sentinels, ranges, code lists)", _LABEL,
        check_fn_for=check_fn_for, doc=__doc__,
    )


if __name__ == "__main__":
    raise SystemExit(guarded(main))
