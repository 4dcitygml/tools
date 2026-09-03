# CityGML Attribute Editor

A lightweight local tool specialized for viewing and editing building attributes.
It provides just one flow — "pick a building on a 2D map → see all its attributes →
fix them and open a PR" — as a front-end for submitters (review is handled by the
existing CI and GitHub).

## Launch

Just launch it from inside this clone (`--repo` is auto-detected. The only dependency
is Python 3.9+. On macOS it runs as-is with the python3 bundled in the Command Line
Tools):

```bash
python3 tools/attr_editor/app.py        # → opens http://localhost:8765
```

> **If you are not familiar with Python or git**: see the [setup guide](setup-guide.md).
> - **macOS**: a **zip version** (app.py + explanatory text; no binaries = no Gatekeeper
>   warning) is distributed on [Releases](https://github.com/4dcitygml/tools/releases).
>   Extract it and launch with the single line "drag app.py onto `python3 ` in the
>   terminal" (works with the CLT-bundled python3 3.9). If there is no clone, a
>   **first-time setup screen** opens where you just paste your fork URL, and on
>   completion it **automatically generates a double-click launcher (.command) on the
>   desktop**.
> - **Windows**: an **all-in-one zip** (app.py + bundled Python "PythonPortable" +
>   bundled MinGit as `PortableGit/`; no install needed) is distributed on
>   [Releases](https://github.com/4dcitygml/tools/releases).
>   Just extract it and double-click `start-windows.bat`. If a Git on PATH has `user.name` /
>   `user.email` configured globally, the existing Git and its credential setup take
>   priority; otherwise the adjacent `PortableGit/` is used for clone / push. Builds
>   are in `packaging/` and `.github/workflows/release-attr-editor.yml`
>   (auto-build and attach on the `attr-editor-v*` tag; bundle versions and
>   SHA-256 pins are recorded in the repository-root `THIRD_PARTY_NOTICES.md`).

- The UI loads Leaflet / Cesium from CDNs (everything except map tiles works offline).
- If you have connected to GitHub via the hub, that connection is reused to send the
  change proposal (PR) automatically. No extra `gh` install or confirmation on the
  GitHub site is normally needed. In standalone use without the hub, it falls back to
  `gh`, then to a compare URL, in that order.
- If there are multiple data packages, the largest is selected automatically
  (`--data 13101` to specify explicitly).

## Usage

1. Click a mesh frame on the map → building footprints appear (first parse takes a few
   seconds)
2. Click a building → a **3D preview at its actual position** appears under the map and
   an attribute card in the right panel
   (LOD1/LOD2 switchable; for buildings without LOD2, the LOD2 and texture buttons are
   grayed out. Shareable via `?tile=&bid=` in the URL)
3. Click a value for inline editing (code lists use dropdowns) → changes are shown in
   yellow
4. When you confirm a value, a **required source-selection field** opens in the same
   row → pick the document you checked
   ("unknown" and "not yet created" cannot be chosen as the source of a new change.
   Sending is blocked while even one attribute has no selection)
5. When you pick a source code, an item-specific note is added to the building's source set
   following the resolution rules in
   [source recording rules](https://github.com/4dcitygml/city-template/blob/main/docs/provenance-rules.md). Codes absent upstream are
   also auto-appended to `thematicSrcDesc` = R2-8.
   For attributes with duplicate names that cannot take item-specific notes, the
   building-level source is synchronized and the attribute mapping is kept in the PR
   body
6. "Send your changes" → add URLs or notes if needed → pass the **pre-submission
   check** (single target building, XML format, changed-file scope, source linkage) →
   the change proposal for the maintainer is created automatically

The PR title and body are generated automatically from the changed items, the
before/after values, and the selected sources. For example, it produces readable text like
"Checked the field survey and corrected the storeys above ground from 2 to 3", so maintainers 
who do not know XML tag names can still read the reason for the change. This generated
text is written in the **repository's working language** (`lang` in `4dcitygml.json`),
because its readers are the city's reviewers; when that differs from your screen
language, a note above the preview says so (see the hub README's "Language policy"). The sources of all
attributes are re-validated not only in the UI but also in the submission API, so a
values-only change proposal cannot be created.

## Structure and design

| File | Role |
|---|---|
| `app.py` | Local HTTP server (GML parsing, JSON API, leaf-value replacement, git/PR) |
| `index.html` | 2D map + attribute panel + editing + PR UI |
| `viewer.html` | Single-building 3D view with Cesium (`?tile=<mesh>&bid=<gml:id>`) |

- Mesh selection is intentionally explicit: the user clicks a mesh frame before its
  building footprints are loaded. Panning or zooming the map does not automatically
  switch meshes, which prevents an unintended parse of a large GML file and keeps the
  selected work target stable.
- Editing does not re-serialize the XML; it **replaces only leaf values in the original
  byte stream by string substitution**
  (preserving UTF-8 BOM, CRLF, indentation, and element order; consistent with the W6
  minimal-diff gate).
- `gml:id`, geometry, `uro:buildingID`, and `core:creationDate` are always read-only.
- PRs are **one building each** (multi-building changes are machine-rejected by CI, so
  the UI never creates them in the first place).
- The pre-submission check completes locally and inspects the post-change state without
  rewriting files. The same check is re-run at submission time; details such as full
  XSD validation are checked by CI after submission.
- Commits follow the format `attr-fix(<attribute-name>): <old> → <new>` plus a `Building: <uro:buildingID>`
  trailer (the same convention as `scripts/suggest_commit.py`;
  `git log --grep "Building: <id>"` works).
