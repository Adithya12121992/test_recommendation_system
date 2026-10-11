"""Deterministic, explicitly synthetic issue evidence from historical co-changes."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import pymysql

from test_recommendation.catalog import CatalogError, git, require, test_methods
from test_recommendation.coverage_collection import write_json
from test_recommendation.database import DatabaseConfig, canonical_json, initialize, load_inputs, seed, utc_datetime

VERSION = 'historical-issues-v1'


def imported_sources(source):
    """Direct absolute production imports in a historical test module."""
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith('mesh_harbor.'))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module and node.module.startswith('mesh_harbor.'):
            modules.add(node.module)
            modules.update(f'{node.module}.{alias.name}' for alias in node.names if alias.name != '*')
    return {'src/' + module.replace('.', '/') + '.py' for module in modules}


def generate(catalog, manifest, repo):
    records, gaps = [], []
    for index, entry in enumerate(manifest['commits']):
        revision = entry['commit_sha']
        # Consult actual history, not intended_primary_suite or ownership descriptions.
        changed = set(git(repo, 'diff', '--name-only', entry['parent_sha'], revision).decode().splitlines())
        paths = sorted(path for path in changed if path.startswith('src/mesh_harbor/') and path.endswith('.py'))
        count = 0
        for suite in catalog['suites']:
            if suite['test_file'] not in changed or not test_methods(repo, revision, suite, optional=True):
                continue
            imports = imported_sources(git(repo, 'show', f'{revision}:{suite["test_file"]}').decode())
            for path in paths:
                if path not in imports:
                    continue
                git(repo, 'cat-file', '-e', f'{revision}:{path}')
                git(repo, 'merge-base', '--is-ancestor', suite['available_since_sha'], revision)
                common = dict(corpus_id=catalog['corpus_id'], suite_id=suite['id'], linked_commit_sha=revision,
                              source_path=path, issue_created_at=entry['committed_at'], is_synthetic=True,
                              provenance='historical test/source co-change and direct test import')
                records.append(dict(**common, kind='jira', jira_key=f'SYN-{entry["sequence"]:03d}',
                                    available_at_commit_sha=revision,
                                    issue_summary=f'[SYNTHETIC] Work item: {entry["subject"]}'))
                count += 1
                # Fictional regressions only; no claim that the commit actually fails.
                if entry['change_category'] in ('validation', 'integration'):
                    delayed = manifest['commits'][min(index+1, len(manifest['commits'])-1)]['commit_sha']
                    records.append(dict(**common, kind='bugzilla', bug_id=100000+entry['sequence'],
                                        available_at_commit_sha=delayed, severity='medium',
                                        bug_summary=f'[SYNTHETIC] Regression scenario: {entry["subject"]}'))
        if not count:
            gaps.append(dict(commit_sha=revision, reason='No changed catalog test directly imports a changed source module'))
    return dict(generator_version=VERSION, corpus_id=catalog['corpus_id'], history_sha256=manifest['history_sha256'],
                label_policy='No expected-suite labels used; synthetic links are not fault-detection ground truth',
                records=records, unmapped_commits=gaps)


def available_records(records, manifest, base_sha, *, policy='rolling-parent-v1'):
    """Availability is ancestry order, independent of DB insertion timestamps."""
    positions = {manifest['baseline']['commit_sha']: 0}
    positions.update({entry['commit_sha']: entry['sequence'] for entry in manifest['commits']})
    require(base_sha in positions, 'Unknown evidence base revision')
    require(policy in ('rolling-parent-v1', 'fixed-training-v1'), 'Unknown issue evidence policy')
    limit = positions[base_sha]
    if policy == 'fixed-training-v1':
        limit = min(limit, max(entry['sequence'] for entry in manifest['commits'] if entry['split'] == 'train'))
    selected = []
    for record in records:
        linked, available = record['linked_commit_sha'], record['available_at_commit_sha']
        require(linked in positions and available in positions and positions[linked] <= positions[available], 'Invalid issue availability ancestry')
        require(record['is_synthetic'] is True, 'Unmarked synthetic evidence')
        if positions[available] <= limit:
            selected.append(record)
    return selected


def validate_records(dataset, catalog, manifest, repo):
    suites = {suite['id']: suite for suite in catalog['suites']}
    available_records(dataset['records'], manifest, catalog['product_snapshot_sha'])
    checked = set()
    for record in dataset['records']:
        require(record['corpus_id'] == catalog['corpus_id'] and record['suite_id'] in suites, 'Unknown issue corpus/suite')
        utc_datetime(record['issue_created_at'])
        for revision in (record['linked_commit_sha'], record['available_at_commit_sha']):
            identity = (revision, record['suite_id'], record['source_path'])
            if identity in checked:
                continue
            suite = suites[record['suite_id']]
            git(repo, 'cat-file', '-e', f'{revision}:{record["source_path"]}')
            git(repo, 'merge-base', '--is-ancestor', suite['available_since_sha'], revision)
            require(test_methods(repo, revision, suite, optional=True), 'Linked suite does not exist at evidence revision')
            checked.add(identity)


def insert_or_verify(cursor, record, catalog_key):
    table = 'jira_mapping' if record['kind'] == 'jira' else 'bugzilla_mapping'
    row = {key: value for key, value in record.items() if key not in ('kind', 'provenance')}
    row['catalog_key'] = catalog_key
    row['issue_created_at'] = utc_datetime(row['issue_created_at'])
    issue_column = 'jira_key' if record['kind'] == 'jira' else 'bug_id'
    keys = ('corpus_id', 'catalog_key', issue_column, 'suite_id', 'source_path')
    cursor.execute(f'SELECT * FROM {table} WHERE '+' AND '.join(f'{key}=%s' for key in keys)+' FOR UPDATE', tuple(row[key] for key in keys))
    existing = cursor.fetchall()
    require(len(existing) <= 1, 'Duplicate synthetic mapping identity')
    if existing:
        require(all(existing[0][key] == value for key, value in row.items()), 'Existing synthetic evidence differs; publish a new version')
    else:
        cursor.execute(f'INSERT INTO {table} ('+','.join(row)+') VALUES('+','.join(['%s']*len(row))+')', tuple(row.values()))


def run(config, catalog_path, repo, artifacts):
    initialize(config)
    catalog, manifest = load_inputs(catalog_path, repo)
    key = seed(config, catalog_path, repo)
    dataset = generate(catalog, manifest, repo)
    validate_records(dataset, catalog, manifest, repo)
    dataset['catalog_key'] = key
    content = canonical_json(dataset)
    digest = hashlib.sha256(content.encode()).hexdigest()
    lock = 'synthetic-issues:' + hashlib.sha256(config.database.encode()).hexdigest()[:24]
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s,10) AS acquired', (lock,))
            require(cursor.fetchone()['acquired'] == 1, 'Synthetic issue generation is already running')
            try:
                cursor.execute('''SELECT content_sha256 FROM synthetic_issue_datasets
                    WHERE corpus_id=%s AND catalog_key=%s AND generator_version=%s FOR UPDATE''',
                    (catalog['corpus_id'], key, VERSION))
                existing = cursor.fetchone()
                if existing:
                    require(existing['content_sha256'] == digest, 'Synthetic dataset changed under the same version')
                for record in dataset['records']:
                    insert_or_verify(cursor, record, key)
                if not existing:
                    cursor.execute('INSERT INTO synthetic_issue_datasets VALUES(%s,%s,%s,%s,%s)',
                                   (catalog['corpus_id'], key, VERSION, digest, content))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                cursor.execute('SELECT RELEASE_LOCK(%s)', (lock,))
    destination = artifacts.resolve() / VERSION
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'dataset.json', dataset)
    cases = []
    for entry in manifest['commits']:
        if entry['split'] != 'evaluation':
            continue
        parent = entry['parent_sha']
        eligible = available_records(dataset['records'], manifest, parent)
        # Filter records whose historical path/suite no longer exists at the base.
        suite_ids = {suite['id'] for suite in catalog['suites'] if test_methods(repo, parent, suite, optional=True)}
        paths = set(git(repo, 'ls-tree', '-r', '--name-only', parent, 'src').decode().splitlines())
        eligible = [record for record in eligible if record['suite_id'] in suite_ids and record['source_path'] in paths]
        cases.append(dict(target_commit_sha=entry['commit_sha'], base_revision_sha=parent,
                          eligible_jira_links=sum(record['kind'] == 'jira' for record in eligible),
                          eligible_bug_links=sum(record['kind'] == 'bugzilla' for record in eligible)))
    write_json(destination / 'evaluation_availability.json', dict(policy='rolling-parent-v1', cases=cases))
    jira = sum(record['kind'] == 'jira' for record in dataset['records'])
    print(f'Saved {jira} Jira links and {len(dataset["records"])-jira} bug links; {len(dataset["unmapped_commits"])} unmapped commits. Artifacts: {destination}')
    return dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-repo', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('test_catalog.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts/issues'))
    args = parser.parse_args()
    try:
        run(DatabaseConfig.load(args.env_file), args.catalog, args.product_repo, args.artifacts)
    except (CatalogError, OSError, SyntaxError, pymysql.MySQLError) as error:
        parser.exit(1, f'Synthetic issue generation failed: {error}\n')


if __name__ == '__main__':
    main()
