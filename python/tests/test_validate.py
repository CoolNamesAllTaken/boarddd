"""validate_board against the shared cases (src/model's validateBoard runs the same file) and jsonschema."""

import jsonschema
import pytest

from boarddd import _codegen
from boarddd.validate import main, validate_board

from conftest import apply_patch, load

CASES = load("model/cases.json")
SCHEMA_RULES = ("duplicate", "is not in", "must equal its key", "must both be set", "is not a layer id", "but /stackup")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    doc = case["replace"] if "replace" in case else apply_patch(load("model/minimal.json"), case["patch"])
    assert validate_board(doc) == case["errors"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_jsonschema_agrees(case):
    """A reference validator accepts/rejects the same documents (model rules aside, which a schema can't express)."""
    doc = case["replace"] if "replace" in case else apply_patch(load("model/minimal.json"), case["patch"])
    schema_errors = [e for e in case["errors"] if not any(r in e for r in SCHEMA_RULES)]
    v = jsonschema.Draft202012Validator(_codegen.schema())
    assert bool(list(v.iter_errors(doc))) == bool(schema_errors)


def test_schema_is_valid_2020_12():
    jsonschema.Draft202012Validator.check_schema(_codegen.schema())


def test_golden_valid(golden):
    assert validate_board(golden) == []
    jsonschema.Draft202012Validator(_codegen.schema()).validate(golden)


def test_cli(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text('{"name": 1}')
    assert main([str(p)]) == 1
    assert "/name: expected string" in capsys.readouterr().out
