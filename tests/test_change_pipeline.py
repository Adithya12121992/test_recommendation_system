from pathlib import Path
import tempfile
import unittest

from test_recommendation.catalog import CatalogError
from test_recommendation.change_pipeline import ANALYZER,VERSION,changed_paths,checksum,diff_ranges,expand,load,save
from test_recommendation.impact_graph import graph_from_sources


class ChangePipelineTests(unittest.TestCase):
    def test_rename_and_space_paths(self):
        rows=changed_paths(b'R100\0src/old name.py\0src/new name.py\0D\0deleted.py\0A\0added.py\0')
        self.assertEqual(rows[0]['old_path'],'src/old name.py')
        self.assertEqual(rows[0]['new_path'],'src/new name.py')
        self.assertIsNone(rows[1]['new_path'])
        self.assertIsNone(rows[2]['old_path'])

    def test_hunks_preserve_zero_and_implicit_counts(self):
        self.assertEqual(diff_ranges('@@ -0,0 +1,4 @@\n@@ -8 +10 @@\n'),[
            dict(old_start=0,old_count=0,new_start=1,new_count=4),dict(old_start=8,old_count=1,new_start=10,new_count=1)])

    def fixture(self):
        graph=graph_from_sources({'src/mesh_harbor/a.py':'def leaf():\n    pass\n',
                                 'src/mesh_harbor/b.py':'from .a import leaf\ndef caller():\n    leaf()\n'},'base')
        change=dict(stage='change_extraction',stage_version=VERSION,analyzer_version=ANALYZER,run_id='run',
                    base_revision_sha='base',target_revision_sha='target',base_graph_sha256=checksum(graph),
                    changed_symbols=[{'id':'mesh_harbor.a:leaf'}],changed_source_paths=['src/mesh_harbor/a.py'],evidence_gaps=[])
        return change,graph

    def test_reverse_calls_have_auditable_paths(self):
        change,graph=self.fixture()
        result=expand(change,graph)
        caller=next(row for row in result['impacted_symbols'] if row['id']=='mesh_harbor.b:caller')
        self.assertEqual(caller['distance'],1)
        self.assertEqual(caller['trace'][0]['callee'],'mesh_harbor.a:leaf')
        self.assertEqual(caller['trace'][0]['line'],3)
        self.assertEqual(result['run_id'],'run')
        self.assertNotIn('mesh_harbor.b:caller',{row['id'] for row in expand(change,graph,0)['impacted_symbols']})

    def test_wrong_revision_or_modified_graph_rejected(self):
        change,graph=self.fixture()
        graph['revision_sha']='target'
        with self.assertRaises(CatalogError):
            expand(change,graph)

    def test_new_symbols_do_not_get_fabricated_parent_callers(self):
        change,graph=self.fixture()
        change['changed_symbols']=[{'id':'mesh_harbor.a:new'}]
        change['evidence_gaps']=[{'kind':'new_symbol','symbol':'mesh_harbor.a:new'}]
        result=expand(change,graph)
        self.assertEqual(result['impacted_symbols'],[])
        self.assertEqual(result['evidence_gaps'],change['evidence_gaps'])

    def test_tampered_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'stage.json'
            save(path,{'run_id':'original'})
            self.assertEqual(load(path),{'run_id':'original'})
            path.write_text(path.read_text().replace('original','modified'))
            with self.assertRaises(CatalogError):
                load(path)

    def test_readable_diff_artifact_tampering_is_rejected(self):
        import hashlib
        from test_recommendation.change_pipeline import validate_patches
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            content=b'diff --git a/a.py b/a.py\n-old\n+new\n'
            (directory/'change.patch').write_bytes(content)
            change=dict(stage_version=VERSION,files=[],diff_artifact=dict(path='change.patch',sha256=hashlib.sha256(content).hexdigest()))
            validate_patches(change,directory)
            (directory/'change.patch').write_bytes(b'changed')
            with self.assertRaises(CatalogError):
                validate_patches(change,directory)

    def test_actual_git_diff_is_saved_with_before_after_code(self):
        import subprocess
        from test_recommendation.change_pipeline import extract,save_extraction,validate_patches
        with tempfile.TemporaryDirectory() as temporary:
            repo=Path(temporary)/'repo'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(repo),*args])
            git('init','-q')
            source=repo/'src/mesh_harbor/example.py'
            source.parent.mkdir(parents=True)
            source.write_text('def value():\n    return 1\n')
            git('add','.')
            git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','Base')
            source.write_text('def value():\n    return 2\n')
            git('add','.')
            git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','Change')
            change=extract(repo,'HEAD')
            output=Path(temporary)/'artifacts/change.json'
            save_extraction(repo,output,change)
            validate_patches(change,output.parent)
            patch=(output.parent/'change.patch').read_text()
            self.assertIn('-    return 1',patch)
            self.assertIn('+    return 2',patch)
            per_file=output.parent/change['files'][0]['diff_artifact']['path']
            self.assertEqual(per_file.read_text(),patch)
