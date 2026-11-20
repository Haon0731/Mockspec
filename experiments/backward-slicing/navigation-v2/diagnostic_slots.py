"""Blind intact slots objects from failed replies for supplementary static review."""

import json
from pathlib import Path
import re
import secrets

from study import read, gen

HERE = Path(__file__).resolve().parent


def extract(raw, required):
    prefix = re.match(r'\s*\{\s*"slots"\s*:\s*', raw)
    if not prefix:
        return None
    try:
        slots, _ = json.JSONDecoder().raw_decode(raw[prefix.end() :])
    except ValueError:
        return None
    if not isinstance(slots, dict) or set(slots) != set(required):
        return None
    return slots if all(isinstance(v, str) for v in slots.values()) else None


def blind_failed(root):
    public = []
    mapping = {}
    inputs = read(root / "inputs" / "manifest.json")
    freeze = read(root / "run" / "freeze.json")
    for name, digest in freeze["sha256"].items():
        if not name.endswith("/trace.json"):
            continue
        path = root / "run" / name
        if gen.sha(path.read_bytes()) != digest:
            raise ValueError("trace digest mismatch")
        t = read(path)
        if t["status"] == "generated" or not t["turns"]:
            continue
        case = name.split("/")[0]
        slots = extract(t["turns"][-1]["text"], inputs[case]["slots"])
        if slots is not None:
            identity = secrets.token_hex(9)
            mapping[identity] = str(Path(name).parent)
            public.append(dict(blind_id=identity, case=case, slots=slots))
    gen.save(root / "diagnostic-private-map.json", mapping)
    gen.save(
        root / "diagnostic-anonymous.json",
        {"candidates": sorted(public, key=lambda r: r["blind_id"])},
    )


if __name__ == "__main__":
    blind_failed(HERE)
