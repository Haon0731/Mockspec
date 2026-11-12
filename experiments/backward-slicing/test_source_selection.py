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


def test_absolute_single_module_import_does_not_match_nested_project_suffix():
    case = Case("python", "tests/test_feature.py", """
from types import SimpleNamespace
from crawl4ai.async_configs import AsyncConfig

def test_feature():
    assert AsyncConfig()
""", {
        "src/crawl4ai/async_configs.py": "class AsyncConfig:\n    pass\n",
        "src/crawl4ai/types.py": "class SimpleNamespace:\n    pass\n",
    })

    result = select_sources(case)

    assert [item["logical_path"] for item in result["sources"]] == [
        "tests/test_feature.py", "src/crawl4ai/async_configs.py"]
    assert "src/crawl4ai/types.py" not in case.reads


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


def test_java_selection_excludes_setup_prefixed_product_copies():
    test_path = "src/test/java/demo/FeatureTest.java"
    feature_path = "src/main/java/demo/Feature.java"
    helper_path = "src/main/java/demo/Helper.java"
    setup_root = "FeatureTest_setUp_deadbeef/src/main/java/demo/"
    case = Case("java", test_path, """
package demo;
import demo.Helper;
class FeatureTest { void testIt() { Feature feature = new Feature(); } }
""", {
        setup_root + "Feature.java": "package demo; class Feature {}",
        feature_path: "package demo; class Feature {}",
        setup_root + "Helper.java": "package demo; class Helper {}",
        helper_path: "package demo; class Helper {}",
    })

    result = select_sources(case)

    assert [item["logical_path"] for item in result["sources"]] == [
        test_path, helper_path, feature_path]
    assert case.reads == [helper_path, feature_path]
    assert all(
        not candidate.startswith("FeatureTest_setUp_")
        for item in result["evidence"]
        for candidate in item["candidates"]
    )


def test_java_selected_method_dependency_owners_precede_unrelated_imports():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
import demo.UnrelatedOne;
import demo.UnrelatedTwo;
class FeatureTest {
  void helper() { new HelperOnly(); }
  void testIt() {
    Executor executor = factory.create(Dependency.options());
    executor.run(missingInput);
    assertTrue(missingInput.done());
  }
}
""", {
        "src/main/java/demo/UnrelatedOne.java": "package demo; class UnrelatedOne {}",
        "src/main/java/demo/UnrelatedTwo.java": "package demo; class UnrelatedTwo {}",
        "src/main/java/demo/HelperOnly.java": "package demo; class HelperOnly {}",
        "src/main/java/demo/Executor.java": "package demo; class Executor {}",
        "src/main/java/demo/Dependency.java": "package demo; class Dependency {}",
    })
    case.target_selector = "demo.FeatureTest#testIt"

    result = select_sources(case, max_files=3)

    assert case.reads == ["src/main/java/demo/Dependency.java",
                          "src/main/java/demo/Executor.java"]
    assert [item["selection_priority"] for item in result["evidence"][:2]] == [
        "selected_method_lexical_priority_candidate",
        "selected_method_lexical_priority_candidate",
    ]
    assert "file_budget_exhausted" in result["diagnostics"]


def test_java_same_named_member_is_not_treated_as_a_type_owner():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
import demo.Unrelated;
class FeatureTest {
  void testIt() {
    Priority value = new Priority();
    factory.Decoy();
    assertNotNull(value);
  }
}
""", {
        "src/main/java/demo/Unrelated.java": "package demo; class Unrelated {}",
        "src/main/java/demo/Priority.java": "package demo; class Priority {}",
        "src/main/java/demo/Decoy.java": "package demo; class Decoy {}",
    })
    case.target_selector = "demo.FeatureTest#testIt"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/main/java/demo/Priority.java"]
    assert all(item["hint"] != "demo.Decoy" for item in result["evidence"])


def test_java_unrelated_constructor_before_chain_does_not_gain_priority():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
class FeatureTest {
  void testIt() {
    Irrelevant noise = new Irrelevant();
    Relevant value = new Relevant();
    assertNotNull(value);
  }
}
""", {
        "src/main/java/demo/Irrelevant.java": "package demo; class Irrelevant {}",
        "src/main/java/demo/Relevant.java": "package demo; class Relevant {}",
    })
    case.target_selector = "demo.FeatureTest#testIt"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/main/java/demo/Relevant.java"]
    assert result["evidence"][0]["selection_priority"] == (
        "selected_method_lexical_priority_candidate")


def test_java_shared_static_method_name_does_not_link_unrelated_owner():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
class FeatureTest {
  void testIt() {
    Unrelated.create();
    Relevant value = Relevant.create();
    assertNotNull(value);
  }
}
""", {
        "src/main/java/demo/Unrelated.java": "package demo; class Unrelated {}",
        "src/main/java/demo/Relevant.java": "package demo; class Relevant {}",
    })
    case.target_selector = "demo.FeatureTest#testIt"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/main/java/demo/Relevant.java"]
    unrelated = next(item for item in result["evidence"]
                     if item["hint"] == "demo.Unrelated")
    assert "selection_priority" not in unrelated


def test_java_ambiguous_receiver_declarations_do_not_choose_last_type():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
import demo.Fallback;
class FeatureTest {
  void testIt() {
    if (left) {
      First worker = factory.first();
      assertTrue(worker.ready());
    }
    if (right) {
      Second worker = factory.second();
      assertTrue(worker.ready());
    }
  }
}
""", {
        "src/main/java/demo/Fallback.java": "package demo; class Fallback {}",
        "src/main/java/demo/First.java": "package demo; class First {}",
        "src/main/java/demo/Second.java": "package demo; class Second {}",
    })
    case.target_selector = "demo.FeatureTest#testIt"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/main/java/demo/Fallback.java"]
    assert all("selection_priority" not in item for item in result["evidence"])


def test_java_direct_static_observation_owner_keeps_priority():
    case = Case("java", "src/test/java/demo/FeatureTest.java", """
package demo;
import demo.Unrelated;
class FeatureTest {
  void testIt() {
    assertTrue(Product.ready());
  }
}
""", {
        "src/main/java/demo/Unrelated.java": "package demo; class Unrelated {}",
        "src/main/java/demo/Product.java": "package demo; class Product {}",
    })
    case.target_selector = "demo.FeatureTest#testIt"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/main/java/demo/Product.java"]
    assert result["evidence"][0]["selection_priority"] == (
        "selected_method_lexical_priority_candidate")


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


def test_target_used_import_precedes_unrelated_import_under_file_budget():
    test_text = """\
import pkg.unrelated
from pkg.feature import feature

def test_feature():
    assert feature("value")
"""
    case = Case("python", "tests/test_feature.py", test_text, {
        "src/pkg/unrelated.py": "UNRELATED = True\n",
        "src/pkg/feature.py": "def feature(value):\n    return value\n",
    })
    case.target_selector = "tests/test_feature.py::test_feature"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/pkg/feature.py"]
    assert [item["logical_path"] for item in result["sources"]] == [
        "tests/test_feature.py", "src/pkg/feature.py"]
    assert result["evidence"][0]["hint"] == "pkg.feature"
    assert result["selected_bytes"] <= 200_000
    assert "file_budget_exhausted" in result["diagnostics"]


def test_rebound_import_is_not_prioritized_over_stable_import():
    test_text = """\
from pkg.stable import stable
from pkg.rebound import rebound

def test_feature():
    rebound = lambda: False
    assert stable()
    assert rebound()
"""
    case = Case("python", "tests/test_feature.py", test_text, {
        "src/pkg/stable.py": "def stable():\n    return True\n",
        "src/pkg/rebound.py": "def rebound():\n    return True\n",
    })
    case.target_selector = "tests/test_feature.py::test_feature"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/pkg/stable.py"]
    assert result["evidence"][0]["hint"] == "pkg.stable"


def test_unrelated_dynamic_path_does_not_preempt_target_used_import():
    test_text = """\
from pathlib import Path
from pkg.feature import feature
OTHER = Path("src/pkg/unrelated.py")

def unused_helper():
    return OTHER.read_text()

def test_feature():
    assert feature()
"""
    case = Case("python", "tests/test_feature.py", test_text, {
        "src/pkg/unrelated.py": "UNRELATED = True\n",
        "src/pkg/feature.py": "def feature():\n    return True\n",
    })
    case.target_selector = "tests/test_feature.py::test_feature"

    result = select_sources(case, max_files=2)

    assert case.reads == ["src/pkg/feature.py"]
    assert result["evidence"][0]["hint"] == "pkg.feature"


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


def test_observation_assignment_chain_precedes_unrelated_test_call():
    case = Case('python', 'tests/test_feature.py', '''from pkg.setup import setup
from pkg.feature import feature

def test_feature():
    setup()
    intermediate = feature()
    result = intermediate
    assert result
''', {'src/pkg/setup.py': 'def setup(): pass\n',
      'src/pkg/feature.py': 'def feature(): return 1\n'})
    case.target_selector = 'tests/test_feature.py::test_feature'
    result = select_sources(case, max_files=2)
    assert case.reads == ['src/pkg/feature.py']
    assert result['evidence'][0]['hint'] == 'pkg.feature'


@pytest.mark.parametrize('cap,admitted', [(512_000, True), (210_000, False), (0, False)])
def test_large_focal_file_has_separate_finite_allowance(cap, admitted):
    test = '''from pathlib import Path
SOURCE = Path("src/pkg/feature.py")
def load_target():
    return SOURCE.read_text()
def test_feature():
    result = load_target()
    assert result
'''
    product = '# ' + 'x' * 225_000 + '\n'
    case = Case('python', 'tests/test_feature.py', test, {
        'src/pkg/feature.py': product,
        'src/pkg/unrelated.py': 'UNRELATED = True\n'})
    case.target_selector = 'tests/test_feature.py::test_feature'
    result = select_sources(case, focal_file_bytes=cap)
    assert result['budgets'] == {'max_files': 8, 'max_bytes': 200_000, 'focal_file_bytes': cap, 'loader_support_file_bytes': 32_000}
    assert result['truncated']
    assert case.reads == ['src/pkg/feature.py']
    assert len(result['sources']) == (2 if admitted else 1)
    if admitted:
        assert result['sources'][1]['text'] == product
        assert result['focal_file_allowance']['bytes'] == len(product.encode())
        assert result['focal_file_allowance']['limit_bytes'] == cap
        assert result['selected_bytes'] > result['budgets']['max_bytes']
    else:
        assert result['focal_file_allowance'] is None
        assert 'focal_file_cap_exceeded:src/pkg/feature.py' in result['diagnostics']


def test_unrelated_exact_path_does_not_get_focal_allowance():
    case = Case('python', 'tests/test_feature.py', '''from pathlib import Path
SOURCE = Path("src/pkg/unrelated.py")
def unused():
    return SOURCE.read_text()
def test_feature():
    assert True
''', {'src/pkg/unrelated.py': '# ' + 'x' * 225_000 + '\n'})
    case.target_selector = 'tests/test_feature.py::test_feature'
    result = select_sources(case)
    assert len(result['sources']) == 1
    assert result['focal_file_allowance'] is None
    assert 'byte_budget_omitted:src/pkg/unrelated.py' in result['diagnostics']


def test_relative_loader_helper_is_bounded_support_not_product_source():
    case = Case('python', 'tests/test_feature.py', '''from .helpers import load_function_from_source_ast
SRC = "src/pkg/feature.py"
def test_feature():
    target = load_function_from_source_ast(SRC, "target")
    assert target()
''', {'tests/helpers.py': 'def load_function_from_source_ast(*args): return None\n',
      'tests/test_other.py': 'def test_hidden(): assert False\n',
      'src/pkg/feature.py': 'def target(): return True\n'})
    case.target_selector = 'tests/test_feature.py::test_feature'
    result = select_sources(case, max_files=3)
    assert case.reads == ['tests/helpers.py', 'src/pkg/feature.py']
    assert result['sources'][1]['selection_role'] == 'loader_support'
    assert result['evidence'][0]['precision'] == 'support_source_only_not_product_binding'
    assert not result['dynamic_loader_bindings']


def test_loader_helper_cap_does_not_admit_large_support_source():
    case = Case('python', 'tests/test_feature.py', '''from .helpers import load_function_from_source_ast
def test_feature():
    assert load_function_from_source_ast("src/feature.py", "feature")
''', {'tests/helpers.py': '# ' + 'x' * 32_000,
      'src/feature.py': 'def feature(): return True\n'})
    case.target_selector = 'tests/test_feature.py::test_feature'
    result = select_sources(case)
    assert all(s['logical_path'] != 'tests/helpers.py' for s in result['sources'])
    assert 'loader_support_byte_budget_omitted:tests/helpers.py' in result['diagnostics']


@pytest.mark.parametrize("via_helper", [False, True])
def test_unrelated_target_read_does_not_preempt_observation_import(via_helper):
    setup = "unrelated()" if via_helper else "SOURCE.read_text()"
    case = Case("python", "tests/test_feature.py", f'''from pathlib import Path
from pkg.feature import feature
SOURCE = Path("src/pkg/unrelated.py")
def unrelated():
    return SOURCE.read_text()
def test_feature():
    {setup}
    result = feature()
    assert result
''', {"src/pkg/unrelated.py": "# " + "x" * 225_000 + "\n",
       "src/pkg/feature.py": "def feature(): return True\n"})
    case.target_selector = "tests/test_feature.py::test_feature"
    result = select_sources(case, max_files=2)
    assert case.reads == ["src/pkg/feature.py"]
    assert result["focal_file_allowance"] is None


def test_java_opaque_detector_excludes_control_flow_and_comments():
    from source_selection import _java_opaque

    text = '''class Example {
  void ordinary(boolean flag) {
    if (flag) { throw new UnsupportedOperationException("conditional"); }
    while (flag) { throw new UnsupportedOperationException(); }
  }
  // public void commented() { throw new UnsupportedOperationException(); }
  public void placeholder() { throw new UnsupportedOperationException("stub"); }
}
'''
    found = _java_opaque("source:1", text)
    assert [item["name"] for item in found] == ["placeholder"]
    assert found[0]["end_line"] == 7


def test_java_opaque_conditional_scan_is_bounded():
    from source_selection import _java_opaque
    import time

    text = "class Layers {\n" + ("  // documentation\n" * 500) + '''  void check(boolean flag) {
    if (flag) { throw new UnsupportedOperationException("ordinary branch"); }
  }
  public void unavailable() { throw new UnsupportedOperationException(); }
}
'''
    started = time.monotonic()
    assert [item["name"] for item in _java_opaque("source:1", text)] == ["unavailable"]
    assert time.monotonic() - started < 1
