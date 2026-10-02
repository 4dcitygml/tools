#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The LOD2 faces of a building and their textures, read one way for the editors, the hub's
review 3D view and the CI preview.

Before, the attribute editor and scripts/extract_building_preview.py each had a copy, and both
had the same two faults: every posList of a polygon became a face of its own (a hole was drawn
as a filled face, with the outer ring's texture coordinates), and the texture coordinates of a
polygon were the first <textureCoordinates> of its target, whichever ring they belonged to.
Here a face is its exterior ring with its holes, and coordinates are looked up by ring id
(CityGML: <app:textureCoordinates ring="#ringId">), as PLATEAU's own converter does.

A face is {"id", "pts", "holes"?}: points as [lon, lat, z] without the closing point; "holes"
only when the polygon has interior rings. Its texture is {"img", "uv", "holes"?}, the uv lists
in the same order as the rings. Shipped next to the hub as program/citygml_faces.py.
"""
from __future__ import annotations

NS_GML = "http://www.opengis.net/gml"


def _ring_points(pos_list, transform=None, z_scale: float = 1.0) -> "list[list[float]]":
    """[lon, lat, z] of a posList (axis order lat, lon, z as in EPSG:6697), closing point dropped.
    transform(x, y) -> (lat, lon) converts a projected CRS."""
    try:
        nums = [float(x) for x in (pos_list.text or "").split()]
    except ValueError:
        return []
    pts = []
    for i in range(0, len(nums) - 2, 3):
        if transform is not None:
            lat, lon = transform(nums[i], nums[i + 1])
            pts.append([round(lon, 7), round(lat, 7), round(nums[i + 2] * z_scale, 3)])
        else:
            pts.append([round(nums[i + 1], 7), round(nums[i], 7), round(nums[i + 2], 3)])
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def lod2_faces(building, ns: dict, transform=None, z_scale: float = 1.0, gid: str = "") -> "list[dict]":
    """The boundedBy polygons of a building, its BuildingParts included. A polygon without a
    gml:id gets the planned id <gid>_p<n> in order of appearance (the texture editor writes the
    same ids when it grants them). Each face keeps its ring ids for the texture lookup."""
    gml, bldg = ns.get("gml", NS_GML), ns["bldg"]
    faces = []
    n = 0
    for bounded in building.iter(f"{{{bldg}}}boundedBy"):
        for poly in bounded.iter(f"{{{gml}}}Polygon"):
            pid = poly.get(f"{{{gml}}}id") or f"{gid}_p{n}"
            n += 1
            ext = poly.find(f"{{{gml}}}exterior/{{{gml}}}LinearRing")
            if ext is None or ext.find(f"{{{gml}}}posList") is None:
                continue
            pts = _ring_points(ext.find(f"{{{gml}}}posList"), transform, z_scale)
            if len(pts) < 3:
                continue
            face = {"id": pid, "pts": pts, "ring": ext.get(f"{{{gml}}}id") or ""}
            holes, hole_rings = [], []
            for ring in poly.findall(f"{{{gml}}}interior/{{{gml}}}LinearRing"):
                hole = _ring_points(ring.find(f"{{{gml}}}posList"), transform, z_scale) \
                    if ring.find(f"{{{gml}}}posList") is not None else []
                if len(hole) >= 3:
                    holes.append(hole)
                    hole_rings.append(ring.get(f"{{{gml}}}id") or "")
            if holes:
                face["holes"], face["holeRings"] = holes, hole_rings
            faces.append(face)
    return faces


def texture_rings(root, ns: dict) -> "tuple[dict, dict]":
    """Every ParameterizedTexture's coordinates: by ring id {ring: {"img", "uv"}}, and by target
    polygon {polygon: {"img", "uv"}} for data whose rings carry no id (the first listed ring)."""
    app = ns["app"]
    by_ring, by_poly = {}, {}
    for tex in root.iter(f"{{{app}}}ParameterizedTexture"):
        uri = tex.find(f"{{{app}}}imageURI")
        img = (uri.text or "").strip() if uri is not None else ""
        if not img:
            continue
        for target in tex.findall(f"{{{app}}}target"):
            pid = (target.get("uri") or "").lstrip("#")
            for coords in target.iter(f"{{{app}}}textureCoordinates"):
                try:
                    vals = [float(x) for x in (coords.text or "").split()]
                except ValueError:
                    continue
                uv = [[round(vals[i], 4), round(vals[i + 1], 4)] for i in range(0, len(vals) - 1, 2)]
                ring = (coords.get("ring") or "").lstrip("#")
                if ring:
                    by_ring[ring] = {"img": img, "uv": uv}
                if pid and pid not in by_poly:
                    by_poly[pid] = {"img": img, "uv": uv}
    return by_ring, by_poly


def face_texture(face: dict, by_ring: dict, by_poly: dict) -> "dict | None":
    """The texture of a face: its exterior ring's coordinates (and its holes'), or None."""
    tex = by_ring.get(face.get("ring") or "") or by_poly.get(face["id"])   # no ring attribute: the target's
    if tex is None:
        return None
    out = {"img": tex["img"], "uv": tex["uv"]}
    if face.get("holes"):
        hole_uv = [by_ring.get(r, {}).get("uv") for r in face.get("holeRings", [])]
        if all(hole_uv) and len(hole_uv) == len(face["holes"]):
            out["holes"] = hole_uv
    return out


def public_face(face: dict) -> dict:
    """A face as clients receive it: no ring ids."""
    return {k: v for k, v in face.items() if k not in ("ring", "holeRings")}
