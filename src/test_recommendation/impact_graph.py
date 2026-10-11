"""Revision-specific Python AST graphs and bounded reverse-call impact analysis."""
import argparse
import ast
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path

import pymysql

from test_recommendation.catalog import CatalogError, git, require
from test_recommendation.coverage_collection import write_json
from test_recommendation.database import DatabaseConfig, canonical_json, initialize, load_inputs, seed

VERSION = 'python-static-v1'


def module_name(path):
    parts = Path(path).with_suffix('').parts[1:]
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def graph_from_sources(sources, revision):
    nodes, contexts, imports, dependencies = {}, {}, {}, set()
    modules = {module_name(path): path for path in sources}
    trees = {path: ast.parse(source, filename=path) for path, source in sources.items()}
    def register(node, module, path, prefix='', owner=None):
        name = f'{prefix}.{node.name}' if prefix else node.name
        identifier = f'{module}:{name}'
        kind = 'class' if isinstance(node, ast.ClassDef) else 'function'
        nodes[identifier] = dict(id=identifier, path=path, module=module, name=name, kind=kind,
                                 start_line=min([node.lineno]+[d.lineno for d in node.decorator_list]),
                                 end_line=node.end_lineno,
                                 ast_sha256=hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest())
        contexts[identifier] = (node, owner)
        for child in node.body:
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                register(child, module, path, name, identifier if kind == 'class' else owner)
    for path, tree in trees.items():
        module = module_name(path)
        identifier = f'{module}:<module>'
        nodes[identifier] = dict(id=identifier, path=path, module=module, name='<module>', kind='module',
                                 start_line=1, end_line=max(1, len(sources[path].splitlines())),
                                 ast_sha256=hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest())
        contexts[identifier] = (tree, None)
        aliases = {}
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(item, ast.ImportFrom):
                package = module if path.endswith('/__init__.py') else module.rsplit('.', 1)[0]
                base = '.'.join(package.split('.')[:len(package.split('.'))-item.level+1]) if item.level else ''
                origin = '.'.join(filter(None, [base, item.module]))
                for alias in item.names:
                    if alias.name != '*':
                        aliases[alias.asname or alias.name] = f'{origin}.{alias.name}'
        imports[module] = aliases
        for target in aliases.values():
            matches = [name for name in modules if target == name or target.startswith(name+'.')]
            if matches:
                destination = max(matches, key=len)
                if modules[destination] != path:
                    dependencies.add((path, modules[destination]))
        for child in tree.body:
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                register(child, module, path)
    dotted = {f'{node["module"]}.{node["name"]}': identifier for identifier, node in nodes.items() if node['kind'] != 'module'}
    def reference(expr, module):
        if isinstance(expr, ast.Name):
            return imports[module].get(expr.id, f'{module}.{expr.id}')
        if isinstance(expr, ast.Attribute):
            base = reference(expr.value, module)
            return f'{base}.{expr.attr}' if base else None
        return None
    def annotation(expr, module):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                expr = ast.parse(expr.value, mode='eval').body
            except SyntaxError:
                return None
        values = [reference(item, module) for item in ast.walk(expr)] if expr else []
        candidates = {dotted[value] for value in values if value in dotted and nodes[dotted[value]]['kind'] == 'class'}
        return next(iter(candidates)) if len(candidates) == 1 else None
    class_fields = defaultdict(dict)
    # Constructor annotations and obvious assignments establish self-field types.
    for identifier, (node, owner) in contexts.items():
        if owner and nodes[identifier]['name'].endswith('.__init__'):
            args = {arg.arg: annotation(arg.annotation, nodes[identifier]['module']) for arg in node.args.args + node.args.kwonlyargs}
            for item in ast.walk(node):
                if isinstance(item, (ast.Assign, ast.AnnAssign)):
                    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                    value = item.value
                    inferred = args.get(value.id) if isinstance(value, ast.Name) else None
                    if isinstance(value, ast.Call):
                        inferred = dotted.get(reference(value.func, nodes[identifier]['module']))
                    if isinstance(item, ast.AnnAssign):
                        inferred = annotation(item.annotation, nodes[identifier]['module']) or inferred
                    for target in targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self' and inferred:
                            class_fields[owner][target.attr] = inferred
    edges, unresolved = [], []
    class Calls(ast.NodeVisitor):
        def __init__(self, identifier, owner):
            self.identifier, self.owner = identifier, owner
            self.module = nodes[identifier]['module']
            self.types = {}
        def visit_FunctionDef(self, node):
            pass
        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        def infer(self, expr):
            if isinstance(expr, ast.Name):
                return self.owner if expr.id in ('self', 'cls') else self.types.get(expr.id)
            if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == 'self':
                return class_fields[self.owner].get(expr.attr)
            if isinstance(expr, ast.Call):
                candidate = dotted.get(reference(expr.func, self.module))
                return candidate if candidate and nodes[candidate]['kind'] == 'class' else None
            return None
        def visit_Assign(self, node):
            inferred = self.infer(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.types.pop(target.id, None)
                    if inferred:
                        self.types[target.id] = inferred
            self.generic_visit(node)
        def visit_Call(self, node):
            candidate = dotted.get(reference(node.func, self.module))
            if isinstance(node.func, ast.Name):
                prefix = nodes[self.identifier]['name'].rsplit('.', 1)[0]
                candidate = dotted.get(f'{self.module}.{prefix}.{node.func.id}', candidate)
            elif isinstance(node.func, ast.Attribute):
                owner_type = self.infer(node.func.value)
                if owner_type:
                    candidate = dotted.get(f'{nodes[owner_type]["module"]}.{nodes[owner_type]["name"]}.{node.func.attr}')
            if candidate:
                edges.append(dict(caller=self.identifier, callee=candidate, line=node.lineno))
                if nodes[candidate]['kind'] == 'class':
                    constructor = dotted.get(f'{nodes[candidate]["module"]}.{nodes[candidate]["name"]}.__init__')
                    if constructor:
                        edges.append(dict(caller=self.identifier, callee=constructor, line=node.lineno))
            else:
                unresolved.append(dict(caller=self.identifier, expression=ast.unparse(node.func), line=node.lineno))
            self.generic_visit(node)
    for identifier, (node, owner) in contexts.items():
        visitor = Calls(identifier, owner)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visitor.types = {arg.arg: annotation(arg.annotation, nodes[identifier]['module']) for arg in node.args.args + node.args.kwonlyargs}
        for statement in node.body:
            visitor.visit(statement)
    return dict(analyzer_version=VERSION, revision_sha=revision, nodes=nodes,
                calls=sorted(edges, key=lambda e: (e['caller'], e['line'], e['callee'])),
                file_dependencies=[dict(importer=a, imported=b) for a, b in sorted(dependencies)],
                unresolved_calls=unresolved)


def build_graph(repo, revision):
    paths = git(repo, 'ls-tree', '-r', '--name-only', revision, 'src/mesh_harbor').decode().splitlines()
    sources = {path: git(repo, 'show', f'{revision}:{path}').decode() for path in paths if path.endswith('.py')}
    return graph_from_sources(sources, revision)


def impact(base, target, hops=3, *, touched_files=()):
    require(type(hops) is int and 0 <= hops <= 3, 'Traversal depth must be between 0 and 3')
    before, after = base['nodes'], target['nodes']
    changed = sorted(identifier for identifier in before.keys() | after.keys()
                     if identifier not in before or identifier not in after or before[identifier]['ast_sha256'] != after[identifier]['ast_sha256'])
    seeds = [identifier for identifier in changed if (before.get(identifier) or after[identifier])['kind'] != 'module']
    reverse = defaultdict(set)
    for edge in base['calls']:
        reverse[edge['callee']].add(edge['caller'])
    distances = {identifier: 0 for identifier in seeds}
    queue = deque(seeds)
    while queue:
        identifier = queue.popleft()
        if distances[identifier] < hops:
            for caller in sorted(reverse[identifier]):
                if caller not in distances:
                    distances[caller] = distances[identifier] + 1
                    queue.append(caller)
    direct_files = sorted(set(touched_files) | {(before.get(identifier) or after[identifier])['path'] for identifier in changed})
    files = {path: 0 for path in direct_files}
    for identifier, distance in distances.items():
        path = (before.get(identifier) or after[identifier])['path']
        files[path] = min(files.get(path, distance), distance)
    dependencies = defaultdict(set)
    for edge in base['file_dependencies']:
        dependencies[edge['imported']].add(edge['importer'])
    queue = deque(files)
    while queue:
        path = queue.popleft()
        if files[path] < hops:
            for importer in sorted(dependencies[path]):
                distance = files[path]+1
                if distance < files.get(importer, hops+1):
                    files[importer] = distance
                    queue.append(importer)
    return dict(analyzer_version=VERSION, base_revision_sha=base['revision_sha'], target_revision_sha=target['revision_sha'],
                max_hops=hops, changed_symbols=seeds, changed_files=direct_files,
                impacted_symbols=[dict(id=k, distance=v) for k, v in sorted(distances.items())],
                impacted_files=[dict(path=k, distance=v) for k, v in sorted(files.items())],
                added_symbols=sorted(after.keys()-before.keys()), removed_symbols=sorted(before.keys()-after.keys()),
                unresolved_base_calls=len(base['unresolved_calls']))


def persist(cursor, table, keys, values):
    columns = list(values)
    cursor.execute(f'SELECT content_sha256 FROM {table} WHERE '+ ' AND '.join(f'{key}=%s' for key in keys), tuple(values[key] for key in keys))
    existing = cursor.fetchone()
    if existing:
        require(existing['content_sha256'] == values['content_sha256'], 'Stored graph/impact differs; bump analyzer version')
    else:
        cursor.execute(f'INSERT INTO {table} ('+','.join(columns)+') VALUES('+','.join(['%s']*len(columns))+')', tuple(values.values()))


def run(config, catalog, repo, artifacts):
    initialize(config)
    _, manifest = load_inputs(catalog, repo)
    seed(config, catalog, repo)
    cases = [entry for entry in manifest['commits'] if entry['split'] == 'evaluation']
    graphs, reports = {}, []
    output = artifacts.resolve() / VERSION
    output.mkdir(parents=True, exist_ok=True)
    with config.connect() as connection:
        with connection.cursor() as cursor:
            for case in cases:
                for revision in (case['parent_sha'], case['commit_sha']):
                    if revision not in graphs:
                        graph = build_graph(repo, revision)
                        content = canonical_json(graph)
                        persist(cursor, 'ast_graphs', ('corpus_id', 'revision_sha', 'analyzer_version'),
                                dict(corpus_id=manifest['corpus_id'], revision_sha=revision, analyzer_version=VERSION,
                                     content_sha256=hashlib.sha256(content.encode()).hexdigest(), graph_json=content))
                        graphs[revision] = graph
                        write_json(output / f'{revision}.json', graph)
                touched = git(repo, 'diff', '--name-only', case['parent_sha'], case['commit_sha'], '--', 'src/mesh_harbor').decode().splitlines()
                report = impact(graphs[case['parent_sha']], graphs[case['commit_sha']], touched_files=touched)
                content = canonical_json(report)
                persist(cursor, 'ast_impacts', ('corpus_id', 'target_commit_sha', 'analyzer_version'),
                        dict(corpus_id=manifest['corpus_id'], target_commit_sha=case['commit_sha'], base_revision_sha=case['parent_sha'],
                             analyzer_version=VERSION, content_sha256=hashlib.sha256(content.encode()).hexdigest(), impact_json=content))
                write_json(output / f'{case["commit_sha"]}.impact.json', report)
                reports.append(report)
                connection.commit()
                print(f'{case["commit_sha"][:7]}: {len(report["changed_symbols"])} changed symbols, {len(report["impacted_files"])} affected files', flush=True)
    write_json(output / 'index.json', dict(analyzer_version=VERSION, graph_count=len(graphs), case_count=len(reports), cases=reports))
    print(f'Saved {len(graphs)} graphs and {len(reports)} impact reports to MySQL and {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-repo', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('test_catalog.yaml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts/ast'))
    args = parser.parse_args()
    try:
        run(DatabaseConfig.load(args.env_file), args.catalog, args.product_repo, args.artifacts)
    except (CatalogError, OSError, SyntaxError, pymysql.MySQLError) as error:
        parser.exit(1, f'AST analysis failed: {error}\n')


if __name__ == '__main__':
    main()
