# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Shared runtime of 4dcitygml's own clients (runtime-v1): every fact in one place.

Where the program is (this file's folder: `program/` in the bundle, `tools/` in the
source tree), where the person's files are (derived from HOME at call time), which git
and python run, how the shared settings file is read and written, how a clone names
its city, how GitHub is reached, and the small console / port helpers.

The hub and the editors put this folder on sys.path with one line and import it;
nothing here depends on them. Standard library only.

Tests isolate everything by pointing HOME (Windows: USERPROFILE) at a temporary
folder and, where a fake GitHub is needed, by replacing `github_api` or `request`.
See docs/client-runtime-contract.md.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

SHARED_DIR = Path(__file__).resolve().parent
WINDOWS = sys.platform.startswith("win")

# The shared modules next to this file (accounts, git_sync, shortcuts, pr_classification,
# pr_markers, pr_reason, building_identity, the i18n and themes packages) import by name. In the source
# tree the modules shared with CI live in scripts/; in the bundle they are copied next to the hub.
for _dir in (SHARED_DIR.parent / "scripts", SHARED_DIR):
    if _dir.is_dir() and str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
# the dialect packs: dialects/ at the source tree's root (next to the hub in the bundle)
if (SHARED_DIR.parent / "dialects").is_dir() and str(SHARED_DIR.parent) not in sys.path:
    sys.path.append(str(SHARED_DIR.parent))

try:
    import city_declaration  # noqa: E402  (scripts/ in the source tree, next to this file in the bundle)
    import release_tags  # noqa: E402
    from i18n import i18n_loader  # noqa: E402
    from themes import theme_loader  # noqa: E402
except ImportError as _missing:   # a partial install: say what to do, not a traceback
    sys.exit(f"A part of the tools is missing ({_missing.name}). Install the tools again with the "
             "one-line command from the city's README.")


# ---- the person's files (contract §3) ----

def home() -> Path:
    return Path.home()


def config_path() -> Path:
    """The shared settings file of all tools."""
    return home() / ".citygml_attr_editor.json"


def auth_dir() -> Path:
    """One GitHub connection per account: <login>.json and <login>.git-credentials."""
    return home() / ".citygml" / "auth"


def legacy_auth_path() -> Path:
    """Earlier versions: one token for the whole computer (migrated by the hub)."""
    return home() / ".citygml_auth.json"


def legacy_credentials_path() -> Path:
    """Earlier versions: a plain-text git credential store (offered for removal)."""
    return home() / ".citygml_git_credentials"


def _installed_tools_dir() -> "Path | None":
    """<tools> when this program runs from <tools>/citygml-hub/<hub-vX.Y.Z>/program (wherever
    that folder is); None in a source tree."""
    hubs = SHARED_DIR.parent.parent
    return hubs.parent if hubs.name == "citygml-hub" and version_tuple(SHARED_DIR.parent.name) else None


def tools_dir() -> Path:
    """The tools folder: launcher, desktop icons and the installed versions. The folder this
    program is installed in; else CITYGML_TOOLS_DIR (a test hook shared with the launcher);
    else ~/Documents/citygml-tools. Every other place is derived from this one."""
    env = os.environ.get("CITYGML_TOOLS_DIR")
    return Path(env) if env else (_installed_tools_dir() or home() / "Documents" / "citygml-tools")


def hubs_dir() -> Path:
    """<tools>/citygml-hub, one sub-folder per installed version."""
    return tools_dir() / "citygml-hub"


LAUNCHER_NAME = "citygml.ps1" if WINDOWS else "citygml.sh"


def launcher_path() -> Path:
    """The per-user launcher the desktop icons run (copied from the bundle)."""
    return tools_dir() / LAUNCHER_NAME


def launcher_source() -> Path:
    """The launcher shipped with this program (program/ in the bundle, install/ in the source tree)."""
    for cand in (SHARED_DIR / LAUNCHER_NAME, SHARED_DIR.parent / "install" / LAUNCHER_NAME):
        if cand.is_file():
            return cand
    return SHARED_DIR / LAUNCHER_NAME


def read_json(path, default=None):
    """A JSON object from a file, or default when the file is missing or broken."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default
    return data if isinstance(data, dict) else ({} if default is None else default)


# ---- versions (contract §5) ----

def version_tuple(tag) -> "tuple | None":
    """(X, Y, Z) of a hub tag (scripts/release_tags.py; a lab prerelease's suffix is ordered with
    its version: D-5-1, such a running hub had no version, so its banner never cleared)."""
    return release_tags.version_key(str(tag or "").strip(), "hub")


def running_hub_tag() -> "str | None":
    """The version this program runs as: CITYGML_HUB_TAG from the launcher, else the
    versioned folder name (<tools>/citygml-hub/<tag>/program)."""
    env = os.environ.get("CITYGML_HUB_TAG", "").strip()
    if version_tuple(env):
        return env
    parent = SHARED_DIR.parent.name
    return parent if version_tuple(parent) else None


def fetch_latest_hub(run=subprocess.run) -> str:
    """Install the newest published hub next to the running one and return its tag.

    The launcher's fetch mode does it (`citygml.sh --fetch-latest` / `citygml.ps1
    -FetchLatest`): one implementation of download → digest check → unpack, shared with
    the first install. The copy shipped with the running version is used (the person's
    copy in the tools folder may be older and lack the mode). The running version is
    never touched; the launcher starts the newest installed version at the next start."""
    script = launcher_source()
    if WINDOWS:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-FetchLatest"]
    else:
        cmd = ["/bin/bash", str(script), "--fetch-latest"]
    env = {**os.environ, "CITYGML_TOOLS_DIR": str(tools_dir())}
    r = run(cmd, capture_output=True, text=True, errors="replace", timeout=900, env=env)
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    tag = lines[-1] if lines else ""
    if r.returncode != 0 or not version_tuple(tag):
        detail = [ln for ln in ((r.stderr or "") + "\n" + (r.stdout or "")).splitlines() if ln.strip()]
        raise RuntimeError(detail[-1] if detail else
                           "The launcher could not install the latest version. Check the internet connection and press \"Get it now\" again.")
    return tag


# ---- git and python ----

_git_exe_cache: "str | None" = None


def git_exe() -> "str | None":
    """The git to use: the bundled MinGit (Windows zip) when present, else the one on PATH.

    The person's other Git work is not consulted: identity is written into the clone
    and credentials are handed over per command, so any git serves."""
    global _git_exe_cache
    if _git_exe_cache is None:
        bundled = SHARED_DIR / "PortableGit" / "cmd" / ("git.exe" if WINDOWS else "git")
        _git_exe_cache = str(bundled) if bundled.is_file() else (shutil.which("git") or "")
    return _git_exe_cache or None


def git_bundled() -> bool:
    exe = git_exe()
    return bool(exe) and Path(exe).parent.parent.name == "PortableGit"


def python_exe() -> str:
    """The python that runs child tools: the bundled portable one when present."""
    for rel in ("python.exe", "pythonw.exe", "bin/python3", "python3"):
        cand = SHARED_DIR / "PythonPortable" / rel
        if cand.is_file():
            return str(cand)
    return sys.executable


def python_bundled() -> bool:
    return "PythonPortable" in Path(python_exe()).parts


def reset_caches() -> None:
    """Forget the resolved executables (tests, or after an install)."""
    global _git_exe_cache
    _git_exe_cache = None


def git_args(*, net: bool = False, store=None) -> list:
    """Leading arguments of a git command.

    Network commands use ONLY the given credential store (the account's own file,
    handed over with -c; the token never appears in argv). The computer's helper
    list is always reset first, so a push can never pick up a keychain, manager or
    global store entry of another account. Without a store, fetches of public data
    stay anonymous and a push fails plainly."""
    args = [git_exe() or "git"]
    if not net:
        return args
    args += ["-c", "credential.helper="]
    if store and Path(store).is_file():
        helper = f"store --file={shlex.quote(Path(store).as_posix())}"
        args += ["-c", f"credential.https://github.com.helper={helper}"]
    return args


def git(root, *args: str, net: bool = False, store=None, timeout=None) -> subprocess.CompletedProcess:
    """Run git in a clone (root=None: outside any clone). Captured text output; raises
    like subprocess.run."""
    where = ["-C", str(root)] if root is not None else []
    return subprocess.run([*git_args(net=net, store=store), *where, *args],
                          capture_output=True, text=True, errors="replace", timeout=timeout)


def git_output(root, *args: str, timeout: int = 60) -> str:
    """stdout of a local git command, or '' when git is missing or the command fails."""
    if not git_exe():
        return ""
    try:
        r = git(root, *args, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


# ---- the shared settings file (contract §3): merge, atomic replace, one writer at a time ----

class FileLock:
    """An advisory lock between processes on <path>.lock: around read-modify-write of the
    settings file (two cities' hubs, an editor), and around git's work in a clone
    (git_sync.clone_lock). It blocks until the other process lets go."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._fh = None

    def __enter__(self):
        try:
            self._fh = open(self._path.with_name(self._path.name + ".lock"), "a+")
            if WINDOWS:
                import msvcrt
                self._fh.seek(0)
                while True:   # LK_LOCK gives up after about 10 seconds; a send may take longer
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError as e:
                        if getattr(e, "errno", None) not in (13, 36):   # EACCES / EDEADLOCK: still held
                            raise
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except (OSError, ImportError):
            pass   # no lock available: the atomic replace alone still prevents torn files
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                if WINDOWS:
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass
            self._fh.close()
        return False


def read_config() -> dict:
    return read_json(config_path())


def update_config(mutate) -> dict:
    """Read-modify-write of the settings file: mutate(cfg) edits the dict in place; keys
    it does not touch survive; the file is replaced atomically under an advisory lock."""
    path = config_path()
    with FileLock(path):
        current = read_json(path)
        mutate(current)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
    return current


CONFIG_VERSION = 2


def migrate_config() -> bool:
    """Bring the settings file to the current form, once (S18; maintainer, 2026-09-30).

    Version 2: the city entries (`cities[<owner/repo>]`, keys in lower case) are the one place
    for a city's clone and account; the single `repo` slot of hub-v1.0 to v1.3 ("the last used
    clone") is folded into the entry of that clone's city and removed. The last used clone is the
    city entry with the newest `last_used`. The old file is kept once as <file>.v1.bak.

    Runs at the start of the hub and of each editor, before anything else reads the settings;
    idempotent, under the settings file's lock. Returns True when it changed the file."""
    changed = []

    def mutate(cfg: dict) -> None:
        if not cfg or cfg.get("version") == CONFIG_VERSION:
            return
        backup = config_path().with_name(config_path().name + ".v1.bak")
        if not backup.exists():
            try:
                backup.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        raw = cfg.get("cities") if isinstance(cfg.get("cities"), dict) else {}
        cities: dict = {}
        for key, entry in raw.items():
            city = city_key(key)
            if city and isinstance(entry, dict):
                cities[city] = {**cities.get(city, {}), **entry}   # "Owner/Repo" and "owner/repo" were one city
        legacy = str(cfg.pop("repo", "") or "")
        if legacy and has_building_data(legacy):
            city = clone_city(legacy)
            if city:
                entry = cities.get(city, {})
                if not entry.get("repo"):
                    entry["repo"] = legacy
                entry.setdefault("last_used", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                cities[city] = entry
        cfg["cities"] = cities
        cfg["version"] = CONFIG_VERSION
        changed.append(True)

    update_config(mutate)
    return bool(changed)


def save_config(values: dict) -> dict:
    """Merge values into the settings file (never replaces keys another tool wrote)."""
    return update_config(lambda cfg: cfg.update(values))


# ---- the city (contract §1) ----



def normalize_upstream(value) -> "str | None":
    """owner/repo, https URL or ssh URL of a GitHub repository → https URL; else None."""
    v = str(value or "").strip().removesuffix(".git")
    m = (re.fullmatch(r"[\w.-]+/[\w.-]+", v)
         or re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)", v)
         or re.fullmatch(r"git@github\.com:([\w.-]+/[\w.-]+)", v))
    if not m:
        return None
    return f"https://github.com/{m.group(1) if m.lastindex else v}"


def github_nwo(value) -> "str | None":
    """owner/repo of a GitHub repository given as owner/repo, https URL or ssh URL, case kept
    (for API paths and links); None otherwise. One parser for the hub and the editors (S11: two
    lenient copies returned "a/b/" for a URL with a trailing slash)."""
    url = normalize_upstream(value)
    return url.split("github.com/", 1)[1] if url else None


def city_key(value) -> "str | None":
    """owner/repo in lower case (the settings key), or None when value is not a GitHub repo."""
    url = normalize_upstream(value)
    return url.split("github.com/")[-1].lower() if url else None


def city(root) -> "city_declaration.Declaration":
    """The clone's declaration (4dcitygml.json), read by its one reader (scripts/city_declaration.py)."""
    return city_declaration.read(root)


def city_meta(root) -> dict:
    """The clone's 4dcitygml.json over its dialect pack's defaults, as data ({} when absent)."""
    return city(root).resolved


def min_hub(root) -> "str | None":
    """The oldest hub a city accepts proposals from (4dcitygml.json `min_hub`), or None."""
    value = city(root).raw.get("min_hub")
    return str(value) if version_tuple(value) else None


def below_min_hub(root) -> "str | None":
    """The city's `min_hub` when this program is older, else None. A program that does not run
    as an installed version (a tools checkout) is never below it."""
    required, running = min_hub(root), running_hub_tag()
    return required if required and running and version_tuple(required) > version_tuple(running) else None


def require_min_hub(root) -> None:
    """Refuse to send for a city whose `min_hub` this program does not meet (maintainer,
    2026-09-30: from hub-v1.5.0 on, an older hub can view but cannot send or request a retry).
    One rule for the hub and both editors."""
    required = below_min_hub(root)
    if required:
        raise RuntimeError(tr("hub", "hub.min_hub_blocked",
                              "This city accepts proposals from {tag} on; this is {running}. Update the "
                              "editing tools from the dashboard (Get it now), then start them again.",
                              tag=required, running=running_hub_tag()))


def clone_city(root) -> "str | None":
    """The city a clone belongs to, from its own 4dcitygml.json (never from the environment)."""
    return city_key(city(root).repo)


norm_lang = city_declaration.language   # a declared `lang` as a catalog language (primary subtag, en)


def repo_lang(root) -> str:
    """The city's working language (4dcitygml.json `lang`): the language of repository-facing
    text such as PR titles and bodies. The UI language is ui_lang()."""
    return city(root).language


def data_dirs(root) -> "list[Path]":
    """The data directories 4dcitygml.json declares, resolved, existing and inside the clone."""
    root = Path(root)
    out = []
    for rel in city(root).data_dirs:
        d = root / rel
        if d.is_dir() and d.resolve().is_relative_to(root.resolve()):
            out.append(d.resolve())
    return out


def requested_city() -> "str | None":
    """The city the launcher asked for (CITYGML_UPSTREAM), or None when started by hand."""
    return city_key(os.environ.get("CITYGML_UPSTREAM", ""))


def city_of(root) -> "str | None":
    """The city a clone belongs to, as the URL of its repository: only the clone's own
    4dcitygml.json (`repo`) says it. None for a clone that names no city: it is synced to
    nothing and sends nowhere (V1; D-3c1: such a clone was reset to the practice city)."""
    return normalize_upstream(city(root).repo)


def upstream_url(root=None) -> "str | None":
    """URL of the city repository: with a clone, the clone's own city (city_of; None when it
    names none); before a clone exists, the city the launcher asked for (CITYGML_UPSTREAM; the
    launcher chooses a practice city by language when none is given, 5-14: the hub had a default
    of its own), else None. The environment never picks the city of an existing clone (V1)."""
    if root is not None:
        return city_of(root)
    return normalize_upstream(os.environ.get("CITYGML_UPSTREAM", ""))


def upstream_nwo(root=None) -> "str | None":
    url = upstream_url(root)
    return url.split("github.com/")[-1] if url else None


def tile_files(folder, pack) -> "dict[str, Path]":
    """The tiles of a buildings folder by their code: the dialect pack's tile names when the
    folder has such files (only those), else every CityGML file by its name (munich's
    690_5335_1.gml). The one rule of the editors and the hub (V7: the hub had its own copy)."""
    files = sorted(Path(folder).glob("*.gml"))
    if pack.tile_name:
        named = {m.group(1): p for p in files if (m := re.fullmatch(pack.tile_name, p.name))}
        if named:
            return named
    return {p.stem: p for p in files}


def has_building_data(root) -> bool:
    """Whether a folder is a city clone with building data: CityGML files in a data folder its
    4dcitygml.json declares (data_dirs; or inside it, where the dialect pack keeps them)."""
    try:
        return any(any(d.rglob("*.gml")) for d in data_dirs(root))
    except OSError:
        return False


def detect_repo() -> "Path | None":
    """When the program itself sits inside a clone (developers), that clone."""
    for anc in [SHARED_DIR, *SHARED_DIR.parents]:
        try:
            if has_building_data(anc):
                return anc
        except OSError:
            continue   # stat can fail on special entries near the file system root
    return None


def city_entry(city, *, forget: bool = False, **changes) -> dict:
    """The one writer of a city's entry in the settings (cities[<owner/repo>], the key in lower
    case; 3c-1: it was written in four places): changes are merged into the entry (a value None
    removes that key), or forget=True removes the whole entry. Merged, never replaced, so a key
    another tool wrote survives. Returns the entry as written ({} when forgotten or no city)."""
    key = str(city or "").lower()
    written: dict = {}

    def mutate(cfg: dict) -> None:
        cities = cfg.get("cities") if isinstance(cfg.get("cities"), dict) else {}
        if forget:
            cities.pop(key, None)
        else:
            entry = dict(cities.get(key)) if isinstance(cities.get(key), dict) else {}
            for name, value in changes.items():
                if value is None:
                    entry.pop(name, None)
                else:
                    entry[name] = value
            cities[key] = entry
            written.update(entry)
        cfg["cities"] = cities

    if key:
        update_config(mutate)
    return written


def remember_clone(city, dest) -> None:
    """Record dest as the clone of city (cities[<city>].repo), used now (last_used)."""
    # to the microsecond: last_clone() orders by it, and two clones can be opened within a second
    city_entry(city, repo=str(dest), last_used=datetime.now().astimezone().isoformat(timespec="microseconds"))


def saved_clone_for(city: str) -> "Path | None":
    """The remembered clone of city, verified against the clone's own 4dcitygml.json: a clone
    of another city is never returned, whatever the settings say."""
    cities = read_config().get("cities")
    entry = cities.get(city) if isinstance(cities, dict) and isinstance(cities.get(city), dict) else {}
    own = str(entry.get("repo") or "")
    return Path(own) if own and has_building_data(own) and clone_city(own) == city else None


def last_clone() -> "Path | None":
    """The clone used last (the city entry with the newest last_used), for tools started
    without a city."""
    cities = read_config().get("cities")
    entries = [e for e in (cities.values() if isinstance(cities, dict) else []) if isinstance(e, dict)]
    for entry in sorted(entries, key=lambda e: str(e.get("last_used") or ""), reverse=True):
        repo = str(entry.get("repo") or "")
        if repo and has_building_data(repo):
            return Path(repo)
    return None


# ---- GitHub over HTTPS (no CLI) ----

USER_AGENT = "4dcitygml-tools"
MACOS_CA_FILE = "/etc/ssl/cert.pem"


def https_context() -> ssl.SSLContext:
    """Keep Python's verified TLS; supply macOS roots when its default store is empty.

    python.org installations can have no CA file until a separate setup script is
    run. macOS already ships a root bundle, independently of that Python install.
    Never modify the user's files or SSL globals, retry insecurely, or override an
    explicit SSL_CERT_FILE / SSL_CERT_DIR. A configured lazy CA directory also
    remains authoritative. This is the OS root bundle, not the user's Keychain.
    """
    return _load_macos_roots(ssl.create_default_context())


def _load_macos_roots(context: ssl.SSLContext) -> ssl.SSLContext:
    if (sys.platform == "darwin"
            and not os.environ.get("SSL_CERT_FILE")
            and not os.environ.get("SSL_CERT_DIR")
            and not context.cert_store_stats()["x509_ca"]
            and not ssl.get_default_verify_paths().capath):
        try:
            context.load_verify_locations(cafile=MACOS_CA_FILE)
        except (OSError, ssl.SSLError):
            raise ssl.SSLCertVerificationError("The macOS root certificate bundle could not be loaded") from None
    return context



def certificate_diagnostics() -> str:
    """Public-issue report: fixed keys, enums and numbers only; never raw errors.

    Called only after a certificate failure. No network, subprocess, persistence,
    account lookup or environment dump. Unknown facts remain explicitly unknown.
    """
    facts = {
        "report": "certificate-diagnostics-v1",
        "error": "certificate-verification-failed",
        "os": {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}.get(sys.platform, "other"),
        "python": ".".join(str(n) for n in sys.version_info[:3]),
        "hub": "unknown",
        "custom_ca_file": "set" if os.environ.get("SSL_CERT_FILE") else "unset",
        "custom_ca_directory": "set" if os.environ.get("SSL_CERT_DIR") else "unset",
        "default_ca_count": "unknown",
        "default_ca_directory": "unknown",
        "tool_ca_count": "unknown",
        "tool_ca_load": "unknown",
    }
    try:
        tag = running_hub_tag()
        if isinstance(tag, str) and re.fullmatch(r"hub-v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", tag):
            facts["hub"] = tag
    except Exception:
        pass
    def count(context):
        value = context.cert_store_stats().get("x509_ca")
        return str(value) if type(value) is int and 0 <= value <= 1000000 else "unknown"
    context = None
    try:
        # Avoid create_default_context here: SSLKEYLOGFILE can cause a file write.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        facts["default_ca_count"] = count(context)
    except Exception:
        context = None
    try:
        facts["default_ca_directory"] = "present" if ssl.get_default_verify_paths().capath else "absent"
    except Exception:
        pass
    try:
        if context is None:
            raise RuntimeError("default context unavailable")
        facts["tool_ca_count"] = count(_load_macos_roots(context))
        facts["tool_ca_load"] = "ok"
    except Exception:
        facts["tool_ca_load"] = "failed"
    return "```text\n" + "\n".join(f"{key}: {value}" for key, value in facts.items()) + "\n```"


def is_certificate_error(error: BaseException) -> bool:
    """Identify direct and urllib-wrapped failures without exposing exception text."""
    return (isinstance(error, ssl.SSLCertVerificationError)
            or isinstance(getattr(error, "reason", None), ssl.SSLCertVerificationError))


def request(url: str, *, method: str = "GET", headers=None, body=None,
            timeout: int = 30) -> "tuple[int, bytes, str]":
    """One HTTP exchange → (status, body bytes, content type). An HTTP error status is
    returned, not raised; being unable to reach the host raises URLError / OSError."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=https_context()) as r:
            return r.status, r.read(), r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", e.headers.get_content_type() if e.headers else ""


def _json_body(raw: bytes) -> "dict | list":
    """The JSON of a response: an object or a list (GitHub's list endpoints — pulls,
    files, comments, reviews, commits — answer with a list). Anything else is {}."""
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, (dict, list)) else {}


def github_api(path: str, token: str, method: str = "GET", payload=None,
               timeout: int = 30) -> "tuple[int, dict | list]":
    """GitHub REST / GraphQL with a token → (HTTP status, JSON object or list)."""
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    status, raw, _ = request("https://api.github.com" + path, method=method, headers=headers,
                             body=body, timeout=timeout)
    return status, _json_body(raw)


def github_raw(path: str, token: str, timeout: int = 30) -> "tuple[int, bytes, str]":
    """A file's bytes from the GitHub Contents API → (status, bytes, content type)."""
    headers = {"Accept": "application/vnd.github.raw", "Authorization": f"Bearer {token}"}
    status, raw, ctype = request("https://api.github.com" + path, headers=headers, timeout=timeout)
    return status, (raw if status == 200 else b""), (ctype if status == 200 else "application/octet-stream")


def post_form(url: str, fields: dict, timeout: int = 15) -> dict:
    """A form POST expecting JSON (GitHub's device flow)."""
    _, raw, _ = request(url, method="POST", headers={"Accept": "application/json"},
                        body=urllib.parse.urlencode(fields).encode("utf-8"), timeout=timeout)
    return _json_body(raw)


def github_user_status(token: str) -> "tuple[int, dict | None]":
    """(HTTP status, user) for a token. 401 means GitHub no longer accepts it; 0 means the
    question could not be asked (offline) — never mistaken for a revocation."""
    if not token:
        return 0, None
    try:
        code, user = github_api("/user", token)
    except (urllib.error.URLError, OSError):
        return 0, None
    return code, (user if code == 200 and isinstance(user, dict) and user.get("login") else None)


def download(url: str, dest, timeout: int = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=https_context()) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh)


# ---- language and theme of the screens ----

def ui_lang() -> str:
    """The display language: CITYGML_LANG > the settings file > the OS locale > en."""
    return i18n_loader.resolve_lang(read_config().get("lang"))


def tr(app: str, key: str, default: str, *, lang: "str | None" = None, **params) -> str:
    """Server-generated text in the display language (or an explicit lang for
    repository-facing text). Fail-open: a broken catalog yields the English default."""
    try:
        return i18n_loader.translate(app, key, default, lang=lang or ui_lang(), **params)
    except Exception:
        s = default
        for k, v in params.items():
            s = s.replace("{" + k + "}", str(v))
        return s


def translator(app: str):
    """tr() bound to one app: `tr(key, default, **params)` in the display language."""
    def bound(key: str, default: str, **params) -> str:
        return tr(app, key, default, **params)
    return bound


def translator_in(app: str):
    """tr() bound to one app, in an explicit language: `tr_lang(lang, key, default, **params)`
    for repository-facing text (PR title and body follow the city's working language)."""
    def bound(lang: str, key: str, default: str, **params) -> str:
        return tr(app, key, default, lang=lang, **params)
    return bound


def page(data: bytes, app: str, root=None, values: "dict | None" = None) -> bytes:
    """A screen as served: the look every screen shares (tools/common.css) first in its head, the
    clone's theme (when there is a clone), the values the page needs as `window.CITYGML` (JSON,
    before any script of the page runs), the language pack, then what every screen shares
    (tools/common.js). The one way a tool hands facts, helpers and its base look to its HTML."""
    data = after_head_start(data, b'<style id="citygml-common-style">\n' + (SHARED_DIR / "common.css").read_bytes() + b"</style>\n")
    data = themed_html(data, root)
    if values is not None:
        payload = json.dumps(values, ensure_ascii=False).replace("</", "<\\/")
        data = _before_head_end(data, f'<script id="citygml-values">window.CITYGML = {payload};</script>\n'.encode("utf-8"))
    data = localized_html(data, app)
    common = (SHARED_DIR / "common.js").read_bytes().replace(b"</", b"<\\/")
    return _before_head_end(data, b'<script id="citygml-common">\n' + common + b"</script>\n")


def after_head_start(data: bytes, block: bytes) -> bytes:
    """block first in the head, so the page's own styles (and a theme after them) win over it."""
    m = re.search(rb"<head\b[^>]*>", data)
    return data[:m.end()] + b"\n" + block + data[m.end():] if m else block + data


def _before_head_end(data: bytes, block: bytes) -> bytes:
    i = data.find(b"</head>")
    return data[:i] + block + data[i:] if i >= 0 else block + data


def localized_html(data: bytes, app: str) -> bytes:
    """Inject the language pack into a page. On failure the page is served as is."""
    try:
        return i18n_loader.inject_i18n(data, app, ui_lang())
    except Exception as e:
        print(f"Ignoring language pack: {e}", file=sys.stderr)
        return data


def themed_html(data: bytes, root) -> bytes:
    """Apply the clone's theme.json to a page. An invalid theme is ignored with a warning."""
    if root is None:
        return data
    try:
        return theme_loader.inject_theme(data, theme_loader.theme_css(theme_loader.resolve_theme(root)))
    except Exception as e:   # themes are decoration, never a reason to stop
        print(f"Ignoring theme.json: {e}", file=sys.stderr)
        return data


def city_logo(root) -> "tuple[Path, str] | None":
    """The clone's logo file and its media type, or None (validated by the theme pack)."""
    try:
        return theme_loader.resolve_logo(root, city(root).logo if root is not None else None)
    except Exception:
        return None


# ---- a remembered value ----

class Memo:
    """One remembered value with an expiry, cleared on events (the only cache shape the tools use).
    The value answers for a key (for example the account and the city it was asked for): asked
    with another key, it is not fresh and has no value.

        fresh, value = memo.get(key)         # fresh is False once the value expired, was cleared or answers another key
        memo.set(value, ttl=60, key=key)     # remember for ttl seconds
        memo.clear()                         # an event made the value stale
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._key = self._value = None
        self._until = 0.0

    def get(self, key=None) -> "tuple[bool, object]":
        with self._lock:
            if key != self._key:
                return False, None
            return time.time() < self._until, self._value

    def set(self, value, ttl: float, key=None):
        with self._lock:
            self._key, self._value, self._until = key, value, time.time() + ttl
        return value

    def clear(self) -> None:
        self.set(None, 0)


# ---- the local HTTP server every tool is ----

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".xml": "application/xml; charset=utf-8", ".gml": "application/xml; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".csv": "text/csv; charset=utf-8",
}


_MIME = mimetypes.MimeTypes()   # the standard table only: never the computer's (the Windows registry)


def media_type(name) -> str:
    """The media type of a file name: the tools' own table first, then Python's standard one."""
    return (CONTENT_TYPES.get(Path(str(name)).suffix.lower()) or _MIME.guess_type(str(name))[0]
            or "application/octet-stream")


def api_error(code: int, data) -> str:
    """GitHub's message of a failed API call, else the HTTP status (V5: written out eleven times)."""
    return str((data.get("message") if isinstance(data, dict) else "") or f"HTTP {code}")


class LocalHandler(BaseHTTPRequestHandler):
    """What the hub's and the editors' request handlers share: JSON and file answers, the
    clone's logo, no access log (a token must never reach a log stream). Subclasses set
    APP_ID (the language catalog) and `root` (the clone they serve, or None)."""

    APP_ID = "hub"

    @property
    def root(self) -> "Path | None":
        return None

    def page_transform(self, data: bytes) -> bytes:
        """A hook for an app-specific rewrite of a page before theme, values and language."""
        return data

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, msg: str, status: int = 400) -> None:
        self._json({"ok": False, "error": msg}, status)

    def _bytes(self, data: bytes, content_type: str, cache: str = "private, max-age=300") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, path, values: "dict | None" = None) -> None:
        """A file of the program: a screen (with theme, values and language applied; never
        cached, so updates arrive) or a static file (cached for an hour)."""
        path = Path(path)
        if not path.is_file():
            self._error("not found", 404)
            return
        data = path.read_bytes()
        html = path.suffix.lower() == ".html"
        if html:
            data = page(self.page_transform(data), self.APP_ID, self.root, values)
        self._bytes(data, CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                    cache="no-cache" if html else "max-age=3600")

    def _city_logo(self) -> None:
        """The clone's logo (4dcitygml.json). Fail-closed: anything the theme pack does not
        accept (relative path, raster extension, under the root, at most 1 MiB) is a 404."""
        got = city_logo(self.root) if self.root is not None else None
        if got is None:
            self._error("not found", 404)
            return
        path, ctype = got
        self._bytes(path.read_bytes(), ctype, cache="max-age=3600")   # the type is fixed from the extension

    def _safe_child(self, base: Path, rel: str) -> "Path | None":
        p = (base / unquote(rel)).resolve()
        return p if p.is_relative_to(base.resolve()) else None


def serve(handler, port: int, banner: list, open_browser: bool = True) -> None:
    """Run a tool's local server on 127.0.0.1:port until Ctrl-C. banner lines may carry {url}."""
    server = ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
    url = f"http://localhost:{port}/"
    for line in banner:
        print(line.replace("{url}", url))
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting")


# ---- console, ports, messages ----

def console_safe() -> None:
    """Never let console output crash the program on a narrow Windows code page: a
    redirected stdout/stderr uses the legacy code page and the embeddable Python
    ignores PYTHONUTF8, so unencodable characters are escaped instead of raising."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def public_message(exc: Exception) -> str:
    """An exception's text without the home folder (it carries the OS user name)."""
    text = str(exc)
    return text.replace(str(home()), "~") if text else text


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


# The editors' first ports: the hub starts them there (or on the next free one) and a
# hand-started editor uses them by default (V8: written in the hub and in each editor)
EDITOR_PORTS = {"attr_editor": 8765, "tex_editor": 8766}


def free_port(start: int, step: int = 2, tries: int = 20, accept=None) -> int:
    """First port from start (stepping by step) that nothing listens on, or that accept(port)
    takes as it is (the hub: its own city's editor already listening). The one port search."""
    port = start
    for _ in range(tries):
        if not port_open(port) or (accept is not None and accept(port)):
            return port
        port += step
    return port


# ---- Tool updates (hub-v1.2.0): check automatically, fetch on request, apply at the next start ----
# The version of the hub is decided by 4dcitygml/tools releases (cities distribute no
# code and pin no client version). At start the hub asks the releases API once; a
# newer version is announced in a banner. "Get it now" runs the launcher's fetch mode
# (fetch_latest_hub: download, SHA-256 digest check, unpack into the versioned
# folder next to the running one). Nothing is downloaded without that click, and the
# running hub is never modified; the launcher picks the newest installed version at
# the next start. A city may declare `min_hub` in 4dcitygml.json: an older hub can view
# but cannot send or request a retry (require_min_hub, from hub-v1.5.0 on); the
# banner says why and stays. It is judged when the clone opens and again after the sync,
# since the sync may bring the city's new min_hub.
def releases_api() -> str:
    """The latest release the launcher installs: CITYGML_RELEASES_API, else CITYGML_TOOLS_REPO's, else
    4dcitygml/tools (the same order as install/release/citygml.sh and citygml.ps1; D10: the check
    used to look at 4dcitygml/tools while "Get it now" fetched from the overridden repository)."""
    if os.environ.get("CITYGML_RELEASES_API"):
        return os.environ["CITYGML_RELEASES_API"]
    repo = os.environ.get("CITYGML_TOOLS_REPO") or ""
    repo = repo if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) else "4dcitygml/tools"
    return f"https://api.github.com/repos/{repo}/releases/latest"


def latest_hub_release(release) -> "dict | None":
    """{tag, notesUrl} of GitHub's latest release when it is a published hub-v release, else None.
    GitHub chooses it (5-5: the hub, the sh and the ps1 installer chose the newest each their own
    way): the hub series is the only one marked latest, and a prerelease never is."""
    if not isinstance(release, dict) or release.get("draft") or not version_tuple(release.get("tag_name")):
        return None
    return {"tag": release["tag_name"], "notesUrl": release.get("html_url") or ""}


def fetch_releases(url: "str | None" = None) -> dict:
    status, raw, _ = request(url or releases_api(), headers={"Accept": "application/vnd.github+json"}, timeout=20)
    if status != 200:
        raise RuntimeError(f"GitHub releases API answered HTTP {status}")   # rate limit, outage: named, not "up to date"
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


class UpdateManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "running": running_hub_tag(), "latest": None, "available": False, "checked": False,
            "notesUrl": "", "minHub": None, "minHubOk": True, "minHubFinal": False, "error": "",
            "fetch": {"state": "idle", "tag": None, "message": ""},
        }

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def set_min_hub(self, clone_root, final: bool = False) -> None:
        """Judge the city's min_hub against the running version: locally, without the network.
        Called when the clone opens and (final) after the sync."""
        required = min_hub(clone_root)
        self._set(minHub=required, minHubOk=below_min_hub(clone_root) is None,
                  minHubFinal=final or self._state["minHubFinal"])

    def check_async(self, fetch_json=None) -> None:
        def work():
            try:
                latest = latest_hub_release((fetch_json or fetch_releases)())
            except Exception as e:  # offline etc.: the running version stays; min_hub is judged anyway
                self._set(checked=True, error=str(e))
                return
            running = version_tuple(self._state["running"])
            newest = version_tuple(latest["tag"]) if latest else None
            available = bool(newest and (running is None or newest > running))
            fetch = dict(self._state["fetch"])
            if latest and (hubs_dir() / latest["tag"] / "program" / "hub.py").is_file() and available:
                fetch = {"state": "done", "tag": latest["tag"], "message": ""}
            self._set(checked=True, latest=latest["tag"] if latest else None,
                      notesUrl=(latest or {}).get("notesUrl", ""), available=available, fetch=fetch)
        threading.Thread(target=work, name="citygml-update-check", daemon=True).start()

    def fetch_async(self, fetch=None) -> dict:
        """Start the launcher's fetch of the newest version (fetch_latest_hub); the
        state reports running / done / failed. fetch is a test seam."""
        with self._lock:
            tag = self._state.get("latest")
            if self._state["fetch"]["state"] == "running":
                return dict(self._state["fetch"])
            if not tag:
                return {"state": "failed", "tag": None, "message": "No newer version is known"}
            self._state["fetch"] = {"state": "running", "tag": tag, "message": ""}

        def work():
            try:
                got = (fetch or fetch_latest_hub)()
                self._set(fetch={"state": "done", "tag": got or tag, "message": ""})
            except Exception as e:
                self._set(fetch={"state": "failed", "tag": tag, "message": public_message(e)})
        threading.Thread(target=work, name="citygml-update-fetch", daemon=True).start()
        return {"state": "running", "tag": tag, "message": ""}
