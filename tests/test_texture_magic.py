#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the image magic-byte check (texture_check --verify-images).

The practice-repository allowlist admits new texture files by extension only;
this check closes the remaining gap by requiring the leading bytes to match
the claimed image type. Run: python -m unittest tests.test_texture_magic
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.texture_check import image_magic_mismatches, main

JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
TIFF_LE_HEAD = b"II*\x00\x08\x00\x00\x00"
HTML_BODY = b"<!doctype html><script>alert(1)</script>"


class TestImageMagicMismatches(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, payload: bytes) -> Path:
        p = self.dir / name
        p.write_bytes(payload)
        return p

    def test_genuine_images_pass(self):
        paths = [
            self._write("wall.jpg", JPEG_HEAD + b"\x00" * 32),
            self._write("roof.jpeg", JPEG_HEAD),
            self._write("logo.png", PNG_HEAD),
            self._write("scan.tif", TIFF_LE_HEAD),
        ]
        self.assertEqual(image_magic_mismatches(paths), [])

    def test_html_smuggled_as_jpg_is_flagged(self):
        bad = self._write("evil.jpg", HTML_BODY)
        self.assertEqual(image_magic_mismatches([bad]), [str(bad)])

    def test_wrong_image_type_for_extension_is_flagged(self):
        # A real PNG is still a mismatch when named .jpg (imageURI consumers
        # and texture pipelines trust the extension).
        bad = self._write("sneaky.jpg", PNG_HEAD)
        self.assertEqual(image_magic_mismatches([bad]), [str(bad)])

    def test_empty_and_unreadable_files_are_flagged(self):
        empty = self._write("empty.jpg", b"")
        missing = self.dir / "missing.jpg"
        self.assertEqual(
            image_magic_mismatches([empty, missing]),
            [str(empty), str(missing)],
        )

    def test_unknown_extensions_are_ignored(self):
        # Non-image extensions are out of scope here (the path allowlist and
        # CODEOWNERS already exclude them); the check must not flag them.
        other = self._write("notes.txt", HTML_BODY)
        self.assertEqual(image_magic_mismatches([other]), [])

    def test_cli_exit_codes(self):
        good = self._write("ok.jpg", JPEG_HEAD)
        bad = self._write("bad.jpg", HTML_BODY)
        self.assertEqual(main(["--verify-images", str(good)]), 0)
        self.assertEqual(main(["--verify-images", str(good), str(bad)]), 1)


if __name__ == "__main__":
    unittest.main()
