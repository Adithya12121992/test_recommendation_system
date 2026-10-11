"""Freeze reviewed change-to-suite cases and validate their historical evidence."""
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

import pymysql

from test_recommendation.catalog import CatalogError, extract_snapshot, git, require, test_methods
from test_recommendation.coverage_collection import write_json
from test_recommendation.database import DatabaseConfig, canonical_json, initialize, load_inputs
from test_recommendation.evaluation_coverage import POLICY, validate_run
from test_recommendation.impact_graph import VERSION as ANALYZER
from test_recommendation.synthetic_issues import VERSION as ISSUE_VERSION, available_records, validate_records


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def check_review(review, catalog):
    cases = review['cases']
    require(len(cases) == 20 and [case['sequence'] for case in cases] == list(range(81,101)), 'Review must cover exactly commits 81–100 in order')
    ids = {suite['id'] for suite in catalog['suites']}
    for case in cases:
        required = case['required_suites']
        require(required and len(required) == len(set(required)) and set(required) <= ids, 'Invalid required suites')
        require(case['rationale'].strip() and case['focus_test'].startswith('test_'), 'Missing behavioral review')
    require(review['human_approved'] is False and review['reviewer'] == 'Codex', 'Unexpected approval attribution')


def reference_run(repo, revision, directory):
    with tempfile.TemporaryDirectory(prefix='golden-reference-') as temporary:
        checkout = Path(temporary)
        extract_snapshot(repo, revision, checkout)
        environment = dict(os.environ, PYTHONPATH=str(checkout/'src'), PYTHONDONTWRITEBYTECODE='1')
        start = time.perf_counter()
        result = subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-v'], cwd=checkout,
                                env=environment, capture_output=True, text=True, timeout=90)
        elapsed = time.perf_counter()-start
        output = result.stdout+result.stderr
        match = re.search(r'Ran (\d+) tests?', output)
        require(result.returncode == 0 and match, f'Reference tests failed at {revision}: {output}')
        path = directory/f'{revision}.reference.txt'
        path.write_text(output)
        return dict(passed=True, test_case_count=int(match[1]), elapsed_seconds=elapsed,
                    python_version=platform.python_version(), output_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    output_path=str(path.resolve()), scope='full unittest discovery including tests outside the recommendation catalog')


def validate_coverage_mappings(cursor, run_id):
    cursor.execute('SELECT suite_id,raw_report_json FROM coverage_suite_results WHERE run_id=%s',(run_id,))
    reports={row['suite_id']:json.loads(row['raw_report_json']) for row in cursor.fetchall()}
    cursor.execute("SELECT suite_id,content FROM coverage_artifacts WHERE run_id=%s AND artifact_kind='json_report'",(run_id,))
    artifact_reports={row['suite_id']:json.loads(row['content']) for row in cursor.fetchall()}
    require(reports==artifact_reports,'Raw reports differ from retained artifacts')
    cursor.execute('SELECT * FROM coverage_mapping WHERE run_id=%s',(run_id,))
    rows=cursor.fetchall()
    identities=set()
    for mapping in rows:
        identity=(mapping['suite_id'],mapping['source_path'])
        require(identity not in identities,'Duplicate coverage mapping')
        identities.add(identity)
        file=reports[mapping['suite_id']]['files'].get(mapping['source_path'])
        require(file is not None,'Mapping missing from raw coverage report')
        summary=file['summary']
        total,covered=summary['num_statements'],summary['covered_lines']
        require(total>0 and mapping['executable_line_count']==total and mapping['covered_line_count']==covered,'Mapping counts differ from report')
        require(json.loads(mapping['executed_lines'])==file['executed_lines'] and json.loads(mapping['missing_lines'])==file['missing_lines'],'Mapping lines differ from report')
        require(abs(float(mapping['line_coverage'])-100*covered/total)<=0.0051,'Mapping percentage differs from report')
        branches=summary['num_branches']
        require((not branches and mapping['branch_coverage'] is None) or
                (branches and mapping['branch_coverage'] is not None and abs(float(mapping['branch_coverage'])-100*summary['covered_branches']/branches)<=0.0051),'Branch percentage differs from report')
    expected={(suite,path) for suite,report in reports.items() for path,file in report['files'].items() if file['summary']['num_statements']>0}
    require(identities==expected,'Coverage mappings are incomplete')


def evidence(cursor, case, catalog, manifest, repo):
    target, parent = case['target_commit_sha'], case['base_revision_sha']
    key = digest(catalog)
    require(git(repo,'rev-parse',f'{target}^').decode().strip() == parent, 'Golden base differs from Git parent')
    expected = {suite['id']: len(test_methods(repo,parent,suite,optional=True)) for suite in catalog['suites']}
    missing = sorted(suite for suite, count in expected.items() if not count)
    expected = {suite: count for suite,count in expected.items() if count}
    cursor.execute('''SELECT * FROM evaluation_case_coverage WHERE corpus_id=%s AND target_commit_sha=%s
        AND catalog_key=%s AND policy_version=%s''',(catalog['corpus_id'],target,key,POLICY))
    link = cursor.fetchone()
    require(link and link['parent_sha']==parent and sorted(json.loads(link['evidence_gaps_json']))==missing, 'Missing/incompatible rolling coverage link')
    validate_run(cursor,link['run_id'],key,parent,target,expected)
    validate_coverage_mappings(cursor,link['run_id'])
    graphs = []
    for revision in (parent,target):
        cursor.execute('SELECT content_sha256,graph_json FROM ast_graphs WHERE corpus_id=%s AND revision_sha=%s AND analyzer_version=%s',(catalog['corpus_id'],revision,ANALYZER))
        row = cursor.fetchone()
        require(row and digest(json.loads(row['graph_json']))==row['content_sha256'], 'Missing/corrupt AST graph')
        graph = json.loads(row['graph_json'])
        require(graph['revision_sha']==revision and graph['analyzer_version']==ANALYZER, 'Graph provenance differs')
        graphs.append(dict(revision_sha=revision,content_sha256=row['content_sha256']))
    cursor.execute('SELECT content_sha256,impact_json FROM ast_impacts WHERE corpus_id=%s AND target_commit_sha=%s AND analyzer_version=%s',(catalog['corpus_id'],target,ANALYZER))
    row=cursor.fetchone()
    require(row and digest(json.loads(row['impact_json']))==row['content_sha256'],'Missing/corrupt impact report')
    report=json.loads(row['impact_json'])
    require(report['base_revision_sha']==parent and report['target_revision_sha']==target,'Impact revision mismatch')
    cursor.execute('SELECT content_sha256,dataset_json FROM synthetic_issue_datasets WHERE corpus_id=%s AND catalog_key=%s AND generator_version=%s',(catalog['corpus_id'],key,ISSUE_VERSION))
    issues=cursor.fetchone()
    require(issues and digest(json.loads(issues['dataset_json']))==issues['content_sha256'],'Missing/corrupt issue dataset')
    issue_dataset=json.loads(issues['dataset_json'])
    # Check persisted lookup rows as well as the immutable fixture payload.
    for record in issue_dataset['records']:
        table='jira_mapping' if record['kind']=='jira' else 'bugzilla_mapping'
        identifier='jira_key' if record['kind']=='jira' else 'bug_id'
        cursor.execute(f'SELECT * FROM {table} WHERE corpus_id=%s AND catalog_key=%s AND {identifier}=%s AND suite_id=%s AND source_path=%s',
                       (catalog['corpus_id'],key,record[identifier],record['suite_id'],record['source_path']))
        actual=cursor.fetchall()
        require(len(actual)==1,'Issue lookup mapping missing or duplicated')
        for field,value in record.items():
            if field in ('kind','provenance'):
                continue
            if field=='issue_created_at':
                from test_recommendation.database import utc_datetime
                value=utc_datetime(value)
            require(actual[0][field]==value,'Issue lookup row differs from fixture')
    # Availability uses the base revision, never target labels or target issue links.
    records=available_records(issue_dataset['records'],manifest,parent)
    paths=set(git(repo,'ls-tree','-r','--name-only',parent,'src').decode().splitlines())
    records=[record for record in records if record['suite_id'] in expected and record['source_path'] in paths]
    require(all(record['linked_commit_sha'] != target for record in records),'Target issue leaked into evidence')
    cursor.execute('SELECT suite_id,artifact_kind,content_sha256 FROM coverage_artifacts WHERE run_id=%s ORDER BY suite_id,artifact_kind',(link['run_id'],))
    coverage_digest=digest(cursor.fetchall())
    return dict(policy_version=POLICY,coverage_run_id=link['run_id'],coverage_artifacts_sha256=coverage_digest,
                unavailable_suite_ids=missing,ast_graphs=graphs,impact_sha256=row['content_sha256'],analyzer_version=ANALYZER,
                issue_dataset_sha256=issues['content_sha256'],issue_generator_version=ISSUE_VERSION,
                eligible_issue_records_sha256=digest(records),eligible_issue_count=len(records))


def validate_dataset(dataset, config, catalog, manifest, repo):
    require(dataset['catalog_key']==digest(catalog) and dataset['history_sha256']==manifest['history_sha256'],'Dataset corpus/catalog changed')
    entries={entry['commit_sha']:entry for entry in manifest['commits'] if entry['split']=='evaluation'}
    require(len(dataset['cases'])==20 and {case['target_commit_sha'] for case in dataset['cases']}==set(entries),'Golden cases do not match evaluation split')
    suites={suite['id']:suite for suite in catalog['suites']}
    with config.connect() as connection:
        with connection.cursor() as cursor:
            for index,case in enumerate(dataset['cases'],1):
                entry=entries[case['target_commit_sha']]
                require(case['case_id']==f'G{index:03d}' and case['case_version']==1 and case['base_revision_sha']==entry['parent_sha'],'Invalid case identity')
                require(evidence(cursor,case,catalog,manifest,repo)==case['evidence'],'Frozen evidence differs')
                for suite_id in case['required_suite_ids']:
                    require(suite_id in suites and test_methods(repo,entry['commit_sha'],suites[suite_id]),'Expected suite missing at target')
                primary=suites[case['required_suite_ids'][0]]
                require(case['focus_test'] in test_methods(repo,entry['commit_sha'],primary),'Behavioral assertion missing')
                require(case['required_suites_without_parent_coverage']==sorted(set(case['required_suite_ids']) & set(case['evidence']['unavailable_suite_ids'])),'Evidence gaps were concealed')
                reference=case['reference_run']
                path=Path(reference['output_path'])
                require(reference['passed'] and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==reference['output_sha256'],'Reference output missing/corrupt')
    return True


def build(config,catalog_path,repo,review_path,destination,artifacts):
    initialize(config)
    catalog,manifest=load_inputs(catalog_path,repo)
    review=json.loads(review_path.read_text())
    check_review(review,catalog)
    if destination.exists():
        dataset=json.loads(destination.read_text())
        require(dataset['review_sha256']==digest(review),'Frozen review changed; create a new dataset version')
        validate_dataset(dataset,config,catalog,manifest,repo)
        print('Existing frozen dataset validated; no reference reruns or overwrites.')
    else:
        directory=artifacts.resolve()/'golden-v1'
        directory.mkdir(parents=True,exist_ok=True)
        dataset=dict(schema_version=1,dataset_id='mesh-harbor-golden',dataset_version=1,corpus_id=catalog['corpus_id'],
                     catalog_key=digest(catalog),history_sha256=manifest['history_sha256'],review_sha256=digest(review),
                     reviewer=review['reviewer'],review_type=review['review_type'],human_approved=False,
                     frozen_at=datetime.now(timezone.utc).isoformat(),oracle_kind=review['oracle_kind'],
                     label_semantics=review['label_semantics'],cases=[])
        with config.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT dataset_json FROM synthetic_issue_datasets WHERE corpus_id=%s AND catalog_key=%s AND generator_version=%s',(catalog['corpus_id'],digest(catalog),ISSUE_VERSION))
                issue_row=cursor.fetchone()
                require(issue_row,'Generate issue evidence first')
                validate_records(json.loads(issue_row['dataset_json']),catalog,manifest,repo)
                for index,label in enumerate(review['cases'],1):
                    entry=manifest['commits'][label['sequence']-1]
                    case=dict(case_id=f'G{index:03d}',case_version=1,target_commit_sha=entry['commit_sha'],base_revision_sha=entry['parent_sha'],
                              required_suite_ids=label['required_suites'],focus_test=label['focus_test'],rationale=label['rationale'])
                    case['evidence']=evidence(cursor,case,catalog,manifest,repo)
                    case['required_suites_without_parent_coverage']=sorted(set(case['required_suite_ids']) & set(case['evidence']['unavailable_suite_ids']))
                    case['reference_run']=reference_run(repo,entry['commit_sha'],directory)
                    dataset['cases'].append(case)
                    write_json(directory/'build_progress.json',dataset)
                    print(f'{case["case_id"]}: reviewed {label["required_suites"]}, reference {case["reference_run"]["test_case_count"]} tests passed',flush=True)
        validate_dataset(dataset,config,catalog,manifest,repo)
        destination.parent.mkdir(parents=True,exist_ok=True)
        write_json(destination,dataset)
    content=canonical_json(dataset)
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT content_sha256 FROM golden_datasets WHERE dataset_id=%s AND dataset_version=1',(dataset['dataset_id'],))
            existing=cursor.fetchone()
            if existing:
                require(existing['content_sha256']==digest(dataset),'Frozen MySQL dataset differs')
            else:
                cursor.execute('INSERT INTO golden_datasets VALUES(%s,1,%s,%s,%s,%s)',(dataset['dataset_id'],catalog['corpus_id'],digest(catalog),digest(dataset),content))
        connection.commit()
    write_json(artifacts.resolve()/'golden-v1'/'validation.json',dict(passed=True,case_count=20,dataset_sha256=digest(dataset),
               case_ids_with_parent_coverage_gaps=[case['case_id'] for case in dataset['cases'] if case['required_suites_without_parent_coverage']]))
    print(f'Frozen and validated 20 cases: {destination}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-repo',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,default=Path('test_catalog.yaml'))
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--review',type=Path,default=Path('evals/golden_review_v1.json'))
    parser.add_argument('--dataset',type=Path,default=Path('evals/golden_dataset_v1.json'))
    parser.add_argument('--artifacts',type=Path,default=Path('artifacts/evaluation'))
    args=parser.parse_args()
    try:
        build(DatabaseConfig.load(args.env_file),args.catalog,args.product_repo,args.review,args.dataset,args.artifacts)
    except (CatalogError,OSError,ValueError,subprocess.SubprocessError,pymysql.MySQLError) as error:
        parser.exit(1,f'Golden preparation failed: {error}\n')


if __name__=='__main__':
    main()
