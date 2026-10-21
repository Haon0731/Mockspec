import asyncio

import pytest

from mock_spec.program_tools import ProgramTools
from mock_spec.schemas import UnifiedTask
from test_schemas import valid_task_data


def test_exact_slice_coordinates():
    tools = ProgramTools(UnifiedTask.from_dict(valid_task_data()))
    result = asyncio.run(tools.inspect_source("mut:0", 11, 11))
    assert result["text"] == "    return dependency(value)"
    assert result["start_line"] == result["end_line"] == 11


@pytest.mark.parametrize("source_id,start,end", [
    ("../private/gold.json", 1, 1), ("src/target.py", 10, 11),
    ("mut:0", 1, 11), ("mut:0", 10, 12), ("mut:0", 11, 10),
    ("mut:0", True, 11), ("mut:0", 10, 11.0),
])
def test_reject_invisible_input(source_id, start, end):
    tools = ProgramTools(UnifiedTask.from_dict(valid_task_data()))
    result = asyncio.run(tools.inspect_source(source_id, start, end))
    assert result["status"] == "error"
    assert "text" not in result


def test_output_limit_explicitly_omits_evidence():
    tools = ProgramTools(UnifiedTask.from_dict(valid_task_data()), max_output_chars=512)
    result = tools.bounded({"status": "ok", "text": "x" * 600})
    assert result["status"] == "incomplete"
    assert "text" not in result


@pytest.mark.real_data
def test_both_reviewed_tasks_source_access():
    from test_real_data import REVIEWS, load_java, load_python, snapshot
    for review in REVIEWS:
        loader = load_java if review["language"] == "java" else load_python
        task = loader(snapshot(review), case_id=review["case_id"])
        tools = ProgramTools(task)
        for sid in tools.store.ids():
            source = tools.store.get(sid)
            result = asyncio.run(tools.inspect_source(sid, source.start_line, source.end_line))
            assert result["status"] == "ok"
            assert result["text"] == "\n".join(source.text.splitlines())
