"""Opt-in checks against the configured MySQL DB. Every write is rolled back."""
from datetime import datetime, timezone
import hashlib
import json
import os
import unittest
from uuid import uuid4

import pymysql
from test_recommendation.database import DatabaseConfig


@unittest.skipUnless(os.environ.get('RUN_MYSQL_TESTS') == '1', 'Set RUN_MYSQL_TESTS=1 for transactional MySQL checks')
class MySQLStorageTests(unittest.TestCase):
    def setUp(self):
        self.connection = DatabaseConfig.load().connect()
        self.connection.begin()
        self.cursor = self.connection.cursor()
        self.cursor.execute("SELECT catalog_key, corpus_id, product_snapshot_sha FROM catalog_versions WHERE corpus_id='mesh-harbor-v1' AND catalog_version=1")
        self.catalog = self.cursor.fetchone()
        self.assertIsNotNone(self.catalog)
        self.run_id = str(uuid4())
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)

    def tearDown(self):
        self.connection.rollback()
        self.cursor.close()
        self.connection.close()

    def test_feedback_links_run_and_catalog_and_requires_review_metadata(self):
        self.cursor.execute("""INSERT INTO recommendation_runs
            (run_id,catalog_key,corpus_id,target_commit_sha,evidence_revision_sha,status,configuration_json)
            VALUES(%s,%s,%s,%s,%s,'passed','{}')""",
            (self.run_id, self.catalog['catalog_key'], self.catalog['corpus_id'],
             self.catalog['product_snapshot_sha'], self.catalog['product_snapshot_sha']))
        feedback_id = str(uuid4())
        self.cursor.execute("""INSERT INTO recommendation_feedback
            (feedback_id,run_id,catalog_key,suite_id,user_id,action,reason_category,reason)
            VALUES(%s,%s,%s,'relay_routes','test-reviewer','accept','relevance','Relevant routing suite')""",
            (feedback_id, self.run_id, self.catalog['catalog_key']))
        self.cursor.execute('SELECT review_status FROM recommendation_feedback WHERE feedback_id=%s', (feedback_id,))
        self.assertEqual(self.cursor.fetchone()['review_status'], 'pending')
        with self.assertRaises(pymysql.MySQLError):
            self.cursor.execute("UPDATE recommendation_feedback SET review_status='approved' WHERE feedback_id=%s", (feedback_id,))
        self.cursor.execute("""UPDATE recommendation_feedback SET review_status='approved',
            reviewed_by='test-reviewer', reviewed_at=UTC_TIMESTAMP(6) WHERE feedback_id=%s""", (feedback_id,))

    def coverage_run(self):
        self.cursor.execute('''INSERT INTO coverage_runs
            (run_id,catalog_key,corpus_id,revision_sha,coverage_tool_version,python_version,
             measurement_kind,status,started_at,environment_json)
            VALUES(%s,%s,%s,%s,'integration-fixture','integration-fixture','line','passed',%s,'{}')''',
            (self.run_id, self.catalog['catalog_key'], self.catalog['corpus_id'], self.catalog['product_snapshot_sha'], self.now))

    def suite_result(self, suite_id='relay_routes'):
        self.cursor.execute('''INSERT INTO coverage_suite_results
            (run_id,catalog_key,suite_id,passed,test_case_count,elapsed_seconds,raw_report_json)
            VALUES(%s,%s,%s,TRUE,1,0.01,%s)''',
            (self.run_id, self.catalog['catalog_key'], suite_id, json.dumps({'fixture': True})))

    def test_coverage_json_lines_and_binary_artifacts_round_trip(self):
        self.coverage_run()
        self.suite_result()
        self.cursor.execute('''INSERT INTO coverage_mapping
            (run_id,suite_id,source_path,executable_line_count,covered_line_count,line_coverage,executed_lines,missing_lines)
            VALUES(%s,'relay_routes','src/mesh_harbor/relay/routing/route_table.py',2,1,50,'[1]','[2]')''', (self.run_id,))
        content = b'fictional transactional artifact\x00\xff'
        self.cursor.execute('''INSERT INTO coverage_artifacts
            (run_id,suite_id,artifact_kind,content_sha256,media_type,content)
            VALUES(%s,'relay_routes','coverage_data',%s,'application/octet-stream',%s)''',
            (self.run_id, hashlib.sha256(content).hexdigest(), content))
        self.cursor.execute('SELECT content FROM coverage_artifacts WHERE run_id=%s', (self.run_id,))
        self.assertEqual(self.cursor.fetchone()['content'], content)
        self.cursor.execute('SELECT executed_lines, function_coverage FROM coverage_mapping WHERE run_id=%s', (self.run_id,))
        row = self.cursor.fetchone()
        self.assertEqual(json.loads(row['executed_lines']), [1])
        self.assertIsNone(row['function_coverage'])

    def test_unknown_suite_is_rejected_by_foreign_key(self):
        self.coverage_run()
        with self.assertRaises(pymysql.IntegrityError):
            self.suite_result('nonexistent_suite')

    def test_issue_availability_and_synthetic_constraint(self):
        values = (self.catalog['corpus_id'], self.catalog['catalog_key'], self.catalog['product_snapshot_sha'], self.catalog['product_snapshot_sha'], self.now)
        statement = '''INSERT INTO jira_mapping
            (corpus_id,catalog_key,suite_id,jira_key,linked_commit_sha,available_at_commit_sha,
             source_path,issue_summary,issue_created_at,is_synthetic)
            VALUES(%s,%s,'relay_routes','TEST-ROLLBACK',%s,%s,
              'src/mesh_harbor/relay/routing/route_table.py','Transactional synthetic fixture',%s,{synthetic})'''
        self.cursor.execute(statement.format(synthetic='TRUE'), values)
        self.cursor.execute('SELECT available_at_commit_sha FROM jira_mapping WHERE id=LAST_INSERT_ID()')
        self.assertEqual(self.cursor.fetchone()['available_at_commit_sha'], self.catalog['product_snapshot_sha'])
        with self.assertRaises(pymysql.MySQLError):
            self.cursor.execute(statement.format(synthetic='FALSE'), values)


if __name__ == '__main__':
    unittest.main()
