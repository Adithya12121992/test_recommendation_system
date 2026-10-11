"""Pinned open-PR acquisition, including multi-commit and fork comparisons."""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import quote,urlsplit

from dotenv import dotenv_values

from test_recommendation.catalog import CatalogError,require
from test_recommendation.change_pipeline import expand,save
from test_recommendation.github_pipeline import GitHub,download_extract


def parse_url(url):
    parts=urlsplit(url)
    require(parts.scheme=='https' and parts.netloc=='github.com' and not parts.query and not parts.fragment,'Use an HTTPS github.com PR URL without query/fragment')
    match=re.fullmatch(r'/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?',parts.path)
    require(match is not None,'Expected https://github.com/owner/repository/pull/number')
    return match[1],int(match[2])


def identity(pr):
    require(pr['state']=='open' and not pr.get('merged',False),'PR must be open and unmerged')
    require(pr['head'].get('repo') and pr['base'].get('repo'),'PR head/base repository unavailable')
    result=dict(base_repository=pr['base']['repo']['full_name'],head_repository=pr['head']['repo']['full_name'],
                base_tip_sha=pr['base']['sha'],head_sha=pr['head']['sha'])
    require(all(re.fullmatch('[0-9a-f]{40}',result[key]) for key in ('base_tip_sha','head_sha')),'Invalid PR revision SHA')
    return result


def acquire(url,token,client_factory=GitHub):
    repository,number=parse_url(url)
    base_client=client_factory(repository,token)
    pr=base_client.get(f'pulls/{number}')
    pinned=identity(pr)
    require(pinned['base_repository'].lower()==repository.lower(),'PR base repository differs from URL')
    head_client=client_factory(pinned['head_repository'],token)
    owner=pinned['head_repository'].split('/')[0]
    head=f'{owner}:{pinned["head_sha"]}' if pinned['head_repository'].lower()!=repository.lower() else pinned['head_sha']
    comparison=base_client.get('compare/'+quote(pinned['base_tip_sha']+'...'+head,safe='.:')+'?per_page=1')
    merge_base=comparison['merge_base_commit']['sha']
    require(re.fullmatch('[0-9a-f]{40}',merge_base),'Invalid merge-base revision')
    files=[]
    for page in range(1,32):
        batch=base_client.get(f'pulls/{number}/files?per_page=100&page={page}')
        if page==31:
            require(not batch,'PR exceeds the supported 3000-file API limit')
        files.extend(batch)
        if len(batch)<100:
            break
    require(len(files)==pr['changed_files'] and len({file['filename'] for file in files})==len(files),'PR file pagination incomplete or changed during acquisition')
    require(identity(base_client.get(f'pulls/{number}'))==pinned,'PR changed during file acquisition; retry with latest revisions')
    head_commit=head_client.commit(pinned['head_sha'])
    require(head_commit['sha']==pinned['head_sha'],'Head commit SHA differs from pinned PR')
    actual_parents=head_commit['parents']
    compare_path='compare/'+quote(merge_base+'...'+head,safe='.:')
    class Comparison:
        # Reuse diff parsing/cleanup and AST analysis with pinned comparison inputs.
        repository=base_client.repository
        def commit(self,reference):
            if reference==pinned['head_sha']:
                return dict(head_commit,parents=[{'sha':merge_base}],files=files[:100])
            return base_client.commit(reference)
        def get(self,path,accept='application/vnd.github+json'):
            if accept=='application/vnd.github.diff':
                return base_client.get(compare_path,accept=accept)
            page=int(path.rsplit('page=',1)[1])
            return dict(head_commit,files=files[(page-1)*100:page*100])
        def sources(self,commit):
            client=head_client if commit['sha']==pinned['head_sha'] else base_client
            return client.sources(commit)
    change,graph=download_extract(Comparison(),pinned['head_sha'])
    require(change['base_revision_sha']==merge_base,'Extraction did not use pinned merge base')
    require(identity(base_client.get(f'pulls/{number}'))==pinned,'PR changed during processing; discard results and retry')
    change.update(acquisition='github_pull_request',comparison='merge_base_to_head',merge_target=len(actual_parents)>1,
                  pull_request=dict(url=url,number=number,**pinned,merge_base_sha=merge_base,
                                    fork=pinned['head_repository'].lower()!=repository.lower(),draft=bool(pr.get('draft')),
                                    commit_count=pr['commits'],changed_file_count=len(files)))
    return change,graph


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pr',required=True)
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--hops',type=int,default=3)
    args=parser.parse_args()
    try:
        token=os.environ.get('GITHUB_TOKEN') or dotenv_values(args.env_file).get('GITHUB_TOKEN')
        change,graph=acquire(args.pr,token)
        result=expand(change,graph,args.hops)
        output=args.output or Path('artifacts/pipeline')/change['run_id']/'change.json'
        save(output,change)
        save(output.with_name('base_graph.json'),graph)
        save(output.with_name('impact.json'),result)
        print(f'Processed PR #{change["pull_request"]["number"]}: {len(change["files"])} files; saved {output}. Temporary diff deleted; no clone used.')
    except (CatalogError,OSError,ValueError,KeyError,SyntaxError) as error:
        parser.exit(1,f'PR acquisition failed: {error}\n')


if __name__=='__main__':
    main()
