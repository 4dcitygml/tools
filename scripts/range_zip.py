#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""A remote ZIP read by HTTP Range: the standard library's zipfile over a file whose bytes come
from the server as they are needed (W3: setup_city_data parsed the ZIP structures itself).

A distribution archive is several GB; only its central directory and the chosen members are
fetched. zipfile checks every member's CRC; extract() writes a member only inside the output
folder (an archive's own names never leave it; D-4a4).
"""
from __future__ import annotations

import io
import shutil
import urllib.request
import zipfile
from pathlib import Path

BLOCK = 1 << 20   # bytes per Range request; members read in offset order reuse a block


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over an HTTP(S) URL that answers Range requests."""

    def __init__(self, url: str, opener=urllib.request.urlopen, timeout: int = 120):
        self.url, self._open, self._timeout = url, opener, timeout
        self._pos, self._block = 0, (None, b"")
        with self._open(urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=timeout) as r:
            rng = r.headers.get("Content-Range", "")
            if r.status != 206 or "/" not in rng:
                raise OSError(f"{url}: the server does not answer Range requests (HTTP {r.status})")
            self.size = int(rng.rsplit("/", 1)[1])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._pos = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence] + offset
        return self._pos

    def _fetch(self, start: int) -> bytes:
        end = min(self.size, start + BLOCK) - 1
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}"})
        with self._open(req, timeout=self._timeout) as r:
            data = r.read()
        if len(data) != end - start + 1:
            raise OSError(f"{self.url}: short Range answer at {start}")
        return data

    def readinto(self, buffer) -> int:
        if self._pos >= self.size:
            return 0
        start = self._pos - self._pos % BLOCK
        if self._block[0] != start:
            self._block = (start, self._fetch(start))
        chunk = self._block[1][self._pos - start:self._pos - start + len(buffer)]
        buffer[:len(chunk)] = chunk
        self._pos += len(chunk)
        return len(chunk)


def open_zip(url: str, opener=urllib.request.urlopen) -> zipfile.ZipFile:
    """The remote archive as a ZipFile (only its central directory is read here); its byte size
    is `archive.size`."""
    remote = RangeFile(url, opener)
    archive = zipfile.ZipFile(io.BufferedReader(remote, buffer_size=1 << 16))
    archive.size = remote.size
    return archive


def extract(archive: zipfile.ZipFile, names, outdir, prefix: str = "") -> list[Path]:
    """Write the named members under outdir (with prefix), in archive order; each must stay inside
    outdir. zipfile raises BadZipFile for a member whose CRC does not match."""
    base = Path(outdir).resolve()
    infos = sorted((archive.getinfo(n) for n in names), key=lambda i: i.header_offset)
    written = []
    for info in infos:
        target = (base / (prefix + info.filename)).resolve()
        if not target.is_relative_to(base) or target == base:
            raise SystemExit(f"Archive entry outside the output folder: {info.filename}")
        if info.is_dir():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        written.append(target)
        if len(written) % 200 == 0 or len(written) == len(infos):
            print(f"  Retrieved {len(written)}/{len(infos)}", flush=True)
    return written
