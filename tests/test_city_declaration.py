# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The city declaration (4dcitygml.json): one schema, one reader (scripts/city_declaration.py),
and no other module opening the file (v2.0.0 phase 1, F1)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import city_declaration as cd
from scripts.building_identity import rule_from_config
from scripts.provenance_manifest import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads(cd.SCHEMA.read_text(encoding="utf-8"))

TOKYO = {"id": "tokyo-station", "name": {"en": "Tokyo Station demo", "de": "Demo Tokio"}, "repo": "o/tokyo",
         "country": "JP", "lang": "ja", "dialect": "plateau", "crs": "EPSG:6697",
         "building_id": {"type": "uro:buildingID"}, "data_dirs": ["13101_chiyoda-ku_pref_2023_citygml_1_op"],
         "map": {"tiles": "gsi", "center": [35.6812, 139.7671], "zoom": 16}, "logo": "logo.png",
         "attribution": "PLATEAU", "license": "CC-BY-4.0", "tools": "a" * 40, "min_hub": "hub-v1.5.0"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class TestReader(unittest.TestCase):
    def test_working_tree_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "t@example.com")
            git(repo, "config", "user.name", "t")
            (repo / cd.FILE).write_text(json.dumps({"repo": "o/r", "lang": "de"}), encoding="utf-8")
            git(repo, "add", cd.FILE)
            git(repo, "commit", "-qm", "one")
            first = git(repo, "rev-parse", "HEAD")
            (repo / cd.FILE).write_text(json.dumps({"repo": "o/r", "lang": "ja-JP"}), encoding="utf-8")
            self.assertEqual(cd.read(repo).language, "ja")              # the working tree
            self.assertEqual(cd.read(repo, first).language, "de")       # as committed at a revision
            self.assertEqual(cd.read(repo, "0" * 40).raw, {})           # an unknown revision reads as empty

    def test_missing_broken_or_not_an_object_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertEqual(cd.read(repo).raw, {})
            for text in ("{not json", "[1, 2]", '"a string"'):
                (repo / cd.FILE).write_text(text, encoding="utf-8")
                self.assertEqual(cd.read(repo).raw, {}, text)

    def test_the_accessors_are_tolerant(self):
        empty = cd.Declaration()
        self.assertEqual((empty.repo, empty.data_dirs, empty.language, empty.dialect, empty.crs, empty.logo),
                         ("", [], "en", None, None, None))
        odd = cd.Declaration({"data_dirs": ["/lod2_citygml/", "", 3, "x"], "lang": 5, "logo": 1, "dialect": 3})
        self.assertEqual(odd.data_dirs, ["lod2_citygml", "x"])
        self.assertEqual(cd.Declaration({"data_dirs": "lod2_citygml"}).data_dirs, [])   # not a list
        self.assertEqual((odd.language, odd.logo, odd.dialect), ("en", None, None))

    def test_the_pack_brings_defaults_the_city_may_override(self):
        # X1 Q2/Q3: building_id and crs from the dialect pack when the city declares none
        plateau = cd.Declaration({"repo": "o/r", "dialect": "plateau"})
        self.assertEqual((plateau.crs, rule_from_config(plateau.resolved).type), ("EPSG:6697", "uro:buildingID"))
        own = cd.Declaration({"dialect": "plateau", "crs": "EPSG:6668", "building_id": {"type": "gml:id"}})
        self.assertEqual((own.crs, rule_from_config(own.resolved).type), ("EPSG:6668", "gml:id"))
        plain = cd.Declaration({"repo": "o/r"})
        self.assertEqual((plain.crs, rule_from_config(plain.resolved).type), (None, "gml:id"))
        # the pack interface (dialects/): the reader asks dialect(name) once; no name, an unknown one or
        # one that is no module name is plain CityGML, which has no answers
        import dialects
        self.assertIs(plain.pack, dialects.PLAIN)
        for name in (None, "", "alkis", "../x", "Plateau", 3):
            self.assertIs(dialects.dialect(name), dialects.PLAIN, name)
        self.assertEqual((plateau.pack.name, bool(plateau.pack.bulk_kinds), sorted(plateau.pack.kinds)),
                         ("plateau", True, ["schema-update", "scope-extract"]))
        self.assertEqual(plateau.pack.municipality(b"<uro:city>13101</uro:city>"), "13101")
        self.assertIsNone(plain.pack.municipality)
        for value, lang in (("ja", "ja"), ("ja-JP", "ja"), ("DE", "de"), (None, "en"), ("", "en")):
            self.assertEqual(cd.language(value), lang, value)

    def test_the_rule_comes_from_the_declaration(self):
        declaration = cd.Declaration({"building_id": {"type": "gen:BIN", "invalid_values": ["1000000"]}})
        rule = rule_from_config(declaration.raw)
        self.assertEqual((rule.type, rule.invalid_values), ("gen:BIN", frozenset({"1000000"})))

    def test_command_line(self):
        # ci/city_checkouts.sh pipes a declaration in and reads the data folders, one per line
        script = REPO_ROOT / "scripts" / "city_declaration.py"

        def run(stdin):
            return subprocess.run([sys.executable, str(script)], input=stdin, capture_output=True).stdout
        self.assertEqual(run(json.dumps({"data_dirs": ["a/", "b"]}).encode()).decode().split(), ["a", "b"])
        self.assertEqual(run(b""), b"")                                    # nothing declared, no line
        self.assertEqual(run(b"not json"), b"")


class TestSchema(unittest.TestCase):
    def errors(self, data):
        return validate(data, SCHEMA)

    def test_a_full_declaration_conforms(self):
        self.assertEqual(self.errors(TOKYO), [])
        self.assertEqual(self.errors({"repo": "o/r"}), [])                           # repo is the one required key

    def test_min_hub_is_the_hub_release_tag_grammar(self):
        # the schema carries the pattern text; scripts/release_tags.py is the grammar: they agree
        from scripts import release_tags
        pattern = SCHEMA["properties"]["min_hub"]["pattern"]
        for tag in ("hub-v2.0.0", "hub-v2.0.0-beta.3", "hub-v2.0.0-rc.1", "hub-v10.20.30", "hub-v2.0", "hub-2.0.0",
                    "tools-v2.0.0", "hub-v2.0.0-", "hub-v2.0.0-a_b", "v2.0.0", "hub-v2.0.0 "):
            hub = (release_tags.parse(tag) or ("",))[0] == "hub"
            self.assertEqual(self.errors({"repo": "o/r", "min_hub": tag}) == [], hub, tag)
        self.assertIn("(-[A-Za-z0-9.-]+)?", pattern)

    def test_the_declarations_of_the_city_checkouts_conform(self):
        # the lab cities declared a min_hub the schema refused and nothing noticed; CI checks the cities out
        from tests import city_checkouts
        refs = json.loads((REPO_ROOT / "ci" / "city-refs.json").read_text(encoding="utf-8"))["repositories"]
        found = 0
        for folder in refs:
            path = REPO_ROOT.parent / folder / "4dcitygml.json"
            if not path.is_file():
                if folder != "city-template":      # the template ships the declaration only as .example
                    city_checkouts.missing(f"{folder}/4dcitygml.json is not checked out next to the tools")
                continue
            found += 1
            self.assertEqual(self.errors(json.loads(path.read_text(encoding="utf-8"))), [], folder)
        if not found:
            city_checkouts.missing("no city checkout next to the tools")

    def test_what_the_schema_refuses(self):
        for change, needle in (
                ({"repo": None}, "string expected"), ({"units": "m"}, "unexpected property units"),
                ({"building_id": {"type": "uro:buildingID", "official": "ALKIS"}}, "unexpected property official"),
                ({"building_id": {"type": "my:id"}}, "does not match"), ({"dialect": "alkis"}, "not in"), ({"edition": "iur-3.0"}, "unexpected property edition"),
                ({"crs": "6697"}, "does not match"), ({"data_dirs": ["../x"]}, "does not match"),
                ({"data_dirs": []}, "at least 1"), ({"map": {"center": [1.0, 2.0, 3.0]}}, "at most 2"),
                ({"map": {"zoom": 30}}, "above maximum"), ({"tools": "tools-v2.0.0"}, "does not match"),
                ({"min_hub": "2.0.0"}, "does not match"), ({"logo": "logo.svg"}, "does not match"),
                ({"oauthClientId": ""}, "unexpected property oauthClientId")):
            with self.subTest(change=change):
                self.assertTrue(any(needle in e for e in self.errors({**TOKYO, **change})), self.errors({**TOKYO, **change}))
        missing = dict(TOKYO)
        del missing["repo"]
        self.assertIn("$: missing required repo", self.errors(missing))

    def test_the_logo_types_are_the_theme_loaders(self):
        loader = (REPO_ROOT / "tools" / "themes" / "theme_loader.py").read_text(encoding="utf-8")
        allowed = set(re.findall(r'"\.(\w+)": "image/', loader))
        pattern = SCHEMA["properties"]["logo"]["pattern"]
        self.assertEqual(set(re.search(r"\\\.\(([\w|]+)\)\$", pattern).group(1).split("|")), allowed)


class TestOneReader(unittest.TestCase):
    def test_no_other_module_opens_the_declaration(self):
        # F1: every tool asks scripts/city_declaration.py; the shell that has no Python reader of its
        # own pipes the bytes into it (ci/city_checkouts.sh)
        opening = re.compile(r'/ *"4dcitygml\.json"|:4dcitygml\.json|4dcitygml\.json"\)\.(read|is_file|exists)'
                             r'|open\([^)]*4dcitygml')
        offenders = []
        for path in [*(REPO_ROOT / "scripts").glob("*.py"), *(REPO_ROOT / "tools").rglob("*.py"),
                     *(REPO_ROOT / "ci").glob("*.sh")]:
            if path.name == "city_declaration.py":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if opening.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
        self.assertEqual([o.split(":")[0] for o in offenders], ["ci/city_checkouts.sh"])   # it pipes the bytes in
        checkouts = (REPO_ROOT / "ci" / "city_checkouts.sh").read_text(encoding="utf-8")
        self.assertIn('python3 "$TOOLS/scripts/city_declaration.py"', checkouts)

    def test_the_hub_bundle_ships_the_reader(self):
        bundle = (REPO_ROOT / "scripts" / "build_bundle.py").read_text(encoding="utf-8")
        self.assertEqual(bundle.count('"city_declaration.py"'), 1)   # in manifest(); required() derives from it (V8)
        workflow = (REPO_ROOT / ".github" / "workflows" / "release-hub.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("scripts/city_declaration.py"), workflow.count("scripts/citygml_faces.py"))



class TestTiles(unittest.TestCase):
    """The tiles of a buildings folder (runtime.tile_files): the pack's tile names and frames.
    The editors and the hub shared the rule as two copies, and the PLATEAU names were in both (V7)."""

    def test_the_packs_names_and_frames(self):
        import dialects
        from tests.support import runtime
        plateau = dialects.dialect("plateau")
        with tempfile.TemporaryDirectory() as d:
            for name in ("53394611_bldg_6697_op.gml", "690_5335_1.gml"):
                (Path(d) / name).write_text("<x/>", encoding="utf-8")
            self.assertEqual(list(runtime.tile_files(d, plateau)), ["53394611"])   # its names only, once it has some
            self.assertEqual(sorted(runtime.tile_files(d, dialects.PLAIN)), ["53394611_bldg_6697_op", "690_5335_1"])
            (Path(d) / "53394611_bldg_6697_op.gml").unlink()
            self.assertEqual(list(runtime.tile_files(d, plateau)), ["690_5335_1"])   # a folder without them: every file
        south, west, north, east = plateau.tile_frame("53394611")
        self.assertAlmostEqual(south, 35.675, places=3)
        self.assertAlmostEqual(west, 139.7625, places=4)
        self.assertIsNone(dialects.PLAIN.tile_frame)

if __name__ == "__main__":
    unittest.main()
