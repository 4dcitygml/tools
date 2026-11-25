#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/range_zip.py: a remote ZIP read by Range with the standard library's zipfile (W3).
The own parser it replaces had no CRC check; zipfile's is kept, and names stay inside."""
from __future__ import annotations

import io
import re
import tempfile
import threading
import unittest
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts import range_zip


def archive(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class RangeServer:
    """One archive at /a.zip, answering Range requests only (as a distribution bucket does)."""

    def __init__(self, data: bytes):
        self.data, self.requests = data, []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                m = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
                server.requests.append(self.headers.get("Range"))
                if not m:
                    self.send_response(200); self.end_headers(); self.wfile.write(server.data); return
                start, end = int(m.group(1)), min(int(m.group(2)), len(server.data) - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(server.data)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                self.wfile.write(server.data[start:end + 1])

            def log_message(self, *a):
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/a.zip"

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


class TestRangeZip(unittest.TestCase):
    def serve(self, data):
        s = RangeServer(data)
        self.addCleanup(s.close)
        return s

    def test_lists_and_extracts_the_chosen_members(self):
        payload = {"pkg/udx/bldg/a.gml": b"<a/>" * 1000, "pkg/codelists/c.xml": b"<c/>", "pkg/big.bin": b"x" * 3_000_000}
        s = self.serve(archive(payload))
        with range_zip.open_zip(s.url) as z, tempfile.TemporaryDirectory() as d:
            self.assertEqual(sorted(z.namelist()), sorted(payload))
            written = range_zip.extract(z, ["pkg/codelists/c.xml", "pkg/udx/bldg/a.gml"], d, prefix="")
            self.assertEqual([p.read_bytes() for p in written], [payload["pkg/udx/bldg/a.gml"], payload["pkg/codelists/c.xml"]])
        self.assertTrue(all(r and r.startswith("bytes=") for r in s.requests))   # never the whole archive
        self.assertLess(len(s.requests), 6)

    def test_a_damaged_member_is_refused(self):
        data = bytearray(archive({"m.gml": b"hello world" * 50}))
        info = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("m.gml")
        start = info.header_offset + 30 + len("m.gml")
        data[start + 3] ^= 0xFF   # flip a byte of the compressed data
        s = self.serve(bytes(data))
        with range_zip.open_zip(s.url) as z, tempfile.TemporaryDirectory() as d:
            with self.assertRaises((zipfile.BadZipFile, zlib.error)):
                range_zip.extract(z, ["m.gml"], d)

    def test_a_name_that_leaves_the_folder_is_refused(self):
        s = self.serve(archive({"../escape.txt": b"x"}))
        with range_zip.open_zip(s.url) as z, tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                range_zip.extract(z, ["../escape.txt"], Path(d) / "out")
            self.assertFalse((Path(d) / "escape.txt").exists())

    def test_a_server_without_range_is_named(self):
        class NoRange(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"PK")

            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), NoRange)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close()))
        with self.assertRaises(OSError):
            range_zip.open_zip(f"http://127.0.0.1:{httpd.server_address[1]}/a.zip")


if __name__ == "__main__":
    unittest.main()
