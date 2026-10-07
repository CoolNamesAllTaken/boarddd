"""KiCad symbols drawn as SVG (per unit, shared layout across revisions).

Works on ``boarddd.io.kicad.Symbol`` (the parse). Output is byte-for-byte kipr's
(python/tests/render/test_render_kipr_goldens.py). Symbol library coordinates are y-up (mm); everything is
flipped to SVG's y-down here.

Source: kipr ``kipr/library/render/sym.py`` at kipr main ``f632b3f`` (boarddd phase F5), its render half; the parse half
moved to ``boarddd.io.kicad.symbol`` in phase F2.
"""

from __future__ import annotations

import math
import re

from ..io.kicad.symbol import PIN_TYPES, Symbol, parse_library, sub_unit
from .geom import BBox, arc_path, arc_points, f, text_el, text_extent

__all__ = [
    "Symbol",
    "parse_library",
    "sub_unit",
    "PIN_TYPES",
    "BACKGROUND",
    "C_BODY_BG",
    "unit_layout",
    "shared_layout",
    "render_symbol",
]

# KiCad default schematic colours
C_BODY = "#840000"
C_BODY_BG = "#FFFFC2"
C_PIN = "#840000"
C_PIN_NAME = "#006464"
C_PIN_NUM = "#A90000"
C_TEXT = "#000084"
C_FIELD = "#006464"
C_REF = "#006464"
C_HIDDEN = "#949494"
BACKGROUND = "#F5F4EF"


def _natkey(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------------------
# rendering (per unit, symbol coordinates; y flipped on output)
# ---------------------------------------------------------------------------


def _fill_attr(s):
    if s["fill"] == "background":
        return C_BODY_BG
    if s["fill"] == "outline":
        return C_BODY
    if s["fill"] == "color" and s["fill_color"]:
        return s["fill_color"]
    return "none"


def _sw(w):
    return f(w if w and w > 0 else 0.1524)


def _unit_elements(sym: Symbol, unit: int, bb: BBox) -> list[str]:
    els = []
    shapes = [s for s in sym.shapes if s["unit"] in (0, unit) and s["body"] in (0, 1)]
    pins = [p for p in sym.pins if p["unit"] in (0, unit) and p["body"] in (0, 1)]
    # filled backgrounds first, like KiCad
    shapes.sort(key=lambda s: 0 if s["fill"] == "background" else 1)
    for s in shapes:
        k = s["kind"]
        fill = _fill_attr(s)
        st = f'stroke="{C_BODY}" stroke-width="{_sw(s["width"])}" stroke-linecap="round" stroke-linejoin="round"'
        w = (s["width"] or 0.15) / 2
        if k == "rectangle":
            (x0, y0), (x1, y1) = s["start"], s["end"]
            y0, y1 = -y0, -y1
            els.append(
                f'<rect x="{f(min(x0, x1))}" y="{f(min(y0, y1))}" width="{f(abs(x1 - x0))}" '
                f'height="{f(abs(y1 - y0))}" fill="{fill}" {st}/>'
            )
            bb.add(x0, y0, w)
            bb.add(x1, y1, w)
        elif k == "circle":
            cx, cy = s["center"]
            els.append(f'<circle cx="{f(cx)}" cy="{f(-cy)}" r="{f(s["r"])}" fill="{fill}" {st}/>')
            bb.add(cx - s["r"], -cy - s["r"], w)
            bb.add(cx + s["r"], -cy + s["r"], w)
        elif k == "arc":
            a, m, e = [(p[0], -p[1]) for p in s["arc"]]
            d = arc_path(a, m, e)
            if fill != "none":
                els.append(f'<path d="{d}Z" fill="{fill}" stroke="none"/>')
            els.append(f'<path d="{d}" fill="none" {st}/>')
            for p in arc_points(a, m, e):
                bb.add(p[0], p[1], w)
        elif k in ("polyline", "bezier"):
            pts = [(x, -y) for x, y in s["pts"]]
            if not pts:
                continue
            if k == "bezier" and len(pts) == 4:
                d = f"M{f(pts[0][0])} {f(pts[0][1])}C" + " ".join(f"{f(x)} {f(y)}" for x, y in pts[1:])
            else:
                d = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts)
            if fill != "none":
                d += "Z"
            els.append(f'<path d="{d}" fill="{fill}" {st}/>')
            for x, y in pts:
                bb.add(x, y, w)
        elif k in ("text", "text_box"):
            if s["hidden"]:
                continue
            h, wd = s["size"]
            ang = s["angle"] / 10 if abs(s["angle"]) > 360 else s["angle"]
            j = s["justify"]
            anchor = "start" if "left" in j else "end" if "right" in j else "middle"
            valign = "top" if "top" in j else "bottom" if "bottom" in j else "center"
            x, y = s["x"], -s["y"]
            if k == "text_box":
                bw, bh = (s["box_size"] + [0, 0])[:2]
                els.append(f'<rect x="{f(x)}" y="{f(y)}" width="{f(bw)}" height="{f(bh)}" fill="{fill}" {st}/>')
                bb.add(x, y)
                bb.add(x + bw, y + bh)
                anchor, valign = "start", "top"
                x += 0.5
                y += 0.5
            els.append(text_el(s["text"], x, y, h, wd, ang, C_TEXT, anchor, valign, bold=s["bold"]))
            text_extent(s["text"], x, y, h, wd, ang, anchor, valign, bb)
    for p in pins:
        els += _pin_elements(sym, p, bb)
    return els


def _pin_elements(sym: Symbol, p, bb: BBox) -> list[str]:
    if p["hidden"]:
        return []
    x, y = p["x"], -p["y"]
    a = math.radians(p["angle"])
    dx, dy = math.cos(a), -math.sin(a)  # screen direction from connection point to body
    L = p["length"]
    ex, ey = x + dx * L, y + dy * L
    els = []
    shape = p["shape"]
    line_start = (x, y)
    line_end = (ex, ey)
    if shape in ("inverted", "inverted_clock"):
        # bubble sits against the body
        r = 0.3175 * 2
        cxb, cyb = ex - dx * r, ey - dy * r
        els.append(
            f'<circle cx="{f(cxb)}" cy="{f(cyb)}" r="{f(r)}" fill="none" stroke="{C_PIN}" stroke-width="0.1524"/>'
        )
        line_end = (ex - dx * 2 * r, ey - dy * 2 * r)
    els.append(
        f'<path d="M{f(line_start[0])} {f(line_start[1])}L{f(line_end[0])} {f(line_end[1])}" '
        f'stroke="{C_PIN}" stroke-width="0.1524" stroke-linecap="round"/>'
    )
    if shape in ("clock", "inverted_clock", "clock_low", "edge_clock_high"):
        s = 0.635
        nx, ny = -dy, dx
        pts = [(ex + nx * s, ey + ny * s), (ex + dx * s, ey + dy * s), (ex - nx * s, ey - ny * s)]
        els.append(
            '<path d="M'
            + "L".join(f"{f(px)} {f(py)}" for px, py in pts)
            + f'" fill="none" stroke="{C_PIN}" stroke-width="0.1524"/>'
        )
    if shape in ("input_low", "clock_low", "output_low"):
        s = 1.27
        nx, ny = -dy, dx
        pts = [(ex, ey), (ex - dx * s, ey - dy * s), (ex - dx * s + nx * s * 0.5, ey - dy * s + ny * s * 0.5)]
        els.append(
            '<path d="M'
            + "L".join(f"{f(px)} {f(py)}" for px, py in pts)
            + f'" fill="none" stroke="{C_PIN}" stroke-width="0.1524"/>'
        )
    if p["type"] == "no_connect" or shape == "non_logic":
        s = 0.4
        els.append(
            f'<path d="M{f(x - s)} {f(y - s)}L{f(x + s)} {f(y + s)}M{f(x + s)} {f(y - s)}L{f(x - s)} {f(y + s)}" '
            f'stroke="{C_PIN}" stroke-width="0.1524"/>'
        )
    # connection point marker (small circle like KiCad's pin end)
    els.append(
        f'<circle cx="{f(x)}" cy="{f(y)}" r="0.25" fill="none" stroke="{C_PIN}" stroke-width="0.05" opacity="0.6"/>'
    )
    bb.add(x, y, 0.3)
    bb.add(ex, ey)
    horizontal = abs(dx) > 0.5
    angle = 0 if horizontal else 90
    off = sym.pin_name_offset
    name = p["name"] if p["name"] not in ("~", "") else ""
    name_h = p["name_size"][0]
    num_h = p["num_size"][0]
    # Names: inside the body when offset > 0, else above the pin line (and numbers below)
    if name and not sym.pin_names_hidden:
        disp = _overbar(name)
        if off > 0:
            nx, ny = ex + dx * off, ey + dy * off
            if horizontal:
                anchor = "start" if dx > 0 else "end"
            else:
                # rotated 90 (text reads bottom->top); "start" is towards the top of the screen
                anchor = "start" if dy < 0 else "end"
            els.append(text_el(disp, nx, ny, name_h, p["name_size"][1], angle, C_PIN_NAME, anchor, "center"))
            text_extent(disp, nx, ny, name_h, p["name_size"][1], angle, anchor, "center", bb)
        else:
            mx, my = (x + ex) / 2, (y + ey) / 2
            if horizontal:
                els.append(text_el(disp, mx, my - 0.3, name_h, p["name_size"][1], 0, C_PIN_NAME, "middle", "bottom"))
                text_extent(disp, mx, my - 0.3, name_h, p["name_size"][1], 0, "middle", "bottom", bb)
            else:
                els.append(text_el(disp, mx - 0.3, my, name_h, p["name_size"][1], 90, C_PIN_NAME, "middle", "bottom"))
                text_extent(disp, mx - 0.3, my, name_h, p["name_size"][1], 90, "middle", "bottom", bb)
    if p["number"] and not sym.pin_numbers_hidden:
        mx, my = (x + ex) / 2, (y + ey) / 2
        below = off == 0 and name and not sym.pin_names_hidden
        if horizontal:
            ny = my + 0.3 if below else my - 0.3
            va = "top" if below else "bottom"
            els.append(text_el(p["number"], mx, ny, num_h, p["num_size"][1], 0, C_PIN_NUM, "middle", va))
            text_extent(p["number"], mx, ny, num_h, p["num_size"][1], 0, "middle", va, bb)
        else:
            nx = mx + 0.3 if below else mx - 0.3
            va = "top" if below else "bottom"
            els.append(text_el(p["number"], nx, my, num_h, p["num_size"][1], 90, C_PIN_NUM, "middle", va))
            text_extent(p["number"], nx, my, num_h, p["num_size"][1], 90, "middle", va, bb)
    return els


def _overbar(name: str) -> str:
    # ~{RESET} -> RESET with a combining overline (U+0305) on each char; cheap but readable
    return re.sub(r"~\{([^}]*)\}", lambda m: "".join(ch + "̅" for ch in m.group(1)), name)


def _field_elements(sym: Symbol, bb: BBox) -> list[str]:
    els = []
    for fl in sym.fields:
        if fl["hidden"] or fl["key"] not in ("Reference", "Value") and fl["hidden"]:
            continue
        if not fl["value"]:
            continue
        h, w = fl["size"]
        j = fl["justify"]
        anchor = "start" if "left" in j else "end" if "right" in j else "middle"
        valign = "top" if "top" in j else "bottom" if "bottom" in j else "center"
        ang = fl["angle"] / 10 if abs(fl["angle"]) > 360 else fl["angle"]
        x, y = fl["x"], -fl["y"]
        txt = fl["value"]
        if fl["key"] == "Reference":
            txt = txt + ("?" if not txt.endswith("?") else "")
        color = C_REF if fl["key"] == "Reference" else C_FIELD
        els.append(text_el(txt, x, y, h, w, ang, color, anchor, valign, bold=fl["bold"]))
        text_extent(txt, x, y, h, w, ang, anchor, valign, bb)
    return els


def unit_layout(sym: Symbol):
    """Per-unit (elements, bbox) — bbox in screen coords relative to the symbol origin."""
    out = {}
    for u in range(1, sym.unit_count + 1):
        bb = BBox()
        els = _unit_elements(sym, u, bb)
        if u == 1:
            els += _field_elements(sym, bb)
        out[u] = (els, bb)
    return out


def shared_layout(layouts: list[dict]):
    """Compute unit slot positions shared by base/head so both renders overlay.

    Returns ({unit: x_offset}, viewbox).
    """
    units = sorted({u for lay in layouts if lay for u in lay})
    slots = {}
    x = 0.0
    gap = 5.08
    vb_y0, vb_y1 = math.inf, -math.inf
    for i, u in enumerate(units):
        ub = BBox()
        for lay in layouts:
            if lay and u in lay:
                ub.add_box(lay[u][1])
        if not ub.valid:
            ub.add(-2.54, -2.54)
            ub.add(2.54, 2.54)
        off = x - ub.x0 if i else 0.0
        slots[u] = off
        vb_y0, vb_y1 = min(vb_y0, ub.y0), max(vb_y1, ub.y1)
        x = off + ub.x1 + gap
        if i == 0:
            vb_x0 = ub.x0
    vb_x1 = x - gap
    m = max(1.27, max(vb_x1 - vb_x0, vb_y1 - vb_y0) * 0.05)
    return slots, (vb_x0 - m, vb_y0 - m, vb_x1 + m, vb_y1 + m)


def render_symbol(layout: dict, slots: dict, vb, px_per_mm: float, sym: Symbol) -> str:
    parts = []
    for u, (els, _) in layout.items():
        off = slots.get(u, 0.0)
        label = ""
        if len(slots) > 1:
            uname = sym.unit_names.get(u) or f"Unit {chr(64 + u) if u <= 26 else u}"
            label = text_el(uname, 0, vb[1] + 1.2, 1.0, 1.0, 0, "#666666", "middle", "center")
        parts.append(f'<g transform="translate({f(off)} 0)" class="unit" data-unit="{u}">{label}{"".join(els)}</g>')
    x0, y0, x1, y1 = vb
    w, h = x1 - x0, y1 - y0
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x0)} {f(y0)} {f(w)} {f(h)}" '
        f'width="{f(w * px_per_mm)}" height="{f(h * px_per_mm)}">'
        f'<rect x="{f(x0)}" y="{f(y0)}" width="{f(w)}" height="{f(h)}" fill="{BACKGROUND}"/>'
        + "".join(parts)
        + "</svg>"
    )
