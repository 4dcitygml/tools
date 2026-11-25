#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""tools/common.js: what every screen shares, put into each by runtime.page (V6). Before, the
pages kept their own copies and they differed: one esc left quotes as they were though it was
used in attribute values (D-3a6), the texture editor's error banner was English only (D-3b7),
and `api` had three contracts."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support import TempHome, runtime

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMON = (REPO_ROOT / "tools" / "common.js").read_text(encoding="utf-8")
SCREENS = ["hub/index.html", "hub/review.html", "hub/setup.html", "hub/settings.html",
           "attr_editor/index.html", "attr_editor/viewer.html", "tex_editor/index.html"]
SHARED = ("$", "esc", "api", "toast", "ago", "makeShortcut", "showErrBanner", "postJson")

STUBS = """
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
const answers = {};
globalThis.fetch = async path => answers[path];
const reply = (status, body) => ({ok: status < 400, status, json: async () => body});
"""

CASES = """
(async () => {
  const out = {};
  out.esc = esc(`a"b'<&>`);
  answers['/x'] = reply(200, {ok: false, reason: 'not connected', prs: []});
  out.reason = (await api('/x')).reason;
  answers['/y'] = reply(400, {ok: false, error: 'refused', status: {step: 1}});
  try { await api('/y', {}); } catch (e) { out.error = e.message; out.data = e.data.status; }
  answers['/z'] = {ok: false, status: 502, json: async () => { throw new Error('not JSON'); }};
  try { await api('/z'); } catch (e) { out.http = e.message; out.noData = e.data; }
  console.log(JSON.stringify(out));
})();
"""


class TestTheHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("Node.js is required")
        run = subprocess.run([node, "-e", STUBS + COMMON + CASES], capture_output=True, text=True, check=True)
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_esc_is_safe_in_attribute_values(self):
        self.assertEqual(self.out["esc"], "a&quot;b&#39;&lt;&amp;&gt;")

    def test_api_has_one_contract(self):
        self.assertEqual(self.out["reason"], "not connected")   # a state the screen shows, not an error
        self.assertEqual((self.out["error"], self.out["data"]), ("refused", {"step": 1}))   # the answer travels along
        self.assertEqual((self.out["http"], self.out["noData"]), ("HTTP 502", None))


class TestEveryScreenHasThem(unittest.TestCase):
    def test_no_screen_keeps_its_own_copy(self):
        for rel in SCREENS:
            src = (REPO_ROOT / "tools" / rel).read_text(encoding="utf-8")
            for name in SHARED:
                self.assertIsNone(re.search(rf"^\s*(?:async )?function {re.escape(name)}\(|^\s*const {re.escape(name)} =", src, re.M),
                                  f"{rel} defines {name}")

    def test_the_page_carries_them_after_the_language_pack(self):
        with TempHome():
            html = runtime.page(b"<html><head></head><body></body></html>", "tex_editor").decode("utf-8")
        self.assertIn("function esc(", html)
        self.assertLess(html.index("window.t"), html.index('id="citygml-common"'))

    def test_the_banner_speaks_every_language(self):
        # D-3b7: the texture editor's banner was "Error: ... (click to close)" in every language
        with TempHome(lang="de"):
            html = runtime.page(b"<html><head></head><body></body></html>", "tex_editor").decode("utf-8")
        self.assertIn("zum Schließen klicken", html)


class TestTheEditorsShare(unittest.TestCase):
    """T0 (#60): the map, tiles, footprints and page address of both editors live in
    attr_editor/editor.js, put into both editors' screens; no page keeps its own copy."""

    EDITOR_SHARED = ("editorMap", "drawTiles", "drawFootprints", "highlightFootprint", "updateUrl", "openFromUrl")

    def test_no_editor_keeps_its_own_copy(self):
        for rel in ("attr_editor/index.html", "tex_editor/index.html"):
            src = (REPO_ROOT / "tools" / rel).read_text(encoding="utf-8")
            for name in self.EDITOR_SHARED:
                self.assertNotIn(f"function {name}(", src, rel)
            self.assertNotIn("L.map('map'", src, rel)

    def test_both_editors_serve_it(self):
        from tests.support import load_app
        attr = load_app("attr_editor_js", "tools/attr_editor/app.py")
        tex = load_app("tex_editor_js", "tools/tex_editor/app.py")
        for handler in (attr.Handler, tex.TexHandler):
            page = handler.page_transform(object.__new__(handler), b"<html><head></head><body></body></html>").decode()
            self.assertIn('<script id="citygml-editor">', page)
            self.assertIn("function openFromUrl(", page)

    def test_the_bundle_carries_it(self):
        from scripts import build_bundle
        names = {arc for _, arc, _ in build_bundle.manifest(REPO_ROOT, "macos")}
        self.assertIn(f"{build_bundle.LIB}/attr_editor/editor.js", names)


class TestTheSharedLook(unittest.TestCase):
    """#66: one base style (tools/common.css) first in every screen's head, the editors' shared layout
    (attr_editor/editor.css) after it, then the page's own differences, then a city's theme."""

    PALETTE = ("--bg:", "--card:", "--border:", "--text:", "--muted:", "--accent:")

    def test_the_order_base_editor_page_theme(self):
        from tests.support import load_app
        attr = load_app("attr_editor_css", "tools/attr_editor/app.py")
        src = b"<html><head><style>/*page*/</style></head><body></body></html>"
        page = attr.Handler.page_transform(object.__new__(attr.Handler), src)
        with TempHome() as home:
            (home / "theme.json").write_text(json.dumps({"extends": "wa"}), encoding="utf-8")
            html = runtime.page(page, "attr_editor", home).decode("utf-8")
        order = [html.index(m) for m in ('id="citygml-common-style"', 'id="citygml-editor-style"', "/*page*/", 'id="city-theme"')]
        self.assertEqual(order, sorted(order))
        self.assertTrue(re.search(r'<head>\s*<style id="citygml-common-style">', html))

    def test_the_base_only_sets_defaults(self):
        # every base selector is inside :where(), so any rule of a page wins without !important
        css = re.sub(r"/\*.*?\*/", "", (REPO_ROOT / "tools" / "common.css").read_text(encoding="utf-8"), flags=re.S)
        css = re.sub(r"@keyframes[^{]*\{.*?\}\s*\}", "", css, flags=re.S)
        for sel in (s.strip() for s in re.findall(r"([^{}]+)\{[^{}]*\}", css)):
            self.assertTrue(sel == ":root" or sel.startswith(":where("), sel)

    def test_one_palette(self):
        for rel in SCREENS:
            src = (REPO_ROOT / "tools" / rel).read_text(encoding="utf-8")
            outside_media = re.sub(r"@media[^{]*\{.*?\}\s*\}", "", src, flags=re.S)   # setup's dark mode
            for var in self.PALETTE:
                self.assertNotIn(var, outside_media, f"{rel} redefines {var} (common.css owns the palette)")

    def test_the_editors_keep_no_copy_of_their_shared_layout(self):
        shared = (REPO_ROOT / "tools" / "attr_editor" / "editor.css").read_text(encoding="utf-8")
        selectors = {s.strip() for s in re.findall(r"^([^{}/*\n][^{}]*)\{", shared, flags=re.M)}
        for rel in ("attr_editor/index.html", "tex_editor/index.html"):
            style = re.search(r"<style>(.*?)</style>", (REPO_ROOT / "tools" / rel).read_text(encoding="utf-8"), re.S)[1]
            own = {s.strip() for s in re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", style, flags=re.S))}
            self.assertEqual(own & selectors, set(), rel)

    def test_the_bundle_carries_both(self):
        from scripts import build_bundle
        names = {arc for _, arc, _ in build_bundle.manifest(REPO_ROOT, "macos")}
        self.assertIn(f"{build_bundle.LIB}/common.css", names)
        self.assertIn(f"{build_bundle.LIB}/attr_editor/editor.css", names)

class TestTheBackground(unittest.TestCase):
    """V6: the background layers come from the server, one table for the editors' 2D map and the
    3D view (before: three copies in the pages, the 3D view's GSI credit worded differently)."""

    def test_the_city_declares_its_layers(self):
        from tests.support import load_app
        attr = load_app("attr_common_js", "tools/attr_editor/app.py")
        osm, gsi = attr.basemaps(None), attr.basemaps("gsi")
        self.assertIsNone(osm["photo"])
        self.assertEqual(osm["plan"], osm["street"])
        self.assertIn("openstreetmap.org", osm["plan"]["url"])
        self.assertEqual([gsi[k]["url"].split("/")[4] for k in ("plan", "street", "photo")], ["pale", "std", "seamlessphoto"])
        self.assertEqual(attr.city_map_config(None), {"basemaps": osm})   # no clone: a world view on OpenStreetMap


if __name__ == "__main__":
    unittest.main()
