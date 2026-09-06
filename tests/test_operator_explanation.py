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

ROOT = Path(__file__).resolve().parents[2]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

gate = load('report_contract', ROOT/'tools/tools/hub/operator_explanation.py')
with patch.dict(sys.modules, {'operator_explanation':gate}):
    runner = load('report_gate', ROOT/'city-template/.github/scripts/check_review_report.py')
with patch.dict(sys.modules, {'operator_explanation':gate,'check_review_report':runner}):
    publisher = load('report_publisher', ROOT/'city-template/.github/scripts/publish_review_report.py')

SHA='a'*40
REPO='munakata-city/citygml'
KEYS=['reason','commit-scope','scope-reproducibility','reproduction','freshness','file-scope','schema','minimal-diff','texture','structure','plausibility','topology','model']

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
    confirmation={'id':200,'state':'COMMENTED','commit_id':SHA,
                  'body':gate.MARKER+'\nReport-ID: '+report['reportId'],
                  'submitted_at':'2026-09-05T12:00:00Z','user':{'login':'operator','type':'User'}}
    return pr,run,inspection,report,comment,confirmation


class ReportContractTest(unittest.TestCase):
    def setUp(self):
        self.pr,self.run,self.inspection,self.report,self.comment,self.confirmation=fixture()
        self.comments=[self.comment];self.reviews=[self.confirmation];self.permission='write'
    def api(self,path):
        if '/comments?' in path:return 200,self.comments
        if '/reviews?' in path:return 200,self.reviews
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
    def test_only_first_party_samples_are_exempt(self):
        self.assertFalse(gate.required('4dcitygml/sample-tokyo-station'))
        self.assertTrue(gate.required('proposer/sample-tokyo-station'))
    def test_mirrored_contract(self):
        expected=(ROOT/'tools/tools/hub/operator_explanation.py').read_bytes()
        for repo in ['city-template','sample-tokyo-station','sample-munich-station','sample-newyork-station']:
            self.assertEqual(expected,(ROOT/repo/'.github/scripts/operator_explanation.py').read_bytes())


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
        with patch.dict('os.environ',{'GITHUB_REPOSITORY':REPO}),patch.object(runner,'api',side_effect=api):runner.run()
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


class ReportPublicationTest(unittest.TestCase):
    def drive(self, failure=None):
        pr, run, inspection, report, comment, confirmation = fixture()
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
                with patch.dict(os.environ, {'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_PATH': str(root/'event.json'), 'REPORT_EVENT_PATH': ''}), patch.object(publisher, 'api', side_effect=api), patch.object(runner, 'api', side_effect=api):
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
