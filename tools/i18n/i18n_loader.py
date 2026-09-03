#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Language pack loading and HTML injection (shared by hub, attribute editor, and texture editor).

Design (plan doc §5.1c):
- **Overlay approach**: the page's source text (strings in the source) is served
  as-is, and only keys matched by the selected language's catalog are replaced.
  If the catalog is missing/broken, fall back to the source text (never block display)
- Catalogs live at `catalogs/<app>/<lang>.json`: a flat dict of key -> display string
- Replacement targets are (1) elements with `data-i18n` / `data-i18n-title` /
  `data-i18n-placeholder` attributes, (2) `t(key, default text[, params])` calls
  in JS. params is a substitution dict for `{name}` placeholders
  (e.g. t('x.files', '{n} files', {n: 3})).
  Plural inflection is not handled (single form only; adjust wording in the catalog)
- Language resolution: env var CITYGML_LANG -> config file "lang" -> OS locale -> en

The source text is English (flipped in Wave 2). ja/de are overlays; the en catalog
is kept as the fallback base layer for de etc. and as a "same as source" reference table.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent / "catalogs"
SUPPORTED = ("en", "ja", "de")
SOURCE_LANG = "en"  # Language of the source text (flipped from ja in Wave 2)

# Keys start lowercase; camelCase is allowed after that because label.* keys
# embed CityGML localnames verbatim (e.g. label.storeysAboveGround).
_KEY = re.compile(r"^[a-z][A-Za-z0-9_.]{0,63}$")


def resolve_lang(config_lang: str | None = None) -> str:
    """Decide the display language. Priority: CITYGML_LANG > config > OS locale > en."""
    for cand in (os.environ.get("CITYGML_LANG"), config_lang):
        if cand:
            c = cand.lower()[:2]
            if c in SUPPORTED:
                return c
    for env in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(env) or ""
        if v[:2].lower() in SUPPORTED:
            return v[:2].lower()
    # Environments without env vars (e.g. Windows .bat launch) try the OS region setting
    try:
        import locale as _locale
        v = _locale.getlocale()[0] or ""
        if v[:2].lower() in SUPPORTED:
            return v[:2].lower()
    except Exception:
        pass
    return "en"


def load_catalog(app: str, lang: str) -> dict:
    """Return the catalog for the given language. Empty for the source language (keep source text). Layered on top of en."""
    if lang == SOURCE_LANG:
        return {}
    merged: dict = {}
    for layer in ("en", lang) if lang != "en" else ("en",):
        p = CATALOG_DIR / app / f"{layer}.json"
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"catalog must be specified as an object: {p}")
        for k, v in data.items():
            if not (_KEY.match(k) and isinstance(v, str)):
                raise ValueError(f"invalid catalog key/value: {p}: {k!r}")
            merged[k] = v
    return merged


def translate(app: str, key: str, default: str, lang: str | None = None,
              **params) -> str:
    """Translate server-generated (Python) text. Falls back to default if missing from/broken catalog.

    - When lang is omitted, resolve via resolve_lang() (same priority as serving)
    - {name} placeholders are substituted from params (same convention as JS t())
    - Fail-open: catalog problems never block text display
    """
    if lang is None:
        lang = resolve_lang()
    try:
        s = load_catalog(app, lang).get(key, default)
    except (ValueError, OSError, json.JSONDecodeError):
        s = default
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


# Injected apply script. Pages only need to use data-i18n attributes or t().
_APPLY_JS = """
window.t = function (key, dflt, params) {
  var v = (window.I18N || {})[key];
  var s = (v === undefined) ? dflt : v;
  if (params) {
    for (var k in params) s = s.split('{' + k + '}').join(params[k]);
  }
  return s;
};
document.addEventListener('DOMContentLoaded', function () {
  if (window.LANG) document.documentElement.lang = window.LANG;
  if (!window.I18N) return;
  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    var v = window.I18N[el.getAttribute('data-i18n')];
    if (v !== undefined) el.textContent = v;
  });
  document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
    var v = window.I18N[el.getAttribute('data-i18n-title')];
    if (v !== undefined) el.title = v;
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
    var v = window.I18N[el.getAttribute('data-i18n-placeholder')];
    if (v !== undefined) el.placeholder = v;
  });
});
"""


def i18n_script(app: str, lang: str) -> str:
    """Return the <script> block (LANG, I18N, apply function)."""
    catalog = load_catalog(app, lang)
    payload = json.dumps(catalog, ensure_ascii=False).replace("</", "<\\/")
    return (f'<script id="city-i18n">\nwindow.LANG = "{lang}";\n'
            f"window.I18N = {payload};\n{_APPLY_JS}</script>\n")


def inject_i18n(html: bytes, app: str, lang: str) -> bytes:
    """Inject the language pack into the HTML just before serving (right before </head>; at the top if absent)."""
    block = i18n_script(app, lang).encode("utf-8")
    marker = b"</head>"
    i = html.find(marker)
    if i >= 0:
        return html[:i] + block + html[i:]
    return block + html
