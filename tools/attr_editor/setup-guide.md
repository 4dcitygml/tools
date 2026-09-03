# Setup guide (for beginners)

Even without a programming environment, you can propose corrections to building
attributes with the attribute editor.
The initial setup (steps 1–4 here) is needed only once. It takes about 30 minutes
(most of which is waiting for the data download).

What you need: an internet connection, a GitHub account (free), and about 10 GB of
free disk space.

---

## 1. Create a GitHub account

1. Open <https://github.com/signup>
2. Enter your email address, a password, and a username (ASCII letters and digits) to register
3. Enter the code from the confirmation email to finish

## 2. Create a "fork" (your own copy) of the building data

1. While logged in, open <https://github.com/4dcitygml/sample-tokyo-station/fork>
2. Press the green **Create fork** button
3. Note down the URL of the resulting page
   (`https://github.com/<your ID>/sample-tokyo-station`) — you will use it later

## 3. Install the tools (used for fetching data and sending proposals)

### On macOS

1. Open the "Terminal" app (Launchpad → "Other"), type `git --version` and press Enter
   → if the "command line developer tools" install screen appears, press "Install"
   (**along with git, this also installs the python3 needed to run the editor**)
2. Install the credential manager **Git Credential Manager**:
   download and install `gcm-osx-*.pkg` from
   <https://github.com/git-ecosystem/git-credential-manager/releases>

### On Windows

**This step is not needed.** The "all-in-one zip" in step 4 bundles all the required
tools (git and the credential manager). You install nothing.

> With the credential manager installed, the first time you send a proposal you only
> need to **log in to GitHub in the browser** to authenticate (no manual entry of
> passwords or tokens).

## 4. Launch the attribute editor

### On macOS (extract the zip and one terminal line; no warnings appear)

1. Download **`citygml-attr-editor-macos.zip`** from the
   [Releases page](https://github.com/4dcitygml/tools/releases) and double-click to
   extract it
2. In the terminal, type `python3 ` (with a trailing space) — **do not press Enter
   yet** — then **drag & drop the app.py from the extracted folder onto the terminal
   window**. Confirm the line reads `python3 /Users/…/app.py`, then press Enter
   (if you see `>>>`, you pressed Enter too early; type `exit()` + Enter to leave and
   try again. Details are also in the folder's "READ-ME-FIRST.txt")
3. The **first-time setup screen** appears in the browser; paste the **fork URL** you
   noted in step 2 and press **Clone** → fetching the data (a few
   GB) starts. When it finishes, the editor screen opens automatically
4. At that point, **"Attribute Editor.command" is created automatically on your desktop**.
   **From next time, just double-click it** (no more terminal. It was created on your
   own Mac, so no security warning appears either)

> If you are comfortable with the terminal, you can also launch in one line without
> the zip:
> `curl -sL https://raw.githubusercontent.com/4dcitygml/tools/main/tools/attr_editor/app.py -o ~/attr_editor.py && python3 ~/attr_editor.py`

### On Windows (extract the zip and double-click; no install)

1. Download **`citygml-attr-editor-windows-full.zip`** (all-in-one, git bundled, about
   100 MB) from the [Releases page](https://github.com/4dcitygml/tools/releases)
2. Right-click the zip → **Extract All**. Extract to a **shallow location such as the
   desktop**
   (deep folders can hit the path-length limit)
3. Double-click **`start-windows.bat`** in the extracted folder (it starts the bundled
   Python — no install needed).
   If a security warning appears the first time, you can proceed via
   **More info → Run anyway**
   (this appears because the distribution is unsigned; it is not abnormal)
4. The **first-time setup screen** appears in the browser; paste the fork URL and press
   **Clone**

> If a git on PATH already has `user.name` and `user.email` configured globally, the
> all-in-one zip also prefers the existing git and its credential setup. If you have
> installed both git and Python 3.9+ yourself, `start-windows.bat` also runs with them
> (the bundled copies are simply not used).

## 5. Everyday use

1. Launch the app (Mac: "Attribute Editor.command" on the desktop / Windows: double-click
   start-windows.bat)
2. Click a mesh frame on the map → click a building → click attribute values on the
   right to correct them
3. **Send your changes** → enter the reason and evidence for the
   correction → **Send this**
4. If you have connected to GitHub via the hub, the change proposal for the maintainer
   is created automatically
5. When "Sending complete. Waiting for maintainer confirmation" appears, you are done

> Only if "Open GitHub to complete sending" appears —
> e.g. in standalone use without the hub — press the green **Create pull request** on
> the linked page, and press the same button once more on the confirmation screen.

## Troubleshooting

| Symptom | What to do |
|---|---|
| No screen appears after launch | Check the error messages in the black window (log). Open <http://localhost:8765/> directly in the browser |
| "git not found" error appears | Redo step 3 and restart the app |
| The clone stops partway | Re-run on a stable connection (delete the destination folder first) |
| Authentication error when sending | Check that the credential manager (step 3) is installed |
| I want to update the data | Quit the app, then run `git pull` in the clone folder (or Sync fork on your GitHub fork page → re-clone) |
| (Mac) I deleted the launcher | Run `python3 ~/Documents/sample-tokyo-station/tools/attr_editor/app.py` in the terminal and it is recreated |
