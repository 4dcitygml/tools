# Integrated front-end (launcher)

> The app presents itself to users as **"Building Data Editing Tools"** (screen titles).
> The distribution zip and its top folder are named **`citygml-hub`**; in code and issues
> the integrated front-end is just called "hub".

A dashboard that **launches the attribute editor and texture editor from buttons on a single
screen** and shows the status of your Pull Requests / Issues together with your achievement
badges. Submitters can use this as their entry point without thinking about "which script to
run".

## Launch

```bash
python3 tools/hub/app.py        # → opens http://localhost:8760
```

The only dependency is the Python 3.9+ standard library. `gh` (GitHub CLI) is **not
required**. Listing PRs / Issues requires a GitHub connection; if not connected, you can
connect from the dashboard's "Connect to GitHub" via the device flow (below)
(if the developer's machine has gh, its token is reused automatically).

## First-time setup (#59 / #86)

When launched without a clone, the **setup screen** opens. The principle is the same as the
entrance page — "one screen, one action" — and in addition there are **zero input fields**.

| | Screen | User's action |
|---|---|---|
| 1 | Connect this computer to GitHub | [Connect] → check the 8-digit code → [Open GitHub] → approve in another tab → return to the original tab |
| 2 | Create your own copy | [Create a copy] (`POST /repos/:owner/:repo/forks`) |
| 3 | Import the data | [Import] (`git clone`, with a progress log) |
| 4 | Ready | [Start] |

- **We do not ask for the fork URL** (the tool knows where the fork is). **We do not ask for
  name or email either** (they are fetched from GitHub and set automatically in `git config`;
  if the email is private, a noreply address is assembled).
  **We do not ask for the save location either** (defaults to Documents folder; changing it
  is folded into "Advanced settings").
- Approval, fork creation, and clone completion are **detected automatically by polling** and
  the screen advances (the user is never made to press "Next").
- Immediately after obtaining the 8-digit code, no other tab is opened automatically. The user
  opens GitHub with a button only after reading, in the original tab, "close the GitHub tab
  and come back". While waiting, the original tab's title also changes to
  **"← come back here"**.
- The `A third-party OAuth application has been added to your account` email that arrives
  after connecting to GitHub is a normal notification that the connection completed. The
  screen explains that it is unrelated to the Mac / Windows confirmations and that no extra
  action is needed.
- For accounts that cannot yet reach the source data under the invitation model (private
  repo), a **"Waiting for your invitation" screen** is shown instead of the fork screen: it
  displays the user's GitHub username in large text, and a **"Copy request text" button**
  copies a template message including the username to send to the maintainer
  (the user never has to compose the message). **Pending invitations are approved
  automatically by the server**, so the user does not need to notice the invitation email
  (the screen advances automatically as soon as it arrives, #96). The waiting screen also
  states that the terminal is waiting as part of "first-time setup" to drive the browser
  screen, and that if the maintainer responds later, the user can close the screen and simply
  launch **the same "start-windows.bat"** (on Mac, the same launcher file) to resume — no need to
  redo the GitHub connection.
  No advance invitation is needed = you do not need to know the recipient's GitHub account at
  distribution time.
- The clone location is shared with the attribute editor (`~/.citygml_attr_editor.json`). For
  private distribution, `--mode private` (or `mode`/`inviteUrl` in `preset.json`) shows the
  invitation guidance (#78).
- After the invitation is confirmed, the screen states up front that creating the working
  copy may take up to a minute and that progress may not change for a few minutes during data
  import. If the import fails, partial data is not deleted; a free alternative save location
  is chosen automatically and [Import again] resumes.
- After the import completes, the screen does not switch automatically; the completion screen
  is kept until [Start] is pressed. On the first dashboard, only the "attribute editor" is
  recommended as **"start here"**.
- When the attribute editor is opened for the first time, the operation order "blue square
  (mesh) on the map → light-blue building → attributes on the right" is explained. A short
  version of the operation order remains on screen after the guide is closed.
- After editing attributes, just "Send your changes": the hub's GitHub connection is reused
  to automatically create the change proposal for the maintainer. If sending fails, the
  edits remain on screen and can be retried safely.

### GitHub authentication (OAuth device flow)

A beginner's Mac has neither `gh` nor git credentials (`gh` is not included in the Command
Line Tools either). So we **implement the OAuth device flow ourselves using only the
standard library**, finishing authentication with "a button + an 8-digit code" without
opening a terminal.

- The `client_id` is **public information** (the device flow needs no client_secret). Provide
  it via `oauthClientId` in `preset.json` or the environment variable
  `CITYGML_OAUTH_CLIENT_ID`.
- If unset, we fall back to `gh auth token` (developer machines pass through with this).
  **To see the same "Connect" screen as a beginner on your own machine, launch with
  `CITYGML_HUB_NO_GH=1`.**
- The obtained token is saved in `~/.citygml_auth.json` (0600). Only when the bundled Git is
  used, it is passed to git via **`credential.helper store` with the dedicated file
  `~/.citygml_git_credentials` (0600)**. If an already-configured existing Git is chosen, its
  credential helper is not modified. In both cases the `origin` URL stays plain, so the token
  is not visible in screen sharing or `git remote -v`.
- The required scope is `public_repo`: **read and write access to the public repositories of
  the user's account** — necessary and sufficient for fork / push / pull request on the public
  city repos. Private repositories are not covered. The same write range is stated on the
  screen shown **before** authorization (`hub.setup_connect_scope`), so users know what they
  grant before pressing Authorize. (Only if a city repo ever goes private would this need to
  revert to `repo`.)
- On a restart where the GitHub user can be confirmed with the saved token, the 8-digit code
  screen is skipped. In that case the screen shows "your previous GitHub connection was
  carried over", so it does not look like unintended automatic progress.

> **Registering an OAuth App is required** (free, one time only): GitHub Settings → Developer
> settings → OAuth Apps → New OAuth App → **check Enable Device Flow** → put the issued
> Client ID into `preset.json`. The Client Secret is not used.

### Language policy (who reads what)

Generated text is language-routed by its **reader**, not by where the code runs:

| Reader | Examples | Language |
|---|---|---|
| The person at the screen | menus, guides, error messages, setup screens | UI language (`CITYGML_LANG` > config > OS locale > en) |
| The city's reviewers / the public record | generated PR title and body, attribute labels inside PR text | **repo working language** (`4dcitygml.json` `lang`, en fallback) |
| Machines | branch prefixes (`edit/`, `tex/`), commit subjects and bodies, `Building:` trailers, `<!--sec:reason-->` / `<!--cp:key-->` anchors, CI placeholder literals | fixed English / literals — never translated |
| The contributor themselves | the reason / notes they typed | kept exactly as written |

Consequences worth knowing:

- When the repo language differs from the UI language, the attribute editor
  shows a note above the PR preview ("this proposal will be written in …").
  The preview itself is **rendered server-side by the same code that posts the
  PR** (`/api/pr-preview`), so preview and PR cannot drift.
- The generated ja/de title prefixes (`属性修正`/`テクスチャ`,
  `Attributkorrektur`/`Textur…`) deliberately match the title fallbacks in
  `review_kind()` and the CI scripts, so even branch-less manual PRs classify;
  this pairing is contract-tested (`tests/test_repo_language.py`).
- Squash-merged PRs carry the repo-language PR title into the history's title
  line (practice repos are periodically reset; the `Building:` trailer
  contract lives in the commit body and is unaffected).

## Layout of the distribution zip

Only **three items are visible at the top** of the extracted folder. Things that are
"meaningless to the user", like `index.html` or `.py` files, are **hidden in `program/`**
(#86).

```
citygml-hub/
├─ READ-ME-FIRST.html   ← the only entrance (= tools/hub/getting-started.html)
├─ start-mac.command             ← on Windows, start-windows.bat
└─ program/                    index.html, hub.py, .bat, licenses,
                                  PortableGit, PythonPortable (Windows only)
```

The Windows zip bundles the python.org **embeddable package** as `PythonPortable/`
(pinned version + SHA-256, verified at build time), so no Python install is needed;
`start-windows.bat` prefers it and falls back to a system `py`/`python`
(decision 2026-08-28 — no frozen executable).
The Windows `PortableGit/` keeps its name for compatibility with the existing search paths;
its contents are **MinGit**, Git for Windows' official minimal configuration for app
bundling. If a Git on PATH already has `user.name` and `user.email` configured globally on
Windows, that Git and its existing credential setup take priority. If either is unset, Git
fails to start, or Git is absent, the bundled MinGit is used.

- The ① and ② in the file names show the **order to open them**. The explanatory text
  (`READ-ME-FIRST.txt`) is not included because **two entrances cause confusion**
  (its content is folded into the ① HTML).
- Launch files are **not placed inside a folder**: the single extra "open the folder"
  operation is a dropout point.
- `app.py`'s `BUNDLE_DIRS` looks for `PortableGit` / `PythonPortable` / `preset.json` both
  next to itself and in `program/`. `start-mac.command` does the same (works with a flat
  layout during development too).

## Entrance page (getting-started.html)

**Users are asked to open this first** (a double-click opens the browser; no server needed).
Its role is narrowed to **clearly explaining "how to launch" only** (it has no buttons,
probes, or diagnostics). It concentrates on getting people safely through the moment where
many of them drop out — **"double-clicking the launch file → the OS security warning"** — by
reassuring them that this is normal and safe.

Design principles (the #86 from-scratch rebuild):

- **One screen, one action.** Advance with "Next" and show the end with progress.
  Mac: 6 steps (blocked → allow → open → folder-access permission → components) /
  Windows: 3 steps (launch → More info → Run) + a completion screen.
- **The screen shows only "what to do now".** Safety details and troubleshooting are folded
  into [when things go wrong] (information is hidden, not thrown away).
- **No jargon.** Fork → "your own copy", clone → "import the data",
  Command Line Tools → "genuine Apple components", signing/Gatekeeper → "a confirmation
  screen that appears only the first time".
- **Show with pictures.** SVG schematic diagrams are used; for Windows SmartScreen the
  screens before pressing "More info" and with "Run" are reproduced separately to match the
  real screenshots.
- Double-clicking the launch file makes **the hub open the browser automatically**, so the
  entrance has no "open" button.
- **Account creation, login, (private) invitation requests, forking, and data fetching are
  guided by the hub's post-launch "Getting started" (#59)** with status display (a static
  `file://` HTML page cannot run `gh`/`git`).

## Launch path (Windows: bundled Python; decision 2026-08-28)

The hub `app.py` is a **single file with no dependency on sibling files**, distributed
as plain `.py` on every OS — there is **exactly one launch path and no frozen
executable**, so what runs is always inspectable source. The Windows zip bundles
everything needed:

- **Python**: the python.org **embeddable package**, bundled as `PythonPortable/`
  (version + SHA-256 pinned; see the repository-root `THIRD_PARTY_NOTICES.md`).
  The launcher (`start-windows.bat` = `packaging/start-windows.bat`) resolves
  **`PythonPortable/` → local `py`/`python`** in that order, so no Python install
  is needed.
- **Git**: MinGit under the compatibility name `PortableGit/`. An existing
  configured Git on PATH takes priority; otherwise the bundled MinGit is
  auto-detected — **no git installation is needed**.
- The hub itself also detects `PythonPortable/` via `python_cmd()` and launches the
  cloned editors (`tools/*/app.py`) with the same Python (`sys.executable`
  propagation plus explicit detection as a safeguard).
- The detection result can be checked in `/api/status` under `runtime` (git/python
  path, bundled).
- macOS bundles no binaries (M1): `start-mac.command` uses the CLT `python3`
  (`PythonPortable/` is also honored there if ever bundled).

> `PythonPortable/` and `PortableGit/` live inside `program/` in the zip (same
> level as `hub.py`).

## What it can do

| Feature | Description |
|---|---|
| **Tool launch** | Launches the attribute editor (:8765) / texture editor (:8766) as child processes and opens them in the browser. If already running, "Open". |
| **First-time setup** | When there is no clone, proceeds through GitHub authentication (`/api/auth/start`) → fork creation (`/api/setup/fork`) → clone (`/api/setup/clone`) **with buttons only**. State is consolidated in `/api/setup/status`; the screen polls every 2 seconds and advances automatically. |
| **Account / repository** | Shows the git branch, user, and GitHub connection status. If not connected, runs "Connect to GitHub" (device flow) on the spot. |
| **Your PRs / Issues** | Lists the PRs and Issues you created, with state (open/closed/merged) and **whether there was a response** (review/comments). Only transient CI failures with no data-side items to confirm can be "re-run" via automated inspection. Data defects are routed to automatic re-inspection after fixing; being behind the latest version is routed to re-importing. |
| **Maintainer approval screen** | Groups the change proposals arriving from the attribute/texture editors by building ID and shows them in two states according to who works next: "waiting for approver confirmation" and "waiting for proposer action". The waiting state carries reason labels such as CI, approver, importing the latest version, or automated inspection in progress. All 11 checks — description, change unit, consistency with the latest version, CityGML format, geometry, attributes, topology, and so on — are shown as pass = green, not applicable = gray, failed = red. The topology check runs on each building's first time (and on geometry changes); later runs that do not change the shape are not applicable. CI never rejects a PR mechanically; it comments the items to confirm and works them out with the proposer. Approvers can also send confirmation comments from 5 templates or free text, recording change requests. Past history, Japanese attribute names, before/after values, supporting documents, the permanent 3D model, and Google Maps can all be checked on the same screen, and approval is recorded. The selected building ID is kept in the URL, so the same building is shown after a reload. `/review.html?demo=1` lets you try the operations without touching real data or the change history. |
| **Achievement badges** | Shows a rank based on the number of merged PRs (✨→🌱→🌿→🌳→🏛️) and the remaining count to the next rank. |
| **Trouble / suggestions** | Creates a UX feedback Issue from an in-hub form using the connected GitHub authentication. No re-login to GitHub is needed. The subject, purpose, and environment are pre-filled, and the poster's current achievement badge and merged-PR count are recorded automatically as reference for maintainers. |

To hide the maintainer cards during a demo use `/?admin=off`; to show them use `/?admin=on`.
They are also shown when there is no parameter. `admin=0` and `admin=false` are also treated
as hidden.

## Structure

| File | Role |
|---|---|
| `app.py` | Local HTTP server (status, contribution API, child-process launch). Port 8760. In the zip: `program/hub.py`. |
| `index.html` | Dashboard UI. In the zip: `program/index.html`. |
| `review.html` | Maintainer UI for per-building change-history review and approval. |
| `getting-started.html` | Entrance of the distribution zip (launch-guide wizard). In the zip: `READ-ME-FIRST.html`. |
| `packaging/start-mac.command` / `.bat` | Launchers for the `.py` version. In the zip: `start-mac.command` (mac) / `program/start-windows.bat` (win). |

- Each tool starts on its default port (attr_editor=8765 / tex_editor=8766); if already
  listening, it is reused.
- The GitHub API is called directly via REST / GraphQL with the standard library (no `gh` CLI
  dependency). Contribution data is fetched in one request with the same GraphQL as gh
  (including PR reviewDecision), cached for 30 seconds, with "Refresh" forcing a re-fetch.
- Authentication uses the device-flow token (`~/.citygml_auth.json`). If gh is present, its
  token is used as a fallback too.
