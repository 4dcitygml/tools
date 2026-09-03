#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the municipality logo (logo in 4dcitygml.json → GET /city-logo).

- Fail-closed checks of resolve_logo (tools/themes/theme_loader.py)
  (traversal, leading /, backslashes, extensions, outside root, size limit, absence)
- /city-logo responses of hub / attribute editor (normal delivery; unset/missing is 404)
- The headers of all 3 screens contain <img id="cityLogo"> (screens fail open and hide it)
- SVG is disallowed per the theme's "XSS structurally impossible" policy (script
  execution risk when the file is opened directly). Only raster (png/jpg/jpeg/webp) is allowed
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(app_rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / app_rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


th = _load("tools/themes/theme_loader.py", "theme_loader_logo_test")
attr = _load("tools/attr_editor/app.py", "attr_city_logo")
hub = _load("tools/hub/app.py", "hub_city_logo")

# Minimal 1x1 PNG (test fixture; the demo city repos carry no real logo)
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


def _city_repo(logo_field=None, files: "dict[str, bytes] | None" = None) -> Path:
    """Temporary clone with 4dcitygml.json (logo field optional) and actual logo files."""
    d = Path(tempfile.mkdtemp())
    meta = {"id": "test-city", "repo": "o/r"}
    if logo_field is not None:
        meta["logo"] = logo_field
    (d / "4dcitygml.json").write_text(json.dumps(meta), encoding="utf-8")
    for rel, data in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return d


class TestResolveLogo(unittest.TestCase):
    def test_valid_png_resolves_with_fixed_content_type(self):
        d = _city_repo("logo.png", {"logo.png": PNG_1PX})
        got = th.resolve_logo(d)
        self.assertIsNotNone(got)
        path, ctype = got
        self.assertEqual(path.read_bytes(), PNG_1PX)
        self.assertEqual(ctype, "image/png")

    def test_subdir_and_other_raster_extensions(self):
        cases = {"assets/logo.jpg": "image/jpeg",
                 "logo.jpeg": "image/jpeg",
                 "logo.webp": "image/webp"}
        for rel, want in cases.items():
            d = _city_repo(rel, {rel: PNG_1PX})
            got = th.resolve_logo(d)
            self.assertIsNotNone(got, rel)
            self.assertEqual(got[1], want)

    def test_svg_rejected_even_if_file_exists(self):
        # SVG can execute scripts when opened directly, so it is structurally disallowed (by design)
        d = _city_repo("logo.svg", {"logo.svg": b"<svg xmlns='...'/>"})
        self.assertIsNone(th.resolve_logo(d))

    def test_unlisted_extensions_rejected(self):
        for rel in ("logo.gif", "logo.html", "logo", "logo.PNG.txt"):
            d = _city_repo(rel, {rel: PNG_1PX})
            self.assertIsNone(th.resolve_logo(d), rel)

    def test_path_traversal_rejected(self):
        outside = Path(tempfile.mkdtemp()) / "evil.png"
        outside.write_bytes(PNG_1PX)
        for rel in ("../evil.png", "a/../../evil.png", "..", "a/..b../x.png"):
            d = _city_repo(rel)
            self.assertIsNone(th.resolve_logo(d), rel)

    def test_absolute_and_backslash_paths_rejected(self):
        d = _city_repo("logo.png", {"logo.png": PNG_1PX})
        for rel in (str(d / "logo.png"), "/etc/logo.png", "a\\b.png", "\\\\host\\share.png"):
            dd = _city_repo(rel)
            self.assertIsNone(th.resolve_logo(dd), rel)

    def test_symlink_escaping_root_rejected(self):
        # The containment check after resolve also rejects exfiltration via symlinks
        outside = Path(tempfile.mkdtemp()) / "real.png"
        outside.write_bytes(PNG_1PX)
        d = _city_repo("logo.png")
        try:
            os.symlink(outside, d / "logo.png")
        except OSError:
            self.skipTest("environment does not support symbolic links")
        self.assertIsNone(th.resolve_logo(d))

    def test_size_limit_1mib(self):
        big = b"\x89PNG" + b"\0" * (th.LOGO_MAX_BYTES + 1)
        d = _city_repo("logo.png", {"logo.png": big})
        self.assertIsNone(th.resolve_logo(d))
        just = b"\x89PNG" + b"\0" * (th.LOGO_MAX_BYTES - 4)
        d2 = _city_repo("logo.png", {"logo.png": just})
        self.assertIsNotNone(th.resolve_logo(d2))

    def test_missing_pieces_return_none(self):
        self.assertIsNone(th.resolve_logo(None))                     # clone not determined
        self.assertIsNone(th.resolve_logo(Path(tempfile.mkdtemp())))  # no 4dcitygml.json
        self.assertIsNone(th.resolve_logo(_city_repo(None)))          # no logo field
        self.assertIsNone(th.resolve_logo(_city_repo("")))            # empty string
        self.assertIsNone(th.resolve_logo(_city_repo(123)))           # wrong type
        self.assertIsNone(th.resolve_logo(_city_repo("logo.png")))    # file missing

    def test_broken_city_json_returns_none(self):
        d = Path(tempfile.mkdtemp())
        (d / "4dcitygml.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(th.resolve_logo(d))


def _respond(handler_cls, **attrs) -> dict:
    """Call Handler._city_logo() without opening a socket and collect the response (status/headers/body)."""
    h = handler_cls.__new__(handler_cls)
    rec = {"status": None, "headers": {}}
    h.send_response = lambda status: rec.__setitem__("status", status)
    h.send_header = lambda k, v: rec["headers"].__setitem__(k, v)
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    for k, v in attrs.items():
        setattr(h, k, v)
    h._city_logo()
    rec["body"] = h.wfile.getvalue()
    return rec


class TestCityLogoEndpoint(unittest.TestCase):
    def test_attr_editor_serves_logo(self):
        d = _city_repo("logo.png", {"logo.png": PNG_1PX})
        rec = _respond(attr.Handler, repo=SimpleNamespace(root=d))
        self.assertEqual(rec["status"], 200)
        self.assertEqual(rec["headers"]["Content-Type"], "image/png")
        self.assertEqual(rec["body"], PNG_1PX)

    def test_attr_editor_404_when_unset_or_missing(self):
        for repo_dir in (_city_repo(None), _city_repo("logo.png")):
            rec = _respond(attr.Handler, repo=SimpleNamespace(root=repo_dir))
            self.assertEqual(rec["status"], 404)

    def test_attr_editor_404_without_repo(self):
        rec = _respond(attr.Handler, repo=None)
        self.assertEqual(rec["status"], 404)

    def test_attr_editor_routes_city_logo(self):
        src = (REPO_ROOT / "tools" / "attr_editor" / "app.py").read_text(encoding="utf-8")
        self.assertIn('elif path == "/city-logo":', src)

    def test_hub_serves_logo(self):
        d = _city_repo("logo.webp", {"logo.webp": PNG_1PX})
        rec = _respond(hub.Handler, hub=SimpleNamespace(root=d))
        self.assertEqual(rec["status"], 200)
        self.assertEqual(rec["headers"]["Content-Type"], "image/webp")
        self.assertEqual(rec["body"], PNG_1PX)

    def test_hub_404_when_unset_missing_or_inactive(self):
        for hub_obj in (SimpleNamespace(root=_city_repo(None)),
                        SimpleNamespace(root=_city_repo("logo.png")),
                        None):
            rec = _respond(hub.Handler, hub=hub_obj)
            self.assertEqual(rec["status"], 404)

    def test_hub_routes_city_logo(self):
        src = (REPO_ROOT / "tools" / "hub" / "app.py").read_text(encoding="utf-8")
        self.assertIn('elif path == "/city-logo":', src)

    def test_tex_editor_inherits_endpoint(self):
        tex = _load("tools/tex_editor/app.py", "tex_city_logo")
        self.assertTrue(hasattr(tex.TexHandler, "_city_logo"))


class TestHeaderMarkup(unittest.TestCase):
    """All 3 screens have a logo slot at the header's top left (before h1), hidden by default (fail open)."""

    IMG = '<img id="cityLogo" src="/city-logo" alt="" style="display:none"'

    def _check(self, rel: str):
        html = (REPO_ROOT / rel).read_text(encoding="utf-8")
        self.assertIn(self.IMG, html)
        self.assertLess(html.find('id="cityLogo"'), html.find("<h1"))
        self.assertIn("#cityLogo { height: 28px; }", html)

    def test_hub_index(self):
        self._check("tools/hub/index.html")

    def test_attr_editor_index(self):
        self._check("tools/attr_editor/index.html")

    def test_tex_editor_index(self):
        self._check("tools/tex_editor/index.html")


if __name__ == "__main__":
    unittest.main()
