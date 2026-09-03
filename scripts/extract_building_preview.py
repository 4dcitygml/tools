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
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

# Repository root (this script lives in <root>/scripts/).
REPO_ROOT = Path(__file__).resolve().parents[1]

# XML namespaces used by CityGML 2.0 documents.
NS_CORE = "http://www.opengis.net/citygml/2.0"
NS_BLDG = "http://www.opengis.net/citygml/building/2.0"
NS_GML = "http://www.opengis.net/gml"
NS_APP = "http://www.opengis.net/citygml/appearance/2.0"

# GitHub caps a PR comment body at 65,536 characters. We keep the generated
# URL comfortably below that so it can be embedded in a comment together with
# surrounding text. When the encoded payload would exceed this budget, texture
# data is dropped in stages (old side first, then both sides) until it fits.
MAX_URL_LEN = 60000


def _get_file_at_sha(repo: Path, sha: str, rel_path: str) -> bytes | None:
    """Return the raw bytes of ``rel_path`` at commit ``sha``, or None if absent.

    Uses ``git show`` so the working tree state is irrelevant; a non-zero exit
    (file missing at that revision, bad sha, ...) yields None.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{rel_path}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _split_floats(text: str) -> list[float]:
    """Parse a whitespace-separated coordinate string into a list of floats."""
    return [float(token) for token in text.split()]


def _extract_buildings(xml_bytes: bytes) -> dict[str, dict]:
    """Parse a CityGML document into a mapping of building id -> summary dict.

    Each summary carries the measured height, the base elevation, a 2D
    footprint, and optionally LOD1 top elevation and LOD2 wall/roof polygons.
    Buildings lacking an id or a usable footprint are silently skipped.
    """
    root = ElementTree.fromstring(xml_bytes)
    buildings: dict[str, dict] = {}

    for member in root.findall(f"{{{NS_CORE}}}cityObjectMember"):
        building = member.find(f"{{{NS_BLDG}}}Building")
        if building is None:
            continue

        gml_id = building.get(f"{{{NS_GML}}}id")
        if not gml_id:
            continue

        # Measured height, defaulting to zero when the element is missing.
        height = 0.0
        height_el = building.find(f"{{{NS_BLDG}}}measuredHeight")
        if height_el is not None and height_el.text:
            height = float(height_el.text)

        # Footprint: prefer the LOD0 roof edge ring, fall back to the LOD1
        # solid's rings.
        pos_text = None
        lod0 = building.find(f"{{{NS_BLDG}}}lod0RoofEdge")
        if lod0 is not None:
            ring_pos = lod0.find(f".//{{{NS_GML}}}LinearRing/{{{NS_GML}}}posList")
            if ring_pos is not None and ring_pos.text:
                pos_text = ring_pos.text

        lod1 = building.find(f"{{{NS_BLDG}}}lod1Solid")
        if pos_text is None and lod1 is not None:
            ring_pos = lod1.find(f".//{{{NS_GML}}}LinearRing/{{{NS_GML}}}posList")
            if ring_pos is not None and ring_pos.text:
                pos_text = ring_pos.text

        if not pos_text:
            continue

        values = _split_floats(pos_text)
        # Coordinates come as (lat, lon, alt) triplets; keep only [lat, lon].
        coords = [
            [values[i], values[i + 1]] for i in range(0, len(values) - 2, 3)
        ]
        if len(coords) < 3:
            continue

        # Base elevation: third component of the first vertex of the first
        # ring inside the LOD1 solid.
        base = 0.0
        if lod1 is not None:
            first_ring = lod1.find(
                f".//{{{NS_GML}}}LinearRing/{{{NS_GML}}}posList"
            )
            if first_ring is not None and first_ring.text:
                tokens = first_ring.text.split()
                if len(tokens) >= 3:
                    base = float(tokens[2])

        entry: dict = {
            "id": gml_id,
            "height": height,
            "base": base,
            "coords": coords,
        }

        # LOD1 top elevation: third token of the very last posList found
        # anywhere under the LOD1 solid (typically the roof face).
        if lod1 is not None:
            pos_lists = lod1.findall(f".//{{{NS_GML}}}posList")
            if pos_lists:
                last = pos_lists[-1]
                tokens = (last.text or "").split()
                if len(tokens) >= 3:
                    entry["lod1top"] = round(float(tokens[2]), 3)

        # LOD2 geometry: one entry per polygon found under each boundedBy
        # surface (walls, roofs, ground).
        faces: list[dict] = []
        for bounded in building.findall(f"{{{NS_BLDG}}}boundedBy"):
            for polygon in bounded.iter(f"{{{NS_GML}}}Polygon"):
                poly_id = polygon.get(f"{{{NS_GML}}}id") or ""
                for pos_list in polygon.iter(f"{{{NS_GML}}}posList"):
                    values = _split_floats(pos_list.text or "")
                    # CityGML in EPSG:6697 stores axes as (lat, lon, alt);
                    # the viewer wants GeoJSON-style [lon, lat, alt], so the
                    # first two components are swapped here.
                    pts = [
                        [
                            round(values[i + 1], 7),
                            round(values[i], 7),
                            round(values[i + 2], 3),
                        ]
                        for i in range(0, len(values) - 2, 3)
                    ]
                    if len(pts) < 3:
                        continue
                    # Drop the closing vertex if the ring repeats its start.
                    if pts[0] == pts[-1]:
                        pts = pts[:-1]
                    if len(pts) < 3:
                        continue
                    faces.append({"id": poly_id, "pts": pts})
        if faces:
            entry["lod2"] = faces

        buildings[gml_id] = entry

    return buildings


def _extract_texmap(xml_bytes: bytes) -> dict[str, dict]:
    """Collect texture assignments: polygon id -> {"img": uri, "uv": [...]}.

    Walks every ParameterizedTexture in the document. The image URI is kept
    verbatim (a relative path) rather than reduced to its basename, because
    identical file names may exist in different appearance folders and would
    collide otherwise. Later assignments for the same polygon overwrite
    earlier ones.
    """
    root = ElementTree.fromstring(xml_bytes)
    texmap: dict[str, dict] = {}

    for tex in root.iter(f"{{{NS_APP}}}ParameterizedTexture"):
        uri_el = tex.find(f"{{{NS_APP}}}imageURI")
        image_uri = (uri_el.text or "").strip() if uri_el is not None else ""
        if not image_uri:
            continue

        for target in tex.findall(f"{{{NS_APP}}}target"):
            poly_id = (target.get("uri") or "").lstrip("#")
            if not poly_id:
                continue
            coords_el = target.find(f".//{{{NS_APP}}}textureCoordinates")
            if coords_el is None or not coords_el.text:
                continue
            try:
                values = _split_floats(coords_el.text)
            except ValueError:
                continue
            uv = [
                [round(values[i], 4), round(values[i + 1], 4)]
                for i in range(0, len(values) - 1, 2)
            ]
            texmap[poly_id] = {"img": image_uri, "uv": uv}

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
        old_bytes = _get_file_at_sha(args.repo, args.base_sha, rel_path)
        new_bytes = _get_file_at_sha(args.repo, args.head_sha, rel_path)

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
    main()
