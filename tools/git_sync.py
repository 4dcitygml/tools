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
  and reports a state the caller can show. GitHub's out-of-date notice remains
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
from contextlib import contextmanager
from pathlib import Path

import runtime

# Abort a transfer that stays below 1 KB/s for 30 s (stalled), never a merely slow one.
LOW_SPEED_ARGS = ["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30"]
LS_REMOTE_TIMEOUT = 20      # [s] one small round trip; DNS/connect hangs must not block
LOCAL_GIT_TIMEOUT = 60      # [s] local-only commands (rev-parse, status, merge, reset)


def _git(root, *args: str, store=None, net: bool = False, timeout=LOCAL_GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """git in the clone through the one runner (runtime.git: the account's store for a network
    command, output decoded with errors replaced; V8, D-3c8: this module had its own runner)."""
    return runtime.git(root, *args, net=net, store=store, timeout=timeout)


def is_clone(root) -> bool:
    try:
        return _git(root, "rev-parse", "--git-dir").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def remote_main_head(root, url: str, store=None) -> "str | None":
    """Commit of upstream main via one round trip, or None when unreachable."""
    try:
        r = _git(root, "ls-remote", "--heads", url, "main", store=store, net=True, timeout=LS_REMOTE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] in ("refs/heads/main",):
            return parts[0]
    return None


def _fetch(root, url: str, sha: str, store=None, log=None) -> "str | None":
    """Fetch upstream main (no wall-clock limit; stalled transfers abort) and return sha, the
    commit ls-remote named, once the clone has it; None otherwise. FETCH_HEAD is never read:
    another process's fetch may overwrite it before it is used (D-3c7)."""
    try:
        if log:
            log("Fetching the latest city data …")
        if _git(root, *LOW_SPEED_ARGS, "fetch", "--quiet", url, "main", store=store, net=True, timeout=None).returncode != 0:
            return None
        return sha if _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def fetch_main(root, url: str, store=None, log=None) -> "str | None":
    """Fetch upstream main; its commit, or None when offline or the fetch failed."""
    sha = remote_main_head(root, url, store)
    return _fetch(root, url, sha, store, log) if sha else None


_clone_locks: "dict[str, threading.RLock]" = {}
_clone_locks_guard = threading.Lock()
_held = threading.local()


@contextmanager
def clone_lock(root):
    """The one lock for git's work in a clone (D18): the hub's sync and both editors are
    separate processes on one working tree, and each used to lock only itself, so a send could
    check out its branch while the sync merged main. Every git mutation of a clone runs inside
    it: a file lock between processes (<clone>/.git/citygml.lock) and a lock between threads;
    a thread that already holds it may take it again."""
    root = Path(root).resolve()
    key = str(root)
    with _clone_locks_guard:
        thread_lock = _clone_locks.setdefault(key, threading.RLock())
    with thread_lock:
        depth = getattr(_held, "depth", {})
        _held.depth = depth
        if depth.get(key):
            depth[key] += 1
            try:
                yield
            finally:
                depth[key] -= 1
            return
        git_dir = root / ".git"
        with runtime.FileLock((git_dir if git_dir.is_dir() else root) / "citygml"):
            depth[key] = 1
            try:
                yield
            finally:
                depth[key] = 0


def sync_main(root, url: str, store=None, log=None) -> dict:
    """Bring local main in line with upstream main.

    Returns {"state": …, "head": <commit or None>, "message": <text>} with state one of
    no-city | not-a-clone | offline | up-to-date | updated | ref-moved | dirty | error.
    "head" is the new main commit when state is updated / ref-moved, else the current one.
    """
    root = Path(root).resolve()

    def run(*args):
        return _git(root, *args)

    if not url:   # a clone that names no city is synced to nothing (V1, D-3c1)
        return {"state": "no-city", "head": None, "message": "The clone names no city (4dcitygml.json repo); not synced"}
    try:
        if not is_clone(root):
            return {"state": "not-a-clone", "head": None, "message": "Not a git clone"}
        cur_main = run("rev-parse", "refs/heads/main").stdout.strip() or None
        remote = remote_main_head(root, url, store)
        if remote is None:
            return {"state": "offline", "head": cur_main, "message": "Upstream not reachable; using the local copy"}
        if remote == cur_main:
            return {"state": "up-to-date", "head": cur_main, "message": "Up to date"}
        new = _fetch(root, url, remote, store, log)
        if not new:
            return {"state": "offline", "head": cur_main, "message": "Fetch failed; using the local copy"}
        # From here the working tree and main change: under the clone's lock, so no editor's send
        # is checking out its branch meanwhile (D18). Fetching above needs no lock.
        with clone_lock(root):
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
            if run("status", "--porcelain", "--untracked-files=no").stdout.strip():   # tracked changes only
                if log:
                    log("Warning: skipped upstream sync due to local unsaved changes")
                return {"state": "dirty", "head": cur_main, "message": "Local changes present; not updated"}
            if run("merge", "--ff-only", new).returncode == 0:
                if log:
                    log(f"Upstream update merged (main → {new[:12]})")
                return {"state": "updated", "head": new, "message": "Updated to the latest city data"}
            if run("reset", "--hard", new).returncode == 0:
                if log:
                    log(f"Updated main to match upstream (was {cur_main[:12] if cur_main else '?'}; old state remains in reflog)")
                return {"state": "updated", "head": new, "message": "Updated to the latest city data (history rewritten upstream)"}
            return {"state": "error", "head": cur_main, "message": "Could not update main"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"state": "error", "head": None, "message": str(e)}


# git's transfer line, "Receiving objects:  45% (450/1000), 12.30 MiB | 2.00 MiB/s" (the words are
# translated by git; the numbers and the unit are not)
TRANSFER = re.compile(r"(\d+)% \(\d+/\d+\), ([\d.]+ [KMGT]?i?B)")


class CloneJob:
    """`git clone` in the background with its progress for a polling screen (the setup
    screens of the hub and the editors share this one implementation).

    say(event, **params) turns the events "running", "no_git", "bad_url", "not_empty",
    "start", "done", "failed" into the app's own wording; net_args() gives the git arguments
    for a network command (the city's account or anonymous). How much has arrived is git's own
    count (`progress`), never an estimate made in advance."""

    def __init__(self, say, net_args, git_available):
        self._say, self._net_args, self._git_available = say, net_args, git_available
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: "str | None" = None
        self.dest: "str | None" = None
        self.lines: list = []
        self.progress: "dict | None" = None

    def state(self) -> dict:
        with self.lock:
            return {"ok": True, "gitAvailable": bool(self._git_available()), "running": self.running,
                    "done": self.done, "error": self.error, "dest": self.dest, "log": self.lines[-8:],
                    "progress": self.progress}

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
            self.running, self.done, self.error, self.progress = True, False, None, None
            self.dest = str(dest_path)
            self.lines = [self._say("start", url=url)]
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
                    received = TRANSFER.search(seg)
                    with self.lock:
                        if received:
                            self.progress = {"percent": int(received[1]), "received": received[2]}
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

    def __init__(self, root, url_provider, store_provider, log=None, on_done=None):
        self._root = root
        self._on_done = on_done   # called with the final state (the city data may have changed)
        self._url = url_provider
        self._store = store_provider   # the account's credential store (or None), asked when it runs
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
            result = sync_main(self._root, self._url(), self._store(), self._log)
        except Exception as e:  # never take the server down
            result = {"state": "error", "head": None, "message": str(e)}
        with self._lock:
            self._state.update(result)
            self._state["finished"] = time.time()
        if self._on_done:
            try:
                self._on_done(self.state)
            except Exception as e:  # noqa: BLE001 - a follow-up never takes the sync down
                if self._log:
                    self._log(f"after sync: {type(e).__name__}: {e}")
        self.done.set()

    @property
    def state(self) -> dict:
        with self._lock:
            return dict(self._state)
