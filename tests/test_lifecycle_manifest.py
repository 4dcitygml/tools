# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.lifecycle_manifest import validate
from scripts.commit_building_scope import inspect_range
from tests.test_commit_building_scope import GitRepo, _gml_members


def event(kind='merge', old=('A', 'B'), new=('C',)):
    return {'version': 1, 'eventId': 'event-2026-001', 'kind': kind,
            'oldIds': list(old), 'newIds': list(new), 'reason': 'One documented building event',
            'confirmedOn': '2026-09-06',
            'evidence': [{'title': 'Survey record', 'url': 'https://example.org/survey/1'}]}


class ManifestTest(unittest.TestCase):
    def test_rebuild_split_merge(self):
        for data in [event(), event('rebuild', ('A','B'), ('C','D')), event('split', ('A',), ('B','C'))]:
            self.assertEqual(validate(data), [])
    def test_invalid_or_missing_records(self):
        for key, value in [('oldIds', []), ('newIds', ['C','C']), ('oldIds', ['A', {}]),
                           ('evidence', []), ('evidence', [{'title':'x','url':'javascript:alert(1)'}]),
                           ('confirmedOn', '2026-02-30'), ('kind', 'other'), ('reason', ''), ('version', True)]:
            with self.subTest(key=key, value=value):
                data=event();data[key]=value;self.assertTrue(validate(data))
        data=event();data['occurredOn']='2026-09-07';self.assertTrue(validate(data))
    def test_wrong_cardinality_or_multiple_events(self):
        self.assertTrue(validate(event('split')))
        self.assertTrue(validate(event('merge', ('A',), ('B', 'C'))))
        data=event();data['events']=[event()];self.assertTrue(validate(data))


class LifecycleGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.repo=GitRepo(Path(self.tmp.name))
        self.base=self.repo.commit_text(self.gml(['A','B','U']), 'baseline\n\nChange-Type: source-baseline')
    def tearDown(self):self.tmp.cleanup()
    def gml(self, ids, changed=()):
        return _gml_members([(bid,'13101',2 if bid in changed else 1) for bid in ids])
    def propose(self, data=None, new=('C','U'), changed=(), trailers=None, bad_hash=False):
        data=data or event()
        path=self.repo.root/'provenance/lifecycle/event.json';path.parent.mkdir(parents=True,exist_ok=True)
        raw=json.dumps(data).encode();path.write_bytes(raw)
        self.repo.run('add', 'provenance/lifecycle/event.json')
        digest='0'*64 if bad_hash else hashlib.sha256(raw).hexdigest()
        if trailers is None:
            trailers=['Building-Deleted: A','Building-Deleted: B','Building-Added: C']
        message='Lifecycle proposal\n\nChange-Type: lifecycle\n'+ '\n'.join(trailers)+f'\nLifecycle-Manifest: provenance/lifecycle/event.json@sha256:{digest}'
        return self.repo.commit_text(self.gml(new,changed),message)
    def errors(self, head):return '\n'.join(e for r in inspect_range(self.repo.root,self.base,head) for e in r.errors)
    def test_merge_and_manifest_pass(self):
        head=self.propose();self.assertEqual(self.errors(head),'')
        self.assertEqual(inspect_range(self.repo.root,self.base,head)[0].lifecycle['oldIds'],['A','B'])
    def test_rebuild_many_to_many(self):
        head=self.propose(event('rebuild',('A','B'),('C','D')),new=('C','D','U'),
                          trailers=['Building-Deleted: A','Building-Deleted: B','Building-Added: C','Building-Added: D'])
        self.assertEqual(self.errors(head),'')
    def test_split_with_retained_id(self):
        head=self.propose(event('split',('A',),('A','C')),new=('A','B','C','U'),changed=('A',),
                          trailers=['Building: A','Building-Added: C'])
        self.assertEqual(self.errors(head),'')
    def test_reversed_added_deleted_trailers_fail(self):
        head=self.propose(trailers=['Building-Added: A','Building-Added: B','Building-Deleted: C'])
        self.assertIn('category',self.errors(head))
    def test_duplicate_trailer_fails(self):
        head=self.propose(trailers=['Building-Deleted: A','Building-Deleted: B','Building-Added: C','Building-Added: C'])
        self.assertIn('category',self.errors(head))
    def test_unrelated_building_change_fails(self):
        head=self.propose(changed=('U',),trailers=['Building-Deleted: A','Building-Deleted: B','Building-Added: C','Building: U'])
        self.assertIn('outside this lifecycle',self.errors(head))
    def test_wrong_relation_and_hash_fail(self):
        head=self.propose(event(old=('A','X')))
        self.assertIn('old/new',self.errors(head))
        self.repo.run('reset','--hard',self.base)
        self.assertIn('SHA-256',self.errors(self.propose(bad_hash=True)))
    def test_missing_manifest_fails(self):
        head=self.repo.commit_text(self.gml(['C','U']),'merge\n\nChange-Type: lifecycle\nBuilding-Deleted: A\nBuilding-Deleted: B\nBuilding-Added: C')
        self.assertIn('Lifecycle-Manifest',self.errors(head))
    def test_other_commit_cannot_join_event_pr(self):
        self.propose()
        head=self.repo.commit_text(self.gml(['C','U'],('U',)),'unrelated\n\nBuilding: U')
        self.assertIn('dedicated commit',self.errors(head))
    def test_other_manifest_cannot_join_event(self):
        p=self.repo.root/'provenance/lifecycle/other.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(event()))
        self.repo.run('add', 'provenance/lifecycle/other.json')
        self.assertIn('other lifecycle records',self.errors(self.propose()))
    def test_existing_event_id_cannot_be_reused(self):
        p=self.repo.root/'provenance/lifecycle/previous.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(event()))
        self.repo.run('add', 'provenance/lifecycle/previous.json');self.repo.run('commit','-qm','prior event record')
        self.base=self.repo.run('rev-parse','HEAD')
        self.assertIn('already recorded',self.errors(self.propose()))
    def test_new_id_cannot_collide_in_an_unchanged_file(self):
        (self.repo.root/'other.gml').write_text(self.gml(['C']))
        self.repo.run('add','other.gml');self.repo.run('commit','-qm','other mesh')
        self.base=self.repo.run('rev-parse','HEAD')
        self.assertIn('unique across',self.errors(self.propose()))
    def test_no_gml_change_cannot_claim_lifecycle(self):
        p=self.repo.root/'note.txt';p.write_text('note');self.repo.run('add','note.txt')
        self.repo.run('commit','-qm','note\n\nChange-Type: lifecycle')
        self.assertIn('must change CityGML',self.errors(self.repo.run('rev-parse','HEAD')))
    def test_unrelated_single_building_additions_do_not_become_an_implicit_event(self):
        self.repo.commit_text(self.gml(['A','B','U','C']),'add C\n\nBuilding-Added: C')
        head=self.repo.commit_text(self.gml(['A','B','U','C','D']),'add D\n\nBuilding-Added: D')
        self.assertIn('declared lifecycle',self.errors(head))
    def test_one_standalone_addition_remains_normal(self):
        head=self.repo.commit_text(self.gml(['A','B','U','C']),'add C\n\nBuilding-Added: C')
        self.assertEqual(self.errors(head),'')


if __name__ == '__main__':unittest.main()
