#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for theme packs (tools/themes/theme_loader.py).

- The three built-in packs (wa/us/de) load and conform to the schema
- theme.json resolution (extends, overrides, missing, invalid)
- Generated CSS contains nothing but token values (declarative tokens only = injection impossible)
- Injection position into HTML
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "theme_loader", REPO_ROOT / "tools" / "themes" / "theme_loader.py")
th = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(th)


class TestBuiltinPacks(unittest.TestCase):
    def test_three_packs_exist(self):
        self.assertEqual(th.builtin_ids(), ["de", "us", "wa"])

    def test_all_builtin_packs_validate(self):
        for tid in th.builtin_ids():
            tokens = th.load_builtin(tid)
            self.assertIn("accent", tokens)
            self.assertIn("name", tokens)

    def test_unknown_builtin_raises(self):
        with self.assertRaises(th.ThemeError):
            th.load_builtin("nope")

    def test_builtin_id_path_traversal_rejected(self):
        with self.assertRaises(th.ThemeError):
            th.load_builtin("../evil")


class TestResolveTheme(unittest.TestCase):
    def _repo(self, theme: dict | None):
        d = tempfile.mkdtemp()
        if theme is not None:
            (Path(d) / "theme.json").write_text(
                json.dumps(theme, ensure_ascii=False), encoding="utf-8")
        return Path(d)

    def test_no_theme_json_returns_none(self):
        self.assertIsNone(th.resolve_theme(self._repo(None)))
        self.assertIsNone(th.resolve_theme(None))

    def test_extends_builtin(self):
        tokens = th.resolve_theme(self._repo({"extends": "wa"}))
        self.assertEqual(tokens["accent"], "#165e83")

    def test_extends_with_override(self):
        tokens = th.resolve_theme(self._repo(
            {"extends": "wa", "tokens": {"accent": "#ff0000"}}))
        self.assertEqual(tokens["accent"], "#ff0000")
        self.assertEqual(tokens["bg"], "#f6f2e7")  # inherited side is kept

    def test_direct_tokens_without_extends(self):
        tokens = th.resolve_theme(self._repo({"bg": "#ffffff"}))
        self.assertEqual(tokens, {"bg": "#ffffff"})

    def test_unknown_token_rejected(self):
        with self.assertRaises(th.ThemeError):
            th.resolve_theme(self._repo({"tokens": {"evil": "x"}, "extends": "wa"}))

    def test_css_injection_in_value_rejected(self):
        # CSS/HTML smuggled into a value is rejected by the regex (guarantees declarative tokens only)
        for bad in ("#fff; } body { display:none", "url(javascript:alert(1))",
                    "#fff</style><script>alert(1)</script>"):
            with self.assertRaises(th.ThemeError):
                th.resolve_theme(self._repo({"accent": bad}))


class TestCssAndInjection(unittest.TestCase):
    def test_css_contains_only_expected_shapes(self):
        css = th.theme_css(th.load_builtin("wa"))
        self.assertIn(":root {", css)
        self.assertIn("--accent: #165e83;", css)
        self.assertIn("header, #header", css)
        self.assertNotIn("<", css)  # no HTML fragments mixed in
        self.assertNotIn("script", css)

    def test_empty_theme_produces_no_css(self):
        self.assertEqual(th.theme_css(None), "")
        self.assertEqual(th.theme_css({}), "")

    def test_inject_before_head_close(self):
        html = b"<html><head><title>t</title></head><body></body></html>"
        out = th.inject_theme(html, ":root { --bg: #fff; }")
        self.assertLess(out.find(b"city-theme"), out.find(b"</head>") + len(out))
        self.assertIn(b'<style id="city-theme">', out)
        # inserted before </head>
        self.assertLess(out.find(b"city-theme"), out.find(b"</head>"))

    def test_inject_noop_without_css(self):
        html = b"<html><head></head></html>"
        self.assertEqual(th.inject_theme(html, ""), html)


class TestAppIntegration(unittest.TestCase):
    def test_attr_editor_applies_theme_to_html(self):
        spec = importlib.util.spec_from_file_location(
            "attr_app_theme_test", REPO_ROOT / "tools" / "attr_editor" / "app.py")
        attr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(attr)
        d = Path(tempfile.mkdtemp())
        (d / "theme.json").write_text('{"extends": "us"}', encoding="utf-8")
        out = attr.themed_html(b"<html><head></head><body></body></html>", d)
        self.assertIn(b"--accent: #0039a6;", out)

    def test_attr_editor_ignores_broken_theme(self):
        spec = importlib.util.spec_from_file_location(
            "attr_app_theme_test2", REPO_ROOT / "tools" / "attr_editor" / "app.py")
        attr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(attr)
        d = Path(tempfile.mkdtemp())
        (d / "theme.json").write_text('{"extends": "wa", "tokens": {"accent": "bad"}}',
                                      encoding="utf-8")
        html = b"<html><head></head><body></body></html>"
        self.assertEqual(attr.themed_html(html, d), html)  # broken theme is ignored and served plain


if __name__ == "__main__":
    unittest.main()
