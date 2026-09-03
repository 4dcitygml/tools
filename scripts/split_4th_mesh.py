#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Split CityGML(bldg) from 3rd-level mesh to 4th-level mesh (1/2 regional mesh).

Divides 3rd-level mesh GML files of 50MiB or larger into 4th-level meshes (1km cells split 2x2,
9-digit codes with last digit 1=SW/2=SE/3=NW/4=NE).

- Assign buildings (cityObjectMember) to the **quadrant with maximum footprint area** (PLATEAU official rule:
  features crossing boundaries are not split but stored whole in the quadrant with larger owned area).
  Buildings without footprint fall back to bbox centroid
- Redistribute ParameterizedTexture targets within global app:appearanceMember to quadrants
  based on referenced polygon IDs (preserve texture consistency)
- Image folder (<mesh>_bldg_6697_appearance/) is shared among 4 subs without renaming
- Envelope recalculated from each sub's building extent

Usage:
    python scripts/split_4th_mesh.py INPUT.gml --outdir DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_parser  # noqa: E402

CORE = "http://www.opengis.net/citygml/2.0"
GML = "http://www.opengis.net/gml"
APP = "http://www.opengis.net/citygml/appearance/2.0"

Q_CORE = f"{{{CORE}}}cityObjectMember"
Q_APPMEMBER = f"{{{APP}}}appearanceMember"
Q_APPEARANCE = f"{{{APP}}}Appearance"
Q_THEME = f"{{{APP}}}theme"
Q_SDM = f"{{{APP}}}surfaceDataMember"
Q_TARGET = f"{{{APP}}}target"
Q_GMLID = f"{{{GML}}}id"
Q_BOUNDEDBY = f"{{{GML}}}boundedBy"
Q_ENVELOPE = f"{{{GML}}}Envelope"
Q_LOWER = f"{{{GML}}}lowerCorner"
Q_UPPER = f"{{{GML}}}upperCorner"


def write_plateau_gml(root: etree._Element, out_path: Path) -> None:
    """Write in PLATEAU's official serialization format (UTF-8 BOM + CRLF + double-quoted declaration)."""
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
    body = body.replace(b"\n", b"\r\n")
    body = b"\xef\xbb\xbf" + body
    out_path.write_bytes(body)


def cell_bounds_from_mesh(code: str) -> tuple[float, float, float, float]:
    """Return the 1km cell's (lat_min, lon_min, dlat, dlon) from an 8-digit 3rd-level mesh code."""
    lat = int(code[0:2]) / 1.5
    lon = int(code[2:4]) + 100.0
    lat += int(code[4]) * (5.0 / 60.0)
    lon += int(code[5]) * (7.5 / 60.0)
    lat += int(code[6]) * (30.0 / 3600.0)
    lon += int(code[7]) * (45.0 / 3600.0)
    return lat, lon, 30.0 / 3600.0, 45.0 / 3600.0


def quadrant(lat: float, lon: float, lat_min: float, lon_min: float,
             dlat: float, dlon: float) -> int:
    """Return the quadrant of a representative point: 1=SW 2=SE 3=NW 4=NE (fallback when no footprint exists)."""
    mid_lat = lat_min + dlat / 2.0
    mid_lon = lon_min + dlon / 2.0
    north = lat >= mid_lat
    east = lon >= mid_lon
    if not north and not east:
        return 1
    if not north and east:
        return 2
    if north and not east:
        return 3
    return 4


def _footprint_rings(building: etree._Element) -> list[list[tuple[float, float]]]:
    """Return the building's footprint rings (lod0RoofEdge / lod0FootPrint) as (lat, lon) sequences."""
    rings: list[list[tuple[float, float]]] = []
    for el in building.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname in ("lod0RoofEdge", "lod0FootPrint"):
            for pe in el.iter(f"{{{GML}}}posList"):
                if not pe.text:
                    continue
                t = pe.text.split()
                pts = [(float(t[i]), float(t[i + 1])) for i in range(0, len(t) - 2, 3)]
                if len(pts) >= 3:
                    rings.append(pts)
    return rings


def _shoelace(pts: list[tuple[float, float]]) -> float:
    """Polygon area (absolute value). Coordinates are (lat, lon) in degrees."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _clip_half(pts, axis: int, val: float, keep_ge: bool):
    """Clip a polygon against a half-plane (axis>=val or axis<=val) (Sutherland-Hodgman)."""
    def inside(p):
        return p[axis] >= val if keep_ge else p[axis] <= val

    def inter(a, b):
        t = (val - a[axis]) / (b[axis] - a[axis])
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    out = []
    n = len(pts)
    for i in range(n):
        a = pts[i]
        b = pts[(i + 1) % n]
        ia, ib = inside(a), inside(b)
        if ia:
            out.append(a)
            if not ib:
                out.append(inter(a, b))
        elif ib:
            out.append(inter(a, b))
    return out


def _clip_box(ring, la_lo, la_hi, lo_lo, lo_hi):
    """Clip a polygon against the rectangle [la_lo,la_hi]x[lo_lo,lo_hi]."""
    p = _clip_half(ring, 0, la_lo, True)
    if len(p) < 3:
        return p
    p = _clip_half(p, 0, la_hi, False)
    if len(p) < 3:
        return p
    p = _clip_half(p, 1, lo_lo, True)
    if len(p) < 3:
        return p
    return _clip_half(p, 1, lo_hi, False)


def assign_quadrant(building: etree._Element, lat_min: float, lon_min: float,
                    dlat: float, dlon: float) -> int | None:
    """Return the quadrant with the largest footprint area (PLATEAU official rule = store in the larger-area side).

    Divide the 3rd-level cell into 4 quadrants, clip the footprint against each
    quadrant rectangle, and compare areas. Parts extending outside the cell
    (belonging to adjacent 3rd-level meshes) are excluded by the rectangle clip.
    Returns None if there is no footprint or the area is 0 (caller falls back to
    the bbox center).

    Note: the quadrant boundaries are lines of constant latitude/longitude, and
    within a 1km cell the latitude scale factor (cos) is nearly constant, so
    comparing areas in lat/lon degrees preserves the quadrant ordering (argmax).
    """
    rings = _footprint_rings(building)
    if not rings:
        return None
    mid_lat = lat_min + dlat / 2.0
    mid_lon = lon_min + dlon / 2.0
    lat_hi = lat_min + dlat
    lon_hi = lon_min + dlon
    boxes = {
        1: (lat_min, mid_lat, lon_min, mid_lon),  # southwest
        2: (lat_min, mid_lat, mid_lon, lon_hi),   # southeast
        3: (mid_lat, lat_hi, lon_min, mid_lon),   # northwest
        4: (mid_lat, lat_hi, mid_lon, lon_hi),    # northeast
    }
    areas = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    for ring in rings:
        for q, (la_lo, la_hi, lo_lo, lo_hi) in boxes.items():
            clipped = _clip_box(ring, la_lo, la_hi, lo_lo, lo_hi)
            if len(clipped) >= 3:
                areas[q] += _shoelace(clipped)
    if max(areas.values()) <= 0.0:
        return None
    return max(areas, key=areas.get)


def iter_coords(building: etree._Element):
    """Yield (lat, lon, z) from every posList / pos inside the building."""
    for tag in (f"{{{GML}}}posList", f"{{{GML}}}pos"):
        for el in building.iter(tag):
            if not el.text:
                continue
            nums = el.text.split()
            for i in range(0, len(nums) - 2, 3):
                try:
                    yield float(nums[i]), float(nums[i + 1]), float(nums[i + 2])
                except ValueError:
                    continue


def building_stats(building: etree._Element):
    """Return the building's (center_lat, center_lon, bbox=(latmin,lonmin,zmin,latmax,lonmax,zmax))."""
    latmin = lonmin = zmin = float("inf")
    latmax = lonmax = zmax = float("-inf")
    for la, lo, z in iter_coords(building):
        latmin = min(latmin, la); latmax = max(latmax, la)
        lonmin = min(lonmin, lo); lonmax = max(lonmax, lo)
        zmin = min(zmin, z); zmax = max(zmax, z)
    if latmin == float("inf"):
        return None
    return (
        (latmin + latmax) / 2.0,
        (lonmin + lonmax) / 2.0,
        (latmin, lonmin, zmin, latmax, lonmax, zmax),
    )


def split(input_path: Path, outdir: Path) -> dict:
    code = input_path.name.split("_")[0]
    if len(code) != 8 or not code.isdigit():
        raise SystemExit(f"Cannot extract 3rd-level mesh 8-digit code from filename: {input_path.name}")
    suffix = input_path.name[len(code):]  # e.g. _bldg_6697_op.gml
    lat_min, lon_min, dlat, dlon = cell_bounds_from_mesh(code)

    parser = safe_parser(huge_tree=True, remove_blank_text=False)
    tree = etree.parse(str(input_path), parser)
    root = tree.getroot()

    # Save the srs attributes of the original Envelope
    src_env = root.find(f"{Q_BOUNDEDBY}/{Q_ENVELOPE}")
    srs_name = src_env.get("srsName") if src_env is not None else "http://www.opengis.net/def/crs/EPSG/0/6697"
    srs_dim = src_env.get("srsDimension") if src_env is not None else "3"

    # 1) Assign buildings to quadrants and build gml:id -> quadrant mapping
    members = root.findall(Q_CORE)
    cell_members: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
    cell_bbox: dict[int, list] = {}
    id2cell: dict[str, int] = {}
    no_coord = 0

    fallback = 0
    for m in members:
        bldg = next((c for c in m if isinstance(c.tag, str)), None)
        stats = building_stats(m) if bldg is not None else None
        if stats is None:
            no_coord += 1
            q = 1  # fallback bucket when no coordinates can be extracted
        else:
            clat, clon, bbox = stats
            # PLATEAU official rule: assign to the quadrant with the largest footprint area; fall back to bbox center if none
            q = assign_quadrant(m, lat_min, lon_min, dlat, dlon)
            if q is None:
                q = quadrant(clat, clon, lat_min, lon_min, dlat, dlon)
                fallback += 1
            bb = cell_bbox.setdefault(q, [float("inf")] * 3 + [float("-inf")] * 3)
            bb[0] = min(bb[0], bbox[0]); bb[1] = min(bb[1], bbox[1]); bb[2] = min(bb[2], bbox[2])
            bb[3] = max(bb[3], bbox[3]); bb[4] = max(bb[4], bbox[4]); bb[5] = max(bb[5], bbox[5])
        cell_members[q].append(m)
        for el in m.iter():
            gid = el.get(Q_GMLID)
            if gid is not None:
                id2cell[gid] = q

    # 2) Redistribute appearance to quadrants
    # There can be multiple appearanceMember elements (seen in FY2023 v3 data). Redistribute while preserving the original member structure
    cell_apps: dict[int, list] = {1: [], 2: [], 3: [], 4: []}  # [(theme_el, [sdm, ...]), ...]
    tgt_total = tgt_routed = tgt_dropped = 0
    for app_member in root.findall(Q_APPMEMBER):
        appearance = app_member.find(Q_APPEARANCE)
        if appearance is None:
            continue
        theme_el = appearance.find(Q_THEME)
        cell_sdm: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
        for sdm in appearance.findall(Q_SDM):
            surfdata = next((c for c in sdm if isinstance(c.tag, str)), None)
            if surfdata is None:
                continue
            # Keep the non-target children of surfdata (ParameterizedTexture etc.) as a template
            header_children = [c for c in surfdata if c.tag != Q_TARGET]
            targets = surfdata.findall(Q_TARGET)
            # Sort targets by quadrant
            per_cell_targets: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
            for t in targets:
                tgt_total += 1
                uri = t.get("uri") or (t.text or "")
                ref = uri.lstrip("#").strip()
                c = id2cell.get(ref)
                if c is None:
                    tgt_dropped += 1
                    continue
                per_cell_targets[c].append(t)
                tgt_routed += 1
            for c in (1, 2, 3, 4):
                if not per_cell_targets[c]:
                    continue
                new_sdm = etree.SubElement(etree.Element("tmp"), Q_SDM)  # detached for now
                new_surf = etree.SubElement(new_sdm, surfdata.tag)
                for k, v in surfdata.attrib.items():
                    new_surf.set(k, v)
                for hc in header_children:
                    from copy import deepcopy
                    new_surf.append(deepcopy(hc))
                for t in per_cell_targets[c]:
                    new_surf.append(t)  # move
                cell_sdm[c].append(new_sdm)
        for c in (1, 2, 3, 4):
            if cell_sdm[c]:
                cell_apps[c].append((theme_el, cell_sdm[c]))

    # 3) Write one output file per quadrant
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for q in (1, 2, 3, 4):
        if not cell_members[q]:
            continue
        new_root = etree.Element(root.tag, nsmap=root.nsmap)
        for k, v in root.attrib.items():
            new_root.set(k, v)
        # boundedBy
        bb = cell_bbox.get(q)
        if bb:
            bounded = etree.SubElement(new_root, Q_BOUNDEDBY)
            env = etree.SubElement(bounded, Q_ENVELOPE)
            env.set("srsName", srs_name); env.set("srsDimension", srs_dim)
            etree.SubElement(env, Q_LOWER).text = f"{bb[0]} {bb[1]} {bb[2]}"
            etree.SubElement(env, Q_UPPER).text = f"{bb[3]} {bb[4]} {bb[5]}"
        # appearance (recreated per original appearanceMember)
        for theme_el, sdms in cell_apps[q]:
            am = etree.SubElement(new_root, Q_APPMEMBER)
            ap = etree.SubElement(am, Q_APPEARANCE)
            if theme_el is not None:
                from copy import deepcopy
                ap.append(deepcopy(theme_el))
            for sdm in sdms:
                ap.append(sdm)
        # cityObjectMembers (move)
        for m in cell_members[q]:
            new_root.append(m)

        out_name = f"{code}{q}{suffix}"
        out_path = outdir / out_name
        write_plateau_gml(new_root, out_path)
        written.append((out_path, len(cell_members[q])))

    return {
        "code": code,
        "members_total": len(members),
        "no_coord": no_coord,
        "fallback_bboxcenter": fallback,
        "targets_total": tgt_total,
        "targets_routed": tgt_routed,
        "targets_dropped": tgt_dropped,
        "written": written,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("--outdir", type=Path, default=None, help="Output directory (default: same as input)")
    args = ap.parse_args()

    outdir = args.outdir or args.input.parent
    rep = split(args.input, outdir)

    print(f"Input: {args.input.name}  (mesh {rep['code']})")
    print(f"  Total buildings: {rep['members_total']}  No coords: {rep['no_coord']}  "
          f"Footprint missing→bbox center: {rep['fallback_bboxcenter']}")
    print(f"  Target: total {rep['targets_total']}  routed {rep['targets_routed']}  dropped {rep['targets_dropped']}")
    print("  Output:")
    total_members = 0
    for path, n in rep["written"]:
        size_mb = path.stat().st_size / 1048576
        total_members += n
        flag = "  ⚠️>100MiB" if path.stat().st_size > 104857600 else ""
        print(f"    {path.name}  {n} buildings  {size_mb:.1f} MB{flag}")
    match_status = "Match" if total_members == rep['members_total'] else "Mismatch!"
    print(f"  Total buildings (output) = {total_members}  / input {rep['members_total']}  → {match_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
