# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The report contract (Exchange Contract A10): the report, its check run, and currency."""
from __future__ import annotations

import hashlib
import json

REPORT_MARKER = '<!-- citygml-review-report -->'
FIELDS = ('change', 'evidence', 'checks', 'impact', 'recommendation')
# The labels CI reads (Exchange Contract A9): the only labels of a report's context.
LABELS = ('texture-override', 'identity-review', 'tooling')
TEXT_LIMIT = 65535   # characters of a check run's output.text


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def context(pr):
    return {'head': pr['head']['sha'], 'base': pr['base']['sha'],
            'title': pr.get('title') or '', 'body': pr.get('body') or '',
            'labels': sorted(x['name'] for x in pr.get('labels', []) if x['name'] in LABELS)}


def pages(api, path, key=None):
    """Every record of a paged GitHub listing; `key` names the list of an object listing
    (check_runs, workflow_runs)."""
    result = []
    for page in range(1, 101):
        code, data = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        items = data.get(key) if key and isinstance(data, dict) else data
        if code != 200 or not isinstance(items, list):
            raise RuntimeError('Cannot read complete GitHub records')
        result.extend(items)
        if len(items) < 100:
            return result
    raise RuntimeError('GitHub pagination limit reached')


def bot(comment):
    return (comment.get('user') or {}).get('login') == 'github-actions[bot]' and (comment.get('user') or {}).get('type') == 'Bot'


def encode_report(report):
    data = dict(report)
    data.pop('reportId', None)
    data['reportId'] = digest(data)
    return data


def report_summary(report):
    """The report for people: the ci-report check's output.summary and the report comment."""
    lines = ['## ' + report['heading'], '']
    for key in FIELDS:
        # Report contents are data. Escape HTML/comments so source text cannot
        # hide, terminate, or impersonate other content when rendered.
        value = str(report['fields'][key]).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        lines.extend(['### ' + report['labels'][key], value, ''])
    lines.extend([f"[CI run {report['runId']} / {report['runAttempt']}]({report['runUrl']})",
                  f"Report-ID: `{report['reportId']}`"])
    return '\n'.join(lines)


def check_result(reason, report):
    """The ci-report check's verdict, the one owner of its fields (A10): `reason` as carried,
    `conclusion` (success only for a current report whose state is pass), `external_id`, and
    the output's `summary` (for people) and `text` (JSON version 2). A report that would not
    fit the check run is not attached: the verdict is then `unavailable`. The localized
    output.title is the caller's."""
    text = json.dumps({'version': 2, 'reason': reason, 'report': report}, ensure_ascii=False, separators=(',', ':'))
    if len(text) > TEXT_LIMIT:
        return check_result('unavailable', None)
    return {'reason': reason,
            'conclusion': 'success' if reason == 'current' and report and report.get('state') == 'pass' else 'failure',
            'external_id': report['reportId'] if report else 'none',
            'summary': (report_summary(report) + '\n\n' if report else '') + f'Result: `{reason}`',
            'text': text}


def read_check(check):
    """(reason, report) of a ci-report check run written by the trusted workflows; (None, None)
    for anything else. A report is taken only whole: version 2 and its reportId."""
    if not check or check.get('name') != 'ci-report' or (check.get('app') or {}).get('slug') != 'github-actions':
        return None, None
    try:
        data = json.loads((check.get('output') or {}).get('text') or '')
        report = data['report']
        if data.get('version') != 2 or not isinstance(data.get('reason'), str):
            return None, None
        if report is not None:
            body = {k: v for k, v in report.items() if k != 'reportId'}
            if body.get('version') != 2 or digest(body) != report['reportId']:
                return None, None
        return data['reason'], report
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, None


def report_check(api, repo, sha):
    """The head's latest ci-report check run written by the trusted workflows, or None."""
    runs = pages(api, f'/repos/{repo}/commits/{sha}/check-runs?check_name=ci-report&filter=all', 'check_runs')
    mine = [c for c in runs if (c.get('app') or {}).get('slug') == 'github-actions']
    return max(mine, key=lambda c: int(c['id'])) if mine else None


def latest_run(api, repo, pr):
    runs = pages(api, f"/repos/{repo}/actions/workflows/pr-analysis.yml/runs?event=pull_request&head_sha={pr['head']['sha']}",
                 'workflow_runs')
    # A cancelled run (superseded by a newer one, or stopped by a person) produced no
    # result; it is not the analysis a report has to match, whatever its id.
    runs = [r for r in runs
            if r.get('head_sha') == pr['head']['sha']
            and (r.get('head_repository') or {}).get('full_name') == pr['head']['repo']['full_name']
            and r.get('head_branch') == pr['head']['ref']
            and r.get('path', '').split('@')[0] == '.github/workflows/pr-analysis.yml'
            and r.get('conclusion') != 'cancelled']
    if not runs:
        raise RuntimeError('No matching analysis run')
    return max(runs, key=lambda r: int(r['id']))


def currency(api, repo, pr, report):
    """The one judgement whether `report` (context, runId, runAttempt) is the current report of
    `pr` (A10): the PR as GitHub holds it now -> (live PR, reason)."""
    code, live = api(f"/repos/{repo}/pulls/{pr['number']}")
    if code != 200 or not isinstance(live, dict):
        raise RuntimeError('Cannot read the PR')
    head = live['head']['sha']
    if live.get('state') != 'open' or head != pr['head']['sha'] or (report and report['context']['head'] != head):
        return live, 'head-changed'
    # Checks attach to commits, not PRs: never share a verdict between two PRs on one head.
    if sum(p['head']['sha'] == head for p in pages(api, f'/repos/{repo}/pulls?state=open')) != 1:
        return live, 'duplicate-head'
    if report is None:
        return live, 'report-pending'
    if report['context'] != context(live):
        return live, 'report-stale'
    run = latest_run(api, repo, live)
    if (report['runId'], report['runAttempt']) != (run['id'], run.get('run_attempt', 1)) or run.get('status') != 'completed':
        return live, 'report-pending'
    return live, 'current'


def evaluate(api, repo, pr):
    """The head's ci-report judged again: its report, the reason, and whether it lets the PR pass."""
    try:
        report = read_check(report_check(api, repo, pr['head']['sha']))[1]
        if report and (report.get('repo'), report.get('pr')) != (repo, pr['number']):
            report = None
        live, reason = currency(api, repo, pr, report)
        return {'valid': check_result(reason, report)['conclusion'] == 'success', 'reason': reason,
                'report': report, 'reportId': report['reportId'] if report else 'none', 'live': live}
    except (RuntimeError, KeyError, TypeError, ValueError, OSError):
        return {'valid': False, 'reason': 'unavailable', 'report': None, 'reportId': 'none', 'live': None}
