import unittest
from unittest.mock import patch

from test_recommendation.catalog import CatalogError
from test_recommendation.synthetic_issues import available_records, generate, imported_sources


class SyntheticIssueTests(unittest.TestCase):
    def manifest(self):
        return {'baseline': {'commit_sha': 'base'}, 'commits': [
            {'commit_sha': 'train', 'sequence': 1, 'split': 'train'},
            {'commit_sha': 'validation', 'sequence': 2, 'split': 'validation'},
            {'commit_sha': 'evaluation', 'sequence': 3, 'split': 'evaluation'}]}

    def record(self, linked='train', available='validation'):
        return dict(linked_commit_sha=linked, available_at_commit_sha=available, is_synthetic=True)

    def test_delayed_link_cannot_be_used_before_availability(self):
        record = self.record()
        self.assertEqual(available_records([record], self.manifest(), 'train'), [])
        self.assertEqual(available_records([record], self.manifest(), 'validation'), [record])

    def test_fixed_training_excludes_later_evidence(self):
        record = self.record(linked='validation', available='validation')
        self.assertEqual(available_records([record], self.manifest(), 'evaluation', policy='fixed-training-v1'), [])
        self.assertEqual(available_records([record], self.manifest(), 'evaluation'), [record])

    def test_future_target_link_and_invalid_ancestry_rejected(self):
        self.assertEqual(available_records([self.record('evaluation', 'evaluation')], self.manifest(), 'validation'), [])
        with self.assertRaises(CatalogError):
            available_records([self.record('evaluation', 'train')], self.manifest(), 'validation')

    def test_imports_are_historical_direct_production_dependencies(self):
        paths = imported_sources('from mesh_harbor.parcels.packet import Packet as P\nimport socket\n')
        self.assertIn('src/mesh_harbor/parcels/packet.py', paths)
        self.assertNotIn('src/socket.py', paths)

    def test_generation_uses_cochanges_not_intended_labels(self):
        suite = {'id': 'suite', 'test_file': 'tests/unit/test_example.py', 'available_since_sha': 'train'}
        entry = dict(commit_sha='train', parent_sha='base', sequence=1, subject='Validate packet',
                     committed_at='2026-01-01T00:00:00+00:00', change_category='validation',
                     intended_primary_suite='DELIBERATELY_WRONG')
        manifest = dict(corpus_id='corpus', history_sha256='digest', commits=[entry])
        catalog = dict(corpus_id='corpus', suites=[suite])
        def fake_git(repo, *args):
            if args[0] == 'diff':
                return b'src/mesh_harbor/parcels/packet.py\ntests/unit/test_example.py\n'
            if args[0] == 'show':
                return b'from mesh_harbor.parcels.packet import Packet\n'
            return b''
        with patch('test_recommendation.synthetic_issues.git', side_effect=fake_git), \
             patch('test_recommendation.synthetic_issues.test_methods', return_value=['test_packet']):
            dataset = generate(catalog, manifest, 'repo')
        self.assertEqual(len(dataset['records']), 2)
        self.assertTrue(all(record['suite_id'] == 'suite' and record['is_synthetic'] for record in dataset['records']))
        self.assertNotIn('DELIBERATELY_WRONG', str(dataset))
