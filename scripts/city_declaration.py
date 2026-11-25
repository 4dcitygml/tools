#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The city declaration, 4dcitygml.json: the one reader (v2.0.0, F1).

Its shape has one owner, schemas/city/4dcitygml.schema.json; every tool asks this module instead of
opening the file. read(repo) reads the working tree, read(repo, rev) the file as committed at a
revision (the analysis reads it at the PR's base). A missing, unreadable or non-object file reads
as the empty declaration; a malformed value reads as absent.

    git show REV:4dcitygml.json | python3 scripts/city_declaration.py   # the data folders, one per line

Standard library only: the hub bundle ships it next to the hub (program/city_declaration.py).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

FILE = "4dcitygml.json"
SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "city" / "4dcitygml.schema.json"


def language(value) -> str:
    """A declared `lang` (BCP 47) as a catalog language: its primary subtag, en when there is none."""
    return (value if isinstance(value, str) else "").split("-")[0].strip().lower() or "en"


@dataclass(frozen=True)
class Declaration:
    raw: dict = field(default_factory=dict)

    @property
    def repo(self) -> str:
        return str(self.raw.get("repo") or "")

    @property
    def data_dirs(self) -> "list[str]":
        """The declared data folders, relative to the repository root (no leading or trailing /)."""
        dirs = self.raw.get("data_dirs")
        return [d.strip("/") for d in dirs if isinstance(d, str) and d.strip("/")] if isinstance(dirs, list) else []

    @property
    def language(self) -> str:
        return language(self.raw.get("lang"))

    @property
    def dialect(self) -> "str | None":
        """The dialect pack the data follows (a folder under dialects/); None for plain CityGML (no pack features)."""
        value = self.raw.get("dialect")
        return value if isinstance(value, str) and value else None

    @property
    def pack(self):
        """The city's dialect pack (dialects/): the one place generic code asks (PLAIN without one)."""
        from dialects import dialect   # the source tree's root; next to the hub in the bundle
        return dialect(self.dialect)

    @property
    def resolved(self) -> dict:
        """The declaration over its pack's defaults: what the city declares wins (X1, Q2/Q3)."""
        pack = self.pack
        return {**{k: v for k, v in (("building_id", pack.building_id), ("crs", pack.crs)) if v}, **self.raw}

    @property
    def crs(self) -> "str | None":
        value = self.resolved.get("crs")
        return str(value) if value else None

    @property
    def logo(self) -> "str | None":
        value = self.raw.get("logo")
        return value if isinstance(value, str) and value else None


def parse(raw: "bytes | str | None") -> Declaration:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return Declaration(data if isinstance(data, dict) else {})


def read(repo=".", rev: "str | None" = None) -> Declaration:
    """The declaration of the working tree, or as committed at rev."""
    if rev:
        try:
            from scripts.repo_git import blob
        except ImportError:   # the hub's source tree puts scripts/ itself on sys.path
            from repo_git import blob
        return parse(blob(repo, rev, FILE))
    try:
        return parse((Path(repo) / FILE).read_bytes())
    except OSError:
        return Declaration()


if __name__ == "__main__":   # ci/city_checkouts.sh: the data folders of the declaration on stdin
    for folder in parse(sys.stdin.buffer.read()).data_dirs:
        print(folder)
