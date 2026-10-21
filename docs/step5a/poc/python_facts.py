"""Bounded AST API probe; no import/execution of analyzed source or CFG engine."""
import ast
import json
import sys
from pathlib import Path


def analyze(text, source_id):
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {'source_id': source_id, 'status': 'parse_error', 'line': exc.lineno,
                'reason': exc.msg}
    facts = []
    def visit(node, guards):
        if isinstance(node, (ast.Call, ast.If, ast.Assert, ast.NamedExpr, ast.FunctionDef)):
            fact = {'kind': type(node).__name__, 'source_id': source_id,
                    'start_line': node.lineno, 'end_line': node.end_lineno,
                    'guards': guards}
            if isinstance(node, ast.Call):
                fact['callee'] = ast.unparse(node.func)
            if isinstance(node, ast.If):
                fact['condition'] = ast.unparse(node.test)
            if isinstance(node, ast.NamedExpr):
                fact['assigned_name'] = ast.unparse(node.target)
                fact['value'] = ast.unparse(node.value)
            facts.append(fact)
        if isinstance(node, ast.If):
            visit(node.test, guards)
            for polarity, nodes in ((True, node.body), (False, node.orelse)):
                for child in nodes:
                    visit(child, guards + [{'if_line': node.lineno, 'polarity': polarity}])
        else:
            for child in ast.iter_child_nodes(node):
                visit(child, guards)
    visit(tree, [])
    return {'status': 'parsed', 'source_id': source_id, 'facts': facts,
            'limitations': ['syntax facts only; no CFG/def-use/slicing/path feasibility',
                            'call resolution and exceptional control flow unknown']}


if __name__ == '__main__':
    print(json.dumps(analyze(Path(sys.argv[1]).read_text(), sys.argv[2]), indent=2))
