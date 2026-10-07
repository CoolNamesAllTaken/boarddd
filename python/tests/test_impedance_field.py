"""boarddd.impedance.fieldsolver against fixtures/impedance/field-cases.json (the JS tests run the same file):
golden references, JS parity of generic sections, tier-1 models and stackup lines, and a sample of the sweep."""

import pytest
from test_impedance import rel

from boarddd import impedance as z

from conftest import load

pytest.importorskip("scipy")

CASES = load("impedance/field-cases.json")
TIER1 = load("impedance/cases.json")
SWEEP_OPTS = {"level": 2, "max_level": 3, "tol": 1}  # fixtures/impedance/make_field_sweep.mjs SWEEP_OPTS
PY = {"maxLevel": "max_level"}


def close(actual, expected, path="", tol=1e-9):
    """Numbers within `tol` relative (1e-12 absolute below 1e-12), everything else equal."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected, path
    elif isinstance(expected, int | float):
        assert isinstance(actual, int | float), path
        tiny = abs(expected) < 1e-12  # roundoff-level error estimates (homogeneous εeff): absolute
        ok = abs(actual - expected) < 1e-12 if tiny else actual == expected or rel(actual, expected) < tol
        assert ok, f"{path}: {actual} != {expected}"
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f"{path} length"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            close(a, e, f"{path}[{i}]", tol)
    else:
        assert sorted(actual) == sorted(expected), path
        for k in expected:
            close(actual[k], expected[k], f"{path}.{k}", tol)


def lean(r) -> dict:
    d = r.to_dict()
    d.pop("ms")
    return d


@pytest.mark.parametrize("g", CASES["golden"], ids=lambda g: f"{g['id']}-{g['model']}-{g['key']}")
def test_golden(g):
    r = z.field_calculate(g["model"], g["args"])
    err = 100 * rel(getattr(r, g["key"]), g["ref"])
    assert err <= g["tol_pct"], f"{getattr(r, g['key'])} vs {g['ref']} ({g['source']}): {err:.3f} %"


@pytest.mark.parametrize("c", CASES["sections"], ids=lambda c: c["id"])
def test_parity_with_js(c):
    opts = {PY.get(k, k): v for k, v in c["opts"].items()}
    if "section" in c:
        r = z.solve_cross_section(c["section"], **opts)
    else:
        r = z.field_calculate(c["model"], c["args"], **opts)
    close(lean(r), c["expect"], c["id"])


@pytest.mark.parametrize("c", CASES["stackup_lines"], ids=lambda c: f"{c['stackup']}-{c['layer']}")
def test_stackup_line_parity(c):
    line = z.line_from_stackup(TIER1["stackups"][c["stackup"]], c["layer"], **c["py_opts"])
    close(line.to_dict(), c["expect"], c["layer"])
    close(lean(z.solve_cross_section(line.section)), c["result"], c["layer"])


@pytest.mark.parametrize("c", CASES["stackup_targets"], ids=lambda c: f"{c['stackup']}-{c['target']['target']}")
def test_stackup_target_parity(c):
    rows = z.evaluate_target(TIER1["stackups"][c["stackup"]], c["target"], solver="field")
    got = []
    for r in rows:
        d = {k: getattr(r, k) for k in r.__dataclass_fields__}
        d["result"] = lean(r.result)
        got.append(d)
    expect = [dict(e) for e in c["expect"]]
    for g, e in zip(got, expect, strict=True):  # ~0 after synthesis: compare absolutely
        assert abs(g.pop("deviation_pct") - e.pop("deviation_pct")) < 1e-6
    close(got, expect, "rows", 1e-8)


def test_sweep_sample():
    for s in CASES["sweep_sample"]:
        model, args, ref, _ = s["row"]
        p = {**args, "fence": max(0.5, 2 * args["h"])} if model == "cpwg" else args
        r = z.field_calculate(model, p, **SWEEP_OPTS)
        for k, v in ref.items():
            assert abs(getattr(r, k) - v) <= 6e-5, (model, args, k, getattr(r, k), v)


def test_invalid_sections():
    with pytest.raises(ValueError, match="no signal"):
        z.solve_cross_section({"conductors": [{"y0": -1, "y1": 0, "net": "gnd"}]})
    with pytest.raises(ValueError, match="no ground"):
        z.solve_cross_section({"conductors": [{"x0": 0, "x1": 1, "y0": 1, "y1": 1.1, "net": "a"}]})
    with pytest.raises(ValueError, match="bounded"):
        z.solve_cross_section({"conductors": [{"y0": -1, "y1": 0, "net": "gnd"}, {"y0": 1, "y1": 1.1, "net": "a"}]})
    with pytest.raises(ValueError, match="no builder"):
        z.field_calculate("ipc2141_microstrip", {"w": 1, "h": 1, "er": 4})
    with pytest.raises(ValueError, match="unknown solver"):
        z.line_from_stackup(TIER1["stackups"]["synthetic"], "F.Cu", width=0.1, solver="magic")
