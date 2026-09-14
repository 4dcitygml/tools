# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""GitHub accounts of the editing tools, one file per account (hub-v1.2.1).

A city clone uses exactly one account: the one recorded for that city in the
shared settings (`cities[<owner/repo>].login`). Everything that needs GitHub —
the REST calls, the fork, the push, the commit identity written into the clone —
comes from that account. Nothing is read from, or written to, the computer's own
Git configuration, its credential helpers or the GitHub CLI unless the person
explicitly picks "use this computer's GitHub" on the account screen.

Files (readable by the owner only; locations in runtime.py):
  ~/.citygml/auth/<login>.json             token, id, display name
  ~/.citygml/auth/<login>.git-credentials  the same token in git-credential-store
                                           format, handed to git with -c for
                                           network commands (never in argv)

Earlier versions kept one token for the whole computer (~/.citygml_auth.json),
wrote the commit identity into the global Git configuration and, on some
computers, left a global credential helper pointing at a plain-text store
(~/.citygml_git_credentials). `legacy_traces()` finds those, `migrate_legacy_token()`
moves the token into an account file and `remove_legacy_traces()` removes only
what an earlier version of these tools wrote itself.

Standard library only; shared by the hub and the editors.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import runtime

_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
ORG_RESTRICTION_MARK = "OAuth App access restrictions"


# ---- account files ----

def safe_login(login) -> str:
    """A GitHub login usable as a file name; ValueError otherwise (never trust input)."""
    value = str(login or "").strip()
    if not _LOGIN_RE.match(value):
        raise ValueError(f"not a GitHub login: {login!r}")
    return value


def account_path(login: str) -> Path:
    return runtime.auth_dir() / f"{safe_login(login)}.json"


def credentials_path(login: str) -> Path:
    return runtime.auth_dir() / f"{safe_login(login)}.git-credentials"


def store_line(token: str) -> str:
    """The one line of an account's credential store, in git-credential-store format."""
    return f"https://x-access-token:{token}@github.com\n"


def store_for(login) -> "Path | None":
    """The credential store git gets for this account's network commands, or None
    (no account, or its store is gone): see runtime.git_args.

    A store whose line ends in CR LF is rewritten from the account's token first:
    hub-v1.3.1 and earlier wrote it in text mode, which on Windows turned the LF into
    CR LF, and git's credential store ignores such a line (a push then failed with
    "could not read Username ... terminal prompts disabled")."""
    if not login:
        return None
    try:
        path = credentials_path(login)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        if b"\r" in path.read_bytes():
            token = token_for(login)
            if token:
                _write_private(path, store_line(token))
    except OSError:
        pass
    return path


def _write_private(path: Path, text: str) -> None:
    """Write text as UTF-8 with the line endings it carries (LF on every platform: a
    text-mode write would turn them into CR LF on Windows, which git's credential
    store rejects), readable by the owner only, replaced atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_bytes(text.encode("utf-8"))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def save_account(login: str, token: str, user_id, name: str = "") -> dict:
    """Store (or refresh) an account. Returns the stored record without the token."""
    login = safe_login(login)
    record = {"login": login, "id": int(user_id or 0), "name": str(name or ""),
              "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _write_private(account_path(login), json.dumps({**record, "token": str(token)}))
    _write_private(credentials_path(login), store_line(token))
    return record


def load_account(login) -> "dict | None":
    """The stored record including the token, or None."""
    try:
        data = runtime.read_json(account_path(login), default=None)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("token") else None


def token_for(login) -> str:
    return str((load_account(login) or {}).get("token") or "")


def public_record(login) -> "dict | None":
    """The stored record without the token (for screens and status payloads)."""
    data = load_account(login)
    return {k: v for k, v in data.items() if k != "token"} if data else None


def list_accounts() -> list:
    """Stored accounts (no tokens), oldest first."""
    folder = runtime.auth_dir()
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.json"):
        try:
            login = safe_login(path.stem)
        except ValueError:
            continue
        rec = public_record(login)
        if rec:
            found.append(rec)
    return sorted(found, key=lambda r: str(r.get("savedAt") or ""))


def delete_account(login) -> bool:
    removed = False
    for path in (account_path(login), credentials_path(login)):
        try:
            path.unlink()
            removed = True
        except OSError:
            pass
    return removed


# ---- identity, always written into the clone (never into the computer's configuration) ----

def noreply_email(login: str, user_id) -> str:
    """GitHub's noreply address: the one address that is safe to publish in commits."""
    login = safe_login(login)
    return f"{int(user_id)}+{login}@users.noreply.github.com" if user_id else f"{login}@users.noreply.github.com"


def apply_clone_identity(root, login: str, user_id) -> bool:
    """Write the account's name and noreply address into the clone's OWN config.

    Local config outranks every global or per-directory setting of the computer,
    so a commit made in this clone is attributed to the account the city uses,
    whatever the person's other Git work is set up as. Nothing global is touched."""
    if not runtime.git_exe() or not login:
        return False
    try:
        for key, val in (("user.name", safe_login(login)), ("user.email", noreply_email(login, user_id))):
            if runtime.git(root, "config", "--local", key, val, timeout=10).returncode != 0:
                return False
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return True


def clear_clone_identity(root) -> None:
    """Remove the account's name from the clone's own config (disconnect / forget)."""
    if not runtime.git_exe():
        return
    for key in ("user.name", "user.email"):
        try:
            runtime.git(root, "config", "--local", "--unset", key, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


def clone_identity(root) -> dict:
    """The identity the clone's own config carries (empty strings when not set locally)."""
    return {key: runtime.git_output(root, "config", "--local", "--get", f"user.{key}", timeout=10)
            for key in ("name", "email")}


# ---- the city ↔ account binding (shared settings file, runtime.py) ----

def city_login(city) -> "str | None":
    """The account recorded for a city (only when its account file still exists)."""
    if not city:
        return None
    cities = runtime.read_config().get("cities")
    entry = cities.get(str(city).lower()) if isinstance(cities, dict) else None
    login = (entry or {}).get("login") if isinstance(entry, dict) else None
    try:
        return safe_login(login) if login and load_account(login) else None
    except ValueError:
        return None


def bind_city_login(city, login) -> None:
    """Record that this city uses this account (login=None forgets the binding)."""
    if not city:
        return

    def mutate(cfg: dict) -> None:
        cities = cfg.get("cities") if isinstance(cfg.get("cities"), dict) else {}
        entry = dict(cities.get(str(city).lower()) or {})
        if login:
            entry["login"] = safe_login(login)
        else:
            entry.pop("login", None)
        cities[str(city).lower()] = entry
        cfg["cities"] = cities

    runtime.update_config(mutate)


def is_legacy_acknowledged() -> bool:
    return bool(runtime.read_config().get("legacyReviewed"))


def acknowledge_legacy() -> None:
    runtime.update_config(lambda cfg: cfg.__setitem__("legacyReviewed", time.strftime("%Y-%m-%d")))


def login_for_clone(root) -> "str | None":
    """The account an editor works as for this clone: the binding of the clone's city in
    the shared settings, read fresh on every call (when the hub rebinds the city while the
    editor is open, the editor follows at once); the login handed over at launch
    (CITYGML_ACCOUNT) counts only for a clone without a 4dcitygml.json."""
    city = runtime.clone_city(root) if root is not None else None
    if city:
        bound = city_login(city)
        if bound:
            return bound
        cities = runtime.read_config().get("cities")
        if isinstance(cities, dict) and city in cities:
            return None   # the city is known and has no account (disconnected): nothing applies
    env = os.environ.get("CITYGML_ACCOUNT", "").strip()
    try:
        return safe_login(env) if env and load_account(env) else None
    except ValueError:
        return None


# ---- what earlier versions left on this computer ----

def _global_helper_values() -> list:
    """Every global credential helper for github.com (the key may hold several values)."""
    if not runtime.git_exe():
        return []
    try:
        r = runtime.git(None, "config", "--global", "--get-all", "credential.https://github.com.helper", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []


def _is_our_legacy_helper(value: str) -> bool:
    """True only for the exact helper an earlier version wrote (a plain-text store at our path)."""
    if not value.startswith("store "):
        return False
    m = re.search(r"--file=(\S+)", value)
    if not m:
        return False
    legacy = runtime.legacy_credentials_path()
    target = m.group(1).strip("'\"")
    candidates = {str(legacy), legacy.as_posix(), "~/" + legacy.name}
    return target in candidates or Path(os.path.expanduser(target)) == legacy


def machine_identity() -> dict:
    """The computer's global Git identity (shown on the settings screen, never changed)."""
    return {key: runtime.git_output(None, "config", "--global", "--get", f"user.{key}", timeout=10)
            for key in ("name", "email")}


def legacy_traces() -> dict:
    """What an earlier version may have left behind, for the one-time hand-over screen."""
    traces = {
        "legacyAuth": runtime.legacy_auth_path().is_file(),
        "plainStore": runtime.legacy_credentials_path().is_file(),
        "globalHelper": any(_is_our_legacy_helper(v) for v in _global_helper_values()),
        "machineIdentity": machine_identity(),
    }
    traces["any"] = bool(traces["legacyAuth"] or traces["plainStore"] or traces["globalHelper"])
    return traces


def migrate_legacy_token(fetch_status=None) -> "str | None":
    """Move the single token of earlier versions into an account file.

    fetch_status(token) -> (HTTP status, {"login", "id", "name"} or None). Returns the
    login, or None when there was nothing to move. The old file is removed only after
    a successful move or when GitHub answered 401 (the token is dead); being offline
    changes nothing."""
    legacy = runtime.legacy_auth_path()
    token = str(runtime.read_json(legacy).get("token") or "")
    if not token:
        return None
    code, user = (fetch_status or runtime.github_user_status)(token)
    login = None
    if user and user.get("login"):
        try:
            save_account(user["login"], token, user.get("id"), user.get("name") or "")
            login = safe_login(user["login"])
        except ValueError:
            login = None
    if login or code == 401:
        try:
            legacy.unlink()
        except OSError:
            pass
    return login


def remove_legacy_traces() -> list:
    """Remove only what an earlier version of these tools wrote. Returns what was removed."""
    removed = []
    legacy = runtime.legacy_credentials_path()
    ours = [v for v in _global_helper_values() if _is_our_legacy_helper(v)]
    if runtime.git_exe() and ours:
        try:
            # only the values that are ours; other helpers on the same key stay
            r = runtime.git(None, "config", "--global", "--unset-all", "credential.https://github.com.helper",
                            r"^store .*" + re.escape(legacy.name), timeout=10)
            if r.returncode == 0:
                removed.append("globalHelper")
        except (OSError, subprocess.SubprocessError):
            pass
    still_referenced = any(_is_our_legacy_helper(v) for v in _global_helper_values())
    if legacy.is_file() and not still_referenced:
        try:
            legacy.unlink()
            removed.append("plainStore")
        except OSError:
            pass
    return removed


# ---- this computer's GitHub (the CLI's sign-in), only on explicit request ----

def gh_hosts_file() -> Path:
    base = os.environ.get("GH_CONFIG_DIR")
    if base:
        return Path(base) / "hosts.yml"
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "GitHub CLI" / "hosts.yml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else runtime.home() / ".config") / "gh" / "hosts.yml"


def machine_login_from_config() -> str:
    """The login the GitHub CLI is signed in with, read from its hosts file only.

    Nothing is sent anywhere: this labels the "use this computer's GitHub" button;
    the token is read (machine_token) only after that button is pressed."""
    if os.environ.get("CITYGML_HUB_NO_GH") == "1":
        return ""
    try:
        lines = gh_hosts_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_github = False
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 0:
            in_github = raw.strip().rstrip(":") == "github.com"
            continue
        if in_github:
            m = re.match(r"^\s*user:\s*(\S+)\s*$", raw)
            if m:
                try:
                    return safe_login(m.group(1))
                except ValueError:
                    return ""
    return ""


def machine_token(run=subprocess.run) -> str:
    """The token of the GitHub CLI's current sign-in, or '' (no CLI, not signed in)."""
    import shutil
    if os.environ.get("CITYGML_HUB_NO_GH") == "1" or not shutil.which("gh"):
        return ""
    try:
        r = run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


# ---- the process environment: nothing from the person's shell may steer git ----

GIT_ENV_OVERRIDES = (
    # identity: these outrank every config file, so the clone-local noreply name would be lost
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL",
    # credentials: an askpass program (VS Code's terminal sets one) can answer with the
    # computer's GitHub sign-in even though the credential helpers are reset
    "GIT_ASKPASS", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
    # transport: an SSH command or proxy from the shell would authenticate as the person's other identity
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND",
    # repository routing and config files: a shell that exports these would point git elsewhere
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
    "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
)


def scrub_git_env(env=None) -> dict:
    """Remove the shell's git overrides from env (the process environment by default) and
    forbid interactive prompts, so git uses only the clone's config and the account's store.

    Returns the environment that was scrubbed (a copy when env is given)."""
    target = os.environ if env is None else dict(env)
    for key in list(target):
        if key in GIT_ENV_OVERRIDES or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            target.pop(key, None)
    target["GIT_TERMINAL_PROMPT"] = "0"
    return target


# ---- error translation ----

def is_org_restriction(code: int, data: dict) -> bool:
    """A 403 caused by an organization that has not approved this OAuth app."""
    return code == 403 and ORG_RESTRICTION_MARK in str((data or {}).get("message") or "")
