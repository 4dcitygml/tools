<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Client runtime contract (runtime-v1)

The [PR Exchange Contract](exchange-contract.md) governs what a submission
must look like. This document governs the other side: how the tools that run
on a contributor's computer are installed, started, kept up to date and kept
apart per city. It is the **internal contract of 4dcitygml's own clients** — the hub, the
editors and the launchers — and of any future version of them built from
shared modules. Third-party tools are not expected to follow it: the shared
core offered to them covers the contract-facing and GitHub-facing parts only
(submission format, CI output, GitHub API), not this runtime, so they keep
their own settings, clones and ports and nothing here can collide with them.

Rules of change: additive only within runtime-v1 (new keys, new endpoints,
new files). Renaming or removing anything here is runtime-v2 and needs a
migration in the launcher.

## 1. One process per clone

- A tool process is bound to exactly one city clone for its whole life. It
  reads the city (theme, logo, language, data, sync target, fork target) from
  that clone's `4dcitygml.json`, never from the environment after start-up.
- Several cities are several processes of the same installed code, on
  different ports. A process never serves two clones.
- The city identity is `owner/repo` in lower case (the `repo` field of
  `4dcitygml.json`, normalised from `owner/repo`, `https://github.com/…` or
  `git@github.com:…` forms).

## 2. How a process is started

| Channel | Meaning |
|---|---|
| `CITYGML_UPSTREAM=<owner/repo>` | Which city's clone to use. Selects the clone only; a clone whose `4dcitygml.json` names another city is not adopted (the process opens its setup screen instead). |
| `CITYGML_HUB_TAG=<hub-vX.Y.Z>` | The version being started (the launcher passes the folder name). Used for the update banner and `min_hub`. |
| `--repo <path>` | Explicit clone (developers). Wins over the environment. |
| `--port <n>` | Preferred port. If taken by another city's process, the next free port (+2 steps) is used. |
| `--no-browser` | Do not open the browser. |
| `CITYGML_LANG` | UI language of the process (repository-facing text follows the clone's `lang`). |

Test hooks (never set by launchers in normal use): `CITYGML_TOOLS_DIR`,
`CITYGML_RELEASES_JSON`, `CITYGML_RELEASES_API`, `CITYGML_ASSET_FILE`,
`CITYGML_NO_EXEC`, `CITYGML_INSTALL_TAG`; `CITYGML_HUB_NO_GH`,
`CITYGML_OAUTH_CLIENT_ID` (development).

## 3. Files on the computer

```
~/Documents/citygml-tools/                     (Windows: %USERPROFILE%\Documents\citygml-tools\)
  citygml.sh | citygml.ps1                     per-user launcher (copied from the bundle / install-v1)
  <City name>.app | <City name>.lnk            desktop launcher, embeds the city id only
  citygml-hub/<hub-vX.Y.Z>/program/hub.py      one folder per installed version; the newest is started
  citygml-hub/citygml-hub-stage.*              transient, while a download is verified
~/Documents/CityGML Data (<repo name>)/        the city clone (one per city)
~/.citygml_attr_editor.json                    shared settings (below)
~/.citygml_auth.json, ~/.citygml_git_credentials   sign-in token / bundled-git credentials
```

Shared settings file (`~/.citygml_attr_editor.json`):

```json
{
  "repo": "/…/CityGML Data (sample-tokyo-station)",
  "lang": "ja",
  "cities": {
    "4dcitygml/sample-tokyo-station": {"repo": "/…/CityGML Data (sample-tokyo-station)", "last_used": "2026-09-06T…"}
  }
}
```

- `cities[<owner/repo>].repo` is the clone of that city; `repo` is the last
  used clone (kept for tools that know only one clone); `lang` is the UI language.
- **Writers merge; they never replace the file.** Keys they do not own must survive.
  Writes are atomic (temporary file in the same folder, then rename), so a
  concurrent writer never sees a torn file. Two writers racing on the *same*
  key still last-write-wins; keep writes rare (after setup, after a version change).
- A clone is used only after its `4dcitygml.json` confirms the city.

## 4. Ports and discovery

- Hub: `8760`, then `+2` while taken by another city. Editors: `8765`
  (attribute), `8766` (texture), same rule.
- Before starting anything on a port, a process asks the port's owner which
  clone it serves and reuses it when it is the same clone:
  hub `GET /api/status` → `{"repo": "<clone path>", "hubTag": …, "sync": …}`;
  editors `GET /api/repo` → `{"root": "<clone path>"}`. These two responses
  are part of the contract.
- Starting a second process for the same clone is avoided (the existing
  page is opened instead).

## 5. Installed code and updates

- Everything executable comes from `4dcitygml/tools` releases `hub-v*`:
  assets `citygml-hub-<ver>-macos.zip` and `citygml-hub-<ver>-windows-full.zip`,
  each with the SHA-256 digest GitHub publishes for it. The zip contains one
  top-level folder `citygml-hub/program/` with the hub, the editors, the
  language and theme packs, the shared modules (`git_sync.py`, `shortcuts.py`,
  `pr_classification.py`) and the launchers (`citygml.sh`, `citygml.ps1`).
- Installation = unpack into `citygml-hub/<tag>/` after the digest matched;
  staging first, rename last; an existing `<tag>` folder is never touched.
- The launcher always starts the newest installed version. Nothing is
  downloaded unless the user asks: the first install by the one-line command,
  later versions by the hub's *Get it now* (`POST /api/update/fetch`) after the
  automatic check (`GET /api/update`). A new version is used from the next start.
- City repositories carry no code and no client pin (Exchange Contract A11).
  A city may declare `min_hub` in `4dcitygml.json`; tools treat it as advice.
- The city clone's `main` is kept in line with the city repository by the
  shared `git_sync` module (ls-remote first, fetch with git's low-speed abort,
  fast-forward or reset, never over unsaved changes). It never touches edit branches.

## 6. HTTP endpoints other processes may rely on

| Endpoint | Owner | Stable fields |
|---|---|---|
| `GET /api/status` | hub | `repo`, `branch`, `nwo`, `hubTag`, `sync.state`, `github.login` |
| `GET /api/repo` | editors | `root` |
| `GET /api/update` | hub | `running`, `latest`, `available`, `minHub`, `minHubOk`, `fetch.state` |
| `POST /api/update/fetch` | hub | starts the verified download; `fetch.state` reports progress |
| `POST /api/shortcut` | hub | creates the desktop launcher for the bound city |

## 6b. Sharing a clone between tools

Several tools may work on the same city clone at once (the hub and the editors
already do). The rules that make this safe: local `main` is machine-managed and
is only moved by `git_sync` (fast-forward / reset, never over unsaved changes);
a tool's own work lives on branches it created (`edit/…`, `tex/…`) and no tool
touches another tool's branches; tools read data files, they never rewrite
`4dcitygml.json`, `theme.json` or the logo. What remains is git's own
`index.lock`: two git commands on one clone at the same instant fail for one of
them — treat that as a retry, not an error. Desktop launchers are named after the
city; a tool that is not the hub must use its own name for its launcher.

## 7. What a new core module must not do

- Keep its own registry of clones or its own settings file for the same facts.
- Read code from a city clone, or accept a client pin from a city repository.
- Download or replace installed versions on its own, or modify the running version's folder.
- Assume a fixed port, or start a second process for a clone that is already served.
- Derive the city from anything but the clone's `4dcitygml.json` once started.
