import unittest
from test_recommendation.evidence import Compatibility,candidates_from_evidence


class EvidenceTests(unittest.TestCase):
    def evidence(self):
        return dict(run_id='run',base_revision_sha='base',target_revision_sha='head',coverage_matches=[],issue_matches=[],
                    feedback_matches=[],suite_timings={},gaps=[])
    def catalog(self):
        return {'suites':[{'id':'endpoint','unittest_target':'tests.EndpointTests','owned_paths':['src/endpoint.py']}]}

    def test_direct_path_fallback_is_protected_without_faking_coverage(self):
        result=candidates_from_evidence(self.evidence(),self.catalog(),{'changed_source_paths':['src/endpoint.py']})
        self.assertTrue(result['candidates'][0]['protected'])
        self.assertEqual(result['candidates'][0]['reasons'][0]['kind'],'catalog_path_fallback')
        self.assertFalse(result['llm_called'])

    def test_feedback_cannot_create_candidates(self):
        evidence=self.evidence();evidence['feedback_matches']=[{'suite_id':'endpoint','signal':1}]
        result=candidates_from_evidence(evidence,self.catalog(),{'changed_source_paths':[]})
        self.assertEqual(result['candidates'],[])

    def test_duplicate_suite_evidence_is_combined(self):
        evidence=self.evidence();evidence['coverage_matches']=[{'suite_id':'endpoint','source_path':'src/endpoint.py'}]
        evidence['issue_matches']=[{'suite_id':'endpoint','source_path':'src/endpoint.py'}]
        result=candidates_from_evidence(evidence,self.catalog(),{'changed_source_paths':['src/endpoint.py']})
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual(len(result['candidates'][0]['reasons']),3)

    def test_coverage_requires_ancestry_and_identical_source_tests(self):
        class Client:
            def commit(self,sha):
                return {'sha':sha,'commit':{'tree':{'sha':'base-tree'}}}
            def get(self,path):
                if path.startswith('compare/'):
                    return {'merge_base_commit':{'sha':'old'}}
                source='different' if path.endswith('changed-tree') else 'same'
                return {'sha':path.split('/')[-1],'tree':[{'path':'src','sha':source,'type':'tree'},{'path':'tests','sha':'tests','type':'tree'}]}
        compat=Compatibility(Client(),'base')
        run={'status':'passed','revision_sha':'old'}
        self.assertTrue(compat.coverage(run,{'tree_sha':'same-tree'})[0])
        self.assertFalse(compat.coverage(run,{'tree_sha':'changed-tree'})[0])
        future=dict(run,revision_sha='future')
        self.assertFalse(compat.coverage(future,{'tree_sha':'same-tree'})[0])

    def test_missing_target_suite_is_reported_and_excluded(self):
        from test_recommendation.catalog import CatalogError
        from test_recommendation.evidence import verify_target_suites
        class Client:
            def get(self,path):
                raise CatalogError('GitHub HTTP 404')
        candidates={'candidates':[{'suite_id':'endpoint'}],'evidence_gaps':[]}
        catalog=self.catalog();catalog['suites'][0]['test_file']='tests/test_endpoint.py'
        verify_target_suites(candidates,catalog,{'target_revision_sha':'head'},Client())
        self.assertEqual(candidates['candidates'],[])
        self.assertEqual(candidates['evidence_gaps'][0]['kind'],'catalog_suite_missing_at_head')
