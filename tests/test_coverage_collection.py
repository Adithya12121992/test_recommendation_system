import unittest

from test_recommendation.catalog import CatalogError
from test_recommendation.coverage_collection import mappings, select_revision


class CoverageCollectionTests(unittest.TestCase):
    def report(self, path='src/mesh_harbor/example.py'):
        return {'meta': {'branch_coverage': True}, 'files': {path: {
            'summary': {'num_statements': 4, 'covered_lines': 3, 'num_branches': 2,
                        'covered_branches': 1, 'percent_covered': 66.67},
            'executed_lines': [1, 2, 3], 'missing_lines': [4]}}}

    def test_line_and_branch_percentages_are_distinct(self):
        row = mappings(self.report())[0]
        self.assertEqual(row['line_coverage'], 75)
        self.assertEqual(row['branch_coverage'], 50)

    def test_no_branches_has_no_branch_percentage(self):
        report = self.report()
        summary = next(iter(report['files'].values()))['summary']
        summary.update(num_branches=0, covered_branches=0)
        self.assertIsNone(mappings(report)[0]['branch_coverage'])

    def test_untrusted_paths_rejected(self):
        for path in ('/tmp/src/mesh_harbor/example.py', 'src/mesh_harbor/../example.py', 'tests/example.py'):
            with self.subTest(path=path), self.assertRaises(CatalogError):
                mappings(self.report(path))

    def test_inconsistent_measurements_rejected(self):
        report = self.report()
        next(iter(report['files'].values()))['missing_lines'] = [3]
        with self.assertRaises(CatalogError):
            mappings(report)

    def test_snapshot_cannot_be_training_evidence(self):
        manifest = {'commits': [{'commit_sha': 'a'*40, 'split': 'train'},
                                {'commit_sha': 'b'*40, 'split': 'validation'},
                                {'commit_sha': 'c'*40, 'split': 'evaluation'}]}
        self.assertEqual(select_revision(manifest, 'train'), ('a'*40, True))
        self.assertEqual(select_revision(manifest, 'snapshot'), ('c'*40, False))
        self.assertEqual(select_revision(manifest, 'b'*40), ('b'*40, False))
        with self.assertRaises(CatalogError):
            select_revision(manifest, 'unknown')
