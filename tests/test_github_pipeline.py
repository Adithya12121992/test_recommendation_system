from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_recommendation.catalog import CatalogError
from test_recommendation.github_pipeline import download_extract


class FakeGitHub:
    repository='owner/repository'
    def __init__(self,fail_source=False,inconsistent=False):
        self.fail_source=fail_source
        self.inconsistent=inconsistent
    def commit(self,reference):
        target=reference!='a'*40
        return {'sha':'b'*40 if target else 'a'*40,'parents':[{'sha':'a'*40}],
                'commit':{'tree':{'sha':'tree'}},'files':[{'filename':'src/mesh_harbor/x.py','status':'modified',
                    'patch':'@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2'}] if target else []}
    def get(self,path,accept=None):
        if self.inconsistent:
            return b'unrelated diff'
        return b'diff --git a/src/mesh_harbor/x.py b/src/mesh_harbor/x.py\n--- a/src/mesh_harbor/x.py\n+++ b/src/mesh_harbor/x.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2\n'
    def sources(self,commit):
        if self.fail_source:
            raise CatalogError('fixture source failure')
        return {'src/mesh_harbor/x.py':'def value():\n    return '+('1' if commit['sha']=='a'*40 else '2')+'\n'}


class RemotePipelineTests(unittest.TestCase):
    def test_remote_processing_does_not_use_git_and_deletes_raw_diff(self):
        with tempfile.TemporaryDirectory() as root:
            actual=tempfile.TemporaryDirectory
            def temporary(**kwargs):
                return actual(dir=root,**kwargs)
            with patch('test_recommendation.github_pipeline.tempfile.TemporaryDirectory',side_effect=temporary), \
                 patch('test_recommendation.catalog.git',side_effect=AssertionError('Local Git forbidden')):
                change,graph=download_extract(FakeGitHub(),'b'*40)
            self.assertEqual(list(Path(root).iterdir()),[])
            self.assertFalse(change['raw_diff_retained'])
            self.assertEqual(change['downloaded_diff_hunk_count'],1)
            self.assertEqual(graph['revision_sha'],'a'*40)
            self.assertEqual(change['changed_symbols'][0]['change'],'modified')
            self.assertNotIn('return 2',str(change))

    def test_cleanup_on_source_failure_and_inconsistent_diff(self):
        for client in (FakeGitHub(fail_source=True),FakeGitHub(inconsistent=True)):
            with tempfile.TemporaryDirectory() as root:
                actual=tempfile.TemporaryDirectory
                def temporary(**kwargs):
                    return actual(dir=root,**kwargs)
                with patch('test_recommendation.github_pipeline.tempfile.TemporaryDirectory',side_effect=temporary):
                    with self.assertRaises(CatalogError):
                        download_extract(client,'b'*40)
                self.assertEqual(list(Path(root).iterdir()),[])

    def test_merge_commit_rejected_before_diff_download(self):
        client=FakeGitHub()
        original=client.commit
        def commit(ref):
            result=original(ref)
            result['parents'].append({'sha':'c'*40})
            return result
        client.commit=commit
        with self.assertRaises(CatalogError):
            download_extract(client,'b'*40)

    def test_downloaded_hunks_are_authoritative_over_api_snippet_context(self):
        client=FakeGitHub()
        original=client.commit
        def commit(reference):
            result=original(reference)
            if result['files']:
                result['files'][0]['patch']='@@ -1 +1 @@\n-old context\n+different context'
            return result
        client.commit=commit
        change,_=download_extract(client,'b'*40)
        self.assertEqual(change['files'][0]['diff_ranges'][0]['old_count'],2)
