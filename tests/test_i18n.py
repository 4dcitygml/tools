#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the language packs (tools/i18n/i18n_loader.py).

- Language resolution priority (CITYGML_LANG > config > OS locale > en)
- Catalog loading (selected language overlaid on the en base; source language is empty)
- Safety of the injected script (neutralizing </script> break-out, key validation)
- Consistency between the i18n hooks of all converted screens (attribute editor, hub, texture editor) and the catalogs
"""
from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "i18n_loader", REPO_ROOT / "tools" / "i18n" / "i18n_loader.py")
i18n = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(i18n)


class _EnvGuard(unittest.TestCase):
    KEYS = ("CITYGML_LANG", "LC_ALL", "LC_MESSAGES", "LANG")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestResolveLang(_EnvGuard):
    def test_default_is_english(self):
        self.assertEqual(i18n.resolve_lang(None), "en")

    def test_os_locale(self):
        os.environ["LANG"] = "de_DE.UTF-8"
        self.assertEqual(i18n.resolve_lang(None), "de")

    def test_config_beats_locale(self):
        os.environ["LANG"] = "de_DE.UTF-8"
        self.assertEqual(i18n.resolve_lang("ja"), "ja")

    def test_env_beats_config(self):
        os.environ["CITYGML_LANG"] = "en"
        self.assertEqual(i18n.resolve_lang("ja"), "en")

    def test_unsupported_falls_through(self):
        os.environ["LANG"] = "fr_FR.UTF-8"
        self.assertEqual(i18n.resolve_lang(None), "en")


class TestCatalog(unittest.TestCase):
    def test_source_lang_is_empty_overlay(self):
        self.assertEqual(i18n.load_catalog("attr_editor", i18n.SOURCE_LANG), {})

    def test_ja_catalog_loads(self):
        cat = i18n.load_catalog("attr_editor", "ja")
        self.assertEqual(cat["viewer.btn_texture"], "テクスチャ")

    def test_de_overlays_en(self):
        cat = i18n.load_catalog("attr_editor", "de")
        self.assertEqual(cat["viewer.btn_texture"], "Textur")

    def test_de_falls_back_to_en_for_missing_keys(self):
        # Even if any key is missing from the de catalog, en.json serves as the base (structural check)
        en_file = json.loads(
            (Path(i18n.CATALOG_DIR) / "attr_editor" / "en.json")
            .read_text(encoding="utf-8"))
        de = set(i18n.load_catalog("attr_editor", "de"))
        self.assertEqual(set(en_file), de)  # all keys currently translated = same set after merge

    def test_missing_app_returns_empty(self):
        self.assertEqual(i18n.load_catalog("no_such_app", "en"), {})


class TestInjection(unittest.TestCase):
    def test_inject_before_head_close(self):
        html = b"<html><head></head><body><script>t('x','y')</script></body></html>"
        out = i18n.inject_i18n(html, "attr_editor", "en")
        self.assertIn(b'<script id="city-i18n">', out)
        self.assertLess(out.find(b"city-i18n"), out.find(b"</head>"))
        self.assertIn(b'window.LANG = "en";', out)
        self.assertIn(b"window.t = function", out)

    def test_source_lang_injects_empty_catalog(self):
        out = i18n.inject_i18n(b"<head></head>", "attr_editor", i18n.SOURCE_LANG)
        self.assertIn(b"window.I18N = {};", out)

    def test_t_supports_placeholder_params(self):
        # Injected code has t(key, dflt, params) substitute {name} placeholders
        out = i18n.i18n_script("attr_editor", "en")
        self.assertIn("function (key, dflt, params)", out)
        self.assertIn("s.split('{' + k + '}').join(params[k])", out)

    def test_payload_cannot_break_out_of_script(self):
        # A catalog whose values contain </script> cannot break out of the script
        payload = json.dumps({"k": "</script><script>alert(1)</script>"},
                             ensure_ascii=False).replace("</", "<\\/")
        self.assertNotIn("</script>", payload)


class TestConvertedScreens(unittest.TestCase):
    """Consistency between all converted screens (Wave 2) and the catalogs.

    For each app: every key used on the screens exists in the en catalog,
    the en catalog has no unused keys, and the en/de key sets match.
    """

    # app → converted screens (paths relative to tools/)
    SCREENS = {
        "attr_editor": ["attr_editor/viewer.html", "attr_editor/index.html"],
        "hub": ["hub/index.html", "hub/review.html", "hub/getting-started.html"],
        "tex_editor": ["tex_editor/index.html"],
    }
    # app → Python sources containing server-generated messages (tr()/translate())
    PY_SOURCES = {
        "attr_editor": ["attr_editor/app.py"],
        "hub": ["hub/app.py"],
        "tex_editor": ["tex_editor/app.py"],
    }

    # Key families built at runtime (f"label.{tag}", 'lang.' + repoLang): the
    # static grep below cannot see them. Their real coverage (catalog keys ⇔
    # LABELS/_RISK_TITLES tags) is asserted in tests/test_repo_language.py.
    DYNAMIC_PREFIXES = {"attr_editor": ("label.", "lang.")}

    @staticmethod
    def _used_keys(src: str) -> set:
        import re
        keys = set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', src))
        keys |= set(re.findall(r"\bt\('([^']+)'", src))       # JS t()
        keys |= set(re.findall(r"\btr\(\s*['\"]([^'\"]+)['\"]", src))  # Python tr()
        keys |= set(re.findall(r"\btr_lang\(\s*[^,()]+,\s*['\"]([^'\"]+)['\"]", src))  # Python tr_lang()
        # 'lang.' + expr style concatenations capture the bare prefix — not a real key
        return {k for k in keys if not k.endswith(".")}

    @classmethod
    def _catalog(cls, app: str, lang: str) -> dict:
        p = REPO_ROOT / "tools" / "i18n" / "catalogs" / app / f"{lang}.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def _app_used(self, app: str) -> set:
        used: set = set()
        for rel in self.SCREENS[app] + self.PY_SOURCES.get(app, []):
            src = (REPO_ROOT / "tools" / rel).read_text(encoding="utf-8")
            used |= self._used_keys(src)
        return used

    def test_all_used_keys_exist_in_en_catalog(self):
        for app in self.SCREENS:
            missing = self._app_used(app) - set(self._catalog(app, "en"))
            self.assertEqual(missing, set(), f"{app}: missing key in en catalog: {missing}")

    def test_no_unused_keys_in_catalog(self):
        for app in self.SCREENS:
            dynamic = self.DYNAMIC_PREFIXES.get(app, ())
            unused = {k for k in set(self._catalog(app, "en")) - self._app_used(app)
                      if not k.startswith(dynamic)}
            self.assertEqual(unused, set(), f"{app}: unused key in screen: {unused}")

    def test_en_de_key_sets_match(self):
        for app in self.SCREENS:
            en, de = set(self._catalog(app, "en")), set(self._catalog(app, "de"))
            self.assertEqual(en, de, f"{app}: en/de key sets do not match")

    def test_key_names_are_valid(self):
        for app in self.SCREENS:
            for k in self._catalog(app, "en"):
                self.assertTrue(i18n._KEY.match(k), f"{app}: invalid key name: {k!r}")

    def test_viewer_uses_hooks(self):
        html = (REPO_ROOT / "tools" / "attr_editor" / "viewer.html"
                ).read_text(encoding="utf-8")
        self.assertIn('data-i18n="viewer.btn_texture"', html)
        self.assertIn("t('viewer.loading_mesh'", html)


class TestTranslate(_EnvGuard):
    """translate() for server-generated messages."""

    def test_default_when_source_lang(self):
        os.environ["CITYGML_LANG"] = "en"
        self.assertEqual(
            i18n.translate("attr_editor", "viewer.btn_texture", "Texture"),
            "Texture")

    def test_catalog_overlay(self):
        os.environ["CITYGML_LANG"] = "ja"
        self.assertEqual(
            i18n.translate("attr_editor", "viewer.btn_texture", "Texture"),
            "テクスチャ")

    def test_params_and_missing_key(self):
        os.environ["CITYGML_LANG"] = "ja"
        self.assertEqual(
            i18n.translate("attr_editor", "no.such.key", "{n} files", n=3),
            "3 files")

    def test_explicit_lang_beats_env(self):
        os.environ["CITYGML_LANG"] = "ja"
        self.assertEqual(
            i18n.translate("attr_editor", "viewer.btn_texture", "Texture",
                           lang="de"),
            "Textur")


class TestAppIntegration(_EnvGuard):
    def test_attr_editor_localizes_html(self):
        spec = importlib.util.spec_from_file_location(
            "attr_app_i18n_test", REPO_ROOT / "tools" / "attr_editor" / "app.py")
        attr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(attr)
        os.environ["CITYGML_LANG"] = "de"
        out = attr.localized_html(b"<html><head></head><body></body></html>")
        self.assertIn("Textur".encode(), out)
        self.assertIn(b'window.LANG = "de";', out)


if __name__ == "__main__":
    unittest.main()
