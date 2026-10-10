"""Validate and independently run the pinned 20-suite recommendation catalog."""
import argparse
import ast
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time

import yaml


class CatalogError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently replacing values."""

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise CatalogError('YAML mapping keys must be unique strings')
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def require(condition, message):
    if not condition:
        raise CatalogError(message)


def valid_path(value, prefix):
    require(isinstance(value, str), 'File paths must be strings')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and value.startswith(prefix)
            and path.as_posix() == value and value.endswith('.py'), f'Invalid path: {value}')


def load_catalog(path):
    try:
        catalog = yaml.load(Path(path).read_text(), Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise CatalogError(f'Invalid catalog YAML: {error}') from error
    require(isinstance(catalog, dict), 'Catalog must be a mapping')
    require(set(catalog) == {'schema_version', 'catalog_version', 'corpus_id', 'product_snapshot_sha', 'suites'}, 'Unexpected catalog fields')
    require(type(catalog['schema_version']) is int and catalog['schema_version'] == 1, 'Unsupported schema version')
    require(type(catalog['catalog_version']) is int and catalog['catalog_version'] > 0, 'Invalid catalog version')
    require(catalog['corpus_id'] == 'mesh-harbor-v1', 'Unexpected corpus')
    require(isinstance(catalog['product_snapshot_sha'], str) and re.fullmatch('[0-9a-f]{40}', catalog['product_snapshot_sha']), 'Snapshot must be a full SHA')
    suites = catalog['suites']
    require(isinstance(suites, list) and len(suites) == 20, 'Catalog must contain exactly 20 suites')
    required = {'id', 'area_suite', 'pipeline', 'description', 'unittest_target', 'test_file',
                'available_since_sha', 'owned_paths', 'estimated_runtime_seconds', 'runtime_estimate_source'}
    for suite in suites:
        require(isinstance(suite, dict) and set(suite) == required, 'Unexpected suite fields')
        for field in ('id', 'area_suite', 'description', 'unittest_target', 'available_since_sha'):
            require(isinstance(suite[field], str) and suite[field].strip(), f'Invalid {field}')
        require(re.fullmatch('[a-z][a-z0-9_]*', suite['id']), 'Invalid suite ID')
        require(re.fullmatch('[A-Za-z]+::[A-Za-z]+', suite['area_suite']), 'Invalid suite name')
        require(suite['pipeline'] == 'verification', 'Unexpected pipeline')
        require(re.fullmatch(r'tests\.unit\.test_[a-z_]+\.[A-Za-z]+Tests', suite['unittest_target']), 'Invalid runnable target')
        require(re.fullmatch('[0-9a-f]{40}', suite['available_since_sha']), 'Introduction must be a full SHA')
        valid_path(suite['test_file'], 'tests/')
        require(suite['test_file'] == suite['unittest_target'].rsplit('.', 1)[0].replace('.', '/') + '.py', 'Test file and target disagree')
        require(isinstance(suite['owned_paths'], list) and suite['owned_paths'], 'Suite must own source paths')
        for path_value in suite['owned_paths']:
            valid_path(path_value, 'src/mesh_harbor/')
        require(len(set(suite['owned_paths'])) == len(suite['owned_paths']), 'Duplicate owned paths')
        runtime = suite['estimated_runtime_seconds']
        require(runtime is None or type(runtime) in (int, float) and math.isfinite(runtime) and runtime > 0, 'Invalid runtime estimate')
        require(suite['runtime_estimate_source'] == ('unmeasured' if runtime is None else 'measured'), 'Runtime provenance disagrees with estimate')
    for field in ('id', 'area_suite', 'unittest_target'):
        require(len({suite[field] for suite in suites}) == 20, f'Duplicate {field}')
    return catalog


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    require(result.returncode == 0, f'Git operation failed: {result.stderr.decode().strip()}')
    return result.stdout


def test_methods(repo, revision, suite, *, optional=False):
    result = subprocess.run(['git', '-C', str(repo), 'show', f'{revision}:{suite["test_file"]}'], capture_output=True)
    if result.returncode and optional:
        return []
    require(result.returncode == 0, f'Missing target file for {suite["id"]} at {revision}')
    class_name = suite['unittest_target'].rsplit('.', 1)[1]
    for node in ast.parse(result.stdout).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            require(any(ast.unparse(base) in ('unittest.TestCase', 'TestCase') for base in node.bases), 'Target is not a unittest suite')
            return [method.name for method in node.body if isinstance(method, ast.FunctionDef) and method.name.startswith('test_')]
    return []


def validate_product(catalog, repo):
    revision = catalog['product_snapshot_sha']
    require(git(repo, 'rev-parse', f'{revision}^{{commit}}').decode().strip() == revision, 'Invalid snapshot')
    readme = git(repo, 'show', f'{revision}:README.md').decode()
    intended = set(re.findall(r'Primary regression suite: `([^`]+)`', readme))
    require(intended == {suite['unittest_target'] for suite in catalog['suites']}, 'Catalog differs from the 20 corpus primary suites')
    results = []
    for suite in catalog['suites']:
        methods = test_methods(repo, revision, suite)
        require(methods, f'Empty or missing suite: {suite["id"]}')
        for source in suite['owned_paths']:
            git(repo, 'cat-file', '-e', f'{revision}:{source}')
        introduced = suite['available_since_sha']
        git(repo, 'merge-base', '--is-ancestor', introduced, revision)
        require(test_methods(repo, introduced, suite), f'Suite not present at introduction: {suite["id"]}')
        parent = git(repo, 'rev-parse', f'{introduced}^').decode().strip()
        require(not test_methods(repo, parent, suite, optional=True), f'Suite existed before its recorded introduction: {suite["id"]}')
        results.append({'id': suite['id'], 'area_suite': suite['area_suite'],
                        'unittest_target': suite['unittest_target'], 'test_case_count': len(methods)})
    return results


def extract_snapshot(repo, revision, destination):
    archive = git(repo, 'archive', revision, 'src', 'tests')
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        for member in tree:
            relative = PurePosixPath(member.name)
            require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe archive path')
            path = destination.joinpath(*relative.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile(), 'Snapshot must not contain symlinks or special files')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(tree.extractfile(member).read())


def run_suites(catalog, repo, results):
    with tempfile.TemporaryDirectory(prefix='mesh-catalog-') as directory:
        checkout = Path(directory)
        extract_snapshot(repo, catalog['product_snapshot_sha'], checkout)
        environment = dict(os.environ, PYTHONPATH=str(checkout / 'src'), PYTHONDONTWRITEBYTECODE='1')
        # Invoke the same interpreter/venv as this tool; never the product worktree.
        import sys
        for result in results:
            start = time.perf_counter()
            completed = subprocess.run([sys.executable, '-B', '-m', 'unittest', result['unittest_target'], '-v'],
                                       cwd=checkout, env=environment, capture_output=True, text=True, timeout=30)
            result['elapsed_seconds'] = round(time.perf_counter()-start, 6)
            count = re.search(r'Ran (\d+) tests?', completed.stderr)
            result['passed'] = completed.returncode == 0 and count is not None and int(count[1]) == result['test_case_count']
            if not result['passed']:
                result['failure_output'] = completed.stdout + completed.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('test_catalog.yaml'))
    parser.add_argument('--product-repo', type=Path, required=True)
    parser.add_argument('--run', action='store_true', help='Run each suite independently from the pinned Git snapshot')
    parser.add_argument('--report', type=Path, help='Write machine-readable validation and timing results')
    args = parser.parse_args()
    try:
        catalog = load_catalog(args.catalog)
        results = validate_product(catalog, args.product_repo)
        if args.run:
            run_suites(catalog, args.product_repo, results)
        report = {'catalog_version': catalog['catalog_version'], 'product_snapshot_sha': catalog['product_snapshot_sha'],
                  'suite_count': len(results), 'test_case_count': sum(result['test_case_count'] for result in results),
                  'suites_executed': args.run, 'suites': results}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        require(not args.run or all(result['passed'] for result in results), 'A catalog suite failed; inspect the report')
        print(f'Validated {len(results)} suites / {report["test_case_count"]} test cases at {catalog["product_snapshot_sha"][:7]}.'
              + (' All suites passed independently.' if args.run else ' Static validation only.'))
    except (CatalogError, OSError, SyntaxError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Catalog validation failed: {error}\n')


if __name__ == '__main__':
    main()
