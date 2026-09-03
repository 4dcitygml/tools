# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""identity-baseline / identity-correction commits in the commit scope gate:
trailers, byte-preserving replacement, manifest reference, tier rule, and the
repository-wide collision check."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.commit_building_scope import inspect_range
from scripts.provenance_manifest import manifest_ref
from tests.test_bulk_manifest import EXAMPLE
from tests.test_commit_building_scope import GitRepo

MANIFEST_PATH = "provenance/identity-baseline/53394611-2020-2025.json"


def _gml_members(members: list[tuple[str, str, int]], gml_ids: tuple[str, ...] = ("g1", "g2")) -> str:
    """Like the base helper, but gml:id is fixed per slot: an identity commit changes only the buildingID value."""
    def building(gml_id: str, stable: str, municipality: str, value: int) -> str:
        return (
            f'<core:cityObjectMember><bldg:Building gml:id="{gml_id}">'
            '<uro:buildingIDAttribute><uro:BuildingIDAttribute>'
            f'<uro:buildingID>{stable}</uro:buildingID>'
            f'<uro:city>{municipality}</uro:city>'
            '</uro:BuildingIDAttribute></uro:buildingIDAttribute>'
            f'<bldg:storeysAboveGround>{value}</bldg:storeysAboveGround>'
            '</bldg:Building></core:cityObjectMember>'
        )
    return (
        '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
        'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
        'xmlns:gml="http://www.opengis.net/gml" '
        'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">'
        + "".join(building(gml_ids[i], *member) for i, member in enumerate(members))
        + '</core:CityModel>'
    )


def _manifest(links: list[tuple[str, str, str]]) -> dict:
    m = json.loads(json.dumps(EXAMPLE))
    m["repository"] = "example/13101-example"
    m["evidence"]["links"] = [
        {"from": f, "to": t, "tier": tier, "method": "mutual_best_iou", "iou": 0.97, "centroid_m": 0.2,
         "hausdorff_m": 0.9, "area_ratio": 1.0, "competitor_iou": 0.0, "chain": ["2020", "2025"]}
        for f, t, tier in links]
    m["evidence"]["unlinked"] = []
    return m


class IdentityCommitScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = GitRepo(Path(self.tmp.name))
        self.base = self.repo.commit_text(_gml_members([("13101-bldg-1", "13101", 3), ("13101-bldg-2", "13101", 4)]), "baseline")
        (self.repo.root / "other").mkdir()
        (self.repo.root / "other" / "far.gml").write_text(_gml_members([("13101-bldg-9", "13101", 1)], ("g9",)), encoding="utf-8")
        self.repo.run("add", "other/far.gml")
        self.repo.run("commit", "-q", "-m", "second tile")
        self.base = self.repo.run("rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_manifest(self, links) -> bytes:
        path = self.repo.root / MANIFEST_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(_manifest(links), ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        path.write_bytes(data)
        self.repo.run("add", MANIFEST_PATH)
        return data

    def _identity_commit(self, source: str, target: str, manifest_bytes: bytes, content: str | None = None,
                         kind: str = "identity-baseline", extra: str = "", ref: str | None = None) -> str:
        content = content or _gml_members([(target if source == "13101-bldg-1" else "13101-bldg-1", "13101", 3),
                                           (target if source == "13101-bldg-2" else "13101-bldg-2", "13101", 4)])
        (self.repo.root / "tile.gml").write_text(content, encoding="utf-8")
        self.repo.run("add", "tile.gml")
        ref = ref or manifest_ref(MANIFEST_PATH, manifest_bytes)
        message = (f"Unify buildingID {source} -> {target} (2025 edition)\n\nChange-Type: {kind}\n"
                   f"Building-ID-From: {source}\nBuilding-ID-To: {target}\n"
                   f"Identity-Evidence: tier=B;method=mutual_best_iou;iou=0.97;centroid_m=0.2;chain=2020>2025\n"
                   f"Provenance-Manifest: {ref}\n{extra}")
        self.repo.run("commit", "-q", "-m", message)
        return self.repo.run("rev-parse", "HEAD")

    def test_valid_identity_commit_passes(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        head = self._identity_commit("13101-bldg-1", "13101-bldg-21", data)
        results = inspect_range(self.repo.root, self.base, head)
        self.assertEqual([r.errors for r in results], [[]])
        self.assertEqual((results[0].identity_from, results[0].identity_to), ("13101-bldg-1", "13101-bldg-21"))

    def test_target_colliding_with_another_file_is_rejected(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-9", "B")])
        head = self._identity_commit("13101-bldg-1", "13101-bldg-9", data)
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("already exists in the repository (other/far.gml)" in e for e in errors), errors)

    def test_target_freed_by_earlier_commit_is_accepted(self) -> None:
        # 2 -> 22 first frees "2"; then 1 -> 2 may reuse it (dependency-ordered chain).
        data = self._write_manifest([("13101-bldg-2", "13101-bldg-22", "B"), ("13101-bldg-1", "13101-bldg-2", "B")])
        self._identity_commit("13101-bldg-2", "13101-bldg-22", data,
                              content=_gml_members([("13101-bldg-1", "13101", 3), ("13101-bldg-22", "13101", 4)]))
        head = self._identity_commit("13101-bldg-1", "13101-bldg-2", data,
                                     content=_gml_members([("13101-bldg-2", "13101", 3), ("13101-bldg-22", "13101", 4)]))
        results = inspect_range(self.repo.root, self.base, head)
        self.assertEqual([r.errors for r in results], [[], []])

    def test_pair_not_in_manifest_or_tier_c_is_rejected(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "C")])
        head = self._identity_commit("13101-bldg-1", "13101-bldg-21", data)
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("tier C" in e for e in errors), errors)
        with mock.patch.dict(os.environ, {"CITYGML_IDENTITY_REVIEW": "true"}):
            self.assertEqual(inspect_range(self.repo.root, self.base, head)[0].errors, [])

    def test_pair_missing_from_manifest_is_rejected(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-99", "B")])
        head = self._identity_commit("13101-bldg-1", "13101-bldg-21", data)
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("not listed in the manifest" in e for e in errors), errors)

    def test_identity_commit_without_gml_change_is_rejected(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        self.repo.run("commit", "-q", "-m", "Add manifest only\n\nChange-Type: identity-baseline\n"
                      "Building-ID-From: 13101-bldg-1\nBuilding-ID-To: 13101-bldg-21\n"
                      f"Provenance-Manifest: {manifest_ref(MANIFEST_PATH, data)}\n")
        head = self.repo.run("rev-parse", "HEAD")
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("no CityGML change" in e for e in errors), errors)

    def test_manifest_digest_is_enforced(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        head = self._identity_commit("13101-bldg-1", "13101-bldg-21", data, ref=f"{MANIFEST_PATH}@sha256:{'0' * 64}")
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("does not match the digest" in e for e in errors), errors)

    def test_byte_preservation_is_enforced(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        head = self._identity_commit("13101-bldg-1", "13101-bldg-21", data,
                                     content=_gml_members([("13101-bldg-21", "13101", 7), ("13101-bldg-2", "13101", 4)]))
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("byte-preserving" in e for e in errors), errors)

    def test_identity_correction_requires_corrects_trailer(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        manifest = json.loads(data); manifest["kind"] = "identity-correction"
        (self.repo.root / MANIFEST_PATH).write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        self.repo.run("add", MANIFEST_PATH)
        data = (self.repo.root / MANIFEST_PATH).read_bytes()
        head = self._identity_commit("13101-bldg-1", "13101-bldg-21", data, kind="identity-correction")
        errors = inspect_range(self.repo.root, self.base, head)[0].errors
        self.assertTrue(any("Corrects:" in e for e in errors), errors)
        head = self._identity_commit("13101-bldg-2", "13101-bldg-22", data, kind="identity-correction",
                                     extra=f"Corrects: {self.base}\n")
        results = inspect_range(self.repo.root, self.base, head)
        self.assertTrue(any("not listed in the manifest" in e for r in results for e in r.errors))


    def test_manifest_changed_after_the_commits_is_rejected(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        self._identity_commit("13101-bldg-1", "13101-bldg-21", data)
        # a later commit rewrites the manifest (same path) -> head digest no longer matches the trailers
        (self.repo.root / MANIFEST_PATH).write_text(json.dumps(_manifest([("13101-bldg-1", "13101-bldg-21", "A")])) + "\n", encoding="utf-8")
        self.repo.run("add", MANIFEST_PATH); self.repo.run("commit", "-q", "-m", "Touch manifest")
        head = self.repo.run("rev-parse", "HEAD")
        errors = [e for r in inspect_range(self.repo.root, self.base, head) for e in r.errors]
        self.assertTrue(any("changed after the commits" in e for e in errors), errors)

    def test_foreign_commit_is_flagged_on_itself(self) -> None:
        data = self._write_manifest([("13101-bldg-1", "13101-bldg-21", "B")])
        self._identity_commit("13101-bldg-1", "13101-bldg-21", data)
        self.repo.commit_text(_gml_members([("13101-bldg-21", "13101", 3), ("13101-bldg-2", "13101", 9)]), "Sneak a value change\n\nBuilding: 13101-bldg-2\n")
        head = self.repo.run("rev-parse", "HEAD")
        results = inspect_range(self.repo.root, self.base, head)
        self.assertEqual(results[0].errors, [])  # the identity commit itself is fine
        self.assertTrue(any("does not belong in an identity PR" in e for e in results[-1].errors), results[-1].errors)


if __name__ == "__main__":
    unittest.main()
