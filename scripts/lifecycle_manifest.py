#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Validate one lifecycle event. Factual relationships still require city approval."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def validate(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ['Lifecycle manifest must be an object.']
    errors = []
    required = {'version', 'eventId', 'kind', 'oldIds', 'newIds', 'reason', 'confirmedOn', 'evidence'}
    if set(data) - required - {'occurredOn'}:
        errors.append('Unknown lifecycle fields; record exactly one event per manifest.')
    if not required <= set(data):
        errors.append('Missing lifecycle fields: ' + ', '.join(sorted(required - set(data))))
    if type(data.get('version')) is not int or data['version'] != 1:
        errors.append('Lifecycle version must be 1.')
    if not isinstance(data.get('eventId'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', data['eventId']):
        errors.append('eventId must be a stable identifier (letters, digits, dot, underscore, hyphen).')
    if data.get('kind') not in ('rebuild', 'split', 'merge'):
        errors.append('kind must be rebuild, split or merge.')
    valid_ids = True
    for key in ('oldIds', 'newIds'):
        ids = data.get(key)
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x.strip() or x != x.strip() or any(c.isspace() for c in x) for x in ids):
            errors.append(f'{key} must be a nonempty list of building IDs.')
            valid_ids = False
        elif len(set(ids)) != len(ids):
            errors.append(f'{key} contains duplicate IDs.')
    if valid_ids:
        if data.get('kind') == 'split' and not (len(data['oldIds']) == 1 and len(data['newIds']) >= 2):
            errors.append('split requires one old ID and at least two new IDs.')
        if data.get('kind') == 'merge' and not (len(data['oldIds']) >= 2 and len(data['newIds']) == 1):
            errors.append('merge requires at least two old IDs and one new ID.')
    if not isinstance(data.get('reason'), str) or not data['reason'].strip():
        errors.append('reason must explain this event.')
    for key in ('confirmedOn', 'occurredOn'):
        if key == 'occurredOn' and key not in data:
            continue
        value = data.get(key)
        try:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError()
            date.fromisoformat(value)
        except ValueError:
            errors.append(f'{key} must be a real date in YYYY-MM-DD form.')
    if not any(x.startswith(('confirmedOn ', 'occurredOn ')) for x in errors) and data.get('occurredOn', '') > data.get('confirmedOn', ''):
        errors.append('occurredOn must not be later than confirmedOn.')
    evidence = data.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        errors.append('At least one evidence record is required.')
    else:
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {'title', 'url'}:
                errors.append('Evidence requires title and url.')
                continue
            if not isinstance(item['title'], str) or not item['title'].strip():
                errors.append('Evidence title must not be empty.')
            try:
                link = urlsplit(item['url']) if isinstance(item['url'], str) else None
                if not link or link.scheme not in ('https', 'http') or not link.netloc or link.username or link.password:
                    raise ValueError()
            except ValueError:
                errors.append('Evidence url must be an HTTP(S) reference without credentials.')
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='provenance/lifecycle/<event>.json, relative to the city repository')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'provenance/lifecycle/[A-Za-z0-9][A-Za-z0-9._-]*\.json', args.manifest.as_posix()):
        print('Use provenance/lifecycle/<event>.json relative to the city repository.')
        return 1
    try:
        raw = args.manifest.read_bytes()
        errors = validate(json.loads(raw))
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        print('\n'.join(errors))
        return 1
    print(f'Lifecycle-Manifest: {args.manifest.as_posix()}@sha256:{hashlib.sha256(raw).hexdigest()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
