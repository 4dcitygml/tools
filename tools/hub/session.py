#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The hub's session: the account side (the OAuth device flow, the account chosen for the
city, its fork) and the process state of one city, its clone, sync and setup (S13: split out of
hub/app.py, code moved unchanged; the Hub is passed in as `Session.hub_class`)."""
from __future__ import annotations

import os
import threading
import time
import urllib.error
from pathlib import Path

import runtime
import accounts
import git_sync
import shortcuts

APP_DIR = Path(__file__).resolve().parent   # preset.json lies next to the hub
tr = runtime.translator("hub")   # server-generated text of the hub in the display language


# --------------------------------------------------------------------------
# Initial setup (GUI shown when there is no clone; same approach as attr_editor)
# --------------------------------------------------------------------------


def load_preset() -> dict:
    """preset.json next to the launcher (distribution defaults such as oauthClientId)."""
    return runtime.read_json(APP_DIR / "preset.json")


# --------------------------------------------------------------------------
# GitHub authentication (OAuth device flow) — #86
#
# A beginner's Mac has neither gh nor git credentials. To finish authentication
# with **just a button and an 8-digit code**, without opening a terminal, the
# device flow is implemented with the standard library.
#   1. POST /login/device/code       → get the user_code (8 digits) and the verification URL
#   2. The user enters the code in the browser and approves (the only human step)
#   3. Poll POST /login/oauth/access_token every `interval` seconds → token
# client_id is public information (the device flow needs no client_secret). Provide
# it via oauthClientId in preset.json or the CITYGML_OAUTH_CLIENT_ID environment
# variable. If unset, fall back to gh.
# --------------------------------------------------------------------------
OAUTH_SCOPE = "public_repo"  # Necessary and sufficient for fork/push/PR on public repos (revert to "repo" when going private)
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


def oauth_client_id() -> str:
    return str(load_preset().get("oauthClientId") or os.environ.get("CITYGML_OAUTH_CLIENT_ID", ""))


class AuthManager:
    """The account of this hub process: the one its city is bound to (hub-v1.2.1).

    One process serves one city, so one bound account. Nothing is used before the
    person has chosen an account on the account screen (a saved one, this computer's
    GitHub CLI sign-in, or a new device-flow sign-in); the choice is remembered per
    city in the shared settings and is never made silently. The computer's own Git
    configuration and credential helpers are left alone.
    """

    # The screen polls the state every 2 seconds, so queries to GitHub are throttled
    # with a TTL (avoiding API rate limits and a sluggish screen).
    USER_TTL = 20
    MACHINE_TTL = 300

    def __init__(self, session: "Session") -> None:
        self.session = session
        self.lock = threading.RLock()
        self.user_code = ""
        self.verify_url = ""
        self.waiting = False
        self.error: "str | None" = None
        self.certificate_report: "str | None" = None
        self._chosen: "str | None" = None   # an account chosen before the city is known (setup)
        self._user = runtime.Memo()      # GitHub's answer for (login, token)
        self._machine = runtime.Memo()
        self._legacy: "dict | None" = None
        self._migrated = False

    # ---- binding to the city ----
    @property
    def city(self) -> "str | None":
        return self.session.city

    @property
    def login(self) -> "str | None":
        """The account this hub works as: the city's binding in the shared settings, read each
        time, so a rebind by another hub or tool is followed at once (V2, D-3c5: the hub kept its
        own copy); before the city is known, the choice made on the setup screen."""
        return accounts.city_login(self.city) if self.city else self._chosen

    @login.setter
    def login(self, value: "str | None") -> None:
        self._chosen = value   # with a city, the binding itself is written (_bind, disconnect)

    @property
    def clone_root(self) -> "Path | None":
        return self.session.hub.root if self.session.hub is not None else None

    def attach(self) -> None:
        """Adopt the account recorded for the session's city (at start; nothing is chosen silently)."""
        self._user.clear()
        if self.login:
            self._apply_identity()
        threading.Thread(target=self._migrate_legacy, name="citygml-auth-migrate", daemon=True).start()

    def _migrate_legacy(self) -> None:
        """Move the single token of earlier versions into an account file (needs one API call)."""
        if self._migrated:
            return
        self._migrated = True
        try:
            accounts.migrate_legacy_token()
        except Exception:
            pass

    def _apply_identity(self) -> None:
        root = self.clone_root
        if not root or not self.login:
            return
        rec = accounts.load_account(self.login) or {}
        accounts.apply_clone_identity(root, self.login, rec.get("id"))

    def clone_opened(self) -> None:
        """A clone is served from now on: record the binding when the account was chosen
        before the clone existed (setup) and the city has none yet, and write the identity
        into the clone. A binding the city already has wins (the settings file is the owner)."""
        if self.city and self._chosen and not accounts.city_login(self.city):
            accounts.bind_city_login(self.city, self._chosen)
        self._apply_identity()

    def _bind(self, login: str, token: str) -> None:
        with self.lock:
            self.login = login   # the token is the account's stored one (save_account ran first)
            self._user.clear()
            self.waiting, self.user_code, self.error = False, "", None
            self.certificate_report = None
        if self.city:
            accounts.bind_city_login(self.city, login)
        self._apply_identity()
        self.session.account_changed()

    def token(self) -> str:
        """The bound account's token, read from its store (no copy in memory, V2)."""
        return accounts.token_for(self.login) if self.login else ""

    def user(self) -> "dict | None":
        """Authenticated user (TTL cache). A token GitHub rejects unbinds the account."""
        login = self.login
        token = accounts.token_for(login) if login else ""
        fresh, user = self._user.get((login, token))
        if fresh:
            return user
        code, user = runtime.github_user_status(token) if token else (0, None)
        with self.lock:
            if (self.login, self.token()) != (login, token):
                # the binding changed while GitHub was asked: that answer is not the new binding's
                return self._user.get((self.login, self.token()))[1]
        if token and code == 401:
            self._drop_dead_account(login, token)
        with self.lock:
            # unchanged binding, or unbound meanwhile (the account was just dropped): remember the
            # answer so the screen's polling does not ask GitHub again within the TTL
            if (self.login, self.token()) == (login, token) or self.login is None:
                self._user.set(user, self.USER_TTL, key=(self.login, self.token()))
        return user

    def _drop_dead_account(self, login, token) -> None:
        """Forget the account whose token GitHub rejected — only if it is still the bound one."""
        with self.lock:
            if (self.login, self.token()) != (login, token):
                return
            self.login = None
            self.error = tr("hub.err_auth_revoked",
                            "GitHub no longer accepts the connection of @{login} (revoked or expired). "
                            "It was removed from this computer. Choose one of the options on this "
                            "screen: a new sign-in with the 8-digit code, or another account.",
                            login=login or "")
        if login:
            accounts.delete_account(login)
        if self.city:
            accounts.bind_city_login(self.city, None)

    def forget_user(self) -> None:
        self._user.clear()

    @staticmethod
    def _save_failed(exc: Exception) -> str:
        where = str(runtime.auth_dir())
        return tr("hub.err_account_save_failed",
                  "The connection could not be saved on this computer ({where}: {reason}). "
                  "Check that the folder can be written to, then choose again.", where=where, reason=str(exc))

    # ---- the choices on the account screen ----
    def use_saved(self, login: str) -> "str | None":
        """Use a stored account for this city. Returns an error message or None."""
        try:
            login = accounts.safe_login(login)
        except ValueError:
            return tr("hub.err_account_unknown", "That account is not saved on this computer. Choose one of the options on this screen.")
        token = accounts.token_for(login)
        if not token:
            return tr("hub.err_account_unknown", "That account is not saved on this computer. Choose one of the options on this screen.")
        code, user = runtime.github_user_status(token)
        if code == 401:
            accounts.delete_account(login)
            return tr("hub.err_auth_revoked",
                      "GitHub no longer accepts the connection of @{login} (revoked or expired). "
                      "It was removed from this computer. Choose one of the options on this "
                      "screen: a new sign-in with the 8-digit code, or another account.", login=login)
        if not user:
            return tr("hub.err_auth_check_failed",
                      "GitHub could not be reached to check the connection; nothing was changed. "
                      "Check the internet connection and press the same choice again.")
        try:
            accounts.save_account(user["login"], token, user.get("id"), user.get("name") or "")
        except OSError as e:
            return self._save_failed(e)
        if user["login"] != login:
            # Renamed or differently cased: keep one file per account. On a case-insensitive
            # file system (macOS, Windows) the two names are already the same file.
            try:
                same = os.path.samefile(accounts.account_path(login), accounts.account_path(user["login"]))
            except OSError:
                same = False
            if not same:
                accounts.delete_account(login)
        self._bind(user["login"], token)
        return None

    def use_machine(self) -> "str | None":
        """Use this computer's GitHub CLI sign-in (only on an explicit click)."""
        token = accounts.machine_token()
        if not token:
            return tr("hub.err_machine_missing",
                      "This computer's GitHub CLI is not signed in (any more). Choose one of the other options.")
        code, user = runtime.github_user_status(token)
        if not user:
            return tr("hub.err_auth_check_failed",
                      "GitHub could not be reached to check the connection; nothing was changed. "
                      "Check the internet connection and press the same choice again.")
        try:
            accounts.save_account(user["login"], token, user.get("id"), user.get("name") or "")
        except OSError as e:
            return self._save_failed(e)
        self._bind(user["login"], token)
        return None

    def disconnect(self, delete: bool = False) -> None:
        """Unbind this city's account (and optionally delete the stored account)."""
        login = self.login
        with self.lock:
            self.login = None
            self._user.clear()
            self.waiting, self.user_code, self.error = False, "", None
            self.certificate_report = None
            root = self.clone_root
        if self.city:
            accounts.bind_city_login(self.city, None)
        if delete and login:
            accounts.delete_account(login)
        if root:
            accounts.clear_clone_identity(root)
        self.session.account_changed()

    def machine(self) -> "dict | None":
        """This computer's GitHub CLI login, read from the CLI's own config file.

        Nothing is sent to GitHub and no token is read until the person presses the
        button (use_machine). CITYGML_HUB_NO_GH=1 hides the option."""
        fresh, found = self._machine.get()
        if fresh:
            return found
        try:
            login = accounts.machine_login_from_config()
        except Exception:
            login = ""
        return self._machine.set({"login": login} if login else None, self.MACHINE_TTL)

    def legacy(self) -> dict:
        """What earlier versions left on this computer (found once per process)."""
        with self.lock:
            if self._legacy is not None:
                return self._legacy
        found = accounts.legacy_traces()
        with self.lock:
            self._legacy = found
        return found

    def review_legacy(self, remove: bool) -> list:
        """The hand-over screen was read; optionally remove what an earlier version wrote."""
        removed = []
        if remove:
            removed = accounts.remove_legacy_traces()
        accounts.acknowledge_legacy()
        with self.lock:
            self._legacy = None
        return removed

    def state(self) -> dict:
        user = self.user() if self.login else None
        legacy = self.legacy()
        if not self.login:
            self.machine()
        with self.lock:
            return {
                "clientId": bool(oauth_client_id()),
                "waiting": self.waiting,
                "userCode": self.user_code,
                "verifyUrl": self.verify_url,
                "error": self.error,
                "certificateReport": self.certificate_report,
                "login": (user or {}).get("login") if user else None,
                "chosen": bool(self.login),
                "bound": self.login,
                "city": self.city,
                "accounts": [a["login"] for a in accounts.list_accounts()],
                "machine": self._machine.get()[1],
                "legacy": legacy if legacy.get("any") else None,
                "legacyReviewed": bool(accounts.is_legacy_acknowledged()),
            }

    # ---- a new sign-in (device flow) ----
    def start(self) -> dict:
        cid = oauth_client_id()
        if not cid:
            raise RuntimeError(tr(
                "hub.err_no_client_id",
                "This tool's GitHub connection setting (client_id) is not configured"))
        with self.lock:
            if self.waiting:
                return {"userCode": self.user_code, "verifyUrl": self.verify_url}
            self.certificate_report = None
            self.error = None
            try:
                r = runtime.post_form(DEVICE_CODE_URL, {"client_id": cid, "scope": OAUTH_SCOPE})
            except (urllib.error.URLError, OSError) as exc:
                if runtime.is_certificate_error(exc):
                    self.certificate_report = runtime.certificate_diagnostics()
                    self.error = tr(
                        "hub.err_auth_certificate",
                        "GitHub's HTTPS certificate could not be verified. The connection was stopped "
                        "before sign-in. Please report this message and the tool version to the distributor.")
                    raise RuntimeError(self.error) from None
                raise RuntimeError(tr(
                    "hub.err_auth_offline",
                    "GitHub could not be reached to start the sign-in. Check the internet "
                    "connection and press the button again."))
            if "device_code" not in r:
                raise RuntimeError(r.get("error_description") or tr(
                    "hub.err_device_start", "Could not start connecting to GitHub"))
            self.user_code = r["user_code"]
            self.verify_url = r.get("verification_uri", "https://github.com/login/device")
            self.waiting, self.error = True, None
            args = (cid, r["device_code"], int(r.get("interval", 5)), int(r.get("expires_in", 900)))
        threading.Thread(target=self._poll, args=args, daemon=True).start()
        return {"userCode": self.user_code, "verifyUrl": self.verify_url}

    def _poll(self, cid: str, device_code: str, interval: int, expires_in: int) -> None:
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                r = runtime.post_form(ACCESS_TOKEN_URL, {
                    "client_id": cid, "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                })
            except OSError as exc:
                if runtime.is_certificate_error(exc):
                    with self.lock:
                        self.waiting = False
                        self.certificate_report = runtime.certificate_diagnostics()
                        self.error = tr(
                            "hub.err_auth_certificate",
                            "GitHub's HTTPS certificate could not be verified. The connection was stopped "
                            "before sign-in. Please report this message and the tool version to the distributor.")
                    return
                continue  # keep waiting through temporary network errors
            if r.get("access_token"):
                token = r["access_token"]
                user = runtime.github_user_status(token)[1]
                save_error = None
                if user:
                    try:
                        accounts.save_account(user["login"], token, user.get("id"), user.get("name") or "")
                    except ValueError:
                        user = None
                    except OSError as e:
                        save_error = self._save_failed(e)
                if save_error:
                    with self.lock:
                        self.waiting, self.error = False, save_error
                elif user:
                    self._bind(user["login"], token)
                else:
                    with self.lock:
                        self.waiting = False
                        self.error = tr("hub.err_auth_user_failed",
                                        "GitHub accepted the sign-in, but could not be asked who you are "
                                        "(offline?). Check the internet connection and start the sign-in again.")
                return
            err = r.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = int(r.get("interval", interval + 5))
                continue
            with self.lock:
                self.waiting = False
                self.error = {
                    "expired_token": tr("hub.err_auth_expired",
                                        "The time limit expired. Please try again."),
                    "access_denied": tr("hub.err_auth_denied",
                                        "The authorization was canceled. Please try again."),
                }.get(err, r.get("error_description") or tr(
                    "hub.err_auth_failed", "Could not connect"))
            return
        with self.lock:
            self.waiting = False
            self.error = tr("hub.err_auth_expired", "The time limit expired. Please try again.")


class Session:
    """The hub process's state for its city: the account side (AuthManager), the clone and its
    sync, the setup flow. `hub_class` is the dashboard it opens on a clone (hub/app.py's Hub)."""

    hub_class = None
    """Everything this hub process knows about the one city it serves (contract §1): the
    city, its clone (once there is one), the account the city is bound to, the clone job
    of the setup screen, the background data sync and the version check. What GitHub said
    about the account's copy of the city (fork) and about reaching the city (access) is
    remembered for the account and the city it was asked for (scope), until an event —
    account chosen or dropped, copy created — clears it."""

    def __init__(self) -> None:
        self.city: "str | None" = None
        self.hub: "Hub | None" = None
        self.account = AuthManager(self)
        self.setup = git_sync.CloneJob(clone_text, self.net_git_args, runtime.git_exe)
        self.sync = None
        self.updates = runtime.UpdateManager()
        self.fork = runtime.Memo()
        self.access = runtime.Memo()
        self._activate_lock = threading.Lock()

    # ---- the account ----
    @property
    def login(self) -> "str | None":
        return self.account.login

    def token(self) -> str:
        return self.account.token()

    def github_login(self) -> "tuple[str, str]":
        """The token and the login GitHub confirms for it; "" for both parts that are missing."""
        token = self.token()
        return token, ((self.account.user() or {}) if token else {}).get("login") or ""

    def upstream(self) -> "str | None":
        """The city's owner/repo: the clone's own city once there is a clone, else the city
        this session was started for. Fork lookup and creation used runtime.upstream_nwo()
        without a clone, which is the practice city on a manual start (S18, F3b)."""
        if self.hub is not None:
            return runtime.upstream_nwo(self.hub.root)
        return self.city or runtime.upstream_nwo()

    def scope(self) -> "tuple[str, str] | None":
        """(account, city) that GitHub's answers are remembered under, or None. The account is read from
        the settings each time: a rebind by another process changes the scope (B01)."""
        login, upstream = self.login, self.upstream()
        return (login, upstream) if login and upstream else None

    def net_git_args(self) -> list:
        """git arguments for a network command as the city's account (or anonymously)."""
        return runtime.git_args(net=True, store=accounts.store_for(self.login))

    def account_changed(self) -> None:
        """The account was chosen or dropped: what was known about its copy is stale. With
        a clone open, uploads must go to the new account's copy before anything else
        happens (an editor may be launched right after this click), so the lookup and
        the origin change run now, not in the background."""
        self.fork.clear()
        self.access.clear()
        if self.hub is not None and self.login:
            try:
                self.fork_nwo(fresh=True)
            except (urllib.error.URLError, OSError):
                pass   # offline: origin is checked again on the next lookup

    # ---- the clone ----
    def open_clone(self, root, *, sync: bool = False) -> "Hub":
        """Serve this clone from now on: remember it for its city, hand it to the account,
        and (at start) keep its main in line with the city and look for newer tools.
        Nothing is downloaded unasked."""
        root = Path(root).resolve()
        self.hub = self.hub_class(root, self)
        # the clone's own city or none (V1): a clone that names no city is not filed under the
        # city the launcher asked for
        self.city = runtime.clone_city(root)
        runtime.remember_clone(self.city, root)
        self.account.clone_opened()
        self.updates.set_min_hub(root, final=not (sync and runtime.git_exe()))
        if sync:
            if runtime.git_exe():
                self.sync = git_sync.BackgroundSync(
                    root, lambda: runtime.upstream_url(root), lambda: accounts.store_for(self.login), log=print,
                    on_done=self._city_synced).start()
            self.updates.check_async()
        return self.hub

    def _city_synced(self, state: dict) -> None:
        """After the sync: judge min_hub on the synced config, and drop what was read before."""
        hub = self.hub
        if hub is None:
            return
        self.updates.set_min_hub(hub.root, final=True)
        if state.get("state") in ("updated", "ref-moved"):
            hub.city_data_changed()

    def start(self, root, city: "str | None", *, sync: bool = True) -> None:
        """What main() knows: the city the launcher asked for and the clone found for it (or None)."""
        self.city = city
        if root is not None:
            self.open_clone(root, sync=sync)
        self.account.attach()
        if runtime.running_hub_tag():
            try:
                shortcuts.ensure_launcher()   # the person's launcher keeps up with the installed version
            except OSError:
                pass

    def try_activate(self) -> None:
        """After the setup screen's clone finished: serve it (the screen keeps polling until then).
        The first clone of a session is handled like a clone found at start: the version check
        and min_hub run, and polls arriving together open it once (S18, F6)."""
        st = self.setup
        with self._activate_lock:
            if self.hub is not None or not st.done or st.dest is None:
                return
            if runtime.has_building_data(Path(st.dest)):
                self.open_clone(st.dest, sync=True)
                return
            st.done = False
            st.error = tr("hub.err_clone_no_bldg",
                          "No building data was found in the cloned destination: the city declares "
                          "no data_dirs folder with CityGML files in its 4dcitygml.json")

    def default_dest(self) -> str:
        """Clone destination (decided without asking). Named after the city so several
        cities' folders are told apart in the file manager; only steps aside with a
        sequence number when it already exists and is not empty."""
        name = f"CityGML Data ({self.city.split('/')[-1]})" if self.city else "CityGML Data"
        return str(next_available_dest(runtime.home() / "Documents" / name))

    # ---- what GitHub knows about the account and the city ----
    def upstream_ok(self) -> "bool | None":
        """Whether the city repository is reachable (None when no account is chosen). Asked
        from the setup screen's polling, so a "no" is kept for 5 seconds; a "yes" is kept
        until the account changes (access disappearing mid-way is not expected)."""
        scope = self.scope()
        if scope is None:
            return None
        fresh, val = self.access.get(scope)
        if val:
            return True
        if fresh:
            return False
        login, upstream = scope
        return self.access.set(upstream_access(accounts.token_for(login), upstream), 5, key=scope)

    def fork_nwo(self, *, fresh: bool = False) -> "str | None":
        """The account's copy of the city (owner/repo), or None. Kept for a minute when
        found, 10 seconds when not (the person may create it on GitHub meanwhile);
        fresh=True asks again. A found copy becomes the clone's origin while its scope is still
        the current one (an answer for an account dropped meanwhile does not move origin back)."""
        scope = self.scope()
        if scope is None:
            return None
        valid, val = self.fork.get(scope)
        if valid and not fresh:
            return val
        login, upstream = scope
        return self._remember_copy(scope, find_fork(accounts.token_for(login), login, upstream))

    def create_copy(self) -> "tuple[str | None, str | None]":
        """The setup screen's button: (owner/repo, error); the new copy is used like a found one."""
        login, upstream = self.login, self.upstream()
        if not upstream:
            return None, tr("hub.err_no_city", "This copy of the data does not name its city (4dcitygml.json repo). "
                            "Open the city's copy, or make a new one from the setup screen.")
        nwo, err = create_fork(accounts.token_for(login) if login else "", login, upstream)
        if nwo:
            self._remember_copy((login, upstream), nwo)
        return nwo, err

    def _remember_copy(self, scope: tuple, found: "str | None") -> "str | None":
        self.fork.set(found, 60 if found else 10, key=scope)
        if found and self.hub is not None and self.scope() == scope:
            self.hub.ensure_origin(found)
        return found

    def fork_or_none(self) -> "str | None":
        """The account's copy, or None when GitHub cannot be asked right now (offline)."""
        try:
            return self.fork_nwo()
        except (urllib.error.URLError, OSError):
            return None

    def copy_missing(self) -> bool:
        """GitHub answered that the bound account has no copy of the city yet (offline: False,
        the dashboard stays usable). The account screen then offers to create it."""
        scope = self.scope()
        if scope is None or self.fork_or_none():
            return False
        fresh, val = self.fork.get(scope)
        return bool(fresh and val is None)

    def setup_state(self) -> dict:
        """The whole setup-screen state (account, copy, clone job) as one payload."""
        auth = self.account.state()
        fork_checked = True
        try:
            # Both only for a verified sign-in (auth["login"]); a saved but unverified account
            # (offline) is shown as "bound", nothing is asked.
            fork = self.fork_nwo() if auth["login"] else None
            # If the fork already exists, upstream is reachable too (avoid extra API calls)
            access = True if fork else (self.upstream_ok() if auth["login"] else None)
        except (urllib.error.URLError, OSError):
            fork, access, fork_checked = None, None, False   # offline: the screen stays usable
        return {
            "ok": True,
            **auth,
            "fork": fork,
            "forkChecked": fork_checked,
            "access": access,
            "clone": self.setup.state(),
            "dest": self.default_dest(),
            "active": self.hub is not None,
            # "account": the clone exists, the city only needs its account chosen (hub-v1.2.1)
            "mode": "account" if self.hub is not None else "setup",
        }

    def settings_payload(self) -> dict:
        """Everything the settings screen shows (hub-v1.2.1): effective values, read-only paths."""
        auth = self.account.state()
        root = self.hub.root if self.hub is not None else None
        return {
            "ok": True,
            "city": self.city,
            "login": auth.get("login"),
            "chosen": auth.get("chosen"),
            "fork": self.fork_or_none() if auth.get("login") else None,
            "clone": str(root) if root else None,
            "identity": accounts.clone_identity(root) if root else {"name": "", "email": ""},
            "machineIdentity": accounts.machine_identity(),
            "accounts": accounts.list_accounts(),
            "hubTag": runtime.running_hub_tag(),
            "update": {k: v for k, v in self.updates.snapshot().items() if not k.startswith("_")},
            "paths": {
                "config": str(runtime.config_path()),
                "auth": str(runtime.auth_dir()),
                "tools": str(runtime.tools_dir()),
                "hubs": str(runtime.hubs_dir()),
            },
            "legacy": auth.get("legacy"),
        }



def upstream_access(token: str, upstream: str) -> bool:
    """Whether this token can reach the city repository upstream (owner/repo). Always 200 for a public repo."""
    code, _ = runtime.github_api(f"/repos/{upstream}", token)
    return code == 200


def find_fork(token: str, login: str, upstream: str, name: "str | None" = None) -> "str | None":
    """login's fork of the city repository upstream (owner/repo), or None if absent: the
    repository login/<name> (the city's name unless told) counts only when GitHub names this
    city as its parent (D-3a1: a fork of another city with the same name was adopted, origin
    repointed to it and the push went there)."""
    code, data = runtime.github_api(f"/repos/{login}/{name or upstream.split('/')[-1]}", token)
    parent = str(((data or {}).get("parent") or {}).get("full_name") or "") if code == 200 else ""
    if data.get("fork") and parent.lower() == upstream.lower():
        return data.get("full_name")
    return None


def create_fork(token: str, login: "str | None", upstream: str) -> "tuple[str | None, str | None]":
    """Create login's fork of the city repository upstream (or return the existing one).
    Returns (owner/repo, error message). The session knows the login (it used to be fetched
    again here, C7). Fork creation is asynchronous, so wait briefly and confirm the fork
    materialized."""
    if not token or not login:
        return None, tr("hub.err_connect_first", "Connect to GitHub first")
    existing = find_fork(token, login, upstream)
    if existing:
        return existing, None
    # GitHub's fork request is idempotent: it answers with login's existing fork of this city
    # (whatever it is called), or names the new one (renamed when the plain name is taken)
    code, data = runtime.github_api(f"/repos/{upstream}/forks", token, method="POST", payload={})
    if code not in (200, 201, 202):
        msg = runtime.api_error(code, data)
        if accounts.is_org_restriction(code, data):
            msg = tr("hub.err_org_restricted",
                     "The organization that hosts this city has not approved this tool yet. "
                     "Ask an organization owner to grant it access (GitHub → Settings → "
                     "Applications → this app → Organization access). You can keep editing "
                     "meanwhile; sending waits until then.")
        elif code == 404:
            msg = tr("hub.err_upstream_unreachable",
                     "The source data repository was not found on GitHub. It may "
                     "not be published yet — contact the data maintainer")
        return None, tr("hub.err_fork_failed", "Could not create the copy: {reason}", reason=msg)
    name = str(data.get("full_name") or "").split("/")[-1] or None
    for _ in range(20):  # wait up to 40 seconds for the fork to materialize
        nwo = find_fork(token, login, upstream, name)
        if nwo:
            return nwo, None
        time.sleep(2)
    return None, tr("hub.err_fork_failed", "Could not create the copy: {reason}",
                    reason=tr("hub.err_fork_pending", "GitHub has not finished the copy yet; try again in a minute."))


# Settings screen (hub-v1.2.1): the effective values of THIS city, item by item.
# There is no free-form editing of the settings file on purpose (each item has
# invariants: the clone must belong to the city, the account must have a stored
# connection); every change goes through an action that keeps them. The file's
# location is shown for people who want to look at it.


def next_available_dest(base: Path) -> Path:
    """Return the next candidate that avoids non-empty destinations and never overwrites partial data."""
    cand = base
    n = 2
    while cand.exists() and (not cand.is_dir() or any(cand.iterdir())):
        cand = base.with_name(f"{base.name}{n}")
        n += 1
    return cand


_CLONE_TEXTS = {
    "running": ("hub.err_clone_running", "A clone is already running"),
    "no_git": ("hub.err_git_missing_setup", "git was not found. Prepare git by following the setup guide"),
    "bad_url": ("hub.err_bad_repo_url", "The repository URL format is invalid"),
    "not_empty": ("hub.err_dest_not_empty", "The destination is not empty: {dest}"),
    "start": ("hub.clone_start", "Clone started: {url}"),
    "done": ("hub.clone_done", "Clone finished"),
    "failed": ("hub.err_clone_failed", "git clone failed (exit {code})"),
}


def clone_text(event: str, **params) -> str:
    """The hub's wording for the clone job's events (git_sync.CloneJob)."""
    key, default = _CLONE_TEXTS[event]
    return tr(key, default, **params)
