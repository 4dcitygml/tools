#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Local review UI launched by the 4dcitygml citydb-tool plugin."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from connector import (  # noqa: E402
    ProposalInput,
    SyncConfig,
    SyncError,
    SyncPlan,
    check_pr_readiness,
    create_proposal,
    inspect_environment,
    plan_sync,
)


class SyncApplication:
    def __init__(self, config: SyncConfig):
        self.config = config
        self.csrf = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self.latest_plan: Optional[SyncPlan] = None

    def status(self) -> dict[str, Any]:
        return {
            "status": inspect_environment(self.config).__dict__,
            "csrf": self.csrf,
            "plugin": "4dcitygml 3DCityDB Sync",
            "version": "0.1.0",
        }

    def sync(self, token: str) -> dict[str, Any]:
        self._require_csrf(token)
        if not self._lock.acquire(blocking=False):
            raise SyncError("Another sync is already running.")
        try:
            self.latest_plan = plan_sync(self.config)
            return self.latest_plan.public_dict()
        finally:
            self._lock.release()

    def propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_csrf(str(payload.get("csrf", "")))
        if self.latest_plan is None:
            raise SyncError("Run Sync before preparing a pull request.")
        proposal = ProposalInput(
            building_id=str(payload.get("buildingId", "")),
            reason=str(payload.get("reason", "")),
            source=str(payload.get("source", "")),
            public_author=str(payload.get("publicAuthor", "")),
            notes=str(payload.get("notes", "")),
        )
        url = create_proposal(self.config, self.latest_plan, proposal)
        return {"url": url}

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_csrf(str(payload.get("csrf", "")))
        if self.latest_plan is None:
            raise SyncError("Run Sync before preparing a pull request.")
        building_id = str(payload.get("buildingId", ""))
        return check_pr_readiness(
            self.config, self.latest_plan, building_id
        ).public_dict()

    def _require_csrf(self, token: str) -> None:
        if not secrets.compare_digest(token, self.csrf):
            raise SyncError("The page security token is no longer valid. Reload the page.")


def make_handler(app: SyncApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "4dcitygml-citydb-sync/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send_bytes((HERE / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/app.js":
                self._send_bytes((HERE / "web" / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            elif self.path == "/styles.css":
                self._send_bytes((HERE / "web" / "styles.css").read_bytes(), "text/css; charset=utf-8")
            elif self.path == "/api/status":
                self._json(HTTPStatus.OK, app.status())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 64 * 1024:
                    raise SyncError("The request is too large.")
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/sync":
                    self._json(HTTPStatus.OK, app.sync(str(payload.get("csrf", ""))))
                elif self.path == "/api/prepare":
                    self._json(HTTPStatus.OK, app.prepare(payload))
                elif self.path == "/api/propose":
                    self._json(HTTPStatus.OK, app.propose(payload))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except (SyncError, json.JSONDecodeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # fail closed without exposing a traceback
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Sync failed: {exc}"})

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[citydb-sync] {self.address_string()} {fmt % args}", file=sys.stderr)

        def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
            self._send_bytes(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _send_bytes(
            self,
            data: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--citygml", type=Path)
    parser.add_argument("--export-file", type=Path)
    parser.add_argument("--citydb-command", default="citydb")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = SyncConfig.load(
            args.repo,
            config_file=args.config,
            citygml=args.citygml,
            export_file=args.export_file,
            citydb_command=args.citydb_command,
        )
        app = SyncApplication(config)
        if args.check:
            print(json.dumps(app.status(), ensure_ascii=False, sort_keys=True))
            return 0
        server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    except (SyncError, OSError) as exc:
        print(f"citydb sync: {exc}", file=sys.stderr)
        return 2

    actual_port = server.server_address[1]
    url = f"http://{args.host}:{actual_port}/"
    print(f"4dcitygml 3DCityDB Sync: {url}")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
