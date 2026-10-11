import unittest
from test_recommendation.impact_graph import graph_from_sources, impact


class ImpactGraphTests(unittest.TestCase):
    def graph(self, change='return 1'):
        return graph_from_sources({
            'src/mesh_harbor/a.py': f'def leaf():\n    {change}\n',
            'src/mesh_harbor/b.py': 'from .a import leaf as alias\ndef caller():\n    return alias()\n',
            'src/mesh_harbor/c.py': 'from mesh_harbor.b import caller\ndef outer():\n    return caller()\n',
        }, change)

    def test_import_alias_relative_import_and_reverse_call_expansion(self):
        base = self.graph()
        self.assertIn({'caller': 'mesh_harbor.b:caller', 'callee': 'mesh_harbor.a:leaf', 'line': 3}, base['calls'])
        report = impact(base, self.graph('return 2'))
        distances = {row['id']: row['distance'] for row in report['impacted_symbols']}
        self.assertEqual(distances['mesh_harbor.b:caller'], 1)
        self.assertEqual(distances['mesh_harbor.c:outer'], 2)
        bounded = impact(base, self.graph('return 2'), 1)
        self.assertNotIn('mesh_harbor.c:outer', {row['id'] for row in bounded['impacted_symbols']})

    def test_self_constructor_and_annotated_argument_calls(self):
        graph = graph_from_sources({'src/mesh_harbor/x.py': '''class Worker:
    def __init__(self):
        pass
    def go(self):
        return self.helper()
    def helper(self):
        pass

def run(worker: Worker):
    instance = Worker()
    worker.go()
    instance.go()
'''}, 'sha')
        edges = {(edge['caller'], edge['callee']) for edge in graph['calls']}
        self.assertIn(('mesh_harbor.x:Worker.go', 'mesh_harbor.x:Worker.helper'), edges)
        self.assertIn(('mesh_harbor.x:run', 'mesh_harbor.x:Worker.__init__'), edges)
        self.assertIn(('mesh_harbor.x:run', 'mesh_harbor.x:Worker.go'), edges)

    def test_deleted_and_added_symbols_and_dynamic_calls(self):
        before = graph_from_sources({'src/mesh_harbor/x.py': 'def old():\n    unknown.invoke()\n'}, 'a')
        after = graph_from_sources({'src/mesh_harbor/x.py': 'def new():\n    pass\n'}, 'b')
        report = impact(before, after)
        self.assertIn('mesh_harbor.x:old', report['removed_symbols'])
        self.assertIn('mesh_harbor.x:new', report['added_symbols'])
        self.assertEqual(len(before['unresolved_calls']), 1)

    def test_formatting_does_not_change_symbols_but_file_diff_is_retained(self):
        before = self.graph()
        after = self.graph('return 1  # comment')
        report = impact(before, after, touched_files=['src/mesh_harbor/a.py'])
        self.assertEqual(report['changed_symbols'], [])
        self.assertEqual(report['changed_files'], ['src/mesh_harbor/a.py'])
        self.assertIn('src/mesh_harbor/b.py', {row['path'] for row in report['impacted_files']})

    def test_cycles_terminate(self):
        before = graph_from_sources({'src/mesh_harbor/x.py': 'def a():\n    b()\ndef b():\n    a()\n'}, 'a')
        after = graph_from_sources({'src/mesh_harbor/x.py': 'def a():\n    b()\n    return 1\ndef b():\n    a()\n'}, 'b')
        self.assertEqual(len(impact(before, after)['impacted_symbols']), 2)
