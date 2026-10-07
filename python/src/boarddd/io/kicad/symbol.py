"""KiCad symbol libraries (``.kicad_sym``): parse only (symbols, units, pins, graphics as plain dicts).

Symbol library coordinates are mm, y up, as the file holds them. Rendering (SVG) is phase F5's
``render/svg.py``.

Source: kipr ``kipr/library/render/sym.py`` (kipr main ``f632b3f``), parse half: ``Symbol``, ``sub_unit``,
``parse_library``; ``read_kicad_sym`` added.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .sexpr import Node, load

__all__ = ["Symbol", "read_kicad_sym", "parse_library", "sub_unit", "PIN_TYPES"]

PIN_TYPES = {
    "input",
    "output",
    "bidirectional",
    "tri_state",
    "passive",
    "free",
    "unspecified",
    "power_in",
    "power_out",
    "open_collector",
    "open_emitter",
    "no_connect",
}


def arc_mid_from_center(center, start, angle_deg):
    """Old-format arcs are centre + start + angle; return (start, mid, end)."""
    cx, cy = center
    sx, sy = start
    rad = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    a = math.radians(angle_deg)
    mid = (cx + rad * math.cos(a0 + a / 2), cy + rad * math.sin(a0 + a / 2))
    end = (cx + rad * math.cos(a0 + a), cy + rad * math.sin(a0 + a))
    return (sx, sy), mid, end


def _xy(node, default=(0.0, 0.0)):
    if node is None:
        return default
    v = node.nums()
    return (v[0], v[1]) if len(v) >= 2 else default


def _at(node):
    at = node.child("at")
    if at is None:
        return 0.0, 0.0, 0.0
    v = at.nums() + [0, 0, 0]
    return v[0], v[1], v[2]


def _hidden(node: Node) -> bool:
    if node.flag("hide"):
        return True
    eff = node.child("effects")
    return bool(eff is not None and eff.flag("hide"))


def _font(node: Node):
    eff = node.child("effects")
    size = (1.27, 1.27)
    justify = []
    bold = False
    if eff is not None:
        fn = eff.child("font")
        if fn is not None:
            s = fn.nums("size") or [1.27, 1.27]
            size = (s[0], s[1] if len(s) > 1 else s[0])
            bold = fn.flag("bold")
        j = eff.child("justify")
        if j is not None:
            justify = [str(a) for a in j.atoms()]
    return size, justify, bold


def sub_unit(name: str, parent: str):
    """Parse 'PARENT_U_B' -> (unit, body_style)."""
    m = re.match(re.escape(parent) + r"_(\d+)_(\d+)$", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r".*_(\d+)_(\d+)$", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


class Symbol:
    def __init__(self, node: Node, library: dict[str, Node] | None = None):
        self.node = node
        self.name = str(node.arg(0, ""))
        self.extends = node.value("extends")
        self.properties: dict[str, str] = {}
        self.fields = []  # property dicts incl. position
        pn = node.child("pin_names")
        self.pin_name_offset = pn.num("offset", 0.508) if pn is not None else 0.508
        self.pin_names_hidden = bool(pn is not None and pn.flag("hide"))
        pnum = node.child("pin_numbers")
        self.pin_numbers_hidden = bool(pnum is not None and pnum.flag("hide"))
        self.power = node.child("power") is not None
        self.unit_names: dict[int, str] = {}
        for p in node.children("property"):
            key, val = str(p.arg(0, "")), str(p.arg(1, ""))
            self.properties[key] = val
            x, y, a = _at(p)
            size, justify, bold = _font(p)
            self.fields.append(
                dict(
                    key=key,
                    value=val,
                    x=x,
                    y=y,
                    angle=a,
                    size=size,
                    justify=justify,
                    hidden=_hidden(p) or key.startswith("ki_"),
                    bold=bold,
                )
            )
        # graphics source: parent symbol for derived ('extends') symbols
        gsrc = node
        if self.extends and library and self.extends in library:
            gsrc = library[self.extends]
            parent = Symbol(gsrc)
            for k, v in parent.properties.items():
                self.properties.setdefault(k, v)
            if not any(fl["key"] in ("Reference", "Value") for fl in self.fields):
                self.fields = parent.fields
            self.pin_name_offset = parent.pin_name_offset
            self.pin_names_hidden = parent.pin_names_hidden
            self.pin_numbers_hidden = parent.pin_numbers_hidden
        self.gname = str(gsrc.arg(0, ""))
        self.shapes = []  # dicts with unit, body
        self.pins = []
        self._collect(gsrc, 0, 1)
        for sub in gsrc.children("symbol"):
            u, b = sub_unit(str(sub.arg(0, "")), self.gname)
            un = sub.value("unit_name")
            if un:
                self.unit_names[u] = str(un)
            self._collect(sub, u, b)

    # ------------------------------------------------------------------
    def _collect(self, node: Node, unit: int, body: int):
        for c in node.children():
            n = c.name
            if n == "pin":
                self.pins.append(self._pin(c, unit, body))
            elif n in ("rectangle", "circle", "arc", "polyline", "bezier", "text", "text_box"):
                s = self._shape(c)
                if s:
                    s["unit"], s["body"] = unit, body
                    self.shapes.append(s)

    def _shape(self, c: Node):
        st = c.child("stroke")
        width = st.num("width", 0) if st is not None else 0
        fill = c.child("fill")
        ftype = str(fill.value("type", "none")) if fill is not None else "none"
        fcolor = None
        if fill is not None and fill.child("color") is not None:
            v = fill.child("color").nums()
            if len(v) >= 3:
                fcolor = f"rgb({int(v[0])},{int(v[1])},{int(v[2])})"
        s = dict(kind=c.name, width=width, fill=ftype, fill_color=fcolor)
        if c.name == "rectangle":
            s["start"], s["end"] = _xy(c.child("start")), _xy(c.child("end"))
        elif c.name == "circle":
            s["center"], s["r"] = _xy(c.child("center")), c.num("radius", 0)
        elif c.name == "arc":
            if c.child("mid") is not None:
                s["arc"] = (_xy(c.child("start")), _xy(c.child("mid")), _xy(c.child("end")))
            else:
                rad = c.child("radius")
                if rad is not None:
                    ang = rad.nums("angles") or [0, 90]
                    s["arc"] = arc_mid_from_center(_xy(rad.child("at")), _xy(c.child("start")), ang[1] - ang[0])
                else:
                    return None
        elif c.name in ("polyline", "bezier"):
            s["pts"] = [tuple(p.nums()[:2]) for p in (c.child("pts") or Node()).children("xy")]
        elif c.name in ("text", "text_box"):
            s["text"] = str(c.arg(0, ""))
            x, y, a = _at(c)
            s.update(x=x, y=y, angle=a)
            s["size"], s["justify"], s["bold"] = _font(c)
            s["hidden"] = _hidden(c)
            if c.name == "text_box":
                s["box_size"] = c.nums("size") or [0, 0]
        return s

    def _pin(self, c: Node, unit: int, body: int):
        x, y, a = _at(c)
        name = c.child("name")
        number = c.child("number")
        alts = [str(al.arg(0, "")) for al in c.children("alternate")]
        return dict(
            type=str(c.arg(0, "")),
            shape=str(c.arg(1, "line")),
            x=x,
            y=y,
            angle=a,
            length=c.num("length", 2.54),
            name=str(name.arg(0, "")) if name is not None else "",
            number=str(number.arg(0, "")) if number is not None else "",
            name_size=_font(name)[0] if name is not None else (1.27, 1.27),
            num_size=_font(number)[0] if number is not None else (1.27, 1.27),
            hidden=c.flag("hide"),
            unit=unit,
            body=body,
            alternates=alts,
            line=c.line_start,
        )

    # ------------------------------------------------------------------
    @property
    def unit_count(self) -> int:
        units = {s["unit"] for s in self.shapes} | {p["unit"] for p in self.pins}
        units.discard(0)
        return max(units) if units else 1

    def has_demorgan(self) -> bool:
        return any(s["body"] == 2 for s in self.shapes) or any(p["body"] == 2 for p in self.pins)

    def stats(self) -> dict:
        pins = [p for p in self.pins if p["body"] in (0, 1)]
        numbers = [p["number"] for p in pins]
        dup = sorted({n for n in numbers if numbers.count(n) > 1})
        by_type: dict[str, int] = {}
        for p in pins:
            by_type[p["type"]] = by_type.get(p["type"], 0) + 1
        return {
            "pin_count": len(pins),
            "unique_pin_numbers": len(set(numbers)),
            "duplicate_pin_numbers": dup,
            "units": self.unit_count,
            "unit_names": {str(k): v for k, v in self.unit_names.items()},
            "has_demorgan": self.has_demorgan(),
            "power_symbol": self.power,
            "extends": self.extends,
            "pin_types": by_type,
            "hidden_pins": sum(1 for p in pins if p["hidden"]),
            "pins": [
                {
                    "number": p["number"],
                    "name": p["name"],
                    "type": p["type"],
                    "shape": p["shape"],
                    "unit": p["unit"],
                    "hidden": p["hidden"],
                    "pos": [p["x"], p["y"]],
                    "angle": p["angle"],
                    "length": p["length"],
                    "alternates": p["alternates"],
                }
                for p in sorted(pins, key=lambda p: (p["unit"], _natkey(p["number"])))
            ],
            "pin_names_hidden": self.pin_names_hidden,
            "pin_numbers_hidden": self.pin_numbers_hidden,
        }


def _natkey(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def parse_library(root: Node) -> dict[str, Node]:
    return {str(s.arg(0, "")): s for s in root.children("symbol")}


def read_kicad_sym(source: str | Path) -> dict[str, Symbol]:
    """Every symbol of a library (path or text), by name; derived symbols ('extends') resolved."""
    root = load(source)
    if root.name != "kicad_symbol_lib":
        raise ValueError("not a .kicad_sym file")
    lib = parse_library(root)
    return {name: Symbol(node, lib) for name, node in lib.items()}
