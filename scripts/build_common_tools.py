#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Build/verify a reproducible common-tools source archive (no city data).

Release mode requires a clean Git checkout at the requested tag. Local mode
labels the archive as an unpublished preview and never invents a commit SHA.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

DIRECTORIES = ('scripts', 'schemas', 'semantics', 'ci', 'docs', 'tools')
FILES = ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'requirements.txt')
SKIP = {'__pycache__', 'dist', 'build', 'node_modules', 'venv'}
REQUIRED = {'scripts/lod0_semantic_manifest.py', 'scripts/commit_building_scope.py',
            'scripts/fetch_materials.py', 'schemas/master.xsd',
            'schemas/provenance/bulk-manifest.schema.json', 'ci/pr_analysis_main.sh',
            'docs/lod0-semantic-correction.md', *FILES}
PREFIX = 'citygml-tools'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def source_files(root):
    paths = []
    for name in FILES:
        paths.append(root / name)
    for folder in DIRECTORIES:
        base = root / folder
        if not base.is_dir() or base.is_symlink():
            raise ValueError(f'Missing or symlinked source directory: {folder}')
        for p in base.rglob('*'):
            rel = p.relative_to(root)
            if any(part.startswith('.') or part in SKIP for part in rel.parts) or p.suffix in ('.pyc', '.pyo'):
                continue
            if p.is_symlink():
                raise ValueError(f'Symlink in source payload: {rel}')
            if p.is_file():
                paths.append(p)
    found = {}
    for p in sorted(paths):
        if not p.is_file() or p.is_symlink():
            raise ValueError(f'Missing or symlinked source file: {p}')
        found[p.relative_to(root).as_posix()] = p.read_bytes()
    if not REQUIRED <= found.keys():
        raise ValueError('Required common-tools payload missing')
    return found


def build(root, output, tag=None):
    root = Path(root).resolve(); output = Path(output).resolve()
    if root == output or root in output.parents:
        raise ValueError('Archive output must be outside the source checkout')
    if tag:
        if not re.fullmatch(r'hub-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', tag):
            raise ValueError('Expected an existing hub-v<semver> release tag')
        commit = git(root, 'rev-parse', 'HEAD')
        if git(root, 'rev-parse', f'refs/tags/{tag}^{{commit}}') != commit or git(root, 'status', '--porcelain'):
            raise ValueError('Release archive requires a clean checkout at the release tag')
    else:
        commit = None
    payload = source_files(root)
    if tag and not set(payload) <= set(git(root, 'ls-files').splitlines()):
        raise ValueError('Release payload contains files outside the tagged Git tree')
    manifest = {'version': 1, 'mode': 'release' if tag else 'local-preview',
                'release_tag': tag, 'source_commit': commit,
                'files': {name: {'bytes': len(raw), 'sha256': digest(raw)} for name, raw in payload.items()}}
    payload['distribution.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted(payload.items()):
            info = zipfile.ZipInfo(f'{PREFIX}/{name}', date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, raw)
    return verify(output)


def verify(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entries')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or name != p.as_posix() or not name.startswith(PREFIX + '/'):
                raise ValueError('Unsafe archive entry')
            mode = z.getinfo(name).external_attr >> 16
            if mode & 0o170000 != 0o100000:
                raise ValueError('Non-regular archive entry')
        manifest = json.loads(z.read(PREFIX + '/distribution.json'))
        if manifest['version'] != 1 or manifest['mode'] not in ('local-preview', 'release'):
            raise ValueError('Unsupported distribution manifest')
        if manifest['mode'] == 'local-preview' and (manifest['release_tag'] is not None or manifest['source_commit'] is not None):
            raise ValueError('Local preview cannot claim a release identity')
        if manifest['mode'] == 'release' and (not re.fullmatch(r'[0-9a-f]{40}', manifest['source_commit'] or '') or not re.fullmatch(r'hub-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', manifest['release_tag'] or '')):
            raise ValueError('Missing release identity')
        expected = {f'{PREFIX}/{name}' for name in manifest['files']} | {PREFIX + '/distribution.json'}
        if set(names) != expected or not REQUIRED <= manifest['files'].keys():
            raise ValueError('Incomplete or undeclared archive contents')
        for name, record in manifest['files'].items():
            raw = z.read(PREFIX + '/' + name)
            if len(raw) != record['bytes'] or digest(raw) != record['sha256']:
                raise ValueError(f'Payload digest mismatch: {name}')
        return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build'); b.add_argument('--root', type=Path, required=True); b.add_argument('--output', type=Path, required=True)
    mode = b.add_mutually_exclusive_group(required=True); mode.add_argument('--local', action='store_true'); mode.add_argument('--release-tag')
    v = sub.add_parser('verify'); v.add_argument('archive', type=Path)
    args = p.parse_args(argv)
    try:
        result = build(args.root, args.output, args.release_tag) if args.command == 'build' else verify(args.archive)
        print(json.dumps({'mode': result['mode'], 'source_commit': result['source_commit'], 'files': len(result['files'])}))
        return 0
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as e:
        p.exit(1, f'{e}\n')


if __name__ == '__main__':
    raise SystemExit(main())
