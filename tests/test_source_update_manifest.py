# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""source-update manifest: value replacement within one attribute family,
exclusion of structural (added/removed) leaves, byte-preserving application,
and the commit scope gate's PR-level rules for manifest-backed commits."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import source_update_manifest as S
from scripts.commit_building_scope import inspect_range
from scripts.provenance_manifest import manifest_ref, validate
from tests.test_commit_building_scope import GitRepo


def gml(buildings: list[tuple[str, dict]]) -> bytes:
    """buildings: (buildingID, {tag: value}) with fixed gml:id per slot; tags are direct bldg children."""
    members = []
    for index, (stable, leaves) in enumerate(buildings):
        body = "".join(f"<bldg:{tag}>{value}</bldg:{tag}>" for tag, value in leaves.items())
        members.append(
            f'<core:cityObjectMember><bldg:Building gml:id="g{index}">'
            f'<uro:buildingIDAttribute><uro:BuildingIDAttribute><uro:buildingID>{stable}</uro:buildingID>'
            f'<uro:city>13101</uro:city></uro:BuildingIDAttribute></uro:buildingIDAttribute>'
            f'{body}</bldg:Building></core:cityObjectMember>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml" '
            'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">' + "".join(members) + "</core:CityModel>\n").encode()


def _args(tmp: Path, current: bytes, new: bytes, family: str = "storeys") -> argparse.Namespace:
    (tmp / "current.gml").write_bytes(current)
    (tmp / "new.gml").write_bytes(new)
    return argparse.Namespace(repository="example/13101-example", mesh="53394611", municipality="13101", family=family,
                              current=str(tmp / "current.gml"), current_label="2024", current_uri=None,
                              edition_new=("2025", str(tmp / "new.gml")), new_uri=None, product="udx/bldg/tile.gml",
                              tools_repo="4dcitygml/tools", tools_commit="0" * 40, plan_issue="https://example.com/issues/3",
                              seed=1, sample_size=30)


class SourceUpdateManifestTest(unittest.TestCase):
    def test_value_changes_of_the_family_are_applied_byte_preservingly(self) -> None:
        current = gml([("13101-bldg-1", {"storeysAboveGround": "9999", "usage": "401"}),
                       ("13101-bldg-2", {"storeysAboveGround": "3", "usage": "401"}),
                       ("13101-bldg-3", {"storeysAboveGround": "5"})])
        new = gml([("13101-bldg-1", {"storeysAboveGround": "6", "usage": "402"}),      # storeys + usage changed
                   ("13101-bldg-2", {"storeysAboveGround": "3", "usage": "401"}),      # unchanged
                   ("13101-bldg-3", {"storeysAboveGround": "5", "storeysBelowGround": "1"}),  # leaf added -> excluded
                   ("13101-bldg-4", {"storeysAboveGround": "2"})])                     # new building -> lifecycle, not here
        with tempfile.TemporaryDirectory() as tmp:
            manifest, product = S.build_manifest(_args(Path(tmp), current, new))
        ev = manifest["evidence"]
        self.assertEqual(validate(manifest), [])
        self.assertEqual(ev["targets"], ["13101-bldg-1"])
        self.assertEqual([(c["path"], c["old"], c["new"]) for c in ev["changes"]], [("/storeysAboveGround", "9999", "6")])
        self.assertEqual([e["id"] for e in ev["excluded"]], ["13101-bldg-3"])
        self.assertEqual(ev["counts"]["only_new"], 1)
        self.assertIn(b"<bldg:storeysAboveGround>6</bldg:storeysAboveGround>", product)
        self.assertIn(b"<bldg:usage>401</bldg:usage>", product)          # other family untouched
        self.assertNotIn(b"storeysBelowGround", product)                  # structural change not applied
        self.assertEqual(len(product), len(current) - 3)                  # "9999" -> "6" only

    def test_ambiguous_leaf_is_excluded(self) -> None:
        current = gml([("13101-bldg-1", {"storeysAboveGround": "2"})]).replace(
            b"<bldg:storeysAboveGround>2</bldg:storeysAboveGround>",
            b"<bldg:storeysAboveGround>2</bldg:storeysAboveGround><bldg:storeysAboveGround>2</bldg:storeysAboveGround>")
        new = gml([("13101-bldg-1", {"storeysAboveGround": "3"})])
        with tempfile.TemporaryDirectory() as tmp:
            manifest, product = S.build_manifest(_args(Path(tmp), current, new))
        self.assertEqual(manifest["evidence"]["targets"], [])
        self.assertTrue(any("added or removed" in e["reason"] or "not unique" in e["reason"] for e in manifest["evidence"]["excluded"]))
        self.assertEqual(product, current)


class SourceUpdateGateTest(unittest.TestCase):
    MANIFEST = "provenance/source-update/53394611-2024-2025-storeys.json"

    def _setup(self, tmp: Path):
        current = gml([("13101-bldg-1", {"storeysAboveGround": "9999"}), ("13101-bldg-2", {"storeysAboveGround": "3"})])
        new = gml([("13101-bldg-1", {"storeysAboveGround": "6"}), ("13101-bldg-2", {"storeysAboveGround": "4"})])
        repo = GitRepo(tmp)
        repo.commit_text(current.decode(), "baseline")
        base = repo.run("rev-parse", "HEAD")
        args = _args(tmp / "work", current, new) if (tmp / "work").mkdir() is None else None
        args.product = "tile.gml"
        manifest, _product = S.build_manifest(args)
        path = tmp / self.MANIFEST
        path.parent.mkdir(parents=True)
        data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
        path.write_bytes(data)
        repo.run("add", self.MANIFEST)
        return repo, base, manifest, data

    def _commit(self, repo: GitRepo, content: bytes, stable: str, data: bytes, ref: str | None = None) -> str:
        (repo.root / "tile.gml").write_bytes(content)
        repo.run("add", "tile.gml")
        ref = ref or manifest_ref(self.MANIFEST, data)
        repo.run("commit", "-q", "-m", f"Update storeys of {stable}\n\nBuilding: {stable}\nAttribute-Family: storeys\nProvenance-Manifest: {ref}\n")
        return repo.run("rev-parse", "HEAD")

    def test_manifest_backed_commits_pass_and_deviations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, manifest, data = self._setup(Path(tmp))
            step1 = gml([("13101-bldg-1", {"storeysAboveGround": "6"}), ("13101-bldg-2", {"storeysAboveGround": "3"})])
            step2 = gml([("13101-bldg-1", {"storeysAboveGround": "6"}), ("13101-bldg-2", {"storeysAboveGround": "4"})])
            self._commit(repo, step1, "13101-bldg-1", data)
            head = self._commit(repo, step2, "13101-bldg-2", data)
            results = inspect_range(repo.root, base, head)
            self.assertEqual([r.errors for r in results], [[], []])
            # a value not in the manifest (7 instead of 6) fails the byte rule
            repo.run("reset", "-q", "--hard", base)
            repo.run("checkout", "-q", head, "--", self.MANIFEST)
            wrong = gml([("13101-bldg-1", {"storeysAboveGround": "7"}), ("13101-bldg-2", {"storeysAboveGround": "3"})])
            head2 = self._commit(repo, wrong, "13101-bldg-1", data)
            errors = inspect_range(repo.root, base, head2)[0].errors
            self.assertTrue(any("manifest's changes applied" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
