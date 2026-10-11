"""GitHub-only change/impact stages; downloaded raw diffs are always temporary."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

from dotenv import dotenv_values

from test_recommendation.catalog import CatalogError, require
from test_recommendation.change_pipeline import VERSION,checksum,diff_ranges,expand,load,save
from test_recommendation.impact_graph import VERSION as ANALYZER,graph_from_sources


class GitHub:
    def __init__(self, repository, token):
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository),'Use repository owner/name')
        require(token,'Set GITHUB_TOKEN in the ignored .env or environment')
        self.repository,self.token=repository,token
    def get(self, path, accept='application/vnd.github+json'):
        request=Request(f'https://api.github.com/repos/{self.repository}/{path}',headers={
            'Authorization':f'Bearer {self.token}','Accept':accept,'User-Agent':'test-recommendation-system',
            'X-GitHub-Api-Version':'2026-03-10'})
        try:
            with urlopen(request,timeout=30) as response:
                content=response.read(16*1024*1024+1)
            require(len(content)<=16*1024*1024,'GitHub response exceeds the supported 16 MiB limit')
            return content if accept!='application/vnd.github+json' else json.loads(content)
        except HTTPError as error:
            raise CatalogError(f'GitHub HTTP {error.code}; check repository access, token permissions, and API limits') from None
        except URLError:
            raise CatalogError('GitHub network request failed') from None
    def commit(self, reference):
        return self.get('commits/'+quote(reference,safe='')+'?per_page=100&page=1')
    def sources(self, commit):
        tree=self.get(f'git/trees/{commit["commit"]["tree"]["sha"]}?recursive=1')
        require(not tree.get('truncated'),'GitHub source tree is truncated; refusing incomplete graph')
        entries=[entry for entry in tree['tree'] if entry['path'].startswith('src/mesh_harbor/') and entry['path'].endswith('.py') and entry['type']=='blob']
        require(all(entry['mode']=='100644' or entry['mode']=='100755' for entry in entries),'Python source symlinks are unsupported')
        def fetch(entry):
            blob=self.get('git/blobs/'+entry['sha'])
            require(blob.get('encoding')=='base64','Unsupported GitHub blob encoding')
            content=base64.b64decode(blob['content'])
            identity=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
            require(identity==entry['sha'],'GitHub source blob checksum mismatch')
            return entry['path'],content.decode()
        with ThreadPoolExecutor(max_workers=6) as pool:
            return dict(pool.map(fetch,entries))


def file_diff(raw_text, old, new):
    """Read hunks from the downloaded diff; API snippets may use other context."""
    left,right=old or new,new or old
    expected=f'diff --git a/{left} b/{right}'
    starts=list(re.finditer(r'^diff --git .*$',raw_text,re.MULTILINE))
    for index,match in enumerate(starts):
        header=match.group()
        block=raw_text[match.start():starts[index+1].start() if index+1<len(starts) else len(raw_text)]
        # Normal paths (including spaces) have an exact header. Quoted paths
        # can be identified by their per-file headers without shell parsing.
        quoted_old=json.dumps('a/'+left,ensure_ascii=False)
        quoted_new=json.dumps('b/'+right,ensure_ascii=False)
        if header==expected or header==f'diff --git {quoted_old} {quoted_new}':
            return block
    raise CatalogError('Changed file missing from downloaded diff: '+(new or old))


def download_extract(client, reference):
    commit=client.commit(reference)
    target=commit['sha']
    require(re.fullmatch('[0-9a-f]{40}',target),'Invalid target SHA')
    require(len(commit['parents'])==1,'Remote mode currently requires a single-parent commit')
    base=commit['parents'][0]['sha']
    files=list(commit['files'])
    page=2
    while len(commit['files'])==100:
        require(page<=31,'Commit exceeds supported GitHub file pagination limit')
        commit=client.get(f'commits/{target}?per_page=100&page={page}')
        require(commit['sha']==target,'GitHub pagination changed target revision')
        if page==31:
            require(not commit['files'],'Commit exceeds supported GitHub file pagination limit')
        files.extend(commit['files'])
        page+=1
    # TemporaryDirectory removes the downloaded diff on success AND exceptions.
    with tempfile.TemporaryDirectory(prefix='recommendation-github-diff-') as temporary:
        diff=client.get(f'commits/{target}',accept='application/vnd.github.diff')
        patch=Path(temporary)/'change.patch'
        patch.write_bytes(diff)
        diff_hash=hashlib.sha256(patch.read_bytes()).hexdigest()
        require(diff or not files,'GitHub returned an empty diff for a changed commit')
        raw_text=diff.decode('utf-8',errors='replace')
        downloaded_ranges=diff_ranges(raw_text)
        rows=[]
        gaps=[]
        paths=set()
        for file in files:
            status=file['status']
            require(status in ('added','removed','modified','renamed','copied','changed','unchanged'),'Unsupported GitHub change status')
            old=None if status=='added' else file.get('previous_filename',file['filename'])
            new=None if status=='removed' else file['filename']
            production=any(path and path.startswith('src/mesh_harbor/') for path in (old,new))
            snippet=file_diff(raw_text,old,new)
            has_hunks=bool(diff_ranges(snippet))
            row=dict(status=status,old_path=old,new_path=new,production_source=production,
                     diff_ranges=diff_ranges(snippet) if snippet and production else [],
                     patch_available=has_hunks,
                     patch_sha256=hashlib.sha256(snippet.encode()).hexdigest() if snippet else None)
            rows.append(row)
            if production:
                paths.update(path for path in (old,new) if path and path.startswith('src/mesh_harbor/'))
                if not has_hunks and status not in ('renamed','copied','unchanged'):
                    gaps.append(dict(kind='patch_unavailable',path=new or old))
        del diff,raw_text
    parent_commit=client.commit(base)
    require(parent_commit['sha']==base,'GitHub returned the wrong parent revision')
    before=graph_from_sources(client.sources(parent_commit),base)
    after=graph_from_sources(client.sources(client.commit(target)),target)
    symbols=[]
    for identifier in sorted(before['nodes'].keys()|after['nodes'].keys()):
        old,new=before['nodes'].get(identifier),after['nodes'].get(identifier)
        if (old or new)['kind']=='module' or old and new and old['ast_sha256']==new['ast_sha256']:
            continue
        symbols.append(dict(id=identifier,kind=(old or new)['kind'],change='added' if old is None else 'removed' if new is None else 'modified',before=old,after=new))
    gaps.extend(dict(kind='new_symbol',symbol=row['id']) for row in symbols if row['change']=='added')
    change=dict(stage='change_extraction',stage_version=VERSION,analyzer_version=ANALYZER,run_id=str(uuid.uuid4()),
                acquisition='github_api',repository=client.repository,base_revision_sha=base,target_revision_sha=target,
                comparison='first_parent',merge_target=False,files=rows,changed_source_paths=sorted(paths),changed_symbols=symbols,
                base_graph_sha256=checksum(before),target_graph_sha256=checksum(after),evidence_gaps=gaps,
                downloaded_diff_hunk_count=len(downloaded_ranges),downloaded_diff_sha256=diff_hash,raw_diff_retained=False,temporary_downloads_deleted=True)
    return change,before


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('extract','expand','run'))
    parser.add_argument('--repository',default='Adithya12121992/test_recommendation_product')
    parser.add_argument('--target')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--hops',type=int,default=3)
    args=parser.parse_args()
    try:
        if args.stage=='expand':
            require(args.input is not None,'--input is required')
            change=load(args.input)
            graph=load(args.input.with_name('base_graph.json'))
            result=expand(change,graph,args.hops)
            output=args.output or args.input.with_name('impact.json')
            save(output,result)
        else:
            require(args.target is not None,'--target is required')
            token=os.environ.get('GITHUB_TOKEN') or dotenv_values(args.env_file).get('GITHUB_TOKEN')
            change,graph=download_extract(GitHub(args.repository,token),args.target)
            output=args.output or Path('artifacts/pipeline')/change['run_id']/'change.json'
            save(output,change)
            save(output.with_name('base_graph.json'),graph)
            if args.stage=='run':
                save(output.with_name('impact.json'),expand(change,graph,args.hops))
        print(f'{args.stage}: {output}; raw downloaded diff deleted, no clone used.')
    except (CatalogError,OSError,ValueError,SyntaxError,KeyError) as error:
        parser.exit(1,f'Remote pipeline failed: {error}\n')


if __name__=='__main__':
    main()
