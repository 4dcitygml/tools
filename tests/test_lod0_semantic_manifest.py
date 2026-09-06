# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Namespace-aware byte preservation and adversarial bulk-PR checks."""
import argparse
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from scripts import lod0_semantic_manifest as L
from scripts.commit_building_scope import inspect_range
from tests.test_commit_building_scope import GitRepo


def fixture():
    members = []
    for i in (1, 2):
        members.append(f'''<c:cityObjectMember><b:Building g:id="b{i}">
<u:buildingIDAttribute><u:BuildingIDAttribute><u:buildingID>40220-bldg-{i}</u:buildingID><u:city>40220</u:city></u:BuildingIDAttribute></u:buildingIDAttribute>
<b:lod0FootPrint><g:MultiSurface><g:surfaceMember><g:Polygon><g:exterior><g:LinearRing><g:posList>33 130 0 33 131 0 34 131 0 33 130 0</g:posList></g:LinearRing></g:exterior></g:Polygon></g:surfaceMember></g:MultiSurface></b:lod0FootPrint>
<b:storeysAboveGround>4</b:storeysAboveGround></b:Building></c:cityObjectMember>''')
    return ('''<?xml version="1.0" encoding="UTF-8"?>
<c:CityModel xmlns:c="http://www.opengis.net/citygml/2.0" xmlns:b="http://www.opengis.net/citygml/building/2.0" xmlns:g="http://www.opengis.net/gml" xmlns:u="https://www.geospatial.jp/iur/uro/3.1">
<!-- <b:lod0FootPrint> must remain literal -->
''' + ''.join(members) + '</c:CityModel>\n').encode().replace(b'xmlns:c=', b'xmlns:core=').replace(b'<c:', b'<core:').replace(b'</c:', b'</core:').replace(b'xmlns:g=', b'xmlns:gml=').replace(b'<g:', b'<gml:').replace(b'</g:', b'</gml:').replace(b' g:id=', b' gml:id=')


class TransformTest(unittest.TestCase):
    def test_only_real_element_names_change(self):
        raw = fixture().replace(b'\n', b'\r\n')
        out, ev = L.transform(raw, '40220')
        expected = raw.replace(b'<b:lod0FootPrint><gml:', b'<b:lod0RoofEdge><gml:').replace(b'</b:lod0FootPrint>', b'</b:lod0RoofEdge>')
        self.assertEqual(out, expected)
        self.assertEqual(ev['targets'], ['40220-bldg-1', '40220-bldg-2'])
        self.assertIn(b'<!-- <b:lod0FootPrint> must remain literal -->', out)

    def test_namespace_alias_and_bom_are_preserved(self):
        raw = b'\xef\xbb\xbf' + fixture().replace(b'xmlns:b=', b'xmlns:building=').replace(b'<b:', b'<building:').replace(b'</b:', b'</building:')
        out, _ = L.transform(raw, '40220')
        self.assertTrue(out.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'<building:lod0RoofEdge>', out)

    def test_partial_then_remaining_is_same_product(self):
        raw = fixture()
        first, _ = L.transform(raw, '40220', ['40220-bldg-1'])
        last, ev = L.transform(first, '40220')
        self.assertEqual(last, L.transform(raw, '40220')[0])
        self.assertEqual(ev['excluded'], [{'id': '40220-bldg-1', 'reason': 'already-roofedge'}])

    def test_wrong_schema_or_municipality_is_rejected(self):
        for raw, city in [(fixture().replace(b'uro/3.1', b'uro/3.2'), '40220'), (fixture(), '13106')]:
            with self.subTest(city=city), self.assertRaises(ValueError): L.transform(raw, city)

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError): L.transform(fixture().replace(b'40220-bldg-2', b'40220-bldg-1'), '40220')

    def test_duplicate_gml_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            L.transform(fixture().replace(b'gml:id="b2"', b'gml:id="b1"'), '40220')

    def test_coexisting_properties_are_rejected(self):
        with self.assertRaises(ValueError): L.transform(fixture().replace(b'</b:Building>', b'<b:lod0RoofEdge/></b:Building>'), '40220')

    def test_dtd_is_rejected(self):
        with self.assertRaises(ValueError): L.transform(fixture().replace(b'<core:CityModel', b'<!DOCTYPE core:CityModel []><core:CityModel', 1), '40220')

    def test_building_part_and_referenced_geometry_are_rejected(self):
        for raw in [fixture().replace(b'</b:Building>', b'<b:BuildingPart/></b:Building>'),
                    fixture().replace(b'<b:lod0FootPrint>', b'<b:lod0FootPrint xmlns:x="http://www.w3.org/1999/xlink" x:href="#other">')]:
            with self.assertRaises(ValueError): L.transform(raw, '40220')

    def test_namespace_lookalike_cannot_be_a_target(self):
        raw = fixture().replace(b'<b:lod0FootPrint>', b'<f:lod0FootPrint xmlns:f="urn:foreign">').replace(b'</b:lod0FootPrint>', b'</f:lod0FootPrint>')
        out, ev = L.transform(raw, '40220')
        self.assertEqual(out, raw)
        self.assertEqual(ev['targets'], [])


class ManifestAndGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.repo = GitRepo(self.root)
        (self.root / 'docs').mkdir()
        self.rationale = self.root / 'docs/rationale.md'
        self.rationale.write_text('Evidence for this fixture only. Human review required.\n')
        self.repo.run('add', 'docs/rationale.md')
        self.base = self.repo.commit_text(fixture().decode(), 'Record fixture source')
        self.path = self.root / 'provenance/semantic-correction/pilot.json'
        self.args = argparse.Namespace(repository='example/citygml', mesh='50305489', municipality='40220',
            current=str(self.root / 'tile.gml'), rationale=str(self.rationale), product='tile.gml',
            current_uri=f'git:{self.base}:tile.gml', rationale_uri=f'git:{self.base}:docs/rationale.md',
            tools_repo='example/tools', tools_commit='1' * 40, plan_issue='https://example.com/issues/1')
        self.m, self.product = L.build_manifest(self.args)

    def write_manifest(self):
        self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(json.dumps(self.m))

    def commits(self):
        self.write_manifest()
        L.cmd_commits(argparse.Namespace(repo=str(self.root), manifest=str(self.path)), self.m)
        return self.repo.run('rev-parse', 'HEAD')

    def test_two_building_commits_pass_full_gate(self):
        result = inspect_range(self.root, self.base, self.commits())
        self.assertEqual([r.errors for r in result], [[], []])
        self.assertEqual((self.root / 'tile.gml').read_bytes(), self.product)

    def test_materials_reproduction_and_changed_rationale(self):
        materials = self.root / 'materials'; materials.mkdir()
        (materials / 'current').write_bytes(fixture()); (materials / 'rationale').write_bytes(self.rationale.read_bytes())
        self.write_manifest()
        cmd = ['verify', '--manifest', str(self.path), '--materials-dir', str(materials)]
        self.assertEqual(L.main(cmd), 0)
        (materials / 'rationale').write_text('changed evidence')
        self.assertEqual(L.main(cmd), 1)

    def test_tampered_targets_product_and_input_fail(self):
        for field in ('targets', 'product', 'current'):
            m = copy.deepcopy(self.m)
            if field == 'targets': m['evidence']['targets'].pop()
            if field == 'product': m['products'][0]['sha256'] = '0' * 64
            raw = fixture().replace(b'>4<', b'>40<') if field == 'current' else fixture()
            with self.subTest(field=field), self.assertRaises(ValueError): L.reproduce(m, raw)

    def test_later_manifest_tampering_is_rejected(self):
        self.commits(); self.m['evidence']['targets'].pop(); self.write_manifest()
        self.repo.run('add', '.'); self.repo.run('commit', '-qm', 'Tamper with manifest')
        self.assertTrue(any(r.errors for r in inspect_range(self.root, self.base, self.repo.run('rev-parse', 'HEAD'))))

    def test_unrelated_file_cannot_hide_in_bulk_pr(self):
        self.commits(); (self.root / 'extra.txt').write_text('unrelated')
        self.repo.run('add', '.'); self.repo.run('commit', '-qm', 'Extra file')
        result = inspect_range(self.root, self.base, self.repo.run('rev-parse', 'HEAD'))
        self.assertTrue(any('outside' in e for r in result for e in r.errors))

    def test_header_change_even_with_same_buildings_is_rejected(self):
        self.commits(); path = self.root / 'tile.gml'
        path.write_bytes(path.read_bytes().replace(b'must remain literal', b'header altered'))
        self.repo.run('add', '.'); self.repo.run('commit', '-qm', 'Alter header')
        result = inspect_range(self.root, self.base, self.repo.run('rev-parse', 'HEAD'))
        self.assertTrue(any('Full PR product' in e for r in result for e in r.errors))

    def test_reverted_unrelated_change_is_still_rejected(self):
        self.commits()
        path = self.root / 'extra.txt'
        path.write_text('not part of the recipe')
        self.repo.run('add', '.'); self.repo.run('commit', '-qm', 'Add extra')
        path.unlink()
        self.repo.run('add', '-u'); self.repo.run('commit', '-qm', 'Remove extra')
        self.assertEqual((self.root / 'tile.gml').read_bytes(), self.product)
        result = inspect_range(self.root, self.base, self.repo.run('rev-parse', 'HEAD'))
        self.assertTrue(any('outside' in e for r in result for e in r.errors))

    def test_ci_preflight_rejects_local_refs_and_wrong_tools_before_fetch(self):
        self.m['repository'] = 'example/citygml'
        self.m['plan_issue'] = 'https://github.com/example/citygml/issues/1'
        self.write_manifest()
        cmd = ['check', '--manifest', str(self.path), '--ci', '--tools-commit', '1' * 40]
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'example/citygml'}):
            self.assertEqual(L.main(cmd), 0)
            self.assertEqual(L.main(cmd[:-1] + ['2' * 40]), 1)
            self.m['materials'][1]['uri'] = self.rationale.as_uri(); self.write_manifest()
            self.assertEqual(L.main(cmd), 1)
            self.m['materials'][1]['name'] = '../escape'; self.write_manifest()
            self.assertEqual(L.main(cmd), 1)

    def test_generate_cannot_overwrite_source(self):
        raw = (self.root / 'tile.gml').read_bytes()
        rc = L.main(['generate', '--repository', 'example/citygml', '--mesh', '50305489',
            '--municipality', '40220', '--current', str(self.root / 'tile.gml'),
            '--rationale', str(self.rationale), '--product', 'tile.gml', '--tools-commit', '1' * 40,
            '--plan-issue', 'https://example.com/issues/1', '--output', str(self.root / 'tile.gml')])
        self.assertEqual(rc, 1)
        self.assertEqual((self.root / 'tile.gml').read_bytes(), raw)

    def test_wrong_base_ref_and_dirty_worktree_fail_before_writes(self):
        self.write_manifest(); self.m['materials'][0]['uri'] = 'git:' + '2' * 40 + ':tile.gml'
        with self.assertRaises(ValueError): L.cmd_commits(argparse.Namespace(repo=str(self.root), manifest=str(self.path)), self.m)
        self.assertEqual(self.repo.run('rev-parse', 'HEAD'), self.base)
        self.m['materials'][0]['uri'] = f'git:{self.base}:tile.gml'
        (self.root / 'extra.txt').write_text('untracked user work')
        with self.assertRaises(ValueError): L.cmd_commits(argparse.Namespace(repo=str(self.root), manifest=str(self.path)), self.m)
        self.assertEqual(self.repo.run('rev-parse', 'HEAD'), self.base)

    def test_path_escape_and_fake_zero_version_are_rejected(self):
        for value in ('../outside.gml', '/tmp/outside.gml'):
            m = copy.deepcopy(self.m); m['products'][0]['path'] = value
            with self.assertRaises(ValueError): L.contract(m)
        m = copy.deepcopy(self.m); m['builder']['tools_commit'] = '0' * 40
        with self.assertRaises(ValueError): L.contract(m)


if __name__ == '__main__': unittest.main()
