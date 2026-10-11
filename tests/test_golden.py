import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_recommendation.catalog import CatalogError, load_catalog
from test_recommendation.golden import check_review, digest, reference_run


class GoldenTests(unittest.TestCase):
    def setUp(self):
        self.review=json.loads(Path('evals/golden_review_v1.json').read_text())
        self.catalog=load_catalog('test_catalog.yaml')

    def test_review_covers_twenty_cases_without_claiming_human_approval(self):
        check_review(self.review,self.catalog)
        self.assertFalse(self.review['human_approved'])
        self.assertEqual(sum(len(case['required_suites'])>1 for case in self.review['cases']),5)

    def test_missing_duplicate_or_unknown_labels_rejected(self):
        for mutation in ('missing','duplicate','unknown','human'):
            review=copy.deepcopy(self.review)
            if mutation=='missing':
                review['cases'].pop()
            elif mutation=='duplicate':
                review['cases'][1]['sequence']=81
            elif mutation=='unknown':
                review['cases'][0]['required_suites']=['unknown_suite']
            else:
                review['human_approved']=True
            with self.subTest(mutation=mutation),self.assertRaises(CatalogError):
                check_review(review,self.catalog)

    def test_checksum_ignores_json_key_order_but_detects_answer_changes(self):
        self.assertEqual(digest({'a':1,'b':2}),digest({'b':2,'a':1}))
        changed=copy.deepcopy(self.review)
        changed['cases'][0]['required_suites']=['tcp_endpoints']
        self.assertNotEqual(digest(changed),digest(self.review))

    def test_reference_failure_is_not_frozen_as_success(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch('test_recommendation.golden.extract_snapshot'), \
             patch('test_recommendation.golden.subprocess.run') as run:
            run.return_value.returncode=1
            run.return_value.stdout=''
            run.return_value.stderr='Ran 1 test\nFAILED (failures=1)'
            with self.assertRaises(CatalogError):
                reference_run('repo','sha',Path(temporary))

    def test_missing_or_changed_mapping_is_rejected(self):
        from test_recommendation.golden import validate_coverage_mappings
        report={'files':{'src/mesh_harbor/a.py':{'summary':{'num_statements':1,'covered_lines':1,'num_branches':0,'covered_branches':0},'executed_lines':[1],'missing_lines':[]}}}
        class Cursor:
            def execute(self,statement,values):
                self.statement=statement
            def fetchall(self):
                if 'coverage_suite_results' in self.statement:
                    return [{'suite_id':'suite','raw_report_json':json.dumps(report)}]
                if 'coverage_artifacts' in self.statement:
                    return [{'suite_id':'suite','content':json.dumps(report).encode()}]
                return []
        with self.assertRaises(CatalogError):
            validate_coverage_mappings(Cursor(),'run')
