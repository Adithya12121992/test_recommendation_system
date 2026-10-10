import copy
from pathlib import Path
import tempfile
import unittest

import yaml

from test_recommendation.catalog import CatalogError, load_catalog

CATALOG = Path(__file__).resolve().parents[1] / 'test_catalog.yaml'


class CatalogValidationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(CATALOG)

    def reject(self, catalog):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.yaml'
            path.write_text(yaml.safe_dump(catalog))
            with self.assertRaises(CatalogError):
                load_catalog(path)

    def test_exact_target_count(self):
        self.assertEqual(len(self.catalog['suites']), 20)
        self.catalog['suites'].pop()
        self.reject(self.catalog)

    def test_duplicate_identifiers_names_and_targets(self):
        for field in ('id', 'area_suite', 'unittest_target'):
            catalog = copy.deepcopy(self.catalog)
            catalog['suites'][1][field] = catalog['suites'][0][field]
            if field == 'unittest_target':
                catalog['suites'][1]['test_file'] = catalog['suites'][0]['test_file']
            with self.subTest(field=field):
                self.reject(catalog)

    def test_duplicate_yaml_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.yaml'
            path.write_text('schema_version: 1\nschema_version: 2\n')
            with self.assertRaises(CatalogError):
                load_catalog(path)

    def test_targets_and_paths_cannot_escape_the_product(self):
        for field, value in (('test_file', '../tests/unsafe.py'),
                             ('unittest_target', 'os.system'),
                             ('owned_paths', ['src/mesh_harbor/../../unsafe.py'])):
            catalog = copy.deepcopy(self.catalog)
            catalog['suites'][0][field] = value
            with self.subTest(field=field):
                self.reject(catalog)

    def test_runtime_estimates_require_valid_measurement_provenance(self):
        for value, source in ((True, 'measured'), (-1, 'measured'), (float('nan'), 'measured'), (1, 'unmeasured'), (None, 'measured')):
            catalog = copy.deepcopy(self.catalog)
            catalog['suites'][0]['estimated_runtime_seconds'] = value
            catalog['suites'][0]['runtime_estimate_source'] = source
            with self.subTest(value=value, source=source):
                self.reject(catalog)

    def test_version_and_revision_types_are_strict(self):
        for field, value in (('schema_version', True), ('catalog_version', 0), ('product_snapshot_sha', 'main')):
            catalog = copy.deepcopy(self.catalog)
            catalog[field] = value
            with self.subTest(field=field):
                self.reject(catalog)


if __name__ == '__main__':
    unittest.main()
