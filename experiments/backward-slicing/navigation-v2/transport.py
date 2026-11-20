"""Tagged source-slot transport shared by Java and Python generation studies."""

import argparse
import asyncio
import importlib.util
from pathlib import Path
import os
import secrets

from study import ARMS, HERE, case_data, gen, read
from mock_spec.tagged_slots import parse_tagged_slots

OUT = HERE / "transport"
LEGACY_INPUTS = HERE / "legacy-json-inputs"
LEGACY_INSTRUCTION = """Complete the marked regions of the masked test using the supplied test, product sources, and the audited read/search tools. Preserve the code outside the regions. Decide how to complete the test from the visible scenario. Return only the two regions as verbatim Java source, with these delimiter lines exactly once in this order. A region may be empty. You have up to ten read/search calls.
<MOCK-STUB>
</MOCK-STUB>
<VERIFY>
</VERIFY>
Input:
"""


def parse(raw):
    """Backward-compatible parser for the frozen two-slot Jib supplement."""
    return parse_tagged_slots(raw, ("MOCK-STUB", "VERIFY"))


async def attempt(client, arm, sources):
    output = OUT / arm
    output.mkdir()
    frozen = (LEGACY_INPUTS / "jib" / (arm + ".txt")).read_text()
    expected = read(LEGACY_INPUTS / "manifest.json")["jib"]["prompts"][arm]
    if gen.sha(frozen.encode()) != expected:
        raise ValueError("input digest mismatch")
    prompt = LEGACY_INSTRUCTION + frozen.split("\nInput:\n", 1)[1]
    (output / "prompt.txt").write_text(prompt)
    history = [dict(role="user", content=prompt)]
    trace = dict(status="failed", source_calls=0, turns=[])
    try:
        async with asyncio.timeout(600):
            while True:
                r = await client.responses.create(
                    model=gen.POLICY["model"],
                    input=history,
                    tools=gen.TOOLS,
                    store=False,
                    tool_choice="auto" if trace["source_calls"] < 10 else "none",
                    include=["reasoning.encrypted_content"],
                    truncation="disabled",
                    max_output_tokens=32768,
                )
                event = dict(
                    status=r.status,
                    text=r.output_text,
                    id=r.id,
                    usage=r.usage.model_dump(),
                    tools=[],
                )
                trace["turns"].append(event)
                if r.status != "completed":
                    raise ValueError("incomplete response")
                calls = [o for o in r.output if o.type == "function_call"]
                if not calls:
                    gen.save(
                        output / "candidate.json",
                        dict(slots=parse(r.output_text), rationale=[]),
                    )
                    trace["status"] = "generated"
                    break
                history.extend(o.model_dump(exclude_none=True) for o in r.output)
                for call in calls:
                    result = "Source budget exhausted"
                    if trace["source_calls"] < 10:
                        trace["source_calls"] += 1
                        result = gen.source_call(call, sources)
                    event["tools"].append(
                        dict(name=call.name, args=call.arguments, result=result)
                    )
                    history.append(
                        dict(
                            type="function_call_output",
                            call_id=call.call_id,
                            output=result,
                        )
                    )
    except Exception as error:
        trace["error_type"] = type(error).__name__
        if isinstance(error, ValueError):
            trace["reason"] = str(error)
    gen.save(output / "trace.json", trace)


async def main():
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != "https://api.deepseek.com":
        raise ValueError("unexpected endpoint")
    OUT.mkdir(exist_ok=False)
    _, sources, _, _ = case_data("jib")
    gen.save(
        OUT / "protocol.json",
        dict(
            protocol_sha256=gen.sha((HERE / "transport-protocol.md").read_bytes()),
            input_manifest_sha256=gen.sha(
                (LEGACY_INPUTS / "manifest.json").read_bytes()
            ),
        ),
    )
    async with gen.AsyncOpenAI(max_retries=0, timeout=600) as client:
        await asyncio.gather(*(attempt(client, arm, sources) for arm in ARMS))
    gen.save(
        OUT / "freeze.json",
        {
            str(p.relative_to(OUT)): gen.sha(p.read_bytes())
            for p in sorted(OUT.glob("*/*"))
            if p.is_file()
        },
    )
    mapping = {secrets.token_hex(9): arm for arm in ARMS}
    gen.save(OUT / "private-map.json", mapping)
    candidates = []
    for identity, arm in sorted(mapping.items()):
        path = OUT / arm / "candidate.json"
        candidates.append(
            dict(
                blind_id=identity,
                case="jib",
                slots=read(path)["slots"] if path.is_file() else None,
            )
        )
    gen.save(OUT / "anonymous.json", dict(candidates=candidates))


def evaluate():
    spec = importlib.util.spec_from_file_location(
        "navigation_evaluator", HERE / "evaluate.py"
    )
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    for name, digest in read(OUT / "freeze.json").items():
        if gen.sha((OUT / name).read_bytes()) != digest:
            raise ValueError("frozen transport artifact changed")
    work = OUT / "evaluation-artifacts"
    work.mkdir()
    subjects = []
    for arm in ARMS:
        candidate = OUT / arm / "candidate.json"
        row = dict(
            case="jib",
            repeat=1,
            arm=arm,
            subject_id=arm,
            candidate=str(candidate) if candidate.exists() else None,
        )
        variants = evaluator._evaluate_jib(
            row, work, Path("/home/haon/projects/Stub-Tester")
        )
        subjects.append(evaluator._summarize_jib(row, variants))
    gen.save(OUT / "evaluation.json", dict(subjects=subjects))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "evaluate"])
    if parser.parse_args().command == "run":
        asyncio.run(main())
    else:
        evaluate()
