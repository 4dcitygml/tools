# Integrated front-end (launcher)

> **Which hub is this?** The hub that residents install (the `hub-v` releases) is built
> from the branch `hub-1.x`; this page mirrors that branch's documentation. The hub code on
> `main` is the development line and is not what the one-line command installs.

> The app presents itself to users as **"Building Data Editing Tools"** (screen titles).
> The distribution zip and its top folder are named **`citygml-hub`**; in code and issues
> the integrated front-end is just called "hub".

A dashboard that **launches the attribute editor and texture editor from buttons on a single
screen** and shows the status of your Pull Requests / Issues together with your achievement
badges. Submitters can use this as their entry point without thinking about "which script to
run".

## Launch

```bash
python3 tools/hub/app.py        # → opens http://localhost:8760 (or the next free port, +2, when another city's hub is running)
```

The only dependency is the Python 3.9+ standard library. `gh` (GitHub CLI) is **not
required**. Listing PRs / Issues requires a GitHub connection; if not connected, you can
choose the city's account on the account screen (below); a developer machine's `gh`
sign-in is offered there as one of the choices, never used on its own.

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
  **start the tools again** (the desktop icon, or the one-line command) to resume — no need to
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
- **One account per city, chosen explicitly (hub-v1.3).** The account screen offers
  three ways in, each one click: the GitHub CLI sign-in of this computer (shown with its
  login when `gh` is signed in), an account connected before on this computer, or a new
  sign-in with the 8-digit code. Nothing is used silently; the choice is recorded per city
  (`cities[<owner/repo>].login` in the shared settings) and never inherited by another city.
- Each account is stored in `~/.citygml/auth/<login>.json` (0600) together with
  `<login>.git-credentials` in git-credential-store format. Network git commands of the hub
  and the editors pass `-c credential.helper=` and `-c credential.https://github.com.helper=store
  --file=<that file>`, on every platform — the computer's keychain, manager or global store is
  never consulted and the token never appears in a command line. The commit identity
  (`<id>+<login>@users.noreply.github.com`) is written into the clone's **local** git config;
  the computer's global identity is shown in Settings but not used and not changed.
- Without an account, network git commands still pass `-c credential.helper=`: the computer's
  credentials are never used, a push fails with a plain "no account is connected" message and
  the editors point back to the hub's Settings. When the account of an existing clone changes,
  `origin` is re-pointed at that account's fork (the copy step runs if the fork does not exist
  yet); the upstream city is never touched.
- A token GitHub rejects (401) unbinds the account and says so ("was revoked");
  Settings → "Disconnect" / "Delete" remove the files on this computer (revoking on GitHub
  is Settings → Applications → Authorized OAuth Apps).
- Earlier versions kept one token in `~/.citygml_auth.json` and, on some computers, wrote a
  global credential helper pointing at the plain-text `~/.citygml_git_credentials`. The first
  start of hub-v1.3 shows a one-time hand-over screen listing what was found, migrates the
  token into an account file, and offers to remove only what the earlier version wrote.
- The label of "this computer's GitHub sign-in" is read from the GitHub CLI's own config file; its token is read only when that choice is pressed (`CITYGML_HUB_NO_GH=1` hides the choice). Proposals are opened with the city's account or on GitHub's own screen — the editors never call `gh pr create`. Every process scrubs the shell's git overrides (`GIT_AUTHOR_*`, `GIT_ASKPASS`, `GIT_SSH*`, …) at start.
- If a city is hosted by an organization with **OAuth App access restrictions** (on by
  default for new organizations), fork creation and PR creation answer 403 until an
  organization owner grants the app access; the hub translates that message and points
  at Organization access under the app's settings page.
- The required scope is `public_repo`: **read and write access to the public repositories of
  the user's account** — necessary and sufficient for fork / push / pull request on the public
  city repos. Private repositories are not covered. The same write range is stated on the
  screen shown **before** authorization (`hub.setup_connect_scope`), so users know what they
  grant before pressing Authorize. (Only if a city repo ever goes private would this need to
  revert to `repo`.)
- On a restart, the account recorded for the city is used without any screen. A city
  without a recorded account always shows the account screen first.

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

## Layout of the installed program

The release zip is the payload of the one-line installer, not a download people open:
its only top-level item is `program/`, unpacked into `citygml-tools/citygml-hub/<tag>/`.

```
citygml-tools/
├─ citygml.sh | citygml.ps1        per-user launcher (copied from program/; the desktop icon runs it)
└─ citygml-hub/<hub-vX.Y.Z>/program/
      hub.py, index.html, review.html, setup.html, settings.html
      runtime.py, accounts.py, git_sync.py, shortcuts.py, pr_classification.py
      attr_editor/, tex_editor/, i18n/, themes/, licenses,
      PortableGit/ and PythonPortable/ (Windows only)
```

`runtime.py` is the one place that knows this layout, the person's files (settings,
accounts, tools folder — all derived from HOME), the git and python to run, how a
clone names its city and how GitHub is reached; the hub and the editors import it and
the other shared modules by name. The person's own Git configuration never decides
which git runs: the identity is written into the clone and credentials are handed over
per command (see `docs/client-runtime-contract.md`).

## Launch path (Windows: bundled Python; decision 2026-08-28)

The hub and the editors are plain `.py` files next to the shared modules, distributed
as source on every OS — there is **exactly one launch path and no frozen executable**,
so what runs is always inspectable. The Windows zip bundles everything needed:

- **Python**: the python.org **embeddable package**, bundled as `PythonPortable/`
  (version + SHA-256 pinned; see the repository-root `THIRD_PARTY_NOTICES.md`).
  The per-user launcher (`citygml.ps1`) starts `program/hub.py` with
  `program/PythonPortable/python.exe`; the fallback starter shipped inside
  `program/` (`start-windows.bat` = `packaging/start-windows.bat`) resolves
  **`PythonPortable/` → local `py`/`python`** in that order. No Python install
  is needed either way.
- **Git**: MinGit under the compatibility name `PortableGit/`, used whenever it is
  present (`runtime.git_exe()`); otherwise the git on PATH — **no git installation
  is needed**.
- The hub launches the bundled editors (`program/attr_editor/app.py`,
  `program/tex_editor/app.py`) with the same Python (`runtime.python_exe()`);
  nothing is ever run from a city clone.
- The detection result can be checked in `/api/status` under `runtime` (git/python
  path, bundled).
- macOS bundles no binaries (M1): the per-user launcher (`citygml.sh`) uses the
  `python3` on PATH (Apple's Command Line Tools); `packaging/start-mac.command`
  is only for running from a source checkout.

> `PythonPortable/` and `PortableGit/` live inside `program/` in the zip (same
> level as `hub.py`).

## What it can do

| Feature | Description |
|---|---|
| **Tool launch** | Launches the attribute editor (:8765) / texture editor (:8766) as child processes and opens them in the browser. If already running, "Open". |
| **First-time setup** | When there is no clone, proceeds through GitHub authentication (`/api/auth/start`) → fork creation (`/api/setup/fork`) → clone (`/api/setup/clone`) **with buttons only**. State is consolidated in `/api/setup/status`; the screen polls every 2 seconds and advances automatically. |
| **Account / repository** | Shows the git branch, user, and GitHub connection status. Without an account, links to the account screen; "Settings" opens the per-city settings page (account, copy on GitHub, data folder, version, desktop icon, saved accounts, start over, how to remove the tools). |
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
| `app.py` | Local HTTP server (status, contribution API, child-process launch). Port 8760, stepping by +2 when taken by another city's hub; a hub already serving the same clone is reused. In the zip: `program/hub.py`. |
| `index.html` | Dashboard UI. In the zip: `program/index.html`. |
| `setup.html` | Initial setup and the account screen (served while there is no clone, or no account for the city). |
| `settings.html` | Per-city settings (account, copy on GitHub, data folder, version, desktop icon, how to remove the tools). |
| `review.html` | Maintainer UI for per-building change-history review and approval. |
| `packaging/start-mac.command` / `.bat` | Launchers for running from a source checkout. The release zip has no top-level launcher any more (hub-v1.2.0): it is the payload of the one-line installer, which unpacks it into `citygml-tools/citygml-hub/<tag>/` and starts `program/hub.py`; `program/citygml.sh` / `citygml.ps1` are the per-user launchers it copies into place. |

- Each tool starts on its default port (attr_editor=8765 / tex_editor=8766); if already
  listening, it is reused.
- The GitHub API is called directly via REST / GraphQL with the standard library (no `gh` CLI
  dependency). Contribution data is fetched in one request with the same GraphQL as gh
  (including PR reviewDecision), cached for 30 seconds, with "Refresh" forcing a re-fetch.
- Authentication uses the token of the account recorded for the city
  (`~/.citygml/auth/<login>.json`); `gh` is never consulted on its own.
