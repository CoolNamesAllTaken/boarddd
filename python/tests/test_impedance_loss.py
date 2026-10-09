"""boarddd.impedance.loss against fixtures/impedance/loss-cases.json (test/impedance/loss.test.mjs runs the same
file): golden references, JS parity of every helper, tier-1 line, field section and Touchstone file, plus the stackup
and analyze_net with a frequency, and (when installed) a live scikit-rf cross-check."""

import math

import pytest
from test_impedance import rel

from boarddd import impedance as z
from boarddd.impedance.loss import line_loss, section_loss, touchstone

from conftest import load

pytest.importorskip("scipy")

CASES = load("impedance/loss-cases.json")
NP_DB_IN = (20 / math.log(10)) * 0.0254
PY = {"maxLevel": "max_level"}


def close(actual, expected, path="", tol=1e-8):
    """Numbers within `tol` relative (1e-12 absolute near 0), everything else equal."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected, path
    elif isinstance(expected, int | float):
        assert isinstance(actual, int | float), path
        ok = actual == expected or abs(actual - expected) < 1e-12 or rel(actual, expected) < tol
        assert ok, f"{path}: {actual} != {expected}"
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f"{path} length"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            close(a, e, f"{path}[{i}]", tol)
    else:
        assert sorted(actual) == sorted(expected), path
        for k in expected:
            close(actual[k], expected[k], f"{path}.{k}", tol)


def solve_opts(o: dict) -> dict:
    return {PY.get(k, k): v for k, v in o.items()}


_SOLVED: dict = {}


def evaluate(g: dict) -> float:
    """A golden case's value (the same evaluator as the JS test)."""
    a = g["args"]

    def pick(r):
        d = r.to_dict()
        return {
            "alpha_c": d["alpha_c"][0],
            "alpha_d": d["alpha_d"][0],
            "alpha": d["alpha"][0],
            "db_per_inch": d["db_per_inch"][0],
            "Z0": d["Z0"][0],
            "alpha_c_db_per_inch": d["alpha_c"][0] * NP_DB_IN,
            "alpha_d_db_per_inch": d["alpha_d"][0] * NP_DB_IN,
            "Z0_lossless": math.sqrt(d["L"][0] / d["C"][0]),
        }[g["key"]]

    kind = g["kind"]
    if kind == "dielectric":
        return z.dielectric_at(a["f"], a)[g["key"]]
    if kind == "cannonball":
        return z.cannonball(a["rz"])[g["key"]]
    if kind == "huray_area":
        return 36 * a["radius"] * a["radius"]
    if kind == "dispersion":
        return z.microstrip_dispersion(a, a["f"])[g["key"]]
    if kind == "line":
        return pick(line_loss(a["model"], a["params"], [a["f"]], **a["loss"]))
    if kind == "section":
        key = repr((a["section"], a["solve"]))
        if key not in _SOLVED:
            _SOLVED[key] = z.solve_cross_section(a["section"], loss=True, **solve_opts(a["solve"]))
        return pick(section_loss(_SOLVED[key], [a["f"]], **a["loss"]))
    if kind == "wheeler":
        f = 1e10
        r = line_loss(a["model"], a["params"], [f])
        return r.R[0] / ((2 * z.surface_resistance(f)) / (a["params"]["w"] * 1e-3))
    raise AssertionError(f"unknown golden kind {kind}")


@pytest.mark.parametrize("g", CASES["golden"], ids=lambda g: f"{g['id']}-{g['key']}")
def test_golden(g):
    v = evaluate(g)
    err = 100 * rel(v, g["ref"])
    assert err <= g["tol_pct"], f"{g['key']} {v} vs {g['ref']}: {err:.3f} % > {g['tol_pct']} %"


def test_parity_helpers():
    P = CASES["parity"]
    for c in P["dielectric"]:
        close([z.dielectric_at(f, c["m"]) for f in c["fs"]], c["expect"], "dielectric")
    for c in P["roughness"]:
        close([z.roughness_factor(f, c["r"]) for f in c["fs"]], c["expect"], "roughness")
    for c in P["dispersion"]:
        close([z.microstrip_dispersion(c["p"], f) for f in c["fs"]], c["expect"], "dispersion")
    for c in P["coupled_dispersion"]:
        close([z.coupled_microstrip_dispersion(c["p"], f) for f in c["fs"]], c["expect"], "coupled")


@pytest.mark.parametrize("c", CASES["parity"]["lines"], ids=lambda c: c["model"])
def test_parity_lines(c):
    close(line_loss(c["model"], c["params"], c["fs"], **c["opts"]).to_dict(), c["expect"], c["model"])


def test_parity_touchstone():
    lines = CASES["parity"]["lines"]
    for c in CASES["parity"]["touchstone"]:
        mine = touchstone(lines[c["line"]]["expect"], c["length"], z0=c["z0"])
        js = c["expect"]
        if mine == js:
            continue
        # Same numbers, maybe a last-digit rounding difference: compare token by token.
        a, b = mine.split("\n"), js.split("\n")
        assert len(a) == len(b) and a[:2] == b[:2]
        for x, y in zip(a[2:], b[2:], strict=True):
            close([float(v) for v in x.split()], [float(v) for v in y.split()], "touchstone", 1e-8)


@pytest.mark.parametrize("c", CASES["parity"]["sections"], ids=lambda c: c["id"])
def test_parity_sections(c):
    r = z.solve_cross_section(c["section"], loss=True, **solve_opts(c["solve"]))
    close(r.loss, c["partials"], f"{c['id']} partials")
    close(section_loss(r, c["fs"], **c["opts"]).to_dict(), c["expect"], c["id"])


def test_dielectric_and_roughness_limits():
    m = {"er": 4.2, "tand": 0.02, "frequency": 1e9}
    assert z.dielectric_at(1e9, m)["er"] == pytest.approx(4.2, rel=1e-12)
    slope = z.dielectric_at(1e10, m)["er"] - z.dielectric_at(1e9, m)["er"]
    assert rel(slope, -(2 / math.pi) * math.log(10) * 4.2 * 0.02) < 0.05
    assert z.dielectric_at(5e9, {**m, "model": "constant"}) == {"er": 4.2, "tand": 0.02}
    with pytest.raises(ValueError):
        z.dielectric_at(1e9, {"er": 0.5})
    assert z.roughness_factor(1e9, None) == 1
    assert 1.99 < z.roughness_factor(1e12, {"rq": 0.001}) <= 2
    with pytest.raises(ValueError):
        z.roughness_factor(1e9, {"model": "huray"})
    with pytest.raises(ValueError):
        line_loss("microstrip", {"w": 0.2, "h": 0.2, "t": 0, "er": 4}, [1e9])


def test_stackup_loss_options():
    st = load("impedance/cases.json")["stackups"]["royalblue"]
    layers = [
        {**x, "roughness_rz": 0.004}
        if x["kind"] == "copper"
        else {**x, "loss_tangent": 0.015, "frequency": 1e10}
        if x["kind"] == "dielectric"
        else x
        for x in st["layers"]
    ]
    line = z.line_from_stackup({**st, "layers": layers}, "F.Cu", width=0.3, solver="field", loss=True)
    assert line.loss["signal"]["roughness"] == {"rz": 0.004}
    fs = [1e9, 1e10]
    t1 = line_loss(line.model, line.params, fs, **line.loss)
    t2 = section_loss(z.solve_cross_section(line.section, loss=True), fs, **line.loss)
    for a, b in zip(t1.db_per_mm, t2.db_per_mm, strict=True):
        assert rel(a, b) < 0.04
    assert z.roughness_of({"roughness_model": "none"}) == {"model": "none"}
    assert z.roughness_of({"name": "x"}) is None


def test_analyze_net_with_a_frequency():
    from boarddd.impedance.route import analyze_net
    from boarddd.validate import validate_impedance

    data = load("impedance/route/pair.json")
    cache: dict = {}
    plain = analyze_net(data["board"], data["copper"], ["USB_P", "USB_N"], cache=cache).to_dict()
    assert plain["summary"]["loss"] is None
    doc = analyze_net(data["board"], data["copper"], ["USB_P", "USB_N"], cache=cache, frequency=5e9).to_dict()
    assert validate_impedance(doc) == []
    loss = doc["summary"]["loss"]
    secs = [s for s in doc["sections"] if s["net"] == "USB_P" and s["loss"]]
    assert loss["db"] == pytest.approx(sum(s["loss"]["db"] for s in secs), rel=1e-6)
    again = analyze_net(data["board"], data["copper"], ["USB_P", "USB_N"], cache=cache, frequency=1e10).to_dict()
    assert again["timing"]["solves"] == 0 and again["summary"]["loss"]["db"] > loss["db"]


def test_live_scikit_rf():
    """Djordjevic-Sarkar and Kirschning-Jansen against scikit-rf now (the fixture holds the same check, frozen)."""
    skrf = pytest.importorskip("skrf")
    import numpy as np
    from skrf.media import MLine

    fs = [1e8, 1e9, 1e10, 3e10]
    ml = MLine(
        frequency=skrf.Frequency.from_f(np.array(fs), unit="hz"),
        w=0.3e-3,
        h=0.17e-3,
        t=35e-6,
        ep_r=3.9,
        tand=0.012,
        rho=1 / 5.8e7,
        rough=0,
        disp="kirschningjansen",
        diel="djordjevicsvensson",
        f_epr_tand=1e9,
        f_low=1e3,
        f_high=1e12,
        model="hammerstadjensen",
        compatibility_mode="qucs",
    )
    for i, f in enumerate(fs):
        d = z.dielectric_at(f, {"er": 3.9, "tand": 0.012, "frequency": 1e9})
        assert rel(d["er"], float(np.real(ml.ep_r_f[i]))) < 1e-9
        p = {
            "w": 0.3,
            "h": 0.17,
            "er": d["er"],
            "eps_eff": float(np.real(ml.ep_reff[i])),
            "Z0": float(np.real(ml.zl_eff[i])),
        }
        disp = z.microstrip_dispersion(p, f)
        assert rel(disp["eps_eff"], float(np.real(ml.ep_reff_f[i]))) < 1e-9
        assert rel(disp["Z0"], float(np.real(ml._z_characteristic[i]))) < 1e-9
