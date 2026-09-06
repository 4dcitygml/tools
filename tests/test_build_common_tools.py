# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts import build_common_tools as B
from tests.test_commit_building_scope import GitRepo


class CommonToolsArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base / 'src'; self.root.mkdir()
        for name in B.DIRECTORIES:
            (self.root / name).mkdir()
        for name in B.REQUIRED:
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('test fixture\n')
        self.archive = self.base / 'tools.zip'

    def test_local_archive_is_deterministic_and_has_no_release_identity(self):
        manifest = B.build(self.root, self.archive)
        other = self.base / 'other.zip'; B.build(self.root, other)
        self.assertEqual(self.archive.read_bytes(), other.read_bytes())
        self.assertEqual(manifest['mode'], 'local-preview')
        self.assertIsNone(manifest['source_commit'])
        self.assertIsNone(manifest['release_tag'])
        self.assertTrue(B.REQUIRED <= manifest['files'].keys())

    def test_caches_and_city_data_outside_source_dirs_are_not_packaged(self):
        (self.root / 'scripts/__pycache__').mkdir()
        (self.root / 'scripts/__pycache__/ignored.pyc').write_bytes(b'cache')
        (self.root / 'udx').mkdir(); (self.root / 'udx/city.gml').write_text('data')
        (self.root / 'tools/.token').write_text('test secret fixture')
        manifest = B.build(self.root, self.archive)
        self.assertFalse(any('ignored' in n or 'city.gml' in n or '.token' in n for n in manifest['files']))

    def test_symlink_rejected(self):
        (self.root / 'scripts/linked.py').symlink_to(self.root / 'README.md')
        with self.assertRaises(ValueError): B.build(self.root, self.archive)

    def test_missing_required_file_rejected(self):
        (self.root / 'scripts/lod0_semantic_manifest.py').unlink()
        with self.assertRaises(ValueError): B.build(self.root, self.archive)

    def test_existing_output_is_not_overwritten(self):
        B.build(self.root, self.archive); raw = self.archive.read_bytes()
        with self.assertRaises(FileExistsError): B.build(self.root, self.archive)
        self.assertEqual(self.archive.read_bytes(), raw)

    def rewrite(self, modify):
        with zipfile.ZipFile(self.archive) as z:
            entries = [(i, z.read(i.filename)) for i in z.infolist()]
        bad = self.base / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as z:
            for info, raw in entries:
                z.writestr(info, modify(info.filename, raw))
        return bad

    def test_changed_payload_rejected(self):
        B.build(self.root, self.archive)
        bad = self.rewrite(lambda n, raw: b'changed' if n.endswith('/README.md') else raw)
        with self.assertRaisesRegex(ValueError, 'digest'): B.verify(bad)

    def test_undeclared_and_traversal_entries_rejected(self):
        for i, name in enumerate(['citygml-tools/extra.txt', '../escape']):
            archive = self.base / f'archive{i}.zip'; B.build(self.root, archive)
            with zipfile.ZipFile(archive, 'a') as z: z.writestr(name, 'extra')
            with self.assertRaises(ValueError): B.verify(archive)

    def test_tagged_release_requires_clean_matching_tree(self):
        repo = GitRepo(self.root); repo.run('add', '.'); repo.run('commit', '-qm', 'Fixture')
        repo.run('tag', 'hub-v1.2.3')
        m = B.build(self.root, self.archive, 'hub-v1.2.3')
        self.assertEqual(m['source_commit'], repo.run('rev-parse', 'HEAD'))
        (self.root / 'README.md').write_text('changed')
        with self.assertRaises(ValueError): B.build(self.root, self.base / 'dirty.zip', 'hub-v1.2.3')

    def test_ignored_files_cannot_enter_a_release_payload(self):
        (self.root / '.gitignore').write_text('scripts/ignored.py\n')
        repo = GitRepo(self.root); repo.run('add', '.'); repo.run('commit', '-qm', 'Fixture'); repo.run('tag', 'hub-v1.2.3')
        (self.root / 'scripts/ignored.py').write_text('unversioned')
        with self.assertRaisesRegex(ValueError, 'tagged Git tree'):
            B.build(self.root, self.archive, 'hub-v1.2.3')


if __name__ == '__main__': unittest.main()
