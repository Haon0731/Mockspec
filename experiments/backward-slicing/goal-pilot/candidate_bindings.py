"""Extract a narrow Mockito binding schema from Spoon GoalIR output."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
TOOLS = Path("/tmp/mockspec-step5b-tools")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict) and "cloudfoundry" in value:
        value = value["cloudfoundry"]
    if not isinstance(value, dict) or not {"mock_stub", "verify"} <= value.keys():
        raise ValueError(f"{path}: candidate requires mock_stub and verify")
    if not all(isinstance(value[key], str) for key in ("mock_stub", "verify")):
        raise ValueError(f"{path}: candidate regions must be strings")
    return {key: value[key] for key in ("mock_stub", "verify")}


def _test_frame(common: dict[str, object]) -> tuple[str, list[str]]:
    text = common["tasks"]["cloudfoundry"]["masked_test"]["text"]
    package = next(
        (line.strip() for line in text.splitlines() if line.strip().startswith("package ")),
        "package goalpilot;",
    )
    imports = [line.strip() for line in text.splitlines() if line.strip().startswith("import ")]
    return package, imports


def _wrapper(index: int, candidate: dict[str, str], package: str, imports: list[str]) -> str:
    body = "\n".join(
        "        " + line if line else "" for region in ("mock_stub", "verify")
        for line in candidate[region].splitlines()
    )
    return "\n".join([
        package, *imports, f"final class MockCandidate{index} {{",
        "    void candidate() {", body, "    }", "}", "",
    ])


def _run_ir(sources: list[dict[str, object]]) -> dict[str, object]:
    if not (TOOLS / "spoon.jar").is_file() or not (TOOLS / "jdk/bin/java").is_file():
        raise RuntimeError("pinned Spoon/JDK unavailable")
    with tempfile.TemporaryDirectory(prefix="mockspec-candidate-bindings-") as directory:
        subprocess.run(
            [str(TOOLS / "jdk/bin/javac"), "-Xlint", "-cp", str(TOOLS / "spoon.jar"),
             "-d", directory, str(HERE / "GoalIR.java")],
            check=True, capture_output=True, text=True, timeout=60,
        )
        process = subprocess.run(
            [str(TOOLS / "jdk/bin/java"), "-cp", f"{directory}:{TOOLS / 'spoon.jar'}", "GoalIR"],
            input=json.dumps({"sources": sources}), check=True, capture_output=True,
            text=True, timeout=60,
        )
    return json.loads(process.stdout)


def _term(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("missing expression")
    if value.get("kind") == "literal":
        return {"kind": "literal", "value": value.get("value")}
    if value.get("kind") == "call" and value.get("name") == "of":
        target = value.get("target")
        target_name = target.get("name", "") if isinstance(target, dict) else ""
        declaring = str(value.get("declaring_type", ""))
        if not (target_name.endswith("EndpointId") or declaring.endswith("EndpointId")):
            raise ValueError("only EndpointId.of calls are supported as arguments")
        arguments = value.get("arguments")
        if not isinstance(arguments, list) or len(arguments) != 1:
            raise ValueError("EndpointId.of requires one argument")
        return {
            "kind": "call",
            "owner": "org.springframework.boot.actuate.endpoint.EndpointId",
            "name": "of",
            "target": None,
            "arguments": [_term(arguments[0])],
        }
    raise ValueError(f"unsupported argument expression: {value.get('text', value.get('kind'))}")


def _var(value: object) -> str | None:
    return value.get("name") if isinstance(value, dict) and value.get("kind") == "var" else None


def _unsupported(statement: dict[str, object], reason: str) -> dict[str, object]:
    return {"text": statement.get("text", ""), "reason": reason,
            "location": statement.get("location", {})}


def _parse_method(method: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {
        "bindings": [], "verifications": [], "unsupported": [],
    }
    body = method.get("body")
    statements = body.get("statements", []) if isinstance(body, dict) else []
    mocks: set[str] = set()
    binding_keys: set[str] = set()
    for statement in statements:
        if not isinstance(statement, dict):
            continue
        if statement.get("kind") == "local":
            value = statement.get("value")
            arguments = value.get("arguments") if isinstance(value, dict) else None
            if (isinstance(value, dict) and value.get("kind") == "call"
                    and value.get("name") == "mock" and isinstance(arguments, list)
                    and len(arguments) == 1 and isinstance(arguments[0], dict)
                    and arguments[0].get("kind") == "class_literal"):
                mocks.add(str(statement["name"]))
                continue
            output["unsupported"].append(_unsupported(statement, "unsupported local declaration"))
            continue
        if statement.get("kind") != "expr" or not isinstance(statement.get("value"), dict):
            output["unsupported"].append(_unsupported(statement, "unsupported statement"))
            continue
        call = statement["value"]
        if call.get("kind") != "call":
            output["unsupported"].append(_unsupported(statement, "expression is not a call"))
            continue
        if call.get("name") == "thenReturn":
            try:
                binding = _binding(call, mocks, statement)
                key = json.dumps(
                    [binding["receiver"], binding["name"], binding["arguments"]],
                    sort_keys=True, separators=(",", ":"),
                )
                if key in binding_keys:
                    raise ValueError("duplicate stub key has unsupported Mockito precedence")
                binding_keys.add(key)
                output["bindings"].append(binding)
            except ValueError as error:
                output["unsupported"].append(_unsupported(statement, str(error)))
            continue
        target = call.get("target")
        if isinstance(target, dict) and target.get("kind") == "call" and target.get("name") == "verify":
            try:
                output["verifications"].append(_verification(call, mocks, statement))
            except ValueError as error:
                output["unsupported"].append(_unsupported(statement, str(error)))
            continue
        output["unsupported"].append(_unsupported(statement, "call is outside the supported Mockito subset"))
    return output


def _binding(call: dict[str, object], mocks: set[str], statement: dict[str, object]) -> dict[str, object]:
    when = call.get("target")
    if not isinstance(when, dict) or when.get("kind") != "call" or when.get("name") != "when":
        raise ValueError("thenReturn target is not when(...)")
    when_arguments = when.get("arguments")
    if not isinstance(when_arguments, list) or len(when_arguments) != 1:
        raise ValueError("when requires one mocked invocation")
    invocation = when_arguments[0]
    if not isinstance(invocation, dict) or invocation.get("kind") != "call":
        raise ValueError("when argument is not an invocation")
    receiver = _var(invocation.get("target"))
    if receiver not in mocks:
        raise ValueError("stub receiver is not a declared mock")
    returns = call.get("arguments")
    if not isinstance(returns, list) or not returns:
        raise ValueError("thenReturn requires at least one response")
    try:
        response_values = [_term(value)["value"] for value in returns]
        arguments = [_term(value) for value in invocation.get("arguments", [])]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(str(error)) from error
    return {"receiver": receiver, "name": invocation.get("name"), "arguments": arguments,
            "returns": response_values, "location": statement.get("location", {})}


def _verification(call: dict[str, object], mocks: set[str], statement: dict[str, object]) -> dict[str, object]:
    verify = call["target"]
    verify_arguments = verify.get("arguments")
    if not isinstance(verify_arguments, list) or len(verify_arguments) not in (1, 2):
        raise ValueError("verify requires a mock and optional times(n)")
    receiver = _var(verify_arguments[0])
    if receiver not in mocks:
        raise ValueError("verification receiver is not a declared mock")
    count = 1
    if len(verify_arguments) == 2:
        times = verify_arguments[1]
        if not isinstance(times, dict) or times.get("kind") != "call" or times.get("name") != "times":
            raise ValueError("only times(n) verification mode is supported")
        arguments = times.get("arguments")
        if (not isinstance(arguments, list) or len(arguments) != 1
                or not isinstance(arguments[0], dict) or arguments[0].get("kind") != "literal"
                or not isinstance(arguments[0].get("value"), int)):
            raise ValueError("times requires one integer literal")
        count = arguments[0]["value"]
    try:
        arguments = [_term(value) for value in call.get("arguments", [])]
    except (TypeError, ValueError) as error:
        raise ValueError(str(error)) from error
    return {"receiver": receiver, "name": call.get("name"), "arguments": arguments,
            "count": count, "location": statement.get("location", {})}


def extract(common_path: Path, candidate_paths: list[Path]) -> dict[str, object]:
    common = json.loads(common_path.read_text(encoding="utf-8"))
    package, imports = _test_frame(common)
    candidates = [_candidate(path) for path in candidate_paths]
    sources = [
        {"source_id": f"candidate:{index}", "logical_path": f"MockCandidate{index}.java",
         "text": _wrapper(index, candidate, package, imports)}
        for index, candidate in enumerate(candidates)
    ]
    ir = _run_ir(sources)
    results = []
    for index, path in enumerate(candidate_paths):
        owner = next(
            item for item in ir["types"] if item["name"].endswith(f"MockCandidate{index}")
        )
        method = next(item for item in owner["methods"] if item["name"] == "candidate")
        parsed = _parse_method(method)
        results.append({"candidate": str(path), "sha256": _digest(path.read_bytes()), **parsed})
    return {"backend": ir["backend"], "candidates": results,
            "limitations": [
                "Only mock declarations, when(...).thenReturn(...), verify(...), and times(n) are interpreted.",
                "Candidate code is parsed by Spoon and is never compiled or executed.",
                "Unsupported statements remain explicit and are not treated as absent behavior.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--common", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = extract(args.common, args.candidates)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"candidates": len(result["candidates"]),
                      "unsupported": sum(len(item["unsupported"]) for item in result["candidates"])}))


if __name__ == "__main__":
    main()
