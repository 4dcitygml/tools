#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Positive/negative tests for plausibility_lint (convention-layer lint: sentinels, plausible ranges, code lists)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from functools import partial  # noqa: E402

import dialects  # noqa: E402
from scripts import plausibility_lint  # noqa: E402
from scripts.citygml_lint import run_lint  # noqa: E402

PLATEAU = dialects.dialect("plateau")   # the fixtures are i-UR data: +-9999 means "unknown"

SQUARE = "0 0 0 0 1 0 1 1 0 1 0 0 0 0 0"


def bldg(bid, height=None, storeys=None, usage=None, codespace="../../codelists/Building_usage.xml"):
    parts = [f'<bldg:Building gml:id="{bid}">']
    if height is not None:
        parts.append(f'<bldg:measuredHeight uom="m">{height}</bldg:measuredHeight>')
    if storeys is not None:
        parts.append(f"<bldg:storeysAboveGround>{storeys}</bldg:storeysAboveGround>")
    if usage is not None:
        parts.append(f'<bldg:usage codeSpace="{codespace}">{usage}</bldg:usage>')
    parts.append("<bldg:lod0FootPrint><gml:MultiSurface><gml:surfaceMember>"
                 "<gml:Polygon><gml:exterior><gml:LinearRing>"
                 f"<gml:posList>{SQUARE}</gml:posList>"
                 "</gml:LinearRing></gml:exterior></gml:Polygon>"
                 "</gml:surfaceMember></gml:MultiSurface></bldg:lod0FootPrint>")
    parts.append("</bldg:Building>")
    return "".join(parts)


def wrap(*bodies):
    inner = "".join(f"<core:cityObjectMember>{b}</core:cityObjectMember>" for b in bodies)
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml">'
            f"{inner}</core:CityModel>").encode("utf-8")


def lint_bytes(raw):
    return run_lint(raw, "t.gml", partial(plausibility_lint.check_building, unknown_value=PLATEAU.unknown_value))


def warncodes(result):
    return {w["code"] for b in result["buildings"] for w in b["warnings"]}


class TestPlausibility(unittest.TestCase):
    def test_clean(self):
        res = lint_bytes(wrap(bldg("b", height=10, storeys=3)))
        self.assertEqual((res["n_errors"], res["n_warnings"]), (0, 0))

    def test_nonpositive_height_warning(self):
        res = lint_bytes(wrap(bldg("b", height=-5)))
        self.assertIn("nonpositive_height", warncodes(res))
        self.assertEqual(res["n_errors"], 0)  # the convention layer is non-blocking

    def test_height_sentinel_ignored(self):
        self.assertNotIn("nonpositive_height", warncodes(lint_bytes(wrap(bldg("b", height=-9999)))))

    def test_storeys_sentinel_ignored(self):
        self.assertNotIn("storeys_out_of_range", warncodes(lint_bytes(wrap(bldg("b", storeys=9999)))))

    def test_help_is_its_own(self):
        # D-c8: --help showed citygml_lint's text (run_main parsed with that module's docstring)
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            plausibility_lint.main(["--help"])
        self.assertIn("Data plausibility lint", out.getvalue())

    def test_sentinels_belong_to_i_ur_data(self):
        # v2.0.0 (1-2, X1): +-9999 means "unknown" only in a city of the plateau dialect; elsewhere
        # it is a value like any other
        from functools import partial
        raw = wrap(bldg("b", height=-9999))
        plain = run_lint(raw, "t.gml", plausibility_lint.check_building)   # no pack: no sentinels
        self.assertIn("nonpositive_height", warncodes(plain))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                gml = Path(tmp) / "t.gml"
                gml.write_bytes(raw)
                for declared, flagged in (({"repo": "o/r"}, True), ({"repo": "o/r", "dialect": "plateau"}, False)):
                    Path(tmp, "4dcitygml.json").write_text(json.dumps(declared), encoding="utf-8")
                    check = plausibility_lint.check_fn_for(gml)
                    result = run_lint(raw, "t.gml", check)
                    self.assertEqual("nonpositive_height" in warncodes(result), flagged, declared)
            finally:
                os.chdir(cwd)

    def test_storeys_out_of_range(self):
        self.assertIn("storeys_out_of_range", warncodes(lint_bytes(wrap(bldg("b", storeys=500)))))

    def test_height_out_of_range(self):
        self.assertIn("height_out_of_range", warncodes(lint_bytes(wrap(bldg("b", height=500)))))

    def test_height_in_feet_is_compared_in_metres(self):
        # US data: 500 ft is 152 m, plausible; 1200 ft is 366 m, above the limit
        feet = lambda h: wrap(bldg("b", height=h).replace('uom="m"', 'uom="ft"'))
        self.assertNotIn("height_out_of_range", warncodes(lint_bytes(feet(500))))
        self.assertIn("height_out_of_range", warncodes(lint_bytes(feet(1200))))

    def test_no_errors_ever(self):
        # the convention layer emits warnings only (never blocks)
        res = lint_bytes(wrap(bldg("b", height=-5, storeys=500)))
        self.assertEqual(res["n_errors"], 0)


# Codelist in the same format as real (PLATEAU) data. 411 = residential (valid), 461 = unknown (the canonical "unknown").
CODELIST = """<?xml version="1.0" encoding="UTF-8"?>
<gml:Dictionary xmlns:gml="http://www.opengis.net/gml" gml:id="cl">
  <gml:dictionaryEntry><gml:Definition gml:id="d1">
    <gml:description>住宅</gml:description><gml:name>411</gml:name>
  </gml:Definition></gml:dictionaryEntry>
  <gml:dictionaryEntry><gml:Definition gml:id="d2">
    <gml:description>不明</gml:description><gml:name>461</gml:name>
  </gml:Definition></gml:dictionaryEntry>
</gml:Dictionary>
"""


class TestMarker(unittest.TestCase):
    def test_the_comment_carries_only_its_marker(self):
        # Exchange Contract v4.0.0, A10 (T16): the former name <!-- plateau-quality-lint --> is gone
        import contextlib
        import io
        from scripts import pr_markers
        with tempfile.TemporaryDirectory() as tmp:
            gml = Path(tmp) / "t.gml"
            gml.write_bytes(wrap(bldg("b", height=500)))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                plausibility_lint.main([str(gml)])
        head = out.getvalue().splitlines()[:2]
        self.assertEqual(head[0], pr_markers.PLAUSIBILITY_LINT)
        self.assertNotIn("plateau-quality-lint", out.getvalue())


class CodelistFixture(unittest.TestCase):
    """The PLATEAU directory layout: <root>/codelists/*.xml and <root>/udx/bldg/*.gml (codeSpace is a
    relative path from the gml file)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "codelists").mkdir()
        (root / "codelists" / "Building_usage.xml").write_text(CODELIST, encoding="utf-8")
        self.gml_path = root / "udx" / "bldg" / "t.gml"
        self.gml_path.parent.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def lint(self, raw):
        return run_lint(raw, str(self.gml_path), plausibility_lint.check_fn_for(self.gml_path))


class TestCodelistCheck(CodelistFixture):
    """Codelist consistency (invalid_code)."""

    def test_valid_code_no_warning(self):
        res = self.lint(wrap(bldg("b", usage="411")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_unknown_code_allowed(self):
        # The "unknown" code is the canonical "unknown" = not an anomaly
        res = self.lint(wrap(bldg("b", usage="461")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_invalid_code_warning(self):
        res = self.lint(wrap(bldg("b", usage="999")))
        self.assertIn("invalid_code", warncodes(res))
        self.assertEqual(res["n_errors"], 0)  # convention layer = non-blocking

    def test_missing_codelist_skipped(self):
        # Referenced codelist missing = unresolvable -> excluded from checking (no false positives)
        res = self.lint(wrap(bldg("b", usage="999", codespace="../../codelists/NoSuchList.xml")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_external_url_skipped(self):
        res = self.lint(wrap(bldg("b", usage="999", codespace="http://example.com/cl.xml")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_without_path_context_skipped(self):
        # Without a file path (plain check_building), skip codelist checking (backward compatibility)
        res = lint_bytes(wrap(bldg("b", usage="999")))
        self.assertNotIn("invalid_code", warncodes(res))


class TestChangedCodeIsAnError(CodelistFixture):
    """A code that this pull request wrote and the local list lacks is an error (the `code-list` row);
    the same code at base stays the warning. The CI run reads base and head from git (patched here)."""

    REL = "udx/bldg/t.gml"

    def ci(self, base, head, *, extra=()):
        """plausibility_lint's CI run over a changed file: (exit code, the json files)."""
        import contextlib
        import io
        from scripts import citygml_lint
        from scripts.diff_citygml import load_buildings
        blobs = {"B": base, "H": head}
        root = Path(self._tmp.name)
        (root / "list.txt").write_text(self.REL + "\n", encoding="utf-8")
        out = io.StringIO()
        with patch("scripts.repo_git.blob", side_effect=lambda repo, sha, rel: blobs[sha]), \
                patch.object(citygml_lint, "buildings_at",
                             side_effect=lambda repo, sha, rel: load_buildings(blobs[sha]) if blobs[sha] else {}), \
                contextlib.redirect_stdout(out):
            code = plausibility_lint.main(["--file-list", str(root / "list.txt"), "--base-sha", "B", "--head-sha", "H",
                                           "--repo", str(root), "--json", *extra])
        return code, json.loads(out.getvalue())["files"]

    def test_a_changed_invalid_value_is_an_error(self):
        code, files = self.ci(wrap(bldg("b", usage="411")), wrap(bldg("b", usage="999")))
        self.assertEqual(code, 1)
        error = files[0]["buildings"][0]["errors"][0]
        self.assertEqual(error["code"], "invalid_code_changed")
        for text in ("usage", "999", "Building_usage.xml", "this pull request changed"):
            self.assertIn(text, error["detail"])
        self.assertEqual(files[0]["n_warnings"], 0)

    def test_the_value_base_already_had_stays_a_warning(self):
        # the building is touched (its height changes), the invalid code is not
        code, files = self.ci(wrap(bldg("b", height=10, usage="999")), wrap(bldg("b", height=11, usage="999")))
        self.assertEqual((code, files[0]["n_errors"], files[0]["n_warnings"]), (0, 0, 1))
        self.assertEqual(files[0]["buildings"][0]["warnings"][0]["code"], "invalid_code")

    def test_a_new_building_counts_every_coded_value_as_changed(self):
        code, files = self.ci(wrap(bldg("a", usage="411")), wrap(bldg("a", usage="411"), bldg("n", usage="999")))
        self.assertEqual((code, files[0]["n_errors"]), (1, 1))
        self.assertEqual(files[0]["buildings"][0]["id"], "n")

    def test_a_new_file_counts_every_coded_value_as_changed(self):
        code, files = self.ci(None, wrap(bldg("n", usage="999")))
        self.assertEqual((code, files[0]["n_errors"]), (1, 1))

    def test_the_same_value_under_another_name_or_list_is_changed(self):
        code, files = self.ci(wrap(bldg("b", usage="999", codespace="../../codelists/Other.xml")),
                              wrap(bldg("b", usage="999")))
        self.assertEqual((code, files[0]["n_errors"]), (1, 1))

    def test_a_valid_value_unresolvable_and_external_lists_are_not_checked(self):
        for usage, codespace in (("411", "../../codelists/Building_usage.xml"),
                                 ("999", "../../codelists/NoSuchList.xml"), ("999", "http://example.com/cl.xml")):
            code, files = self.ci(wrap(bldg("b", usage="461")), wrap(bldg("b", usage=usage, codespace=codespace)))
            self.assertEqual((code, files), (0, [{"file": self.REL, "buildings": [], "n_errors": 0, "n_warnings": 0}]),
                             (usage, codespace))

    def test_a_run_over_files_has_no_base_and_behaves_as_before(self):
        self.gml_path.write_bytes(wrap(bldg("b", usage="999")))
        self.assertEqual(plausibility_lint.main([str(self.gml_path)]), 0)
        res = self.lint(wrap(bldg("b", usage="999")))
        self.assertEqual((res["n_errors"], res["n_warnings"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
