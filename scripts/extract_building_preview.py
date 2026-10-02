#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Detect CityGML building changes between two git commits and emit a preview URL.

This command-line tool is meant to run inside CI. Given a base commit, a head
commit, and a list of files touched by a pull request, it compares the CityGML
building data at both revisions, pairs up modified / added / removed buildings,
and serializes the result into a URL fragment (gzip-compressed, Base64-encoded
JSON) that a Cesium-based viewer page can decode client-side.

Exactly one line is written to stdout: the preview URL, or an empty string when
nothing relevant changed.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from pathlib import Path

# Repository root (this script lives in <root>/scripts/).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import citygml_dialect, citygml_faces  # noqa: E402
from scripts.safe_xml import safe_fromstring  # noqa: E402
from scripts.repo_git import blob  # noqa: E402

# GitHub caps a PR comment body at 65,536 characters. We keep the generated
# URL comfortably below that so it can be embedded in a comment together with
# surrounding text. When the encoded payload would exceed this budget, texture
# data is dropped in stages (old side first, then both sides) until it fits.
MAX_URL_LEN = 60000


def _split_floats(text: str) -> list[float]:
    """Parse a whitespace-separated coordinate string into a list of floats."""
    return [float(token) for token in text.split()]


def _points(text: str, tf, zs: float) -> list[list[float]]:
    """[lat, lon, z] of a posList in the file's own CRS (a projected CRS is converted)."""
    values = _split_floats(text)
    out = []
    for i in range(0, len(values) - 2, 3):
        if tf is None:
            out.append([values[i], values[i + 1], values[i + 2]])
        else:
            lat, lon = tf(values[i], values[i + 1])
            out.append([round(lat, 7), round(lon, 7), round(values[i + 2] * zs, 3)])
    return out


def _extract_buildings(xml_bytes: bytes) -> dict[str, dict]:
    """Parse a CityGML document into a mapping of building id -> summary dict.

    Each summary carries the measured height, the base elevation, a 2D
    footprint, and optionally LOD1 top elevation and LOD2 wall/roof polygons.
    The file is read in its own CityGML version and CRS (citygml_dialect): a
    projected CRS is converted to latitude/longitude, heights in feet to metres.
    The footprint is the LOD0 roof edge, else the LOD1 solid, else the lowest
    LOD2 face (data with LOD2 only). Buildings lacking an id or any of these
    are skipped.
    """
    root = safe_fromstring(xml_bytes, huge_tree=True)
    ns = citygml_dialect.ns_for_root(root)
    tf = citygml_dialect.file_transformer(root, ns)
    zs = getattr(tf, "z_scale", 1.0) if tf is not None else 1.0
    gml, bldg = ns["gml"], ns["bldg"]
    buildings: dict[str, dict] = {}

    for building in root.iter(f"{{{bldg}}}Building"):
        gml_id = building.get(f"{{{gml}}}id")
        if not gml_id:
            continue

        # Footprint: the LOD0 roof edge ring, else the LOD1 solid's first ring.
        pos_text = None
        lod0 = building.find(f"{{{bldg}}}lod0RoofEdge")
        if lod0 is not None:
            ring_pos = lod0.find(f".//{{{gml}}}LinearRing/{{{gml}}}posList")
            if ring_pos is not None and ring_pos.text:
                pos_text = ring_pos.text
        lod1 = building.find(f"{{{bldg}}}lod1Solid")
        lod1_rings = lod1.findall(f".//{{{gml}}}posList") if lod1 is not None else []
        if pos_text is None and lod1_rings and lod1_rings[0].text:
            pos_text = lod1_rings[0].text

        # LOD2 geometry: the boundedBy polygons, BuildingParts included, holes as holes
        # (scripts/citygml_faces.py, the one reader the editors use too)
        faces = [citygml_faces.public_face(f) for f in citygml_faces.lod2_faces(building, ns, tf, zs, gml_id)]

        if pos_text:
            ring = _points(pos_text, tf, zs)
        elif faces:
            low = min(faces, key=lambda f: sum(p[2] for p in f["pts"]) / len(f["pts"]))
            ring = [[lat, lon, z] for lon, lat, z in low["pts"]]
        else:
            continue
        coords = [[lat, lon] for lat, lon, _z in ring]
        if len(coords) < 3:
            continue

        # Base elevation: the first vertex of the LOD1 solid, else the lowest LOD2 point.
        lod2_z = [p[2] for f in faces for p in f["pts"]]
        base = 0.0
        if lod1_rings and lod1_rings[0].text:
            first = _points(lod1_rings[0].text, tf, zs)
            base = first[0][2] if first else 0.0
        elif lod2_z:
            base = min(lod2_z)

        # Measured height (in the data's units); for data with LOD2 only, the LOD2 extent.
        height = 0.0
        height_el = building.find(f"{{{bldg}}}measuredHeight")
        if height_el is not None and height_el.text:
            height = float(height_el.text) * zs
        elif not pos_text and lod2_z:
            height = round(max(lod2_z) - min(lod2_z), 3)

        entry: dict = {
            "id": gml_id,
            "height": height,
            "base": base,
            "coords": coords,
        }

        # LOD1 top elevation: the very last posList under the LOD1 solid (typically the roof face).
        if lod1_rings:
            last = _points(lod1_rings[-1].text or "", tf, zs)
            if last:
                entry["lod1top"] = round(last[0][2], 3)

        if faces:
            entry["lod2"] = faces

        buildings[gml_id] = entry

    return buildings


def _extract_texmap(xml_bytes: bytes) -> dict[str, dict]:
    """Texture of every LOD2 face: face id -> {"img", "uv"[, "holes"]}, looked up by the face's
    own rings (scripts/citygml_faces.py). The image URI is kept verbatim (a relative path):
    identical file names may exist in different appearance folders."""
    root = safe_fromstring(xml_bytes, huge_tree=True)
    ns = citygml_dialect.ns_for_root(root)
    by_ring, by_poly = citygml_faces.texture_rings(root, ns)
    texmap: dict[str, dict] = {}
    for building in root.iter(f"{{{ns['bldg']}}}Building"):
        gml_id = building.get(f"{{{ns['gml']}}}id") or ""
        for face in citygml_faces.lod2_faces(building, ns, gid=gml_id):
            tex = citygml_faces.face_texture(face, by_ring, by_poly)
            if tex is not None:
                texmap[face["id"]] = tex
    return texmap


def _centroid(building: dict) -> tuple[float, float]:
    """Average (lat, lon) of a building footprint."""
    coords = building["coords"]
    lat = sum(pt[0] for pt in coords) / len(coords)
    lon = sum(pt[1] for pt in coords) / len(coords)
    return lat, lon


def _match_buildings(
    old_bldgs: dict[str, dict], new_bldgs: dict[str, dict]
) -> list[dict]:
    """Pair up old and new buildings into change records.

    Returns a list of {"old": ..., "new": ...} dicts covering three cases:
    same-id buildings whose geometry or height changed, added buildings paired
    with their geographically nearest removed candidate, and removed buildings
    that no added building claimed.
    """
    pairs: list[dict] = []

    for gml_id, new_b in new_bldgs.items():
        old_b = old_bldgs.get(gml_id)
        if old_b is None:
            continue
        if old_b["height"] != new_b["height"] or old_b["coords"] != new_b["coords"]:
            pairs.append({"old": old_b, "new": new_b})

    removed = [b for bid, b in old_bldgs.items() if bid not in new_bldgs]
    added = [b for bid, b in new_bldgs.items() if bid not in old_bldgs]

    if not removed and not added and not pairs:
        return []

    claimed: set[str] = set()
    for new_b in added:
        if not removed:
            pairs.append({"old": None, "new": new_b})
            continue
        new_lat, new_lon = _centroid(new_b)

        def sq_dist(candidate: dict) -> float:
            lat, lon = _centroid(candidate)
            return (lat - new_lat) ** 2 + (lon - new_lon) ** 2

        # Nearest removed building by squared centroid distance. A candidate
        # already claimed by a previous added building stays eligible: several
        # new buildings may legitimately replace a single demolished one.
        best = min(removed, key=sq_dist)
        pairs.append({"old": best, "new": new_b})
        claimed.add(best["id"])

    for old_b in removed:
        if old_b["id"] not in claimed:
            pairs.append({"old": old_b, "new": None})

    return pairs


def _attach_tex(pair: dict, old_tex: dict, new_tex: dict) -> None:
    """Embed texture data into a change pair, in place.

    For each side of the pair that carries LOD2 faces, gather the texture
    entries (image URI + UV coordinates) keyed by face id and store them under
    a "tex" key. Because the UVs ride along inside the URL fragment itself,
    the viewer needs no companion file such as uvmap.json.
    """
    for side, texmap in (("old", old_tex), ("new", new_tex)):
        building = pair.get(side)
        if not building or "lod2" not in building:
            continue
        tex = {
            face["id"]: texmap[face["id"]]
            for face in building["lod2"]
            if face["id"] in texmap
        }
        if tex:
            building["tex"] = tex


def _encode(all_pairs: list[dict], base_url: str) -> str:
    """Serialize pairs into the final viewer URL.

    A single pair is emitted as a bare object, multiple pairs as an array.
    The JSON is gzip-compressed (level 9, mtime forced to 0 so identical data
    always yields byte-identical output and therefore a stable URL) and then
    URL-safe Base64-encoded into the fragment. Gzip typically shrinks the
    payload dramatically; the viewer sniffs the first two bytes for the gzip
    magic (0x1f 0x8b) and falls back to plain JSON when it is absent.
    """
    payload = all_pairs[0] if len(all_pairs) == 1 else all_pairs
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    b64 = base64.urlsafe_b64encode(compressed).decode("ascii")
    return f"{base_url}/#{b64}"


def _finalize_url(all_pairs: list[dict], base_url: str) -> str:
    """Encode the pairs, degrading texture detail until the URL fits.

    Stage 1 tries the full payload. Stage 2 strips textures from the old side
    only and tags every pair with texNote="new-only". Stage 3 strips both
    sides (texNote="none"). If even the texture-free payload exceeds
    MAX_URL_LEN (bulk PRs: source baselines, annual source updates with
    hundreds of buildings) the function returns "" — no preview — because a
    fragment of that size neither opens in a browser nor fits the GitHub
    comment limit once it is embedded in the summary and metadata comments.
    The viewer inspects texNote to tell the user which textures were
    sacrificed for size.
    """
    url = _encode(all_pairs, base_url)
    if len(url) <= MAX_URL_LEN:
        return url

    for pair in all_pairs:
        if pair.get("old"):
            pair["old"].pop("tex", None)
        pair["texNote"] = "new-only"
    url = _encode(all_pairs, base_url)
    if len(url) <= MAX_URL_LEN:
        return url

    for pair in all_pairs:
        for side in ("old", "new"):
            if pair.get(side):
                pair[side].pop("tex", None)
        pair["texNote"] = "none"
    url = _encode(all_pairs, base_url)
    if len(url) <= MAX_URL_LEN:
        return url
    print(
        f"preview: payload too large even without textures ({len(url)} chars > "
        f"{MAX_URL_LEN}); no preview URL is generated for this PR",
        file=sys.stderr,
    )
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare CityGML buildings between two commits and print a "
            "Cesium preview URL (empty line when nothing changed)."
        )
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--file-list", type=Path, required=True)
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()

    # Only building GML files are relevant for the preview.
    listed = (ln.strip() for ln in args.file_list.read_text().splitlines())
    targets = [p for p in listed if p.endswith(".gml") and "/bldg/" in p]
    if not targets:
        print("")
        return

    all_pairs: list[dict] = []
    for rel_path in targets:
        old_bytes = blob(args.repo, args.base_sha, rel_path)
        new_bytes = blob(args.repo, args.head_sha, rel_path)

        old_bldgs = _extract_buildings(old_bytes) if old_bytes is not None else {}
        new_bldgs = _extract_buildings(new_bytes) if new_bytes is not None else {}

        pairs = _match_buildings(old_bldgs, new_bldgs)
        if not pairs:
            continue

        old_tex = _extract_texmap(old_bytes) if old_bytes is not None else {}
        new_tex = _extract_texmap(new_bytes) if new_bytes is not None else {}
        for pair in pairs:
            _attach_tex(pair, old_tex, new_tex)

        all_pairs.extend(pairs)

    if not all_pairs:
        print("")
        return
    print(_finalize_url(all_pairs, args.base_url))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # run directly: the repository root
    from scripts.gate_result import guarded
    raise SystemExit(guarded(main))
