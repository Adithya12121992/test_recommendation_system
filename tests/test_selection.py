import json
import unittest
from copy import deepcopy
from test_recommendation.catalog import CatalogError
from test_recommendation.change_pipeline import checksum
from test_recommendation.selection import rank, select

class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.change=dict(run_id='run',base_revision_sha='a',target_revision_sha='b',changed_source_paths=['x'],changed_symbols=[])
        self.impact=dict(**{k:self.change[k] for k in ('run_id','base_revision_sha','target_revision_sha')},
                         extraction_sha256=checksum(self.change),impacted_files=[],unresolved_calls=[])
        def row(name,path,protected=False):
            return dict(suite_id=name,unittest_target=name,reasons=[dict(kind='measured_coverage',source_path=path,impact_distance=0)],
                        protected=protected,feedback=[],measured_runtime_seconds=1,target_test_case_count=2,target_availability_verified=True)
        self.candidates=dict(**{k:self.change[k] for k in ('run_id','base_revision_sha','target_revision_sha')},
                             candidates=[row('one','x',True),row('two','y')],evidence_gaps=[])
    def caller(self,decisions):
        return lambda *args: dict(id='reply',model='fixture',usage={},stop_reason='end_turn',content=[dict(type='text',text=json.dumps(dict(decisions=decisions)))])
    def decisions(self,one=True,two=False):
        return [dict(suite_id='one',selected=one,reason='evidence'),dict(suite_id='two',selected=two,reason='evidence')]
    def run_selection(self,decisions):
        return select(rank(self.candidates),self.change,self.impact,'fixture',caller=self.caller(decisions))
    def test_feedback_bounded_and_repeated_links_do_not_inflate(self):
        row=self.candidates['candidates'][0]
        row['feedback']=[dict(signal=1)]*100
        row['reasons']*=5
        ranked=rank(self.candidates)
        selected=next(r for r in ranked['candidates'] if r['suite_id']=='one')
        self.assertEqual(selected['technical_score'],75)
        self.assertLessEqual(selected['feedback_adjustment'],5)
    def test_restores_unique_measured_path_and_protected(self):
        result=self.run_selection(self.decisions(False,False))
        self.assertEqual(set(result['selected_suite_ids']),{'one','two'})
        self.assertEqual(result['status'],'completed')
    def test_allows_redundant_unprotected_suite_reduction(self):
        self.candidates['candidates'][1]['reasons'][0]['source_path']='x'
        result=self.run_selection(self.decisions())
        self.assertEqual(result['selected_suite_ids'],['one'])
        self.assertEqual(result['suite_reduction_fraction'],0.5)
    def test_unknown_duplicate_and_missing_decisions_fallback(self):
        for decisions in ([dict(suite_id='invented',selected=True,reason='x')],self.decisions()[:1],self.decisions()+self.decisions()):
            result=self.run_selection(decisions)
            self.assertEqual(result['status'],'conservative_fallback')
            self.assertEqual(result['selected_count'],2)
    def test_gaps_retain_all(self):
        self.candidates['candidates'][1]['reasons'][0]['source_path']='x'
        self.candidates['evidence_gaps']=[dict(kind='new_code')]
        self.assertEqual(self.run_selection(self.decisions())['selected_count'],2)
    def test_api_failure_and_truncation_retain_all(self):
        def fail(*args):
            raise CatalogError('Claude API HTTP 401')
        result=select(rank(self.candidates),self.change,self.impact,'fixture',caller=fail)
        self.assertEqual(result['selected_count'],2)
        self.assertEqual(result['status'],'conservative_fallback')
        response=self.caller(self.decisions())()
        response['stop_reason']='max_tokens'
        result=select(rank(self.candidates),self.change,self.impact,'fixture',caller=lambda *args:response)
        self.assertEqual(result['status'],'conservative_fallback')
    def test_mismatched_provenance_rejected(self):
        impact=deepcopy(self.impact);impact['target_revision_sha']='c'
        with self.assertRaises(CatalogError):
            select(rank(self.candidates),self.change,impact,'fixture',caller=self.caller(self.decisions()))

if __name__=='__main__':
    unittest.main()
