import json
from pathlib import Path
import unittest
from test_recommendation.catalog import CatalogError,load_catalog
from test_recommendation.negative_evaluation import evaluate,prediction,run

class NegativeEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset=json.loads(Path('evals/golden_dataset_v1.json').read_text())
        cls.catalog=load_catalog(Path('test_catalog.yaml'))
        cls.ids={s['id'] for s in cls.catalog['suites']}
    def test_all_frozen_negative_controls_detected(self):
        result=run(self.dataset,self.catalog)
        self.assertEqual(result['summary'],dict(golden_cases=20,controls=160,passed=160,failed=0))
    def test_extras_are_review_flags_not_false_positives(self):
        case=self.dataset['cases'][0]
        selected=case['required_suite_ids']+[next(s for s in self.ids if s not in case['required_suite_ids'])]
        result=evaluate(case,prediction(case,selected),self.ids)
        self.assertEqual(result['required_suite_recall'],1)
        self.assertEqual(result['status'],'required_suites_present')
        self.assertTrue(result['extra_suite_ids_for_review'])
    def test_partial_multi_required_recall(self):
        case=dict(self.dataset['cases'][0],required_suite_ids=sorted(self.ids)[:2])
        result=evaluate(case,prediction(case,case['required_suite_ids'][:1]),self.ids)
        self.assertEqual(result['required_suite_recall'],0.5)
    def test_real_predictions_report_failure_and_pin_versions(self):
        predictions=[prediction(c,c['required_suite_ids']) for c in self.dataset['cases']]
        predictions[0]['selected_suite_ids']=[]
        predictions[1]['case_version']=2
        result=run(self.dataset,self.catalog,predictions)
        self.assertEqual(result['summary']['missing_required'],1)
        self.assertEqual(result['summary']['invalid'],1)
        self.assertEqual(result['summary']['recall_denominator_valid_cases'],19)
    def test_incomplete_submission_rejected(self):
        with self.assertRaises(CatalogError):
            run(self.dataset,self.catalog,[])
    def test_wrong_catalog_rejected(self):
        with self.assertRaises(CatalogError):
            run(dict(self.dataset,catalog_key='wrong'),self.catalog)
