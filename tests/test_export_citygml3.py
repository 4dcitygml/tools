#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/export_citygml3.py: the CityGML 3 export for every city (REQ-dialect-packs section 7).
citygml-tools is not part of the test run: a stand-in answers its three commands."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import city_declaration, export_citygml3 as X

CORE = "http://www.opengis.net/citygml/2.0"
TILE = f"""<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="{CORE}" xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
  xmlns:app="http://www.opengis.net/citygml/appearance/2.0" xmlns:gml="http://www.opengis.net/gml" {{ns}}>
 <core:cityObjectMember><bldg:Building gml:id="b1">{{ade}}</bldg:Building></core:cityObjectMember>
 <core:cityObjectMember><bldg:Building gml:id="b2"/></core:cityObjectMember>
 <app:appearanceMember><app:Appearance gml:id="ap1"><app:surfaceDataMember><app:ParameterizedTexture>
  <app:imageURI>tex/a.jpg</app:imageURI></app:ParameterizedTexture></app:surfaceDataMember></app:Appearance></app:appearanceMember>
 <app:appearanceMember><app:Appearance><app:surfaceDataMember><app:ParameterizedTexture>
  <app:imageURI>https://example.org/b.jpg</app:imageURI></app:ParameterizedTexture></app:surfaceDataMember></app:Appearance></app:appearanceMember>
</core:CityModel>
"""
PLAIN = TILE.format(ns="", ade="")
WITH_ADE = TILE.format(ns='xmlns:x="urn:example:ade"', ade="<x:height>3</x:height>")


class FakeTools:
    """citygml-tools' --version, upgrade (a copy into -o) and validate; every call is recorded."""

    def __init__(self, valid=True):
        self.calls, self.valid = [], valid

    def __call__(self, args, **kwargs):
        self.calls.append(args[1:])
        out = ""
        if args[1] == "--version":
            out = "citygml-tools version 2.5.0"
        elif args[1] == "upgrade":
            shutil.copyfile(args[-1], Path(args[args.index("-o") + 1]) / Path(args[-1]).name)
        elif args[1] == "validate":
            out = "All files are valid." if self.valid else "Invalid content."
        return subprocess.CompletedProcess(args, 0 if self.valid else 1, stdout=out, stderr="")


class ExportCase(unittest.TestCase):
    def city(self, declaration: dict, files: dict) -> Path:
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        (repo / city_declaration.FILE).write_text(json.dumps({"repo": "o/r", "lang": "de", **declaration}), encoding="utf-8")
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text, encoding="utf-8")
        return repo

    def export(self, repo, tools, **kw):
        with mock.patch.object(X.subprocess, "run", tools):
            return X.export(repo, repo / "out", Path("citygml-tools"), **kw)


class TestScan(unittest.TestCase):
    def test_one_pass_reads_names_appearances_and_image_folders(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "t.gml"
            src.write_text(PLAIN, encoding="utf-8")
            members = []
            found = X.scan(src, on_member=lambda el: members.append(el.tag.rsplit("}", 1)[1]))
        self.assertEqual(members, ["cityObjectMember", "cityObjectMember", "appearanceMember", "appearanceMember"])
        self.assertEqual(found.appearance_ids, {"ap1"})
        self.assertEqual(found.image_dirs, {"tex"})   # a URL is not a local folder
        self.assertIn((CORE, "CityModel"), found.qnames)


class TestExport(ExportCase):
    def test_a_plain_city_is_upgraded_and_validated(self):
        repo = self.city({"data_dirs": ["lod2"]}, {"lod2/a.gml": PLAIN, "lod2/b.gml": PLAIN, "lod2/codelists/c.xml": "<c/>"})
        tools = FakeTools()
        written = self.export(repo, tools)
        self.assertEqual(written, [repo / "out/lod2/a.gml", repo / "out/lod2/b.gml"])
        self.assertTrue((repo / "out/lod2/codelists/c.xml").is_file())
        self.assertTrue((repo / "out/lod2/tex").is_dir())   # the tool writes the images below it
        self.assertEqual([c for c in tools.calls if c[0] == "validate"], [["validate", str(repo / "out/lod2/*.gml")]])

    def test_an_unknown_vocabulary_is_refused_before_anything_is_written(self):
        repo = self.city({"data_dirs": ["lod2"]}, {"lod2/a.gml": WITH_ADE})
        tools = FakeTools()
        with self.assertRaisesRegex(X.ConversionError, "urn:example:ade"):
            self.export(repo, tools)
        self.assertEqual(tools.calls, [])
        self.assertFalse((repo / "out/lod2/a.gml").exists())

    def test_an_invalid_output_fails(self):
        repo = self.city({"data_dirs": ["lod2"]}, {"lod2/a.gml": PLAIN})
        with self.assertRaisesRegex(X.ConversionError, "not schema-valid"):
            self.export(repo, FakeTools(valid=False))

    def test_no_building_files_is_an_error(self):
        repo = self.city({"data_dirs": ["lod2"]}, {"lod2/readme.txt": ""})
        with self.assertRaisesRegex(X.ConversionError, "no building files"):
            self.export(repo, FakeTools())

    def test_a_provisional_namespace_needs_the_consent(self):
        # PLATEAU's hook writes the provisional i4d-UR: without the consent nothing runs
        repo = self.city({"data_dirs": ["pkg"], "dialect": "plateau"}, {"pkg/udx/bldg/a.gml": PLAIN})
        tools = FakeTools()
        with self.assertRaisesRegex(X.ConversionError, "provisional"):
            self.export(repo, tools)
        self.assertEqual(tools.calls, [])

    def test_the_command_line_names_the_error(self):
        repo = self.city({"data_dirs": ["lod2"]}, {"lod2/a.gml": WITH_ADE})
        with mock.patch("sys.stderr") as err:
            code = X.main(["export", "--repo", str(repo), "--outdir", str(repo / "out"), "--citygml-tools", "t"])
        self.assertEqual(code, 2)
        self.assertIn("urn:example:ade", "".join(c.args[0] for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
