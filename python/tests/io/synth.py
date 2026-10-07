"""
Gerber and placement files written to order, for the tests that read pads off a board.

Real exports are the yardstick everywhere else in this suite, and the paste reader was
developed against a real Altium board; but a test about "a SOT-23 next to a 0402" needs to
say exactly where every opening is, and no fixture says that. So these write the two formats
the way their tools do -- KiCad's `RoundRect` macro with a 4.6 format in millimeters, Altium's
`ROUNDEDRECT` built from primitives in inches -- from a list of (x, y, w, h) boxes.

Ported from magpie `tests/synth.py` at 3a0374d3.
"""

from __future__ import annotations

_KICAD_HEAD = """%TF.GenerationSoftware,KiCad,Pcbnew,9.0.1*%
%TF.FileFunction,Paste,{side_word}*%
%TF.FilePolarity,Positive*%
%FSLAX46Y46*%
%MOMM*%
%LPD*%
G01*
G04 APERTURE LIST*
%AMRoundRect*
0 Rectangle with rounded corners*
0 $1 Rounding radius*
0 $2 $3 $4 $5 $6 $7 $8 $9 X,Y pos of 4 corners*
0 Add a 4 corners polygon primitive as box body*
4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*
0 Add four circle primitives for the rounded corners*
1,1,$1+$1,$2,$3*
1,1,$1+$1,$4,$5*
1,1,$1+$1,$6,$7*
1,1,$1+$1,$8,$9*
0 Add four rect primitives between the rounded corners*
20,1,$1+$1,$2,$3,$4,$5,0*
20,1,$1+$1,$4,$5,$6,$7,0*
20,1,$1+$1,$6,$7,$8,$9,0*
20,1,$1+$1,$8,$9,$2,$3,0*%
"""


def kicad_paste(boxes, side: str = "top") -> str:
    """A KiCad paste layer flashing one RoundRect per (x, y, w, h) box, in mm."""
    lines = [_KICAD_HEAD.format(side_word="Top" if side == "top" else "Bot")]
    apertures: dict[tuple[float, float], int] = {}
    for _x, _y, w, h in boxes:
        key = (round(w, 4), round(h, 4))
        if key not in apertures:
            number = 10 + len(apertures)
            apertures[key] = number
            r = round(min(w, h) * 0.25, 6)
            hw, hh = round(w / 2 - r, 6), round(h / 2 - r, 6)
            lines.append(
                f"%ADD{number}RoundRect,{r:.6f}X{-hw:.6f}X{-hh:.6f}X{hw:.6f}X{-hh:.6f}"
                f"X{hw:.6f}X{hh:.6f}X{-hw:.6f}X{hh:.6f}X0*%"
            )
    lines.append("G04 APERTURE END LIST*")
    current = None
    for x, y, w, h in boxes:
        number = apertures[(round(w, 4), round(h, 4))]
        if number != current:
            lines.append(f"D{number}*")
            current = number
        lines.append(f"X{round(x * 1e6):d}Y{round(y * 1e6):d}D03*")
    lines.append("M02*")
    return "\n".join(lines) + "\n"


def altium_paste(boxes) -> str:
    """
    An Altium top-paste layer: inches, 2.4 format, one `ROUNDEDRECT` macro per size built the
    way Altium builds them (two center lines and four corner circles), the coordinates on one
    line and the `D03*` on the next.
    """
    lines = [
        "G04 #@! TF.GenerationSoftware,Altium Limited,Altium Designer,25.4.2*",
        "%FSLAX24Y24*%",
        "%MOIN*%",
        "G70*",
        "G01*",
        "G75*",
    ]
    apertures: dict[tuple[float, float], int] = {}
    for _x, _y, w, h in boxes:
        key = (round(w, 4), round(h, 4))
        if key in apertures:
            continue
        number = 20 + len(apertures)
        apertures[key] = number
        wi, hi = w / 25.4, h / 25.4
        r = min(wi, hi) * 0.15
        lines += [
            f"%AMROUNDEDRECTD{number}*",
            f"21,1,{wi:.4f},{hi - 2 * r:.4f},0,0,0.0*",
            f"21,1,{wi - 2 * r:.4f},{hi:.4f},0,0,0.0*",
            f"1,1,{2 * r:.4f},{wi / 2 - r:.4f},{hi / 2 - r:.4f}*",
            f"1,1,{2 * r:.4f},{-(wi / 2 - r):.4f},{hi / 2 - r:.4f}*",
            f"1,1,{2 * r:.4f},{-(wi / 2 - r):.4f},{-(hi / 2 - r):.4f}*",
            f"1,1,{2 * r:.4f},{wi / 2 - r:.4f},{-(hi / 2 - r):.4f}*",
            "%",
            f"%ADD{number}ROUNDEDRECTD{number}*%",
        ]
    current = None
    for x, y, w, h in boxes:
        number = apertures[(round(w, 4), round(h, 4))]
        if number != current:
            lines.append(f"D{number}*")
            current = number
        lines.append(f"X{round(x / 25.4 * 1e4):d}Y{round(y / 25.4 * 1e4):d}D02*")
        lines.append("D03*")
    lines.append("M02*")
    return "\n".join(lines) + "\n"


def pos_csv(rows) -> str:
    """KiCad's placement CSV from (ref, value, footprint, x, y, rot, side) tuples."""
    out = ["Ref,Val,Package,PosX,PosY,Rot,Side"]
    for ref, value, footprint, x, y, rot, side in rows:
        out.append(f'"{ref}","{value}","{footprint}",{x:.6f},{y:.6f},{rot:.6f},{side}')
    return "\n".join(out) + "\n"


# ─── Footprints, as boxes about the origin ────────────────────────────────────


def chip(long_side: float, short: float, gap: float | None = None):
    """A two-terminal chip lying along x: two pads, one at each end."""
    gap = gap if gap is not None else long_side * 0.4
    pad_w = (long_side - gap) / 2 + 0.1
    pad_h = short + 0.1
    dx = gap / 2 + pad_w / 2
    return [(-dx, 0, pad_w, pad_h), (dx, 0, pad_w, pad_h)]


def two_rows(per_side: int, pitch: float, span: float, w: float = 0.6, length: float = 1.5):
    """Two rows of `per_side` pads along y at x = +-span/2, the way a SOIC lies."""
    out = []
    for i in range(per_side):
        y = (i - (per_side - 1) / 2) * pitch
        out += [(-span / 2, y, length, w), (span / 2, y, length, w)]
    return out


def four_rows(
    ends: int,
    sides: int,
    pitch: float,
    size: float,
    w: float = 0.25,
    length: float = 0.8,
    thermal: float | None = None,
    windows: int = 1,
):
    """A QFN/QFP: `ends` pads on N and S, `sides` on E and W, optionally a thermal pad cut
    into `windows` x `windows` paste squares."""
    out = []
    for i in range(sides):
        y = (i - (sides - 1) / 2) * pitch
        out += [(-size / 2, y, length, w), (size / 2, y, length, w)]
    for i in range(ends):
        x = (i - (ends - 1) / 2) * pitch
        out += [(x, -size / 2, w, length), (x, size / 2, w, length)]
    if thermal:
        cell = thermal / windows
        for i in range(windows):
            for j in range(windows):
                out.append(((i - (windows - 1) / 2) * cell, (j - (windows - 1) / 2) * cell, cell * 0.8, cell * 0.8))
    return out


def grid(n: int, pitch: float, diameter: float):
    return [
        ((i - (n - 1) / 2) * pitch, (j - (n - 1) / 2) * pitch, diameter, diameter) for i in range(n) for j in range(n)
    ]


def placed(boxes, x: float, y: float, rotation: float = 0.0, side: str = "top"):
    """The boxes of a footprint at a placement: rotated, moved, mirrored for the underside."""
    import math

    angle = math.radians(rotation)
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for px, py, w, h in boxes:
        rx, ry = px * c - py * s, px * s + py * c
        if side == "bottom":
            rx = -rx
        if round(rotation / 90) % 2:
            w, h = h, w
        out.append((x + rx, y + ry, w, h))
    return out
