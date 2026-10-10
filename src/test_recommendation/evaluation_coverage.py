"""Collect and pin exact-parent coverage for the 20 reserved evaluation changes.

Rolling replay incorporates previous changes after they have landed; it is a
separate policy from a fixed training cutoff, and does not establish golden labels.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pymysql

from test_recommendation.catalog import CatalogError, git, require, test_methods
from test_recommendation.coverage_collection import collect, write_json
from test_recommendation.database import DatabaseConfig, canonical_json, initialize, load_inputs

POLICY = 'rolling-parent-v1'


def validate_run(cursor, run_id, catalog_key, revision, target, expected):
    cursor.execute('SELECT * FROM coverage_runs WHERE run_id=%s', (run_id,))
    run = cursor.fetchone()
    require(run and run['status'] == 'passed' and run['catalog_key'] == catalog_key and
            run['revision_sha'] == revision, 'Coverage run is failed or incompatible')
    environment = json.loads(run['environment_json'])
    require(environment.get('purpose') == 'rolling_parent_evidence' and
            environment.get('eligible_target_sha') == target, 'Run is not evidence for this rolling case')
    cursor.execute('SELECT suite_id,passed,test_case_count FROM coverage_suite_results WHERE run_id=%s', (run_id,))
    results = cursor.fetchall()
    require({row['suite_id']: row['test_case_count'] for row in results} == expected and
            all(row['passed'] for row in results), 'Coverage suite results are incomplete')
    cursor.execute('SELECT suite_id,artifact_kind,content_sha256,content FROM coverage_artifacts WHERE run_id=%s', (run_id,))
    artifacts = cursor.fetchall()
    require({(row['suite_id'], row['artifact_kind']) for row in artifacts} ==
            {(suite, kind) for suite in expected for kind in ('coverage_data', 'json_report', 'test_output')}, 'Coverage artifacts are incomplete')
    require(all(hashlib.sha256(row['content']).hexdigest() == row['content_sha256'] for row in artifacts), 'Artifact checksum mismatch')


def build(config, catalog_path, repo, artifacts):
    initialize(config)
    catalog, manifest = load_inputs(catalog_path, repo)
    key = hashlib.sha256(canonical_json(catalog).encode()).hexdigest()
    cases = [entry for entry in manifest['commits'] if entry['split'] == 'evaluation']
    require(len(cases) == 20, 'Expected 20 reserved evaluation commits')
    output = artifacts.resolve() / 'evaluation_cases' / POLICY
    output.mkdir(parents=True, exist_ok=True)
    summary = {'policy_version': POLICY, 'catalog_key': key, 'corpus_id': catalog['corpus_id'],
               'golden_labels_reviewed': False, 'cases': []}
    for entry in cases:
        target, parent = entry['commit_sha'], entry['parent_sha']
        require(git(repo, 'rev-parse', f'{target}^').decode().strip() == parent, 'Manifest parent differs from Git')
        expected = {suite['id']: len(test_methods(repo, parent, suite, optional=True)) for suite in catalog['suites']}
        missing = [suite for suite, count in expected.items() if not count]
        expected = {suite: count for suite, count in expected.items() if count}
        with config.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('''SELECT * FROM evaluation_case_coverage WHERE corpus_id=%s AND target_commit_sha=%s
                    AND catalog_key=%s AND policy_version=%s''', (catalog['corpus_id'], target, key, POLICY))
                link = cursor.fetchone()
                if link:
                    require(link['parent_sha'] == parent and json.loads(link['evidence_gaps_json']) == missing, 'Existing case linkage differs')
                    run_id = link['run_id']
                    validate_run(cursor, run_id, key, parent, target, expected)
                else:
                    run_id = None
        if run_id is None:
            measured = collect(config, catalog_path, repo, parent, artifacts, target_sha=target)
            run_id = measured['run_id']
            with config.connect() as connection:
                with connection.cursor() as cursor:
                    validate_run(cursor, run_id, key, parent, target, expected)
                    cursor.execute('''INSERT INTO evaluation_case_coverage
                        (corpus_id,target_commit_sha,parent_sha,catalog_key,run_id,policy_version,evidence_gaps_json)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                        (catalog['corpus_id'], target, parent, key, run_id, POLICY, canonical_json(missing)))
                connection.commit()
        case = {'case_id': f'E{entry["sequence"]-80:03d}', 'sequence': entry['sequence'],
                'target_commit_sha': target, 'base_revision_sha': parent, 'coverage_run_id': run_id,
                'available_suite_ids': sorted(expected), 'unavailable_suite_ids': missing,
                'suite_count': len(expected), 'test_case_count': sum(expected.values()),
                'coverage_artifact_directory': str(artifacts.resolve() / parent / run_id)}
        summary['cases'].append(case)
        write_json(output / 'index.json', summary)
        print(f'{case["case_id"]}: {target[:7]} uses parent {parent[:7]}, {len(expected)} suites; {len(missing)} unavailable', flush=True)
    print(f'Pinned coverage for {len(cases)} cases: {output / "index.json"}')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-repo', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('test_catalog.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts/coverage'))
    args = parser.parse_args()
    try:
        build(DatabaseConfig.load(args.env_file), args.catalog, args.product_repo, args.artifacts)
    except (CatalogError, OSError, ValueError, pymysql.MySQLError) as error:
        parser.exit(1, f'Evaluation coverage failed: {error}\n')


if __name__ == '__main__':
    main()
