"""Prepare and run the seven-case fact-map study with audited frozen sources."""

import argparse
import asyncio
from collections import Counter
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "issue35"))
from issue34 import prepare_inputs as flask
from issue34 import source_tool
from issue35 import generate as gen
from mock_spec.validation import SourceStore

ARMS = ("v1", "ablation", "v2")
CASES = ("jib", "flask_308")
ALL_CASES = (
    "flask_308",
    "jib",
    "zipkin",
    "langflow_189",
    "ansible_119",
    "click_423",
    "click_402",
)
PROMPT = (
    "Complete the marked regions of the masked test using the supplied test, product sources, "
    "and the audited read/search tools. Preserve the code outside the regions. Decide how to "
    "complete the test from the visible scenario. Return one JSON object with fields slots and "
    "rationale. slots maps every required slot name to replacement source; for Python the first "
    "line is relative to the marker, without the marker's existing indentation. Use valid "
    "language syntax for an unused region. rationale is an array of short source-grounded "
    "explanations. You have up to ten read/search calls.\nInput:\n"
)
LEGACY_INPUTS = HERE / "legacy-json-inputs"


def read(path):
    return json.loads(path.read_bytes())


def case_data(case):
    if case != "flask_308":
        if case not in gen.case_keys():
            raise ValueError(f"unknown frozen case: {case}")
        contract, sources = gen.load_case(case)
        return (
            contract,
            sources,
            gen.load_prompt_sources(case),
            contract["identity"]["snapshot_id"],
        )
    previous = read(flask.STUDY)
    old_root = Path("/home/haon/projects/Stub-Tester")
    snapshot = (
        old_root
        / source_tool.REGISTRY
        / "formal-snapshots"
        / source_tool.DATASET
        / source_tool.SNAPSHOT
    )
    selected = flask.adapter_task(
        old_root,
        read(flask.FLASK_RESULTS / "task.json"),
        read(flask.FLASK_RESULTS / "sources.json"),
    )
    segments = flask.snapshot_segments(old_root, previous)
    contract = previous["case_result"]
    scope = {r["logical_path"]: r for r in contract["source_scope"]}
    task, repo = flask.load_task_with_repository(snapshot, case_id=case)
    sources = {}
    try:
        store = SourceStore(task, repository=repo)
        for segment in segments:
            path = segment["logical_path"]
            src = store.analysis_source(path)
            raw = next(
                (
                    s
                    for s in [src.text, src.text + "\n"]
                    if gen.sha(s.encode()) == segment["sha256"]
                ),
                None,
            )
            if raw is None:
                raise ValueError("Flask source identity changed")
            source_id = scope[path]["source_id"]
            sources[source_id] = dict(
                source_id=source_id,
                logical_path=path,
                text=raw,
                start_line=src.start_line,
                end_line=src.start_line + len(raw.splitlines()) - 1,
            )
    finally:
        repo.close()
    initial = {}
    for k in ("masked_test", "mut_source"):
        piece = selected[k]
        source_id = scope[piece["logical_path"]]["source_id"]
        initial[source_id] = {
            field: piece[field]
            for field in ("text", "logical_path", "start_line", "end_line")
        }
        initial[source_id]["source_id"] = source_id
    return contract, sources, initial, source_tool.SNAPSHOT


def _base_payload(contract, sources, initial, slots):
    return {
        "case_id": contract["identity"]["case_id"],
        "initial_sources": initial,
        "slot_ids": slots,
        "available_sources": [
            {key: value for key, value in source.items() if key != "text"}
            for source in sources.values()
        ],
    }


def evidence_arms(contract, sources):
    from facts import format_facts

    original = gen.format_navigation(contract, sources)
    original.pop("projection")
    ablated = copy.deepcopy(original)
    assert (
        ablated["interpretation"].pop("call_binding_is_interaction_candidate") is True
    )
    return {"v1": original, "ablation": ablated, "v2": format_facts(contract, sources)}


def prepare(out):
    """Replay the immutable two-case JSON-transport input package."""
    shutil.copytree(LEGACY_INPUTS, out)


def prepare_tagged(out):
    """Freeze seven-case Base/v2 inputs using the shared source-slot transport."""
    from facts import format_facts
    from mock_spec.tagged_slots import build_tagged_prompt

    out.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for case in ALL_CASES:
        contract, sources, initial, snapshot = case_data(case)
        slots = gen.slot_ids(contract, sources["test"]["text"])
        base = _base_payload(contract, sources, initial, slots)
        facts = format_facts(contract, sources)
        case_dir = out / case
        case_dir.mkdir()
        gen.save(case_dir / "facts.json", facts)
        hashes = {}
        for name, evidence in (("base-tagged", None), ("v2-tagged", facts)):
            prompt = build_tagged_prompt(base, evidence, slots)
            (case_dir / f"{name}.txt").write_text(prompt)
            hashes[name] = gen.sha(prompt.encode())
        manifest[case] = {
            "slots": slots,
            "analysis": contract["identity"],
            "generation_snapshot": snapshot,
            "prompts": hashes,
            "sources": {
                source_id: gen.sha(source["text"].encode())
                for source_id, source in sources.items()
            },
        }
    gen.save(out / "manifest.json", manifest)


async def run(inputs, out):
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != "https://api.deepseek.com":
        raise ValueError("expected study endpoint")
    manifest = read(inputs / "manifest.json")
    prompts = {}
    sources = {}
    for case in CASES:
        _, sources[case], _, _ = case_data(case)
        if {
            k: gen.sha(s["text"].encode()) for k, s in sources[case].items()
        } != manifest[case]["sources"]:
            raise ValueError("source drift")
        for arm in ARMS:
            text = (inputs / case / (arm + ".txt")).read_text()
            if gen.sha(text.encode()) != manifest[case]["prompts"][arm]:
                raise ValueError("prompt drift")
            prompts[case, arm] = text
    async with gen.AsyncOpenAI(max_retries=0, timeout=600) as client:
        model = (await client.models.retrieve(gen.POLICY["model"])).model_dump()
        size = (
            max(len(s.encode()) for s in prompts.values())
            + len(gen.encode(gen.TOOLS).encode())
            + 4096
        )
        if size + 32768 > model["context_window"] or 32768 > model["max_output_tokens"]:
            raise ValueError("prompt exceeds capacity")
        out.mkdir(parents=True, exist_ok=False)
        gen.save(
            out / "protocol.json",
            dict(
                model=model,
                max_output_tokens=32768,
                max_source_calls=10,
                wall_seconds=600,
                retries=0,
                truncation="disabled",
                input_manifest=gen.sha((inputs / "manifest.json").read_bytes()),
                protocol=gen.sha((HERE / "protocol.md").read_bytes()),
                rubric=gen.sha((HERE / "rubric.md").read_bytes()),
            ),
        )
        for i in range(3):
            order = ARMS[i:] + ARMS[:i]
            await asyncio.gather(
                *(
                    gen.attempt(
                        client,
                        out / case / str(i + 1) / arm,
                        prompts[case, arm],
                        manifest[case]["slots"],
                        sources[case],
                    )
                    for arm in order
                    for case in CASES
                )
            )
    traces = list(out.glob("*/*/*/trace.json"))
    if len(traces) != 18:
        raise ValueError("incomplete matrix")
    files = sorted(
        traces + list(out.glob("*/*/*/candidate.json")) + [out / "protocol.json"]
    )
    gen.save(
        out / "freeze.json",
        dict(
            attempts=18,
            statuses=dict(Counter(read(f)["status"] for f in traces)),
            sha256={str(f.relative_to(out)): gen.sha(f.read_bytes()) for f in files},
        ),
    )


def blind(out):
    freeze = read(out / "freeze.json")
    if freeze["attempts"] != 18 or any(
        gen.sha((out / f).read_bytes()) != h for f, h in freeze["sha256"].items()
    ):
        raise ValueError("invalid freeze")
    public = []
    private = {}
    for case in CASES:
        for i in range(1, 4):
            for arm in ARMS:
                path = out / case / str(i) / arm
                identity = secrets.token_hex(9)
                private[identity] = str(path.relative_to(out))
                trace = read(path / "trace.json")
                public.append(
                    dict(
                        blind_id=identity,
                        case=case,
                        slots=read(path / "candidate.json")["slots"]
                        if trace["status"] == "generated"
                        else None,
                    )
                )
    gen.save(out.parent / "private-map.json", private)
    gen.save(
        out.parent / "anonymous.json",
        {"candidates": sorted(public, key=lambda r: r["blind_id"])},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the seven-case fact navigation study."
    )
    parser.add_argument(
        "command",
        choices=[
            "prepare",
            "run",
            "facts",
            "legacy-prepare",
            "legacy-run",
            "legacy-blind",
        ],
    )
    parser.add_argument("--case", choices=ALL_CASES)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.command.startswith("legacy-"):
        inputs = args.inputs or HERE / "legacy-json-inputs"
        out = args.run_dir or HERE / "run"
        if args.command == "legacy-prepare":
            prepare(inputs)
        elif args.command == "legacy-run":
            asyncio.run(run(inputs, out))
        else:
            blind(out)
    elif args.command == "facts":
        from facts import format_facts

        if not args.case:
            parser.error("facts requires --case")
        c, sources, _, _ = case_data(args.case)
        print(json.dumps(format_facts(c, sources), ensure_ascii=False, indent=2))
    elif args.command == "prepare":
        prepare_tagged(args.inputs or HERE / "inputs")
    else:
        from tagged_study import run_tagged

        asyncio.run(
            run_tagged(
                args.inputs or HERE / "inputs", args.run_dir or HERE / "tagged" / "run"
            )
        )
