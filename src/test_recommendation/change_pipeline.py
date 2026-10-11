"""Independently replayable change extraction and parent-graph impact stages."""
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import re
import uuid

from test_recommendation.catalog import CatalogError, git, require
from test_recommendation.database import canonical_json
from test_recommendation.impact_graph import VERSION as ANALYZER, build_graph

VERSION = 'change-impact-v2'


def checksum(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'payload_sha256': checksum(payload), 'payload': payload}, indent=2)+'\n')


def load(path):
    document = json.loads(path.read_text())
    require(checksum(document['payload']) == document['payload_sha256'], 'Stage artifact checksum mismatch')
    return document['payload']


def changed_paths(raw):
    fields = raw.decode().split('\0')
    if fields[-1] == '':
        fields.pop()
    result, position = [], 0
    while position < len(fields):
        status = fields[position]
        position += 1
        require(re.fullmatch('[ACDMRTUXB][0-9]*', status), 'Unexpected Git change status')
        count = 2 if status[0] in ('R', 'C') else 1
        require(position+count <= len(fields), 'Incomplete Git change record')
        paths = fields[position:position+count]
        position += count
        result.append(dict(status=status, old_path=None if status[0]=='A' else paths[0],
                           new_path=None if status[0]=='D' else paths[-1]))
    return result


def diff_ranges(patch):
    ranges = []
    for old, old_count, new, new_count in re.findall(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', patch, re.MULTILINE):
        ranges.append(dict(old_start=int(old), old_count=int(old_count) if old_count else 1,
                           new_start=int(new), new_count=int(new_count) if new_count else 1))
    return ranges


def patch_bytes(repo, base, target, paths=()):
    return git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--no-color',
               '--unified=3', '-M', base, target, '--', *paths)


def save_extraction(repo, output, change):
    output.parent.mkdir(parents=True, exist_ok=True)
    base, target = change['base_revision_sha'], change['target_revision_sha']
    patches = [(change['diff_artifact'], patch_bytes(repo,base,target))]
    for record in change['files']:
        paths = sorted({path for path in (record['old_path'],record['new_path']) if path})
        patches.append((record['diff_artifact'],patch_bytes(repo,base,target,paths)))
    for reference, content in patches:
        require(hashlib.sha256(content).hexdigest()==reference['sha256'], 'Diff changed during extraction')
        path = output.parent / reference['path']
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
    save(output,change)


def validate_patches(change, directory):
    if change['stage_version']=='change-impact-v1':
        return  # Legacy extraction saved hashes without readable patches.
    for reference in [change['diff_artifact']]+[record['diff_artifact'] for record in change['files']]:
        require(re.fullmatch(r'change\.patch|diffs/[0-9]+\.patch',reference['path']), 'Unsafe diff artifact path')
        path=directory/reference['path']
        require(path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==reference['sha256'], 'Diff artifact missing or modified')


def extract(repo, target_ref, base_ref=None, run_id=None):
    target = git(repo, 'rev-parse', '--verify', '--end-of-options', f'{target_ref}^{{commit}}').decode().strip()
    parents = git(repo, 'show', '-s', '--format=%P', target).decode().split()
    require(parents or base_ref, 'Root commit requires an explicit existing base revision')
    base = git(repo, 'rev-parse', '--verify', '--end-of-options', f'{base_ref}^{{commit}}').decode().strip() if base_ref else parents[0]
    require(base != target, 'Base and target must differ')
    git(repo, 'merge-base', '--is-ancestor', base, target)
    before, after = build_graph(repo, base), build_graph(repo, target)
    files = changed_paths(git(repo, 'diff', '--no-ext-diff', '--name-status', '-z', '-M', base, target))
    source_paths = set()
    for index, record in enumerate(files, 1):
        paths = sorted({path for path in (record['old_path'],record['new_path']) if path})
        readable = patch_bytes(repo, base, target, paths)
        record['diff_artifact'] = dict(path=f'diffs/{index:03d}.patch', sha256=hashlib.sha256(readable).hexdigest())
        record['production_source'] = any(path.startswith('src/mesh_harbor/') for path in paths)
        if record['production_source']:
            source_paths.update(path for path in paths if path.startswith('src/mesh_harbor/'))
            patch = git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--no-color', '--unified=0', '-M', base, target, '--', *paths).decode()
            record['diff_ranges'] = diff_ranges(patch)
            record['patch_sha256'] = hashlib.sha256(patch.encode()).hexdigest()
        else:
            record['diff_ranges'] = []
    symbols = []
    for identifier in sorted(before['nodes'].keys() | after['nodes'].keys()):
        old, new = before['nodes'].get(identifier), after['nodes'].get(identifier)
        if (old or new)['kind'] == 'module' or old and new and old['ast_sha256']==new['ast_sha256']:
            continue
        symbols.append(dict(id=identifier, kind=(old or new)['kind'],
                            change='added' if old is None else 'removed' if new is None else 'modified',
                            before=old, after=new))
    payload = dict(stage='change_extraction', stage_version=VERSION, analyzer_version=ANALYZER,
                   run_id=run_id or str(uuid.uuid4()), base_revision_sha=base, target_revision_sha=target,
                   comparison='first_parent' if base_ref is None else 'explicit_ancestor',
                   diff_artifact=dict(path='change.patch', sha256=hashlib.sha256(patch_bytes(repo,base,target)).hexdigest()),
                   merge_target=len(parents)>1, files=files, changed_source_paths=sorted(source_paths),
                   changed_symbols=symbols, base_graph_sha256=checksum(before), target_graph_sha256=checksum(after),
                   evidence_gaps=[dict(kind='new_symbol',symbol=row['id']) for row in symbols if row['change']=='added'])
    return payload


def traverse(seeds, reverse, hops):
    traces = {seed: [] for seed in sorted(set(seeds))}
    queue = deque(traces)
    while queue:
        current = queue.popleft()
        if len(traces[current]) >= hops:
            continue
        for destination, edge in sorted(reverse[current], key=lambda item: (item[0], canonical_json(item[1]))):
            if destination not in traces:
                traces[destination] = traces[current]+[edge]
                queue.append(destination)
    return traces


def expand(change, base_graph, hops=3):
    require(change['stage']=='change_extraction' and change['stage_version'] in (VERSION,'change-impact-v1'), 'Unsupported extraction artifact')
    require(type(hops) is int and 0 <= hops <= 3, 'Hops must be between 0 and 3')
    require(base_graph['revision_sha']==change['base_revision_sha'] and base_graph['analyzer_version']==change['analyzer_version']
            and checksum(base_graph)==change['base_graph_sha256'], 'Parent graph provenance/hash mismatch')
    # New target symbols cannot have parent callers. Keep them as explicit gaps.
    seeds = [symbol['id'] for symbol in change['changed_symbols'] if symbol['id'] in base_graph['nodes']]
    reverse = defaultdict(list)
    for edge in base_graph['calls']:
        reverse[edge['callee']].append((edge['caller'],dict(kind='call',caller=edge['caller'],callee=edge['callee'],line=edge['line'])))
    symbol_traces = traverse(seeds, reverse, hops)
    file_reasons = defaultdict(list)
    for path in change['changed_source_paths']:
        file_reasons[path].append(dict(kind='direct_change',distance=0,trace=[]))
    for identifier, trace in symbol_traces.items():
        file_reasons[base_graph['nodes'][identifier]['path']].append(dict(kind='symbol_impact',symbol=identifier,distance=len(trace),trace=trace))
    imports = defaultdict(list)
    for edge in base_graph['file_dependencies']:
        imports[edge['imported']].append((edge['importer'],dict(kind='import',**edge)))
    # File dependencies form a separate conservative signal with its own hop limit.
    file_traces = traverse(change['changed_source_paths'], imports, hops)
    for path, trace in file_traces.items():
        if trace:
            file_reasons[path].append(dict(kind='import_dependency',distance=len(trace),trace=trace))
    impacted = set(symbol_traces)
    unresolved = [call for call in base_graph['unresolved_calls'] if call['caller'] in impacted or base_graph['nodes'][call['caller']]['path'] in file_reasons]
    return dict(stage='impact_expansion',stage_version=VERSION,analyzer_version=ANALYZER,run_id=change['run_id'],
                base_revision_sha=change['base_revision_sha'],target_revision_sha=change['target_revision_sha'],
                extraction_sha256=checksum(change),base_graph_sha256=checksum(base_graph),max_hops=hops,
                impacted_symbols=[dict(id=identifier,path=base_graph['nodes'][identifier]['path'],distance=len(trace),trace=trace)
                                  for identifier,trace in sorted(symbol_traces.items())],
                impacted_files=[dict(path=path,distance=min(reason['distance'] for reason in reasons),reasons=reasons)
                                for path,reasons in sorted(file_reasons.items())],
                unresolved_calls=unresolved,evidence_gaps=change['evidence_gaps'],
                limitations=['Static calls are hypotheses; dynamic dispatch and scope-sensitive bindings are incomplete.',
                             'Import dependencies are broader signals than demonstrated runtime impact.',
                             'New symbols need fallback selection; no target call edges or target tests are used for expansion.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('extract','expand'))
    parser.add_argument('--product-repo',type=Path,required=True)
    parser.add_argument('--target')
    parser.add_argument('--base')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--hops',type=int,default=3)
    args = parser.parse_args()
    try:
        if args.stage=='extract':
            require(args.target is not None,'--target is required for extraction')
            result=extract(args.product_repo,args.target,args.base)
            output=args.output or Path('artifacts/pipeline')/result['run_id']/'change.json'
        else:
            require(args.input is not None,'--input is required for expansion')
            change=load(args.input)
            validate_patches(change,args.input.parent)
            graph=build_graph(args.product_repo,change['base_revision_sha'])
            result=expand(change,graph,args.hops)
            output=args.output or args.input.with_name('impact.json')
        if args.stage=='extract':
            save_extraction(args.product_repo,output,result)
        else:
            save(output,result)
        print(f'{args.stage}: run {result["run_id"]}; saved {output}')
    except (CatalogError,OSError,ValueError,SyntaxError,KeyError) as error:
        parser.exit(1,f'Pipeline stage failed: {error}\n')


if __name__=='__main__':
    main()
