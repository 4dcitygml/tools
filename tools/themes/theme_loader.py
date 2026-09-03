#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Theme pack loading, validation, and CSS generation (shared by hub, attribute editor, and texture editor).

Design (plan doc §5.1d):
- A theme is **declarative tokens only**. Arbitrary CSS/JS is not accepted (script
  injection is structurally impossible even with third-party themes). Values are
  validated with a per-token regular expression
- Placing `theme.json` at the city repo root means **it applies just by cloning**.
  Format: {"extends": "<builtin pack id>", "tokens": {...overrides...}} or bare tokens
- Diagnostic colors (🔴old/🔵new, diff, check results) are outside theming (no tokens defined)
- Municipality logos follow the same style: the optional `logo` field in
  `4dcitygml.json` (path relative to the root) is resolved fail-closed by
  resolve_logo() (invalid/missing -> None)

Token list (all optional):
  name       display name (shown in the UI but not used for styling)
  bg card border text muted accent   ... override each screen's :root custom properties
  header_bg header_fg                ... header band colors
  font font_head                     ... body / heading font stacks
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BUILTIN_DIR = Path(__file__).resolve().parent

_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
# Font stack: alphanumerics, spaces, commas, quotes, hyphens, CJK only (no braces, < > ;)
_FONT = re.compile(r"^[\w \-,'\"　-ヿ一-鿿]+$")
_NAME = re.compile(r"^[^<>{};]{1,80}$")

TOKEN_RULES = {
    "name": _NAME,
    "bg": _COLOR, "card": _COLOR, "border": _COLOR,
    "text": _COLOR, "muted": _COLOR, "accent": _COLOR,
    "header_bg": _COLOR, "header_fg": _COLOR,
    "font": _FONT, "font_head": _FONT,
}


class ThemeError(ValueError):
    pass


def _validate(tokens: dict) -> dict:
    """Pass only allowed tokens whose values match the rules. Invalid input is an error (not silently dropped)."""
    if not isinstance(tokens, dict):
        raise ThemeError("tokens must be specified as an object")
    out = {}
    for k, v in tokens.items():
        rule = TOKEN_RULES.get(k)
        if rule is None:
            raise ThemeError(f"unknown token: {k}")
        if not isinstance(v, str) or not rule.match(v):
            raise ThemeError(f"invalid value for token {k}: {v!r}")
        out[k] = v
    return out


def builtin_ids() -> list[str]:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.json"))


def load_builtin(theme_id: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", theme_id or ""):
        raise ThemeError(f"invalid theme id: {theme_id!r}")
    p = BUILTIN_DIR / f"{theme_id}.json"
    if not p.is_file():
        raise ThemeError(f"built-in theme not found: {theme_id} (available: {', '.join(builtin_ids())})")
    return _validate(json.loads(p.read_text(encoding="utf-8")))


def resolve_theme(repo_root: Path | None) -> dict | None:
    """Resolve the city repo's theme.json and return a token dict. None if absent (no theme)."""
    if repo_root is None:
        return None
    p = Path(repo_root) / "theme.json"
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ThemeError("theme.json must be specified as an object")
    base = load_builtin(data["extends"]) if "extends" in data else {}
    override = _validate(data.get("tokens", data if "extends" not in data else {}))
    return {**base, **override}


def theme_css(tokens: dict | None) -> str:
    """Generate injectable CSS from validated tokens (empty string if no theme)."""
    if not tokens:
        return ""
    root_map = {k: f"--{k}" for k in ("bg", "card", "border", "text", "muted", "accent")}
    lines = []
    root_vars = [f"{css}: {tokens[k]};" for k, css in root_map.items() if k in tokens]
    if root_vars:
        lines.append(":root { " + " ".join(root_vars) + " }")
    if "font" in tokens:
        lines.append(f"html, body {{ font-family: {tokens['font']}; }}")
    if "font_head" in tokens:
        lines.append(f"h1, h2, h3 {{ font-family: {tokens['font_head']}; }}")
    if "header_bg" in tokens or "header_fg" in tokens:
        decl = []
        if "header_bg" in tokens:
            decl.append(f"background: {tokens['header_bg']};")
        if "header_fg" in tokens:
            decl.append(f"color: {tokens['header_fg']};")
        lines.append("header, #header { " + " ".join(decl) + " }")
    return "\n".join(lines)


# ---- Municipality logo (optional `logo` field in 4dcitygml.json) ----
# Raster images only. SVG is disallowed even via <img>, since "opening the file
# directly" could execute scripts (the theme's "XSS structurally impossible"
# design principle applies to logos too).
LOGO_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
LOGO_MAX_BYTES = 1 << 20  # 1 MiB (plenty for a decorative header image)


def resolve_logo(repo_root: "Path | None") -> "tuple[Path, str] | None":
    """Resolve the municipality logo from the city repo's 4dcitygml.json; return (real path, Content-Type).

    Validation is fail-closed: if the path is not relative (leading /, backslash,
    `..`), the extension is not in the raster allowlist, it resolves outside the
    root, the file is missing, or it exceeds 1 MiB, return None (caller serves
    404; the screen simply keeps the logo hidden).
    """
    if repo_root is None:
        return None
    root = Path(repo_root).resolve()
    cj = root / "4dcitygml.json"
    if not cj.is_file():
        return None
    try:
        rel = json.loads(cj.read_text(encoding="utf-8")).get("logo")
    except (OSError, ValueError):
        return None
    if not isinstance(rel, str) or not rel:
        return None
    if rel.startswith("/") or "\\" in rel or ".." in rel:
        return None
    ctype = LOGO_TYPES.get(Path(rel).suffix.lower())
    if ctype is None:
        return None
    p = (root / rel).resolve()
    # Exfiltration via symlinks etc. is also rejected by the containment check after resolve
    if not p.is_relative_to(root) or not p.is_file():
        return None
    if p.stat().st_size > LOGO_MAX_BYTES:
        return None
    return p, ctype


def inject_theme(html: bytes, css: str) -> bytes:
    """Inject theme CSS into the HTML just before serving (right before </head>; at the top if absent)."""
    if not css:
        return html
    block = f'<style id="city-theme">\n{css}\n</style>\n'.encode("utf-8")
    marker = b"</head>"
    i = html.find(marker)
    if i >= 0:
        return html[:i] + block + html[i:]
    return block + html
