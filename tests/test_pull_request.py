import unittest
from unittest.mock import patch
from test_recommendation.catalog import CatalogError
from test_recommendation.pull_request import acquire,parse_url


class PullRequestTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(parse_url('https://github.com/owner/repo/pull/12'),('owner/repo',12))
        for url in ('https://example.com/owner/repo/pull/1','https://github.com/owner/repo/issues/1','https://github.com/owner/repo/pull/0'):
            with self.assertRaises(CatalogError):
                parse_url(url)

    def fixture(self,fork=True,move=False,closed=False):
        calls=[]
        pr=dict(state='closed' if closed else 'open',merged=False,changed_files=1,commits=3,
                base={'sha':'a'*40,'repo':{'full_name':'owner/repo'}},head={'sha':'b'*40,'repo':{'full_name':'contributor/repo' if fork else 'owner/repo'}})
        class Client:
            def __init__(self,repository,token):
                self.repository=repository
                self.reads=0
            def get(self,path,accept=None):
                calls.append((self.repository,path,accept))
                if path=='pulls/1':
                    self.reads+=1
                    if move and self.reads>1:
                        return dict(pr,head={'sha':'d'*40,'repo':pr['head']['repo']})
                    return pr
                if path.startswith('compare/'):
                    return {'merge_base_commit':{'sha':'c'*40}}
                if path.startswith('pulls/1/files'):
                    return [{'filename':'src/mesh_harbor/x.py','status':'modified'}]
            def commit(self,reference):
                return dict(sha=reference,parents=[{'sha':'e'*40},{'sha':'f'*40}],commit={'tree':{'sha':'tree'}})
            def sources(self,commit):
                calls.append((self.repository,'sources',commit['sha']))
                return {}
        return Client,calls

    def test_multicommit_fork_uses_merge_base_and_head_repository(self):
        factory,calls=self.fixture()
        def extraction(client,reference):
            self.assertEqual(client.commit(reference)['parents'],[{'sha':'c'*40}])
            client.sources({'sha':'b'*40})
            client.sources({'sha':'c'*40})
            client.get('commits/'+reference,accept='application/vnd.github.diff')
            return {'base_revision_sha':'c'*40,'files':[{}]},{}
        with patch('test_recommendation.pull_request.download_extract',side_effect=extraction):
            change,_=acquire('https://github.com/owner/repo/pull/1','token',factory)
        self.assertTrue(change['pull_request']['fork'])
        self.assertEqual(change['pull_request']['commit_count'],3)
        self.assertTrue(change['merge_target'])
        self.assertIn(('contributor/repo','sources','b'*40),calls)
        self.assertIn(('owner/repo','sources','c'*40),calls)
        self.assertTrue(any(path=='compare/'+'c'*40+'...contributor:'+'b'*40 for _,path,_ in calls))

    def test_closed_or_moving_pr_rejected(self):
        for kwargs in ({'closed':True},{'move':True}):
            factory,_=self.fixture(**kwargs)
            with patch('test_recommendation.pull_request.download_extract') as extract:
                with self.assertRaises(CatalogError):
                    acquire('https://github.com/owner/repo/pull/1','token',factory)
                extract.assert_not_called()

    def test_same_repository_uses_pinned_sha_and_file_pagination(self):
        factory,calls=self.fixture(fork=False)
        class Client(factory):
            def get(self,path,accept=None):
                if path=='pulls/1':
                    pr=super().get(path,accept)
                    return dict(pr,changed_files=101)
                if path.startswith('pulls/1/files'):
                    start=0 if path.endswith('page=1') else 100
                    stop=100 if start==0 else 101
                    return [{'filename':f'src/mesh_harbor/f{index}.py','status':'modified'} for index in range(start,stop)]
                return super().get(path,accept)
        def extraction(client,reference):
            self.assertEqual(len(client.commit(reference)['files']),100)
            second=client.get('commits/'+reference+'?per_page=100&page=2')
            self.assertEqual(len(second['files']),1)
            self.assertEqual(second['files'][0]['filename'],'src/mesh_harbor/f100.py')
            client.get('commits/'+reference,accept='application/vnd.github.diff')
            return {'base_revision_sha':'c'*40,'files':[{}]*101},{}
        with patch('test_recommendation.pull_request.download_extract',side_effect=extraction):
            change,_=acquire('https://github.com/owner/repo/pull/1','token',Client)
        self.assertFalse(change['pull_request']['fork'])
        self.assertTrue(any(path=='compare/'+'c'*40+'...'+'b'*40 for _,path,_ in calls))

    def test_update_during_graph_processing_is_rejected(self):
        factory,_=self.fixture()
        class Client(factory):
            def get(self,path,accept=None):
                result=super().get(path,accept)
                if path=='pulls/1' and self.reads==3:
                    return dict(result,base={'sha':'d'*40,'repo':result['base']['repo']})
                return result
        with patch('test_recommendation.pull_request.download_extract',return_value=({'base_revision_sha':'c'*40,'files':[]},{})):
            with self.assertRaises(CatalogError):
                acquire('https://github.com/owner/repo/pull/1','token',Client)

    def test_adapter_runs_real_diff_and_ast_processing_without_local_git(self):
        factory,_=self.fixture()
        class Client(factory):
            def get(self,path,accept=None):
                if accept=='application/vnd.github.diff':
                    return b'diff --git a/src/mesh_harbor/x.py b/src/mesh_harbor/x.py\n--- a/src/mesh_harbor/x.py\n+++ b/src/mesh_harbor/x.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2\n'
                return super().get(path,accept)
            def sources(self,commit):
                value=2 if commit['sha']=='b'*40 else 1
                return {'src/mesh_harbor/x.py':f'def value():\n    return {value}\n'}
        with patch('test_recommendation.catalog.git',side_effect=AssertionError('Local Git forbidden')):
            change,graph=acquire('https://github.com/owner/repo/pull/1','token',Client)
        self.assertEqual(graph['revision_sha'],'c'*40)
        self.assertEqual(change['changed_symbols'][0]['id'],'mesh_harbor.x:value')
        self.assertFalse(change['raw_diff_retained'])
        self.assertTrue(change['temporary_downloads_deleted'])
