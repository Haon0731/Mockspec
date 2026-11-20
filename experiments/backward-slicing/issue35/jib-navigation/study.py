"""Freeze, generate and blind the Jib navigation comparison."""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import generate as generation
from variants import build_prompts

ARMS = ("OC", "PC", "OR", "PR")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(dest, direct=False):
    if direct:
        from direct import build_direct_prompts

        prompts, proof = build_direct_prompts()
    else:
        prompts, proof = build_prompts()
    arms = ("DC", "DR") if direct else ARMS
    assert set(prompts) == set(arms)
    baseline = HERE.parent / "prompts/jib/C.txt"
    if not direct and prompts["OC"].encode() != baseline.read_bytes():
        raise ValueError("OC differs from the frozen #35 prompt")
    dest.mkdir(parents=True, exist_ok=False)
    generation.save(dest / "proof.json", proof)
    paths = {}
    for arm in arms:
        path = dest / (arm + ".txt")
        path.write_bytes(prompts[arm].encode())
        paths[arm] = {"sha256": digest(path), "bytes": path.stat().st_size}
    generation.save(
        dest / "manifest.json",
        {
            "prompts": paths,
            "proof_sha256": digest(dest / "proof.json"),
            "protocol_sha256": digest(
                HERE / ("supplementary/protocol.md" if direct else "protocol.md")
            ),
            "rubric_sha256": digest(HERE.parent / "java_evaluation_criteria.json"),
            "historical_baseline_sha256": digest(baseline),
        },
    )


async def run(inputs, dest, direct=False):
    arms = ("DC", "DR") if direct else ARMS
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != "https://api.deepseek.com":
        raise ValueError("unexpected endpoint")
    manifest = json.loads((inputs / "manifest.json").read_text())
    prompts = {}
    for arm in arms:
        path = inputs / (arm + ".txt")
        if digest(path) != manifest["prompts"][arm]["sha256"]:
            raise ValueError("input drift")
        prompts[arm] = path.read_text()
    if (
        digest(inputs / "proof.json") != manifest["proof_sha256"]
        or digest(HERE / ("supplementary/protocol.md" if direct else "protocol.md"))
        != manifest["protocol_sha256"]
    ):
        raise ValueError("proof or protocol changed")
    _, sources = generation.load_case("jib")
    expected = json.loads((HERE.parent / "prompts/manifest.json").read_text())["jib"]
    if {i: generation.sha(s["text"].encode()) for i, s in sources.items()} != expected[
        "sources"
    ]:
        raise ValueError("audited source projection changed")
    async with generation.AsyncOpenAI(max_retries=0, timeout=600) as client:
        model = (await client.models.retrieve(generation.POLICY["model"])).model_dump()
        limit = (
            max(len(p.encode()) for p in prompts.values())
            + len(generation.encode(generation.TOOLS).encode())
            + 4096
        )
        if (
            model["context_window"] < limit + 32768
            or model["max_output_tokens"] < 32768
        ):
            raise ValueError("model capacity too small")
        dest.mkdir(parents=True, exist_ok=False)
        generation.save(
            dest / "protocol.json",
            {
                "model": model,
                "manifest_sha256": digest(inputs / "manifest.json"),
                "initial_token_bound": limit,
                "max_source_calls": 10,
                "wall_seconds": 600,
                "max_output_tokens": 32768,
                "truncation": "disabled",
                "retries": 0,
            },
        )
        for i in range(3):
            offset = i % len(arms)
            order = arms[offset:] + arms[:offset]
            await asyncio.gather(
                *(
                    generation.attempt(
                        client,
                        dest / str(i + 1) / arm,
                        prompts[arm],
                        expected["slots"],
                        sources,
                    )
                    for arm in order
                )
            )
    traces = sorted(dest.glob("*/*/trace.json"))
    if len(traces) != 3 * len(arms):
        raise ValueError("incomplete matrix")
    files = traces + list(dest.glob("*/*/candidate.json")) + [dest / "protocol.json"]
    generation.save(
        dest / "freeze.json",
        {
            "attempts": len(traces),
            "statuses": dict(
                Counter(json.loads(t.read_text())["status"] for t in traces)
            ),
            "sha256": {str(f.relative_to(dest)): digest(f) for f in files},
        },
    )


def blind(run_dir, dest, direct=False):
    arms = ("DC", "DR") if direct else ARMS
    freeze = json.loads((run_dir / "freeze.json").read_text())
    if freeze["attempts"] != 3 * len(arms) or any(
        digest(run_dir / f) != h for f, h in freeze["sha256"].items()
    ):
        raise ValueError("invalid freeze")
    mapping = {}
    candidates = []
    for i in range(1, 4):
        for arm in arms:
            name = secrets.token_hex(8)
            prefix = f"{i}/{arm}"
            trace = json.loads((run_dir / prefix / "trace.json").read_text())
            path = run_dir / prefix / "candidate.json"
            slots = (
                json.loads(path.read_text())["slots"]
                if trace["status"] == "generated"
                else None
            )
            candidates.append({"blind_id": name, "slots": slots})
            mapping[name] = prefix
    generation.save(dest / "private-map.json", mapping)
    generation.save(
        dest / "anonymous.json", sorted(candidates, key=lambda r: r["blind_id"])
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run", "blind"])
    parser.add_argument("--inputs", type=Path, default=HERE / "inputs")
    parser.add_argument("--run-dir", type=Path, default=HERE / "run")
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.inputs, args.direct)
    elif args.mode == "run":
        asyncio.run(run(args.inputs, args.run_dir, args.direct))
    else:
        blind(
            args.run_dir,
            HERE / "supplementary" if args.direct else args.run_dir.parent,
            args.direct,
        )
