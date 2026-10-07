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
from .fieldsolver import FieldResult, etched, mask

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
    ref_name = mask = mask_index = None
    j = i + step
    while 0 <= j < len(layers):
        layer = layers[j]
        kind = layer.get("kind")
        if kind == "copper":
            name = layer.get("layer") or layer["name"]
            if ref is not False and (ref is None or ref in (name, layer["name"])):
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
            mask_index = j
        j += step
    if ref is not None and ref is not False and ref_name is None:
        raise ValueError(f"reference plane {ref} not found {'above' if step < 0 else 'below'}")
    return {
        "h": h,
        "er": h / s if h > 0 else None,
        "ref": ref_name,
        "mask": mask if ref_name is None else None,
        "index": None if ref_name is None else j,
        "mask_index": mask_index if ref_name is None else None,
    }


def _num(x: float) -> str:
    """JS String(number) for the values used in warnings."""
    return str(int(x)) if x == int(x) else repr(x)


@dataclass(frozen=True)
class Line:
    model: str | None
    structure: str
    params: dict
    warnings: list[str] = field(default_factory=list)
    section: dict | None = None
    """With solver="field": the real cross-section for solve_cross_section."""

    def to_dict(self) -> dict:
        """The JS object (`section` only with solver="field")."""
        d = dataclasses.asdict(self)
        if d["section"] is None:
            del d["section"]
        return d


def _span(e) -> dict:
    """The x0/x1 of an extent ({x0?, x1?}; None or missing ends run to the domain edge)."""
    e = e or {}
    return {k: e[k] for k in ("x0", "x1") if e.get(k) is not None}


def _stackup_section(layers, i, up, down, o: dict, structure: str) -> dict:
    """The real cross-section of signal copper i for the field solver (the JS stackupSection)."""
    if down["index"] is not None:
        seq = list(range(down["index"], (up["index"] or 0) - 1, -1))
    elif up["index"] is not None:
        seq = list(range(up["index"], len(layers)))
    else:  # no plane at all (a layout's CPW): bottom up
        seq = list(range(len(layers) - 1, -1, -1))
    layout = o.get("layout")
    refs = {x for x in (up["index"], down["index"]) if x is not None}
    pair = o["kind"] == "differential"
    w, s = o["width"], o.get("gap")
    conductors: list[dict] = []
    dielectrics: list[dict] = []

    def thick(lay):
        if lay.get("kind") == "copper":
            v = lay.get("thickness")
            return STACKUP_DEFAULTS["copper_thickness"] if v is None else v
        return lay["thickness"] if lay.get("kind") == "dielectric" else 0

    def er_of(lay):
        v = (lay or {}).get("epsilon_r")
        return STACKUP_DEFAULTS["epsilon_r"] if v is None else v

    inner = up["index"] is not None and down["index"] is not None
    mask_index = up["mask_index"] if up["mask_index"] is not None else down["mask_index"]
    y = 0
    slab = None
    mask_at = None
    for j in seq:
        lay = layers[j]
        if lay.get("kind") == "mask":
            if j == mask_index:
                mask_at = lay
            continue
        d = thick(lay)
        if j in refs:
            if not (structure == "coplanar" and not inner):
                ext = ((layout or {}).get("planes") or {}).get("top" if j == up["index"] else "bottom")
                conductors.append({**_span(ext), "y0": y, "y1": y + d, "net": "gnd"})
        elif j == i:
            slab = {"y0": y, "y1": y + d}
            if inner:
                nb = [x for x in (layers[i - 1] if i > 0 else None, layers[i + 1] if i + 1 < len(layers) else None)]
                nb = [x for x in nb if x is not None and x.get("kind") == "dielectric"]
                pre = next((x for x in nb if x.get("dielectric") == "prepreg"), nb[0] if nb else None)
                dielectrics.append({"y0": y, "y1": y + d, "er": er_of(pre)})
        elif lay.get("kind") == "copper":
            k = j - (1 if j > i else -1)
            dielectrics.append({"y0": y, "y1": y + d, "er": er_of(layers[k] if 0 <= k < len(layers) else None)})
        elif lay.get("kind") == "dielectric":
            dielectrics.append({"y0": y, "y1": y + d, "er": er_of(lay)})
        y += d
    if layout:
        traces = [{"x0": b["x0"], "x1": b["x1"], **slab, "net": b["net"]} for b in layout["traces"]]
    elif pair:
        traces = [
            {"x0": -s / 2 - w, "x1": -s / 2, **slab, "net": "p"},
            {"x0": s / 2, "x1": s / 2 + w, **slab, "net": "n"},
        ]
    else:
        traces = [{"x0": -w / 2, "x1": w / 2, **slab, "net": "sig"}]
    conductors += [r for b in traces for r in etched(b, o.get("etch") or 0)]
    grounds: list[dict] = []
    if layout:
        grounds = [{**_span(g), **slab, "net": "gnd"} for g in layout.get("grounds") or []]
        conductors += grounds
    elif structure.startswith("coplanar"):
        e = (s / 2 + w if pair else w / 2) + o["coplanar_gap"]
        grounds = [{"x1": -e, **slab, "net": "gnd"}, {"x0": e, **slab, "net": "gnd"}]
        conductors += grounds
    if mask_at is not None and o.get("mask", True) is not False:
        ct = mask_at.get("thickness_over_copper")
        if ct is None:
            ct = mask_at.get("thickness")
        if ct is None:
            ct = STACKUP_DEFAULTS["mask_thickness"]
        c = mask_at.get("thickness")
        er = mask_at.get("epsilon_r")
        er = STACKUP_DEFAULTS["mask_epsilon_r"] if er is None else er
        dielectrics += mask(traces + grounds, slab["y0"], c=ct if c is None else c, ct=ct, er=er)
    return {"conductors": conductors, "dielectrics": dielectrics}


def line_from_stackup(
    stackup,
    layer: str,
    *,
    width: float,
    kind: str = "single",
    gap: float | None = None,
    structure: str | None = None,
    coplanar_gap: float | None = None,
    ref_top: str | bool | None = None,
    ref_bottom: str | bool | None = None,
    mask: bool = True,
    solver: str = "closedform",
    etch: float = 0,
    layout: dict | None = None,
) -> Line:
    """A signal layer ('F.Cu', 'In1.Cu') of a board model stackup as a closed-form line.

    `structure` defaults to microstrip on an outer layer and stripline on an inner one; `ref_top`/`ref_bottom`
    (ImpedanceLayer) default to the nearest copper; `mask` models the solder mask on an outer single-ended
    microstrip. `solver="field"` also returns `section`, the real cross-section for the tier-2 field solver
    (every layer's own εr, the mask on any outer structure); then differential coplanar and coplanar on inner
    layers are allowed too (`model` None when tier 1 has none); `etch` makes the traces trapezoids whose top is
    `etch` narrower than `width`. `layout` (field solver; boarddd.impedance.route) gives the copper in the signal
    layer as found on a board: `traces` [{x0, x1, net}], coplanar `grounds` [{x0?, x1?}] and the reference planes'
    extents `planes: {top, bottom}`; `ref_top`/`ref_bottom` False means no plane on that side.
    """
    if solver not in ("closedform", "field"):
        raise ValueError(f"unknown solver {solver}")
    is_field = solver == "field"
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
    # A layout (route.py) may describe a CPW with no plane at all: its coplanar grounds.
    if up["ref"] is None and down["ref"] is None and not (is_field and layout and layout.get("grounds")):
        raise ValueError(f"{layer} has no reference plane")
    structure = structure or ("microstrip" if outer else "stripline")
    ref, open_ = (down, up) if up["ref"] is None else (up, down)
    params: dict = {"w": width, "t": t}
    if structure == "stripline" or (is_field and not outer and structure.startswith("coplanar")):
        if outer:
            raise ValueError(f"{layer} is an outer layer: no stripline")
        # Different εr above and below: weight each by its plane capacitance (εr/h), as the strip sees them in parallel.
        er = (up["er"] / up["h"] + down["er"] / down["h"]) / (1 / up["h"] + 1 / down["h"])
        params.update(h1=up["h"], h2=down["h"], er=er)
        model = model_for(structure, kind) if structure == "stripline" else None
        if structure != "stripline":
            if coplanar_gap is None:
                raise ValueError(f"{structure} needs coplanar_gap")
            params["gap"] = coplanar_gap
    else:
        if not outer:
            raise ValueError(f"{layer} is an inner layer: tier 1 has no embedded {structure}")
        params.update(h=ref["h"], er=ref["er"])
        masked = bool(mask) and open_["mask"] is not None
        coated = masked and structure == "microstrip" and kind == "single"
        # Tier 1 models the mask on microstrip (single and coupled) and CPWG; not on CPW.
        with_mask = masked and (structure == "microstrip" or (structure == "coplanar_grounded" and kind == "single"))
        if masked and not with_mask and not is_field:
            warnings.append(f"{structure} {kind}: the solder mask is not modelled (tier 1)")
        if with_mask:
            params.update(open_["mask"])
        model = model_for(structure, kind, coated=coated)
        if structure.startswith("coplanar"):
            if coplanar_gap is None:
                raise ValueError(f"{structure} needs coplanar_gap")
            params["gap"] = coplanar_gap
    if model is None and not is_field:
        raise ValueError(f"tier 1 has no {kind} {structure} model")
    if kind == "differential":
        if gap is None:
            raise ValueError("a differential line needs gap")
        params["s"] = gap
    section = None
    if is_field:
        o = {"kind": kind, "width": width, "gap": gap, "coplanar_gap": coplanar_gap, "mask": mask, "etch": etch}
        o["layout"] = layout
        section = _stackup_section(layers, i, up, down, o, structure)
    return Line(model, structure, params, warnings, section)


def _field_synthesis(stackup, layer: str, opts: dict, key: str, target: float, start: float):
    """The width giving `target` with the field solver (the JS fieldSynthesis): secant steps to 1e-4."""
    from .fieldsolver import solve_cross_section

    fo = opts.get("field_options") or {}
    lo = {k: v for k, v in opts.items() if k != "field_options"}

    def run(w):
        return solve_cross_section(line_from_stackup(stackup, layer, **{**lo, "width": w}).section, **fo)

    w0 = start
    r0 = run(w0)
    w1 = w0 * (1.1 if getattr(r0, key) > target else 0.9)
    r1 = run(w1)
    it = 0
    while it < 20 and abs(w1 - w0) > 1e-4 * w1:
        f0, f1 = getattr(r0, key) - target, getattr(r1, key) - target
        if f1 == f0:
            break
        w2 = min(2 * w1, max(w1 / 2, w1 - (f1 * (w1 - w0)) / (f1 - f0)))
        w0, r0 = w1, r1
        w1 = w2
        r1 = run(w1)
        it += 1
    return w1, r1


@dataclass(frozen=True)
class TargetEvaluation:
    layer: str
    model: str | None
    key: str
    value: float
    target: float
    deviation_pct: float
    ok: bool | None
    """Within tolerance_pct; None when the target has no tolerance."""
    width: float
    """The layer's width, or the synthesized one when the target gives none."""
    synthesized: bool
    result: LineResult | CoupledResult | FieldResult
    warnings: list[str]


def evaluate_target(stackup, target, *, field_options: dict | None = None, **opts) -> list[TargetEvaluation]:
    """Evaluate a net class's ImpedanceTarget on a stackup: one row per target layer with its geometry.

    A layer with no width gets the width synthesized for the target instead. `opts` go to line_from_stackup;
    `solver="field"` evaluates (and synthesizes) with the tier-2 field solver (`field_options` go to
    solve_cross_section), and `result` is then its FieldResult.
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
        if opts.get("solver") == "field":
            from .fieldsolver import solve_cross_section

            if synthesized:
                if line.model:
                    start = synthesize(line.model, params, target["target"], key=key).value
                else:
                    start = params.get("h", params.get("h1"))
                width, result = _field_synthesis(
                    stackup, il["layer"], {**o, "field_options": field_options}, key, target["target"], start
                )
            else:
                width = params["w"]
                result = solve_cross_section(line.section, **(field_options or {}))
            result = dataclasses.replace(result, model=line.model, flags=[])
        else:
            if synthesized:
                syn = synthesize(line.model, params, target["target"], key=key)
                params, result = syn.params, syn.result
            else:
                result = calculate(line.model, params)
            width = params["w"]
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
                width,
                synthesized,
                result,
                line.warnings,
            )
        )
    return rows
