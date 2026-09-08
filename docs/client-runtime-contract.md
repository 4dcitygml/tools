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
| `CITYGML_ACCOUNT` | Set by the hub for the editors it launches: the login the city is bound to. The editors re-read the city's binding from the settings file on every use and fall back to this value only for a clone without `4dcitygml.json`. |
| `CITYGML_HUB_NO_GH` | `1` hides the "use this computer's GitHub" choice (the GitHub CLI's sign-in) on the account screen. The label of that choice is read from the CLI's own config file; the CLI's token is read only when the choice is pressed. |

Every client scrubs the shell's git overrides from its own environment at start
(`GIT_AUTHOR_*`, `GIT_COMMITTER_*`, `EMAIL`, `GIT_ASKPASS`, `SSH_ASKPASS`, `GIT_SSH*`,
`GIT_PROXY_COMMAND`, `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG*`) and sets
`GIT_TERMINAL_PROMPT=0`, so a commit or push can only use the clone's config and
the account's store. Proposals are opened with the account's token or on GitHub's
own screen — never through the GitHub CLI.

Test hooks (never set by launchers in normal use): `CITYGML_TOOLS_DIR`,
`CITYGML_RELEASES_JSON`, `CITYGML_RELEASES_API`, `CITYGML_ASSET_FILE`,
`CITYGML_NO_EXEC`, `CITYGML_INSTALL_TAG`; `CITYGML_OAUTH_CLIENT_ID` (development).

## 3. Files on the computer

```
~/Documents/citygml-tools/                     (Windows: %USERPROFILE%\Documents\citygml-tools\)
  citygml.sh | citygml.ps1                     per-user launcher (copied from the bundle / install-v1)
  <City name>.app | <City name>.lnk            desktop launcher, embeds the city id only
  citygml-hub/<hub-vX.Y.Z>/program/hub.py      one folder per installed version; the newest is started
  <temp>/citygml-hub-stage.*                   transient, while the launcher verifies a download
~/Documents/CityGML Data (<repo name>)/        the city clone (one per city)
~/.citygml_attr_editor.json                    shared settings (below)
~/.citygml/auth/<login>.json                   one GitHub connection per account (token, id; 0600)
~/.citygml/auth/<login>.git-credentials        the same token in git-credential-store format (0600)
~/.citygml_auth.json, ~/.citygml_git_credentials   earlier versions only; migrated / offered for removal
```

Shared settings file (`~/.citygml_attr_editor.json`):

```json
{
  "repo": "/…/CityGML Data (sample-tokyo-station)",
  "lang": "ja",
  "cities": {
    "4dcitygml/sample-tokyo-station": {"repo": "/…/CityGML Data (sample-tokyo-station)", "last_used": "2026-09-06T…", "login": "citydatawalker"}
  },
  "legacyReviewed": "2026-09-07"
}
```

- `cities[<owner/repo>].repo` is the clone of that city; `repo` is the last
  used clone (kept for tools that know only one clone); `lang` is the UI language.
- `cities[<owner/repo>].login` is the GitHub account that city uses (hub-v1.3):
  its token comes from `~/.citygml/auth/<login>.json`, its fork is `<login>/<repo>`,
  and its noreply address is written into the clone's **local** git config. A
  client never reads the computer's global git identity, credential helpers or
  the GitHub CLI on its own; `login` is set only by an explicit choice on the
  account screen and is never inherited by another city. A `login` without an
  account file is treated as unset.
- Network git commands pass `-c credential.helper=` and
  `-c credential.https://github.com.helper=store --file=<the account's .git-credentials>`;
  the token is never placed in the command line.
- `legacyReviewed` records that the one-time hand-over screen (what earlier
  versions left on the computer) was shown.
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
  page is opened instead) — for the same version only. A hub of another version
  left running for the clone (an update, or hub-v1.0.x reporting no `hubTag`) is
  named on the console and the new version starts on the next free port.

## 5. Installed code and updates

- Everything executable comes from `4dcitygml/tools` releases `hub-v*`:
  assets `citygml-hub-<ver>-macos.zip` and `citygml-hub-<ver>-windows-full.zip`,
  each with the SHA-256 digest GitHub publishes for it. The zip contains one
  top-level folder `citygml-hub/program/` with the hub, the editors, the
  language and theme packs, the shared modules (`runtime.py`, `accounts.py`,
  `git_sync.py`, `shortcuts.py`, `pr_classification.py`) and the launchers
  (`citygml.sh`, `citygml.ps1`). `runtime.py` is the one place that knows the
  files, folders, executables, settings file, city resolution and GitHub access
  described here; the hub and the editors import it and the other shared modules
  by name (the program's own folder is put on `sys.path`). What the zip contains is
  listed in one place, `scripts/build_bundle.py`, and checked by
  `scripts/verify_bundle.py`; the release workflow and the tests run those two.
- Installation = unpack into `citygml-hub/<tag>/` after the digest matched;
  staging first, rename last; an existing `<tag>` folder is never touched.
- The launcher always starts the newest installed version. Nothing is
  downloaded unless the user asks: the first install by the one-line command,
  later versions by the hub's *Get it now* (`POST /api/update/fetch`) after the
  automatic check (`GET /api/update`). A new version is used from the next start.
  The launcher is the one place that downloads, verifies and unpacks a release:
  *Get it now* runs its fetch mode (`citygml.sh --fetch-latest` /
  `citygml.ps1 -FetchLatest`), which prints the installed tag.
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
