"""Refit tier 1's "boarddd" correction constants on fixtures/impedance/field-sweep.json (boarddd's own field solver).

Each group of constants in boarddd.impedance.closedform._FIT is fitted by least squares on the relative error of
the impedances it affects, over the sweep rows inside the model's validity flags; the script prints the error
before and after and the new table, rounded to three significant figures. Copy the table into closedform.py
and closedform.js (the same numbers), then run make_cases.mjs and both test suites.

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
FREE = {  # the constants fitted in each group (offset: the blend's lower end; its span stays log(30))
    "offset": ["lo"],
    "mask": ["k", "a", "p", "b", "kappa"],
    "coplanar": ["corner", "backing"],
    "coupledMicrostrip": ["even", "odd"],
    "coupledStripline": ["decay"],
}


def errors(rows) -> np.ndarray:
    out = []
    for model, args, ref in rows:
        r = calculate(model, args)
        out += [100 * (getattr(r, k) - v) / v for k, v in ref.items()]
    return np.array(out)


def inside(model, args) -> bool:
    return not calculate(model, args).flags


def sig3(x: float) -> float:
    return float(f"{x:.3g}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default=str(HERE / "field-sweep.json"))
    rows = [r[:3] for r in json.loads(Path(ap.parse_args().sweep).read_text())["rows"]]
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

        def stats(e):
            return f"max {np.abs(e).max():.2f} %, rms {math.sqrt(float((e**2).mean())):.2f} %, mean {e.mean():+.2f} %"

        print(f"{group:18s} n={len(before):3d}  before {stats(before)}  after {stats(after)}  {fit[group]}")
    print(json.dumps(fit))


if __name__ == "__main__":
    main()
