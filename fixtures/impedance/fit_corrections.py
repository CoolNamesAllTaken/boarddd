"""Refit tier 1's "boarddd" correction constants on boarddd's own field solver.

Each group of constants in boarddd.impedance.closedform._FIT is fitted by least squares on the relative error of
the impedances it affects, over the rows of fixtures/impedance/field-sweep.json inside the model's validity
flags. The mask terms of CPWG and coupled microstrip are fitted on field-mask-sweep.json, to the ratio of each
masked impedance to the same line without mask (so the bare formula's own error does not leak into them). The
script prints the error before and after and the new table, rounded to three significant figures. Copy the table
into closedform.py and closedform.js (the same numbers), then run make_cases.mjs and both test suites.

    python fixtures/impedance/fit_corrections.py [--sweep fixtures/impedance/qs-sweep.json]

Needs numpy and scipy (the ``dev`` extra).
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from boarddd.impedance import calculate, closedform

HERE = Path(__file__).parent

# group -> (models, row filter), fitted in this order (the offset blend feeds coupled stripline)
GROUPS = {
    "offset": (["stripline"], lambda a: a.get("h2", a["h1"]) != a["h1"]),
    "mask": (["coated_microstrip"], lambda a: True),
    "coplanar": (["cpwg"], lambda a: a["t"] >= 0.018),
    "coupledMicrostrip": (["coupled_microstrip"], lambda a: a["t"] >= 0.018),
    "coupledStripline": (["coupled_stripline"], lambda a: a["t"] >= 0.018),
}
# mask group -> (model, key)
MASK_GROUPS = {
    "maskCpwg": ("cpwg", "Z0"),
    "maskCoupledEven": ("coupled_microstrip", "Zeven"),
    "maskCoupledOdd": ("coupled_microstrip", "Zodd"),
}
FREE = {  # the constants fitted in each group (offset: the blend's lower end; its span stays log(30))
    "offset": ["lo"],
    "mask": ["k", "a", "p", "b", "kappa"],
    "coplanar": ["corner", "backing"],
    "coupledMicrostrip": ["even", "odd"],
    "coupledStripline": ["decay"],
    "maskCpwg": ["k", "a", "p", "b"],
    "maskCoupledEven": ["k", "a", "p", "b"],
    "maskCoupledOdd": ["k", "a", "p", "b"],
}


def errors(rows) -> np.ndarray:
    out = []
    for model, args, ref in rows:
        r = calculate(model, args)
        out += [100 * (getattr(r, k) - v) / v for k, v in ref.items()]
    return np.array(out)


def mask_errors(pairs, key) -> np.ndarray:
    """% error of tier 1's masked/bare ratio against the field solver's, per (masked row, bare row)."""
    out = []
    for (model, args, ref), (_, bare_args, bare_ref) in pairs:
        r, b = getattr(calculate(model, args), key), getattr(calculate(model, bare_args), key)
        out.append(100 * ((r / b) / (ref[key] / bare_ref[key]) - 1))
    return np.array(out)


def stats(e) -> str:
    return f"max {np.abs(e).max():.2f} %, rms {math.sqrt(float((e**2).mean())):.2f} %, mean {e.mean():+.2f} %"


def inside(model, args) -> bool:
    return not calculate(model, args).flags


def sig3(x: float) -> float:
    return float(f"{x:.3g}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default=str(HERE / "field-sweep.json"))
    ap.add_argument("--mask-sweep", default=str(HERE / "field-mask-sweep.json"))
    args = ap.parse_args()
    rows = [r[:3] for r in json.loads(Path(args.sweep).read_text())["rows"]]
    mask_rows = [r[:3] for r in json.loads(Path(args.mask_sweep).read_text())["rows"]]
    fit = copy.deepcopy(closedform._FIT)
    for group, (models, keep) in GROUPS.items():
        sel = [r for r in rows if r[0] in models and keep(r[1]) and inside(r[0], r[1])]
        names = FREE[group]
        before = errors(sel)

        def res(x, names=names, group=group, sel=sel):
            for n, v in zip(names, x, strict=True):
                closedform._FIT[group][n] = float(v)
            return errors(sel)

        x0 = [closedform._FIT[group][n] for n in names]
        sol = least_squares(res, x0, x_scale="jac")
        for n, v in zip(names, sol.x, strict=True):
            closedform._FIT[group][n] = sig3(v)
            fit[group][n] = sig3(v)
        after = errors(sel)

        print(f"{group:18s} n={len(before):3d}  before {stats(before)}  after {stats(after)}  {fit[group]}")
    bare = {json.dumps(r[1], sort_keys=True): r for r in mask_rows if "c" not in r[1]}
    for group, (model, key) in MASK_GROUPS.items():
        pairs = []
        for r in mask_rows:
            if r[0] != model or "c" not in r[1] or not inside(r[0], r[1]):
                continue
            b = {k: v for k, v in r[1].items() if k not in ("c", "erc")}
            pairs.append((r, bare[json.dumps(b, sort_keys=True)]))
        names = FREE[group]
        closedform._FIT[group]["b"] = 0.0
        before = mask_errors(pairs, key)

        def res(x, names=names, group=group, pairs=pairs, key=key):
            for n, v in zip(names, x, strict=True):
                closedform._FIT[group][n] = float(v)
            return mask_errors(pairs, key)

        sol = least_squares(res, [closedform._FIT[group][n] for n in names], x_scale="jac")
        for n, v in zip(names, sol.x, strict=True):
            closedform._FIT[group][n] = sig3(v)
            fit[group][n] = sig3(v)
        after = mask_errors(pairs, key)
        print(f"{group:18s} n={len(before):3d}  ratio before (b=0) {stats(before)}  after {stats(after)}  {fit[group]}")
    print(json.dumps(fit))


if __name__ == "__main__":
    main()
