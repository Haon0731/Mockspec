from dataclasses import dataclass

import pytest

from mock_spec.validation import InputValidationError
from source_selection import select_sources


@dataclass
class Source:
    logical_path: str
    text: str
    source_id: str = "unused"
    start_line: int = 1


class Case:
    def __init__(self, language, test_path, test_text, files):
        self.language = language
        self.masked_test = Source(test_path, test_text, "test")
        self.source_paths = tuple([test_path, *files])
        self.files = files
        self.reads = []

    def read_source(self, path):
        self.reads.append(path)
        value = self.files[path]
        if isinstance(value, Exception):
            raise value
        return Source(path, value)


def test_python_dynamic_path_and_one_import_hop_with_opaque_boundary():
    case = Case("python", "tests/test_feature.py", """
from test_utils import load_function_from_source_ast
root = "src/pkg"
target = load_function_from_source_ast(f"{root}/feature.py", "target")
""", {
        "src/pkg/feature.py": "from pkg.helper import support\ndef target():\n    return support()\n",
        "src/pkg/helper.py": "def support():\n    raise NotImplementedError()\n",
        "tests/test_other.py": "from pkg.secret import answer\n",
        "src/pkg/secret.py": "answer = 42\n",
    })

    result = select_sources(case)

    assert [item["logical_path"] for item in result["sources"]] == [
        "tests/test_feature.py", "src/pkg/feature.py", "src/pkg/helper.py"]
    assert "tests/test_other.py" not in case.reads
    assert "src/pkg/secret.py" not in case.reads
    assert result["opaque_methods"] == [{
        "source_id": "source:2", "name": "support", "start_line": 1, "end_line": 2}]


def test_pathlib_read_exec_loader_selects_visible_source_and_reports_function_origin():
    case = Case("python", "tests/test_encryption.py", """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "pkg" / "encryption.py"

def load_target():
    source_lines = MODULE_PATH.read_text(encoding="utf-8").splitlines()
    start_line = 3
    end_line = 5
    function_source = "\\n".join(source_lines[start_line - 1:end_line])
    namespace = {}
    exec(compile(function_source, str(MODULE_PATH), "exec"), namespace)
    return namespace["target"]

target_fn = load_target()
assert target_fn("value")
""", {
        "src/pkg/encryption.py": "VALUE = 1\n\ndef target(value):\n    return bool(value)\n",
        "src/pkg/other.py": "def target(value):\n    return False\n",
    })

    result = select_sources(case)

    assert [source["logical_path"] for source in result["sources"]] == [
        "tests/test_encryption.py", "src/pkg/encryption.py"]
    assert result["evidence"][0] == {
        "stage": "masked_test",
        "hint_kind": "python_visible_source_path",
        "hint": "src/pkg/encryption.py",
        "candidates": ["src/pkg/encryption.py"],
        "precision": "exact_visible_path",
    }
    binding = result["dynamic_loader_bindings"][0]
    assert binding["status"] == "resolved"
    assert binding["path"] == "src/pkg/encryption.py"
    assert binding["loader"] == "load_target"
    assert binding["namespace"] == "namespace"
    assert binding["function_key"] == "target"
    assert binding["function_definition"] == {
        "source_id": "source:1", "logical_path": "src/pkg/encryption.py",
        "name": "target", "start_line": 3, "end_line": 4,
    }
    assert binding["test_bindings"] == [{"name": "target_fn", "line": 15}]
    assert binding["call_sites"] == [{"name": "target_fn", "line": 16}]


def test_dynamic_loader_does_not_bind_wrong_file_or_dynamic_namespace_key():
    case = Case("python", "tests/test_encryption.py", """
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "pkg" / "encryption.py"

def load_target(key):
    namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), namespace)
    return namespace[key]

loaded = load_target("target")
assert loaded()
""", {
        "src/pkg/encryption.py": "def target():\n    return True\n",
        "src/pkg/other.py": "def target():\n    return False\n",
    })

    result = select_sources(case)

    assert [source["logical_path"] for source in result["sources"]] == [
        "tests/test_encryption.py", "src/pkg/encryption.py"]
    assert result["dynamic_loader_bindings"] == [{
        "status": "unknown", "loader": "load_target",
        "path": "src/pkg/encryption.py", "path_line": 4,
        "namespace": "namespace", "exec_line": 8,
        "return_line": 9, "reason": "dynamic_namespace_key",
    }]
    assert not any("other.py" in candidate for evidence in result["evidence"]
                   for candidate in evidence["candidates"])


@pytest.mark.parametrize(
    ("loader_body", "reason"),
    [
        ("""source_lines = MODULE_PATH.read_text().splitlines()
    start_line = 1
    end_line = 2
    function_source = "\\n".join(source_lines[start_line - 1:end_line])
    namespace = {}
    exec(compile(function_source, str(MODULE_PATH), "exec"), namespace)
    return namespace["target"]""", "function_definition_not_unique"),
        ("""namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), namespace)
    namespace["target"] = replacement
    return namespace["target"]""", "namespace_key_rebound"),
        ("""namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), namespace)
    namespace = other_namespace
    return namespace["target"]""", "namespace_key_rebound"),
        ("""namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), namespace)
    namespace[dynamic_key] = replacement
    return namespace["target"]""", "namespace_key_rebound"),
    ],
)
def test_dynamic_loader_preserves_unknown_for_out_of_slice_or_rebound_key(loader_body, reason):
    case = Case("python", "tests/test_encryption.py", f"""from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "pkg" / "encryption.py"
def load_target():
    {loader_body}
target_fn = load_target()
assert target_fn()
""", {"src/pkg/encryption.py": "VALUE = 1\n\ndef target():\n    return True\n"})

    result = select_sources(case)

    assert result["dynamic_loader_bindings"][0]["status"] == "unknown"
    assert result["dynamic_loader_bindings"][0]["reason"] == reason


def test_dynamic_loader_preserves_unknown_after_test_callable_rebinding():
    case = Case("python", "tests/test_encryption.py", """from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "pkg" / "encryption.py"
def load_target():
    namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), namespace)
    return namespace["target"]
target_fn = load_target()
target_fn = replacement
assert target_fn()
""", {"src/pkg/encryption.py": "def target():\n    return True\n"})

    result = select_sources(case)

    assert result["dynamic_loader_bindings"][0]["status"] == "unknown"
    assert result["dynamic_loader_bindings"][0]["reason"] == "test_callable_rebound"


def test_unsupported_visible_path_is_unknown_and_does_not_select_by_function_name():
    case = Case("python", "tests/test_encryption.py", """MODULE_PATH = locate_at_runtime()
def load_target():
    namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), namespace)
    return namespace["target"]
target_fn = load_target()
assert target_fn()
""", {"src/pkg/encryption.py": "def target():\n    return True\n"})

    result = select_sources(case)

    assert [source["logical_path"] for source in result["sources"]] == [
        "tests/test_encryption.py"]
    assert "unresolved_visible_source_path:4" in result["diagnostics"]
    assert result["dynamic_loader_bindings"][0]["status"] == "unknown"
    assert result["dynamic_loader_bindings"][0]["reason"] == "unresolved_visible_source_path"


def test_loader_read_does_not_prove_exec_text_origin():
    for code in ('"def target(): return False"', 'unrelated_text'):
        case = Case("python", "tests/test_loader.py", f'''from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "target.py"
def load_target():
    source = MODULE_PATH.read_text()
    namespace = {{}}
    exec(compile({code}, str(MODULE_PATH), "exec"), namespace)
    return namespace["target"]
target = load_target()
assert target()
''', {"src/target.py": "def target():\n    return True\n"})
        result = select_sources(case)
        assert result["dynamic_loader_bindings"][0]["status"] == "unknown"
        assert result["dynamic_loader_bindings"][0]["reason"] == "exec_source_provenance_not_supported"


def test_loader_reassigned_source_text_loses_provenance():
    case = Case("python", "tests/test_loader.py", '''from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "target.py"
def load_target():
    source = MODULE_PATH.read_text()
    source = obtain_other_source()
    namespace = {}
    exec(compile(source, str(MODULE_PATH), "exec"), namespace)
    return namespace["target"]
target = load_target()
assert target()
''', {"src/target.py": "def target():\n    return True\n"})
    result = select_sources(case)
    assert result["dynamic_loader_bindings"][0]["status"] == "unknown"


def test_java_import_and_same_package_type_are_candidates_without_mut_claim():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
import demo.Helper;
class FeatureTest { void testIt() { Feature feature = new Feature(); } }
""", {
        "src/main/java/demo/Feature.java": (
            "package demo; class Feature { String run() { "
            "throw new UnsupportedOperationException(); } }") ,
        "src/main/java/demo/Helper.java": "package demo; class Helper {}",
    })

    result = select_sources(case)

    paths = {item["logical_path"] for item in result["sources"]}
    assert paths == {"src/test/java/demo/FeatureTest.java",
                     "src/main/java/demo/Feature.java", "src/main/java/demo/Helper.java"}
    assert all("mut" not in item for item in result["evidence"])
    assert result["opaque_methods"][0]["name"] == "run"


def test_ambiguous_import_preserved_and_file_budget_stops_further_reads():
    case = Case("python", "tests/test_feature.py", "import shared\n", {
        "src/one/shared.py": "VALUE = 1\n",
        "src/two/shared.py": "VALUE = 2\n",
    })

    result = select_sources(case, max_files=2)

    assert len(result["sources"]) == 2
    assert result["evidence"][0]["precision"] == "candidate"
    assert result["evidence"][0]["candidates"] == [
        "src/one/shared.py", "src/two/shared.py"]
    assert case.reads == ["src/one/shared.py"]
    assert result["truncated"] is True
    assert "file_budget_exhausted" in result["diagnostics"]


def test_byte_budget_omits_whole_source_without_truncation():
    test_text = "import feature\n"
    product = "VALUE = 'complete-file'\n"
    case = Case("python", "tests/test_feature.py", test_text,
                {"src/feature.py": product})

    result = select_sources(case, max_bytes=len(test_text.encode()) + len(product.encode()) - 1)

    assert result["sources"] == [{"source_id": "test",
                                   "logical_path": "tests/test_feature.py",
                                   "text": test_text, "start_line": 1}]
    assert result["truncated"] is True
    assert result["diagnostics"] == ["byte_budget_omitted:src/feature.py"]
    assert result["attempted_files"] == 1
    assert result["read_bytes"] == len(test_text.encode()) + len(product.encode())


def test_byte_overshoot_stops_without_reading_more_ambiguous_candidates():
    test_text = "import pkg.shared\n"
    case = Case("python", "tests/test_feature.py", test_text, {
        "src/one/pkg/shared.py": "X = 'first file is too large'\n",
        "src/two/pkg/shared.py": "X = 'must not be read'\n",
        "src/three/pkg/shared.py": "X = 'also must not be read'\n",
    })

    result = select_sources(case, max_files=4, max_bytes=len(test_text.encode()) + 1)

    assert case.reads == ["src/one/pkg/shared.py"]
    assert result["attempted_files"] == 1
    assert result["truncated"] is True


def test_java_comments_and_literals_do_not_create_source_candidates():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
// Phantom ignored = new Phantom();
class FeatureTest { String text = "Ghost"; Real value = new Real(); }
""", {
        "src/main/java/demo/Phantom.java": "package demo; class Phantom {}",
        "src/main/java/demo/Ghost.java": "package demo; class Ghost {}",
        "src/main/java/demo/Real.java": "package demo; class Real {}",
    })

    result = select_sources(case)

    assert case.reads == ["src/main/java/demo/Real.java"]
    assert [item["logical_path"] for item in result["sources"]] == [
        "src/test/java/demo/FeatureTest.java", "src/main/java/demo/Real.java"]


def test_empty_candidate_is_recorded_without_blocking_valid_candidate():
    case = Case("python", "tests/test_feature.py", "import pkg.shared\n", {
        "src/a/pkg/shared.py": InputValidationError("frozen source is empty"),
        "src/b/pkg/shared.py": "VALUE = 'usable'\n",
    })

    result = select_sources(case)

    assert case.reads == ["src/a/pkg/shared.py", "src/b/pkg/shared.py"]
    assert result["attempted_files"] == 2
    assert [source["logical_path"] for source in result["sources"]] == [
        "tests/test_feature.py", "src/b/pkg/shared.py"]
    assert result["source_rejections"] == [{
        "logical_path": "src/a/pkg/shared.py", "reason": "frozen source is empty"}]
    assert result["diagnostics"] == ["empty_source_rejected:src/a/pkg/shared.py"]


def test_input_identity_failure_is_not_swallowed():
    case = Case("python", "tests/test_feature.py", "import pkg.feature\n", {
        "src/pkg/feature.py": InputValidationError(
            "frozen source content differs from manifest"),
    })

    with pytest.raises(InputValidationError, match="differs from manifest"):
        select_sources(case)
