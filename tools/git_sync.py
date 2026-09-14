# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Keeping a city clone's main in line with the upstream city repository.

One implementation shared by the hub and the editors (bundled next to them as
program/git_sync.py; in the source tree it is tools/git_sync.py).

Design (2026-09-06):
- No wall-clock timeout on `git fetch`. A large annual update may legitimately
  take minutes; a fetch killed halfway throws away everything received and would
  never complete on retry. Instead git's own progress-based abort is used:
  http.lowSpeedLimit / http.lowSpeedTime (stalled transfers are cut, slow ones
  are not).
- A cheap check first: `git ls-remote` (one round trip) tells whether upstream
  main moved at all. Nothing is fetched when it did not.
- Fail-open: offline, not a clone, or any git error leaves the clone as it is
  and reports a state the caller can show. The CI base-freshness gate remains
  the authoritative check on a proposal.
- Local main is machine-managed: fast-forward when possible, hard reset when
  histories diverged (practice repositories rewrite main daily). Modified
  tracked files stop the update (the user's work is never discarded). Edit
  branches are never touched.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

# Abort a transfer that stays below 1 KB/s for 30 s (stalled), never a merely slow one.
LOW_SPEED_ARGS = ["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30"]
LS_REMOTE_TIMEOUT = 20      # [s] one small round trip; DNS/connect hangs must not block
LOCAL_GIT_TIMEOUT = 60      # [s] local-only commands (rev-parse, status, merge, reset)


def _with_low_speed(base_args: list) -> list:
    """Insert the progress-based abort settings right after the git executable."""
    return [base_args[0], *LOW_SPEED_ARGS, *base_args[1:]]


def _run(base_args: list, root, args: list, *, timeout=None) -> subprocess.CompletedProcess:
    return subprocess.run([*base_args, "-C", str(root), *args],
                          capture_output=True, text=True, timeout=timeout)


def is_clone(root, base_args: list) -> bool:
    try:
        return _run(base_args, root, ["rev-parse", "--git-dir"], timeout=LOCAL_GIT_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def remote_main_head(root, url: str, base_args: list) -> "str | None":
    """Commit of upstream main via one round trip, or None when unreachable."""
    try:
        r = _run(base_args, root, ["ls-remote", "--heads", url, "main"], timeout=LS_REMOTE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] in ("refs/heads/main",):
            return parts[0]
    return None


def fetch_main(root, url: str, base_args: list, log=None) -> "str | None":
    """Fetch upstream main (no wall-clock limit; stalled transfers abort). Returns FETCH_HEAD or None."""
    try:
        if log:
            log("Fetching the latest city data …")
        r = _run(_with_low_speed(base_args), root, ["fetch", "--quiet", url, "main"])
        if r.returncode != 0:
            return None
        head = _run(base_args, root, ["rev-parse", "FETCH_HEAD"], timeout=LOCAL_GIT_TIMEOUT)
        return head.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def sync_main(root, url: str, base_args: list, log=None) -> dict:
    """Bring local main in line with upstream main.

    Returns {"state": …, "head": <commit or None>, "message": <text>} with state one of
    not-a-clone | offline | up-to-date | updated | ref-moved | dirty | error.
    "head" is the new main commit when state is updated / ref-moved, else the current one.
    """
    root = Path(root).resolve()

    def run(*args, timeout=LOCAL_GIT_TIMEOUT):
        return _run(base_args, root, list(args), timeout=timeout)

    try:
        if not is_clone(root, base_args):
            return {"state": "not-a-clone", "head": None, "message": "Not a git clone"}
        cur_main = run("rev-parse", "refs/heads/main").stdout.strip() or None
        remote = remote_main_head(root, url, base_args)
        if remote is None:
            return {"state": "offline", "head": cur_main, "message": "Upstream not reachable; using the local copy"}
        if remote == cur_main:
            return {"state": "up-to-date", "head": cur_main, "message": "Up to date"}
        new = fetch_main(root, url, base_args, log)
        if not new:
            return {"state": "offline", "head": cur_main, "message": "Fetch failed; using the local copy"}
        branch = run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
            # main is not checked out: move only the ref; the working tree stays untouched
            if new != cur_main and run("branch", "-f", "main", new).returncode == 0:
                if log:
                    log(f"Upstream update merged into main ({new[:12]})")
                return {"state": "ref-moved", "head": new, "message": "main updated (not checked out)"}
            return {"state": "up-to-date", "head": cur_main, "message": "Up to date"}
        if new == cur_main:
            return {"state": "up-to-date", "head": cur_main, "message": "Up to date"}
        dirty = [ln for ln in run("status", "--porcelain").stdout.splitlines()
                 if ln.strip() and not ln.startswith("??")]
        if dirty:
            if log:
                log("Warning: skipped upstream sync due to local unsaved changes")
            return {"state": "dirty", "head": cur_main, "message": "Local changes present; not updated"}
        if run("merge", "--ff-only", "FETCH_HEAD").returncode == 0:
            if log:
                log(f"Upstream update merged (main → {new[:12]})")
            return {"state": "updated", "head": new, "message": "Updated to the latest city data"}
        if run("reset", "--hard", "FETCH_HEAD").returncode == 0:
            if log:
                log(f"Updated main to match upstream (was {cur_main[:12] if cur_main else '?'}; old state remains in reflog)")
            return {"state": "updated", "head": new, "message": "Updated to the latest city data (history rewritten upstream)"}
        return {"state": "error", "head": cur_main, "message": "Could not update main"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"state": "error", "head": None, "message": str(e)}


class CloneJob:
    """`git clone` in the background with its progress for a polling screen (the setup
    screens of the hub and the editors share this one implementation).

    say(event, **params) turns the events "running", "no_git", "bad_url", "not_empty",
    "start", "size_note", "done", "failed" into the app's own wording; net_args() gives
    the git arguments for a network command (the city's account or anonymous)."""

    def __init__(self, say, net_args, git_available):
        self._say, self._net_args, self._git_available = say, net_args, git_available
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: "str | None" = None
        self.dest: "str | None" = None
        self.lines: list = []

    def state(self) -> dict:
        with self.lock:
            return {"ok": True, "gitAvailable": bool(self._git_available()), "running": self.running,
                    "done": self.done, "error": self.error, "dest": self.dest, "log": self.lines[-8:]}

    def start(self, url: str, dest: str) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError(self._say("running"))
            if not self._git_available():
                raise RuntimeError(self._say("no_git"))
            url = url.strip()
            if not re.match(r"^(https://|git@|file://|/)", url):
                raise ValueError(self._say("bad_url"))
            dest_path = Path(dest).expanduser()
            if dest_path.exists() and any(dest_path.iterdir()):
                raise ValueError(self._say("not_empty", dest=dest_path))
            self.running, self.done, self.error = True, False, None
            self.dest = str(dest_path)
            self.lines = [self._say("start", url=url), self._say("size_note")]
        threading.Thread(target=self._run, args=(url, str(dest_path)), name="citygml-clone", daemon=True).start()

    def _run(self, url: str, dest: str) -> None:
        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen([*self._net_args(), "clone", "--progress", url, dest],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            assert proc.stdout is not None
            for raw in proc.stdout:
                seg = raw.rstrip("\r\n").split("\r")[-1].strip()   # git progress arrives \r-separated
                if seg:
                    with self.lock:
                        if self.lines and self.lines[-1].split(":")[0] == seg.split(":")[0]:
                            self.lines[-1] = seg   # overwrite same-kind progress lines
                        else:
                            self.lines.append(seg)
            code = proc.wait()
            with self.lock:
                self.running = False
                if code == 0:
                    self.done = True
                    self.lines.append(self._say("done"))
                else:
                    self.error = self._say("failed", code=code)
        except Exception as e:  # noqa: BLE001 — shown in the screen
            with self.lock:
                self.running = False
                self.error = f"{type(e).__name__}: {e}"


class BackgroundSync:
    """Run sync_main once on a thread; expose its progress for a status endpoint."""

    def __init__(self, root, url_provider, base_args_provider, log=None):
        self._root = root
        self._url = url_provider
        self._base = base_args_provider
        self._log = log
        self._lock = threading.Lock()
        self._state = {"state": "pending", "head": None, "message": "Checking for the latest city data …",
                       "started": None, "finished": None}
        self.done = threading.Event()

    def start(self) -> "BackgroundSync":
        threading.Thread(target=self._work, name="citygml-sync", daemon=True).start()
        return self

    def _work(self) -> None:
        with self._lock:
            self._state["started"] = time.time()
        try:
            result = sync_main(self._root, self._url(), self._base(), self._log)
        except Exception as e:  # never take the server down
            result = {"state": "error", "head": None, "message": str(e)}
        with self._lock:
            self._state.update(result)
            self._state["finished"] = time.time()
        self.done.set()

    @property
    def state(self) -> dict:
        with self._lock:
            return dict(self._state)
