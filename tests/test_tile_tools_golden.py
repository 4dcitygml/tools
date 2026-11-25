#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The tile tools' output, recorded (W1: four tools wrote tiles four ways; one writer replaces
them, so what they write is pinned first and every difference is named).

A PLATEAU mesh tile (UTF-8 BOM, CRLF, two appearance members) through split_4th_mesh and back
through merge_to_3rd_mesh; a CityGML 1.0 UTM tile (Munich style) through split_utm_tile and
extract_area. For each written file: BOM, line ends, the XML declaration, the root's prefixes,
the Envelope's attributes and corners, gml:name, the member IDs, and the appearance targets per
texture. Regenerate after an intended change: CITYGML_GOLDEN_WRITE=1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tile_tools_golden.json"
NS = {"core": "http://www.opengis.net/citygml/2.0", "bldg": "http://www.opengis.net/citygml/building/2.0",
      "gml": "http://www.opengis.net/gml", "app": "http://www.opengis.net/citygml/appearance/2.0"}
NS10 = {k: v.replace("/2.0", "/1.0") for k, v in NS.items()}

# mesh 53394611: lat 35.675 .. 35.683333, lon 139.7625 .. 139.775; quadrant centres
CENTRES = {"sw": (35.677083, 139.765625), "se": (35.677083, 139.771875),
           "nw": (35.68125, 139.765625), "ne": (35.68125, 139.771875)}


def _ring(lat, lon, d=0.0004, z=0.0):
    pts = [(lat - d, lon - d), (lat - d, lon + d), (lat + d, lon + d), (lat + d, lon - d), (lat - d, lon - d)]
    return " ".join(f"{a:.6f} {b:.6f} {z}" for a, b in pts)


def mesh_tile() -> bytes:
    buildings = "".join(
        f'\n\t<core:cityObjectMember>\n\t\t<bldg:Building gml:id="b-{q}">\n'
        f'\t\t\t<bldg:lod0RoofEdge><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="poly-{q}">'
        f'<gml:exterior><gml:LinearRing><gml:posList>{_ring(*c)}</gml:posList></gml:LinearRing></gml:exterior>'
        f'</gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod0RoofEdge>\n'
        f'\t\t\t<bldg:lod2Solid><gml:Solid><gml:exterior><gml:CompositeSurface><gml:surfaceMember>'
        f'<gml:Polygon gml:id="wall-{q}"><gml:exterior><gml:LinearRing><gml:posList>{_ring(*c, z=10.0)}</gml:posList>'
        f'</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:CompositeSurface></gml:exterior>'
        f'</gml:Solid></bldg:lod2Solid>\n'
        f'\t\t</bldg:Building>\n\t</core:cityObjectMember>'
        for q, c in CENTRES.items())

    def appearance(image, quads):
        targets = "".join(f'<app:target uri="#wall-{q}">0 0 1 0 1 1 0 0</app:target>' for q in quads)
        return (f'\n\t<app:appearanceMember><app:Appearance><app:theme>rgbTexture</app:theme>'
                f'<app:surfaceDataMember><app:ParameterizedTexture><app:imageURI>{image}</app:imageURI>'
                f'{targets}</app:ParameterizedTexture></app:surfaceDataMember></app:Appearance></app:appearanceMember>')
    text = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml" '
            'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            '\t<gml:boundedBy><gml:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/6697" srsDimension="3">'
            '<gml:lowerCorner>35.675 139.7625 0</gml:lowerCorner><gml:upperCorner>35.683333 139.775 10</gml:upperCorner>'
            '</gml:Envelope></gml:boundedBy>'
            + appearance("53394611_bldg_6697_appearance/a.jpg", ("sw", "ne"))
            + appearance("53394611_bldg_6697_appearance/b.jpg", ("se", "nw", "sw"))
            + buildings + '\n</core:CityModel>\n')
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def utm_tile() -> bytes:
    """CityGML 1.0, ETRS89 / UTM 32 (a Bavarian 2 km tile 690_5334), one building per 1 km cell."""
    cells = {"a": (690300, 5334300), "b": (691300, 5334300), "c": (690300, 5335300), "d": (691300, 5335300)}
    members = "".join(
        f'\n<cityObjectMember><bldg:Building gml:id="DEBY_{k}"><bldg:lod2Solid><gml:Solid><gml:exterior>'
        f'<gml:CompositeSurface><gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList>'
        f'{e} {n} 500 {e + 10} {n} 500 {e + 10} {n + 10} 510 {e} {n} 500</gml:posList></gml:LinearRing>'
        f'</gml:exterior></gml:Polygon></gml:surfaceMember></gml:CompositeSurface></gml:exterior></gml:Solid>'
        f'</bldg:lod2Solid></bldg:Building></cityObjectMember>'
        for k, (e, n) in cells.items())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CityModel xmlns="http://www.opengis.net/citygml/1.0" xmlns:gml="http://www.opengis.net/gml" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/1.0">\n'
            '<gml:boundedBy><gml:Envelope srsName="urn:adv:crs:ETRS89_UTM32*DE_DHHN2016_NH" srsDimension="3">'
            '<gml:lowerCorner>690000 5334000 500</gml:lowerCorner><gml:upperCorner>692000 5336000 510</gml:upperCorner>'
            '</gml:Envelope></gml:boundedBy>' + members + '\n</CityModel>\n').encode("utf-8")


def facts(path: Path) -> dict:
    raw = path.read_bytes()
    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    first = body.split(b"\n", 1)[0].rstrip(b"\r").decode("utf-8")
    root = ET.fromstring(body)
    gml = "http://www.opengis.net/gml"
    env = root.find(f"{{{gml}}}boundedBy/{{{gml}}}Envelope")
    prefixes = sorted({p for p in __import__("re").findall(r'xmlns:?([A-Za-z0-9_]*)=', body[:2000].decode("utf-8", "replace"))})
    textures = []
    for pt in root.iter():
        if pt.tag.endswith("}ParameterizedTexture"):
            uri = next(c.text for c in pt if c.tag.endswith("}imageURI"))
            textures.append([uri, sorted(t.get("uri") for t in pt if t.tag.endswith("}target"))])
    return {
        "bom": raw.startswith(b"\xef\xbb\xbf"), "crlf": b"\r\n" in raw, "declaration": first if first.startswith("<?xml") else None,
        "root": root.tag, "prefixes": prefixes,
        "envelope": None if env is None else {
            "attrib": dict(sorted(env.attrib.items())),
            "lower": env.find(f"{{{gml}}}lowerCorner").text, "upper": env.find(f"{{{gml}}}upperCorner").text},
        "name": [el.text for el in root if el.tag == f"{{{gml}}}name"],
        "members": [b.get(f"{{{gml}}}id") for m in root if m.tag.endswith("}cityObjectMember") for b in m],
        "appearanceMembers": sum(1 for m in root if m.tag.endswith("}appearanceMember")),
        "textures": sorted(textures),
    }


def run(script: str, *args, cwd=None) -> None:
    """A tool by its path: scripts/<tool>.py, or the PLATEAU pack's (dialects/plateau/<tool>.py)."""
    path = REPO_ROOT / "scripts" / script
    path = path if path.is_file() else REPO_ROOT / "dialects" / "plateau" / script
    r = subprocess.run([sys.executable, str(path), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise AssertionError(f"{script} failed:\n{r.stdout}\n{r.stderr}")


def outputs() -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        tile = d / "mesh" / "53394611_bldg_6697_op.gml"
        tile.parent.mkdir()
        tile.write_bytes(mesh_tile())
        run("split_4th_mesh.py", tile, "--outdir", d / "split")
        for p in sorted((d / "split").glob("*.gml")):
            out[f"split_4th_mesh/{p.name}"] = facts(p)
        run("merge_to_3rd_mesh.py", "--code", "53394611", "--dir", d / "split", "--out", d / "merged" / tile.name)
        out[f"merge_to_3rd_mesh/{tile.name}"] = facts(d / "merged" / tile.name)
        utm = d / "utm" / "690_5334.gml"
        utm.parent.mkdir()
        utm.write_bytes(utm_tile())
        (d / "utm-split").mkdir()
        run("split_utm_tile.py", utm, "--tile-km", "2", "--out-km", "1", "--outdir", d / "utm-split")
        for p in sorted((d / "utm-split").glob("*.gml")):
            out[f"split_utm_tile/{p.name}"] = facts(p)
        run("extract_area.py", utm, "--bbox", "690000", "5334000", "691999", "5334999", "--output", d / "area.gml")
        out["extract_area/area.gml"] = facts(d / "area.gml")
    return out


class TestTileToolsGolden(unittest.TestCase):
    def test_the_tile_tools_write_as_recorded(self):
        seen = json.loads(json.dumps(outputs()))
        if os.environ.get("CITYGML_GOLDEN_WRITE"):
            FIXTURE.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(seen), sorted(recorded))
        for key in recorded:
            with self.subTest(file=key):
                self.assertEqual(seen[key], recorded[key])



class TestTileNames(unittest.TestCase):
    """D-4a5: the tools matched more than their own names."""

    def test_a_re_split_does_not_keep_the_old_size(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "690_5334_2.gml"   # a 2 km tile named with its size
            src.write_bytes(utm_tile())
            run("split_utm_tile.py", src, "--tile-km", "2", "--out-km", "1", "--outdir", Path(d) / "out")
            self.assertEqual(sorted(p.name for p in (Path(d) / "out").glob("*.gml")),
                             ["690_5334_1.gml", "690_5335_1.gml", "691_5334_1.gml", "691_5335_1.gml"])

    def test_the_merge_takes_the_four_quadrants_only(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tile = d / "53394611_bldg_6697_op.gml"
            tile.write_bytes(mesh_tile())
            run("split_4th_mesh.py", tile, "--outdir", d / "split")
            stray = d / "split" / "53394611x_bldg_6697_op.gml"   # not a quadrant: `?` took it
            stray.write_bytes((d / "split" / "533946111_bldg_6697_op.gml").read_bytes())
            run("merge_to_3rd_mesh.py", "--code", "53394611", "--dir", d / "split", "--out", d / "m.gml")
            self.assertEqual(sorted(facts(d / "m.gml")["members"]), ["b-ne", "b-nw", "b-se", "b-sw"])

if __name__ == "__main__":
    unittest.main()
