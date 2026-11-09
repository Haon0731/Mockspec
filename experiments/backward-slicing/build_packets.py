"""Attach original observed lines to automatically selected graph nodes."""
import json
import time
from prepare import HERE, write
from python_probe import Probe


def build():
    directory = HERE/'results/flask'
    data = json.loads((directory/'sources.json').read_text())
    # Assertion and call-event criteria are separate: the latter is necessary for
    # exception-only procedures that have no return statement.
    import ast
    seeds = []
    for source in data['sources']:
        tree = ast.parse(source['text'])
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert) or (source['source_id'] == 'mut' and isinstance(node, ast.Call)):
                seeds.append({'source_id': source['source_id'], 'line': source['start_line'] + node.lineno - 1})
    payload = {'sources': data['sources'], 'seeds': seeds}
    start = time.perf_counter()
    graph = Probe(payload).run()
    write(directory/'slice.json', graph)
    write(directory/'probe-input.json', payload)
    write(directory/'metrics.json', {'elapsed_seconds': time.perf_counter()-start,
                                     'nodes': len(graph['nodes']), 'edges': len(graph['edges']), 'seeds': len(seeds)})
    for name in ('flask', 'cloudfoundry'):
        directory = HERE/'results'/name
        data = json.loads((directory/'sources.json').read_text())
        graph = json.loads((directory/'slice.json').read_text())
        excerpts = []
        for source in data['sources']:
            selected = set()
            for node in graph['nodes']:
                if node['source_id'] == source['source_id']:
                    selected.update(range(node['start_line'], node['end_line']+1))
            lines = source['text'].splitlines()
            if selected:
                excerpts.append({'source_id': source['source_id'], 'logical_path': source['logical_path'],
                                 'lines': {str(i): lines[i-source['start_line']] for i in sorted(selected)
                                           if 0 <= i-source['start_line'] < len(lines)}})
        # Compact lossless topology: original code is supplied in the line map,
        # numeric node ids avoid repeating long repository identities per edge.
        ids = {n['id']: i for i, n in enumerate(graph['nodes'])}
        packed = {**graph,
                  'nodes': [{**{k:v for k,v in n.items() if k not in ('id','code')}, 'id': ids[n['id']]} for n in graph['nodes']],
                  'edges': [{**e, 'from': ids[e['from']], 'to': ids[e['to']]} for e in graph['edges']],
                  'seeds': [ids[s] for s in graph['seeds']]}
        packet = {'origin': 'automatic_source_evidence_probe', 'graph': packed, 'original_lines': excerpts}
        (directory/'agent-evidence.json').write_text(json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n')

if __name__ == '__main__':
    build()
