"""The dataclasses, the generated files and (de)serialisation."""

import json
import tomllib

import boarddd
from boarddd import _codegen, model
from boarddd.validate import validate_board

from conftest import FIXTURES, REPO, load


def test_generated_files_are_up_to_date():
    # CI also runs `python -m boarddd.model --check`; this names the stale file in pytest output
    for rel, text in _codegen.outputs().items():
        assert (REPO / rel).read_text("utf-8") == text, f"{rel} is stale: run python -m boarddd.model --write"


def test_check_cli(capsys):
    assert model.main(["--check", "--root", str(REPO)]) == 0
    assert model.main(["--bogus"]) == 2


def test_schema_id_and_root():
    s = _codegen.schema()
    assert s["$id"] == model.SCHEMA_ID == "boarddd/board@1"
    assert s["properties"]["schema"]["const"] == model.SCHEMA_ID
    assert {"schema", "name", "source", "units", "frame"} <= set(s["required"])


def test_schema_js_is_the_same_object():
    js = (REPO / "src/model/schema.js").read_text("utf-8")
    body = js[js.index("export default ") + len("export default ") :].rstrip().rstrip(";")
    assert json.loads(body) == json.loads((REPO / "schema/board.schema.json").read_text("utf-8"))


def test_every_dataclass_has_a_definition_and_an_interface():
    s = _codegen.schema()
    dts = (REPO / "src/model/board.d.ts").read_text("utf-8")
    for cls in _codegen._classes():
        assert cls is model.Board or cls.__name__ in s["$defs"]
        assert f"export interface {cls.__name__} {{" in dts


def test_one_version_for_js_and_python():
    pkg = json.loads((REPO / "package.json").read_text("utf-8"))
    py = tomllib.loads((REPO / "python/pyproject.toml").read_text("utf-8"))
    assert pkg["version"] == py["project"]["version"] == boarddd.__version__


def test_defaults_make_a_valid_board():
    b = model.Board(name="empty", source=model.Source(kind="other"))
    assert validate_board(b.to_dict()) == []
    assert b.to_dict()["units"] == "mm" and b.to_dict()["frame"] == "board"


def test_round_trip_golden(golden):
    text = (FIXTURES / "royalblue54L_feather/board.json").read_text("utf-8")
    b = model.Board.from_json(text)
    assert isinstance(b.components[0], model.Component)
    assert isinstance(b.footprints["Capacitor_SMD:C_0402_1005Metric"].pads[0], model.Pad)
    assert b.to_json() == text


def test_round_trip_minimal_fills_defaults():
    data = load("model/minimal.json")
    b = model.Board.from_dict(data)
    out = b.to_dict()
    assert validate_board(out) == []
    assert out["components"][0]["populate"] is True and out["components"][1]["populate"] is False
    assert out["drills"][0]["x2"] is None and out["drills"][1]["x2"] == 7.0
    assert model.Board.from_dict(out) == b


def test_from_dict_is_strict():
    data = load("model/minimal.json")
    data["components"][0]["colour"] = "red"
    try:
        model.Board.from_dict(data)
    except TypeError as e:
        assert "colour" in str(e)
    else:
        raise AssertionError("unknown field accepted")
