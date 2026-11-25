#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""CityGML attribute editor — local server.

Runs a lightweight HTTP server on top of a local clone of a city repository, and
lets the browser UI (index.html / viewer.html) browse/edit building attributes
and create PRs.

- GML parsing happens server-side; the browser only gets lightweight JSON (memory cache).
- Edits never re-serialize the XML: only leaf values in the original byte stream
  are replaced via string substitution (UTF-8 BOM, CRLF, indentation and element
  order preserved byte-for-byte; consistent with the W6 minimal-diff gate).
- Change proposals are created automatically via the GitHub API after
  branch → commit (Building: trailer) → push, reusing the OAuth connection saved
  by the hub. Standalone use without the hub ends at a compare URL on GitHub's own
  screen (never the computer's GitHub CLI).

Usage:
    python app.py --repo ~/<city clone> [--port <port>] [--no-browser]   # runtime.EDITOR_PORTS
    # --repo may be omitted when placed inside the clone (tools/attr_editor/ etc.; auto-detected)

Distributed as plain .py with a bundled Python (PythonPortable) on Windows
(decision 2026-08-28 — no frozen executable).
The clone location is remembered in ~/.citygml_attr_editor.json.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import threading
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

APP_DIR = Path(__file__).resolve().parent
# The shared runtime (program/runtime.py in the bundle, tools/runtime.py in the source
# tree) holds every fact the tools share and puts the other shared modules on sys.path.
_SHARED = next((d for d in (APP_DIR, APP_DIR.parent) if (d / "runtime.py").is_file()), None)
if _SHARED is None:
    sys.exit("runtime.py is missing next to the editor: install the tools again with the one-line command")
sys.path.insert(0, str(_SHARED))
import runtime  # noqa: E402
import building_identity  # noqa: E402
import dialects  # noqa: E402
import citygml_dialect  # noqa: E402
import codelists as codelist_files  # noqa: E402
import citygml_faces  # noqa: E402
import pr_classification  # noqa: E402
import pr_markers  # noqa: E402
import pr_reason  # noqa: E402
import accounts  # noqa: E402
import git_sync  # noqa: E402


tr = runtime.translator("attr_editor")          # server-generated text in the display language
tr_lang = runtime.translator_in("attr_editor")  # repository-facing text in the city's working language

# ---- Theme pack (tools/themes/theme_loader.py via runtime; a broken theme.json is ignored) ----


_OSM = {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "maxZoom": 19,
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}


def _gsi(layer: str, ext: str = "png") -> dict:
    return {"url": f"https://cyberjapandata.gsi.go.jp/xyz/{layer}/{{z}}/{{x}}/{{y}}.{ext}", "maxZoom": 18,
            "attribution": tr("editor.gsi_attribution", "GSI Tiles")}


def basemaps(tiles: "str | None") -> dict:
    """The background layers of the screens for a city's `map.tiles`: `plan` under the editors'
    2D map, `street` and `photo` (None: no aerial layer) in the 3D view. OpenStreetMap unless the
    city declares GSI's tiles, which cover Japan only (V6: before, each page had its own copy)."""
    if tiles == "gsi":
        return {"plan": _gsi("pale"), "street": _gsi("std"), "photo": _gsi("seamlessphoto", "jpg")}
    return {"plan": _OSM, "street": _OSM, "photo": None}


def city_map_config(repo_root) -> dict:
    """The map the screens open on, from the clone's 4dcitygml.json: the background layers and,
    when the city sets them, the centre and zoom (only validated values are adopted; without
    them the screens start from a world view and fit the tiles)."""
    m = runtime.city_meta(repo_root).get("map") if repo_root is not None else None
    if not isinstance(m, dict):
        m = {}
    out: dict = {"basemaps": basemaps(m.get("tiles"))}
    c = m.get("center")
    if (isinstance(c, list) and len(c) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in c)
            and -90 <= c[0] <= 90 and -180 <= c[1] <= 180):
        out["center"] = [float(c[0]), float(c[1])]
    z = m.get("zoom")
    if isinstance(z, (int, float)) and not isinstance(z, bool) and 1 <= z <= 19:
        out["zoom"] = int(z)
    return out


# ---- Language pack (tools/i18n/i18n_loader.py via runtime; a broken catalog falls back to English) ----


norm_repo_lang = runtime.norm_lang   # the city's `lang` as a catalog language
read_repo_lang = runtime.repo_lang   # the city's working language from its 4dcitygml.json


def created_by_trailer(root: "Path | str", app: str) -> str:
    """Client-identification commit trailer (exchange contract Part B, SHOULD).

    `Created-By: <app>/<version>` - the version of the running hub (hub-v1.5.0 -> 1.5.0;
    maintainer decision 8, 2026-10-01), omitted when this runs from a tools checkout. It was
    read from the clone's install/tools-release.json, which a city repository does not carry
    (A11), so no version was ever written. Third-party clients emit their own name here; we
    emit ours for the same reason (reachability and ecosystem credit)."""
    tag = runtime.running_hub_tag() or ""
    version = tag.removeprefix("hub-v")
    return f"Created-By: {app}/{version}" if version else f"Created-By: {app}"


def sync_upstream_main(root) -> "str | None":
    """Bring the machine-managed local main in line with the upstream city repo.

    Delegates to the shared git_sync module: one cheap ls-remote round trip, then a
    fetch with git's progress-based abort (no wall-clock cut-off — a large annual
    update may take minutes and must be allowed to finish). Fail-open: offline or
    not a clone leaves the data as it is. Returns the new main commit when an
    update happened, else None.
    """
    if not runtime.git_exe():
        return None
    result = git_sync.sync_main(Path(root).resolve(), runtime.upstream_url(root),
                                accounts.store_for(accounts.login_for_clone(root)), log=print)
    return result.get("head") if result.get("state") in ("updated", "ref-moved") else None


NS = citygml_dialect.NS
crs_transformer = citygml_dialect.crs_transformer
ns_for_root = citygml_dialect.ns_for_root


# For QName reconstruction (namespace URI → conventional prefix), both CityGML versions (D-3b4).
# Used for source-note keys (R2-2)
PREFIX_BY_URI = {
    **{uri: prefix for ns in (citygml_dialect.NS, citygml_dialect.NS10) for prefix, uri in ns.items()},
    "http://www.opengis.net/citygml/relief/2.0": "dem",
    "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0": "xAL",
}

# Source recording (docs/provenance-rules.md): the convention is the dialect pack's (Pack.sources)
_THEMATIC_RE = re.compile(
    rb"<((?:\w+:)?)thematicSrcDesc\b([^>]*)>([^<]*)</(?:\w+:)?thematicSrcDesc>"
)
_CREATION_RE = re.compile(rb"<(?:\w+:)?creationDate>[^<]*</(?:\w+:)?creationDate>")


# Always read-only leaves (spec §3: gml:id, geometry, buildingID, creationDate)
READONLY_TAGS = {"creationDate"}   # and the leaf of the city's stable ID (building_identity.identity_leaf)
# Geometry tags (safety net rejected by the edit API; not shown in the UI)
GEOMETRY_TAGS = {"posList", "pos", "lowerCorner", "upperCorner"}


def _change_key(change: dict) -> str:
    """Stable edit key for an attribute leaf, shared between browser and server."""
    if change.get("key"):
        return str(change["key"])
    try:
        return f"{change['tag']}#{int(change['index'])}"
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(tr(
            "editor.err_change_ident",
            "The changed attribute could not be identified. Please reload the page",
        )) from exc


def validate_source_selections(
    changes: list[dict], source_selections: list[dict], code_table: dict[str, str],
    not_sources: frozenset = frozenset(),
) -> dict[str, dict[str, str]]:
    """Verify that every attribute value change has an explicitly selected, valid source
    (`not_sources`: codes that are never one; still shown for existing data). A city whose
    dialect records no sources, or whose data lacks the code list, records none."""
    leaf_changes = [c for c in changes if c.get("kind") != "src"]
    if not leaf_changes or not code_table:
        return {}

    selected: dict[str, dict[str, str]] = {}
    for item in source_selections:
        key = str(item.get("key") or "")
        code = str(item.get("code") or "").strip()
        if not key:
            raise ValueError(tr(
                "editor.err_source_target_ident",
                "The attribute for this source could not be identified. Please reload the page",
            ))
        if key in selected:
            raise ValueError(tr(
                "editor.err_source_duplicate",
                "Multiple sources are specified for the same attribute",
            ))
        if not code or code not in code_table:
            raise ValueError(tr(
                "editor.err_source_not_in_list",
                "The selected source is not in the code list. Please reload the page",
            ))
        if code in not_sources:
            raise ValueError(tr(
                "editor.err_source_unknown_code",
                '"Unknown" or "Not created" cannot be chosen as the source of a changed attribute',
            ))
        selected[key] = {"code": code, "label": str(code_table[code])}

    missing = [str(c.get("label") or c.get("tag") or tr("editor.attr_fallback", "attribute"))
               for c in leaf_changes if _change_key(c) not in selected]
    if missing:
        raise ValueError(tr(
            "editor.err_source_missing",
            "Choose a source for the changed attributes: {list}",
            list=tr("editor.list_sep", ", ").join(dict.fromkeys(missing)),
        ))
    return selected


def _md_text(value: object) -> str:
    """Make user input safe as a single-line string embedded in normal PR text."""
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\r", " ").replace("\n", " ").strip()
    )


def _md_cell(value: object, blank: str = "(blank)") -> str:
    return _md_text(value).replace("|", "\\|") or blank


def _pr_change_label(lang: str, change: dict) -> str:
    """Repo-language label of a change row (known tags translate; data names pass through)."""
    tag = str(change.get("tag") or "")
    return _md_text(label_in(lang, tag, str(change.get("label") or tag)))


def pr_summary(
    leaf_changes: list[dict],
    selected_sources: dict[str, dict[str, str]],
    lang: str = "en",
) -> str:
    """Human summary paragraphs of the PR body.

    Shared by build_pr_body() and the send-dialog preview endpoint, so the
    preview is rendered by the same code as the posted PR and cannot drift."""
    blank = tr_lang(lang, "pr.blank", "(blank)")
    grouped: dict[str, dict] = {}
    for change in leaf_changes:
        src = selected_sources[_change_key(change)]
        group = grouped.setdefault(
            src["code"], {"label": src["label"], "changes": []}
        )
        group["changes"].append(change)

    summary_parts: list[str] = []
    for group in grouped.values():
        src_label = _md_text(group["label"])
        grouped_changes = group["changes"]
        if len(grouped_changes) == 1:
            change = grouped_changes[0]
            summary_parts.append(tr_lang(
                lang, "pr.checked_one",
                'Checked "{source}" and corrected "{label}" from "{old}" to "{new}".',
                source=src_label, label=_pr_change_label(lang, change),
                old=_md_text(change["old"]) or blank,
                new=_md_text(change["new"]) or blank,
            ))
        else:
            lines = [tr_lang(lang, "pr.checked_many",
                             'Checked "{source}" and corrected the following:',
                             source=src_label)]
            lines.extend(
                tr_lang(lang, "pr.checked_item", '- "{label}": from "{old}" to "{new}"',
                        label=_pr_change_label(lang, c),
                        old=_md_text(c["old"]) or blank,
                        new=_md_text(c["new"]) or blank)
                for c in grouped_changes
            )
            summary_parts.append("\n".join(lines))
    return "\n\n".join(summary_parts)


def build_pr_body(
    building_id: str,
    gid: str,
    leaf_changes: list[dict],
    selected_sources: dict[str, dict[str, str]],
    reason: str = "",
    r28_codes: list[str] | None = None,
    lang: str = "en",
) -> str:
    """Build a PR body readable by non-engineers from the changed values and sources.

    Repo-facing text: all prose and known attribute labels resolve in `lang`
    (the repository's working language), independent of the editor's UI
    language. The `<!--sec:reason-->` anchor is appended outside the translated
    heading so CI reason extraction stays language-independent."""

    def lbl(change: dict) -> str:
        return _pr_change_label(lang, change)

    blank = tr_lang(lang, "pr.blank", "(blank)")

    def cell(text: str) -> str:
        return text.replace("|", "\\|") or blank

    rows = "\n".join(
        "| " + cell(lbl(c)) + " | " + _md_cell(c["old"], blank) + " | "
        + _md_cell(c["new"], blank) + " | "
        + tr_lang(lang, "pr.source_cell", "{label} ({code})",
                  label=_md_cell(selected_sources[_change_key(c)]["label"], blank),
                  code=_md_cell(selected_sources[_change_key(c)]["code"], blank))
        + " |"
        for c in leaf_changes
    )
    source_note = ""
    if r28_codes:
        codes = ", ".join(dict.fromkeys(r28_codes))
        source_note = "\n" + tr_lang(
            lang, "pr.source_added",
            "Because the selected source was not in this building's source"
            " list, source code(s) {codes} were also added.", codes=codes) + "\n"
    # The proposer's own words are the reason section (Exchange Contract A1; maintainer decision 1,
    # 2026-10-01): the generated summary above carried the anchor, so A1 could never fail. Empty, it
    # keeps CI's placeholder literal (as the texture editor does), which fails the reason check.
    supplement = _md_text(reason) or "(please fill in)"
    return (
        f"## {tr_lang(lang, 'pr.heading_summary', 'Summary of changes')}\n\n"
        + pr_summary(leaf_changes, selected_sources, lang)
        + source_note
        + f"\n\n## {tr_lang(lang, 'pr.heading_reason', 'Reason and supporting evidence')} {pr_markers.SECTION_REASON}\n\n"
        + supplement
        + f"\n\n## {tr_lang(lang, 'pr.heading_target', 'Target building')}\n\n"
        + f"- {tr_lang(lang, 'pr.label_building_id', 'Building ID')}: {_md_text(building_id)}\n"
        + f"- {tr_lang(lang, 'pr.label_internal_id', 'Internal data ID')}: `{_md_text(gid)}`\n\n"
        + f"## {tr_lang(lang, 'pr.heading_details', 'Details')}\n\n"
        + tr_lang(lang, "pr.details_columns", "| Item | Before | After | Confirmed source |")
        + "\n|---|---|---|---|\n"
        + rows
        + "\n\n<sub>"
        + tr_lang(lang, "pr.cc0_footer",
                  "The data changes in this PR are provided under the"
                  " [Data Contribution Policy]({url}) (CC0 1.0).",
                  url="../blob/main/docs/data-contribution-policy.md")
        + "</sub>\n"
    )

def _english_labels(prefix: str) -> dict:
    """Display names in English: the catalog's label.* entries, their one owner (V10: the code
    kept a second copy). Undefined tags display the tag name as-is."""
    try:
        en = json.loads((runtime.i18n_loader.CATALOG_DIR / "attr_editor" / "en.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):   # a broken catalog shows tag names, never stops the editor
        return {}
    return {k[len(prefix):]: v for k, v in en.items()
            if k.startswith(prefix) and (prefix != "label." or not k.startswith("label.risk_"))}


LABELS = _english_labels("label.")   # localname → English display name

# localname directly under Building → attribute-card group
_GROUP_BY_TOPTAG = {
    "class": "basic",
    "usage": "basic",
    "measuredHeight": "basic",
    "storeysAboveGround": "basic",
    "storeysBelowGround": "basic",
    "roofType": "basic",
    "yearOfConstruction": "basic",
    "buildingDetailAttribute": "detail",
    "buildingIDAttribute": "ident",
    "creationDate": "ident",
    "stringAttribute": "addr",
    "genericAttributeSet": "addr",
    "address": "addr",
    "bldgDisasterRiskAttribute": "risk",
    "bldgKeyValuePairAttribute": "kv",
    "bldgDataQualityAttribute": "quality",
}

_RISK_TITLES = _english_labels("label.risk_")   # risk attribute → English title


def ui_label(tag: str) -> str:
    """Attribute display name in the user's UI language (label.* catalog keys)."""
    return tr(f"label.{tag}", LABELS.get(tag, tag))


def label_in(lang: str, tag: str, fallback: str = "") -> str:
    """Attribute display name in an explicit language.

    Used with lang="en" for commit messages (history stays greppable English)
    and with the repo language for PR text. Unknown tags keep the caller's
    fallback (data-derived names are not translated)."""
    if tag in LABELS:
        return tr_lang(lang, f"label.{tag}", LABELS[tag])
    return fallback or tag


# --------------------------------------------------------------------------
# Byte spans and leaf-value replacement (same approach as reconstruct_minimal.py)
# --------------------------------------------------------------------------
building_spans = building_identity.building_spans   # gml:id -> [start, end) of each member


def _xml_escape(text: str) -> bytes:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ).encode("utf-8")


def _local(elem: ET.Element) -> str:
    tag = elem.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _qname(elem: ET.Element, prefixes: "dict | None" = None) -> str:
    """The element's QName (prefix:local) by `prefixes` (PREFIX_BY_URI and the dialect pack's).
    Used for source-note keys (R2-2)."""
    tag = elem.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        prefix = (prefixes or PREFIX_BY_URI).get(uri)
        return f"{prefix}:{local}" if prefix else local
    return tag


def _line_indent(data: bytes, pos: int) -> bytes:
    """Return the indentation (tabs/spaces) from line start up to pos (the tag-opening '<')."""
    line_start = data.rfind(b"\n", 0, pos) + 1
    ws = data[line_start:pos]
    return ws if not ws.strip() else b""


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------
class Edit:
    """An edit written into the working tree, ready to send (send_proposal)."""

    def __init__(self, describe, added: "list[str] | None" = None, extra: "dict | None" = None):
        self.describe = describe        # building ID -> (commit message, PR title, PR body)
        self.added = list(added or [])  # new files the commit adds (repository paths)
        self.extra = dict(extra or {})  # facts the screen shows after sending


_sending = threading.local()   # the account a sending thread pinned (Repo._one_account)


class Repo:
    def __init__(self, root: Path, data: str | None = None):
        self.root = root.resolve()
        # The data folders the city declares (data_dirs); inside one, its dialect pack may keep the
        # buildings in a folder of their own. No folder is guessed: an undeclared city is told to declare one.
        self._pack = runtime.city(self.root).pack
        self._prefixes = {**PREFIX_BY_URI, **dict(self._pack.namespaces)}
        packages = {
            bldg: d for d in runtime.data_dirs(self.root)
            for bldg in [dialects.buildings_dir(d, self._pack)]
            if (not data or data in d.name) and any(bldg.glob("*.gml"))
        }
        candidates = list(packages)
        if not candidates:
            raise RuntimeError(tr(
                "editor.err_no_bldg_data",
                "No building data in {root}: the city's 4dcitygml.json declares no data_dirs folder "
                "with CityGML files (or --data names none of them)",
                root=self.root,
            ))
        # With multiple packages, use the one with the most data (total .gml bytes) (--data can override)
        self.bldg_dir = max(
            candidates,
            key=lambda d: sum(p.stat().st_size for p in d.glob("*.gml")),
        )
        # The data package: the declared folder that holds them (never above the clone: /raw/ serves
        # files below it)
        self.data_root = packages[self.bldg_dir]
        if len(candidates) > 1:
            print(f"Data package candidates: {', '.join(d.name for d in packages.values())}\n  → using "
                  f"{self.data_root.name} (override with --data)")
        # City metadata (4dcitygml.json): building ID type and display-language default
        meta = runtime.city_meta(self.root)
        self._id_rule = building_identity.rule_from_config(meta)
        self._crs = runtime.city(self.root).crs           # the files' CRS when they name none
        # Repo working language (4dcitygml.json "lang"): the language of repo-facing
        # generated text (PR title/body). UI labels follow the user's language via tr().
        self._repo_lang = runtime.norm_lang(meta.get("lang"))
        self.tex_override: "Path | None" = None  # --textures: for displaying swapped-in textures
        self.codelists_dir = self.data_root / "codelists"
        self._tile_cache: dict[str, dict] = {}
        self._tile_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._codelists: dict | None = None

    # ---- File listing ----
    def tile_files(self) -> dict[str, Path]:
        return runtime.tile_files(self.bldg_dir, self._pack)

    def _tile_bounds(self, code: str, path: Path) -> "list[float] | None":
        """The tile's frame: the dialect pack's frame of a tile it names (a mesh cell), else the
        file's own Envelope (a numeric file name elsewhere is not a mesh code)."""
        pack = self._pack
        if pack.tile_frame and pack.tile_name and re.fullmatch(pack.tile_name, path.name):
            return pack.tile_frame(code) or self._envelope_bounds(path)
        return self._envelope_bounds(path)

    def _envelope_bounds(self, path: Path) -> "list[float] | None":
        """Compute [south, west, north, east] from the gml:Envelope at the top of the file.

        For tile-frame display of international data without mesh codes
        (munich/newyork etc.). Projected coordinate systems are converted to
        WGS84 via crs_transformer.
        """
        try:
            with path.open("rb") as f:
                head = f.read(8192).decode("utf-8", "replace")
        except OSError:
            return None
        low = re.search(r"<gml:lowerCorner>([-\d.eE ]+)</gml:lowerCorner>", head)
        up = re.search(r"<gml:upperCorner>([-\d.eE ]+)</gml:upperCorner>", head)
        if not (low and up):
            return None
        try:
            lo = [float(x) for x in low.group(1).split()]
            hi = [float(x) for x in up.group(1).split()]
        except ValueError:
            return None
        if len(lo) < 2 or len(hi) < 2:
            return None
        m = re.search(r'srsName="([^"]*)"', head)
        tf = crs_transformer((m.group(1) if m else "") or (self._crs or ""))
        if tf is None:   # a CRS the tools do not know: no frame, never read as lat/lon
            return None
        s, w = tf(lo[0], lo[1])
        n, e = tf(hi[0], hi[1])
        return [round(min(s, n), 7), round(min(w, e), 7),
                round(max(s, n), 7), round(max(w, e), 7)]

    def tiles_json(self) -> list[dict]:
        return [
            {
                "code": code,
                "file": p.name,
                "size": p.stat().st_size,
                "bounds": self._tile_bounds(code, p),
                "loaded": code in self._tile_cache,
            }
            for code, p in self.tile_files().items()
        ]

    def resources_json(self) -> list[dict]:
        """Primary documents (specification/ and metadata/) linked from the evidence card."""
        out = []
        for sub in ("specification", "metadata"):
            d = self.data_root / sub
            if d.is_dir():
                out += [
                    {"name": p.name, "path": f"/raw/{sub}/{p.name}"}
                    for p in sorted(d.iterdir())
                    if p.is_file() and not p.name.startswith(".")
                ]
        return out

    # ---- Code lists ----
    def list_key(self, path: Path) -> str:
        """The page's key of a code list file: its path below <data_root>/codelists (edition folders
        included), else its own path."""
        try:
            return path.relative_to(self.codelists_dir.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def codelists(self) -> dict:
        """The lists of <data_root>/codelists (the source list and the pack's default lists live there)."""
        if self._codelists is None:
            files = sorted(self.codelists_dir.resolve().rglob("*.xml")) if self.codelists_dir.is_dir() else []
            tables = {self.list_key(p): codelist_files.read(p) for p in files}
            self._codelists = {k: t for k, t in tables.items() if t is not None}
        return self._codelists

    # ---- Tile loading (parse + cache) ----
    @staticmethod
    def _stamp(path: Path) -> "tuple | None":
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _cached_tile(self, code: str) -> "dict | None":
        """A parsed tile while its file is as parsed: the hub's sync and the other editor run in
        other processes and change files without telling this one (D-3c9)."""
        cached = self._tile_cache.get(code)
        if cached is None or cached["_stamp"] != self._stamp(self.root / cached["relpath"]):
            return None
        return cached

    def tile(self, code: str) -> dict:
        cached = self._cached_tile(code)
        if cached is not None:
            return cached
        with self._locks_guard:
            lock = self._tile_locks.setdefault(code, threading.Lock())
        with lock:
            cached = self._cached_tile(code)
            if cached is None:
                path = self.tile_files().get(code)
                stamp = self._stamp(path) if path is not None else None   # before reading: a change meanwhile reparses
                cached = {**self._parse_tile(code), "_stamp": stamp}
                self._tile_cache[code] = cached
            return cached

    def _parse_tile(self, code: str) -> dict:
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        raw = path.read_bytes()
        spans = building_spans(raw)
        root = ET.fromstring(raw)
        ns = ns_for_root(root)
        # Files in projected systems (UTM, state plane, etc.) are converted to WGS84 lat/lon before returning
        tf = citygml_dialect.file_transformer(root, ns, self._crs or "")

        buildings: dict[str, dict] = {}
        order: list[str] = []
        outside: dict[str, dict] = {}   # the lists the values name that are not under <data_root>/codelists
        list_keys: dict = {}            # codeSpace -> key, found once per tile (a building names hundreds)
        for member in root.findall(f"{{{ns['core']}}}cityObjectMember"):
            bel = member.find(f"{{{ns['bldg']}}}Building")
            if bel is None:
                continue
            gid = bel.get(f"{{{ns['gml']}}}id", "")
            if not gid or gid not in spans:
                continue
            s, e = spans[gid]
            b = self._parse_building(bel, raw[s:e], ns, tf, path.parent, outside, list_keys)
            buildings[gid] = b
            order.append(gid)
        # appearance: face id -> {img, uv[, holes]}, by the face's own rings (scripts/citygml_faces.py)
        by_ring, by_poly = citygml_faces.texture_rings(root, ns)
        texmap: dict[str, dict] = {}
        for b in buildings.values():
            for f in b["lod2"]:
                tex = citygml_faces.face_texture(f, by_ring, by_poly)
                if tex is not None:
                    texmap[f["id"]] = tex

        return {
            "code": code,
            "file": path.name,
            "relpath": str(path.relative_to(self.root)),
            "bounds": self._tile_bounds(code, path),
            "order": order,
            "buildings": buildings,
            "texmap": texmap,
            "codelists": outside,
        }

    def _parse_building(self, bel: ET.Element, span: bytes, ns: dict = NS,
                        tf=None, gml_dir: "Path | None" = None, outside: "dict | None" = None,
                        list_keys: "dict | None" = None) -> dict:
        gid = bel.get(f"{{{ns['gml']}}}id", "")

        # ---- Geometry (same extraction as extract_building_preview.py) ----
        height_el = bel.find(f"{{{ns['bldg']}}}measuredHeight")
        height = 0.0
        if height_el is not None and height_el.text:
            try:
                height = float(height_el.text)
            except ValueError:
                pass

        zs = getattr(tf, "z_scale", 1.0) if tf is not None else 1.0
        pos_el = bel.find(
            f".//{{{ns['bldg']}}}lod0RoofEdge//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
        )
        if pos_el is None:
            pos_el = bel.find(
                f".//{{{ns['bldg']}}}lod1Solid//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
            )
        if pos_el is None:
            # LoD2-only data (munich/newyork etc.) uses the ground surface as the footprint
            pos_el = bel.find(
                f".//{{{ns['bldg']}}}GroundSurface//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
            )
        coords: list[list[float]] = []
        if pos_el is not None and pos_el.text:
            nums = [float(x) for x in pos_el.text.split()]
            if tf is not None:   # an unknown CRS gives no footprint (no map), never lat/lon
                coords = [
                    [round(v, 7) for v in tf(nums[i], nums[i + 1])]
                    for i in range(0, len(nums) - 2, 3)
                ]

        base = 0.0
        lod1_pos = bel.find(
            f".//{{{ns['bldg']}}}lod1Solid//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
        )
        if lod1_pos is None:
            # LoD2-only data uses the ground surface elevation as the base
            lod1_pos = bel.find(
                f".//{{{ns['bldg']}}}GroundSurface//{{{ns['gml']}}}LinearRing/{{{ns['gml']}}}posList"
            )
        if lod1_pos is not None and lod1_pos.text:
            parts = lod1_pos.text.split()
            if len(parts) >= 3:
                base = float(parts[2]) * zs

        lod1top = None
        lod1 = bel.find(f".//{{{ns['bldg']}}}lod1Solid")
        if lod1 is not None:
            pls = lod1.findall(f".//{{{ns['gml']}}}posList")
            if pls and pls[-1].text:
                parts = pls[-1].text.split()
                if len(parts) >= 3:
                    lod1top = round(float(parts[2]) * zs, 3)

        # LOD2 faces (BuildingParts included; holes kept as holes): scripts/citygml_faces.py
        lod2 = citygml_faces.lod2_faces(bel, ns, tf, zs, gid) if tf is not None else []   # unknown CRS: no faces

        # ---- Attribute tree (enumerate non-geometry leaves) ----
        # Edit address = (tag localname, occurrence index of that tag within the span).
        # Occurrence order is matched against the regex match sequence on the original bytes (values verified too).
        tag_matches: dict[str, list] = {}
        tag_ptr: dict[str, int] = {}
        id_leaf = building_identity.identity_leaf(span, self._id_rule)

        def leaf_index(tag: str, value: str) -> int | None:
            if tag not in tag_matches:
                tag_matches[tag] = list(building_identity.leaf_pattern(tag).finditer(span))
                tag_ptr[tag] = 0
            want = _xml_escape(value)
            ms = tag_matches[tag]
            i = tag_ptr[tag]
            while i < len(ms):
                if ms[i].group(2) == want:
                    tag_ptr[tag] = i + 1
                    return i
                i += 1
            return None

        codelists = self.codelists()
        items: list[dict] = []
        # Source info (resolution rule of docs/provenance-rules.md: note > upper thematicSrcDesc > unknown)
        src_upper: list[str] = []
        src_specific: dict[str, str] = {}
        sources = self._pack.sources
        src_codelist = sources.codelist if sources else ""

        defaults = dict(self._pack.default_codelists)

        def codelist_of(el: ET.Element) -> "str | None":
            """The page's key of the code list of a value: the file its codeSpace names relative to the
            .gml file, as CI reads it (codelist_files.resolve); a value without codeSpace gets the pack's
            list for the element from <data_root>/codelists. None: no list, free input."""
            cs = el.get("codeSpace")
            if not cs:
                key = defaults.get(_local(el))
                return key if key in codelists else None
            if list_keys is not None and cs in list_keys:
                return list_keys[cs]
            path = codelist_files.resolve(cs, gml_dir) if gml_dir is not None else None
            key = self.list_key(path) if path else None
            if key is not None and key not in codelists:
                table = codelist_files.read(path)
                if table is None:
                    key = None
                elif outside is not None:
                    outside[key] = table
            if list_keys is not None:
                list_keys[cs] = key
            return key

        def leaf(el: ET.Element) -> dict:
            ln = _local(el)
            idx = leaf_index(ln, (el.text or ""))
            return {
                "tag": ln,
                "label": ui_label(ln),
                "value": (el.text or "").strip(),
                "index": idx,
                "codelist": codelist_of(el),
                "uom": el.get("uom"),
                "readonly": ln in READONLY_TAGS or ln in GEOMETRY_TAGS or idx is None or (ln, idx) == id_leaf,
                "qname": _qname(el, self._prefixes),
            }

        def collect_leaves(elem: ET.Element, out: list[dict]) -> None:
            for child in elem:
                ln = _local(child)
                if not ln or ln.startswith("lod") or ln == "boundedBy":
                    continue
                if len(child) > 0:
                    collect_leaves(child, out)
                else:
                    out.append(leaf(child))

        for top in bel:
            ln = _local(top)
            if not ln or ln.startswith("lod") or ln == "boundedBy":
                continue
            if sources and ln == "genericAttributeSet" and top.get("name") == sources.set_name:
                # Source-note set (R2-1): not shown on cards; feeds the resolution rule.
                # Consume leaf indexes (keeps edit addresses of later same-tag leaves correct)
                for entry in top:
                    name = entry.get("name") or ""
                    for v in entry:
                        leaf_index(_local(v), (v.text or ""))
                        if _local(v) == "value" and not (sources.evidence_prefix and name.startswith(sources.evidence_prefix)):
                            src_specific[name] = (v.text or "").strip()
                continue
            if ln == "bldgDataQualityAttribute":
                for el in top.iter():
                    if _local(el) == "thematicSrcDesc":
                        code = (el.text or "").strip()
                        if code:
                            src_upper.append(code)
                        src_codelist = codelist_of(el) or src_codelist
            group = _GROUP_BY_TOPTAG.get(ln, "other")
            title = ""
            if ln in ("stringAttribute", "genericAttributeSet"):
                title = top.get("name") or ""
            elif ln == "bldgDisasterRiskAttribute" and len(top) > 0:
                child_ln = _local(top[0])
                title = tr(f"label.risk_{child_ln}",
                           _RISK_TITLES.get(child_ln, child_ln))
            elif ln == "address":
                title = tr("editor.addr_group", "Address")
            leaves: list[dict] = []
            if len(top) == 0:   # direct leaves (class / usage / creationDate etc.)
                leaves.append(leaf(top))
            else:
                collect_leaves(top, leaves)
            if group == "quality":
                for lf in leaves:
                    lf["readonly"] = True
            if leaves:
                items.append({"group": group, "title": title, "leaves": leaves})

        center = None
        if coords:
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            center = [
                round((min(lats) + max(lats)) / 2, 7),
                round((min(lons) + max(lons)) / 2, 7),
            ]

        return {
            "gid": gid,
            "buildingID": self.stable_id(span, gid),   # the city's rule, as the send uses (D-3b3)
            "footprint": coords,
            "center": center,
            "height": height,
            "base": base,
            "lod1top": lod1top,
            "lod2": lod2,
            "items": items,
            # None when the city's data has no source code list: no source is asked or written
            "src": {
                "upper": src_upper,
                "specific": src_specific,
                "codelist": src_codelist,
            } if sources and codelists.get(src_codelist) else None,
        }

    @property
    def _not_sources(self) -> frozenset:
        return self._pack.sources.not_sources if self._pack.sources else frozenset()

    def _source_table(self, building: dict) -> dict:
        """The source code table of a building ({} when the city's data has none)."""
        src = building.get("src")
        return (self.codelists().get(str(src["codelist"])) or {}) if src else {}

    # ---- Response shaping ----
    def tile_json(self, code: str) -> dict:
        t = self.tile(code)
        return {
            "code": t["code"],
            "file": t["file"],
            "relpath": t["relpath"],
            "bounds": t["bounds"],
            "codelists": t["codelists"],     # the lists outside <data_root>/codelists that its values name
            "buildings": [
                {k: b[k] for k in ("gid", "buildingID", "footprint", "center", "height", "items", "src")}
                for b in (t["buildings"][g] for g in t["order"])
            ],
        }

    def building_json(self, code: str, gid: str) -> dict:
        t = self.tile(code)
        b = t["buildings"].get(gid)
        if b is None:
            raise KeyError(gid)
        tex = {
            f["id"]: t["texmap"][f["id"]]
            for f in b["lod2"]
            if f["id"] in t["texmap"]
        }
        return {
            "tile": code,
            "gid": gid,
            "buildingID": b["buildingID"],
            "coords": b["footprint"],
            "center": b["center"],
            "height": b["height"],
            "base": b["base"],
            "lod1top": b["lod1top"],
            "lod2": [citygml_faces.public_face(f) for f in b["lod2"]],
            "tex": tex,
        }

    # ---- Editing (byte-preserving leaf replacement + source-note insert/update) ----
    def _edited_bytes(
        self,
        code: str,
        gid: str,
        changes: list[dict],
        source_selections: list[dict] | None = None,
    ) -> "tuple[Path, bytes, list[str]]":
        """Assemble the post-edit byte stream in memory (the file is not written yet)."""
        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        raw = path.read_bytes()
        spans = building_spans(raw)
        if gid not in spans:
            raise KeyError(gid)
        s, e = spans[gid]
        span = raw[s:e]

        # Apply leaf replacements first (note insertion can shift same-tag leaf order, so it is batched later)
        leaf_changes = [c for c in changes if c.get("kind") != "src"]
        src_changes = [c for c in changes if c.get("kind") == "src"]

        id_leaf = building_identity.identity_leaf(span, self._id_rule)
        for ch in leaf_changes:
            tag = str(ch["tag"])
            idx = int(ch["index"])
            old = str(ch["old"])
            new = str(ch["new"])
            if tag in READONLY_TAGS or tag in GEOMETRY_TAGS or (tag, idx) == id_leaf:
                raise ValueError(tr("editor.err_readonly", "{tag} is read-only", tag=tag))
            if new == old:
                continue
            matches = list(building_identity.leaf_pattern(tag).finditer(span))
            if idx >= len(matches):
                raise ValueError(tr(
                    "editor.err_leaf_not_found",
                    "{tag}[{idx}] was not found (the file may have been modified)",
                    tag=tag, idx=idx,
                ))
            m = matches[idx]
            if m.group(2) != _xml_escape(old):
                raise ValueError(tr(
                    "editor.err_leaf_mismatch",
                    "The current value of {tag}[{idx}] does not match (expected: {old})."
                    " Please reload the page",
                    tag=tag, idx=idx, old=repr(old),
                ))
            span = span[: m.start(2)] + _xml_escape(new) + span[m.end(2) :]

        r28: list[str] = []
        for ch in src_changes:
            span = self._apply_src_change(
                raw, span, str(ch["qname"]), str(ch.get("old") or ""), str(ch["new"])
            )
            span, synced = self._sync_upper_src(span, str(ch["new"]))
            if synced:
                r28.append(str(ch["new"]))

        # Attributes with duplicate QNames cannot get a per-item note, but the selected
        # code is always reflected in the building-level source list. The item mapping itself remains in the PR body.
        for selection in source_selections or []:
            code_value = str(selection.get("code") or "")
            if not code_value:
                continue
            span, synced = self._sync_upper_src(span, code_value)
            if synced:
                r28.append(code_value)

        new_raw = raw[:s] + span + raw[e:]
        # Self-check: the building span structure is not broken
        if set(building_spans(new_raw)) != set(spans):
            raise RuntimeError(tr(
                "editor.err_postedit_verify",
                "Verification after editing failed (apply aborted)",
            ))
        return path, new_raw, r28

    def apply_edits(
        self,
        code: str,
        gid: str,
        changes: list[dict],
        source_selections: list[dict] | None = None,
    ) -> dict:
        path, new_raw, r28 = self._edited_bytes(
            code, gid, changes, source_selections
        )
        try:
            ET.fromstring(new_raw)
        except ET.ParseError as exc:
            raise RuntimeError(tr(
                "editor.err_postedit_xml",
                "XML verification after editing failed (apply aborted): {exc}",
                exc=exc,
            ))
        path.write_bytes(new_raw)
        self._tile_cache.pop(code, None)  # invalidate cache (re-parse next time)
        return {
            "ok": True,
            "relpath": str(path.relative_to(self.root)),
            "applied": len(changes),
            "r28": list(dict.fromkeys(r28)),
        }

    def stable_id(self, span: bytes, gid: str) -> str:
        """A building's ID under the city's rule: shown, checked and sent alike."""
        return building_identity.stable_id(span, gid, self._id_rule)

    def _other_data_changes(self, rel: str) -> list[str]:
        """Tracked files other than `rel` changed under any declared data folder (D-3b2: one
        folder was looked at, git's quoted names were missed, a Windows `rel` never matched)."""
        folders = [d.relative_to(self.root).as_posix() for d in runtime.data_dirs(self.root)]
        if not folders:
            return []
        out = self._git("status", "--porcelain", "-z", "--untracked-files=no", "--", *folders).stdout
        target, others, entries = rel.replace("\\", "/"), [], iter(out.split("\0"))
        for entry in entries:
            if entry[:1] in ("R", "C"):
                next(entries, None)   # -z: a rename's source path follows as its own entry
            if entry and entry[3:] != target:
                others.append(entry[3:])
        return others

    def _pretest(self, body: dict) -> dict:
        """Run safe pre-submission checks that the distributed build alone can execute."""
        code = str(body.get("tile") or "")
        gid = str(body.get("gid") or "")
        changes = body.get("changes") or []
        reason = str(body.get("reason") or "").strip()
        checks: list[dict] = []

        def add(key: str, label: str, passed: bool, detail: str) -> None:
            checks.append({
                "key": key,
                "label": label,
                "status": "pass" if passed else "fail",
                "detail": detail,
            })

        # the reason is required: it is the PR's reason section, which CI checks (A1)
        add(
            "reason", tr("editor.check_reason_label", "Reason and evidence"),
            pr_reason.filled(reason),
            tr("editor.check_reason_pass", "Your reason is what reviewers read as the reason for the change")
            if pr_reason.filled(reason)
            else tr("editor.check_reason_fail",
                    "Write why you changed the value and what you checked (at least 5 characters)"),
        )
        add(
            "changes", tr("editor.check_changes_label", "Changes"), bool(changes),
            tr("editor.check_changes_pass", "There are {n} changed item(s)", n=len(changes))
            if changes else tr("editor.check_changes_fail", "There are no items to change"),
        )

        path = self.tile_files().get(code)
        if path is None:
            add("building", tr("editor.check_building_label", "Target building"), False,
                tr("editor.check_building_missing", "The target building data was not found"))
            return {"ok": True, "passed": False, "checks": checks}
        rel = str(path.relative_to(self.root))
        source_selections = [dict(s) for s in (body.get("sourceSelections") or [])]
        try:
            tile = self.tile(code)
            building = tile["buildings"].get(gid)
            if building is None:
                raise KeyError(gid)
            source_table = self._source_table(building)
            validate_source_selections(changes, source_selections, source_table, self._not_sources)
            checks.append({
                "key": "source",
                "label": tr("editor.th_source", "Source"),
                "status": "pass" if source_table else "na",
                "detail": tr(
                    "editor.check_source_pass",
                    "A document you checked is selected for every changed attribute",
                ) if source_table else tr(
                    "editor.check_source_none",
                    "This city's data records no source per attribute; your reason is the record",
                ),
            })
            _path, new_raw, r28 = self._edited_bytes(
                code, gid, changes, source_selections
            )
            spans = building_spans(new_raw)
            building_id = gid
            if gid in spans:
                building_id = self.stable_id(new_raw[slice(*spans[gid])], gid)
            add("building", tr("editor.check_building_label", "Target building"),
                gid in spans,
                tr("editor.check_building_pass",
                   "Only the single building with building ID {id} is targeted",
                   id=building_id))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            if not any(item["key"] == "source" for item in checks):
                checks.append({
                    "key": "source",
                    "label": tr("editor.th_source", "Source"),
                    "status": "fail",
                    "detail": str(exc),
                })
            add("building", tr("editor.check_building_label", "Target building"),
                False, str(exc))
            return {"ok": True, "passed": False, "checks": checks}

        try:
            ET.fromstring(new_raw)
            add("xml", tr("editor.check_xml_label", "CityGML format"), True,
                tr("editor.check_xml_pass",
                   "The file still parses correctly as XML after the change"))
        except ET.ParseError as exc:
            add("xml", tr("editor.check_xml_label", "CityGML format"), False,
                tr("editor.check_xml_fail",
                   "The changed XML cannot be parsed: {exc}", exc=exc))

        others = self._other_data_changes(rel)
        add(
            "scope", tr("editor.check_scope_label", "Changed file scope"), not others,
            tr("editor.check_scope_pass", "Only the target building's file will be sent")
            if not others
            else tr("editor.check_scope_fail",
                    "Other building data also has unorganized changes"),
        )
        if r28:
            checks.append({
                "key": "source-sync",
                "label": tr("editor.check_srcsync_label", "Source list sync"),
                "status": "pass",
                "detail": tr(
                    "editor.check_srcsync_pass",
                    "The item-specific source note and the building-level source code"
                    " are updated together",
                ),
            })
        else:
            checks.append({
                "key": "source-sync",
                "label": tr("editor.check_srcsync_label", "Source list sync"),
                "status": "na",
                "detail": tr("editor.check_srcsync_na",
                             "No source note needs to be added this time"),
            })
        passed = all(item["status"] in ("pass", "na") for item in checks)
        return {
            "ok": True,
            "passed": passed,
            "checks": checks,
            "buildingID": building_id,
            "note": tr(
                "editor.pretest_server_note",
                "Detailed schema checks and more run again in the automated checks"
                " after you send",
            ),
        }

    def pretest(self, body: dict) -> dict:
        with git_sync.clone_lock(self.root):   # one lock with the other editor and the hub's sync (D18)
            return self._pretest(body)

    def _apply_src_change(self, raw: bytes, span: bytes, qname: str, old: str, new: str) -> bytes:
        """Replace the source-note value (the pack's set of notes), or insert one per convention if absent.

        - R2-1: the set has the pack's fixed name, at most 1 per building (append to an existing one)
        - R2-5: with no set, insert immediately after core:creationDate
        - Indentation and line endings are copied from surrounding lines (consistent with byte preservation)
        """
        if not re.fullmatch(r"[A-Za-z_][\w.-]*(:[\w.-]+)?", qname):
            raise ValueError(tr(
                "editor.err_src_qname",
                "The attribute name for the source note is invalid: {qname}",
                qname=repr(qname),
            ))
        eol = b"\r\n" if b"\r\n" in span else b"\n"
        qb = qname.encode("utf-8")
        newb = _xml_escape(new)
        set_name = self._pack.sources.set_name.encode("utf-8")
        m_set = re.search(b'<gen:genericAttributeSet name="' + re.escape(set_name) + rb'"[^>]*>.*?</gen:genericAttributeSet>',
                          span, re.S)
        entry_re = re.compile(
            rb'(<gen:stringAttribute name="' + re.escape(qb) + rb'">\s*<gen:value>)'
            rb"([^<]*)(</gen:value>)",
            re.S,
        )
        if m_set:
            m = entry_re.search(span, m_set.start(), m_set.end())
            if m:
                if m.group(2) != _xml_escape(old):
                    raise ValueError(tr(
                        "editor.err_src_mismatch",
                        "The current value of the source note ({qname}) does not match"
                        " (expected: {old}). Please reload the page",
                        qname=qname, old=repr(old),
                    ))
                return span[: m.start(2)] + newb + span[m.end(2) :]
            if old:
                raise ValueError(tr(
                    "editor.err_src_not_found",
                    "The source note ({qname}) was not found"
                    " (the file may have been modified)",
                    qname=qname,
                ))
            # Insert the entry just before the existing set's closing tag (line start incl. indentation)
            set_indent = _line_indent(span, m_set.start())
            close_at = span.rfind(b"</gen:genericAttributeSet>", m_set.start(), m_set.end())
            ws_start = close_at
            while ws_start > 0 and span[ws_start - 1 : ws_start] in (b"\t", b" "):
                ws_start -= 1
            entry = (
                set_indent + b"\t<gen:stringAttribute name=\"" + qb + b"\">" + eol
                + set_indent + b"\t\t<gen:value>" + newb + b"</gen:value>" + eol
                + set_indent + b"\t</gen:stringAttribute>" + eol
            )
            return span[:ws_start] + entry + span[ws_start:]
        if old:
            raise ValueError(tr(
                "editor.err_src_not_found",
                "The source note ({qname}) was not found"
                " (the file may have been modified)",
                qname=qname,
            ))
        anchor = _CREATION_RE.search(span)
        if anchor is None:
            raise ValueError(tr(
                "editor.err_src_no_creation",
                "Adding a source note to a building without creationDate"
                " is not supported",
            ))
        indent = _line_indent(span, anchor.start())
        xmlns = (   # the generics module of the file's own version (D-3b4: always 2.0)
            b""
            if b"xmlns:gen=" in raw[: raw.find(building_identity.member_markers(raw)[0])]
            else b' xmlns:gen="' + citygml_dialect.ns_for_bytes(raw)["gen"].encode("utf-8") + b'"'
        )
        block = (
            indent + b'<gen:genericAttributeSet name="' + set_name + b'"' + xmlns + b">" + eol
            + indent + b"\t<gen:stringAttribute name=\"" + qb + b"\">" + eol
            + indent + b"\t\t<gen:value>" + newb + b"</gen:value>" + eol
            + indent + b"\t</gen:stringAttribute>" + eol
            + indent + b"</gen:genericAttributeSet>"
        )
        return span[: anchor.end()] + eol + block + span[anchor.end() :]

    def _sync_upper_src(self, span: bytes, code: str) -> "tuple[bytes, bool]":
        """R2-8: append the note code to the upper thematicSrcDesc if missing (within the same PR).

        Clones an existing thematicSrcDesc element (prefix, codeSpace) as the template.
        Does nothing for buildings lacking the container (DataQualityAttribute) itself.
        """
        ms = list(_THEMATIC_RE.finditer(span))
        if not ms:
            return span, False
        codeb = code.encode("utf-8")
        if any(m.group(3).strip() == codeb for m in ms):
            return span, False
        last = ms[-1]
        eol = b"\r\n" if b"\r\n" in span else b"\n"
        indent = _line_indent(span, last.start())
        new_el = (
            b"<" + last.group(1) + b"thematicSrcDesc" + last.group(2) + b">"
            + codeb
            + b"</" + last.group(1) + b"thematicSrcDesc>"
        )
        return span[: last.end()] + eol + indent + new_el + span[last.end() :], True

    # ---- git / PR ----
    @property
    def login(self) -> "str | None":
        """The account this editor works as for this clone (accounts.login_for_clone), read
        fresh, except in a sending thread: one account for the push and the PR (D-3c6)."""
        pinned = getattr(_sending, "login", None)
        return pinned[0] if pinned else accounts.login_for_clone(self.root)

    @contextlib.contextmanager
    def _one_account(self):
        """A send reads the clone's account once, under the send lock: a rebind mid-send cannot
        split push and PR. The pin belongs to the sending thread alone, so a second request (the
        server answers each in its own thread) neither overwrites it nor clears it (B04)."""
        _sending.login = (accounts.login_for_clone(self.root),)
        try:
            yield
        finally:
            _sending.login = None

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        net = bool(args) and args[0] in ("push", "fetch", "pull")
        r = runtime.git(self.root, *args, net=net, store=accounts.store_for(self.login) if net else None)
        if check and r.returncode != 0:
            raise RuntimeError(tr(
                "editor.err_git_failed", "git {args} failed:\n{stderr}",
                args=" ".join(args), stderr=r.stderr.strip(),
            ))
        return r

    def _fetch_upstream_main(self) -> "str | None":
        """Freshly fetched commit of the upstream city's main (None when offline).

        Runs right before a submission, so it is allowed to take as long as the
        transfer needs; only a stalled transfer is cut (git_sync's low-speed abort)."""
        return git_sync.fetch_main(self.root, runtime.upstream_url(self.root), accounts.store_for(self.login), log=print)

    def _fresh_pr_base(self, rel: str) -> "str | None":
        """Commit to cut the edit branch from, so the PR base is never stale.

        None (offline etc.) falls back to branching from the local HEAD as
        before; GitHub's out-of-date notice then remains the after-the-fact net.
        When upstream moved the target file itself, the browser edit was made
        against old bytes: sync main so a page reload serves the latest data,
        and ask the user to redo the edit.
        """
        base = self._fetch_upstream_main()
        if base is None:
            return None
        diff = self._git("diff", "--name-only", "HEAD", base, "--", rel, check=False)
        if diff.stdout.strip():
            sync_upstream_main(self.root)
            self._tile_cache.clear()
            raise ValueError(tr(
                "editor.err_upstream_advanced",
                "This building's file has been updated in the city repository."
                " Please reload the page and redo the edit on the latest data.",
            ))
        return base

    def _checkout_pr_branch(self, branch: str, base: "str | None") -> None:
        """Create the edit branch from the fetched upstream main (fallback: HEAD).

        The working tree keeps the just-applied edit (the target file is known
        to be identical between HEAD and base). If unrelated local changes make
        git refuse the switch, cut from HEAD as before (fail-open).
        """
        if base:
            r = self._git("checkout", "-b", branch, base, check=False)
            if r.returncode == 0:
                return
        self._git("checkout", "-b", branch)

    def git_status(self) -> dict:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        dirty = [
            ln for ln in self._git("status", "--porcelain").stdout.splitlines() if ln.strip()
        ]
        return {"branch": branch, "dirty": dirty}

    def submission_info(self) -> dict:
        """What a send would do, for the confirmation shown before commit / push / PR (hub-v1.2.1).

        Everything here is read from the clone and the account store; nothing is
        guessed from the computer's global Git configuration."""
        login = self.login
        return {
            "ok": True,
            "account": login,
            "identity": accounts.clone_identity(self.root),
            "origin": self._origin_nwo(),
            "originOk": self._origin_ok(login),
            "upstream": runtime.upstream_nwo(self.root),
            "branchPattern": "edit/<building id>-<date>-<time>",
            "autoPr": bool(accounts.token_for(login)),
            "blocked": self._send_blocked(login),
        }

    def _origin_ok(self, login: "str | None") -> bool:
        """origin is the account's copy on GitHub (over HTTPS): where a push as this account goes."""
        origin = self._origin_nwo()            # the seam tests use for the GitHub-facing name
        return bool(login and origin and self._origin_url().startswith("https://github.com/")
                    and origin.split("/")[0].lower() == login.lower())

    def _send_blocked(self, login: "str | None") -> str:
        """Why nothing can be sent yet ("" when a send can go): the one rule the confirmation shows
        and the send itself checks under its lock (V6, P1). A send therefore never pushes without an
        account, without the account's author in the clone, or to a copy that is not the account's."""
        if not (login and all(accounts.clone_identity(self.root).values())):
            return tr("editor.confirm_blocked", "Nothing can be sent yet. Open the hub, choose the GitHub account for this "
                      "city (Settings → GitHub account), then open this screen again.")
        if not self._origin_ok(login):
            return tr("editor.confirm_origin_bad", "Nothing can be sent yet: the upload target of this data folder is not "
                      "your copy under this account. Open the hub and choose the account again (Settings → Change); "
                      "the hub sets the upload target.")
        return ""

    def _origin_url(self) -> str:
        r = self._git("remote", "get-url", "origin", check=False)
        return r.stdout.strip() if r.returncode == 0 else ""

    def _origin_nwo(self) -> "str | None":
        return runtime.github_nwo(self._origin_url())

    def _head(self, branch: str) -> "str | None":
        """The proposal's head as GitHub names it, `<fork owner>:<branch>` (None without a fork)."""
        origin = self._origin_nwo()
        return f"{origin.split('/', 1)[0]}:{branch}" if origin else None

    def _compare_url(self, branch: str) -> "str | None":
        """GitHub screen for proposing changes upstream. Never a fork-internal-only compare."""
        head = self._head(branch)
        return (f"https://github.com/{runtime.upstream_nwo(getattr(self, 'root', None))}/compare/main...{head}?expand=1"
                if head else None)

    def _create_pr_api(self, branch: str, title: str,
                       body: str) -> "tuple[str | None, str | None]":
        """Reuse the hub's OAuth connection to create the proposal without a GitHub screen."""
        token = accounts.token_for(self.login)
        head = self._head(branch)
        if not token or not head:
            return None, None
        try:
            code, data = runtime.github_api(
                f"/repos/{runtime.upstream_nwo(self.root)}/pulls", token, method="POST",
                payload={"title": title, "head": head, "base": "main", "body": body})
        except (urllib.error.URLError, OSError) as exc:
            code, data = 0, {"message": tr("editor.api_conn_error", "Connection error: {reason}",
                                           reason=getattr(exc, "reason", exc))}
        if code == 201 and data.get("html_url"):
            return str(data["html_url"]), None
        message = runtime.api_error(code, data)
        if accounts.is_org_restriction(code, data):
            message = tr("editor.err_org_restricted",
                         "The organization that hosts this city has not approved this tool yet. "
                         "Ask an organization owner to grant it access (GitHub → Settings → "
                         "Applications → this app → Organization access). Your upload is kept; "
                         "the proposal can be opened later.")
        return None, tr(
            "editor.err_pr_auto_failed",
            "Could not automatically create the change proposal for the maintainer"
            " ({message}).",
            message=message,
        )

    def preview_pr(self, body: dict) -> dict:
        """Dry-run of the auto-generated PR summary for the send dialog.

        Rendered by the same pr_summary() as the posted PR body (repo
        language), so the preview cannot drift from the real text. Lenient by
        design: changes without a selected source are skipped here; create_pr
        still enforces full validation before anything is sent."""
        code = str(body.get("tile") or "")
        gid = str(body.get("gid") or "")
        changes = [c for c in (body.get("changes") or []) if isinstance(c, dict)]
        selections = [s for s in (body.get("sourceSelections") or [])
                      if isinstance(s, dict)]
        rlang = getattr(self, "_repo_lang", "en")
        source_table: dict = {}
        try:
            building = self.tile(code)["buildings"].get(gid) or {}
            source_table = self._source_table(building)
        except Exception:
            pass  # preview only: missing context degrades to code-only source names
        by_key = {str(s.get("key") or ""): str(s.get("code") or "").strip()
                  for s in selections}
        leaf_changes: list[dict] = []
        selected_sources: dict[str, dict[str, str]] = {}
        for c in changes:
            if c.get("kind") == "src":
                continue
            try:
                key = _change_key(c)
            except (KeyError, TypeError, ValueError):
                continue
            source_code = by_key.get(key)
            if not source_code:
                continue
            leaf_changes.append(c)
            selected_sources[key] = {
                "code": source_code,
                "label": str(source_table.get(source_code) or source_code),
            }
        summary = (pr_summary(leaf_changes, selected_sources, rlang)
                   if leaf_changes else "")
        # City display name in the UI language, for the "written in <lang>" note
        city = ""
        try:
            meta = runtime.city_meta(self.root)
            names = meta.get("name") or {}
            ui = runtime.ui_lang()
            if isinstance(names, dict):
                city = str(names.get(ui) or names.get("en") or meta.get("id") or "")
            else:
                city = str(names or meta.get("id") or "")
        except Exception:
            pass
        return {"ok": True, "summary": summary, "repoLang": rlang, "city": city}

    def send_proposal(self, kind: str, code: str, gid: str, rel: str, apply) -> dict:
        """The one way both editors send a proposal (S10; before, each had its own copy and they
        differed: the attribute editor committed without a pathspec, so it could sweep in another
        editor's staged files, D18, and its rollback kept nothing of the texture editor's cleanup).

        Under the send lock: a base fetched just now, no other change in the data folders, `apply()`
        validates and writes the edit and returns an Edit; the stable building ID from the written
        file; a branch named by the A5 prefix of `kind`; a path-limited commit with the Building
        trailer; push to the account's fork; the PR through the API (or GitHub's compare page).
        Any failure before the PR returns the clone to where it was."""
        runtime.require_min_hub(self.root)
        if not runtime.city_of(self.root):   # V1, D-3c1: a clone that names no city sends nowhere
            raise RuntimeError(tr("editor.no_city", "This copy of the data does not name its city "
                                  "(4dcitygml.json repo), so it cannot send a proposal. Open the "
                                  "city's copy from the dashboard."))
        with git_sync.clone_lock(self.root), self._one_account():   # one lock with the other editor and the hub's sync (D18)
            # The edit branch is cut from the freshly fetched upstream main, so the
            # PR base cannot be stale (the practice repo rewrites main every day).
            pr_base = self._fresh_pr_base(rel)
            # Working-tree check: no tracked file in the data folders changed other than the target
            # (the commit takes only its own paths, so other and untracked changes are tolerated)
            others = self._other_data_changes(rel)
            if others:
                raise RuntimeError(tr(
                    "editor.err_data_dirty",
                    "Other files in the city's data have changes that are not sent."
                    " Please undo or send them first:\n{list}",
                    list="\n".join(others[:10]),
                ))
            # The confirmation's rule, under the lock with the pinned account, before the edit is
            # written (D-3b1): an account, its author in the clone (else git would put the computer's
            # global name and e-mail into a public commit) and origin = the account's copy.
            blocked = self._send_blocked(self.login)
            if blocked:
                raise RuntimeError(blocked)
            edit = apply()

            # The stable building ID, from the written file (same rule as suggest_commit.py)
            raw = (self.root / rel).read_bytes()
            spans = building_spans(raw)
            building_id = gid
            if gid in spans:
                s, e = spans[gid]
                building_id = self.stable_id(raw[s:e], gid)
            safe_bid = re.sub(r"[^A-Za-z0-9._-]", "-", building_id)
            branch = f"{pr_classification.BRANCH_PREFIXES[kind][0]}{safe_bid}-{datetime.now():%Y%m%d-%H%M%S}"
            message, pr_title, pr_body = edit.describe(building_id)
            paths = [rel] + list(edit.added)

            # the way back: the branch, or the commit of a detached clone (D-3b5: "HEAD" was taken)
            prev = (self._git("symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
                    or self._git("rev-parse", "HEAD").stdout.strip())

            def rollback() -> None:
                self._git("checkout", prev, check=False)
                self._git("checkout", "--", rel, check=False)
                target_dir = (self.root / rel).parent
                # new files are untracked here: delete them by hand, but never an image main has
                kept = {Path(t) for t in self._git("ls-files", "--", *edit.added).stdout.splitlines()} if edit.added else set()
                for p in {self.root / a for a in edit.added if Path(a) not in kept}:
                    p.unlink(missing_ok=True)
                    if p.parent != target_dir and p.parent.is_dir() and not any(p.parent.iterdir()):
                        p.parent.rmdir()   # a folder the edit created (new texturing's appearance folder)
                self._tile_cache.pop(code, None)

            try:
                self._checkout_pr_branch(branch, pr_base)
                self._git("add", *paths)
                # Path-limited commit: unrelated staged changes in the index are not swept in
                self._git("commit", "-m", message, "--", *paths)
                commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            except RuntimeError:
                rollback()
                raise

            result: dict = {"ok": True, "branch": branch, "commit": commit, "buildingID": building_id, **edit.extra}
            push = self._git("push", "-u", "origin", branch, check=False)
            if push.returncode != 0:
                rollback()
                self._git("branch", "-D", branch, check=False)
                raise RuntimeError(tr(
                    "editor.err_push_failed",
                    "Could not send to GitHub. Your edits remain on this screen."
                    " Check your internet connection and try again.\n{stderr}",
                    stderr=push.stderr.strip(),
                ))
            result["pushed"] = True

            pr_url, api_note = self._create_pr_api(branch, pr_title, pr_body)
            # hub-v1.2.1: the proposal is opened with the city's account or by the person on
            # GitHub's own screen — never through the computer's GitHub CLI (another identity).
            if pr_url:
                result["prUrl"] = pr_url
            else:
                # Standalone use without the hub keeps a fallback of confirming via the GitHub screen.
                result["compareUrl"] = self._compare_url(branch)
                if api_note:
                    result["note"] = api_note

            # Return to main (the original branch)
            self._git("checkout", prev, check=False)
            self._tile_cache.pop(code, None)
            return result

    def create_pr(self, body: dict) -> dict:
        code = body["tile"]
        gid = body["gid"]
        changes = [dict(c) for c in (body.get("changes") or [])]
        source_selections = [dict(s) for s in (body.get("sourceSelections") or [])]
        reason = (body.get("reason") or "").strip()
        if not changes:
            raise ValueError(tr("editor.err_no_changes", "There are no changes"))

        path = self.tile_files().get(code)
        if path is None:
            raise FileNotFoundError(code)
        rel = str(path.relative_to(self.root))

        def apply() -> Edit:
            # inside the send lock, on the freshly fetched base: validate, then write the edit
            pretest = self._pretest(body)
            if not pretest.get("passed"):
                failed = [
                    item.get("detail") or item.get("label")
                    or tr("editor.check_needed", "Needs attention")
                    for item in pretest.get("checks", [])
                    if item.get("status") == "fail"
                ]
                raise ValueError(tr(
                    "editor.err_pretest_not_passed",
                    "The pre-submission check has not passed: {list}",
                    list=tr("editor.fail_sep", " / ").join(failed),
                ))

            # Value changes require an explicitly selected source. Validate here too,
            # using real data leaves and the code table, not relying on the browser alone.
            tile = self.tile(code)
            building = tile["buildings"].get(gid)
            if building is None:
                raise KeyError(gid)
            selected_sources = validate_source_selections(
                changes, source_selections, self._source_table(building), self._not_sources
            )
            leaf_lookup: dict[str, dict] = {}
            qname_counts: dict[str, int] = {}
            for item in building["items"]:
                for leaf in item["leaves"]:
                    if leaf.get("index") is not None:
                        leaf_lookup[f"{leaf['tag']}#{leaf['index']}"] = leaf
                    qname = str(leaf.get("qname") or "")
                    if qname:
                        qname_counts[qname] = qname_counts.get(qname, 0) + 1

            leaf_changes = [c for c in changes if c.get("kind") != "src"]
            normalized_selections: list[dict] = []
            existing_src_changes = {
                str(c.get("qname") or ""): c
                for c in changes if c.get("kind") == "src"
            }
            for change in leaf_changes:
                key = _change_key(change)
                leaf = leaf_lookup.get(key)
                if leaf is None or leaf.get("readonly"):
                    raise ValueError(tr(
                        "editor.err_not_editable",
                        "{label} cannot be edited. Please reload the page",
                        label=change.get("label") or change.get("tag")
                        or tr("editor.attr_fallback", "attribute"),
                    ))
                # Display name and edit address are fixed from the current building, never trusting user input.
                change.update(
                    key=key,
                    tag=leaf["tag"],
                    index=leaf["index"],
                    label=leaf["label"],
                )
                source = selected_sources.get(key)
                if source is None:
                    continue   # the city records no sources
                qname = str(leaf.get("qname") or "")
                normalized_selections.append(
                    {"key": key, "code": source["code"], "qname": qname}
                )

                # For uniquely addressable attributes, the server also fills in the gen "出典" note.
                # With duplicate names, apply_edits reflects it in the upper source list, and the mapping stays in the PR.
                if qname and qname_counts.get(qname) == 1:
                    original = str(building["src"]["specific"].get(qname) or "")
                    present = existing_src_changes.get(qname)
                    if present and str(present.get("new") or "") != source["code"]:
                        raise ValueError(tr(
                            "editor.err_source_conflict",
                            'The source specified for "{label}" does not match',
                            label=leaf["label"],
                        ))
                    if original != source["code"] and present is None:
                        generated = {
                            "kind": "src",
                            "qname": qname,
                            "old": original,
                            "new": source["code"],
                            # English on purpose: this label surfaces in the commit body (history contract)
                            "label": f"Source ({label_in('en', leaf['tag'], leaf['label'])})",
                        }
                        changes.append(generated)
                        existing_src_changes[qname] = generated

            # Apply the changes (preserving original bytes)
            applied = self.apply_edits(code, gid, changes, normalized_selections)

            def describe(building_id: str) -> "tuple[str, str, str]":
                # Commit message: Update attributes (<attr name>): <old> → <new>, plus a Building: trailer.
                # History stays English (greppable, language-independent contract), so labels
                # resolve as "en" here even when the repo/UI language differs.
                def commit_label(c: dict) -> str:
                    return label_in("en", str(c.get("tag") or ""),
                                    str(c.get("label") or c.get("tag") or ""))

                described_changes = leaf_changes or changes
                first = described_changes[0]
                if len(described_changes) == 1:
                    subject = (f"Update attributes ({commit_label(first)}):"
                               f" {first['old']} → {first['new']}")
                else:
                    subject = (f"Update attributes ({commit_label(first)}"
                               f" and {len(described_changes) - 1} more)")
                lines = [subject, ""]
                if len(changes) > 1:
                    lines += [f"- {commit_label(c)}: {c['old']} → {c['new']}" for c in changes]
                    lines.append("")
                if reason:
                    lines += [reason, ""]
                lines.append(f"Building: {building_id}")
                lines.append(created_by_trailer(self.root, "citygml-attr-editor"))
                message = "\n".join(lines)

                # PR title and body are repo-facing: both resolve in the repository's
                # working language (4dcitygml.json "lang"), independent of the UI
                # language. Classification stays safe in any language via the edit/
                # branch prefix; the ja/de title prefixes also match hub/CI title
                # fallbacks for manual PRs. Commit subject stays English (above).
                rlang = getattr(self, "_repo_lang", "en")
                if leaf_changes:
                    title_label = label_in(rlang, str(first.get("tag") or ""),
                                           str(first.get("label") or ""))
                    if len(leaf_changes) > 1:
                        pr_title = tr_lang(rlang, "pr.title_attr_many",
                                           "Update building info: {label} and {n} more",
                                           label=title_label, n=len(leaf_changes) - 1)
                    else:
                        pr_title = tr_lang(rlang, "pr.title_attr",
                                           "Update building info: {label}",
                                           label=title_label)
                    pr_body = build_pr_body(
                        building_id,
                        gid,
                        leaf_changes,
                        selected_sources,
                        reason,
                        applied.get("r28") or [],
                        lang=rlang,
                    )
                else:
                    # Keep the legacy operation of only maintaining source notes without changing values.
                    legacy_label = str(first.get("label") or "")
                    if len(changes) == 1:
                        pr_title = tr_lang(rlang, "pr.title_source_only",
                                           "Update attributes ({label}): {old} → {new}",
                                           label=legacy_label,
                                           old=first["old"], new=first["new"])
                    else:
                        pr_title = tr_lang(rlang, "pr.title_source_only_many",
                                           "Update attributes ({label} and {n} more)",
                                           label=legacy_label, n=len(changes) - 1)
                    blank = tr_lang(rlang, "pr.blank", "(blank)")
                    rows = "\n".join(
                        f"| {_md_cell(c['label'], blank)} | {_md_cell(c['old'], blank)} |"
                        f" {_md_cell(c['new'], blank)} |"
                        for c in changes
                    )
                    pr_body = (
                        f"## {tr_lang(rlang, 'pr.heading_source_update', 'Source information update')}"
                        f" ({_md_text(building_id)} / `{_md_text(gid)}`)\n\n"
                        + tr_lang(rlang, "pr.details_columns3", "| Item | Before | After |")
                        + f"\n|---|---|---|\n{rows}\n\n"
                        f"## {tr_lang(rlang, 'pr.heading_reason', 'Reason and supporting evidence')}"
                        f" {pr_markers.SECTION_REASON}\n\n"
                        f"{_md_text(reason) or '(please fill in)'}\n"
                    )
                return message, pr_title, pr_body

            return Edit(describe)

        return self.send_proposal("attribute", code, gid, rel, apply)

# --------------------------------------------------------------------------
# HTTP server (the hub starts the editor with --repo <clone>)
# --------------------------------------------------------------------------


class Handler(runtime.LocalHandler):
    APP_ID = "attr_editor"  # selects the language catalog (tex_editor overrides in its subclass)
    repo: "Repo | None" = None  # set at startup
    hub: "str | None" = None    # the hub's address when the hub launched this editor

    @property
    def root(self) -> "Path | None":
        return self.repo.root if self.repo is not None else None

    def page_transform(self, data: bytes) -> bytes:
        """Both editors' screens get what they share, inline like common.js / common.css: editor.css first
        in the head (the page's own style wins over it), editor.js (only definitions the pages call)."""
        data = runtime.after_head_start(data, b'<style id="citygml-editor-style">\n'
                                        + (APP_DIR / "editor.css").read_bytes() + b"</style>\n")
        return data.replace(b"</head>", b'<script id="citygml-editor">\n'
                            + (APP_DIR / "editor.js").read_bytes().replace(b"</", b"<\\/") + b"</script>\n</head>", 1)

    def _file(self, path: Path, values: "dict | None" = None) -> None:
        self.serve_file(path, {"map": city_map_config(self.root), "hub": self.hub, **(values or {})})

    # ---- Routing ----
    def do_GET(self) -> None:
        try:
            if not self.route_get(urlparse(self.path).path):
                self._error("not found", 404)
        except FileNotFoundError as e:
            self._error(tr("editor.err_tile_not_found", "Tile not found: {exc}", exc=e), 404)
        except KeyError as e:
            self._error(tr("editor.err_building_not_found",
                           "Building not found: {exc}", exc=e), 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 — returned as an API response
            self._error(f"{type(e).__name__}: {runtime.public_message(e)}", 500)

    def route_get(self, path: str) -> bool:
        """Answer one GET; False for a path this tool does not serve (the texture editor adds
        its own paths over these and shares the error answers of do_GET)."""
        if path in ("/", "/index.html"):
            self._file(APP_DIR / "index.html")
        elif path == "/viewer.html":
            self._file(APP_DIR / "viewer.html")
        elif path == "/city-logo":
            self._city_logo()
        elif path == "/api/tiles":
            self._json(
                {
                    "ok": True,
                    "tiles": self.repo.tiles_json(),
                    "resources": self.repo.resources_json(),
                }
            )
        elif path == "/api/codelists":
            self._json(self.repo.codelists())
        elif path == "/api/status":
            self._json({"ok": True, **self.repo.git_status()})
        elif path == "/api/submission":
            self._json(self.repo.submission_info())
        elif path == "/api/repo":
            # Self-report so the hub can check which city the editor on this port serves
            self._json({"ok": True, "root": str(self.repo.root),
                        "app": self.APP_ID})
        elif path.startswith("/api/tile/"):
            code = path.rsplit("/", 1)[-1]
            self._json({"ok": True, "tile": self.repo.tile_json(code)})
        elif path.startswith("/api/building/"):
            parts = path.split("/")
            if len(parts) != 5:
                self._error("bad path")
                return True
            self._json({"ok": True, "building": self.repo.building_json(parts[3], unquote(parts[4]))})
        elif path.startswith("/textures/"):
            rel = path[len("/textures/"):]
            p = None
            if self.repo.tex_override is not None:
                cand = self._safe_child(self.repo.tex_override, rel)
                if cand is not None and cand.is_file():
                    p = cand  # use the variant if present, else fall back to the original
            if p is None:
                p = self._safe_child(self.repo.bldg_dir, rel)
            self._file(p) if p else self._error("forbidden", 403)
        elif path.startswith("/raw/"):
            # /raw/specification/... /raw/metadata/... (primary evidence documents)
            p = self._safe_child(self.repo.data_root, path[len("/raw/"):])
            self._file(p) if p else self._error("forbidden", 403)
        else:
            return False
        return True

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/pretest":
                self._json(self.repo.pretest(body))
            elif path == "/api/pr-preview":
                self._json(self.repo.preview_pr(body))
            elif path == "/api/pr":
                self._json(self.repo.create_pr(body))
            else:
                self._error("not found", 404)
        except (ValueError, RuntimeError) as e:
            self._error(str(e))
        except FileNotFoundError as e:
            self._error(tr("editor.err_tile_not_found", "Tile not found: {exc}", exc=e), 404)
        except KeyError as e:
            self._error(tr("editor.err_missing_field",
                           "A required field is missing: {exc}", exc=e), 400)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {runtime.public_message(e)}", 500)


def main(handler=None, repo_class=None, app: str = "attr_editor", title: str = "CityGML attribute editor") -> None:
    """Either editor's start (the texture editor passes its handler, Repo, app id and title)."""
    handler, repo_class = handler or Handler, repo_class or Repo
    runtime.console_safe()
    accounts.scrub_git_env()   # the shell's git overrides never reach the clone
    parser = argparse.ArgumentParser(description=sys.modules[handler.__module__].__doc__)
    parser.add_argument("--repo", type=Path, help="local clone of the city repository (can be omitted when run from inside the clone)")
    parser.add_argument("--data", help="substring of data package name (to select if multiple exist; e.g., 13101)")
    parser.add_argument("--textures", type=Path,
                        help="texture replacement directory (for 3D tone variant comparison; missing images show originals)")
    parser.add_argument("--port", type=int, default=runtime.EDITOR_PORTS[app])
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--hub", help="the hub's address (the hub passes it; the screen links back to it)")
    args = parser.parse_args()

    handler.repo, handler.hub = open_repo(repo_class, args), args.hub
    runtime.serve(handler, args.port, [title + ": {url}", f"  Data: {handler.repo.bldg_dir}"],
                  open_browser=not args.no_browser)


def open_repo(repo_class, args):
    """The clone an editor serves: --repo (the hub always passes it), else the clone this runs
    inside, else the last one used. The hub clones and keeps main in line with the city; an editor
    neither clones (its own first-run setup was never reached from the hub, S12) nor syncs at start
    (it raced the hub's sync on the same clone); a send fetches the city's main itself."""
    runtime.migrate_config()   # the hub has done it already; a standalone start does it here (S18)
    accounts.migrate_tokens()  # TEMPORARY (V2): remove in v1.7.0, once every hub has started once on v1.6
    repo_root = args.repo or runtime.detect_repo() or runtime.last_clone()
    if repo_root is None:
        sys.exit("Error: no clone found. Start the editors from the hub, or pass --repo <clone>.")
    try:
        repo = repo_class(repo_root, args.data)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")
    if args.textures:
        repo.tex_override = args.textures.resolve()
        print(f"  Texture replacement: {repo.tex_override}")
    return repo


if __name__ == "__main__":
    main()
