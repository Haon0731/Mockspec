import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

FILE = Path(__file__).with_name("generate.py")
SPEC = importlib.util.spec_from_file_location("issue35_generate", FILE)
generation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generation)


def test_source_access_uses_exact_approved_intervals():
    sources = {
        "s": {
            "source_id": "s",
            "logical_path": "foo.py",
            "start_line": 8,
            "end_line": 9,
            "text": "first\nsecond\n",
        }
    }

    def read(locator, start, end):
        args = {"locator": locator, "start_line": start, "end_line": end}
        return generation.source_call(
            SimpleNamespace(name="read_source", arguments=json.dumps(args)), sources
        )

    assert json.loads(read("foo.py", 9, 9))["text"] == "second"
    assert read("s", 7, 8).startswith("Invalid")
    assert read("../foo.py", 8, 9).startswith("Invalid")


def test_invalid_candidate_is_counted_once_and_raw_output_retained(tmp_path):
    class Responses:
        calls = 0

        async def create(self, **options):
            self.calls += 1
            assert options["truncation"] == "disabled"
            return SimpleNamespace(
                output=[],
                output_text='{"slots":{},"rationale":[]}',
                status="completed",
                id="fixture",
                model="fixture",
                usage=None,
            )

    responses = Responses()
    dest = tmp_path / "attempt"
    asyncio.run(
        generation.attempt(
            SimpleNamespace(responses=responses), dest, "prompt", ["mock_stub_001"], {}
        )
    )
    trace = json.loads((dest / "trace.json").read_text())
    assert responses.calls == 1 and trace["status"] == "failed"
    assert json.loads(trace["turns"][0]["text"])["slots"] == {}
    assert not (dest / "candidate.json").exists()


def test_attempt_accepts_an_injected_candidate_transport(tmp_path):
    class Responses:
        async def create(self, **options):
            return SimpleNamespace(
                output=[],
                output_text="verbatim response",
                status="completed",
                id="fixture",
                model="fixture",
                usage=None,
            )

    seen = []

    def parser(raw, slots):
        seen.append((raw, slots))
        return {"slots": {slots[0]: "  pass"}, "rationale": []}

    destination = tmp_path / "attempt"
    asyncio.run(
        generation.attempt(
            SimpleNamespace(responses=Responses()),
            destination,
            "prompt",
            ["slot"],
            {},
            candidate_parser=parser,
        )
    )

    assert seen == [("verbatim response", ["slot"])]
    assert json.loads((destination / "candidate.json").read_text()) == {
        "slots": {"slot": "  pass"},
        "rationale": [],
    }
