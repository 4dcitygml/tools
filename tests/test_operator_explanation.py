# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
# The city side of the report contract lives in the city-template repository, expected
# as a sibling checkout named like this one (tools -> city-template; a suffixed checkout
# tools<suffix> -> city-template<suffix>). Without it these tests skip.
SIBLING_SUFFIX = REPO_ROOT.name.removeprefix('tools')


def sibling(repo_name):
    """The same-generation checkout of another 4dcitygml repository next to this one."""
    return REPO_ROOT.parent / (repo_name + SIBLING_SUFFIX)


CITY_TEMPLATE = sibling('city-template')
CITY_SCRIPTS = CITY_TEMPLATE / '.github' / 'scripts'
if not CITY_SCRIPTS.is_dir():
    raise unittest.SkipTest(f"city-template checkout not found next to this repository: {CITY_TEMPLATE}")

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

gate = load('report_contract', REPO_ROOT/'tools/hub/operator_explanation.py')
# What the city scripts import as `operator_explanation`. Transition (2026-09-17): a city
# checkout from before the practice exemption was retired still imports `required`; it
# gets a copy of the contract that answers True, the contract itself stays as it is.
import types
_for_city = types.ModuleType('operator_explanation'); _for_city.__dict__.update(gate.__dict__)
_for_city.required = lambda repo: True
# The city scripts reach GitHub through one client module (github_api.py, review step 5);
# every test patches its `api` once. A checkout from before that step keeps the client
# inside check_review_report, which the same patch reaches through the alias below.
with patch.dict(sys.modules, {'operator_explanation':_for_city}):
    if (CITY_SCRIPTS/'github_api.py').is_file():
        client = load('github_api', CITY_SCRIPTS/'github_api.py')
        with patch.dict(sys.modules, {'github_api':client}):
            runner = load('report_gate', CITY_SCRIPTS/'check_review_report.py')
            with patch.dict(sys.modules, {'check_review_report':runner}):
                publisher = load('report_publisher', CITY_SCRIPTS/'publish_review_report.py')
    else:
        runner = load('report_gate', CITY_SCRIPTS/'check_review_report.py')
        with patch.dict(sys.modules, {'check_review_report':runner}):
            publisher = load('report_publisher', CITY_SCRIPTS/'publish_review_report.py')
        client = None


def github(stub):
    """Every GitHub call of the city scripts answered by stub (path, method='GET', payload=None)."""
    if client is not None:
        return patch.object(client, 'api', side_effect=stub)
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.object(runner, 'api', side_effect=stub))
    stack.enter_context(patch.object(publisher, 'api', side_effect=stub))
    return stack

SHA='a'*40
REPO='example-city/citygml'
KEYS=['reason','classification','commit-scope','scope-reproducibility','reproduction','freshness','file-scope','schema','minimal-diff','texture','structure','plausibility','topology','model']

def fixture(number=7):
    pr={'number':number,'head':{'sha':SHA,'repo':{'full_name':'proposer/citygml'},'ref':'edit/b-1'},
        'base':{'sha':'b'*40,'ref':'main'},'title':'Storeys correction','body':'Field survey evidence',
        'labels':[],'draft':True,'state':'open','node_id':'PR_node','user':{'login':'proposer'}}
    run={'id':50,'run_attempt':1,'status':'completed','conclusion':'success','head_sha':SHA,
         'head_repository':{'full_name':'proposer/citygml'},'head_branch':'edit/b-1','path':'.github/workflows/pr-analysis.yml'}
    inspection={'context':gate.context(pr),'pr':number,'lang':'ja','hasGml':True,
                'checks':[{'key':k,'label':k,'status':'pass'} for k in KEYS]}
    report=publisher.build_report(REPO,pr,run,inspection,{'summary.md':'Storeys: 2 → 3'},[{'filename':'city/udx/bldg/a.gml'}])
    comment={'id':100,'body':gate.report_comment(report),'user':{'login':'github-actions[bot]','type':'Bot'}}
    return pr,run,inspection,report,comment


class ReportContractTest(unittest.TestCase):
    def setUp(self):
        self.pr,self.run,self.inspection,self.report,self.comment=fixture()
        self.comments=[self.comment];self.reviews=[];self.permission='write';self.check_runs=[]
    def api(self,path):
        if '/comments?' in path:return 200,self.comments
        if '/reviews?' in path:return 200,self.reviews
        if '/check-runs?' in path:return 200,{'check_runs':self.check_runs}   # the head's ci-report runs (reused when present)
        if '/actions/' in path:return 200,{'workflow_runs':[self.run]}
        if '/collaborators/' in path:return 200,{'permission':self.permission}
        self.fail(path)
    def evaluate(self):return gate.evaluate(self.api,REPO,self.pr)
    def test_generated_report_is_ready_without_a_human_confirmation(self):
        result=self.evaluate();self.assertTrue(result['valid']);self.assertIn('2 → 3',result['fields']['change'])
    def test_report_visible_before_human_confirmation(self):
        self.reviews=[];result=self.evaluate();self.assertTrue(result['reportReady']);self.assertTrue(result['valid'])
    def test_head_base_body_title_labels_invalidate_confirmation(self):
        for key in ('head','base','body','title','labels'):
            with self.subTest(key=key):
                self.pr=fixture()[0]
                if key in ('head','base'):self.pr[key]['sha']='c'*40
                elif key=='labels':self.pr[key]=[{'name':'texture-override'}]
                else:self.pr[key]='new evidence'
                self.assertFalse(self.evaluate()['valid'])
    def test_latest_run_ignores_a_cancelled_run_with_a_higher_id(self):
        # opened and labeled queued two runs in the same second; cancel-in-progress cancelled
        # the one with the higher id (seen on a practice repository's pull request): the
        # completed run is the analysis the report matches.
        cancelled=dict(self.run,id=51,status='completed',conclusion='cancelled')
        runs=[cancelled,self.run]
        api=lambda path:(200,{'workflow_runs':runs}) if '/actions/' in path else self.api(path)
        self.assertEqual(gate.latest_run(api,REPO,self.pr)['id'],50)
        self.assertTrue(gate.current_report(api,REPO,self.pr)['reportReady'])
        runs[:]=[cancelled]
        with self.assertRaises(RuntimeError):gate.latest_run(api,REPO,self.pr)
        running=dict(self.run,id=52,status='in_progress',conclusion=None)
        runs[:]=[self.run,cancelled,running]
        self.assertEqual(gate.latest_run(api,REPO,self.pr)['id'],52)
        self.assertFalse(gate.current_report(api,REPO,self.pr)['reportReady'])
    def test_rerun_attempt_or_new_run_requires_new_report(self):
        self.run['run_attempt']=2;self.assertFalse(self.evaluate()['reportReady'])
        self.run['run_attempt']=1;self.run['id']=51;self.assertFalse(self.evaluate()['reportReady'])
    def test_incomplete_running_ci_never_confirms(self):
        self.run['status']='in_progress';self.assertFalse(self.evaluate()['valid'])
    def test_forged_bot_lookalike_or_changed_payload_rejected(self):
        self.comment['user']={'login':'proposer','type':'User'};self.assertFalse(self.evaluate()['reportReady'])
        self.comment=fixture()[4];self.comments=[self.comment]
        self.comment['body']=self.comment['body'].replace('citygml-report:', 'broken-report:')
        self.assertFalse(self.evaluate()['reportReady'])
    def test_machine_report_validation_never_reads_human_reviews(self):
        def api(path):
            if '/reviews' in path or '/collaborators/' in path:self.fail('Human approvals belong to GitHub')
            return self.api(path)
        self.assertTrue(gate.evaluate(api, REPO, self.pr)['valid'])
    def test_api_error_blocks(self):
        self.assertFalse(gate.evaluate(lambda p:(403,{}),REPO,self.pr)['valid'])
    def test_same_report_has_stable_digest_but_run_changes_digest(self):
        self.assertEqual(gate.encode_report(self.report)['reportId'],self.report['reportId'])
        self.report['runAttempt']=2;self.assertNotEqual(gate.encode_report(self.report)['reportId'],self.report['reportId'])
    def test_missing_summary_is_system_failure_not_human_writing_task(self):
        r=publisher.build_report(REPO,self.pr,self.run,self.inspection,{},[])
        self.assertEqual(r['state'],'system')
    def test_failed_data_has_auto_return_instructions(self):
        self.inspection['checks'][0]['status']='fail';self.run['conclusion']='failure'
        r=publisher.build_report(REPO,self.pr,self.run,self.inspection,{'summary.md':'delta'},[])
        self.assertEqual(r['state'],'fix');self.assertIn('再検査',r['fields']['recommendation'])
    def test_invalid_data_without_diff_summary_still_requests_a_fix(self):
        self.inspection['checks'][6]['status']='fail';self.run['conclusion']='failure'
        r=publisher.build_report(REPO,self.pr,self.run,self.inspection,{},[])
        self.assertEqual(r['state'],'fix')
    def test_lifecycle_relation_and_evidence_are_in_machine_report(self):
        from tests.test_lifecycle_manifest import event
        self.inspection['lifecycle']=[event()]
        r=publisher.build_report(REPO,self.pr,self.run,self.inspection,{'summary.md':'delta'},[])
        self.assertIn('A, B → C',r['fields']['change'])
        self.assertIn('Survey record',r['fields']['change'])
        self.assertIn('自治体',r['fields']['recommendation'])
    def test_missing_checks_rejected(self):
        self.inspection['checks']=self.inspection['checks'][:-1]
        with self.assertRaises(ValueError):publisher.build_report(REPO,self.pr,self.run,self.inspection,{},[])
    def test_newer_analyzer_gates_are_accepted(self):
        # The trusted side runs from main and is merged before the analysis side (operator
        # handbook 6.7): a report must accept extra gates, and a failing extra gate counts.
        self.inspection['checks'].append({'key':'new-gate','label':'new-gate','status':'pass'})
        r=publisher.build_report(REPO,self.pr,self.run,self.inspection,{'summary.md':'delta'},[])
        self.assertEqual(r['state'],'pass');self.assertIn('new-gate',r['fields']['checks'])
        self.inspection['checks'][-1]['status']='fail';self.run['conclusion']='failure'
        self.assertEqual(publisher.build_report(REPO,self.pr,self.run,self.inspection,{'summary.md':'delta'},[])['state'],'fix')
    def test_report_html_cannot_inject_a_hidden_payload(self):
        self.report['fields']['change']='<script>alert(1)</script>'
        body=gate.report_comment(gate.encode_report(self.report))
        self.assertNotIn('<script>',body)
    def test_all_comment_pages_read(self):
        def api(path):
            if '/comments?' in path and '&page=1' in path:return 200,[{'id':1}] * 100
            if '/comments?' in path and '&page=2' in path:return 200,self.comments
            return self.api(path)
        self.assertTrue(gate.evaluate(api,REPO,self.pr)['valid'])
    def test_practice_repositories_are_validated_like_any_other(self):
        # The practice repositories run the whole pipeline (Exchange Contract Part C): a stale
        # report is a stale report there too, and the field `required` is always True.
        practice=gate.evaluate(self.api,'4dcitygml/sample-tokyo-station',self.pr)
        self.assertTrue(practice['required'])              # no exemption any more
        self.assertFalse(practice['valid'])                # the fixture's report belongs to another repository
        result=gate.evaluate(self.api,REPO,self.pr)
        self.assertTrue(result['required']);self.assertTrue(result['valid'])
        self.pr['body']='changed after the report'
        stale=gate.evaluate(self.api,REPO,self.pr)
        self.assertTrue(stale['required']);self.assertFalse(stale['valid'])
        self.assertFalse(hasattr(gate,'required'))
    def test_mirrored_contract(self):
        expected=(REPO_ROOT/'tools/hub/operator_explanation.py').read_bytes()
        for repo in ['city-template','sample-tokyo-station','sample-munich-station','sample-newyork-station']:
            self.assertEqual(expected,(sibling(repo)/'.github/scripts/operator_explanation.py').read_bytes())


class GateTransitionsTest(unittest.TestCase):
    setUp = ReportContractTest.setUp
    api = ReportContractTest.api
    def drive(self):
        writes=[]
        def api(path,method='GET',payload=None):
            if method!='GET':writes.append((path,payload));return 201,{'id':len(writes),'data':{}}
            if '/pulls?state=open' in path:return 200,[self.pr]
            if path.endswith('/pulls/7'):return 200,self.pr
            return self.api(path)
        with patch.dict('os.environ',{'GITHUB_REPOSITORY':REPO}),github(api):runner.run()
        return writes
    def test_report_check_needs_no_human_confirmation(self):
        self.reviews=[];writes=self.drive()
        self.assertEqual(writes[-1][1]['conclusion'],'success')
        self.assertEqual(writes[0][1]['name'],'ci-report')
        self.assertFalse(any(path == '/graphql' or '/dismissals' in path for path,_ in writes))
    def test_stale_report_blocks_without_changing_draft_or_approvals(self):
        self.pr['draft']=False;self.pr['body']='new source';writes=self.drive()
        self.assertEqual(writes[-1][1]['conclusion'],'failure')
        self.assertFalse(any(path == '/graphql' or '/dismissals' in path for path,_ in writes))
    def test_existing_report_check_is_reused_not_recreated(self):
        # one ci-report row per head: an existing run of ours is reopened in place
        self.check_runs=[{'id':77,'app':{'slug':'github-actions'}},{'id':78,'app':{'slug':'someone-else'}}]
        writes=self.drive()
        self.assertTrue(writes[0][0].endswith('/check-runs/77'),writes[0][0])
        self.assertEqual(writes[0][1]['status'],'in_progress')
        self.assertFalse(any(path.endswith('/check-runs') for path,_ in writes))
        self.assertEqual(writes[-1][1]['conclusion'],'success')


class ReportPublicationTest(unittest.TestCase):
    def drive(self, failure=None):
        pr, run, inspection, report, comment = fixture()
        writes = []
        def api(path, method='GET', payload=None):
            if method != 'GET':
                writes.append((path, payload))
                if failure == 'delivery' and path.endswith('/comments'):
                    return 403, {}
                return 201, {'id': 900}
            if path.endswith('/pulls/7'):return 200, pr
            if '/pulls?state=open' in path:return 200, [pr]
            if '/actions/' in path:return 200, {'workflow_runs': [run]}
            if '/comments?' in path:return 200, []
            if '/check-runs?' in path:return 200, {'check_runs': []}
            if '/files?' in path:return 200, [{'filename': 'city/udx/bldg/a.gml'}]
            self.fail(path)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'out').mkdir()
            (root/'event.json').write_text(json.dumps({'workflow_run': run}))
            (root/'out/pr.txt').write_text('7')
            if failure != 'missing':
                (root/'out/inspection.json').write_text(json.dumps(inspection))
            (root/'out/summary.md').write_text('Storeys: 2 → 3')
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_PATH': str(root/'event.json'), 'REPORT_EVENT_PATH': ''}), github(api):
                    if failure:
                        with self.assertRaises((RuntimeError, FileNotFoundError)):publisher.run()
                    else:
                        publisher.run()
            finally:
                os.chdir(previous)
        return writes
    def test_report_delivery_precedes_successful_check(self):
        writes = self.drive()
        self.assertIn(gate.REPORT_MARKER, writes[-2][1]['body'])
        self.assertEqual(writes[-1][1]['conclusion'], 'success')
    def test_missing_evidence_or_failed_delivery_never_passes(self):
        for cause in ('missing', 'delivery'):
            with self.subTest(cause=cause):
                writes = self.drive(cause)
                self.assertEqual(writes[-1][1]['conclusion'], 'failure')
                self.assertFalse(any(p.get('conclusion') == 'success' for _, p in writes))


if __name__=='__main__':unittest.main()
