"""Create or edit one author-owned PR pipeline log comment."""
from datetime import datetime,timezone
import json
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

from test_recommendation.catalog import CatalogError,require
from test_recommendation.change_pipeline import save

MARKER='<!-- test-recommendation-system:pipeline-log:v1 -->'


def request(token,path,method='GET',body=None):
    req=Request('https://api.github.com/'+path,method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization':'Bearer '+token,'User-Agent':'test-recommendation-system',
                         'Accept':'application/vnd.github+json','Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise CatalogError(f'PR log API HTTP {error.code}; commenting needs Issues: write or Pull requests: write') from None
    except URLError:
        raise CatalogError('PR log network request failed') from None


def render(change,impact,evidence,candidates,ranking=None,selection=None):
    lines=[MARKER,'## Test recommendation pipeline log','',
           f'Updated: {datetime.now(timezone.utc).isoformat()}',f'Run: `{change["run_id"]}`',
           f'Base/merge base: `{change["base_revision_sha"]}`',f'Head: `{change["target_revision_sha"]}`','',
           '### Completed stages','',
           f'1. **PR acquisition:** pinned revisions; {len(change["files"])} changed files; raw downloaded diff deleted; no clone.',
           f'2. **Change extraction:** {len(change["changed_symbols"])} changed definitions.',
           f'3. **Impact expansion:** {len(impact["impacted_files"])} source files; parent graph only; max {impact["max_hops"]} hops.',
           f'4. **Evidence retrieval:** {len(evidence["coverage_matches"])} coverage matches, {len(evidence["issue_matches"])} historical issue matches, {len(evidence["feedback_matches"])} reviewed feedback matches.',
           f'5. **Candidate generation:** {len(candidates["candidates"])} suites; reasons below.','',
           ('**Test execution has not run.**' if selection else '**Claude selection, ranking, and test execution have not run.**'),'',
           '### Impacted code','']
    lines.extend(f'- `{row["path"]}` — distance {row["distance"]}; '+', '.join(sorted({reason['kind'] for reason in row['reasons']})) for row in impact['impacted_files'])
    lines+=['','### Candidate suites','']
    for row in candidates['candidates']:
        reasons=', '.join(sorted({reason['kind'] for reason in row['reasons']}))
        runtime=row['measured_runtime_seconds']
        lines.append(f'- `{row["suite_id"]}` → `{row["unittest_target"]}`; {reasons}; measured runtime '+(f'{runtime:.4f}s' if runtime is not None else 'unavailable')+f'; protected fallback: {row["protected"]}.')
    lines+=['','### Suites retrieved by source','',
            'These are suite-level mappings; individual test names below come from the pinned PR head, not per-test coverage.']
    sources=[('Measured coverage',evidence['coverage_matches']),
             ('Synthetic Jira',[r for r in evidence['issue_matches'] if r['tracker']=='jira_mapping']),
             ('Synthetic bugs',[r for r in evidence['issue_matches'] if r['tracker']=='bugzilla_mapping']),
             ('Reviewed feedback',evidence['feedback_matches'])]
    for label,rows in sources:
        lines+=['',f'**{label}:** {len(rows)} evidence links; {len({r["suite_id"] for r in rows})} unique suites.']
        for row in rows:
            detail=(f'{row["covered_line_count"]} executed lines in `{row["source_path"]}`; impact distance {row["impact_distance"]}'
                    if label=='Measured coverage' else
                    f'issue `{row["issue_id"]}` → `{row["source_path"]}`' if 'issue_id' in row else
                    f'{row["action"]}; {row["reason"]}')
            lines.append(f'- `{row["suite_id"]}` — {detail}.')
    fallbacks=[r['suite_id'] for r in candidates['candidates'] if r['protected']]
    lines+=['', '**Catalog fallback:** '+(', '.join(f'`{s}`' for s in fallbacks) or 'none')+'.',
            '', '### Individual tests available at PR head','']
    for row in candidates['candidates']:
        lines.append(f'**{row["suite_id"]}:** {row.get("target_test_case_count",0)} tests.')
        lines.extend('- `'+method+'`' for method in row.get('target_test_methods',[]))
    if ranking:
        lines+=['','### Candidate ranking','',
                'Untuned technical heuristic; feedback contributes at most ±5 points. Scores are not probabilities.']
        for row in ranking['candidates']:
            lines.append(f'- `{row["suite_id"]}`: **{row["score"]}/100**; components `{json.dumps(row["score_components"],sort_keys=True)}`; feedback {row["feedback_adjustment"]:+g}.')
    if selection:
        lines+=['','### Claude selection','',f'Status: `{selection["status"]}`; model: `{selection.get("model_returned",selection["model_requested"])}`.',
                'Final suites: '+', '.join(f'`{s}`' for s in selection['selected_suite_ids'])+'.',
                f'Suites retained: {selection["selected_count"]}/{selection["candidate_count"]}; suite reduction: {selection["suite_reduction_fraction"]:.0%}; selected test count: {selection["selected_test_case_count"]}.']
        for row in selection.get('claude_decisions',[]):
            lines.append(f'- `{row["suite_id"]}`: Claude {"retained" if row["selected"] else "omitted"} — '+row['reason'])
        lines.extend('- Guardrail: `'+json.dumps(action,sort_keys=True)+'`' for action in selection['guardrail_actions'])
        if selection.get('error'):
            lines.append('API/validation failure: '+selection['error'])
        lines.append('Runtime is an estimate from prior coverage runs; no execution or fault-detection claim.')
    lines+=['','### Evidence provenance and gaps','',f'Coverage run: `{evidence.get("selected_coverage_run_id","none")}`',
            f'Measured revision: `{evidence.get("coverage_revision_sha","none")}`',
            'Coverage reuse requires ancestry plus identical full source/test subtree hashes. Synthetic issues are labeled fictional; golden answers are never retrieved.',
            f'Unresolved static calls in affected code: {len(impact["unresolved_calls"])}.']
    lines.extend('- '+json.dumps(gap,sort_keys=True) for gap in evidence['gaps'])
    logs=dict(coverage_decisions=evidence['coverage_audit'],issue_decisions=evidence['issue_audit'],
              feedback_decisions=evidence['feedback_audit'],changed_files=change['files'],
              changed_symbols=[{'id':row['id'],'change':row['change']} for row in change['changed_symbols']],
              impact_traces=impact['impacted_files'],candidate_reasons=candidates['candidates'])
    lines+=['','<details><summary>Detailed stage logs and evidence decisions</summary>','',
            '```json',json.dumps(logs,indent=2),'```','','</details>','',
            'This comment describes the pinned run above. A later push requires a new run. The same comment is updated for subsequent logs.']
    body='\n'.join(lines)
    require(len(body.encode())<60000,'Logs exceed comment size budget; retain artifacts instead of publishing a partial log')
    return body


def upsert(token,repository,number,body):
    user=request(token,'user')
    existing=[]
    for page in range(1,101):
        batch=request(token,f'repos/{repository}/issues/{number}/comments?per_page=100&page={page}')
        existing.extend(comment for comment in batch if comment.get('user',{}).get('id')==user['id'] and comment.get('body','').startswith(MARKER))
        if len(batch)<100:
            break
    else:
        raise CatalogError('Comment pagination limit reached; refusing to create duplicate logs')
    if existing:
        comment=min(existing,key=lambda row:row['id'])
        return request(token,f'repos/{repository}/issues/comments/{comment["id"]}','PATCH',{'body':body})
    return request(token,f'repos/{repository}/issues/{number}/comments','POST',{'body':body})


def post_logs(change,impact,evidence,candidates,token,directory,ranking=None,selection=None):
    pr=change.get('pull_request')
    require(pr is not None,'PR logging requires PR acquisition provenance')
    current=request(token,f'repos/{change["repository"]}/pulls/{pr["number"]}')
    require(current['state']=='open' and current['head']['sha']==pr['head_sha'] and current['base']['sha']==pr['base_tip_sha'],
            'PR moved or closed; regenerate artifacts before commenting')
    body=render(change,impact,evidence,candidates,ranking,selection)
    (directory/'pr_log.md').write_text(body)
    comment=upsert(token,change['repository'],pr['number'],body)
    save(directory/'pr_comment.json',dict(comment_id=comment['id'],url=comment['html_url'],run_id=change['run_id']))
    print('PR log:',comment['html_url'])
    return comment
