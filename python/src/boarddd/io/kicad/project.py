"""KiCad project files (``.kicad_pro``): net classes, their net assignments and impedance targets.

Targets, in priority order (impedance report section 2/4.2):

1. a KiCad 10 tuning profile linked from the class (``net_settings.classes[].tuning_profile`` ->
   ``tuning_profiles.tuning_profiles_impedance_geometric[]``: target Z, single/differential, per-layer
   width, gap and reference planes);
2. the class name convention: ``SE_50``, ``DP_90``, ``BAL_D90_C30`` with an optional structure suffix
   (``_MS`` microstrip, ``_SL`` stripline, ``_CP``/``_CPW`` coplanar, ``_CPWG``/``_GCPW`` grounded
   coplanar), or a value with a unit (``50ohm``, ``85Ohm-diff_PCIE``, ``zse_50r``); differential when the name
   says DP/diff/BAL or a ``D<n>`` value, or when every net of the class is half of a differential pair.

KiCad 10 writes tuning-profile widths, gaps and delays as integers in its internal unit (nm) and the profile
``type`` as 0 (single) / 1 (differential) (``common/project/tuning_profiles.cpp``, KiCad 10.0); both are read
here, as are mm values and 'single'/'differential' strings.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ... import model as m

__all__ = ["KicadProject", "read_kicad_pro", "assign_nets", "impedance_from_name"]

_RULES = ("track_width", "clearance", "diff_pair_width", "diff_pair_gap", "via_diameter", "via_drill")


@dataclass
class KicadProject:
    """What boarddd uses from a ``.kicad_pro``."""

    classes: list[dict] = field(default_factory=list)  # net_settings.classes, raw
    patterns: list[tuple[str, str]] = field(default_factory=list)  # (pattern, class)
    assignments: dict[str, list[str]] = field(default_factory=dict)  # net -> [class]
    tuning_profiles: dict[str, dict] = field(default_factory=dict)  # profile_name -> raw profile
    data: dict = field(default_factory=dict)


def read_kicad_pro(source: str | Path | dict) -> KicadProject:
    """A ``.kicad_pro`` (path, JSON text or the parsed dict)."""
    if isinstance(source, dict):
        data = source
    else:
        text = source if isinstance(source, str) and source.lstrip().startswith("{") else Path(source).read_text("utf-8")
        data = json.loads(text)
    ns = data.get("net_settings") or {}
    patterns = [
        (str(p.get("pattern", "")), str(p.get("netclass", "")))
        for p in ns.get("netclass_patterns") or []
        if isinstance(p, dict) and p.get("pattern")
    ]
    assignments = {}
    for net, classes in (ns.get("netclass_assignments") or {}).items():
        assignments[str(net)] = [classes] if isinstance(classes, str) else [str(c) for c in classes or []]
    tp = (data.get("tuning_profiles") or {}).get("tuning_profiles_impedance_geometric") or []
    return KicadProject(
        classes=[c for c in ns.get("classes") or [] if isinstance(c, dict) and c.get("name")],
        patterns=patterns,
        assignments=assignments,
        tuning_profiles={str(p["profile_name"]): p for p in tp if isinstance(p, dict) and p.get("profile_name")},
        data=data,
    )


# ---------------------------------------------------------------------------------------------------------------------
# targets


def _length(v) -> float | None:
    """A tuning-profile length: KiCad's integer nm, or mm when it is already small."""
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return round(v / 1e6, 6) if v >= 1000 else v


def _ref(v) -> str | None:
    v = str(v or "")
    return None if v in ("", "UNDEFINED", "UNSELECTED", "undefined") else v


def impedance_from_profile(profile: dict) -> m.ImpedanceTarget | None:
    """A KiCad 10 tuning profile as an ImpedanceTarget (None without a target impedance)."""
    try:
        target = float(profile.get("target_impedance") or 0)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    kind = profile.get("type")
    differential = kind in (1, "1", "differential", "DIFFERENTIAL")
    layers = [
        m.ImpedanceLayer(
            layer=str(e.get("signal_layer")),
            width=_length(e.get("width")),
            gap=_length(e.get("diff_pair_gap")) if differential else None,
            ref_top=_ref(e.get("top_reference_layer")),
            ref_bottom=_ref(e.get("bottom_reference_layer")),
        )
        for e in profile.get("layer_entries") or []
        if isinstance(e, dict) and e.get("signal_layer")
    ]
    return m.ImpedanceTarget(
        kind="differential" if differential else "single", target=target, source="tuning_profile", layers=layers
    )


_STRUCTURE = {
    "ms": "microstrip",
    "microstrip": "microstrip",
    "sl": "stripline",
    "stripline": "stripline",
    "cp": "coplanar",
    "cpw": "coplanar",
    "cpwg": "coplanar_grounded",
    "gcpw": "coplanar_grounded",
}
_DIFF_WORDS = {"dp", "diff", "differential", "bal", "dpair"}
_NUM = r"(\d+(?:\.\d+)?)"


def impedance_from_name(name: str) -> m.ImpedanceTarget | None:
    """The class name convention: SE_50_CP, DP_90_MS, BAL_D90_C30_CPWG, 50ohm, 85Ohm-diff_PCIE, zse_50r."""
    tokens = [t for t in re.split(r"[_\-\s]+", name.lower()) if t]
    target = common = None
    differential = any(t in _DIFF_WORDS or t.startswith("diff") or t.endswith("diff") for t in tokens)
    structure = None
    lead = False  # the previous token announced a value (SE, DP, Z...)
    for t in tokens:
        if t in _STRUCTURE and structure is None:
            structure = _STRUCTURE[t]
        if target is None:
            if mt := re.fullmatch(_NUM + r"(?:ohms?|r|Ω)", t):
                target = float(mt.group(1))
            elif mt := re.fullmatch(_NUM + r"ohms?(?:diff\w*)?", t):
                target = float(mt.group(1))
            elif lead and (mt := re.fullmatch(_NUM, t)):
                target = float(mt.group(1))
            elif mt := re.fullmatch(r"d" + _NUM, t):
                target, differential = float(mt.group(1)), True
            elif mt := re.fullmatch(r"(?:se|z|zse|zo|z0)" + _NUM, t):
                target = float(mt.group(1))
        elif common is None and (mt := re.fullmatch(r"c" + _NUM, t)):
            common = float(mt.group(1))
        lead = t in ("se", "dp", "z", "zse", "zo", "z0", "zdiff", "diff", "bal")
    if target is None or not 5 <= target <= 500:
        return None
    return m.ImpedanceTarget(
        kind="differential" if differential else "single",
        target=target,
        common_mode=common if differential else None,
        structure=structure,
        source="name",
    )


# ---------------------------------------------------------------------------------------------------------------------
# classes and assignment


def _matches(pattern: str, net: str) -> bool:
    if fnmatch.fnmatchcase(net, pattern):
        return True
    try:
        return re.fullmatch(pattern, net) is not None
    except re.error:
        return False


def assign_nets(nets: list[m.Net], project: KicadProject, warnings: list[str] | None = None) -> list[m.NetClass]:
    """Net classes from the project, with their nets; sets each Net.net_class (the effective class: the
    highest-priority one among its explicit assignments and matching patterns, else Default)."""
    warnings = warnings if warnings is not None else []
    raw = {c["name"]: c for c in project.classes}
    default = raw.get("Default", {})
    classes: dict[str, m.NetClass] = {}
    for name, c in raw.items():
        nc = m.NetClass(name=name)
        for k in _RULES:
            v = c.get(k, default.get(k))  # KiCad 8+: a class without a value takes Default's
            nc.__setattr__(k, float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        nc.priority = c.get("priority") if isinstance(c.get("priority"), int) else None
        nc.tuning_profile = str(c["tuning_profile"]) if c.get("tuning_profile") else None
        if nc.tuning_profile:
            profile = project.tuning_profiles.get(nc.tuning_profile)
            if profile is None:
                warnings.append(f"net class {name}: tuning profile {nc.tuning_profile!r} is not in the project")
            else:
                nc.impedance = impedance_from_profile(profile)
        if nc.impedance is None and name != "Default":
            nc.impedance = impedance_from_name(name)
        classes[name] = nc
    for pattern, cls in project.patterns:
        if cls in classes:
            classes[cls].patterns.append(pattern)

    def rank(cls: str) -> int:
        p = classes[cls].priority
        return p if p is not None else 2**31

    for net in nets:
        mine = [c for c in project.assignments.get(net.name, []) if c in classes]
        mine += [cls for pattern, cls in project.patterns if cls in classes and _matches(pattern, net.name)]
        mine = list(dict.fromkeys(mine))
        for c in mine:
            classes[c].nets.append(net.name)
        if mine:
            net.net_class = min(mine, key=rank)
        elif "Default" in classes:
            net.net_class = "Default"
            classes["Default"].nets.append(net.name)
    # a class named only by its value ('90ohm') whose nets are all differential pairs is a differential target
    paired = {n.name for n in nets if n.pair}
    for nc in classes.values():
        imp = nc.impedance
        if imp and imp.source == "name" and imp.kind == "single" and nc.nets and set(nc.nets) <= paired:
            imp.kind = "differential"
    for c in project.assignments.values():
        for cls in c:
            if cls not in classes:
                warnings.append(f"netclass_assignments names an unknown class {cls!r}")
                break
    return list(classes.values())
