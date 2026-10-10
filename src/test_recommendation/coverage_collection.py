"""Measure independent suites at a pinned revision and retain their evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid

import coverage
import pymysql

from test_recommendation.catalog import CatalogError, extract_snapshot, git, require, test_methods, valid_path
from test_recommendation.database import DatabaseConfig, canonical_json, load_inputs, seed


def mappings(report):
    """Keep line percentages separate from coverage.py's combined branch score."""
    rows = []
    require(report.get('meta', {}).get('branch_coverage') is True, 'Branch measurement missing')
    for path, item in report['files'].items():
        valid_path(path, 'src/mesh_harbor/')
        summary = item['summary']
        total, covered = summary['num_statements'], summary['covered_lines']
        executed, missing = item['executed_lines'], item['missing_lines']
        require(0 <= covered <= total and covered == len(set(executed)) and
                total == covered + len(set(missing)) and not set(executed).intersection(missing), 'Invalid line counts')
        branches, hit = summary['num_branches'], summary['covered_branches']
        require(0 <= hit <= branches, 'Invalid branch counts')
        if total:
            rows.append(dict(source_path=path, executable_line_count=total, covered_line_count=covered,
                             line_coverage=100*covered/total, branch_coverage=100*hit/branches if branches else None,
                             executed_lines=executed, missing_lines=missing))
    return rows


def select_revision(manifest, requested):
    if requested == 'train':
        entry = [item for item in manifest['commits'] if item['split'] == 'train'][-1]
    elif requested == 'snapshot':
        entry = manifest['commits'][-1]
    else:
        entries = [item for item in manifest['commits'] if item['commit_sha'] == requested]
        require(len(entries) == 1, 'Revision must be train, snapshot, or a full corpus commit SHA')
        entry = entries[0]
    return entry['commit_sha'], entry['split'] == 'train'


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def collect(config, catalog_path, repo, requested, artifact_root, *, target_sha=None):
    catalog, manifest = load_inputs(catalog_path, repo)
    revision, train_evidence = select_revision(manifest, requested)
    if target_sha is not None:
        targets = [entry for entry in manifest['commits'] if entry['commit_sha'] == target_sha]
        require(len(targets) == 1 and targets[0]['split'] == 'evaluation', 'Target must be an evaluation commit')
        require(targets[0]['parent_sha'] == revision, 'Rolling coverage must measure the exact target parent')
        require(git(repo, 'rev-parse', f'{target_sha}^').decode().strip() == revision, 'Target parent differs from Git')
    catalog_key = seed(config, catalog_path, repo)
    eligible, unavailable = [], []
    for suite in catalog['suites']:
        methods = test_methods(repo, revision, suite, optional=True)
        if methods:
            eligible.append((suite, len(methods)))
        else:
            unavailable.append(suite['id'])
    require(eligible, 'No catalog suites exist at this revision')
    run_id = str(uuid.uuid4())
    destination = artifact_root.resolve() / revision / run_id
    destination.mkdir(parents=True)
    environment_info = {'platform': platform.platform(), 'purpose': 'training_evidence' if train_evidence else 'diagnostic',
                        'usable_for_held_out_evaluation': train_evidence, 'unavailable_suite_ids': unavailable}
    if target_sha is not None:
        environment_info.update(purpose='rolling_parent_evidence', eligible_target_sha=target_sha)
    report = dict(run_id=run_id, revision_sha=revision, catalog_key=catalog_key,
                  **environment_info, status='running', suites=[])
    source_files = set(git(repo, 'ls-tree', '-r', '--name-only', revision, 'src').decode().splitlines())
    index = {}
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''INSERT INTO coverage_runs(run_id,catalog_key,corpus_id,revision_sha,
                coverage_tool_version,python_version,measurement_kind,status,started_at,environment_json)
                VALUES(%s,%s,%s,%s,%s,%s,'branch','running',%s,%s)''',
                (run_id, catalog_key, catalog['corpus_id'], revision, coverage.__version__, platform.python_version(),
                 datetime.now(timezone.utc).replace(tzinfo=None), canonical_json(environment_info)))
        connection.commit()
        try:
            with tempfile.TemporaryDirectory(prefix='mesh-coverage-') as temporary:
                checkout = Path(temporary)
                extract_snapshot(repo, revision, checkout)
                (checkout / '.coveragerc').write_text('[run]\nbranch = True\nrelative_files = True\nsource = src/mesh_harbor\n')
                for suite, expected in eligible:
                    directory = destination / suite['id']
                    directory.mkdir()
                    data_path = directory / '.coverage'
                    json_path = directory / 'coverage.json'
                    output_path = directory / 'test_output.txt'
                    env = dict(os.environ, PYTHONPATH=str(checkout / 'src'), PYTHONDONTWRITEBYTECODE='1', COVERAGE_FILE=str(data_path))
                    started = time.perf_counter()
                    try:
                        result = subprocess.run([sys.executable, '-B', '-m', 'coverage', 'run', '-m', 'unittest', suite['unittest_target'], '-v'],
                                                cwd=checkout, env=env, capture_output=True, text=True, timeout=60)
                        output = result.stdout + result.stderr
                        count = re.search(r'Ran (\d+) tests?', output)
                        measured_count = int(count[1]) if count else 0
                        passed = result.returncode == 0 and measured_count == expected
                    except subprocess.TimeoutExpired:
                        output, measured_count, passed = 'Suite timed out after 60 seconds.\n', 0, False
                    elapsed = time.perf_counter() - started
                    output_path.write_text(output)
                    raw, rows = {}, []
                    if data_path.exists():
                        converted = subprocess.run([sys.executable, '-B', '-m', 'coverage', 'json', '-o', str(json_path)],
                                                   cwd=checkout, env=env, capture_output=True, text=True, timeout=30)
                        require(converted.returncode == 0, f'Coverage JSON failed: {converted.stderr}')
                        raw = json.loads(json_path.read_text())
                        rows = mappings(raw)
                        require(all(row['source_path'] in source_files for row in rows), 'Coverage contains a path outside the pinned source tree')
                    else:
                        passed = False
                    with connection.cursor() as cursor:
                        cursor.execute('''INSERT INTO coverage_suite_results VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                                       (run_id, catalog_key, suite['id'], passed, measured_count, elapsed, canonical_json(raw)))
                        for row in rows:
                            cursor.execute('''INSERT INTO coverage_mapping VALUES(%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s)''',
                                (run_id, suite['id'], row['source_path'], row['executable_line_count'], row['covered_line_count'],
                                 row['line_coverage'], row['branch_coverage'], canonical_json(row['executed_lines']), canonical_json(row['missing_lines'])))
                            if passed and row['covered_line_count']:
                                index.setdefault(row['source_path'], []).append({'suite_id': suite['id'], 'executed_lines': row['executed_lines']})
                        for kind, path, media in [('coverage_data', data_path, 'application/octet-stream'),
                                                   ('json_report', json_path, 'application/json'), ('test_output', output_path, 'text/plain')]:
                            if path.exists():
                                content = path.read_bytes()
                                cursor.execute('INSERT INTO coverage_artifacts VALUES(%s,%s,%s,%s,%s,%s)',
                                               (run_id, suite['id'], kind, hashlib.sha256(content).hexdigest(), media, content))
                    connection.commit()
                    report['suites'].append(dict(suite_id=suite['id'], passed=passed, test_case_count=measured_count,
                                                 expected_test_case_count=expected, elapsed_seconds=elapsed, mapped_files=len(rows)))
                    write_json(destination / 'run.json', report)
                    print(f'{suite["id"]}: {"passed" if passed else "FAILED"} ({measured_count} cases)', flush=True)
            report['status'] = 'passed' if all(item['passed'] for item in report['suites']) else 'failed'
        except BaseException as error:
            connection.rollback()
            report['status'] = 'failed'
            report['error'] = str(error)
            raise
        finally:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE coverage_runs SET status=%s,completed_at=UTC_TIMESTAMP(6) WHERE run_id=%s', (report['status'], run_id))
            connection.commit()
            write_json(destination / 'run.json', report)
            write_json(destination / 'coverage_index.json', {'run_id': run_id, 'revision_sha': revision,
                       'catalog_key': catalog_key, 'usable_for_held_out_evaluation': train_evidence and report['status'] == 'passed', 'files': index})
    require(report['status'] == 'passed', f'Some suites failed; inspect {destination}')
    print(f'Saved {len(eligible)} suites to MySQL and {destination}')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-repo', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('test_catalog.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--revision', default='train', help='train (default), snapshot, or full corpus SHA')
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts/coverage'))
    args = parser.parse_args()
    try:
        collect(DatabaseConfig.load(args.env_file), args.catalog, args.product_repo, args.revision, args.artifacts)
    except (CatalogError, OSError, ValueError, subprocess.SubprocessError, pymysql.MySQLError) as error:
        parser.exit(1, f'Coverage collection failed: {error}\n')


if __name__ == '__main__':
    main()
