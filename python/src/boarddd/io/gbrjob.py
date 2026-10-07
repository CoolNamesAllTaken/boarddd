"""
The board's real dimensions, from the .gbrjob file.

Worth its own module for one reason: this is the only trustworthy statement of how big the
board is. Measuring the gerbers does not work -- a KiCad export made with "plot drawing sheet"
enabled draws the sheet border and title block on *every* layer, so on one real package
every single gerber spans 412 x 259 mm around a board that is 14.55 x 23.55. A viewer
that framed itself on gerber extents would show a drawing sheet with a speck in the middle.

The .gbrjob is JSON the gerber standard defines, KiCad writes it, and `GeneralSpecs.Size` is
the board. Absent or malformed, the answer is None and the viewer falls back to framing the
placements -- which is what it is really showing anyway.

Pure: no settings, no filesystem.

Source: `magpie/pcb/jobfile.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable

from boarddd import model as m

__all__ = ["board_size", "project_name", "looks_like_panel", "read_stackup"]

#: The largest side accepted: a kilometre. Anything bigger is not a board (and would overflow a numeric(12,6) column).
MAX_SIDE_MM = 10**6


def board_size(text: str) -> tuple[float, float] | None:
    """(width, height) in millimeters, or None if the job file does not say."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    size = _specs(data).get("Size") or {}
    try:
        width = float(size["X"])
        height = float(size["Y"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    # `"NaN"`, `"Infinity"` and 1e400 are all floats; none is a board, and each was a 500 when
    # the package's board size -- numeric(12,6), so under a kilometre -- was asked to hold it.
    if not all(math.isfinite(side) and 0 < side < MAX_SIDE_MM for side in (width, height)):
        return None
    return width, height


def project_name(text: str) -> str:
    """
    What KiCad called the project, or '' if the job file does not say.

    A KiKit panel is a project of its own, named `<board>-panel`, and that suffix is the one
    thing that tells a panel export from a board export before either is opened -- the gerbers
    inside carry the same layer headers either way.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    project = _specs(data).get("ProjectId") or {}
    return str(project.get("Name") or "").strip() if isinstance(project, dict) else ""


def _specs(data: dict) -> dict:
    """
    The `GeneralSpecs` object, or {} when the file has something else there.

    Checked, because an application reads this for every listing of the board's files:
    `{"GeneralSpecs": "x"}` was an AttributeError, and so a server error on the very page
    that offers to remove the file.
    """
    specs = data.get("GeneralSpecs")
    return specs if isinstance(specs, dict) else {}


def looks_like_panel(name: str) -> bool:
    """Whether a project name is a KiKit panel's. The `-panel` suffix is KiKit's own."""
    return name.lower().endswith("-panel") or name.lower().endswith("_panel")


# ---------------------------------------------------------------------------------------------------------------------
# MaterialStackup (boarddd phase I1)

STACKUP_KIND = {
    "legend": "silk",
    "solderpaste": "paste",
    "soldermask": "mask",
    "copper": "copper",
    "dielectric": "dielectric",
}
_SUBLAYER = re.compile(r"^(.*?)\s*\((\d+)/(\d+)\)$")  # KiCad: "F.Cu/In1.Cu (1/2)"


def _num(value) -> float | None:
    try:
        v = float(value)  # KiCad writes DielectricConstant/LossTangent as strings
    except (TypeError, ValueError):
        return None
    return round(v, 6) + 0.0 if math.isfinite(v) else None


def read_stackup(
    job: str | dict,
    copper_ids: list[str] | None = None,
    layer_id: Callable[[str, str], str | None] | None = None,
) -> m.Stackup:
    """
    The job's GeneralSpecs (thickness, copper count, finish, ImpedanceControlled) and MaterialStackup
    as a model Stackup, top to bottom.

    `copper_ids`: the board's copper Layer.ids top to bottom, to link copper entries to them (only when
    the counts agree). `layer_id(kind, side)`: the Layer.id of a silk/paste/mask entry. Er and Df are
    numbers or (KiCad) strings; a dielectric KiCad splits into sublayers ("F.Cu/In1.Cu (1/2)", "(2/2)")
    becomes one layer with `sublayers`, as the .kicad_pcb reader reads it. The job format has no
    prepreg/core, frequency or roughness.
    """
    if isinstance(job, str):
        try:
            job = json.loads(job)
        except (ValueError, TypeError):
            job = {}
    if not isinstance(job, dict):
        job = {}
    specs = _specs(job)
    entries = [e for e in (job.get("MaterialStackup") or []) if isinstance(e, dict)]
    kinds = [STACKUP_KIND.get(str(e.get("Type") or "").replace(" ", "").lower(), "other") for e in entries]
    coppers = [i for i, k in enumerate(kinds) if k == "copper"]
    out: list[m.StackupLayer] = []
    for i, (entry, kind) in enumerate(zip(entries, kinds, strict=True)):
        if not coppers or i < coppers[0] or (kind == "copper" and i == coppers[0]):
            side = "top"
        elif i > coppers[-1] or (kind == "copper" and i == coppers[-1]):
            side = "bottom"
        else:
            side = "inner"
        layer = None
        if kind == "copper":
            index = coppers.index(i)
            if copper_ids and len(copper_ids) == len(coppers):
                layer = copper_ids[index]
        elif kind in ("silk", "paste", "mask") and layer_id is not None:
            layer = layer_id(kind, side)
        name = str(entry.get("Name") or entry.get("Type") or "")
        sl = m.StackupLayer(
            name=name,
            kind=kind,
            side=side,
            thickness=_num(entry.get("Thickness")),
            material=str(entry["Material"]) if entry.get("Material") else None,
            color=str(entry["Color"]) if entry.get("Color") else None,
            epsilon_r=_num(entry.get("DielectricConstant")) or None,
            loss_tangent=_num(entry.get("LossTangent")),
            layer=layer,
            conductivity=_num(entry.get("Conductivity")) or None,
        )
        sub = _SUBLAYER.match(name) if kind == "dielectric" else None
        if sub and out and int(sub.group(2)) > 1 and out[-1].kind == "dielectric" and out[-1].name == sub.group(1):
            _add_sublayer(out[-1], sl)
            continue
        if sub:
            sl.name = sub.group(1)
            _add_sublayer(sl, None)
        out.append(sl)

    def color(kind: str, side: str) -> str | None:
        return next((la.color for la in out if la.kind == kind and la.side == side and la.color), None)

    count = specs.get("LayerNumber")
    controlled = specs.get("ImpedanceControlled")
    return m.Stackup(
        thickness=_num(specs.get("BoardThickness")),
        copper_layers=count if isinstance(count, int) and count > 0 else (len(coppers) or None),
        finish=str(specs["Finish"]) if specs.get("Finish") else None,
        mask_color=m.SideValues(top=color("mask", "top"), bottom=color("mask", "bottom")),
        silk_color=m.SideValues(top=color("silk", "top"), bottom=color("silk", "bottom")),
        layers=out,
        impedance_controlled=controlled if isinstance(controlled, bool) else None,
    )


def _add_sublayer(layer: m.StackupLayer, part: m.StackupLayer | None) -> None:
    """Fold a "(n/N)" entry into `layer`: totals as in boarddd.io.kicad (sum, series Er, weighted Df)."""
    if not layer.sublayers:
        layer.sublayers = [
            m.StackupSublayer(
                thickness=layer.thickness,
                material=layer.material,
                color=layer.color,
                epsilon_r=layer.epsilon_r,
                loss_tangent=layer.loss_tangent,
            )
        ]
    if part is None:
        return
    layer.sublayers.append(
        m.StackupSublayer(
            thickness=part.thickness,
            material=part.material,
            color=part.color,
            epsilon_r=part.epsilon_r,
            loss_tangent=part.loss_tangent,
        )
    )
    subs = layer.sublayers
    if all(s.thickness is not None for s in subs):
        total = sum(s.thickness for s in subs)
        layer.thickness = round(total, 6)
        if total > 0 and all(s.epsilon_r for s in subs):
            layer.epsilon_r = round(total / sum(s.thickness / s.epsilon_r for s in subs), 6)
        if total > 0 and all(s.loss_tangent is not None for s in subs):
            layer.loss_tangent = round(sum(s.thickness * s.loss_tangent for s in subs) / total, 6)
    materials = list(dict.fromkeys(s.material for s in subs if s.material))
    layer.material = " + ".join(materials) if materials else None
