"""boarddd.impedance against fixtures/impedance/cases.json (the JS tests run the same file): golden references,
JS parity, synthesis, validity flags; plus live cross-checks against scipy and scikit-rf when installed."""

import math

import pytest

from boarddd import impedance as z

from conftest import load

CASES = load("impedance/cases.json")


def rel(a, b):
    return abs(a - b) / abs(b)


def close(actual, expected, path="", tol=1e-9):
    """Numbers within `tol` relative, everything else equal (the JS results in cases.json)."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected, path
    elif isinstance(expected, int | float):
        assert isinstance(actual, int | float), path
        assert actual == expected or rel(actual, expected) < tol, f"{path}: {actual} != {expected}"
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f"{path} length"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            close(a, e, f"{path}[{i}]", tol)
    else:
        assert sorted(actual) == sorted(expected), path
        for k in expected:
            close(actual[k], expected[k], f"{path}.{k}", tol)


@pytest.mark.parametrize("g", CASES["golden"], ids=lambda g: f"{g['id']}-{g['model']}-{g['key']}")
def test_golden(g):
    r = z.calculate(g["model"], g["args"])
    err = 100 * rel(getattr(r, g["key"]), g["ref"])
    assert err <= g["tol_pct"], f"{getattr(r, g['key'])} vs {g['ref']} ({g['source']}): {err:.3f} %"
    assert r.flags == []


@pytest.mark.parametrize("c", CASES["parity"], ids=lambda c: c["id"])
def test_parity_with_js(c):
    close(z.calculate(c["model"], c["args"]).to_dict(), c["expect"], c["id"])


@pytest.mark.parametrize("c", CASES["synthesis"], ids=lambda c: f"{c['model']}-{c['target']}")
def test_synthesis_parity(c):
    opts = dict(c["opts"])
    r = z.synthesize(c["model"], c["params"], c["target"], **opts)
    close(r.value, c["expect"]["value"], "value", 1e-8)
    close(r.result.to_dict(), c["expect"]["result"], "result", 1e-8)
    key = opts.get("key") or ("Zdiff" if isinstance(r.result, z.CoupledResult) else "Z0")
    assert rel(getattr(r.result, key), c["target"]) < 1e-6


def test_synthesis_out_of_reach():
    with pytest.raises(ValueError, match="not reachable"):
        z.synthesize("microstrip", {"h": 1, "er": 4}, 0.5)


def test_flags():
    def codes(r):
        return [f.code for f in r.flags]

    assert codes(z.microstrip(w=0.36, h=0.2104, t=0.035, er=4.4)) == []
    assert codes(z.microstrip(w=200, h=1, er=4)) == ["w/h"]
    assert codes(z.stripline(w=0.1, h1=0.05, h2=0.4, t=0.035, er=4)) == ["h_max/h_min", "h_min/t"]
    assert codes(z.cpwg(w=0.15, gap=0.4, h=0.1, t=0.035, er=4.4)) == ["h/gap"]
    assert codes(z.coupled_stripline(w=0.1, s=0.02, h1=0.2, t=0.035, er=4)) == ["s/(b-t)"]
    f = z.microstrip(w=200, h=1, er=4).flags[0]
    assert f == z.Flag("w/h", 200, 0.01, 100, "w/h = 200 is outside 0.01..100 (Hammerstad-Jensen)")


def test_ipc_is_a_comparison():
    r = z.ipc2141_microstrip(w=3.3, h=0.794, t=0.035, er=4.2)
    assert r.comparison is True and rel(r.Z0, 21.0765) < 1e-5
    assert "comparison" not in z.microstrip(w=1, h=1, er=4).to_dict()


def test_bad_inputs():
    with pytest.raises(ValueError):
        z.microstrip(w=-1, h=1, er=4)
    with pytest.raises(ValueError):
        z.microstrip(w=1, h=1, er=0.5)
    with pytest.raises(ValueError, match="unknown model"):
        z.calculate("nope", {})


def test_monotonic_in_width():
    sweeps = {
        "microstrip": {"h": 0.2, "t": 0.035, "er": 4.4},
        "stripline": {"h1": 0.1, "h2": 0.4, "t": 0.035, "er": 4},
        "cpwg": {"gap": 0.15, "h": 0.3, "t": 0.035, "er": 4.4},
        "coupled_microstrip": {"s": 0.15, "h": 0.2, "t": 0.035, "er": 4.4},
        "coupled_stripline": {"s": 0.15, "h1": 0.1, "h2": 0.25, "t": 0.035, "er": 4},
    }
    for model, p in sweeps.items():
        prev, w = math.inf, 0.005
        while w < 20:
            r = z.calculate(model, {**p, "w": w})
            v = r.Zdiff if isinstance(r, z.CoupledResult) else r.Z0
            assert v < prev, (model, w)
            prev, w = v, w * 1.05


def test_cohn_exact_against_scipy():
    special = pytest.importorskip("scipy.special")
    for w, b, er in [(0.15, 0.4, 4.1), (0.05, 0.4, 4.0), (2.0, 0.4, 3.5)]:
        k = 1 / math.cosh(math.pi * w / (2 * b))
        ref = z.ETA0 / (4 * math.sqrt(er)) * special.ellipk(k * k) / special.ellipk(1 - k * k)
        assert rel(z.stripline(w=w, h1=b / 2, er=er).Z0, ref) < 1e-11


def test_against_scikit_rf():
    pytest.importorskip("skrf")
    import numpy as np
    from skrf import Frequency
    from skrf.media import CPW, MLine

    f = Frequency(1, 1, 1, "MHz")
    for w, h, t, er in [(1.5, 0.794, 0.035, 4.2), (0.2, 0.1, 0.018, 3.0)]:
        m = MLine(
            frequency=f,
            w=w * 1e-3,
            h=h * 1e-3,
            t=t * 1e-3,
            ep_r=er,
            disp="none",
            diel="frequencyinvariant",
            rho=1e-30,
            tand=0,
            rough=0,
            model="hammerstadjensen",
        )
        assert rel(z.microstrip(w=w, h=h, t=t, er=er).Z0, float(np.real(m.z0[0]))) < 1e-4
    c = CPW(
        frequency=f,
        w=0.3e-3,
        s=0.15e-3,
        h=0.3e-3,
        ep_r=4.5,
        rho=1e-30,
        tand=0,
        has_metal_backside=True,
        diel="frequencyinvariant",
    )
    assert rel(z.cpwg(w=0.3, gap=0.15, h=0.3, er=4.5).Z0, float(np.real(c.z0[0]))) < 1e-4


def test_accuracy_envelope_against_the_field_solver_sweep():
    errs: dict[str, list[float]] = {}
    for model, args, ref in load("impedance/qs-sweep.json")["rows"]:
        r = z.calculate(model, args)
        if r.flags or args["t"] < 0.018:  # real copper, inside the validity range
            continue
        for k, v in ref.items():
            errs.setdefault(model, []).append(100 * (getattr(r, k) - v) / v)
    assert len(errs) == 6
    for model, e in errs.items():
        rms = math.sqrt(sum(x * x for x in e) / len(e))
        assert max(map(abs, e)) < 2.5 and rms < 1, model
