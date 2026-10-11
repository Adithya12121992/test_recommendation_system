"""Create, migrate, and seed the local recommendation-system MySQL database."""
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re

from dotenv import dotenv_values
import pymysql

from test_recommendation.catalog import CatalogError, git, load_catalog, require, validate_product


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def load(cls, path='.env'):
        local = dotenv_values(path)
        values = {key: os.environ.get(key, local.get(key)) for key in
                  ('MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD')}
        require(all(isinstance(value, str) and value for value in values.values()), 'Set all five MYSQL_* variables in .env or the environment')
        require(re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', values['MYSQL_DATABASE']), 'Invalid database identifier')
        try:
            port = int(values['MYSQL_PORT'])
        except ValueError as error:
            raise CatalogError('MYSQL_PORT must be an integer') from error
        require(1 <= port <= 65535, 'MYSQL_PORT is outside the TCP port range')
        return cls(values['MYSQL_HOST'], port, values['MYSQL_DATABASE'], values['MYSQL_USER'], values['MYSQL_PASSWORD'])

    def connect(self, *, select_database=True):
        connection = pymysql.connect(host=self.host, port=self.port, user=self.user,
                                     password=self.password, database=self.database if select_database else None,
                                     charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
                                     autocommit=False, connect_timeout=5, read_timeout=30, write_timeout=30)
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        return connection


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def utc_datetime(value):
    timestamp = datetime.fromisoformat(value)
    require(timestamp.tzinfo is not None, 'Commit timestamps must include a timezone')
    return timestamp.astimezone(timezone.utc).replace(tzinfo=None)


def initialize(config):
    with config.connect(select_database=False) as connection:
        with connection.cursor() as cursor:
            # Identifier is strictly validated by DatabaseConfig.load.
            cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{config.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
    lock_name = 'recommendation:migration:' + hashlib.sha256(config.database.encode()).hexdigest()[:24]
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s, 10) AS acquired', (lock_name,))
            require(cursor.fetchone()['acquired'] == 1, 'Another schema migration holds the database lock')
            try:
                cursor.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INT PRIMARY KEY, checksum CHAR(64) NOT NULL,
                    status ENUM('applying','applied') NOT NULL, applied_at DATETIME(6) NULL
                ) ENGINE=InnoDB""")
                for name in ('001_initial.sql', '002_feedback.sql', '003_case_coverage.sql', '004_ast_graphs.sql', '005_synthetic_issues.sql'):
                    version = int(name.split('_')[0])
                    sql = files('test_recommendation').joinpath('sql/' + name).read_text()
                    checksum = hashlib.sha256(sql.encode()).hexdigest()
                    cursor.execute('SELECT * FROM schema_migrations WHERE version=%s', (version,))
                    migration = cursor.fetchone()
                    if migration:
                        require(migration['checksum'] == checksum, 'Applied migration checksum changed; add a new migration instead')
                        if migration['status'] == 'applied':
                            continue
                    else:
                        if version == 1:
                            cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME<>%s', (config.database, 'schema_migrations'))
                            require(not cursor.fetchall(), 'Refusing to initialize an existing database with untracked tables')
                        cursor.execute("INSERT INTO schema_migrations(version, checksum, status) VALUES(%s,%s,'applying')", (version, checksum))
                        connection.commit()
                    # Static DDL only; no embedded semicolons.
                    for statement in sql.split(';'):
                        if statement.strip():
                            cursor.execute(statement)
                    cursor.execute("UPDATE schema_migrations SET status='applied', applied_at=UTC_TIMESTAMP(6) WHERE version=%s", (version,))
                    connection.commit()
            finally:
                cursor.execute('SELECT RELEASE_LOCK(%s)', (lock_name,))


JSON_COLUMNS = {'manifest_json', 'catalog_json', 'changed_files', 'source_components', 'owned_paths'}
SEED_TABLES = {'corpora', 'commit_metadata', 'catalog_versions', 'test_suites'}


def save_immutable(cursor, table, keys, row):
    require(table in SEED_TABLES, 'Unknown seed table')
    columns = list(row)
    require(all(re.fullmatch('[a-z_][a-z0-9_]*', column) for column in columns), 'Invalid seed column')
    condition = ' AND '.join(f'`{key}`=%s' for key in keys)
    cursor.execute(f'SELECT * FROM `{table}` WHERE {condition} FOR UPDATE', tuple(row[key] for key in keys))
    existing = cursor.fetchone()
    if existing:
        for column, expected in row.items():
            actual = existing[column]
            if column in JSON_COLUMNS:
                actual, expected = json.loads(actual), json.loads(expected)
                require(canonical_json(actual) == canonical_json(expected), f'Existing immutable {table}.{column} differs')
            else:
                require(actual == expected, f'Existing immutable {table}.{column} differs')
        return
    names = ','.join(f'`{column}`' for column in columns)
    placeholders = ','.join(['%s'] * len(columns))
    cursor.execute(f'INSERT INTO `{table}` ({names}) VALUES({placeholders})', tuple(row[column] for column in columns))


def load_inputs(catalog_path, product_repo):
    catalog = load_catalog(catalog_path)
    validate_product(catalog, product_repo)
    manifest = json.loads(git(product_repo, 'show', 'HEAD:corpus_manifest.json'))
    require(manifest['corpus_id'] == catalog['corpus_id'], 'Catalog and manifest corpus IDs differ')
    require(manifest['snapshot']['commit_sha'] == catalog['product_snapshot_sha'], 'Catalog and manifest snapshots differ')
    commits = git(product_repo, 'rev-list', '--reverse', f'{manifest["baseline"]["commit_sha"]}..{catalog["product_snapshot_sha"]}').decode().splitlines()
    require(commits == [entry['commit_sha'] for entry in manifest['commits']] and len(commits) == 100, 'Manifest history does not match Git')
    digest = hashlib.sha256(('\n'.join(commits)+'\n').encode()).hexdigest()
    require(digest == manifest['history_sha256'], 'Manifest history digest differs')
    require([entry['sequence'] for entry in manifest['commits']] == list(range(1, 101)), 'Manifest sequence numbers differ')
    for anchor in ('baseline', 'snapshot'):
        require(git(product_repo, 'rev-parse', f'refs/tags/{manifest[anchor]["tag"]}^{{commit}}').decode().strip() == manifest[anchor]['commit_sha'], 'Pinned corpus tag differs')
    return catalog, manifest


def seed(config, catalog_path, product_repo):
    catalog, manifest = load_inputs(catalog_path, product_repo)
    corpus_id = catalog['corpus_id']
    catalog_key = hashlib.sha256(canonical_json(catalog).encode()).hexdigest()
    baseline_sha = manifest['baseline']['commit_sha']
    baseline = git(product_repo, 'show', '-s', '--format=%P%n%T%n%aI%n%cI%n%s', baseline_sha).decode().splitlines()
    parents, tree_sha, authored_at, committed_at, subject = baseline
    baseline_row = dict(corpus_id=corpus_id, commit_sha=baseline_sha, parent_sha=parents or None, tree_sha=tree_sha,
                        sequence_number=None, split_name='baseline', commit_message=subject, change_category='baseline',
                        changed_files='[]', source_components='[]', authored_at=utc_datetime(authored_at), committed_at=utc_datetime(committed_at))
    with config.connect() as connection:
        try:
            with connection.cursor() as cursor:
                save_immutable(cursor, 'corpora', ('corpus_id',), dict(corpus_id=corpus_id,
                    baseline_sha=baseline_sha, snapshot_sha=catalog['product_snapshot_sha'],
                    history_sha256=manifest['history_sha256'], manifest_json=canonical_json(manifest)))
                save_immutable(cursor, 'commit_metadata', ('corpus_id', 'commit_sha'), baseline_row)
                for entry in manifest['commits']:
                    save_immutable(cursor, 'commit_metadata', ('corpus_id', 'commit_sha'), dict(
                        corpus_id=corpus_id, commit_sha=entry['commit_sha'], parent_sha=entry['parent_sha'], tree_sha=entry['tree_sha'],
                        sequence_number=entry['sequence'], split_name=entry['split'], commit_message=entry['subject'],
                        change_category=entry['change_category'], changed_files=canonical_json(entry['changed_files']),
                        source_components=canonical_json(entry['source_components']), authored_at=utc_datetime(entry['authored_at']),
                        committed_at=utc_datetime(entry['committed_at'])))
                save_immutable(cursor, 'catalog_versions', ('catalog_key',), dict(catalog_key=catalog_key, corpus_id=corpus_id,
                    catalog_version=catalog['catalog_version'], product_snapshot_sha=catalog['product_snapshot_sha'], catalog_json=canonical_json(catalog)))
                for suite in catalog['suites']:
                    save_immutable(cursor, 'test_suites', ('catalog_key', 'suite_id'), dict(catalog_key=catalog_key, corpus_id=corpus_id,
                        suite_id=suite['id'], area_suite=suite['area_suite'], unittest_target=suite['unittest_target'], test_file=suite['test_file'],
                        description=suite['description'], owned_paths=canonical_json(suite['owned_paths']), available_since_sha=suite['available_since_sha'],
                        estimated_runtime_seconds=suite['estimated_runtime_seconds']))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return catalog_key


def status(config):
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT TABLE_NAME AS table_name FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME', (config.database,))
            tables = [row['table_name'] for row in cursor.fetchall()]
            counts = {}
            for table in tables:
                require(re.fullmatch('[a-z_]+', table), 'Unexpected table identifier')
                cursor.execute(f'SELECT COUNT(*) AS total FROM `{table}`')
                counts[table] = cursor.fetchone()['total']
    return {'host': config.host, 'port': config.port, 'database': config.database, 'table_counts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'seed', 'status'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--catalog', type=Path, default=Path('test_catalog.yaml'))
    parser.add_argument('--product-repo', type=Path)
    args = parser.parse_args()
    try:
        config = DatabaseConfig.load(args.env_file)
        if args.command == 'init':
            initialize(config)
            print(f'Initialized {config.database} with all packaged migrations.')
        elif args.command == 'seed':
            require(args.product_repo is not None, '--product-repo is required for seed')
            key = seed(config, args.catalog, args.product_repo)
            print(f'Imported corpus baseline, 100 evolution commits, and 20 suites. Catalog key: {key}')
        else:
            print(json.dumps(status(config), indent=2))
    except (CatalogError, OSError, ValueError, pymysql.MySQLError) as error:
        parser.exit(1, f'Database operation failed: {error}\n')


if __name__ == '__main__':
    main()
