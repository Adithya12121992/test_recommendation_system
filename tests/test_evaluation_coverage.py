import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_recommendation.catalog import CatalogError
from test_recommendation.coverage_collection import collect
from test_recommendation.evaluation_coverage import validate_run


class Cursor:
    def __init__(self, revision='parent', status='passed', purpose='rolling_parent_evidence'):
        self.run = dict(status=status, catalog_key='catalog', revision_sha=revision,
                        environment_json=json.dumps(dict(purpose=purpose, eligible_target_sha='target')))
        self.results = [dict(suite_id='suite', passed=True, test_case_count=5)]
        content = b'evidence'
        self.artifacts = [dict(suite_id='suite', artifact_kind=kind, content=content,
                               content_sha256=hashlib.sha256(content).hexdigest())
                          for kind in ('coverage_data', 'json_report', 'test_output')]
    def execute(self, sql, values):
        self.sql = sql
    def fetchone(self):
        return self.run
    def fetchall(self):
        return self.artifacts if 'coverage_artifacts' in self.sql else self.results


class EvaluationCoverageTests(unittest.TestCase):
    def validate(self, cursor):
        validate_run(cursor, 'run', 'catalog', 'parent', 'target', {'suite': 5})

    def test_complete_parent_evidence_is_accepted(self):
        self.validate(Cursor())

    def test_future_failed_and_diagnostic_runs_are_rejected(self):
        for cursor in (Cursor(revision='target'), Cursor(status='failed'), Cursor(purpose='diagnostic')):
            with self.assertRaises(CatalogError):
                self.validate(cursor)

    def test_missing_suite_or_corrupted_artifact_is_rejected(self):
        cursor = Cursor()
        cursor.results = []
        with self.assertRaises(CatalogError):
            self.validate(cursor)
        cursor = Cursor()
        cursor.artifacts[0]['content'] = b'corrupt'
        with self.assertRaises(CatalogError):
            self.validate(cursor)

    def test_wrong_parent_rejected_before_seeding_or_measurement(self):
        manifest = {'commits': [{'commit_sha': 'base', 'parent_sha': 'older', 'split': 'train'},
                                {'commit_sha': 'target', 'parent_sha': 'different', 'split': 'evaluation'}]}
        with patch('test_recommendation.coverage_collection.load_inputs', return_value=({}, manifest)), \
             patch('test_recommendation.coverage_collection.seed') as seed:
            with self.assertRaises(CatalogError):
                collect(None, Path('catalog'), Path('repo'), 'base', Path('artifacts'), target_sha='target')
            seed.assert_not_called()
