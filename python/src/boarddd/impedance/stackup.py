"""boarddd.impedance from the board model.

A boarddd/board@1 stackup (StackupLayer, top to bottom) and a net class's ImpedanceTarget (structure microstrip |
stripline | coplanar | coplanar_grounded, kind single | differential) become a closed-form model and its parameters.
src/impedance/stackup.js is the same code; fixtures/impedance/cases.json ("stackup") keeps them in step.
Accepts board.json dicts or boarddd.model dataclasses.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from .closedform import CoupledResult, LineResult, calculate, synthesize

STACKUP_DEFAULTS = {"copper_thickness": 0.035, "epsilon_r": 4.5, "mask_thickness": 0.01, "mask_epsilon_r": 3.3}
"""Defaults for what a stackup leaves out (KiCad's own defaults); each use adds a warning."""


def _plain(obj):
    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj


def model_for(structure: str, kind: str = "single", *, coated: bool = False) -> str | None:
    """The closed-form model for an ImpedanceTarget structure and kind (None when tier 1 has none)."""
    diff = kind == "differential"
    if structure == "microstrip":
        return "coupled_microstrip" if diff else "coated_microstrip" if coated else "microstrip"
    if structure == "stripline":
        return "coupled_stripline" if diff else "stripline"
    if structure == "coplanar":
        return None if diff else "cpw"
    if structure == "coplanar_grounded":
        return None if diff else "cpwg"
    raise ValueError(f"unknown structure {structure}")


def _side(layers: list[dict], i: int, step: int, ref: str | None, warnings: list[str]) -> dict:
    """Dielectric between copper i and the reference copper in direction `step` (-1 up, +1 down)."""
    h = s = 0.0
    ref_name = mask = None
    j = i + step
    while 0 <= j < len(layers):
        layer = layers[j]
        kind = layer.get("kind")
        if kind == "copper":
            name = layer.get("layer") or layer["name"]
            if ref is None or ref in (name, layer["name"]):
                ref_name = name
                break
            ct = layer.get("thickness")
            ct = STACKUP_DEFAULTS["copper_thickness"] if ct is None else ct
            prev = layers[j - step] if 0 <= j - step < len(layers) else {}
            prev_er = prev.get("epsilon_r")
            h += ct  # a plane voided under the trace: filled with resin
            s += ct / (STACKUP_DEFAULTS["epsilon_r"] if prev_er is None else prev_er)
        elif kind == "dielectric":
            if layer.get("thickness") is None:
                raise ValueError(f"stackup layer {layer['name']} has no thickness")
            er = layer.get("epsilon_r")
            if er is None:
                er = STACKUP_DEFAULTS["epsilon_r"]
                warnings.append(f"{layer['name']}: no epsilon_r, using {_num(er)}")
            h += layer["thickness"]
            s += layer["thickness"] / er
        elif kind == "mask" and ref_name is None and mask is None:
            c = layer.get("thickness_over_copper")
            c = layer.get("thickness") if c is None else c
            erc = layer.get("epsilon_r")
            if c is None:
                c = STACKUP_DEFAULTS["mask_thickness"]
                warnings.append(f"{layer['name']}: no thickness, using {_num(c)} mm")
            if erc is None:
                erc = STACKUP_DEFAULTS["mask_epsilon_r"]
                warnings.append(f"{layer['name']}: no epsilon_r, using {_num(erc)}")
            mask = {"c": c, "erc": erc}
        j += step
    if ref is not None and ref_name is None:
        raise ValueError(f"reference plane {ref} not found {'above' if step < 0 else 'below'}")
    return {"h": h, "er": h / s if h > 0 else None, "ref": ref_name, "mask": mask if ref_name is None else None}


def _num(x: float) -> str:
    """JS String(number) for the values used in warnings."""
    return str(int(x)) if x == int(x) else repr(x)


@dataclass(frozen=True)
class Line:
    model: str
    structure: str
    params: dict
    warnings: list[str] = field(default_factory=list)


def line_from_stackup(
    stackup,
    layer: str,
    *,
    width: float,
    kind: str = "single",
    gap: float | None = None,
    structure: str | None = None,
    coplanar_gap: float | None = None,
    ref_top: str | None = None,
    ref_bottom: str | None = None,
    mask: bool = True,
) -> Line:
    """A signal layer ('F.Cu', 'In1.Cu') of a board model stackup as a closed-form line.

    `structure` defaults to microstrip on an outer layer and stripline on an inner one; `ref_top`/`ref_bottom`
    (ImpedanceLayer) default to the nearest copper; `mask` models the solder mask on an outer single-ended
    microstrip.
    """
    layers = [_plain(x) for x in (_plain(stackup) or {}).get("layers", [])]
    i = next(
        (k for k, x in enumerate(layers) if x.get("kind") == "copper" and layer in (x.get("layer"), x["name"])), -1
    )
    if i < 0:
        raise ValueError(f"no copper layer {layer} in the stackup")
    warnings: list[str] = []
    t = layers[i].get("thickness")
    if t is None:
        t = STACKUP_DEFAULTS["copper_thickness"]
        warnings.append(f"{layer}: no thickness, using {_num(t)} mm")
    up, down = _side(layers, i, -1, ref_top, warnings), _side(layers, i, 1, ref_bottom, warnings)
    outer = up["ref"] is None or down["ref"] is None
    if up["ref"] is None and down["ref"] is None:
        raise ValueError(f"{layer} has no reference plane")
    structure = structure or ("microstrip" if outer else "stripline")
    ref, open_ = (down, up) if up["ref"] is None else (up, down)
    params: dict = {"w": width, "t": t}
    if structure == "stripline":
        if outer:
            raise ValueError(f"{layer} is an outer layer: no stripline")
        # Different εr above and below: weight each by its plane capacitance (εr/h), as the strip sees them in parallel.
        er = (up["er"] / up["h"] + down["er"] / down["h"]) / (1 / up["h"] + 1 / down["h"])
        params.update(h1=up["h"], h2=down["h"], er=er)
        model = model_for(structure, kind)
    else:
        if not outer:
            raise ValueError(f"{layer} is an inner layer: tier 1 has no embedded {structure}")
        params.update(h=ref["h"], er=ref["er"])
        coated = structure == "microstrip" and kind == "single" and mask and open_["mask"] is not None
        if mask and open_["mask"] is not None and not coated:
            warnings.append(f"{structure} {kind}: the solder mask is not modelled (tier 1)")
        if coated:
            params.update(open_["mask"])
        model = model_for(structure, kind, coated=coated)
        if structure.startswith("coplanar"):
            if coplanar_gap is None:
                raise ValueError(f"{structure} needs coplanar_gap")
            params["gap"] = coplanar_gap
    if model is None:
        raise ValueError(f"tier 1 has no {kind} {structure} model")
    if kind == "differential":
        if gap is None:
            raise ValueError("a differential line needs gap")
        params["s"] = gap
    return Line(model, structure, params, warnings)


@dataclass(frozen=True)
class TargetEvaluation:
    layer: str
    model: str
    key: str
    value: float
    target: float
    deviation_pct: float
    ok: bool | None
    """Within tolerance_pct; None when the target has no tolerance."""
    width: float
    """The layer's width, or the synthesized one when the target gives none."""
    synthesized: bool
    result: LineResult | CoupledResult
    warnings: list[str]


def evaluate_target(stackup, target, **opts) -> list[TargetEvaluation]:
    """Evaluate a net class's ImpedanceTarget on a stackup: one row per target layer with its geometry.

    A layer with no width gets the width synthesized for the target instead. `opts` go to line_from_stackup.
    """
    target = _plain(target)
    key = "Zdiff" if target["kind"] == "differential" else "Z0"
    rows = []
    for il in target.get("layers") or []:
        il = _plain(il)
        o = {
            **opts,
            "kind": target["kind"],
            "structure": target.get("structure") or opts.get("structure"),
            "gap": il.get("gap") if il.get("gap") is not None else opts.get("gap"),
            "ref_top": il.get("ref_top"),
            "ref_bottom": il.get("ref_bottom"),
            "width": il.get("width") if il.get("width") is not None else 1,
        }
        line = line_from_stackup(stackup, il["layer"], **o)
        params = line.params
        synthesized = il.get("width") is None
        if synthesized:
            syn = synthesize(line.model, params, target["target"], key=key)
            params, result = syn.params, syn.result
        else:
            result = calculate(line.model, params)
        value = getattr(result, key)
        dev = 100 * (value - target["target"]) / target["target"]
        tol = target.get("tolerance_pct")
        rows.append(
            TargetEvaluation(
                il["layer"],
                line.model,
                key,
                value,
                target["target"],
                dev,
                None if tol is None else abs(dev) <= tol,
                params["w"],
                synthesized,
                result,
                line.warnings,
            )
        )
    return rows
