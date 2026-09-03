#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the municipality scope extractor."""
from __future__ import annotations

import unittest

from scripts.extract_municipality import extract
from scripts.reconstruct_minimal import building_spans


def _member(gml_id: str, municipality: str | None, value: int) -> bytes:
    city = b"" if municipality is None else (
        f"<uro:city>{municipality}</uro:city>".encode()
    )
    return (
        b'<core:cityObjectMember><bldg:Building gml:id="'
        + gml_id.encode()
        + b'">'
        + city
        + f"<bldg:storeysAboveGround>{value}</bldg:storeysAboveGround>".encode()
        + b"</bldg:Building></core:cityObjectMember>"
    )


def _gml(*members: bytes) -> bytes:
    return (
        b'<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
        b'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
        b'xmlns:gml="http://www.opengis.net/gml" xmlns:uro="urn:uro" '
        b'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">\n  '
        + b"\n  ".join(members)
        + b"\n</core:CityModel>\n"
    )


class TestExtractMunicipality(unittest.TestCase):
    def test_removes_only_other_municipality_and_preserves_target_member(self):
        kept = _member("keep", "13101", 1)
        removed = _member("remove", "13105", 2)
        source = _gml(kept, removed)
        output, report = extract(source, "13101")
        self.assertEqual(set(building_spans(output)), {"keep"})
        start, end = building_spans(output)["keep"]
        self.assertEqual(output[start:end], kept)
        self.assertEqual(report["retained_buildings"], 1)
        self.assertEqual(report["removed_buildings"], 1)

    def test_same_input_is_byte_deterministic(self):
        source = _gml(
            _member("keep", "13101", 1),
            _member("remove", "13105", 2),
        )
        first, first_report = extract(source, "13101")
        second, second_report = extract(source, "13101")
        self.assertEqual(first, second)
        self.assertEqual(first_report["output_sha256"], second_report["output_sha256"])

    def test_missing_municipality_fails_closed(self):
        source = _gml(_member("unknown", None, 1))
        with self.assertRaisesRegex(ValueError, "uniquely determine"):
            extract(source, "13101")

    def test_invalid_target_code_fails(self):
        source = _gml(_member("keep", "13101", 1))
        with self.assertRaisesRegex(ValueError, "5 digits"):
            extract(source, "1310")

    def test_new_dangling_appearance_target_fails(self):
        appearance = (
            b'<core:appearanceMember><app:Appearance gml:id="appearance">'
            b'<app:surfaceDataMember><app:ParameterizedTexture gml:id="texture">'
            b'<app:target uri="#remove"/>'
            b'</app:ParameterizedTexture></app:surfaceDataMember>'
            b'</app:Appearance></core:appearanceMember>'
        )
        source = _gml(
            _member("keep", "13101", 1),
            _member("remove", "13105", 2),
            appearance,
        )
        with self.assertRaisesRegex(RuntimeError, "missing local references"):
            extract(source, "13101")


if __name__ == "__main__":
    unittest.main()
