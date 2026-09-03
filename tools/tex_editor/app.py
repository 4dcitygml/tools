#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""PLATEAU CityGML LOD2 texture editor — local server (sister tool of the attribute editor).

Specializes in replacing and newly adding building exterior photos (LOD2
textures) (#119). The editing method is **atlas baking**:

- The browser perspective-corrects the user photo and bakes it into the UV
  region of a copy of the original atlas image
- The server **adds** the new image under a content-addressed name
  (tex_<sha256 first 12>.jpg) and replaces exactly one GML `app:imageURI` leaf
  value with a uniqueness-verified match (UV and XML structure unchanged)

This stays consistent with the repository conventions (R1: never overwrite
existing images / R3: imageURI must exist / one building at a time) and the W6
minimal diff. Shared machinery (GML parsing, leaf replacement, git/PR,
first-run setup) is reused by importing tools/attr_editor/app.py.

Usage:
    python app.py [--repo ~/sample-tokyo-station] [--port 8766] [--no-browser]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

APP_DIR = Path(__file__).resolve().parent

# Load the attribute editor as the shared base (by path, not import, to stay a standalone file)
_ATTR_PATH = APP_DIR.parent / "attr_editor" / "app.py"
if not _ATTR_PATH.is_file():
    sys.exit(f"Error: {_ATTR_PATH} not found (run from tools/tex_editor inside clone)")
_spec = importlib.util.spec_from_file_location("attr_editor_app", _ATTR_PATH)
attr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attr)


def tr(key: str, default: str, **params) -> str:
    """Translate server-side (Python) generated text (fail-open).

    Same scheme as attr.tr() but looks up the tex_editor catalog (attr's
    version is pinned to the attr_editor catalog and cannot be reused).
    """
    mod = attr.i18n_module()
    if mod is not None:
        try:
            return mod.translate("tex_editor", key, default, **params)
        except Exception:
            pass
    s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def tr_lang(lang: str, key: str, default: str, **params) -> str:
    """tr() with an explicit language, for repo-facing text (PR title/body).

    Repo-facing text follows the repository's working language (4dcitygml.json
    "lang"), not the UI language of the person editing."""
    mod = attr.i18n_module()
    if mod is not None:
        try:
            return mod.translate("tex_editor", key, default, lang=lang, **params)
        except Exception:
            pass
    s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s

_IMAGE_MAX_BYTES = 30 * 1024 * 1024  # cap on the baked atlas size (safety net)

# Wall clustering thresholds
_WALL_MAX_NZ = 0.3        # a "wall" if the normal's vertical component is below this (otherwise roof etc.)
_WALL_DOT_MIN = 0.96      # normal-azimuth agreement to treat faces as the same wall (facade)

# New-texture mode (#119: for LOD2 data without appearance)
_VIRTUAL_PREFIX = "__new__/"  # virtual atlas name = __new__/<gid> (the frontend substitutes a locally generated one)
_NEW_ATLAS_MAX = 2048         # max edge of a new atlas [px]
_NEW_PPM = 40.0               # target resolution of a new atlas [px/m] (auto-shrinks if it does not fit)
_NEW_GUTTER = 4               # gutter between wall regions [px] (prevents bleeding)


def _wall_clusters(faces_geo: list) -> "tuple[dict, list]":
    """Group LOD2 faces into normal-azimuth clusters (= walls / facades).

    Real building facades are sets of "parallel planes at different depths" due
    to setbacks and balconies (measured: 0.3–5 m plane offsets at the same
    azimuth). For texturing it is natural to paste photos by orthographic
    projection onto a parallel plane, so faces are grouped **by azimuth only,
    never by plane distance**. s (horizontal position along the wall) can be
    shared across differing planes as long as the azimuth matches.

    faces_geo: [{"pid": str, "pts": [[lon, lat, z], ...]}] (pts are rings with the closing point removed)
    Returns: (pid -> {"wall": wall_id|None, "ring2d": [[s, h], ...]}, walls)
      ring2d is in wall coordinates [m] (s = horizontal position along the wall,
      h = elevation). Vertex order equals pts (= also corresponds to
      textureCoordinates). Non-wall faces get wall=None and ring2d=None.
    """
    import math

    if not faces_geo:
        return {}, []
    lon0, lat0 = faces_geo[0]["pts"][0][0], faces_geo[0]["pts"][0][1]
    if abs(lon0) > 360 or abs(lat0) > 90:
        # Projected systems (UTM, state plane, etc.): already in meters, no conversion
        kx = ky = 1.0
    else:
        kx = 111320.0 * math.cos(math.radians(lat0))
        ky = 110540.0

    clusters: list = []  # {"n": (nx,ny,nz), "d": float, "faces": [(pid, P)]}
    result: dict = {}

    for f in faces_geo:
        P = [((p[0] - lon0) * kx, (p[1] - lat0) * ky, p[2]) for p in f["pts"]]
        # Normal via Newell's method
        nx = ny = nz = 0.0
        for i in range(len(P)):
            x1, y1, z1 = P[i]
            x2, y2, z2 = P[(i + 1) % len(P)]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if norm < 1e-9 or abs(nz / norm) >= _WALL_MAX_NZ:
            result[f["pid"]] = {"wall": None, "ring2d": None}
            continue
        n = (nx / norm, ny / norm, nz / norm)

        home = None
        for c in clusters:
            dot = n[0] * c["n"][0] + n[1] * c["n"][1] + n[2] * c["n"][2]
            if dot > _WALL_DOT_MIN:
                home = c
                break
        if home is None:
            home = {"n": n, "faces": []}
            clusters.append(home)
        home["faces"].append((f["pid"], P))

    walls: list = []
    for c in clusters:
        n = c["n"]
        tlen = math.hypot(n[0], n[1])
        t = (-n[1] / tlen, n[0] / tlen)  # horizontal direction along the wall
        wid = len(walls)
        s0 = h0 = float("inf")
        s1 = h1 = float("-inf")
        for pid, P in c["faces"]:
            ring2d = [[round(p[0] * t[0] + p[1] * t[1], 3), round(p[2], 3)] for p in P]
            result[pid] = {"wall": wid, "ring2d": ring2d}
            for s, h in ring2d:
                s0, s1 = min(s0, s), max(s1, s)
                h0, h1 = min(h0, h), max(h1, h)
        walls.append(
            {
                "id": wid,
                "faces": [pid for pid, _ in c["faces"]],
                "s0": round(s0, 3),
                "s1": round(s1, 3),
                "h0": round(h0, 3),
                "h1": round(h1, 3),
            }
        )
    return result, walls


def grant_polygon_ids(raw: bytes, s: int, e: int, gid: str,
                      needed: "set[str]") -> "tuple[bytes, list[str]]":
    """Materialize gml:id on polygons that lack one (newyork etc., #16).

    needed holds planned IDs of the form `<gid>_p<n>` (n = document order of
    Polygons under boundedBy within the building span; same counting as attr's
    _parse_building). IDs that already exist are left alone. Inserts gml:id
    into each target Polygon's opening tag, and gml:id="<pid>_r0" into its
    first LinearRing (exterior). Coordinates and element structure are
    unchanged (gml:id is a standard GML attribute, XSD-conformant).
    Returns: (updated raw, list of granted IDs).
    """
    span = raw[s:e]
    to_add = {p for p in needed
              if re.search(rb'gml:id="' + re.escape(p.encode()) + rb'"', span) is None
              and p.startswith(f"{gid}_p")}
    if not to_add:
        return raw, []
    idx = {int(p.rsplit("_p", 1)[1]): p for p in to_add}

    bounded_re = re.compile(rb"<(?:\w+:)?boundedBy\b.*?</(?:\w+:)?boundedBy>", re.DOTALL)
    poly_open_re = re.compile(rb"<(?:\w+:)?Polygon\b[^>]*>")
    ring_open_re = re.compile(rb"<(?:\w+:)?LinearRing\b")
    inserts: list = []  # (offset within span, bytes to insert)
    n = 0
    for bm in bounded_re.finditer(span):
        block = bm.group(0)
        for pm in poly_open_re.finditer(block):
            pid = idx.get(n)
            n += 1
            if pid is None:
                continue
            open_tag = pm.group(0)
            if b"gml:id=" in open_tag:
                continue  # real ID present (a mismatch with the planned ID is left alone)
            # Insert gml:id just before the ">" of <gml:Polygon ...>
            tag_end = bm.start() + pm.end() - 1
            inserts.append((tag_end, f' gml:id="{pid}"'.encode()))
            # Insert the ring ID into the first LinearRing (exterior) of the same Polygon.
            # Search bound is the next Polygon start (else block end) = prevents inserting into a neighbor
            nxt = poly_open_re.search(block, pm.end())
            rm = ring_open_re.search(block, pm.end(), nxt.start() if nxt else len(block))
            if rm is not None:
                inserts.append((bm.start() + rm.end(), f' gml:id="{pid}_r0"'.encode()))
    if not inserts:
        return raw, []
    for off, ins in sorted(inserts, reverse=True):
        span = span[:off] + ins + span[off:]
    return raw[:s] + span + raw[e:], sorted(idx[k] for k in idx if idx[k] in to_add)


class TexRepo(attr.Repo):
    """Adds texture editing to the attribute editor's Repo."""

    # ---- New texturing (#119): one-atlas-per-building UV layout ----
    def _new_layout(self, code: str, gid: str) -> dict:
        """Deterministically compute the one-atlas-per-building layout for an untextured building.

        Stacks a rectangular region per wall cluster (_wall_clusters)
        vertically, and normalizes ring2d (s×h [m]) into the region to get UVs.
        Consistency is guaranteed because faces_json (the browser's baking
        coordinates) and apply (the GML textureCoordinates) use the same result.
        Walls only (roofs etc. are not textured).
        """
        import math

        t = self.tile(code)
        b = t["buildings"].get(gid)
        if b is None:
            raise KeyError(gid)
        if any(f["id"] and f["id"] in t["texmap"] for f in b["lod2"]):
            raise ValueError(tr(
                "tex.err_already_textured",
                "This building already has textures (use replace mode)"))
        wall_info, walls = _wall_clusters(
            [{"pid": f["id"], "pts": f["pts"]} for f in b["lod2"] if f["id"]]
        )
        walls = [w for w in walls if w["faces"]]
        if not walls:
            raise ValueError(tr("tex.err_no_walls", "No walls (vertical faces) found"))

        # Stack by descending height (ties broken deterministically by width and id). Lower resolution until it fits
        order = sorted(
            walls,
            key=lambda w: (-(w["h1"] - w["h0"]), -(w["s1"] - w["s0"]), w["id"]),
        )
        ppm = _NEW_PPM
        width = height = 0
        for _ in range(6):
            width = max(int(math.ceil((w["s1"] - w["s0"]) * ppm)) for w in order)
            height = sum(
                int(math.ceil((w["h1"] - w["h0"]) * ppm)) for w in order
            ) + _NEW_GUTTER * (len(order) - 1)
            if width <= _NEW_ATLAS_MAX and height <= _NEW_ATLAS_MAX:
                break
            ppm *= min(_NEW_ATLAS_MAX / width, _NEW_ATLAS_MAX / height) * 0.999

        uv: dict = {}
        y = 0
        for w in order:
            hpx = int(math.ceil((w["h1"] - w["h0"]) * ppm))
            for pid in w["faces"]:
                ring = wall_info[pid]["ring2d"]
                uv[pid] = [
                    [
                        round((s - w["s0"]) * ppm / width, 6),
                        round(1.0 - (y + (w["h1"] - h) * ppm) / height, 6),
                    ]
                    for s, h in ring
                ]
            y += hpx + _NEW_GUTTER
        return {
            "img": _VIRTUAL_PREFIX + gid,
            "width": width,
            "height": height,
            "walls": walls,
            "wall_info": wall_info,
            "uv": uv,
        }

    # ---- Face listing ----
    def _atlas_index(self, t: dict) -> "tuple[dict, dict]":
        """Return pid -> building gml:id, and imageURI -> [pid,...] (faces sharing an atlas)."""
        pid2gid: dict = {}
        for gid, b in t["buildings"].items():
            for f in b["lod2"]:
                if f["id"]:
                    pid2gid[f["id"]] = gid
        img_pids: dict = {}
        for pid, info in t["texmap"].items():
            img_pids.setdefault(info["img"], []).append(pid)
        return pid2gid, img_pids

    def faces_json(self, code: str, gid: str) -> dict:
        t = self.tile(code)
        b = t["buildings"].get(gid)
        if b is None:
            raise KeyError(gid)
        pid2gid, img_pids = self._atlas_index(t)
        # Compute coplanar clusters (walls) (classified by geometry, textured or not)
        wall_info, walls = _wall_clusters(
            [{"pid": f["id"], "pts": f["pts"]} for f in b["lod2"] if f["id"]]
        )
        faces = []
        for f in b["lod2"]:
            pid = f["id"]
            info = t["texmap"].get(pid)
            if not info:
                continue  # v1 skips untextured faces (replacement only)
            img = info["img"]
            owners = {pid2gid.get(p) for p in img_pids.get(img, [])} - {None}
            w = wall_info.get(pid, {"wall": None, "ring2d": None})
            faces.append(
                {
                    "pid": pid,
                    "img": img,
                    "uv": info["uv"],
                    "shared": len(img_pids.get(img, [])),
                    # Real data has 0% cross-building sharing, but if shared, disable editing as a safety net
                    "editable": owners == {gid},
                    "wall": w["wall"],
                    "ring2d": w["ring2d"],
                    # 3D ring for camera-alignment mode [[lon, lat, z], ...] (vertex order matches uv)
                    "ring3d": f["pts"],
                }
            )
        # New-texture mode (#119): untextured LOD2 buildings get a virtual atlas layout in the response.
        # The frontend creates a blank canvas at the newAtlas size, then bakes with the same flow as replacement
        new_atlas = None
        if not faces and b["lod2"]:
            try:
                layout = self._new_layout(code, gid)
            except ValueError:
                layout = None  # no walls etc. → "nothing editable" as before
            if layout is not None:
                wall_info, walls = layout["wall_info"], layout["walls"]
                for f in b["lod2"]:
                    pid = f["id"]
                    if pid not in layout["uv"]:
                        continue  # roofs etc. excluded (walls only)
                    w = wall_info[pid]
                    faces.append(
                        {
                            "pid": pid,
                            "img": layout["img"],
                            "uv": layout["uv"][pid],
                            "shared": 1,
                            "editable": True,
                            "wall": w["wall"],
                            "ring2d": w["ring2d"],
                            "ring3d": f["pts"],
                        }
                    )
                new_atlas = {
                    "img": layout["img"],
                    "width": layout["width"],
                    "height": layout["height"],
                }

        # Rebuild the wall face lists with textured faces only (exclude geometry-only faces)
        tex_pids = {f["pid"] for f in faces}
        walls = [
            {**w, "faces": [p for p in w["faces"] if p in tex_pids]}
            for w in walls
        ]
        walls = [w for w in walls if len(w["faces"]) > 0]
        return {
            "tile": code,
            "gid": gid,
            "buildingID": b["buildingID"],
            "faces": faces,
            "walls": walls,
            "newAtlas": new_atlas,
        }

    # ---- Texture apply (add new image + replace imageURI leaf value) ----
    def apply_textures(self, code: str, gid: str, images: list) -> dict:
        """Apply images: [{"orig": current imageURI, "data": base64 of the baked JPEG}].

        When orig is a virtual atlas name (__new__/<gid>), branch to new texturing (#119).
        """
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        if not images:
            raise ValueError(tr("tex.err_no_changes", "There are no changes"))
        if any(str(i.get("orig", "")).startswith(_VIRTUAL_PREFIX) for i in images):
            if len(images) != 1:
                raise ValueError(tr(
                    "tex.err_new_single_image",
                    "New texturing uses one image (one atlas) per building"))
            return self._apply_new_texture(code, gid, images[0])
        t = self.tile(code)
        pid2gid, img_pids = self._atlas_index(t)

        raw = path.read_bytes()
        added: list = []
        replaced: list = []
        for item in images:
            orig = str(item["orig"])
            if orig not in img_pids:
                raise ValueError(tr(
                    "tex.err_unknown_imageuri",
                    "This imageURI does not exist in this mesh: {orig}", orig=orig))
            owners = {pid2gid.get(p) for p in img_pids[orig]} - {None}
            if owners != {gid}:
                raise ValueError(tr(
                    "tex.err_shared_atlas",
                    "{orig} cannot be replaced because it is shared by multiple"
                    " buildings (owners: {owners})",
                    orig=orig, owners=sorted(owners),
                ))
            data = base64.b64decode(str(item["data"]))
            if not data.startswith(b"\xff\xd8"):
                raise ValueError(tr("tex.err_not_jpeg", "The image is not a JPEG"))
            if len(data) > _IMAGE_MAX_BYTES:
                raise ValueError(tr("tex.err_image_too_large", "The image is too large (over 30 MB)"))

            digest = hashlib.sha256(data).hexdigest()[:12]
            newrel = str(PurePosixPath(orig).parent / f"tex_{digest}.jpg")
            newpath = (self.bldg_dir / newrel).resolve()
            if not newpath.is_relative_to(self.bldg_dir.resolve()):
                raise ValueError(tr("tex.err_bad_image_path", "Invalid image path"))
            if newpath.exists() and newpath.read_bytes() != data:
                raise RuntimeError(tr("tex.err_hash_collision", "Hash collision: {path}", path=newrel))  # practically never happens

            # Replace the imageURI leaf value (verified unique within the file. R1: existing images untouched)
            pat = re.compile(
                rb"(<(?:\w+:)?imageURI>)" + re.escape(orig.encode("utf-8")) + rb"(</(?:\w+:)?imageURI>)"
            )
            hits = list(pat.finditer(raw))
            if len(hits) != 1:
                raise ValueError(tr(
                    "tex.err_imageuri_match_count",
                    "imageURI matched {n} time(s) (expected 1): {orig}",
                    n=len(hits), orig=orig))
            m = hits[0]
            raw = raw[: m.start()] + m.group(1) + newrel.encode("utf-8") + m.group(2) + raw[m.end() :]

            newpath.write_bytes(data)
            added.append(newrel)
            replaced.append({"orig": orig, "new": newrel})

        path.write_bytes(raw)
        self._tile_cache.pop(code, None)
        return {
            "ok": True,
            "relpath": str(path.relative_to(self.root)),
            "added": added,
            "replaced": replaced,
        }

    def _apply_new_texture(self, code: str, gid: str, item: dict) -> dict:
        """New texturing (#119): add a one-per-building atlas and insert an appearance block.

        - Coordinates are unchanged. Only references to existing polygon gml:id /
          exterior ring gml:id. For data without IDs (newyork etc., #16),
          gml:id is granted to the target building's polygons first (standard
          GML attribute; deterministic document-order IDs)
        - Images are added new under content-addressed names (R1); committed
          together with the GML for R3 consistency
        - Elements use the fixed app: prefix (consistent with the check regexes
          of scripts/texture_check.py); xmlns:app is declared locally on the
          element (the root opening tag is untouched); BOM/CRLF preserved
        - From the 2nd building in the same tile onward, surfaceDataMember is
          appended inside the existing app:Appearance
        """
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        orig = str(item["orig"])
        if orig != _VIRTUAL_PREFIX + gid:
            raise ValueError(tr(
                "tex.err_virtual_name_mismatch",
                "The virtual atlas name does not match the target building: {orig}",
                orig=orig))
        layout = self._new_layout(code, gid)
        data = base64.b64decode(str(item["data"]))
        if not data.startswith(b"\xff\xd8"):
            raise ValueError(tr("tex.err_not_jpeg", "The image is not a JPEG"))
        if len(data) > _IMAGE_MAX_BYTES:
            raise ValueError(tr("tex.err_image_too_large", "The image is too large (over 30 MB)"))

        raw = path.read_bytes()
        spans = attr.building_spans(raw)
        if gid not in spans:
            raise KeyError(gid)
        s, e = spans[gid]

        # Materialize gml:id on polygons lacking one (newyork etc., #16).
        # Spans shift after granting, so refetch them
        raw, ids_added = grant_polygon_ids(raw, s, e, gid, set(layout["uv"]))
        if ids_added:
            spans = attr.building_spans(raw)
            s, e = spans[gid]
        span = raw[s:e]

        # textureCoordinates ring target = each polygon's exterior ring gml:id
        ring_ids: dict = {}
        for pid in layout["uv"]:
            pm = re.search(
                rb'<gml:Polygon gml:id="'
                + re.escape(pid.encode("utf-8"))
                + rb'".*?</gml:Polygon>',
                span,
                re.DOTALL,
            )
            rm = pm and re.search(rb'<gml:LinearRing gml:id="([^"]+)"', pm.group(0))
            if not rm:
                raise ValueError(tr(
                    "tex.err_ring_no_id",
                    "Cannot add textures because the exterior ring has no gml:id: {pid}",
                    pid=pid))
            ring_ids[pid] = rm.group(1).decode("utf-8")

        # Image path: <tile stem without _op>_appearance/tex_<sha256 first 12>.jpg
        stem = path.stem[:-3] if path.stem.endswith("_op") else path.stem
        digest = hashlib.sha256(data).hexdigest()[:12]
        newrel = f"{stem}_appearance/tex_{digest}.jpg"
        newpath = (self.bldg_dir / newrel).resolve()
        if not newpath.is_relative_to(self.bldg_dir.resolve()):
            raise ValueError(tr("tex.err_bad_image_path", "Invalid image path"))
        if newpath.exists() and newpath.read_bytes() != data:
            raise RuntimeError(tr("tex.err_hash_collision", "Hash collision: {path}", path=newrel))

        # surfaceDataMember block (targets in building face order; first UV repeated at the end to close the ring)
        eol = "\r\n" if b"\r\n" in raw[:4096] else "\n"
        t = self.tile(code)
        lines = [
            f"<app:surfaceDataMember><app:ParameterizedTexture>"
            f"<app:imageURI>{newrel}</app:imageURI>",
            "\t\t\t\t\t<app:mimeType>image/jpg</app:mimeType>",
        ]
        for f in t["buildings"][gid]["lod2"]:
            pid = f["id"]
            uv = layout["uv"].get(pid)
            if not uv:
                continue
            coords = " ".join(f"{u:.6f} {v:.6f}" for u, v in uv + [uv[0]])
            lines += [
                f'\t\t\t\t\t<app:target uri="#{pid}">',
                "\t\t\t\t\t\t<app:TexCoordList>",
                f'\t\t\t\t\t\t\t<app:textureCoordinates ring="#{ring_ids[pid]}">'
                f"{coords}</app:textureCoordinates>",
                "\t\t\t\t\t\t</app:TexCoordList>",
                "\t\t\t\t\t</app:target>",
            ]
        lines.append("\t\t\t\t</app:ParameterizedTexture></app:surfaceDataMember>")
        sdm = eol.join(lines)

        if b"<app:appearanceMember" in raw:
            # Append to the existing Appearance (this tool creates one Appearance per tile)
            anchor = b"</app:Appearance>"
            if raw.count(anchor) != 1:
                raise ValueError(tr(
                    "tex.err_appearance_not_unique",
                    "Cannot append automatically because app:Appearance is not unique"))
            i = raw.index(anchor)
            insert = ("\t" + sdm + eol + "\t\t").encode("utf-8")
        else:
            # New: just before the first cityObjectMember (directly under CityModel, same position as real PLATEAU data)
            m = re.search(rb"<(?:\w+:)?cityObjectMember\b", raw)
            if not m:
                raise ValueError(tr(
                    "tex.err_no_cityobjectmember", "cityObjectMember not found"))
            i = m.start()
            block = eol.join(
                [
                    '<app:appearanceMember xmlns:app='
                    '"http://www.opengis.net/citygml/appearance/2.0">'
                    "<app:Appearance><app:theme>rgbTexture</app:theme>",
                    "\t\t\t" + sdm,  # lines 2+ of sdm carry their own indentation
                    "\t\t</app:Appearance></app:appearanceMember>",
                ]
            )
            insert = (block + eol + "\t").encode("utf-8")
        raw = raw[:i] + insert + raw[i:]

        newpath.parent.mkdir(parents=True, exist_ok=True)
        newpath.write_bytes(data)
        path.write_bytes(raw)
        self._tile_cache.pop(code, None)
        return {
            "ok": True,
            "relpath": str(path.relative_to(self.root)),
            "added": [newrel],
            "replaced": [{"orig": orig, "new": newrel}],
            "new": True,
            "idsAdded": ids_added,
        }

    # ---- PR (commit GML + new images) ----
    def create_tex_pr(self, body: dict) -> dict:
        code = body["tile"]
        gid = body["gid"]
        images = body.get("images") or []
        face_count = int(body.get("faceCount") or 0) or len(images)
        reason = (body.get("reason") or "").strip()
        if not body.get("consentCC0"):
            raise ValueError(tr(
                "tex.err_cc0_required",
                "Agreement to the data contribution policy (CC0) is required"
                " (docs/data-contribution-policy.md)"))

        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        rel = str(path.relative_to(self.root))

        with self._git_lock:
            # Cut the edit branch from the freshly fetched upstream main (same as the attribute editor)
            pr_base = self._fresh_pr_base(rel)
            # No tracked files under udx/ changed other than the target (same criterion as the attribute editor)
            status = self._git("status", "--porcelain").stdout.splitlines()
            others = [
                ln
                for ln in status
                if ln.strip()
                and not ln.startswith("??")
                and "/udx/" in ln[3:]
                and ln[3:].strip() != rel
            ]
            if others:
                raise RuntimeError(tr(
                    "tex.err_udx_dirty",
                    "There are changes to files other than the target under udx/."
                    " Please clean them up first:\n{list}",
                    list="\n".join(others[:10]),
                ))

            result_apply = self.apply_textures(code, gid, images)
            added = result_apply["added"]
            is_new = bool(result_apply.get("new"))  # new texturing (#119) or replacement

            # Resolve buildingID (the stable ID)
            raw = path.read_bytes()
            spans = attr.building_spans(raw)
            building_id = gid
            if gid in spans:
                s, e = spans[gid]
                building_id = attr.stable_building_id_from_span(
                    raw[s:e],
                    gid,
                    getattr(self, "_bid_type", "uro:buildingID"),
                    getattr(self, "_bid_invalid_values", ()),
                )

            now = datetime.now()
            safe_bid = re.sub(r"[^A-Za-z0-9._-]", "-", building_id)
            branch = f"tex/{safe_bid}-{now:%Y%m%d-%H%M%S}"

            verb = "Add" if is_new else "Update"
            subject = f"{verb} textures ({face_count} faces): {building_id}"
            lines = [subject, ""]
            lines += [
                f"- {'(no existing texture)' if is_new else r['orig']} → {r['new']}"
                for r in result_apply["replaced"]
            ]
            lines.append("")
            if reason:
                lines += [reason, ""]
            lines.append(f"Building: {building_id}")
            lines.append(attr.created_by_trailer(self.root, "citygml-tex-editor"))
            message = "\n".join(lines)

            prev = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

            def _rollback() -> None:
                self._git("checkout", prev, check=False)
                self._git("checkout", "--", rel, check=False)
                for a in added:  # new images are untracked, so delete them manually
                    p = self.bldg_dir / a
                    p.unlink(missing_ok=True)
                    if p.parent != self.bldg_dir and not any(p.parent.iterdir()):
                        p.parent.rmdir()  # also clean up the appearance folder created by new texturing
                self._tile_cache.pop(code, None)

            try:
                self._checkout_pr_branch(branch, pr_base)
                add_paths = [rel] + [
                    str((self.bldg_dir / a).relative_to(self.root)) for a in added
                ]
                self._git("add", *add_paths)
                # Path-limited commit: unrelated staged changes in the index are not swept in
                self._git("commit", "-m", message, "--", *add_paths)
                commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            except RuntimeError:
                _rollback()
                raise

            result: dict = {
                "ok": True,
                "branch": branch,
                "commit": commit,
                "buildingID": building_id,
                "added": added,
            }

            push = self._git("push", "-u", "origin", branch, check=False)
            if push.returncode != 0:
                _rollback()
                self._git("branch", "-D", branch, check=False)
                raise RuntimeError(tr(
                    "tex.err_push_failed",
                    "Could not send to GitHub. Your edits remain on this screen."
                    " Check your internet connection and try again.\n{stderr}",
                    stderr=push.stderr.strip(),
                ))
            result["pushed"] = True

            # PR title and body are repo-facing: they resolve in the repository's
            # working language (4dcitygml.json "lang"). The commit subject above
            # stays English (history contract). The ja/de title prefixes
            # (pr.title_tex_* catalog values) match hub/CI title fallbacks —
            # contract-tested; classification is branch-first (tex/) anyway.
            rlang = attr.read_repo_lang(self.root)
            no_texture = tr_lang(rlang, "pr.tex_no_existing", "(no existing texture)")
            rows = "\n".join(
                (f"| {no_texture} | `" + r["new"] + "` |")
                if is_new
                else f"| `{r['orig']}` | `{r['new']}` |"
                for r in result_apply["replaced"]
            )
            ids_note = (
                " " + tr_lang(rlang, "pr.tex_ids_note",
                              "Missing polygon IDs (gml:id) were added to the target"
                              " building first (standard GML attributes; coordinates"
                              " unchanged).")
                if result_apply.get("idsAdded") else ""
            )
            mechanism = (
                tr_lang(rlang, "pr.tex_mechanism_new",
                        "**New textures** for a building without any (appearance block"
                        " added with new images, #119). Coordinates are unchanged; the"
                        " UVs are generated from wall clusters (consistent with R1/R3).")
                + ids_note
                if is_new
                else tr_lang(rlang, "pr.tex_mechanism_update",
                             "Existing images are never overwritten: new images are"
                             " added and the imageURI values are swapped (consistent with"
                             " R1/R3). UVs and the XML structure are unchanged.")
            )
            heading = (tr_lang(rlang, "pr.heading_tex_new", "New textures")
                       if is_new else
                       tr_lang(rlang, "pr.heading_tex_update", "Texture update"))
            if is_new:
                pr_title = tr_lang(rlang, "pr.title_tex_add",
                                   "Add textures ({n} faces): {bid}",
                                   n=face_count, bid=building_id)
            else:
                pr_title = tr_lang(rlang, "pr.title_tex_update",
                                   "Update textures ({n} faces): {bid}",
                                   n=face_count, bid=building_id)
            # "(please fill in)" is a fixed literal in every language: it is one of
            # CI's placeholder strings, so an empty reason keeps failing the
            # reason check regardless of the repo language. Do not translate.
            pr_body = (
                f"## {heading} ({building_id} / `{gid}` / {face_count} faces)\n\n"
                + tr_lang(rlang, "pr.tex_columns",
                          "| Before image | After image (newly added) |")
                + f"\n|---|---|\n{rows}\n\n"
                f"{mechanism}\n\n"
                f"## {tr_lang(rlang, 'pr.heading_reason', 'Reason and supporting evidence')}"
                f" <!--sec:reason-->\n\n"
                f"{reason or '(please fill in)'}\n\n"
                f"## {tr_lang(rlang, 'pr.heading_rights', 'Rights confirmation')}\n\n"
                + tr_lang(rlang, "pr.rights_body",
                          "The submitter agrees that the photos used are their own,"
                          " that they are provided as **CC0 1.0** (moral rights will not"
                          " be exercised), and that they have confirmed the notes on"
                          " portrait rights and personal information"
                          " ([Data Contribution Policy]({url}) v2).",
                          url="../blob/main/docs/data-contribution-policy.md")
                + "\n"
            )
            import shutil as _shutil

            pr_url, api_note = self._create_pr_api(branch, pr_title, pr_body)
            if pr_url:
                result["prUrl"] = pr_url
            elif _shutil.which("gh"):
                gh = subprocess.run(
                    ["gh", "pr", "create", "--head", branch, "--title", pr_title, "--body", pr_body],
                    capture_output=True,
                    text=True,
                    cwd=str(self.root),
                )
                if gh.returncode == 0:
                    result["prUrl"] = gh.stdout.strip().splitlines()[-1]
                else:
                    result["compareUrl"] = self._compare_url(branch)
                    result["note"] = (
                        (api_note + "\n" if api_note else "")
                        + tr("tex.note_confirm_github",
                             "Complete the submission on the GitHub confirmation screen.")
                        + "\n"
                        + gh.stderr.strip()
                    )
            else:
                result["compareUrl"] = self._compare_url(branch)
                if api_note:
                    result["note"] = api_note

            self._git("checkout", prev, check=False)
            self._tile_cache.pop(code, None)
            return result


class TexHandler(attr.Handler):
    APP_ID = "tex_editor"
    repo: "TexRepo | None" = None

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/tonestandard":
                # The city's standard tone (tone_standard.json). Returns null if absent
                std_path = APP_DIR / "tone_standard.json"
                if std_path.is_file():
                    import json as _json

                    self._json({"ok": True, "standard": _json.loads(std_path.read_text(encoding="utf-8"))})
                else:
                    self._json({"ok": True, "standard": None})
                return
            if self.repo is not None:
                if path in ("/", "/index.html"):
                    self._file(APP_DIR / "index.html")
                    return
                if path.startswith("/api/faces/"):
                    parts = path.split("/")
                    if len(parts) != 5:
                        self._error("bad path")
                        return
                    self._json({"ok": True, **self.repo.faces_json(parts[3], unquote(parts[4]))})
                    return
        except KeyError as e:
            self._error(tr("tex.err_building_not_found",
                           "Building not found: {exc}", exc=e), 404)
            return
        except FileNotFoundError as e:
            self._error(tr("tex.err_tile_not_found",
                           "Tile not found: {exc}", exc=e), 404)
            return
        except BrokenPipeError:
            return
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {e}", 500)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path in ("/api/texture", "/api/pr"):
            try:
                import json as _json

                length = int(self.headers.get("Content-Length") or 0)
                body = _json.loads(self.rfile.read(length) or b"{}")
                if self.repo is None:
                    self._error(tr("tex.err_setup_incomplete", "Setup is not complete"), 409)
                elif path == "/api/texture":
                    self._json(self.repo.apply_textures(body["tile"], body["gid"], body["images"]))
                else:
                    self._json(self.repo.create_tex_pr(body))
            except (ValueError, RuntimeError) as e:
                self._error(str(e))
            except FileNotFoundError as e:
                self._error(tr("tex.err_tile_not_found",
                               "Tile not found: {exc}", exc=e), 404)
            except KeyError as e:
                self._error(tr("tex.err_missing_field",
                               "A required field is missing: {exc}", exc=e), 400)
            except BrokenPipeError:
                pass
            except Exception as e:  # noqa: BLE001
                self._error(f"{type(e).__name__}: {e}", 500)
            return
        super().do_POST()


def create_server(repo_root, port: int, *, data: "str | None" = None,
                  textures=None) -> ThreadingHTTPServer:
    """Entry point for external callers (e.g. the integrated frontend) to assemble this server (for frozen builds)."""
    attr.sync_upstream_main(repo_root)
    TexHandler.repo = TexRepo(Path(repo_root), data)
    if textures:
        TexHandler.repo.tex_override = Path(textures).resolve()
    return ThreadingHTTPServer(("127.0.0.1", int(port)), TexHandler)


def _make_console_safe() -> None:
    """Never let console output crash the app on a narrow code page.

    On Windows a redirected stdout/stderr uses the legacy code page (cp1252,
    cp932, ...), and the embeddable Python ignores PYTHONUTF8/PYTHONIOENCODING
    (._pth isolated mode). Help text and log lines contain characters such as
    "→", so unencodable characters are escaped instead of raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def main() -> None:
    _make_console_safe()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="local clone of sample-tokyo-station (can be omitted when run from inside clone)")
    parser.add_argument("--data", help="substring of data package name (to select if multiple exist; e.g., 13101)")
    parser.add_argument("--textures", type=Path,
                        help="texture replacement directory (for 3D tone variant comparison)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo or attr.detect_repo()
    if repo_root is None:
        cfg = attr.load_config()
        saved = cfg.get("repo")
        if saved and attr.has_building_data(Path(saved)):
            repo_root = Path(saved)
    if repo_root is None:
        sys.exit(
            "Error: clone not found. Specify with --repo or run first-time setup "
            "in the attribute editor (tools/attr_editor/app.py) first"
        )

    attr.sync_upstream_main(repo_root)
    try:
        TexHandler.repo = TexRepo(repo_root, args.data)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")
    if args.textures:
        TexHandler.repo.tex_override = args.textures.resolve()
        print(f"  Texture replacement: {TexHandler.repo.tex_override}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), TexHandler)
    url = f"http://localhost:{args.port}/"
    print(f"CityGML Texture Editor: {url}")
    print(f"  Data: {TexHandler.repo.bldg_dir}")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting")


if __name__ == "__main__":
    main()
