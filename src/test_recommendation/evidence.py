"""Revision-compatible evidence lookup and auditable suite candidate generation."""
import argparse
import ast
import base64
from datetime import datetime,timezone
import hashlib
import json
import os
from urllib.parse import quote
from pathlib import Path

from dotenv import dotenv_values
import pymysql

from test_recommendation.catalog import CatalogError,load_catalog,require
from test_recommendation.change_pipeline import checksum,load,save
from test_recommendation.database import DatabaseConfig,canonical_json,initialize
from test_recommendation.github_pipeline import GitHub

VERSION='evidence-candidates-v1'


def candidates_from_evidence(evidence,catalog,change):
    candidates={}
    suites={suite['id']:suite for suite in catalog['suites']}
    def add(suite_id,reason):
        require(suite_id in suites,'Evidence references an unknown suite')
        candidates.setdefault(suite_id,dict(suite_id=suite_id,unittest_target=suites[suite_id]['unittest_target'],
                                             reasons=[],protected=False))['reasons'].append(reason)
    for row in evidence['coverage_matches']:
        add(row['suite_id'],dict(kind='measured_coverage',**row))
    for row in evidence['issue_matches']:
        add(row['suite_id'],dict(kind='historical_issue',**row))
    # Ownership is a conservative fallback, explicitly not measured evidence.
    direct=set(change['changed_source_paths'])
    for suite in catalog['suites']:
        overlap=sorted(direct & set(suite['owned_paths']))
        if overlap:
            add(suite['id'],dict(kind='catalog_path_fallback',source_paths=overlap,
                                 limitation='Snapshot catalog metadata; verify suite availability before execution'))
            candidates[suite['id']]['protected']=True
    for row in candidates.values():
        row['feedback']= [item for item in evidence['feedback_matches'] if item['suite_id']==row['suite_id']]
        row['measured_runtime_seconds']=evidence['suite_timings'].get(row['suite_id'])
    return dict(stage='candidate_generation',stage_version=VERSION,run_id=evidence['run_id'],
                base_revision_sha=evidence['base_revision_sha'],target_revision_sha=evidence['target_revision_sha'],
                evidence_sha256=checksum(evidence),candidates=[candidates[key] for key in sorted(candidates)],
                evidence_gaps=evidence['gaps'],ranking_applied=False,llm_called=False,tests_executed=False)


class Compatibility:
    def __init__(self,client,base_sha):
        self.client=client
        self.base_sha=base_sha
        self.trees={}
        self.ancestor_cache={base_sha:True}
        commit=client.commit(base_sha)
        require(commit['sha']==base_sha,'GitHub base revision mismatch')
        self.base_signature=self.signature(commit['commit']['tree']['sha'])
    def signature(self,tree_sha):
        if tree_sha not in self.trees:
            tree=self.client.get('git/trees/'+tree_sha)
            require(tree.get('sha')==tree_sha and not tree.get('truncated'),'Incomplete or mismatched compatibility tree')
            signature={entry['path']:entry['sha'] for entry in tree['tree'] if entry['path'] in ('src','tests') and entry['type']=='tree'}
            require(set(signature)=={'src','tests'},'Compatibility requires full src and tests subtrees')
            self.trees[tree_sha]=signature
        return self.trees[tree_sha]
    def ancestor(self,revision):
        if revision not in self.ancestor_cache:
            comparison=self.client.get(f'compare/{revision}...{self.base_sha}?per_page=1')
            self.ancestor_cache[revision]=comparison['merge_base_commit']['sha']==revision
        return self.ancestor_cache[revision]
    def coverage(self,run,revision):
        if run['status']!='passed':
            return False,'run_not_passed'
        if run['revision_sha']==self.base_sha:
            return True,'exact_base_revision'
        if revision is None:
            return False,'unknown_measurement_revision'
        if self.signature(revision['tree_sha'])!=self.base_signature:
            return False,'source_or_tests_differ_from_base'
        if not self.ancestor(run['revision_sha']):
            return False,'measurement_not_ancestor_of_base'
        return True,'ancestor_with_identical_src_and_tests_trees'


def validate_coverage(cursor,run_id,suite_ids):
    cursor.execute('SELECT * FROM coverage_suite_results WHERE run_id=%s',(run_id,))
    results=cursor.fetchall()
    require(results and all(row['passed'] and row['suite_id'] in suite_ids for row in results),'Invalid suite coverage results')
    reports={row['suite_id']:json.loads(row['raw_report_json']) for row in results}
    cursor.execute('SELECT * FROM coverage_artifacts WHERE run_id=%s',(run_id,))
    artifacts=cursor.fetchall()
    require({(row['suite_id'],row['artifact_kind']) for row in artifacts}=={(suite,kind) for suite in reports for kind in ('coverage_data','json_report','test_output')},'Incomplete coverage artifacts')
    for artifact in artifacts:
        require(hashlib.sha256(artifact['content']).hexdigest()==artifact['content_sha256'],'Coverage artifact checksum mismatch')
        if artifact['artifact_kind']=='json_report':
            require(json.loads(artifact['content'])==reports[artifact['suite_id']],'Raw report differs from artifact')
    cursor.execute('SELECT * FROM coverage_mapping WHERE run_id=%s',(run_id,))
    mappings=cursor.fetchall()
    expected={(suite,path) for suite,report in reports.items() for path,file in report['files'].items() if file['summary']['num_statements']>0}
    require({(row['suite_id'],row['source_path']) for row in mappings}==expected,'Incomplete coverage mappings')
    for row in mappings:
        file=reports[row['suite_id']]['files'][row['source_path']]
        require(json.loads(row['executed_lines'])==file['executed_lines'] and json.loads(row['missing_lines'])==file['missing_lines'],'Mapping lines differ from report')
        require(row['covered_line_count']==file['summary']['covered_lines'] and row['executable_line_count']==file['summary']['num_statements'],'Mapping counts differ from report')
    return results,mappings


def retrieve(config,catalog,change,impact,client):
    require(change['run_id']==impact['run_id'] and impact['extraction_sha256']==checksum(change),'Extraction/impact run mismatch')
    require(change['base_revision_sha']==impact['base_revision_sha'] and change['target_revision_sha']==impact['target_revision_sha'],'Stage revisions differ')
    require(change['repository'].lower()=='adithya12121992/test_recommendation_product','Catalog currently belongs to the product repository only')
    cutoff=datetime.now(timezone.utc)
    base=change['base_revision_sha']
    key=checksum(catalog)
    compatibility=Compatibility(client,base)
    paths={row['path']:row['distance'] for row in impact['impacted_files']}
    report=dict(stage='evidence_retrieval',stage_version=VERSION,run_id=change['run_id'],base_revision_sha=base,
                target_revision_sha=change['target_revision_sha'],catalog_key=key,impact_sha256=checksum(impact),
                retrieved_at=cutoff.isoformat(),policy='live_base_compatible',coverage_matches=[],issue_matches=[],
                feedback_matches=[],suite_timings={},coverage_audit=[],issue_audit=[],feedback_audit=[],gaps=[])
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT commit_sha,parent_sha,tree_sha,split_name FROM commit_metadata WHERE corpus_id=%s',(catalog['corpus_id'],))
            revisions={row['commit_sha']:row for row in cursor.fetchall()}
            cursor.execute('SELECT * FROM coverage_runs WHERE corpus_id=%s AND catalog_key=%s ORDER BY completed_at DESC,run_id',(catalog['corpus_id'],key))
            selected=None
            for run in cursor.fetchall():
                valid,reason=compatibility.coverage(run,revisions.get(run['revision_sha']))
                report['coverage_audit'].append(dict(run_id=run['run_id'],revision_sha=run['revision_sha'],eligible=valid,reason=reason))
                if valid and selected is None:
                    selected=run
            available=set()
            if selected:
                results,mappings=validate_coverage(cursor,selected['run_id'],{suite['id'] for suite in catalog['suites']})
                available={row['suite_id'] for row in results}
                report['selected_coverage_run_id']=selected['run_id']
                report['coverage_revision_sha']=selected['revision_sha']
                for row in results:
                    report['suite_timings'][row['suite_id']]=row['elapsed_seconds']
                for row in mappings:
                    if row['source_path'] in paths and row['covered_line_count']>0:
                        report['coverage_matches'].append(dict(suite_id=row['suite_id'],source_path=row['source_path'],
                            run_id=row['run_id'],revision_sha=selected['revision_sha'],impact_distance=paths[row['source_path']],
                            executed_lines=json.loads(row['executed_lines']),covered_line_count=row['covered_line_count']))
            else:
                report['gaps'].append(dict(kind='no_compatible_coverage',base_revision_sha=base))
            # Traverse validated seeded corpus ancestry only after proving its anchor.
            anchor=catalog['product_snapshot_sha']
            known_ancestors=set()
            if anchor in revisions and compatibility.ancestor(anchor):
                current=anchor
                while current in revisions and current not in known_ancestors:
                    known_ancestors.add(current)
                    current=revisions[current]['parent_sha']
            elif base in revisions:
                current=base
                while current in revisions and current not in known_ancestors:
                    known_ancestors.add(current)
                    current=revisions[current]['parent_sha']
            for table,identifier,summary in (('jira_mapping','jira_key','issue_summary'),('bugzilla_mapping','bug_id','bug_summary')):
                cursor.execute(f'SELECT * FROM {table} WHERE corpus_id=%s AND catalog_key=%s',(catalog['corpus_id'],key))
                for row in cursor.fetchall():
                    valid=row['available_at_commit_sha'] in known_ancestors and row['linked_commit_sha'] in known_ancestors and row['suite_id'] in available
                    relevant=row['source_path'] in paths
                    report['issue_audit'].append(dict(kind=table,issue_id=row[identifier],suite_id=row['suite_id'],source_path=row['source_path'],
                        included=bool(valid and relevant),reason='eligible_path_match' if valid and relevant else 'unavailable_revision_or_suite' if not valid else 'path_not_impacted'))
                    if valid and relevant:
                        link=row['linked_commit_sha']
                        seen_chain=set()
                        current=row['available_at_commit_sha']
                        while current in revisions and current not in seen_chain and current!=link:
                            seen_chain.add(current)
                            current=revisions[current]['parent_sha']
                        require(current==link,'Issue link becomes available before its linked change')
                        report['issue_matches'].append(dict(suite_id=row['suite_id'],source_path=row['source_path'],tracker=table,
                            issue_id=row[identifier],summary=row[summary],linked_commit_sha=row['linked_commit_sha'],
                            available_at_commit_sha=row['available_at_commit_sha'],is_synthetic=bool(row['is_synthetic'])))
            cursor.execute('''SELECT f.*,r.target_commit_sha,r.evidence_revision_sha FROM recommendation_feedback f
                JOIN recommendation_runs r ON f.run_id=r.run_id AND f.catalog_key=r.catalog_key
                WHERE r.corpus_id=%s AND f.catalog_key=%s ORDER BY f.reviewed_at DESC,f.feedback_id''',(catalog['corpus_id'],key))
            seen=set()
            for row in cursor.fetchall():
                revision=row['target_commit_sha']
                relevant_paths=[]
                if revision in revisions:
                    cursor.execute('SELECT changed_files FROM commit_metadata WHERE corpus_id=%s AND commit_sha=%s',(catalog['corpus_id'],revision))
                    relevant_paths=[item['path'] for item in json.loads(cursor.fetchone()['changed_files']) if item['path'] in paths]
                duplicate=(row['user_id'],revision,row['suite_id']) in seen
                reviewed=row['reviewed_at']
                valid=(row['review_status']=='approved' and reviewed is not None and reviewed<=cutoff.replace(tzinfo=None)
                    and revision in known_ancestors and row['evidence_revision_sha'] in known_ancestors and row['suite_id'] in available
                    and bool(relevant_paths) and row['action'] in ('accept','reject','add') and row['reason_category'] in ('relevance','failure_detection') and not duplicate)
                report['feedback_audit'].append(dict(feedback_id=row['feedback_id'],included=valid,reason='approved_context_match' if valid else 'unapproved_unavailable_unrelated_or_duplicate'))
                if valid:
                    seen.add((row['user_id'],revision,row['suite_id']))
                    report['feedback_matches'].append(dict(feedback_id=row['feedback_id'],suite_id=row['suite_id'],action=row['action'],
                         reason=row['reason'],target_commit_sha=revision,signal=-1 if row['action']=='reject' else 1))
            for path in change['changed_source_paths']:
                if not any(row['source_path']==path for row in report['coverage_matches']):
                    report['gaps'].append(dict(kind='changed_path_without_measured_hit',source_path=path))
    report['gaps'].extend(impact['evidence_gaps'])
    return report


def verify_target_suites(candidates,catalog,change,client):
    suites={suite['id']:suite for suite in catalog['suites']}
    verified=[]
    for candidate in candidates['candidates']:
        suite=suites[candidate['suite_id']]
        path='contents/'+quote(suite['test_file'],safe='/')+'?ref='+change['target_revision_sha']
        try:
            blob=client.get(path)
        except CatalogError as error:
            if 'HTTP 404' not in str(error):
                raise
            candidates['evidence_gaps'].append(dict(kind='catalog_suite_missing_at_head',suite_id=suite['id']))
            continue
        require(blob.get('encoding')=='base64','Unsupported target suite source encoding')
        source=base64.b64decode(blob['content'])
        require(hashlib.sha1(b'blob '+str(len(source)).encode()+b'\0'+source).hexdigest()==blob['sha'],'Target suite blob checksum mismatch')
        class_name=suite['unittest_target'].rsplit('.',1)[1]
        classes=[node for node in ast.parse(source).body if isinstance(node,ast.ClassDef) and node.name==class_name]
        methods=[node.name for cls in classes for node in cls.body if isinstance(node,ast.FunctionDef) and node.name.startswith('test_')]
        if not methods:
            candidates['evidence_gaps'].append(dict(kind='catalog_target_empty_or_missing_at_head',suite_id=suite['id']))
            continue
        candidate['target_test_case_count']=len(methods)
        candidate['target_test_methods']=[suite['unittest_target']+'.'+name for name in sorted(methods)]
        candidate['target_test_blob_sha']=blob['sha']
        candidate['target_availability_verified']=True
        verified.append(candidate)
    candidates['candidates']=verified
    return candidates


def persist_stage(config,catalog,payload):
    content=canonical_json(payload)
    digest=checksum(payload)
    with config.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT payload_json FROM pipeline_stage_artifacts WHERE run_id=%s AND stage=%s AND content_sha256=%s',
                           (payload['run_id'],payload['stage'],digest))
            existing=cursor.fetchone()
            if existing:
                require(checksum(json.loads(existing['payload_json']))==digest,'Stored stage checksum mismatch')
            else:
                cursor.execute('INSERT INTO pipeline_stage_artifacts '
                    '(run_id,stage,content_sha256,corpus_id,catalog_key,base_revision_sha,target_revision_sha,payload_json) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(payload['run_id'],payload['stage'],digest,catalog['corpus_id'],
                    checksum(catalog),payload['base_revision_sha'],payload['target_revision_sha'],content))
        connection.commit()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--change',type=Path,required=True)
    parser.add_argument('--impact',type=Path)
    parser.add_argument('--catalog',type=Path,default=Path('test_catalog.yaml'))
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--comment-pr',action='store_true')
    args=parser.parse_args()
    try:
        change=load(args.change);impact=load(args.impact or args.change.with_name('impact.json'))
        token=os.environ.get('GITHUB_TOKEN') or dotenv_values(args.env_file).get('GITHUB_TOKEN')
        catalog=load_catalog(args.catalog)
        config=DatabaseConfig.load(args.env_file)
        initialize(config)
        evidence=retrieve(config,catalog,change,impact,GitHub(change['repository'],token))
        candidates=candidates_from_evidence(evidence,catalog,change)
        head_repository=change.get('pull_request',{}).get('head_repository',change['repository'])
        verify_target_suites(candidates,catalog,change,GitHub(head_repository,token))
        save(args.change.with_name('evidence.json'),evidence)
        save(args.change.with_name('candidates.json'),candidates)
        persist_stage(config,catalog,evidence)
        persist_stage(config,catalog,candidates)
        print(f'Retrieved {len(evidence["coverage_matches"])} coverage and {len(evidence["issue_matches"])} issue matches; generated {len(candidates["candidates"])} candidates.')
        if args.comment_pr:
            from test_recommendation.pr_logs import post_logs
            post_logs(change,impact,evidence,candidates,token,args.change.parent)
    except (CatalogError,OSError,ValueError,KeyError,pymysql.MySQLError) as error:
        parser.exit(1,f'Evidence/candidate stage failed: {error}\n')


if __name__=='__main__':
    main()
