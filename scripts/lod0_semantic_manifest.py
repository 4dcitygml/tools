#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Pilot recipe: rename CityGML 2.0 LOD0 FootPrint to RoofEdge, byte-preservingly.

This is a semantic correction requiring human evidence review, not a geometry
repair or a claim that every footprint is a roof edge. i-UR 3.1 only for now.
Generate/apply/commits/verify use the existing bulk provenance and CI flow.
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from xml.parsers import expat
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.provenance_manifest import validate, sha256_hex, manifest_ref, canonical_bytes

KIND = 'semantic-correction'
RECIPE = 'lod0-footprint-to-roofedge-v1'
SCRIPT = 'scripts/lod0_semantic_manifest.py'
NS = {'c': 'http://www.opengis.net/citygml/2.0',
      'b': 'http://www.opengis.net/citygml/building/2.0',
      'g': 'http://www.opengis.net/gml', 'u': 'https://www.geospatial.jp/iur/uro/3.1'}
FOOT = '{' + NS['b'] + '}lod0FootPrint'
ROOF = '{' + NS['b'] + '}lod0RoofEdge'


def safe_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '\\' in value or ':' in value or value != p.as_posix():
        raise ValueError('Expected a normalized relative repository path')
    return value


def plan(raw: bytes, municipality: str):
    """Return byte edits keyed by stable ID, plus explicit exclusions.

    lxml establishes the supported topology and identities; Expat locates the
    actual XML name tokens. Comments, CDATA, attributes and namespace lookalikes
    cannot become replacement targets. No XML reserialization is performed.
    """
    root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))
    info = root.getroottree().docinfo
    if info.doctype or info.encoding.upper().replace('-', '') not in ('UTF8', 'ASCII'):
        raise ValueError('Only UTF-8/ASCII XML without a DTD is supported')
    if root.tag != '{' + NS['c'] + '}CityModel':
        raise ValueError('CityGML 2.0 CityModel required')
    buildings = root.findall('./c:cityObjectMember/b:Building', NS)
    if not buildings or len(buildings) != len(root.findall('.//b:Building', NS)) or root.findall('.//b:BuildingPart', NS):
        raise ValueError('Only direct, non-part Building members are supported')
    edits, excluded, order = {}, [], []
    total_foot, total_roof = 0, 0
    seen, seen_gml = set(), set()
    for b in buildings:
        gml_id = b.get('{' + NS['g'] + '}id')
        if not gml_id or gml_id in seen_gml:
            raise ValueError('Unique gml:id required for each Building')
        seen_gml.add(gml_id)
        ids = b.xpath('./u:buildingIDAttribute/u:BuildingIDAttribute/u:buildingID/text()', namespaces=NS)
        cities = b.xpath('./u:buildingIDAttribute/u:BuildingIDAttribute/u:city/text()', namespaces=NS)
        if len(ids) != 1 or not re.fullmatch(re.escape(municipality) + r'-bldg-[0-9]+', ids[0]) or cities != [municipality]:
            raise ValueError('Exactly one matching i-UR 3.1 buildingID and municipality required')
        stable = ids[0]
        if stable in seen:
            raise ValueError('Duplicate buildingID')
        seen.add(stable); order.append(stable)
        feet, roofs = b.findall(FOOT), b.findall(ROOF)
        total_foot += len(feet); total_roof += len(roofs)
        if len(feet) > 1 or len(roofs) > 1 or (feet and roofs):
            raise ValueError('Ambiguous or coexisting FootPrint/RoofEdge properties')
        if not feet:
            excluded.append({'id': stable, 'reason': 'already-roofedge' if roofs else 'no-footprint'})
            continue
        if feet[0].attrib or len(feet[0].findall('./g:MultiSurface', NS)) != 1 or not feet[0].findall('.//g:posList', NS):
            raise ValueError('FootPrint must contain an inline MultiSurface with posList; references are unsupported')
        edits[stable] = []
    if total_foot != len(root.findall('.//b:lod0FootPrint', NS)) or total_roof != len(root.findall('.//b:lod0RoofEdge', NS)):
        raise ValueError('LOD0 property outside a direct Building property')
    parser = expat.ParserCreate(namespace_separator='|')
    stack, index = [], -1
    def record():
        pos = parser.CurrentByteIndex
        match = re.match(rb'</?(?:[A-Za-z_][\w.-]*:)?lod0FootPrint(?=[\s>])', raw[pos:])
        if not match:
            raise ValueError('Cannot locate the exact FootPrint name token')
        end = pos + match.end()
        edits[order[index]].append((end - len(b'lod0FootPrint'), end, b'lod0RoofEdge'))
    def start(name, attrs):
        nonlocal index
        if name == NS['b'] + '|Building':
            index += 1
        if name == NS['b'] + '|lod0FootPrint':
            if not stack or stack[-1] != NS['b'] + '|Building':
                raise ValueError('Unsupported FootPrint location')
            record()
        stack.append(name)
    def end(name):
        if name == NS['b'] + '|lod0FootPrint':
            record()
        stack.pop()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(raw, True)
    if any(len(v) != 2 for v in edits.values()):
        raise ValueError('Exactly one opening and closing name per target required')
    return edits, sorted(excluded, key=lambda x: x['id']), len(buildings)


def transform(raw, municipality, targets=None):
    edits, excluded, count = plan(raw, municipality)
    chosen = set(edits) if targets is None else set(targets)
    if not chosen <= set(edits):
        raise ValueError('Requested building is not an eligible FootPrint target')
    out = raw
    for start, end, replacement in sorted((e for key in chosen for e in edits[key]), reverse=True):
        out = out[:start] + replacement + out[end:]
    evidence = {'recipe': RECIPE, 'profile': 'citygml-2.0/iur-3.1',
                'allowed_paths': ['/lod0FootPrint', '/lod0RoofEdge'],
                'targets': sorted(edits), 'excluded': excluded,
                'counts': {'buildings': count, 'changed': len(edits), 'excluded': len(excluded)}}
    return out, evidence


def contract(m):
    errors = validate(m)
    if errors:
        raise ValueError('Manifest schema: ' + '; '.join(errors[:3]))
    if m['kind'] != KIND or m['builder']['script'] != SCRIPT:
        raise ValueError('Wrong manifest kind or builder script')
    if m['builder']['tools_commit'] == '0' * 40:
        raise ValueError('A real tools commit SHA is required (local snapshot permitted for local rehearsal only)')
    if len(m['products']) != 1 or not safe_path(m['products'][0]['path']).endswith('.gml'):
        raise ValueError('Exactly one GML product required')
    if m['scope'].get('edition_from') != 'iur-3.1' or m['scope'].get('edition_to') != 'iur-3.1':
        raise ValueError('This recipe does not migrate schema editions')
    if len(m['materials']) != 2 or [a.get('name') for a in m['materials']] != ['current', 'rationale']:
        raise ValueError('Exactly current and rationale materials are required')
    for material in m['materials']:
        if material.get('members') or material['bytes'] <= 0:
            raise ValueError('Use individual current GML and rationale files')
        uri = material['uri']
        if not (uri.startswith('https://') or uri.startswith('file:///') or re.fullmatch(r'git:[0-9a-f]{40}:.+', uri)):
            raise ValueError('Material URI must be HTTPS, a pinned git blob, or a local rehearsal file')
        if uri.startswith('git:'):
            safe_path(uri.split(':', 2)[2])
    expected = {'recipe': RECIPE, 'profile': 'citygml-2.0/iur-3.1'}
    if m['invocation'].get('parameters') != expected:
        raise ValueError('Unsupported recipe or profile parameters')
    if not m['plan_issue'].strip():
        raise ValueError('A plan reference is required')
    return m


def reproduce(m, raw, rationale=None):
    contract(m)
    current = m['materials'][0]
    if sha256_hex(raw) != current['sha256'] or len(raw) != current['bytes']:
        raise ValueError('Current input digest/size does not match the manifest')
    if rationale is not None:
        material = m['materials'][1]
        if len(rationale) != material['bytes'] or sha256_hex(rationale) != material['sha256']:
            raise ValueError('Rationale digest/size does not match the manifest')
    product, evidence = transform(raw, m['scope']['municipality'])
    if not evidence['targets']:
        raise ValueError('No eligible changes; do not create an empty PR')
    if canonical_bytes(evidence) != canonical_bytes(m['evidence']):
        raise ValueError('Evidence differs from the independently derived plan')
    if sha256_hex(product) != m['products'][0]['sha256'] or m['products'][0].get('buildings') != evidence['counts']['buildings']:
        raise ValueError('Product digest/count does not match the reproduced result')
    # Fixed sorted sample, entirely derived from targets. Human review stays in GitHub.
    if m['sample_audit'] != {'seed': 0, 'size': min(30, len(evidence['targets'])),
                             'ids': evidence['targets'][:30], 'result': 'pending'}:
        raise ValueError('Sample must be the declared deterministic review selection')
    return product


def build_manifest(args):
    raw, rationale = Path(args.current).read_bytes(), Path(args.rationale).read_bytes()
    product, evidence = transform(raw, args.municipality)
    def material(name, path, uri, data):
        return {'name': name, 'uri': uri or Path(path).resolve().as_uri(), 'bytes': len(data), 'sha256': sha256_hex(data)}
    m = {'schemaVersion': 1, 'kind': KIND, 'repository': args.repository,
         'scope': {'mesh': args.mesh, 'municipality': args.municipality, 'edition_from': 'iur-3.1', 'edition_to': 'iur-3.1'},
         'plan_issue': args.plan_issue,
         'materials': [material('current', args.current, args.current_uri, raw), material('rationale', args.rationale, args.rationale_uri, rationale)],
         'builder': {'tools_repo': args.tools_repo, 'tools_commit': args.tools_commit, 'script': SCRIPT},
         'invocation': {'command': ['python3', SCRIPT, 'generate', '--repository', args.repository,
                        '--mesh', args.mesh, '--municipality', args.municipality, '--current', 'current',
                        '--rationale', 'rationale', '--product', args.product, '--tools-repo', args.tools_repo,
                        '--tools-commit', args.tools_commit, '--plan-issue', args.plan_issue,
                        '--output', 'regenerated-manifest.json'], 'parameters': {'recipe': RECIPE, 'profile': 'citygml-2.0/iur-3.1'},
                        'environment': {'python': sys.version.split()[0]}},
         'products': [{'path': safe_path(args.product), 'sha256': sha256_hex(product), 'buildings': evidence['counts']['buildings']}],
         'evidence': evidence,
         'sample_audit': {'seed': 0, 'size': min(30, len(evidence['targets'])), 'ids': evidence['targets'][:30], 'result': 'pending'},
         'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    reproduce(m, raw, rationale)
    return m, product


def report(m):
    e = m['evidence']
    return '\n'.join(['# LOD0 semantic correction — pilot', '',
        '## Change', f"Rename FootPrint to RoofEdge for {len(e['targets'])} buildings; coordinates and all other bytes stay unchanged.",
        '## Evidence', 'Review the rationale material and plan Issue. Reproduction does not establish the real-world meaning of the outline.',
        '## Checks', 'Input/rationale digests, namespace and supported topology, deterministic targets, exact product bytes; existing XSD/geometry CI still applies.',
        '## Impact', 'Interpretation of the LOD0 boundary changes. Building IDs, coordinates, heights and other attributes are preserved.',
        '## Recommended action', 'Inspect the evidence and exclusions, then use standard GitHub review. No automatic approval.',
        '', f"Excluded: {len(e['excluded'])}. Recipe: {RECIPE}. Tools commit: {m['builder']['tools_commit']}.", ''])


def git(repo, *args, binary=False):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=not binary).strip() if not binary else subprocess.check_output(['git', '-C', str(repo), *args])


def cmd_commits(args, m):
    repo = Path(args.repo).resolve()
    manifest_path = Path(args.manifest).resolve()
    rel = safe_path(manifest_path.relative_to(repo).as_posix())
    if not rel.startswith('provenance/semantic-correction/'):
        raise ValueError('Manifest must be under provenance/semantic-correction/')
    product_path = repo / m['products'][0]['path']
    product_path.resolve().relative_to(repo)
    # Allow only the newly generated manifest in an otherwise clean worktree.
    status = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=all'], text=True)
    if any(line != '?? ' + rel for line in status.splitlines()):
        raise ValueError('Commit generation requires a clean worktree except for its untracked manifest')
    base = git(repo, 'rev-parse', 'HEAD')
    expected_uri = f"git:{base}:{m['products'][0]['path']}"
    if m['materials'][0]['uri'] != expected_uri:
        raise ValueError('Current material must reference the exact HEAD and product path')
    if product_path.is_symlink():
        raise ValueError('GML product cannot be a symlink')
    raw = product_path.read_bytes()
    if git(repo, 'show', f"{base}:{m['products'][0]['path']}", binary=True) != raw:
        raise ValueError('Working input differs from the recorded git blob')
    from scripts.reconstruct_minimal import building_spans
    if len(building_spans(raw)) != m['products'][0]['buildings']:
        raise ValueError('Current commit-scope parser requires core:cityObjectMember and gml:id serialization')
    expected = reproduce(m, raw)
    ref = manifest_ref(rel, manifest_path.read_bytes())
    for stable in m['evidence']['targets']:
        raw, _ = transform(raw, m['scope']['municipality'], [stable])
        product_path.write_bytes(raw)
        git(repo, 'add', '--', m['products'][0]['path'], rel)
        message = f"Correct LOD0 boundary meaning for {stable}\n\nBuilding: {stable}\nProvenance-Manifest: {ref}\nCreated-By: {SCRIPT}/{RECIPE}\n"
        subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-F', '-'], input=message.encode(), check=True)
    if product_path.read_bytes() != expected:
        raise ValueError('Committed output differs from the planned product')
    print(f"{len(m['evidence']['targets'])} building commits created; full product matches")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    g = sub.add_parser('generate')
    for key in ('repository', 'mesh', 'municipality', 'current', 'rationale', 'product', 'tools-commit', 'plan-issue', 'output'):
        g.add_argument('--' + key, required=True)
    g.add_argument('--tools-repo', default='4dcitygml/tools')
    g.add_argument('--current-uri'); g.add_argument('--rationale-uri'); g.add_argument('--apply-output'); g.add_argument('--report')
    a = sub.add_parser('apply'); a.add_argument('--manifest', required=True); a.add_argument('--input', required=True); a.add_argument('--output', required=True)
    c = sub.add_parser('commits'); c.add_argument('--manifest', required=True); c.add_argument('--repo', required=True)
    v = sub.add_parser('verify'); v.add_argument('--manifest', required=True); v.add_argument('--materials-dir', required=True)
    check = sub.add_parser('check'); check.add_argument('--manifest', required=True)
    check.add_argument('--ci', action='store_true'); check.add_argument('--tools-commit')
    args = p.parse_args(argv)
    try:
        if args.command == 'generate':
            inputs = {Path(args.current).resolve(), Path(args.rationale).resolve()}
            outputs = [Path(x).resolve() for x in (args.output, args.apply_output, args.report) if x]
            if inputs.intersection(outputs) or len(set(outputs)) != len(outputs):
                raise ValueError('Output files must be distinct and must not overwrite inputs')
            m, product = build_manifest(args)
            dest = Path(args.output); dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            if args.apply_output:
                if Path(args.apply_output).resolve() == Path(args.current).resolve():
                    raise ValueError('Do not overwrite the input')
                Path(args.apply_output).write_bytes(product)
            if args.report:
                Path(args.report).write_text(report(m), encoding='utf-8')
            print(json.dumps(m['evidence']['counts']))
            return 0
        m = contract(json.loads(Path(args.manifest).read_text(encoding='utf-8')))
        if args.command == 'check':
            if args.ci:
                if args.tools_commit != m['builder']['tools_commit']:
                    raise ValueError('City CI must use the declared trusted tools commit')
                if m['repository'] != os.environ.get('GITHUB_REPOSITORY'):
                    raise ValueError('Manifest must identify the current city repository')
                if not re.fullmatch('https://github.com/' + re.escape(m['repository']) + r'/issues/[1-9][0-9]*', m['plan_issue']):
                    raise ValueError('City CI requires a plan Issue in the current city repository')
                if any(a['uri'].startswith('file:') for a in m['materials']):
                    raise ValueError('Local file materials are only for rehearsal')
            print('manifest contract: OK')
            return 0
        if args.command == 'commits':
            return cmd_commits(args, m)
        if args.command == 'apply':
            if Path(args.input).resolve() == Path(args.output).resolve():
                raise ValueError('Do not overwrite the input')
            Path(args.output).write_bytes(reproduce(m, Path(args.input).read_bytes()))
        else:
            base = Path(args.materials_dir)
            reproduce(m, (base / 'current').read_bytes(), (base / 'rationale').read_bytes())
            print('reproduction: OK (input, rationale, targets and full product); human evidence review still required')
            print(report(m))
        return 0
    except (ValueError, KeyError, TypeError, OSError, etree.XMLSyntaxError, expat.ExpatError, subprocess.CalledProcessError) as e:
        print(f'::error::{e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
