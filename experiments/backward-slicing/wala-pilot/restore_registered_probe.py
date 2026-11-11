"""Materialize a registered probe source after a compatible control-mode extension."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def restore(current: Path, expected_sha256: str, output: Path) -> None:
    text = current.read_text(encoding="utf-8")
    restored = text.replace(
        "if (args.length != 5 && args.length != 6) {", "if (args.length != 5) {").replace(
        "criterion1,criterion2,... [FULL|NONE]", "criterion1,criterion2,...").replace(
        '    Slicer.ControlDependenceOptions control = args.length == 6 && args[5].equals("NONE")\n'
        '        ? Slicer.ControlDependenceOptions.NONE : Slicer.ControlDependenceOptions.FULL;\n'
        '    var slicer = new CISlicer(graph, pointers, modRef, Slicer.DataDependenceOptions.NO_HEAP, control);',
        '    var slicer = new CISlicer(graph, pointers, modRef, Slicer.DataDependenceOptions.NO_HEAP,\n'
        '        Slicer.ControlDependenceOptions.FULL);').replace(
        '"control_dependence", control.toString())', '"control_dependence", "FULL")')
    observed = hashlib.sha256(restored.encode()).hexdigest()
    if observed != expected_sha256:
        raise ValueError(f"restored source hash {observed} != registered {expected_sha256}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(restored, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    restore(args.current, args.expected_sha256, args.output)


if __name__ == "__main__":
    main()
