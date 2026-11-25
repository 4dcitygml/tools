# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The city's trusted scripts (city-template .github/scripts) against the contract master
(Exchange Contract A10, v4.0.0): the publisher builds the report, the report-review job writes
it into the head's ci-report check run, and the master, the hub and the scripts read it back
from there. tests/test_report_contract.py tests the master on its own."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import city_checkouts

REPO_ROOT = Path(__file__).resolve().parents[1]
# The city side of the report contract lives in the city-template repository, expected
# as a sibling checkout named like this one (tools -> city-template; a suffixed checkout
# tools<suffix> -> city-template<suffix>). Without it these tests skip (fail in CI).
SIBLING_SUFFIX = REPO_ROOT.name.removeprefix('tools')


def sibling(repo_name):
    """The same-generation checkout of another 4dcitygml repository next to this one."""
    return REPO_ROOT.parent / (repo_name + SIBLING_SUFFIX)


CITY_TEMPLATE = sibling('city-template')
CITY_SCRIPTS = CITY_TEMPLATE / '.github' / 'scripts'
if not CITY_SCRIPTS.is_dir():
    city_checkouts.missing(f"city-template checkout not found next to this repository: {CITY_TEMPLATE}")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load('report_contract', REPO_ROOT/'tools/hub/operator_explanation.py')
# The city scripts import the contract master as `operator_explanation`, reach GitHub through
# one client module (github_api.py; every test patches its `api` once) and import their other
# siblings (texts.py, ...) by name from their folder.
with patch.dict(sys.modules, {'operator_explanation': gate}), \
        patch.object(sys, 'path', [str(CITY_SCRIPTS)] + sys.path):
    client = load('github_api', CITY_SCRIPTS/'github_api.py')
    with patch.dict(sys.modules, {'github_api': client}):
        runner = load('report_gate', CITY_SCRIPTS/'check_review_report.py')
        publisher = load('report_publisher', CITY_SCRIPTS/'publish_review_report.py')


def github(stub):
    """Every GitHub call of the city scripts answered by stub (path, method='GET', payload=None)."""
    return patch.object(client, 'api', side_effect=stub)


SHA = 'a'*40
REPO = 'example-city/citygml'
from scripts import gate_result  # noqa: E402
KEYS = ['reason', 'classification', 'commit-scope', 'scope-reproducibility', 'reproduction', 'file-scope', 'schema',
        'minimal-diff', 'texture', 'structure', 'code-list', 'plausibility', 'topology', 'model']
BOT = {'slug': 'github-actions'}


def check_run(report, reason='current', cid=60, app=BOT):
    """The head's ci-report as check_review_report.verify writes it (check_result of the master)."""
    result = gate.check_result(reason, report)
    return {'id': cid, 'name': 'ci-report', 'app': app, 'status': 'completed', 'conclusion': result['conclusion'],
            'output': {'summary': result['summary'], 'text': result['text']}}


def fixture(number=7):
    pr = {'number': number, 'head': {'sha': SHA, 'repo': {'full_name': 'proposer/citygml'}, 'ref': 'edit/b-1'},
          'base': {'sha': 'b'*40, 'ref': 'main'}, 'title': 'Storeys correction', 'body': 'Field survey evidence',
          'labels': [], 'draft': True, 'state': 'open', 'node_id': 'PR_node', 'user': {'login': 'proposer'}}
    run = {'id': 50, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success', 'head_sha': SHA,
           'head_repository': {'full_name': 'proposer/citygml'}, 'head_branch': 'edit/b-1',
           'path': '.github/workflows/pr-analysis.yml'}
    inspection = {'context': gate.context(pr), 'pr': number, 'lang': 'ja', 'hasGml': True,
                  'checks': [dict(gate_result.result(k, 0), label=k) for k in KEYS]}   # the rows as the analysis writes them (S17)
    report = publisher.build_report(REPO, pr, run, inspection, {'summary.md': 'Storeys: 2 → 3'},
                                    [{'filename': 'city/udx/bldg/a.gml'}])
    return pr, run, inspection, report


class ReportContractTest(unittest.TestCase):
    def setUp(self):
        self.pr, self.run, self.inspection, self.report = fixture()
        self.runs = [self.run]
        self.check_runs = [check_run(self.report)]
        self.reviews = []

    def api(self, path):
        if path.endswith('/pulls/7'): return 200, self.pr
        if '/pulls?state=open' in path: return 200, [self.pr]
        if '/reviews?' in path: return 200, self.reviews
        if '/check-runs?' in path: return 200, {'check_runs': self.check_runs}
        if '/actions/' in path: return 200, {'workflow_runs': self.runs}
        if '/files?' in path: return 200, [{'filename': 'city/udx/bldg/a.gml'}]   # data only: no tooling label needed (D11)
        if path.endswith('/contents/4dcitygml.json'): return 404, {}   # the check's title in the city's language: en
        self.fail(path)

    def evaluate(self, api=None):
        return gate.evaluate(api or self.api, REPO, self.pr)

    def test_generated_report_is_ready_without_a_human_confirmation(self):
        result = self.evaluate()
        self.assertTrue(result['valid'])
        self.assertEqual(result['reason'], 'current')
        self.assertIn('2 → 3', result['report']['fields']['change'])

    def test_head_base_body_title_labels_invalidate_the_report(self):
        for key in ('head', 'base', 'body', 'title', 'labels'):
            with self.subTest(key=key):
                self.pr = fixture()[0]
                if key in ('head', 'base'): self.pr[key]['sha'] = 'c'*40
                elif key == 'labels': self.pr[key] = [{'name': 'texture-override'}]
                else: self.pr[key] = 'new evidence'
                self.assertFalse(self.evaluate()['valid'])
        self.pr = fixture()[0]
        self.pr['labels'] = [{'name': 'documentation'}]   # not an A9 label (T7)
        self.assertTrue(self.evaluate()['valid'])

    def test_latest_run_ignores_a_cancelled_run_with_a_higher_id(self):
        # opened and labeled queued two runs in the same second; cancel-in-progress cancelled
        # the one with the higher id (seen on a practice repository's pull request): the
        # completed run is the analysis the report matches.
        cancelled = dict(self.run, id=51, status='completed', conclusion='cancelled')
        self.runs = [cancelled, self.run]
        self.assertEqual(gate.latest_run(self.api, REPO, self.pr)['id'], 50)
        self.assertTrue(self.evaluate()['valid'])
        self.runs = [cancelled]
        with self.assertRaises(RuntimeError): gate.latest_run(self.api, REPO, self.pr)
        self.runs = [self.run, cancelled, dict(self.run, id=52, status='in_progress', conclusion=None)]
        self.assertEqual(gate.latest_run(self.api, REPO, self.pr)['id'], 52)
        self.assertEqual(self.evaluate()['reason'], 'report-pending')

    def test_rerun_attempt_or_new_run_requires_new_report(self):
        self.run['run_attempt'] = 2
        self.assertEqual(self.evaluate()['reason'], 'report-pending')
        self.run['run_attempt'] = 1; self.run['id'] = 51
        self.assertEqual(self.evaluate()['reason'], 'report-pending')

    def test_incomplete_running_ci_never_confirms(self):
        self.run['status'] = 'in_progress'
        self.assertFalse(self.evaluate()['valid'])

    def test_forged_check_or_changed_payload_rejected(self):
        self.check_runs = [check_run(self.report, app={'slug': 'someone-else'})]
        self.assertFalse(self.evaluate()['valid'])
        forged = check_run(self.report)
        forged['output']['text'] = forged['output']['text'].replace('2 → 3', '2 → 9')   # reportId no longer matches
        self.check_runs = [forged]
        self.assertIsNone(self.evaluate()['report'])

    def test_machine_report_validation_never_reads_human_reviews(self):
        def api(path):
            if '/reviews' in path or '/collaborators/' in path: self.fail('Human approvals belong to GitHub')
            return self.api(path)
        self.assertTrue(self.evaluate(api)['valid'])

    def test_api_error_blocks(self):
        self.assertFalse(self.evaluate(lambda p: (403, {}))['valid'])

    def test_same_report_has_stable_digest_but_run_changes_digest(self):
        self.assertEqual(gate.encode_report(self.report)['reportId'], self.report['reportId'])
        self.report['runAttempt'] = 2
        self.assertNotEqual(gate.encode_report(self.report)['reportId'], self.report['reportId'])

    def test_missing_summary_is_system_failure_not_human_writing_task(self):
        r = publisher.build_report(REPO, self.pr, self.run, self.inspection, {}, [])
        self.assertEqual(r['state'], 'system')

    def test_failed_data_has_auto_return_instructions(self):
        self.inspection['checks'][0]['status'] = 'fail'; self.run['conclusion'] = 'failure'
        r = publisher.build_report(REPO, self.pr, self.run, self.inspection, {'summary.md': 'delta'}, [])
        self.assertEqual(r['state'], 'fix'); self.assertIn('再検査', r['fields']['recommendation'])

    def test_invalid_data_without_diff_summary_still_requests_a_fix(self):
        self.inspection['checks'][6]['status'] = 'fail'; self.run['conclusion'] = 'failure'
        r = publisher.build_report(REPO, self.pr, self.run, self.inspection, {}, [])
        self.assertEqual(r['state'], 'fix')

    def test_lifecycle_relation_and_evidence_are_in_machine_report(self):
        from tests.test_lifecycle_manifest import event
        self.inspection['lifecycle'] = [event()]
        r = publisher.build_report(REPO, self.pr, self.run, self.inspection, {'summary.md': 'delta'}, [])
        self.assertIn('A, B → C', r['fields']['change'])
        self.assertIn('Survey record', r['fields']['change'])
        self.assertIn('自治体', r['fields']['recommendation'])
        self.assertEqual(r['lifecycle'], [event()])

    def test_missing_checks_rejected(self):
        self.inspection['checks'] = self.inspection['checks'][:-1]
        with self.assertRaises(ValueError): publisher.build_report(REPO, self.pr, self.run, self.inspection, {}, [])

    def test_the_payload_fields_clients_rely_on(self):
        # What the master (read_check, currency), the hub (ci_verdict) and the trusted check read from
        # the report, pinned against a report the city publisher really builds.
        r = publisher.build_report(REPO, self.pr, self.run, self.inspection, {'summary.md': 'delta'}, [])
        for key in ('version', 'lang', 'repo', 'pr', 'context', 'runId', 'runAttempt', 'runUrl', 'toolsRef', 'state',
                    'checks', 'lifecycle', 'heading', 'labels', 'fields', 'reportId'):
            self.assertIn(key, r, key)
        self.assertEqual((r['version'], r['lang']), (2, 'ja'))
        self.assertEqual(set(r['fields']), set(gate.FIELDS))
        self.assertIn(r['state'], ('pass', 'fix', 'system'))
        self.assertEqual([c['key'] for c in r['checks']], [row.key for row in gate_result.ROWS])
        for c in r['checks']:
            self.assertTrue({'key', 'status', 'severity', 'ran'} <= set(c), c)
            self.assertIn(c['status'], gate_result.STATUSES)
            self.assertIn(c['severity'], (gate_result.BLOCKING, gate_result.ADVISORY))
            self.assertIsInstance(c['ran'], bool)
        check = check_run(r)
        self.assertEqual(gate.read_check(check), ('current', r))   # the report survives the check run whole
        # the hub reads the row states from the head's ci-report
        hub_spec = importlib.util.spec_from_file_location('hub_payload', REPO_ROOT / 'tools/hub/app.py')
        hub = importlib.util.module_from_spec(hub_spec)
        hub_spec.loader.exec_module(hub)
        analyze = {'id': 1, 'name': 'analyze', 'app': BOT, 'status': 'completed'}
        verdict = hub.review.ci_verdict([analyze, check], self.pr)
        self.assertEqual(verdict['rows'], {c['key']: c['status'] for c in r['checks']})
        self.assertEqual((verdict['status'], verdict['state']), ('pass', 'pass'))
        # the contract names the row fields
        doc = ' '.join((REPO_ROOT / 'docs/exchange-contract.md').read_text(encoding='utf-8').split())
        self.assertIn("one `checks[]` entry per row with `key`, `status`, `severity`", doc)
        self.assertIn("and `ran` (the check reached a result", doc)
        self.assertIn("the `ci-report` check run carries them to clients (A10)", doc)

    def test_newer_analyzer_gates_are_accepted(self):
        # The trusted side runs from main and is merged before the analysis side (operator
        # handbook 6.7): a report must accept extra gates, and a failing extra gate counts.
        self.inspection['checks'].append({'key': 'new-gate', 'label': 'new-gate', 'status': 'pass', 'ran': True})
        r = publisher.build_report(REPO, self.pr, self.run, self.inspection, {'summary.md': 'delta'}, [])
        self.assertEqual(r['state'], 'pass'); self.assertIn('new-gate', r['fields']['checks'])
        self.inspection['checks'][-1]['status'] = 'fail'; self.run['conclusion'] = 'failure'
        self.assertEqual(publisher.build_report(REPO, self.pr, self.run, self.inspection, {'summary.md': 'delta'}, [])['state'], 'fix')

    def test_report_html_cannot_inject_a_hidden_payload(self):
        self.report['fields']['change'] = '<script>alert(1)</script>'
        self.assertNotIn('<script>', gate.report_summary(gate.encode_report(self.report)))

    def test_all_check_run_pages_read(self):
        def api(path):
            if '/check-runs?' in path and '&page=1' in path:
                return 200, {'check_runs': [{'id': i, 'name': 'ci-report', 'app': BOT} for i in range(100)]}
            if '/check-runs?' in path and '&page=2' in path:
                return 200, {'check_runs': self.check_runs}   # ours, the newest by id, on the second page
            return self.api(path)
        self.check_runs = [dict(check_run(self.report), id=500)]
        self.assertTrue(self.evaluate(api)['valid'])

    def test_practice_repositories_are_validated_like_any_other(self):
        # The practice repositories run the whole pipeline (Exchange Contract Part C): a stale
        # report is a stale report there too.
        self.assertFalse(gate.evaluate(self.api, '4dcitygml/sample-tokyo-station', self.pr)['valid'])   # another repository's report
        self.assertTrue(self.evaluate()['valid'])
        self.pr['body'] = 'changed after the report'
        self.assertEqual(self.evaluate()['reason'], 'report-stale')

    def test_mirrored_contract(self):
        expected = (REPO_ROOT/'tools/hub/operator_explanation.py').read_bytes()
        for repo in ['city-template', 'sample-tokyo-station', 'sample-munich-station', 'sample-newyork-station']:
            self.assertEqual(expected, (sibling(repo)/'.github/scripts/operator_explanation.py').read_bytes(), repo)


class GateTransitionsTest(unittest.TestCase):
    setUp = ReportContractTest.setUp
    api = ReportContractTest.api

    def drive(self, call=None):
        writes = []

        def api(path, method='GET', payload=None):
            if method != 'GET':
                writes.append((path, payload))
                return 201, {'id': 900 + len(writes)}
            return self.api(path)
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': REPO}), github(api):
            os.environ.pop('GITHUB_EVENT_PATH', None)   # the runner's own event is not this PR's
            (call or runner.run)()
        return writes

    def test_report_check_needs_no_human_confirmation(self):
        # review-report.yml: the head's ci-report is judged again and rewritten in place
        writes = self.drive()
        self.assertTrue(writes[0][0].endswith('/check-runs/60'), writes[0][0])
        self.assertEqual(writes[0][1]['status'], 'in_progress')
        self.assertEqual(writes[-1][1]['conclusion'], 'success')
        self.assertEqual(json.loads(writes[-1][1]['output']['text'])['report'], self.report)
        self.assertFalse(any(path == '/graphql' or '/dismissals' in path for path, _ in writes))

    def test_stale_report_blocks_without_changing_draft_or_approvals(self):
        self.pr['draft'] = False; self.pr['body'] = 'new source'
        writes = self.drive()
        self.assertEqual(writes[-1][1]['conclusion'], 'failure')
        self.assertEqual(json.loads(writes[-1][1]['output']['text'])['reason'], 'report-stale')
        self.assertFalse(any(path == '/graphql' or '/dismissals' in path for path, _ in writes))

    def test_existing_report_check_is_reused_not_recreated(self):
        # one ci-report row per head: an existing run of ours is reopened in place
        self.check_runs = [dict(check_run(self.report), id=77), dict(check_run(self.report), id=78, app={'slug': 'someone-else'})]
        writes = self.drive()
        self.assertTrue(writes[0][0].endswith('/check-runs/77'), writes[0][0])
        self.assertFalse(any(path.endswith('/check-runs') for path, _ in writes))
        self.assertEqual(writes[-1][1]['conclusion'], 'success')

    def test_the_poster_hands_its_report_to_a_new_check(self):
        # post_comments: no check on the head yet; verify() creates it and writes the report it was given
        self.check_runs = []
        writes = self.drive(lambda: self.assertTrue(runner.verify(REPO, self.pr, self.report)))
        self.assertTrue(writes[0][0].endswith('/check-runs'))
        self.assertEqual(writes[0][1]['name'], 'ci-report')
        self.assertEqual(writes[-1][1]['external_id'], self.report['reportId'])
        self.assertEqual(writes[-1][1]['conclusion'], 'success')

    def test_a_workflow_change_waits_for_the_tooling_label(self):
        def api(path):
            if '/files?' in path: return 200, [{'filename': '.github/workflows/pr-analysis.yml'}]
            return ReportContractTest.api(self, path)
        self.api = api
        writes = self.drive()
        self.assertEqual(json.loads(writes[-1][1]['output']['text'])['reason'], 'workflow-change')
        self.assertEqual(writes[-1][1]['conclusion'], 'failure')


class ReportPublicationTest(unittest.TestCase):
    def drive(self, failure=None):
        pr, run, inspection, _report = fixture()
        writes = []

        def api(path, method='GET', payload=None):
            if method != 'GET':
                writes.append((path, payload))
                if failure == 'delivery' and path.endswith('/comments'):
                    return 403, {}
                return 201, {'id': 900}
            if '/files?' in path: return 200, [{'filename': 'city/udx/bldg/a.gml'}]
            self.fail(path)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'out').mkdir()
            if failure != 'missing':
                (root/'out/inspection.json').write_text(json.dumps(inspection))
            (root/'out/summary.md').write_text('Storeys: 2 → 3')
            previous = Path.cwd()
            try:
                os.chdir(root)
                # post_comments.run() checks the run and the PR, posts the detail comments and hands
                # the report to publish() in the same process; this is that step
                with patch.dict(os.environ, {'GITHUB_REPOSITORY': REPO}), github(api):
                    if failure:
                        with self.assertRaises((RuntimeError, FileNotFoundError)): publisher.publish(REPO, pr, run, [])
                        report = None
                    else:
                        report = publisher.publish(REPO, pr, run, [])
            finally:
                os.chdir(previous)
        return writes, report

    def test_publisher_posts_the_report_for_people_and_leaves_the_check_to_verify(self):
        writes, report = self.drive()
        body = writes[-1][1]['body']
        self.assertTrue(body.startswith(gate.REPORT_MARKER))
        self.assertIn(report['reportId'], body)
        self.assertNotIn('"version"', body)   # the comment is for people; the machine form travels in the check run
        self.assertFalse(any('/check-runs' in path for path, _ in writes))

    def test_missing_evidence_or_failed_delivery_never_passes(self):
        for cause in ('missing', 'delivery'):
            with self.subTest(cause=cause):
                writes, _ = self.drive(cause)
                self.assertFalse(any(p.get('conclusion') == 'success' for _, p in writes))
                if cause == 'missing':
                    self.assertEqual(writes, [])


if __name__ == '__main__': unittest.main()
