"""The result of an impedance analysis along a route (``boarddd/impedance@1``): what ``analyze_net`` (route.py) and
the JS ``analyzeNet`` (src/impedance/route.js) return.

One document per net or differential pair: the route cut into sections (each a stretch with one cross-section:
structure, geometry, reference planes, impedance, flags), the discontinuities along it and a length-weighted
summary against the net class's target. Positions are board@1's (mm, board frame); ``s`` is the distance along
the route from its start. ``schema/impedance.schema.json`` and ``src/impedance/result.d.ts`` are generated from
these dataclasses (``python -m boarddd.model --write``); see docs/impedance.md "Along a route".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from ..model import Vec2, _f, from_dict, to_dict

__all__ = [
    "SCHEMA_ID",
    "ImpedanceAnalysis",
    "Target",
    "Section",
    "SectionGeometry",
    "SectionZ",
    "Reference",
    "Summary",
    "Discontinuity",
    "Timing",
    "STRUCTURES",
    "FLAGS",
]

SCHEMA_ID = "boarddd/impedance@1"

Structure = Literal["microstrip", "embedded_microstrip", "stripline", "offset_stripline", "cpw", "cpwg", "none"]
Flag = Literal["no_ref", "plane_gap", "ref_edge", "neighbour", "uncoupled", "overlap", "override", "solver_error"]
STRUCTURES = Structure.__args__
FLAGS = Flag.__args__


@dataclass(kw_only=True)
class Target:
    """The impedance the route is checked against."""

    value: float = _f("Ohms.", exclusiveMinimum=0)
    key: Literal["Z0", "Zdiff"] = _f("What it applies to: Z0 for a net, Zdiff for a pair.")
    tolerance_pct: float = _f("Allowed deviation, percent (the analysis's default when the target gives none).")
    source: Literal["net_class", "option"] = _f("The net class's ImpedanceTarget, or the caller's.")
    net_class: str | None = _f("The net class it comes from.", default=None)


@dataclass(kw_only=True)
class SectionGeometry:
    """The cross-section as found on the board (mm); gaps are edge to edge, quantised as solved."""

    width: float = _f("Track width.", minimum=0)
    thickness: float = _f("Copper thickness (stackup).", minimum=0)
    partner_width: float | None = _f("Pairs: the partner's width.", default=None)
    gap: float | None = _f("Pairs: the gap to the partner.", default=None)
    coplanar_gap: tuple[float | None, float | None] = _f(
        "Gap to the coplanar ground on the left and right (seen along the route); null: none within the window."
    )
    h_top: float | None = _f("Dielectric height to the reference plane above.", default=None)
    h_bottom: float | None = _f("Dielectric height to the reference plane below.", default=None)


@dataclass(kw_only=True)
class SectionZ:
    """The impedance of a section's cross-section."""

    Z0: float | None = _f("Single-ended characteristic impedance, ohms.", default=None)
    Zdiff: float | None = _f(
        "Differential impedance, ohms (an uncoupled stretch of a pair: 2 Z0, its two lines independent).",
        default=None,
    )
    Zcommon: float | None = _f("Common-mode impedance, ohms.", default=None)
    Zodd: float | None = _f("Odd-mode impedance, ohms.", default=None)
    Zeven: float | None = _f("Even-mode impedance, ohms.", default=None)
    eps_eff: float | None = _f("Effective dielectric constant (single-ended).", default=None)
    error_pct: float | None = _f("The field solver's error estimate, percent; null for closed form.", default=None)
    solver: Literal["field", "closedform"] = _f("Tier 2 (field solver) or tier 1 (closed form).")
    model: str | None = _f("The closed-form model, for tier 1.", default=None)


@dataclass(kw_only=True)
class Reference:
    """A reference plane under (bottom) or over (top) a section."""

    side: Literal["top", "bottom"] = _f("Above or below the trace.")
    layer: str = _f("Copper layer id of the plane.")
    net: str = _f("The plane's net ('' when unknown).")
    h: float = _f("Dielectric height from the trace's layer, mm.", minimum=0)
    extent: tuple[float | None, float | None] = _f(
        "Where the plane ends across the trace, relative to its centre line (mm, quantised); null: beyond the window."
    )
    skipped: list[str] = _f(
        "Nearer copper layers with no plane under the trace (a split, a void, or a signal layer).", factory=list
    )


@dataclass(kw_only=True)
class Section:
    """A stretch of the route with one cross-section."""

    net: str = _f("The net it belongs to (a pair's coupled stretches are the first net's).")
    start: Vec2 = _f("Where it starts, board frame.")
    end: Vec2 = _f("Where it ends, board frame.")
    s0: float = _f("Distance along the route where it starts, mm.", minimum=0)
    s1: float = _f("Distance along the route where it ends, mm.", minimum=0)
    length: float = _f("mm.", minimum=0)
    layer: str = _f("Copper layer id.")
    structure: Structure = _f("The transmission-line structure (classified, or overridden).")
    kind: Literal["single", "differential"] = _f("'differential' where a pair is edge-coupled.")
    geometry: SectionGeometry = _f("The cross-section.")
    z: SectionZ | None = _f("Its impedance; null when it has none (no reference plane, or not solvable).")
    refs: list[Reference] = _f("Reference planes.", factory=list)
    flags: list[Flag] = _f(
        "no_ref: no reference plane (no Z); plane_gap: a plane is split or voided under the trace; ref_edge: the "
        "plane ends within the margin; neighbour: another signal within the coplanar window; uncoupled: a pair's "
        "line away from its partner; overlap: other copper overlaps the trace; override: set by the caller; "
        "solver_error.",
        factory=list,
    )
    tracks: list[str] = _f("Ids (copper@1 Track.id) of the tracks it covers.", factory=list)


@dataclass(kw_only=True)
class Summary:
    """The route's impedance weighted by length (the first net of a pair)."""

    key: Literal["Z0", "Zdiff"] = _f("What is summarised.")
    length: float = _f("Route length, mm.", minimum=0)
    length_with_z: float = _f("Length with an impedance, mm.", minimum=0)
    z_weighted: float | None = _f("Length-weighted mean, ohms.", default=None)
    z_min: float | None = _f("Lowest section value, ohms.", default=None)
    z_max: float | None = _f("Highest section value, ohms.", default=None)
    out_of_tolerance_length: float | None = _f(
        "Length outside the target's tolerance, mm; null without a target.", default=None
    )
    out_of_tolerance_pct: float | None = _f(
        "Share of the route outside tolerance or without an impedance, percent; null without a target.",
        default=None,
    )
    within: bool | None = _f("Every section has an impedance within tolerance; null without a target.", default=None)


@dataclass(kw_only=True)
class Discontinuity:
    """A change along the route, at its position."""

    type: Literal["via", "ref_change", "width_change", "plane_gap", "no_ref", "uncoupled", "ref_edge"] = _f(
        "What changes."
    )
    at: Vec2 = _f("Where, board frame.")
    s: float = _f("Distance along the route, mm.")
    layer: str = _f("Copper layer after the change.")
    net: str = _f("Net.")
    detail: str = _f("Human-readable detail.", default="")


@dataclass(kw_only=True)
class Timing:
    """How long the analysis took."""

    ms: float = _f("Wall time, ms.", minimum=0)
    solve_ms: float = _f("Of which solving cross-sections, ms.", minimum=0)
    stations: int = _f("Stations cut.", minimum=0)
    solves: int = _f("Cross-sections solved (distinct geometries not in the cache).", minimum=0)
    cache_hits: int = _f("Sections whose cross-section was already solved.", minimum=0)


@dataclass(kw_only=True)
class ImpedanceAnalysis:
    """Impedance along one net's (or a differential pair's) route."""

    schema: Literal["boarddd/impedance@1"] = _f("Format id.", default=SCHEMA_ID, required=True)
    board: str = _f("The board@1 document's name.")
    nets: list[str] = _f("The net, or the pair [p, n].", minItems=1, maxItems=2)
    kind: Literal["single", "differential"] = _f("A net or a pair.")
    solver: Literal["field", "closedform"] = _f("The solver asked for (tier 1 falls back to tier 2 where it has none).")
    target: Target | None = _f("The target; null when there is none.")
    sections: list[Section] = _f("The route in sections, in route order (a pair's uncoupled second net last).")
    summary: Summary = _f("Length-weighted summary.")
    discontinuities: list[Discontinuity] = _f("Changes along the route.", factory=list)
    options: dict[str, Any] = _f("The options the analysis ran with.", factory=dict)
    warnings: list[str] = _f("Defaults used, unsolvable sections, target kind mismatches.", factory=list)
    timing: Timing = _f("Timing.")

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    def to_json(self, indent: int | None = 1) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImpedanceAnalysis:
        return from_dict(cls, data)
