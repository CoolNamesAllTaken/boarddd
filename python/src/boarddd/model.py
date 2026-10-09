"""The normalised board model (``boarddd/board@1``): one JSON document per board revision.

Readers (server side, Python) produce a :class:`Board`; renderers (browser side, JS) consume it as
``board.json``. These dataclasses own the format: ``schema/board.schema.json`` and the JS typings in
``src/model`` are generated from them (``python -m boarddd.model --write``; CI runs ``--check``).

Conventions (see docs/model.md):

* units are millimetres, angles degrees;
* the **board frame**: x right, y up, seen from the top, origin = the source's file origin (Gerber,
  ODB++ and IPC-2581 store this frame; KiCad readers negate y);
* footprints (pads, graphics) keep KiCad's footprint semantics: footprint frame, mm, y **down**, as in
  a ``.kicad_mod`` file, top-side (library) orientation; a component places its footprint.
"""

from __future__ import annotations

import json
import sys
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

SCHEMA_ID = "boarddd/board@1"

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Side = Literal["top", "bottom"]
LayerSide = Literal["top", "bottom", "inner", "none"]


def doc(text: str, **schema: Any) -> dict[str, Any]:
    """Field metadata: a description plus extra JSON Schema keywords (minimum, minItems, pattern...)."""
    return {"doc": text, "schema": schema}


def _f(text: str, default: Any = MISSING, factory: Any = MISSING, **schema: Any) -> Any:
    if factory is not MISSING:
        return field(default_factory=factory, metadata=doc(text, **schema))
    if default is not MISSING:
        return field(default=default, metadata=doc(text, **schema))
    return field(metadata=doc(text, **schema))


# ---------------------------------------------------------------------------------------------------------------------
# source / provenance


@dataclass(kw_only=True)
class SourceFile:
    """One input file the model was read from."""

    path: str = _f("Path relative to the source root (package, project folder or archive).")
    role: Literal[
        "pcb",
        "copper",
        "mask",
        "paste",
        "silk",
        "outline",
        "drill",
        "drill_map",
        "fab",
        "job",
        "placement",
        "bom",
        "netlist",
        "model",
        "odb",
        "ipc2581",
        "other",
    ] = _f("What the file holds.")
    side: LayerSide | None = _f("Board side, for layer files.", default=None)
    sha256: str | None = _f("Lower-case hex SHA-256 of the file's bytes.", default=None, pattern="^[0-9a-f]{64}$")


@dataclass(kw_only=True)
class Source:
    """Where the model came from (provenance)."""

    kind: Literal["kicad_pcb", "gerber", "odbpp", "ipc2581", "step", "other"] = _f(
        "The primary input format the reader used."
    )
    files: list[SourceFile] = _f("Input files.", factory=list)
    generator: str | None = _f(
        "The tool that wrote the inputs, e.g. 'KiCad Pcbnew 10.0.6' (Gerber X2/gbrjob GenerationSoftware).",
        default=None,
    )
    reader: str | None = _f("The reader that built this model, e.g. 'boarddd 0.2.0 io.kicad'.", default=None)
    created: str | None = _f("When the inputs were made (ISO 8601), if the source says.", default=None)
    commit: str | None = _f("VCS commit of the design (kipr: the head sha).", default=None)
    ref: str | None = _f("VCS ref or upload/package id the inputs belong to.", default=None)


@dataclass(kw_only=True)
class Origin:
    """Origins the source defines besides the file origin, in the board frame, so writers can go back."""

    aux: Vec2 | None = _f("Auxiliary (place/drill-file) origin, e.g. KiCad's aux_axis_origin.", default=None)
    grid: Vec2 | None = _f("Grid origin.", default=None)
    drill: Vec2 | None = _f("Origin the drill files were written relative to, when not the file origin.", default=None)


# ---------------------------------------------------------------------------------------------------------------------
# outline, stackup, layers, drills


@dataclass(kw_only=True)
class Outline:
    """The board shape: arcs flattened to polylines, loops implicitly closed (last point != first)."""

    board: list[Vec2] = _f("The outer edge, counter-clockwise.", minItems=3)
    cutouts: list[list[Vec2]] = _f(
        "Internal cutouts (routed holes, slots wider than a drill), clockwise.", factory=list, items={"minItems": 3}
    )
    approximate: bool = _f(
        "True when the edge is a fallback (bounding box, convex hull) rather than the real profile.", default=False
    )


@dataclass(kw_only=True)
class SideValues:
    """A value per board side."""

    top: str | None = _f("Top side.", default=None)
    bottom: str | None = _f("Bottom side.", default=None)


@dataclass(kw_only=True)
class Tolerance:
    """Manufacturing tolerances of a stackup layer, as [minus, plus] deviations (IPC-2581 tolPlus/tolMinus)."""

    thickness: Vec2 | None = _f("[minus, plus] in mm, e.g. [-0.01, 0.01].", default=None)
    epsilon_r: Vec2 | None = _f("[minus, plus] of the dielectric constant.", default=None)


@dataclass(kw_only=True)
class StackupSublayer:
    """One part of a dielectric the source splits into several (KiCad ``addsublayer``, gbrjob '(1/2)' entries)."""

    thickness: float | None = _f("mm.", default=None, minimum=0)
    material: str | None = _f("Material name, e.g. '2116 RC58%'.", default=None)
    color: str | None = _f("Colour as the source names it.", default=None)
    epsilon_r: float | None = _f("Dielectric constant.", default=None, exclusiveMinimum=0)
    loss_tangent: float | None = _f("Dielectric loss tangent.", default=None, minimum=0)


@dataclass(kw_only=True)
class StackupLayer:
    """One physical layer, listed top to bottom."""

    name: str = _f("Source name: 'F.Cu', 'dielectric 1', 'Top Solder Mask'...")
    kind: Literal["silk", "paste", "mask", "copper", "dielectric", "other"] = _f("Material role.")
    side: LayerSide = _f("'inner' for inner copper and dielectrics.")
    thickness: float | None = _f("mm (with sublayers: their sum).", default=None, minimum=0)
    material: str | None = _f(
        "'FR4', 'Polyimide'... (with sublayers of different materials: their names joined by ' + ').", default=None
    )
    color: str | None = _f("Colour as the source names it: a name ('Blue', 'Matte Black') or '#RRGGBB'.", default=None)
    epsilon_r: float | None = _f(
        "Dielectric constant (with sublayers: the series value, total thickness / sum(thickness_i / epsilon_r_i)).",
        default=None,
        exclusiveMinimum=0,
    )
    loss_tangent: float | None = _f(
        "Dielectric loss tangent (with sublayers: the thickness-weighted mean).", default=None, minimum=0
    )
    layer: str | None = _f("The Layer.id this physical layer is drawn by, if any.", default=None)
    dielectric: Literal["prepreg", "core"] | None = _f(
        "Dielectrics: prepreg or core, when the source says (KiCad type, IPC-2581 DIELPREG/DIELCORE).", default=None
    )
    sublayers: list[StackupSublayer] = _f(
        "Dielectrics the source splits into parts, top to bottom, the first included; empty otherwise.",
        factory=list,
    )
    frequency: float | None = _f(
        "Hz at which epsilon_r and loss_tangent are given (KiCad spec_frequency, ODB++ FrequencyVal).",
        default=None,
        exclusiveMinimum=0,
    )
    dielectric_model: Literal["constant", "djordjevic_sarkar"] | None = _f(
        "How epsilon_r/loss_tangent vary with frequency (KiCad dielectric_model).", default=None
    )
    locked: bool | None = _f("Thickness fixed for impedance control (KiCad '(thickness ... locked)').", default=None)
    tolerance: Tolerance | None = _f("Manufacturing tolerances, when the source gives them.", default=None)
    finished_thickness: float | None = _f(
        "Copper: plated (finished) thickness, mm, when it differs from the base foil.", default=None, minimum=0
    )
    roughness_rq: float | None = _f("Copper: RMS surface roughness, mm (0.0005 = 0.5 um).", default=None, minimum=0)
    roughness_rz: float | None = _f(
        "Copper: 10-point mean roughness Rz of the foil's bonded side, mm (datasheet value; the cannonball model).",
        default=None,
        minimum=0,
    )
    nodule_radius: float | None = _f(
        "Copper: Huray nodule (snowball) radius, mm, when the source gives Huray parameters.",
        default=None,
        exclusiveMinimum=0,
    )
    nodule_ratio: float | None = _f(
        "Copper: Huray surface ratio, nodules' area per flat area (N 4 pi a^2 / A_flat).", default=None, minimum=0
    )
    roughness_model: Literal["none", "hammerstad", "huray", "cannonball"] | None = _f(
        "Copper: roughness model for loss; default from the fields given (nodule_* huray, roughness_rz cannonball, "
        "roughness_rq hammerstad).",
        default=None,
    )
    conductivity: float | None = _f("Copper: conductivity, S/m.", default=None, exclusiveMinimum=0)
    etch_factor: float | None = _f(
        "Copper: etch factor (thickness / one side's undercut), for trapezoidal traces.", default=None, minimum=0
    )
    thickness_over_copper: float | None = _f(
        "Solder mask: thickness over the traces, mm, when it differs from 'thickness' (over the laminate).",
        default=None,
        minimum=0,
    )


@dataclass(kw_only=True)
class Stackup:
    """Board build: thickness, finish, colours, copper count and the physical layers."""

    thickness: float | None = _f("Finished board thickness, mm.", default=None, exclusiveMinimum=0)
    copper_layers: int | None = _f("Number of copper layers.", default=None, minimum=1)
    finish: str | None = _f("Surface finish as the source names it ('ENIG', 'HASL', 'OSP', 'None'...).", default=None)
    mask_color: SideValues = _f("Solder mask colour per side.", factory=SideValues)
    silk_color: SideValues = _f("Silkscreen colour per side.", factory=SideValues)
    layers: list[StackupLayer] = _f(
        "Physical layers top to bottom; empty when the source has no stackup.", factory=list
    )
    impedance_controlled: bool | None = _f(
        "The board is ordered impedance controlled (KiCad dielectric_constraints, gbrjob ImpedanceControlled).",
        default=None,
    )


@dataclass(kw_only=True)
class Layer:
    """One drawable layer (a Gerber, an ODB++ layer, a drill file...)."""

    id: str = _f("Unique id; KiCad canonical names where they apply ('F.Cu', 'In1.Cu', 'Edge.Cuts', 'PTH').")
    role: Literal[
        "copper", "mask", "paste", "silk", "outline", "drill", "fab", "courtyard", "adhesive", "user", "other"
    ] = _f("What the layer is.")
    side: LayerSide = _f("Board side; 'none' for outline/drill/user layers.")
    order: int = _f(
        "Stack position: copper 1..N top to bottom (Gerber L<n>); other layers ascend in drawing order.", minimum=0
    )
    files: list[str] = _f("SourceFile paths that draw this layer (usually one).", factory=list)
    format: Literal["gerber", "excellon", "odb", "ipc2581", "svg", "other"] = _f(
        "Format of the files.", default="gerber"
    )
    polarity: Literal["positive", "negative"] = _f("Gerber X2 file polarity.", default="positive")
    function: str | None = _f("Raw Gerber X2 %TF.FileFunction (or ODB++ layer type).", default=None)
    plated: bool | None = _f("Drill layers: plated (PTH) or not (NPTH); null when mixed.", default=None)


@dataclass(kw_only=True)
class Drill:
    """A hole in the board frame: round when x2/y2 are null, otherwise a slot from (x, y) to (x2, y2)."""

    x: float = _f("Centre (slot: first end), mm.")
    y: float = _f("Centre (slot: first end), mm.")
    diameter: float = _f("Finished hole diameter (slot: width), mm; 0 when the source writes a placeholder.", minimum=0)
    plated: bool = _f("Plated through hole.")
    x2: float | None = _f("Slot: second end.", default=None)
    y2: float | None = _f("Slot: second end.", default=None)
    tool: str | None = _f("Excellon tool, e.g. 'T3'.", default=None)
    function: Literal["via", "component", "mechanical"] | None = _f("Gerber X2 drill function.", default=None)
    filled: bool = _f("Filled/capped via (not punched in 3D).", default=False)
    layer: str | None = _f("The drill Layer.id this hole comes from.", default=None)


# ---------------------------------------------------------------------------------------------------------------------
# footprints (KiCad footprint frame: mm, y down, library orientation)


@dataclass(kw_only=True)
class PadDrill:
    """A pad's hole; an oval hole runs along its long axis."""

    shape: Literal["circle", "oval"] = _f("Hole shape.")
    size: Vec2 = _f("Width, height (circle: both the diameter), mm.")
    offset: Vec2 = _f("Copper offset relative to the hole (KiCad (drill (offset)) semantics).", default=(0.0, 0.0))


@dataclass(kw_only=True)
class Primitive:
    """A custom-pad primitive flattened to a filled loop, pad-local."""

    pts: list[Vec2] = _f("Loop points.", minItems=3)


@dataclass(kw_only=True)
class Aperture:
    """A mask or paste opening when it differs from the copper (magpie Aperture)."""

    shape: Literal["rect", "roundrect", "circle", "oval", "polygon"] = _f("Opening shape.")
    size: Vec2 = _f("Width, height, mm.")
    center: Vec2 = _f("Pad-local centre.", default=(0.0, 0.0))
    rotation: float = _f("Degrees, relative to the pad.", default=0.0)
    roundrect_ratio: float | None = _f("Corner radius / shorter side.", default=None, minimum=0, maximum=0.5)
    polygon: list[Vec2] | None = _f("Pad-local outline, for 'polygon'.", default=None)


@dataclass(kw_only=True)
class Pad:
    """boarddd's JS Pad (src/geom/pads.js, checked against pcbnew) plus magpie's extra fields."""

    number: str = _f("Pad number/name ('' for unnumbered pads).")
    type: Literal["smd", "thru_hole", "np_thru_hole", "connect"] = _f("KiCad pad type.")
    shape: Literal["circle", "rect", "oval", "roundrect", "chamfered_rect", "trapezoid", "custom"] = _f(
        "KiCad pad shape."
    )
    at: Vec3 = _f("x, y (footprint frame, y down) and rotation in degrees relative to the footprint.")
    size: Vec2 = _f("Width, height, mm.")
    layers: list[str] = _f("KiCad layer names, wildcards kept ('*.Cu', 'F.Paste'...).")
    drill: PadDrill | None = _f("The hole, for thru_hole/np_thru_hole.", default=None)
    offset: Vec2 | None = _f(
        "Copper shape offset of a pad without a hole (castellated SMD pads); pads with one keep it in drill.offset.",
        default=None,
    )
    roundrect_rratio: float | None = _f(
        "roundrect/chamfered: corner radius / shorter side.", default=None, minimum=0, maximum=0.5
    )
    chamfer_ratio: float | None = _f("chamfered_rect: chamfer / shorter side.", default=None, minimum=0, maximum=0.5)
    chamfer: list[Literal["top_left", "top_right", "bottom_left", "bottom_right"]] | None = _f(
        "chamfered_rect: which corners.", default=None
    )
    rect_delta: Vec2 | None = _f("trapezoid: KiCad rect_delta.", default=None)
    anchor: Literal["circle", "rect"] | None = _f("custom: anchor shape.", default=None)
    primitives: list[Primitive] | None = _f("custom: primitives as filled loops, pad-local.", default=None)
    solder_mask_margin: float | None = _f("Pad mask expansion override, mm.", default=None)
    solder_paste_margin: float | None = _f("Pad paste expansion override, mm.", default=None)
    function: str | None = _f(
        "Pad function/net role when the source says ('ground', 'heatsink', 'fiducial'...).", default=None
    )
    paste: list[Aperture] | None = _f(
        "Paste openings when they differ from the copper; null = unknown/as copper, [] = no paste.", default=None
    )
    mask: Aperture | None = _f("Mask opening when it differs from copper + margin.", default=None)
    holes: list[PadDrill] | None = _f("Extra holes (rare; multi-drill pads from ODB++/IPC-2581).", default=None)


@dataclass(kw_only=True)
class Graphic:
    """A footprint drawing (silk, fab, courtyard): arcs/circles flattened, footprint frame."""

    layer: str = _f("KiCad layer name ('F.SilkS', 'F.Fab', 'F.CrtYd'...).")
    kind: Literal["line", "rect", "circle", "arc", "poly", "curve"] = _f("Original primitive.")
    pts: list[Vec2] = _f("Points.", minItems=2)
    width: float = _f("Stroke width, mm (0 for filled-only).", minimum=0)
    closed: bool = _f("Closed loop.", default=False)
    filled: bool = _f("Filled.", default=False)


@dataclass(kw_only=True)
class Footprint:
    """A footprint definition, shared by components that use it (library orientation, top side)."""

    name: str = _f("Library id, e.g. 'Resistor_SMD:R_0603_1608Metric' (the key in Board.footprints).")
    pads: list[Pad] = _f("Pads.", factory=list)
    graphics: list[Graphic] = _f("Silk/fab/courtyard drawings.", factory=list)
    attr: list[str] = _f(
        "KiCad footprint attributes as written in the library ('smd', 'through_hole', ...).", factory=list
    )


# ---------------------------------------------------------------------------------------------------------------------
# components


@dataclass(kw_only=True)
class PartNumber:
    """A manufacturer part number."""

    mpn: str = _f("Manufacturer part number.")
    manufacturer: str | None = _f("Manufacturer.", default=None)


@dataclass(kw_only=True)
class ModelRef:
    """A 3D model placed on a component (KiCad (model ...) semantics)."""

    path: str = _f("Model path as the source writes it (env vars like ${KICAD9_3DMODEL_DIR} kept).")
    offset: Vec3 = _f("mm, footprint frame (KiCad 3D viewer: x right, y up, z up).", default=(0.0, 0.0, 0.0))
    rotate: Vec3 = _f("Degrees about x, y, z.", default=(0.0, 0.0, 0.0))
    scale: Vec3 = _f("Scale.", default=(1.0, 1.0, 1.0))
    hide: bool = _f("Hidden in the 3D view.", default=False)


@dataclass(kw_only=True)
class Component:
    """A placed part."""

    ref: str = _f("Reference designator.")
    side: Side = _f("Mounting side.")
    x: float = _f("Footprint origin, board frame, mm.")
    y: float = _f("Footprint origin, board frame, mm.")
    rotation: float = _f(
        "Degrees counter-clockwise seen from the top, as KiCad stores the footprint orientation and its pos "
        "file prints it (bottom parts too; see docs/model.md for the placement transform)."
    )
    value: str | None = _f("Value field.", default=None)
    footprint: str | None = _f(
        "Footprint name: a key in Board.footprints when present, else the source's name.", default=None
    )
    mount: Literal["smd", "tht", "other"] | None = _f("Assembly technology.", default=None)
    populate: bool = _f("False for DNP (do not populate).", default=True)
    in_bom: bool = _f("Listed in the BOM.", default=True)
    in_pos: bool = _f("Listed in placement (pick-and-place) files.", default=True)
    mpn: list[PartNumber] = _f("Manufacturer part numbers (alternates allowed).", factory=list)
    models: list[ModelRef] = _f("3D models.", factory=list)
    height: float | None = _f("Body height above the board, mm, when known.", default=None, minimum=0)
    attributes: dict[str, str] = _f("Other source fields (KiCad properties, BOM columns).", factory=dict)


@dataclass(kw_only=True)
class PanelInstance:
    """One board copy in a panel, board frame of the panel."""

    x: float = _f("Offset, mm.")
    y: float = _f("Offset, mm.")
    rotation: float = _f("Degrees counter-clockwise.", default=0.0)


@dataclass(kw_only=True)
class Panel:
    """Panelisation, when the source is a panel of identical boards."""

    instances: list[PanelInstance] = _f("Board copies.", factory=list)


# ---------------------------------------------------------------------------------------------------------------------
# nets and net classes (impedance targets)


@dataclass(kw_only=True)
class Net:
    """An electrical net."""

    name: str = _f("Net name as the source writes it ('/USB/D+', 'GND').")
    net_class: str | None = _f(
        "The NetClass.name it belongs to ('Default' when the source assigns none).", default=None
    )
    pair: str | None = _f(
        "The partner net of a differential pair (KiCad pairs '+'/'-' and 'P'/'N' suffixes).", default=None
    )


@dataclass(kw_only=True)
class ImpedanceLayer:
    """Geometry a target applies to on one signal layer (KiCad tuning profile layer entry, IPC-2581 Impedance)."""

    layer: str = _f("Signal layer ('F.Cu', 'In1.Cu').")
    width: float | None = _f("Track width, mm.", default=None, minimum=0)
    gap: float | None = _f("Differential pair gap, mm.", default=None, minimum=0)
    ref_top: str | None = _f("Reference plane above, if any.", default=None)
    ref_bottom: str | None = _f("Reference plane below, if any.", default=None)


@dataclass(kw_only=True)
class ImpedanceTarget:
    """A controlled-impedance target."""

    kind: Literal["single", "differential"] = _f("Single-ended Z0 or differential Zdiff.")
    target: float = _f("Ohms.", exclusiveMinimum=0)
    tolerance_pct: float | None = _f("Allowed deviation, percent (10 = +-10 %); null = unspecified.", default=None)
    common_mode: float | None = _f(
        "Differential: common-mode target, ohms, when given (class names like BAL_D90_C30).", default=None
    )
    structure: Literal["microstrip", "stripline", "coplanar", "coplanar_grounded"] | None = _f(
        "Transmission-line structure, when the source says (MS, SL, CPW, CPWG).", default=None
    )
    source: Literal["tuning_profile", "ipc2581", "odbpp", "name", "user"] = _f(
        "Where the target comes from: a KiCad tuning profile, an IPC-2581/ODB++ spec, the class name convention "
        "(SE_50_CP, DP_90_MS, 90ohm...) or a user entry."
    )
    layers: list[ImpedanceLayer] = _f("Per-layer geometry, when the source gives it.", factory=list)


@dataclass(kw_only=True)
class NetClass:
    """A net class (KiCad .kicad_pro net_settings) with its design rules and impedance target."""

    name: str = _f("Class name.")
    nets: list[str] = _f("Net names in the class.", factory=list)
    patterns: list[str] = _f("Assignment patterns (KiCad netclass_patterns, wildcards).", factory=list)
    track_width: float | None = _f("mm.", default=None, minimum=0)
    clearance: float | None = _f("mm.", default=None, minimum=0)
    diff_pair_width: float | None = _f("mm.", default=None, minimum=0)
    diff_pair_gap: float | None = _f("mm.", default=None, minimum=0)
    via_diameter: float | None = _f("mm.", default=None, minimum=0)
    via_drill: float | None = _f("mm.", default=None, minimum=0)
    priority: int | None = _f("KiCad priority (lower wins; Default is the largest).", default=None)
    tuning_profile: str | None = _f("KiCad 10 tuning profile name, when set.", default=None)
    impedance: ImpedanceTarget | None = _f("Controlled-impedance target, when known.", default=None)


# ---------------------------------------------------------------------------------------------------------------------
# the board


@dataclass(kw_only=True)
class Board:
    """One board revision."""

    schema: Literal["boarddd/board@1"] = _f("Format id.", default=SCHEMA_ID, required=True)
    name: str = _f("Board name (project or file stem).")
    revision: str | None = _f("Design revision as the source states it.", default=None)
    source: Source = _f("Provenance.")
    units: Literal["mm"] = _f("Length unit.", default="mm", required=True)
    frame: Literal["board"] = _f("Coordinate frame: x right, y up, seen from the top.", default="board", required=True)
    origin: Origin = _f("Source origins (board frame).", factory=Origin)
    outline: Outline | None = _f("Board edge; null when the source has none.", default=None)
    stackup: Stackup = _f("Build: thickness, finish, colours, physical layers.", factory=Stackup)
    layers: list[Layer] = _f("Drawable layers.", factory=list)
    drills: list[Drill] = _f("Holes.", factory=list)
    footprints: dict[str, Footprint] = _f("Footprint definitions by library id.", factory=dict)
    components: list[Component] = _f("Placed parts.", factory=list)
    panel: Panel | None = _f("Panel, when the source is one.", default=None)
    nets: list[Net] = _f("Nets, when the source has them (KiCad, ODB++, IPC-2581, Gerber X2).", factory=list)
    net_classes: list[NetClass] = _f("Net classes with their rules and impedance targets.", factory=list)
    warnings: list[str] = _f("What the reader could not represent or had to guess.", factory=list)
    meta: dict[str, Any] = _f("Free-form application data (not interpreted by boarddd).", factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    def to_json(self, indent: int | None = 1) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Board:
        return from_dict(cls, data)

    @classmethod
    def from_json(cls, text: str) -> Board:
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------------------------------------------------
# (de)serialisation


def to_dict(obj: Any) -> Any:
    """Dataclasses -> plain JSON values (tuples become lists; every field is written, None as null)."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls, globalns=vars(sys.modules[cls.__module__]), localns={"Vec2": Vec2, "Vec3": Vec3})


def from_dict(cls: type, data: Any) -> Any:
    """Plain JSON values -> dataclasses. Strict about keys; validate first for good error messages."""
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__}: expected an object")
    hints = _hints(cls)
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise TypeError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
    return cls(**{k: _convert(hints[k], v) for k, v in data.items()})


def _convert(tp: Any, v: Any) -> Any:
    origin, args = get_origin(tp), get_args(tp)
    if v is None:
        return None
    if origin is Union or (origin is not None and type(None) in args):
        inner = [a for a in args if a is not type(None)]
        return _convert(inner[0], v)
    if is_dataclass(tp):
        return from_dict(tp, v)
    if origin is list:
        return [_convert(args[0], x) for x in v]
    if origin is tuple:
        return tuple(_convert(a, x) for a, x in zip(args, v, strict=True))
    if origin is dict:
        return {k: _convert(args[1], x) for k, x in v.items()}
    if tp is float and isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    return v


# ---------------------------------------------------------------------------------------------------------------------
# CLI: python -m boarddd.model --schema | --write | --check


def main(argv: list[str] | None = None) -> int:
    from . import _codegen

    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else _codegen.repo_root()
    outputs = _codegen.outputs()
    if "--schema" in argv:
        sys.stdout.write(outputs["schema/board.schema.json"])
        return 0
    if "--write" in argv:
        for rel, text in outputs.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
            print(f"wrote {rel}")
        return 0
    if "--check" in argv:
        stale = [
            rel for rel, text in outputs.items() if not (root / rel).exists() or (root / rel).read_text("utf-8") != text
        ]
        for rel in stale:
            print(f"{rel} is out of date: run `python -m boarddd.model --write`", file=sys.stderr)
        return 1 if stale else 0
    print("usage: python -m boarddd.model --schema | --write | --check [--root REPO]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
