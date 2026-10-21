"""Read Joern's exported edges; do not infer/rebuild CFG or data-flow edges."""
from collections import Counter
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

NS = {'g': 'http://graphml.graphdrawing.org/xmlns'}


def read_graph(path):
    root = ET.parse(path).getroot()
    keys = {k.attrib['id']: k.attrib['attr.name'] for k in root.findall('g:key', NS)}
    def props(element):
        return {keys[d.attrib['key']]: d.text for d in element.findall('g:data', NS)}
    nodes = {n.attrib['id']: props(n) for n in root.findall('g:graph/g:node', NS)}
    edges = [{'source': e.attrib['source'], 'target': e.attrib['target'], **props(e)}
             for e in root.findall('g:graph/g:edge', NS)]
    # AST membership is used only for source attribution, never for flow inference.
    parents = {e['target']: e['source'] for e in edges if e['labelE'] == 'AST'}
    def filename(node_id):
        seen = set()
        while node_id not in seen:
            seen.add(node_id)
            n = nodes[node_id]
            if n.get('FILENAME'):
                return n['FILENAME']
            if node_id not in parents:
                return None
            node_id = parents[node_id]
        return None
    def brief(node_id):
        n = nodes[node_id]
        result = {'id': node_id, 'label': n['labelV'], 'file': filename(node_id),
                  'line': int(n['LINE_NUMBER']) if n.get('LINE_NUMBER') else None}
        if n['labelV'] not in {'METHOD', 'BLOCK', 'CONTROL_STRUCTURE', 'TYPE_DECL', 'FILE'}:
            result['code'] = (n.get('CODE') or '')[:240]
        if n.get('NAME'): result['name'] = n['NAME']
        return result
    chosen = []
    for edge in edges:
        if edge['labelE'] not in {'CFG', 'CDG', 'REACHING_DEF'}:
            continue
        left, right = brief(edge['source']), brief(edge['target'])
        if any((x['file'] or '').endswith(('JarFiles.java', 'helpers.py')) and x['line'] is not None
               and (x['file'].endswith('JarFiles.java') or x['line'] >= 265)
               for x in (left, right)):
            chosen.append({'label': edge['labelE'], 'source': left, 'target': right,
                           'variable': edge.get('property')})
    files = [n.get('NAME') for n in nodes.values() if n['labelV'] == 'FILE']
    return {'node_count': len(nodes), 'edge_count': len(edges),
            'edge_counts': dict(Counter(e['labelE'] for e in edges)), 'file_nodes': files,
            'selected_edges': chosen,
            'evidence_kind': 'actual exported Joern edge labels, not reconstructed edges'}


if __name__ == '__main__':
    output = read_graph(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(output, indent=2) + '\n')
