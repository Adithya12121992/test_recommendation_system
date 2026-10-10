from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_recommendation.catalog import CatalogError
from test_recommendation.database import DatabaseConfig, canonical_json, save_immutable, utc_datetime


class DatabaseConfigurationTests(unittest.TestCase):
    def config(self, database='example_db'):
        return '\n'.join(('MYSQL_HOST=localhost', 'MYSQL_PORT=3306', f'MYSQL_DATABASE={database}',
                          'MYSQL_USER=example_user', 'MYSQL_PASSWORD="fictional test password"'))

    def test_dotenv_quotes_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text(self.config())
            with patch.dict('os.environ', {'MYSQL_PORT': '3308'}, clear=True):
                config = DatabaseConfig.load(path)
            self.assertEqual(config.port, 3308)
            self.assertEqual(config.password, 'fictional test password')
            self.assertNotIn(config.password, repr(config))

    def test_database_identifiers_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            for name in ('db;DROP DATABASE other', '../other', 'a`b', '1db'):
                path.write_text(self.config(name))
                with patch.dict('os.environ', {}, clear=True), self.assertRaises(CatalogError):
                    DatabaseConfig.load(path)

    def test_missing_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('MYSQL_HOST=localhost\n')
            with patch.dict('os.environ', {}, clear=True), self.assertRaises(CatalogError):
                DatabaseConfig.load(path)

    def test_commit_timestamps_are_normalized_to_utc(self):
        self.assertEqual(utc_datetime('2026-10-09T18:00:00-07:00'), datetime(2026, 10, 10, 1))
        with self.assertRaises(CatalogError):
            utc_datetime('2026-10-09T18:00:00')

    def test_immutable_import_compares_json_semantically(self):
        cursor = Mock()
        cursor.fetchone.return_value = {'corpus_id': 'fixture', 'manifest_json': '{"b":2,"a":1}'}
        save_immutable(cursor, 'corpora', ('corpus_id',), {'corpus_id': 'fixture', 'manifest_json': canonical_json({'a': 1, 'b': 2})})
        self.assertEqual(cursor.execute.call_count, 1)

    def test_immutable_import_rejects_different_data(self):
        cursor = Mock()
        cursor.fetchone.return_value = {'corpus_id': 'fixture', 'manifest_json': '{"a":2}'}
        with self.assertRaises(CatalogError):
            save_immutable(cursor, 'corpora', ('corpus_id',), {'corpus_id': 'fixture', 'manifest_json': '{"a":1}'})


if __name__ == '__main__':
    unittest.main()
