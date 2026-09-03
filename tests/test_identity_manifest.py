# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""identity-baseline manifest: ID regimes per boundary, tiering with geometric
evidence, chain composition, collision deferral, ordering, and byte-preserving
application. Synthetic three-edition fixtures with uro:buildingID."""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts import identity_manifest as I
from scripts.provenance_manifest import validate


def square(lat: float, lon: float, size_m: float = 10.0) -> list[tuple[float, float]]:
    d_lat = size_m / 111_000
    d_lon = size_m / 90_000
    return [(lat, lon), (lat + d_lat, lon), (lat + d_lat, lon + d_lon), (lat, lon + d_lon), (lat, lon)]


def citygml(buildings: list[tuple[str, str, list[tuple[float, float]]]]) -> bytes:
    members = []
    for index, (building_id, city, points) in enumerate(buildings):
        coords = " ".join(f"{lat} {lon} 0" for lat, lon in points)
        members.append(
            f'<core:cityObjectMember><bldg:Building gml:id="g{index}-{building_id}">'
            f'<bldg:lod0RoofEdge><gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>'
            f'<gml:posList>{coords}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod0RoofEdge>'
            f'<uro:buildingIDAttribute><uro:BuildingIDAttribute><uro:buildingID>{building_id}</uro:buildingID>'
            f'<uro:city>{city}</uro:city></uro:BuildingIDAttribute></uro:buildingIDAttribute>'
            f'</bldg:Building></core:cityObjectMember>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml" '
            'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">' + "".join(members) + "</core:CityModel>\n").encode()


BASE = 35.7100
LON = 139.7800
P = [square(BASE + i * 0.0005, LON) for i in range(8)]  # eight well-separated footprints


class IdentityManifestTest(unittest.TestCase):
    def _generate(self, editions: list[tuple[str, bytes]], tmp: Path, **overrides):
        paths = []
        for label, data in editions:
            path = tmp / f"{label}.gml"
            path.write_bytes(data)
            paths.append((label, str(path)))
        args = argparse.Namespace(
            kind="identity-baseline", repository="example/13101-example", mesh="53394611", municipality="13101",
            edition=paths, edition_uri=None, product="udx/bldg/base.gml", product_source=None,
            tools_repo="4dcitygml/tools", tools_commit="0" * 40, plan_issue="https://example.com/issues/1",
            seed=1, sample_size=30, **overrides)
        return I.build_manifest(args, I.Thresholds())

    def test_continuous_regime_links_by_id_with_geometry_check(self) -> None:
        e0 = citygml([("13101-bldg-1", "13101", P[0]), ("13101-bldg-2", "13101", P[1])])
        e1 = citygml([("13101-bldg-1", "13101", P[0]), ("13101-bldg-2", "13101", P[1])])
        with tempfile.TemporaryDirectory() as tmp:
            manifest, product = self._generate([("2020", e0), ("2021", e1)], Path(tmp))
        ev = manifest["evidence"]
        self.assertEqual(ev["boundaries"][0]["id_regime"], "continuous")
        self.assertEqual(ev["links"], [])           # nothing to replace
        self.assertEqual(ev["unchanged"], 2)
        self.assertEqual(validate(manifest), [])

    def test_renumbered_regime_ignores_coincidental_shared_ids(self) -> None:
        # Old: A at P0 (id 1), B at P1 (id 2). New: same buildings renumbered, and
        # the number "1" now belongs to a *different* building at P5.
        e0 = citygml([("13101-bldg-1", "13101", P[0]), ("13101-bldg-2", "13101", P[1]), ("13101-bldg-3", "13101", P[2])])
        e1 = citygml([("13101-bldg-101", "13101", P[0]), ("13101-bldg-102", "13101", P[1]), ("13101-bldg-1", "13101", P[5]), ("13101-bldg-103", "13101", P[2])])
        with tempfile.TemporaryDirectory() as tmp:
            manifest, product = self._generate([("2022", e0), ("2023", e1)], Path(tmp))
        ev = manifest["evidence"]
        self.assertEqual(ev["boundaries"][0]["id_regime"], "renumbered")
        pairs = {(l["from"], l["to"]) for l in ev["links"]}
        self.assertEqual(pairs, {("13101-bldg-1", "13101-bldg-101"), ("13101-bldg-2", "13101-bldg-102"), ("13101-bldg-3", "13101-bldg-103")})
        self.assertTrue(all(l["tier"] == "A" for l in ev["links"]))
        self.assertIn(b"<uro:buildingID>13101-bldg-101</uro:buildingID>", product)
        self.assertNotIn(b"<uro:buildingID>13101-bldg-1</uro:buildingID>", product)

    def test_chain_cuts_at_weak_link_and_defers_collisions(self) -> None:
        # Building X: id 1 -> 11 -> 21 (A, A). Building Y: id 2 -> 12 (A) then moves
        # 3 m and shrinks in the last edition (tier C) -> chain cut, keeps id 2.
        # Building Z: past-only, keeps id 5; a current building reuses number 5 -> deferred.
        # 8 m square shifted 2.5 m north inside the 10 m footprint: IoU ~0.58, area ratio 0.64 -> tier C
        shifted = [(lat + 2.5 / 111_000, lon) for lat, lon in square(BASE + 1 * 0.0005, LON, 8.0)]
        e0 = citygml([("13101-bldg-1", "13101", P[0]), ("13101-bldg-2", "13101", P[1]), ("13101-bldg-5", "13101", P[2]), ("13101-bldg-7", "13101", P[3])])
        e1 = citygml([("13101-bldg-11", "13101", P[0]), ("13101-bldg-12", "13101", P[1]), ("13101-bldg-17", "13101", P[3])])
        e2 = citygml([("13101-bldg-21", "13101", P[0]), ("13101-bldg-22", "13101", shifted), ("13101-bldg-5", "13101", P[3])])
        with tempfile.TemporaryDirectory() as tmp:
            manifest, product = self._generate([("2020", e0), ("2023", e1), ("2025", e2)], Path(tmp))
        ev = manifest["evidence"]
        links = {l["from"]: l for l in ev["links"]}
        self.assertEqual(set(links), {"13101-bldg-1"})
        self.assertEqual(links["13101-bldg-1"]["to"], "13101-bldg-21")
        self.assertEqual(links["13101-bldg-1"]["chain"], ["2020", "2023", "2025"])
        unlinked = {u["id"]: u for u in ev["unlinked"]}
        self.assertEqual(unlinked["13101-bldg-2"]["tier"], "C")          # weak last step
        self.assertEqual(unlinked["13101-bldg-5"]["tier"], "D")          # past-only
        self.assertEqual(unlinked["13101-bldg-7"]["tier"], "C")          # 7 -> 17 -> 5 collides with retained 5
        self.assertIn("still held by a retained", unlinked["13101-bldg-7"]["reason"])
        self.assertIn(b"<uro:buildingID>13101-bldg-7</uro:buildingID>", product)
        self.assertEqual(validate(manifest), [])

    def test_order_links_and_simultaneous_apply(self) -> None:
        links = [{"from": "13101-bldg-1", "to": "13101-bldg-2"}, {"from": "13101-bldg-2", "to": "13101-bldg-3"},
                 {"from": "13101-bldg-8", "to": "13101-bldg-9"}, {"from": "13101-bldg-9", "to": "13101-bldg-8"}]
        ordered, cyclic = I.order_links(links)
        self.assertEqual([l["from"] for l in ordered], ["13101-bldg-2", "13101-bldg-1"])  # free the target first
        self.assertEqual({l["from"] for l in cyclic}, {"13101-bldg-8", "13101-bldg-9"})
        raw = citygml([("13101-bldg-1", "13101", P[0]), ("13101-bldg-2", "13101", P[1])])
        out = I.apply_links(raw, ordered)
        self.assertIn(b"<uro:buildingID>13101-bldg-2</uro:buildingID>", out)
        self.assertIn(b"<uro:buildingID>13101-bldg-3</uro:buildingID>", out)
        self.assertNotIn(b"<uro:buildingID>13101-bldg-1</uro:buildingID>", out)
        self.assertEqual(len(out), len(raw))  # byte-preserving apart from the values
        with self.assertRaises(SystemExit):
            I.apply_links(raw, [{"from": "13101-bldg-1", "to": "13101-bldg-2"}])  # would duplicate id 2

    def test_commit_message_carries_the_contract_trailers(self) -> None:
        link = {"from": "13101-bldg-1", "to": "13101-bldg-21", "tier": "B", "method": "mutual_best_iou", "iou": 0.97,
                "centroid_m": 0.3, "hausdorff_m": 1.0, "area_ratio": 1.01, "competitor_iou": 0.0, "chain": ["2020", "2025"]}
        message = I.commit_message(link, "provenance/identity-baseline/x.json", b"{}", "identity-baseline", "2025")
        for token in ("Change-Type: identity-baseline", "Building-ID-From: 13101-bldg-1", "Building-ID-To: 13101-bldg-21",
                      "Identity-Evidence: tier=B;method=mutual_best_iou;iou=0.97", "chain=2020>2025",
                      "Provenance-Manifest: provenance/identity-baseline/x.json@sha256:", "Created-By: identity_manifest.py/identity-baseline"):
            self.assertIn(token, message)


    def test_fetch_materials_verifies_local_file_digests(self) -> None:
        from scripts.fetch_materials import fetch_material
        from scripts.provenance_manifest import sha256_hex
        raw = citygml([("13101-bldg-1", "13101", P[0])])
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "2020.gml"
            src.write_bytes(raw)
            good = {"name": "2020", "uri": src.resolve().as_uri(), "sha256": sha256_hex(raw), "bytes": len(raw)}
            self.assertEqual(fetch_material(good, Path(tmp)).resolve(), src.resolve())
            with self.assertRaises(SystemExit):
                fetch_material({**good, "sha256": "0" * 64}, Path(tmp))


    def test_boundary_without_any_shared_id_is_renumbered(self) -> None:
        e0 = citygml([("13101-bldg-1", "13101", P[0]), ("13101-bldg-2", "13101", P[1])])
        e1 = citygml([("13101-bldg-201", "13101", P[0]), ("13101-bldg-202", "13101", P[1])])
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _product = self._generate([("2022", e0), ("2023", e1)], Path(tmp))
        ev = manifest["evidence"]
        self.assertEqual(ev["boundaries"][0]["id_regime"], "renumbered")
        self.assertEqual({l["tier"] for l in ev["links"]}, {"A"})  # identical footprints, not demoted to C


if __name__ == "__main__":
    unittest.main()
