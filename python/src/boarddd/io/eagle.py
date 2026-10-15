"""
Eagle / Fusion Electronics boards (``.brd``, XML, Eagle 6 and later): nets, net classes and routed copper.

* ``board/classes/class`` -- number, name, width, drill and ``clearance`` (the one to its own class); ``signal@class``
  names its class by number. A class named by the class-name convention (``DP_90``, ``50ohm``...) gets that target
  (``boarddd.io.kicad.project.impedance_from_name``); Eagle has no impedance field of its own.
* ``board/signals/signal`` -- the nets; pairs by the naming rule (``boarddd.nets.pairs_among``: +/-, _P/_N, P/N).
* ``signal/wire`` on copper layers 1-16 (1 top, 16 bottom) with ``curve`` arcs, and ``signal/via`` -- copper@1
  tracks and vias, so tracks like a picked one can be found on an Eagle board without its Gerbers.

Lengths are millimetres in the file (Eagle's XML is always mm). Binary (pre-6) boards are refused.
"""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from boarddd import copper as cu
from boarddd import model as m

__all__ = ["read_eagle_brd", "read_eagle_copper", "is_eagle"]


def _load(source) -> tuple[ET.Element, bytes, Path | None]:
    if isinstance(source, (bytes, bytearray)):
        data, path = bytes(source), None
    elif isinstance(source, str) and source.lstrip().startswith("<"):
        data, path = source.encode("utf-8"), None
    else:
        path = Path(source)
        data = path.read_bytes()
    if not is_eagle(data):
        raise ValueError("not an Eagle XML board (.brd from Eagle 6 or later)")
    return ET.fromstring(data), data, path


def is_eagle(data: bytes) -> bool:
    """Whether bytes are an Eagle XML board."""
    head = data[:2048].lstrip()
    return head.startswith(b"<") and b"<eagle" in head


def _num(v, default=None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _copper_layers(root: ET.Element, used: set[int]) -> dict[int, str]:
    """Eagle copper layer number -> copper@1 id: 1 F.Cu, 16 B.Cu, the inner ones in between that are used."""
    inner = sorted(n for n in used if 1 < n < 16)
    out = {1: "F.Cu", 16: "B.Cu"}
    for i, n in enumerate(inner):
        out[n] = f"In{i + 1}.Cu"
    return out


def read_eagle_brd(source, *, name: str | None = None) -> m.Board:
    """An Eagle .brd -> board@1 with its nets (pairs by name) and net classes (rules, targets by name)."""
    from boarddd.io.kicad.project import impedance_from_name
    from boarddd.nets import pairs_among

    root, data, path = _load(source)
    board = root.find("drawing/board")
    if board is None:
        raise ValueError("Eagle file without a board (a schematic?)")
    warnings: list[str] = []
    raw = {}
    for c in board.findall("classes/class"):
        number = c.get("number", "0")
        own = next((_num(cl.get("value")) for cl in c.findall("clearance") if cl.get("class", number) == number), None)
        raw[number] = {
            "name": c.get("name") or f"class {number}",
            "width": _num(c.get("width")),
            "drill": _num(c.get("drill")),
            "clearance": own,
        }
    signals = [(s.get("name", ""), s.get("class", "0")) for s in board.findall("signals/signal") if s.get("name")]
    pair = {}
    for p, n in pairs_among([s for s, _ in signals]):
        pair[p], pair[n] = n, p
    nets = []
    classes: dict[str, m.NetClass] = {}
    for sig, number in signals:
        c = raw.get(number) or {"name": "default", "width": None, "drill": None, "clearance": None}
        cname = "Default" if c["name"].lower() == "default" else c["name"]
        if cname not in classes:
            classes[cname] = m.NetClass(
                name=cname,
                track_width=c["width"] or None,
                clearance=c["clearance"] or None,
                via_drill=c["drill"] or None,
                impedance=None if cname == "Default" else impedance_from_name(cname),
            )
        classes[cname].nets.append(sig)
        nets.append(m.Net(name=sig, net_class=cname, pair=pair.get(sig)))
    for nc in classes.values():
        imp = nc.impedance
        if imp and imp.kind == "single" and nc.nets and all(pair.get(n) in nc.nets for n in nc.nets):
            imp.kind = "differential"
        if imp and imp.kind == "differential":
            nc.diff_pair_width, nc.track_width = nc.track_width, None
    version = root.get("version")
    return m.Board(
        name=name or (path.stem if path else "board"),
        source=m.Source(
            kind="eagle",
            files=[
                m.SourceFile(
                    path=path.name if path else "board.brd", role="pcb", sha256=hashlib.sha256(data).hexdigest()
                )
            ],
            generator=f"Eagle {version}" if version else "Eagle",
            reader="boarddd.io.eagle",
        ),
        nets=nets,
        net_classes=list(classes.values()),
        warnings=warnings,
    )


def _arc_mid(x1: float, y1: float, x2: float, y2: float, curve: float) -> tuple[float, float]:
    """The point halfway along an Eagle arc wire (``curve`` degrees, counter-clockwise from 1 to 2)."""
    a = math.radians(curve)
    c = math.hypot(x2 - x1, y2 - y1)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    d = (c / 2) / math.tan(a / 2)
    nx, ny = -(y2 - y1) / c, (x2 - x1) / c  # left of the chord
    cx, cy = mx + nx * d, my + ny * d
    h = a / 2
    vx, vy = x1 - cx, y1 - cy
    return (cx + vx * math.cos(h) - vy * math.sin(h), cy + vx * math.sin(h) + vy * math.cos(h))


def read_eagle_copper(source, *, name: str | None = None) -> cu.Copper:
    """An Eagle .brd's routed copper: wires on layers 1-16 as copper@1 tracks (arcs through their middle), vias."""
    root, data, path = _load(source)
    board = root.find("drawing/board")
    if board is None:
        raise ValueError("Eagle file without a board (a schematic?)")
    wires = []
    vias = []
    for sig in board.findall("signals/signal"):
        net = sig.get("name", "")
        for w in sig.findall("wire"):
            layer = int(_num(w.get("layer"), 0) or 0)
            if 1 <= layer <= 16:
                wires.append((net, layer, w))
        for v in sig.findall("via"):
            vias.append((net, v))
    used = {layer for _, layer, _ in wires}
    for _, v in vias:
        a, _, b = str(v.get("extent", "1-16")).partition("-")
        used |= {int(_num(a, 1)), int(_num(b, 16))}
    ids = _copper_layers(root, used)
    order = [ids[n] for n in sorted(ids) if n in used or n in (1, 16)]
    tracks = []
    for net, layer, w in wires:
        x1, y1, x2, y2 = (_num(w.get(k), 0.0) for k in ("x1", "y1", "x2", "y2"))
        curve = _num(w.get("curve"), 0.0) or 0.0
        mid = None
        if abs(curve) > 1e-9 and math.hypot(x2 - x1, y2 - y1) > 1e-9:
            mx, my = _arc_mid(x1, y1, x2, y2, curve)
            mid = (round(mx, 6), round(my, 6))
        tracks.append(
            cu.Track(layer=ids[layer], net=net, width=_num(w.get("width"), 0.0), start=(x1, y1), end=(x2, y2), mid=mid)
        )
    out_vias = []
    for net, v in vias:
        a, _, b = str(v.get("extent", "1-16")).partition("-")
        top, bottom = ids.get(int(_num(a, 1))), ids.get(int(_num(b, 16)))
        drill = _num(v.get("drill"), 0.0) or 0.0
        diameter = _num(v.get("diameter"), 0.0) or round(drill * 1.5, 6)
        span = (top or "F.Cu", bottom or "B.Cu")
        out_vias.append(
            cu.Via(
                at=(_num(v.get("x"), 0.0), _num(v.get("y"), 0.0)),
                net=net,
                diameter=diameter,
                drill=drill,
                span=span,
                type="through" if span == (order[0], order[-1]) else "blind",
            )
        )
    names = sorted({t.net for t in tracks} | {v.net for v in out_vias})
    return cu.Copper(
        board=name or (path.stem if path else "board"),
        source=m.Source(
            kind="eagle",
            reader="boarddd.io.eagle",
            files=[
                m.SourceFile(
                    path=path.name if path else "board.brd", role="pcb", sha256=hashlib.sha256(data).hexdigest()
                )
            ],
        ),
        layers=order,
        nets=names,
        tracks=tracks,
        vias=out_vias,
    )
