# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
import unittest
from tests.test_operator_explanation import gate, fixture, REPO


def review(id, user, state='APPROVED', sha='a'*40):
    return {'id': id, 'user': {'login':user,'type':'User'}, 'state':state, 'commit_id':sha}


class NativeApprovalTest(unittest.TestCase):
    def setUp(self):
        self.pr=fixture()[0]
        self.records=[]
        self.required=4
        self.classic=None
        self.permission={}
        self.rules_error=False
        self.graphql_error=False
    def api(self,path,method='GET',payload=None):
        if '/rules/branches/' in path:
            return (403,{}) if self.rules_error else (200,[{'type':'pull_request','parameters':{'required_approving_review_count':self.required}}])
        if path=='/graphql':
            return (200,{'errors':[{'message':'forbidden'}]}) if self.graphql_error else (200,{'data':{'repository':{'ref':{'branchProtectionRule':self.classic,'refUpdateRule':None}}}})
        if '/reviews?' in path:return 200,self.records
        if '/collaborators/' in path:
            user=path.split('/collaborators/')[1].split('/')[0]
            permission=self.permission.get(user,'write')
            return (403,{}) if permission=='error' else (200,{'permission':permission})
        self.fail(path)
    def progress(self):
        policy=gate.approval_policy(self.api,REPO,'main')
        return gate.approval_progress(self.api,REPO,self.pr,'reviewer',policy)
    def test_four_people_then_remaining_two_and_one(self):
        self.records=[review(1,'contractor-a'),review(2,'contractor-b')]
        self.assertEqual(self.progress()['remaining'],2)
        self.records.append(review(3,'reviewer'))
        result=self.progress()
        self.assertEqual(result['remaining'],1)
        self.assertTrue(result['myApproval'])
    def test_count_can_change_without_code_or_restart(self):
        self.records=[review(1,'reviewer')]
        for required,remaining in [(4,3),(2,1),(1,0),(3,2)]:
            self.required=required
            self.assertEqual(self.progress()['remaining'],remaining)
    def test_one_account_is_one_vote_and_comments_do_not_erase_it(self):
        self.records=[review(1,'REVIEWER'),review(2,'reviewer'),review(3,'reviewer','COMMENTED')]
        self.assertEqual(self.progress()['approved'],1)
    def test_changes_requested_and_dismissed_are_not_approvals(self):
        for state in ('CHANGES_REQUESTED','DISMISSED'):
            self.records=[review(1,'reviewer'),review(2,'reviewer',state)]
            self.assertEqual(self.progress()['approved'],0)
    def test_approve_after_request_changes_counts(self):
        self.records=[review(1,'reviewer','CHANGES_REQUESTED'),review(2,'reviewer')]
        self.assertEqual(self.progress()['approved'],1)
    def test_author_and_read_only_reviews_do_not_count(self):
        self.records=[review(1,'proposer'),review(2,'reader'),review(3,'reviewer')]
        self.permission['reader']='read'
        self.assertEqual(self.progress()['approvedBy'],['reviewer'])
    def test_active_review_on_older_commit_follows_github_stale_review_policy(self):
        self.records=[review(1,'reviewer',sha='old-sha')]
        self.assertEqual(self.progress()['approved'],1)
    def test_permission_lookup_failure_is_unknown_not_zero(self):
        self.records=[review(1,'reviewer')];self.permission['reviewer']='error'
        self.assertIsNone(self.progress()['remaining'])
    def test_missing_policy_or_graphql_errors_are_unknown(self):
        self.rules_error=True;self.assertFalse(self.progress()['known'])
        self.rules_error=False;self.graphql_error=True;self.assertFalse(self.progress()['known'])
    def test_classic_and_multiple_rulesets_use_strictest_count(self):
        self.classic={'requiresApprovingReviews':True,'requiredApprovingReviewCount':5,
                      'requiresCodeOwnerReviews':True,'requireLastPushApproval':False}
        result=self.progress()
        self.assertEqual(result['required'],5)
        self.assertTrue(result['additionalConditions'])
        self.required=6;self.assertEqual(self.progress()['required'],6)
    def test_non_admin_visible_classic_rules_are_also_counted(self):
        def api(path,method='GET',payload=None):
            code,data=self.api(path,method,payload)
            if path=='/graphql':data['data']['repository']['ref']['refUpdateRule']={'requiredApprovingReviewCount':5,'requiresCodeOwnerReviews':False}
            return code,data
        self.assertEqual(gate.approval_policy(api,REPO,'main')['required'],5)
    def test_all_review_pages_are_used(self):
        def api(path,method='GET',payload=None):
            if '/reviews?' in path:
                if '&page=1' in path:return 200,[review(i,'commenter','COMMENTED') for i in range(100)]
                return 200,[review(101,'reviewer')]
            return self.api(path,method,payload)
        result=gate.approval_progress(api,REPO,self.pr,'reviewer',gate.approval_policy(api,REPO,'main'))
        self.assertEqual(result['approved'],1)
    def test_non_admin_fallback_when_admin_field_is_forbidden(self):
        def api(path,method='GET',payload=None):
            if path=='/graphql':
                if 'branchProtectionRule' in payload['query']:return 200,{'errors':[{'message':'forbidden'}]}
                return 200,{'data':{'repository':{'ref':{'refUpdateRule':{'requiredApprovingReviewCount':5,'requiresCodeOwnerReviews':False}}}}}
            return self.api(path,method,payload)
        self.assertEqual(gate.approval_policy(api,REPO,'main')['required'],5)
    def test_beyond_required_is_zero_not_negative(self):
        self.required=1;self.records=[review(1,'a'),review(2,'b')]
        self.assertEqual(self.progress()['remaining'],0)
    def test_missing_branch_or_permission_payload_is_unknown(self):
        def api(path,method='GET',payload=None):
            if path=='/graphql':return 200,{'data':{'repository':{'ref':None}}}
            return self.api(path,method,payload)
        self.assertFalse(gate.approval_policy(api,REPO,'main')['known'])
        self.records=[review(1,'reviewer')]
        def missing_permission(path):
            if '/collaborators/' in path:return 200,{}
            return self.api(path)
        result=gate.approval_progress(missing_permission,REPO,self.pr,'reviewer',{'known':True,'required':4})
        self.assertFalse(result['known'])


if __name__=='__main__':unittest.main()
