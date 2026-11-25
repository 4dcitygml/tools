# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""D-4a4: a bulk manifest's material name and archive member paths stay inside the materials
folder, and every manifest is checked against the schema before anything is fetched. Before the
fix, a data-only PR could name a material `../x` and write a file of its choosing outside the
folder (over the tools checkout's scripts, say) from its own commits."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import fetch_materials, provenance_manifest


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class FetchStaysInsideTheFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo, self.out = root / "city", root / "materials"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        self.payload = b"print('forged analysis')\n"
        (self.repo / "data.gml").write_bytes(self.payload)
        git(self.repo, "add", "data.gml")
        git(self.repo, "commit", "-qm", "one")
        self.sha = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def material(self, name: str) -> dict:
        return {"name": name, "uri": f"git:{self.sha}:data.gml", "sha256": hashlib.sha256(self.payload).hexdigest(),
                "bytes": len(self.payload)}

    def test_a_name_that_leaves_the_folder_is_refused(self):
        escape = Path(self.tmp.name) / "escaped.py"
        for name in ("../escaped.py", "sub/../../escaped.py", str(escape), ".."):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                fetch_materials.fetch_material(self.material(name), self.out, self.repo)
        self.assertFalse(escape.exists())
        self.assertEqual(fetch_materials.fetch_material(self.material("data.gml"), self.out, self.repo),
                         (self.out / "data.gml").resolve())

    def test_the_schema_refuses_such_names_and_member_paths(self):
        schema = provenance_manifest.load_schema()
        material = schema["properties"]["materials"]["items"]["properties"]
        for bad in ("../x.zip", "a/b.zip", "a\\b.zip", ".."):
            self.assertTrue(provenance_manifest.validate(bad, material["name"]), bad)
        self.assertEqual(provenance_manifest.validate("13100_tokyo23-ku_2020_citygml_4_2_op.zip", material["name"]), [])
        member = material["members"]["items"]["properties"]["path"]
        for bad in ("../x.gml", "a/../../x.gml", "/etc/x", "a\\b.gml", "C:x.gml"):
            self.assertTrue(provenance_manifest.validate(bad, member), bad)
        self.assertEqual(provenance_manifest.validate("13100_tokyo23-ku_2020_citygml_4_2_op/udx/bldg/a_op.gml", member), [])

    def test_every_manifest_is_checked_before_anything_is_fetched(self):
        # before: only semantic-correction validated first; fetch_materials took any JSON
        manifest = Path(self.tmp.name) / "m.json"
        manifest.write_text(json.dumps({"materials": [self.material("../escaped.py")]}), encoding="utf-8")
        with self.assertRaises(SystemExit) as refused:
            fetch_materials.main(["--manifest", str(manifest), "--outdir", str(self.out), "--repo", str(self.repo)])
        self.assertIn("schema", str(refused.exception))
        self.assertFalse((Path(self.tmp.name) / "escaped.py").exists())
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))


if __name__ == "__main__":
    unittest.main()
