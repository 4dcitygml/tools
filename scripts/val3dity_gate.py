#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Topological-consistency gate (official engine val3dity, diff-based).

The substance of PLATEAU's official topology check (geometry-validator) is
**val3dity's error codes (100-405)**. This gate reproduces it without FME and
validates **only the buildings changed by the PR** with the official engine.

**Why a diff gate (important)**: the official PLATEAU LOD2 data itself already
contains ~0.09% topological defects (measured: the officially distributed LOD2
has a certain number of invalids originating from the source data). Therefore
requiring "100% pass overall" would fail unrelated PRs on pre-existing defects.
This gate compares base/head and warns **only about invalids newly introduced
by the PR (before valid or newly added -> after invalid)** (same philosophy as
the existing lint's changed-buildings-only scope). Pre-existing defects are
tolerated as the baseline.

Toolchain (all OSS, no FME needed):
    Extract changed buildings -> project EPSG:6697->UTM (pyproj, metric) -> convert to CityJSON (citygml-tools)
    -> val3dity (--planarity_d2p_tol 0.03 = compliant with §6.3 L12) -> compare report validity per building

External tools are specified via environment variables (CI provides them; if unset, default names on PATH):
    CITYGML_TOOLS      citygml-tools executable (default: "citygml-tools")
    VAL3DITY_CMD       val3dity executable (default: "val3dity")
When the external tools / pyproj are unavailable, **exit normally doing nothing, as advisory** (do not break CI).

Usage:
    # CI: validate only changed buildings of changed .gml files (warn only on base->head regressions)
    val3dity_gate.py --file-list L --base-sha B --head-sha H --repo R
    # Local: validate all buildings in the files (baseline health check)
    val3dity_gate.py FILE.gml [FILE2.gml ...]
exit code: 0 by default (advisory). Only with --enforce, 1 if there are regressions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_constants import VAL3DITY_MARKER, VAL3DITY_PLANARITY_D2P_M  # noqa: E402
from scripts.diff_citygml import GML_NS, _gml_id, _local, load_buildings  # noqa: E402
from scripts.citygml_lint import _changed_ids  # noqa: E402
from scripts.safe_xml import safe_fromstring  # noqa: E402

CITYGML_TOOLS = os.environ.get("CITYGML_TOOLS", "citygml-tools")
VAL3DITY_CMD = os.environ.get("VAL3DITY_CMD", "val3dity")
_COORD_TAGS = {"posList", "pos", "lowerCorner", "upperCorner"}

# val3dity error code -> label (for report display; source: val3dity/geometry-validator).
_CODE_LABEL = {
    "101": "too few points", "102": "consecutive points too close", "103": "consecutive points too close", "104": "ring self-intersection",
    "105": "collapsed geometry", "201": "inner/outer ring intersection", "202": "duplicated rings", "203": "non-planar (distance to plane)",
    "204": "non-planar (normals deviation)", "205": "inner ring touches outer", "206": "hole outside the outer ring",
    "207": "nested holes", "208": "wrong face orientation", "300": "not 2-manifold", "301": "too few faces",
    "302": "shell not closed", "303": "faces intersect", "305": "disconnected components", "306": "shell self-intersection",
    "307": "wrong face orientation", "308": "inconsistent face orientation", "309": "unused vertices",
    "401": "shells intersect", "402": "duplicated shells", "403": "inner shell outside", "404": "interior disconnected",
    "405": "wrong solid orientation", "601": "building-level anomaly", "609": "building without a Solid",
}


def _tools_available() -> Optional[str]:
    """Whether the external tools / pyproj are all available. Returns a reason string if something is missing, None if complete."""
    try:
        import pyproj  # noqa: F401
    except Exception:
        return "pyproj not installed"
    for cmd, name in ((CITYGML_TOOLS, "citygml-tools"), (VAL3DITY_CMD.split()[0], "val3dity")):
        from shutil import which
        if which(cmd) is None and not Path(cmd).exists():
            return f"{name} not found ({cmd})"
    return None


# --- Extract changed buildings (pruning at cityObjectMember granularity) -----
def _subset_root(xml_bytes: bytes, ids: set) -> Optional[etree._Element]:
    """Return a CityModel keeping only the Buildings whose ids are in `ids` (None if none remain).

    Namespaces, Envelope, and srsName are kept as in the original (val3dity/citygml-tools need the coordinate system).
    """
    root = safe_fromstring(xml_bytes)
    kept = 0
    for member in list(root):
        if _local(member)[1] != "cityObjectMember":
            continue
        bid = None
        for el in member.iter():
            if _local(el)[1] in ("Building",) and _gml_id(el):
                bid = _gml_id(el)
                break
        if bid in ids:
            kept += 1
        else:
            root.remove(member)
    return root if kept else None


def _centroid_lonlat(root: etree._Element) -> Optional[tuple]:
    """Get a representative (lon, lat) from the Envelope or the first posList (for UTM zone selection)."""
    for tag in ("lowerCorner", "posList", "pos"):
        for el in root.iter():
            if _local(el)[1] == tag and el.text:
                t = el.text.split()
                if len(t) >= 3:
                    return (float(t[1]), float(t[0]))  # (lon, lat) <- PLATEAU is lat lon h
    return None


def _utm_epsg(lon: float, lat: float) -> str:
    from pyproj.aoi import AreaOfInterest
    from pyproj.database import query_utm_crs_info
    info = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01),
    )
    return f"EPSG:{info[0].code}"


def _reproject(root: etree._Element, dst_epsg: str) -> None:
    """Project posList etc. from EPSG:6697 (lat lon h) to dst_epsg (meters) (always_xy)."""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:6697", dst_epsg, always_xy=True)
    for el in root.iter():
        if not isinstance(el.tag, str) or _local(el)[1] not in _COORD_TAGS or not el.text:
            continue
        t = el.text.split()
        if not t or len(t) % 3:
            continue
        lats = [float(t[i]) for i in range(0, len(t), 3)]
        lons = [float(t[i + 1]) for i in range(0, len(t), 3)]
        hs = [float(t[i + 2]) for i in range(0, len(t), 3)]
        xs, ys, zs = tr.transform(lons, lats, hs)
        el.text = " ".join(f"{v:.4f}" for xyz in zip(xs, ys, zs) for v in xyz)
    for el in root.iter():
        v = el.get("srsName")
        if v and "6697" in v:
            el.set("srsName", dst_epsg.split(":")[-1].join(v.split("6697")))


# --- Running val3dity -------------------------------------------------------
def _validate_ids(xml_bytes: bytes, ids: set, work: Path) -> Optional[dict]:
    """Extract the buildings in `ids` -> project -> CityJSON -> val3dity, returning {building_id: {"valid":bool,"codes":[...]}}.

    None when there is nothing to extract or a tool fails (= undecidable, skip).
    """
    root = _subset_root(xml_bytes, ids)
    if root is None:
        return None
    c = _centroid_lonlat(root)
    if c is None:
        return None
    _reproject(root, _utm_epsg(c[0], c[1]))
    gml = work / "subset.gml"
    gml.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
    # CityGML → CityJSON
    r = subprocess.run([CITYGML_TOOLS, "to-cityjson", f"--output={work}/", str(gml)],
                       capture_output=True)
    cj = work / "subset.json"
    if r.returncode != 0 or not cj.is_file():
        return None
    # val3dity (report JSON)
    rep = work / "report.json"
    cmd = VAL3DITY_CMD.split() + [str(cj), "--planarity_d2p_tol", str(VAL3DITY_PLANARITY_D2P_M),
                                  "--report", str(rep)]
    subprocess.run(cmd, capture_output=True)
    if not rep.is_file():
        return None
    data = json.loads(rep.read_text())
    out: dict = {}
    for feat in data.get("features", []):
        codes = sorted({str(e.get("code")) for e in feat.get("errors", [])})
        out[feat.get("id")] = {"valid": feat.get("validity") is not False, "codes": codes}
    return out


# --- CI: base->head regression detection ------------------------------------
def gate_ci(repo: Path, base_sha: str, head_sha: str, gml_files: list) -> dict:
    """Validate the changed buildings of each changed .gml and collect invalids (regressions) introduced by the PR."""
    from scripts.extract_building_preview import _get_file_at_sha

    regressions: list = []
    checked = 0
    skipped = 0
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for rel in gml_files:
            head_bytes = _get_file_at_sha(repo, head_sha, rel)
            if head_bytes is None:
                continue
            base_bytes = _get_file_at_sha(repo, base_sha, rel)
            old_map = load_buildings(base_bytes) if base_bytes else {}
            new_map = load_buildings(head_bytes)
            changed = _changed_ids(old_map, new_map)
            if not changed:
                continue
            head_v = _validate_ids(head_bytes, changed, work)
            if head_v is None:
                skipped += 1
                continue
            base_v = _validate_ids(base_bytes, changed & set(old_map), work) if base_bytes else {}
            base_v = base_v or {}
            checked += len(head_v)
            regressions.extend(find_regressions(head_v, base_v, rel))
    return {"regressions": regressions, "checked": checked, "skipped": skipped}


def find_regressions(head_v: dict, base_v: dict, rel: str) -> list:
    """Of the invalid buildings in head, return **only the regressions introduced by the PR** (pure function, testable).

    Regression = (absent in base = newly added and invalid) or (was valid in base but became invalid).
    Buildings already invalid in base (= pre-existing PLATEAU defects) are excluded by the diff approach.
    """
    regs: list = []
    for bid, hv in head_v.items():
        if hv["valid"]:
            continue
        bv = base_v.get(bid)
        if bv is None or bv.get("valid"):
            regs.append({"file": rel, "id": bid, "codes": hv["codes"],
                         "was": "new" if bv is None else "valid in base"})
    return regs


def render(result: dict) -> str:
    regs = result["regressions"]
    lines = [VAL3DITY_MARKER, "## 🧱 Topological consistency gate (official val3dity engine, diff-based)", ""]
    if not regs:
        lines += ["✅ The buildings changed by this PR introduce no new topological inconsistencies in the official val3dity check.", ""]
        return "\n".join(lines).rstrip() + "\n"
    lines.append(f"**⚠️ This PR introduces new topological inconsistencies ({len(regs)} building(s)).**"
                 " These are not pre-existing data defects but invalids caused/added by the change.")
    lines.append("")
    for r in regs:
        codes = ", ".join(f"{c} {_CODE_LABEL.get(c, '')}".strip() for c in r["codes"]) or "unknown"
        lines.append(f"- `{r['id']}` (`{r['file']}`, {r['was']}): {codes}")
    lines += ["", "<sub>Uses the same error-code system as official val3dity (100-405)."
              f" Planarity tolerance is {VAL3DITY_PLANARITY_D2P_M}m per §6.3 L12."
              " Pre-existing PLATEAU-origin defects are excluded by the diff-based approach.</sub>"]
    return "\n".join(lines).rstrip() + "\n"


def _local_files(paths: list, work: Path) -> dict:
    """Local: validate all buildings of each file (baseline health)."""
    total = invalid = 0
    per_file = []
    for p in paths:
        b = Path(p).read_bytes()
        ids = set(load_buildings(b))
        v = _validate_ids(b, ids, work)
        if v is None:
            per_file.append((p, None, None)); continue
        inv = [(k, x["codes"]) for k, x in v.items() if not x["valid"]]
        total += len(v); invalid += len(inv)
        per_file.append((p, len(v), inv))
    return {"total": total, "invalid": invalid, "per_file": per_file}


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="val3dity topological consistency gate (diff-based)")
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--repo", type=Path, default=REPO_ROOT)
    p.add_argument("--base-sha", default=None)
    p.add_argument("--head-sha", default=None)
    p.add_argument("--file-list", type=Path, default=None)
    p.add_argument("--enforce", action="store_true", help="Exit 1 if regressions found (default: advisory)")
    args = p.parse_args(argv)

    missing = _tools_available()
    if missing:
        sys.stderr.write(f"[val3dity_gate] Skipped ({missing}). Advisory in environments without tools.\n")
        return 0

    if args.file_list is not None:
        if not (args.base_sha and args.head_sha):
            p.error("--base-sha and --head-sha required when using --file-list.")
        gml_files = [ln.strip() for ln in args.file_list.read_text(encoding="utf-8").splitlines()
                     if ln.strip().endswith(".gml")]
        result = gate_ci(args.repo, args.base_sha, args.head_sha, gml_files)
        sys.stdout.write(render(result))
        return 1 if (args.enforce and result["regressions"]) else 0

    if args.files:
        with tempfile.TemporaryDirectory() as td:
            res = _local_files(args.files, Path(td))
        for path, n, inv in res["per_file"]:
            if n is None:
                print(f"{path}: Skipped (extraction/tool unavailable)"); continue
            print(f"{path}: {n} buildings · {len(inv)} invalid")
            for k, codes in inv[:20]:
                print(f"    - {k}: {' · '.join(codes)}")
        print(f"### Total: {res['total']} buildings · {res['invalid']} invalid")
        return 1 if (args.enforce and res["invalid"]) else 0

    p.error("Specify .gml or --file-list (CI).")


if __name__ == "__main__":
    raise SystemExit(main())
