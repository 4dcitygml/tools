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
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

SHARED_DIR = Path(__file__).resolve().parent
WINDOWS = sys.platform.startswith("win")

# The shared modules next to this file (accounts, git_sync, shortcuts, pr_classification,
# the i18n and themes packages) import by name. In the source tree pr_classification.py
# lives in scripts/ (shared with CI); in the bundle it is copied next to the hub.
for _dir in (SHARED_DIR.parent / "scripts", SHARED_DIR):
    if _dir.is_dir() and str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

try:
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

_HUB_TAG_RE = re.compile(r"^hub-v(\d+)\.(\d+)\.(\d+)$")


def version_tuple(tag) -> "tuple | None":
    m = _HUB_TAG_RE.match(str(tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


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

class _FileLock:
    """An advisory lock around read-modify-write of the settings file, so two tools (two
    cities' hubs, an editor) writing at the same moment cannot lose each other's keys."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._fh = None

    def __enter__(self):
        try:
            self._fh = open(self._path.with_name(self._path.name + ".lock"), "a+")
            if WINDOWS:
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
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
    with _FileLock(path):
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


def save_config(values: dict) -> dict:
    """Merge values into the settings file (never replaces keys another tool wrote)."""
    return update_config(lambda cfg: cfg.update(values))


# ---- the city (contract §1) ----

DEFAULT_CITY_URL = "https://github.com/4dcitygml/sample-tokyo-station"   # the practice city


def normalize_upstream(value) -> "str | None":
    """owner/repo, https URL or ssh URL of a GitHub repository → https URL; else None."""
    v = str(value or "").strip().removesuffix(".git")
    m = (re.fullmatch(r"[\w.-]+/[\w.-]+", v)
         or re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)", v)
         or re.fullmatch(r"git@github\.com:([\w.-]+/[\w.-]+)", v))
    if not m:
        return None
    return f"https://github.com/{m.group(1) if m.lastindex else v}"


def city_key(value) -> "str | None":
    """owner/repo in lower case (the settings key), or None when value is not a GitHub repo."""
    url = normalize_upstream(value)
    return url.split("github.com/")[-1].lower() if url else None


def city_meta(root) -> dict:
    """The clone's 4dcitygml.json (data only; {} when absent)."""
    return read_json(Path(root) / "4dcitygml.json")


def clone_city(root) -> "str | None":
    """The city a clone belongs to, from its own 4dcitygml.json (never from the environment)."""
    return city_key(city_meta(root).get("repo", ""))


def requested_city() -> "str | None":
    """The city the launcher asked for (CITYGML_UPSTREAM), or None when started by hand."""
    return city_key(os.environ.get("CITYGML_UPSTREAM", ""))


def upstream_url(root=None, *, ignore_env: bool = False) -> str:
    """URL of the city repository. Priority: CITYGML_UPSTREAM > the clone's 4dcitygml.json
    > the clone's git remote `upstream` > the practice city. ignore_env=True asks for the
    clone's own city only (the sync target: a launcher for another city must never
    rewrite this clone)."""
    if not ignore_env:
        env = normalize_upstream(os.environ.get("CITYGML_UPSTREAM", ""))
        if env:
            return env
    if root:
        declared = normalize_upstream(city_meta(root).get("repo", ""))
        if declared:
            return declared
        remote = normalize_upstream(git_output(root, "remote", "get-url", "upstream", timeout=10))
        if remote:
            return remote
    return DEFAULT_CITY_URL


def upstream_nwo(root=None, *, ignore_env: bool = False) -> str:
    return upstream_url(root, ignore_env=ignore_env).split("github.com/")[-1]


def has_building_data(root) -> bool:
    """Whether a folder is a city clone with building data (PLATEAU */udx/bldg or the
    data_dirs declared in 4dcitygml.json)."""
    root = Path(root)
    try:
        if any(root.glob("*/udx/bldg")):
            return True
    except OSError:
        return False
    return any(
        (root / str(rel)).is_dir() and any((root / str(rel)).glob("*.gml"))
        for rel in (city_meta(root).get("data_dirs") or [])
        if isinstance(rel, str)
    )


def detect_repo() -> "Path | None":
    """When the program itself sits inside a clone (developers), that clone."""
    for anc in [SHARED_DIR, *SHARED_DIR.parents]:
        try:
            if has_building_data(anc):
                return anc
        except OSError:
            continue   # stat can fail on special entries near the file system root
    return None


def remember_clone(city, dest) -> None:
    """Record dest as the clone of city (cities[<city>].repo) and as the last used clone
    (repo). The city's entry is merged, never replaced: its account binding and any key
    another tool wrote survive."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def mutate(cfg: dict) -> None:
        cities = cfg.get("cities") if isinstance(cfg.get("cities"), dict) else {}
        if city:
            entry = cities.get(city) if isinstance(cities.get(city), dict) else {}
            cities[city] = {**entry, "repo": str(dest), "last_used": stamp}
        cfg["cities"] = cities
        cfg["repo"] = str(dest)

    update_config(mutate)


def saved_clone_for(city: str) -> "Path | None":
    """The remembered clone of city, verified against the clone's own 4dcitygml.json.

    Falls back to the legacy single `repo` entry once, migrating it into `cities`
    when it turns out to belong to this city. A clone of another city is never
    returned, whatever the settings say."""
    cfg = read_config()
    cities = cfg.get("cities") if isinstance(cfg.get("cities"), dict) else {}
    entry = cities.get(city) if isinstance(cities.get(city), dict) else {}
    own = str(entry.get("repo") or "")
    for cand in (own, str(cfg.get("repo") or "")):
        if cand and has_building_data(cand) and clone_city(cand) == city:
            if cand != own:
                remember_clone(city, cand)   # migrate the legacy entry
            return Path(cand)
    return None


def last_clone() -> "Path | None":
    """The last used clone (legacy single slot), for tools started without a city."""
    saved = str(read_config().get("repo") or "")
    return Path(saved) if saved and has_building_data(saved) else None


# ---- GitHub over HTTPS (no CLI) ----

USER_AGENT = "4dcitygml-tools"


def request(url: str, *, method: str = "GET", headers=None, body=None,
            timeout: int = 30) -> "tuple[int, bytes, str]":
    """One HTTP exchange → (status, body bytes, content type). An HTTP error status is
    returned, not raised; being unable to reach the host raises URLError / OSError."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as fh:
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


def page(data: bytes, app: str, root=None, values: "dict | None" = None) -> bytes:
    """A screen as served: the clone's theme (when there is a clone), the values the
    page needs as `window.CITYGML` (JSON, before any script of the page runs), then the
    language pack. The one way a tool hands facts to its HTML."""
    data = themed_html(data, root)
    if values is not None:
        payload = json.dumps(values, ensure_ascii=False).replace("</", "<\\/")
        block = f'<script id="citygml-values">window.CITYGML = {payload};</script>\n'.encode("utf-8")
        i = data.find(b"</head>")
        data = data[:i] + block + data[i:] if i >= 0 else block + data
    return localized_html(data, app)


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
        return theme_loader.resolve_logo(root)
    except Exception:
        return None


# ---- a remembered value ----

class Memo:
    """One remembered value with an expiry, cleared on events (the only cache shape the tools use).

        fresh, value = memo.get()            # fresh is False once the value expired or was cleared
        memo.set(value, ttl=60)              # remember for ttl seconds
        memo.clear()                         # an event made the value stale
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._value = None
        self._until = 0.0

    def get(self) -> "tuple[bool, object]":
        with self._lock:
            return time.time() < self._until, self._value

    def set(self, value, ttl: float):
        with self._lock:
            self._value, self._until = value, time.time() + ttl
        return value

    def clear(self) -> None:
        with self._lock:
            self._value, self._until = None, 0.0


# ---- the local HTTP server every tool is ----

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".xml": "application/xml; charset=utf-8", ".gml": "application/xml; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".csv": "text/csv; charset=utf-8",
}


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


def free_port(start: int, step: int = 2, tries: int = 20) -> int:
    """First port from start (stepping by step) that nothing listens on."""
    port = start
    for _ in range(tries):
        if not port_open(port):
            return port
        port += step
    return port
