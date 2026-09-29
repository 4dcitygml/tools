# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Machine report validation."""
from __future__ import annotations

import base64
import hashlib
import json
import re

REPORT_MARKER = '<!-- citygml-review-report -->'
FIELDS = ('change', 'evidence', 'checks', 'impact', 'recommendation')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def context(pr):
    return {'head': pr['head']['sha'], 'base': pr['base']['sha'],
            'title': pr.get('title') or '', 'body': pr.get('body') or '',
            'labels': sorted(x['name'] for x in pr.get('labels', []) if x['name'] != 'city-review')}


def pages(api, path):
    result = []
    for page in range(1, 101):
        code, data = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        if code != 200 or not isinstance(data, list):
            raise RuntimeError('Cannot read complete GitHub records')
        result.extend(data)
        if len(data) < 100:
            return result
    raise RuntimeError('GitHub pagination limit reached')


def bot(comment):
    return (comment.get('user') or {}).get('login') == 'github-actions[bot]' and (comment.get('user') or {}).get('type') == 'Bot'


def decode_report(comment):
    if not bot(comment) or not str(comment.get('body', '')).startswith(REPORT_MARKER):
        return None
    match = re.search(r'<!-- citygml-report:([A-Za-z0-9+/=]+) -->', comment['body'])
    if not match:
        return None
    try:
        data = json.loads(base64.b64decode(match[1], validate=True))
        identifier = data.pop('reportId')
        if digest(data) != identifier or data.get('version') != 1:
            return None
        data['reportId'] = identifier
        return data
    except (ValueError, KeyError, TypeError):
        return None


def encode_report(report):
    data = dict(report)
    data.pop('reportId', None)
    data['reportId'] = digest(data)
    return data


def report_comment(report):
    payload = base64.b64encode(json.dumps(report, ensure_ascii=False).encode()).decode()
    lines = [REPORT_MARKER, f'<!-- citygml-report:{payload} -->',
             '## ' + report['heading'], '']
    for key in FIELDS:
        # Report contents are data. Escape HTML/comments so source text cannot
        # hide, terminate, or impersonate the report payload when rendered.
        value = str(report['fields'][key]).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        lines.extend(['### ' + report['labels'][key], value, ''])
    lines.extend([f"[CI run {report['runId']} / {report['runAttempt']}]({report['runUrl']})",
                  f"Report-ID: `{report['reportId']}`"])
    return '\n'.join(lines)


def latest_run(api, repo, pr):
    code, data = api(f"/repos/{repo}/actions/workflows/pr-analysis.yml/runs?event=pull_request&head_sha={pr['head']['sha']}&per_page=100")
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError('Cannot read CI run')
    # A cancelled run (superseded by a newer one, or stopped by a person) produced no
    # result; it is not the analysis a report has to match, whatever its id.
    runs = [r for r in data.get('workflow_runs', [])
            if r.get('head_sha') == pr['head']['sha']
            and (r.get('head_repository') or {}).get('full_name') == pr['head']['repo']['full_name']
            and r.get('head_branch') == pr['head']['ref']
            and r.get('path', '').split('@')[0] == '.github/workflows/pr-analysis.yml'
            and r.get('conclusion') != 'cancelled']
    if not runs:
        raise RuntimeError('No matching analysis run')
    return max(runs, key=lambda r: int(r['id']))


def current_report(api, repo, pr):
    invalid = {'required': True, 'valid': False, 'reportReady': False, 'reason': 'report-pending'}
    comments = pages(api, f"/repos/{repo}/issues/{pr['number']}/comments")
    candidates = [c for c in comments if bot(c) and str(c.get('body') or '').startswith(REPORT_MARKER)]
    if not candidates:
        return invalid
    comment = max(candidates, key=lambda c: int(c['id']))
    report = decode_report(comment)
    if not report or report.get('repo') != repo or report.get('pr') != pr['number']:
        return {**invalid, 'reason': 'report-unavailable'}
    if report.get('context') != context(pr):
        return {**invalid, 'reason': 'report-stale'}
    run = latest_run(api, repo, pr)
    if (report.get('runId'), report.get('runAttempt')) != (run['id'], run.get('run_attempt', 1)) or run.get('status') != 'completed':
        return invalid
    # A valid report can describe failure. Keep it visible, but never confirm it.
    success = run.get('conclusion') == 'success' and report.get('state') == 'pass'
    return {**invalid, 'reportReady': success, 'reason': 'operator' if success else report.get('state', 'system'),
            'report': report, 'reportId': report['reportId'], 'headSha': pr['head']['sha'],
            'fields': report['fields'], 'reportUrl': comment.get('html_url', '')}


def evaluate(api, repo, pr):
    """Validate machine evidence only. GitHub owns human approval requirements."""
    try:
        result = current_report(api, repo, pr)
        return {**result, 'valid': result['reportReady'],
                'reason': 'current' if result['reportReady'] else result['reason']}
    except (RuntimeError, KeyError, TypeError, ValueError, OSError):
        return {'required': True, 'valid': False, 'reportReady': False, 'reason': 'report-unavailable'}
