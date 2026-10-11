import unittest
from unittest.mock import patch
from test_recommendation.pr_logs import MARKER,upsert


class PRLogTests(unittest.TestCase):
    def test_edits_existing_owned_marker_comment(self):
        calls=[]
        def request(token,path,method='GET',body=None):
            calls.append((path,method,body))
            if path=='user':
                return {'id':1}
            if 'comments?' in path:
                return [{'id':7,'body':MARKER+' old','user':{'id':1}},
                        {'id':8,'body':MARKER+' foreign','user':{'id':2}}]
            return {'id':7}
        with patch('test_recommendation.pr_logs.request',side_effect=request):
            upsert('token','owner/repo',1,MARKER+' new')
        self.assertEqual(calls[-1][0],'repos/owner/repo/issues/comments/7')
        self.assertEqual(calls[-1][1],'PATCH')
        self.assertFalse(any(method=='POST' for _,method,_ in calls))

    def test_creates_once_when_no_owned_comment_exists(self):
        with patch('test_recommendation.pr_logs.request',side_effect=[{'id':1},[],{'id':9}]) as request:
            result=upsert('token','owner/repo',1,MARKER+' log')
        self.assertEqual(result['id'],9)
        self.assertEqual(request.call_args.args[2],'POST')
