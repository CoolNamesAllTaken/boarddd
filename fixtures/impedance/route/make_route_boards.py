"""Generate the boards analyzeNet / analyze_net are tested on (docs/impedance.md "Along a route"):

    python fixtures/impedance/route/make_route_boards.py          # rewrites the files below
    python fixtures/impedance/route/make_route_boards.py --check  # fails if they differ

Synthetic boards, each {"board": boarddd/board@1, "copper": boarddd/copper@1} in <case>.json:

* split.json: a 0.35 mm microstrip on F.Cu crossing a 2 mm split in its In1.Cu GND plane, a +3V3 plane on In2.Cu
  under the whole board (the return path the split leaves); a second net crosses a split with nothing under it;
* cpwg.json: a 0.3 mm F.Cu trace in a GND pour with a 0.15 mm gap for 10 mm, then 0.4 mm, over In1.Cu GND;
* inner.json: a 0.15 mm In1.Cu trace between an F.Cu GND pour (0.2 mm above) and an In2.Cu GND plane (1.0 mm
  below) for 10 mm (an offset stripline), then 10 mm where F.Cu has no pour (an embedded microstrip);
* pair.json: a 0.25/0.15 mm pair (90 ohm) on F.Cu over In1.Cu GND, coupled for 16 mm, fanning out to 3 mm apart at both
  ends (the breakout).

And royalblue-usb.json.gz: royalblue54L_feather's board@1 (stackup, nets) and the copper@1 near its USB pair
(/Debugger/D+, /Debugger/D-; every item whose box comes within 3 mm of the pair's tracks), read with
boarddd.io.kicad from fixtures/royalblue54L_feather/kicad; the browser and JS tests analyse it
(python/tests/test_impedance_route.py checks that the subset gives the full board's answer).
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "python" / "src"))

from boarddd import copper as cu  # noqa: E402
from boarddd import model as m  # noqa: E402
from boarddd.io.kicad import read_kicad_copper, read_kicad_pcb  # noqa: E402


def stackup(layers: list[tuple]) -> m.Stackup:
    """[(name, kind, thickness, er)] top to bottom; copper names are layer ids; masks 0.02 mm, Er 3.5."""
    out = []
    for name, kind, t, er in layers:
        side = "top" if name.startswith("F.") else "bottom" if name.startswith("B.") else "inner"
        out.append(
            m.StackupLayer(
                name=name,
                kind=kind,
                side=side,
                thickness=t,
                epsilon_r=er,
                layer=name if kind in ("copper", "mask") else None,
            )
        )
    n = sum(x.kind == "copper" for x in out)
    return m.Stackup(thickness=round(sum(x.thickness for x in out), 6), copper_layers=n, layers=out)


FOUR = [
    ("F.Mask", "mask", 0.02, 3.5),
    ("F.Cu", "copper", 0.035, None),
    ("prepreg 1", "dielectric", 0.2, 4.2),
    ("In1.Cu", "copper", 0.035, None),
    ("core", "dielectric", 1.0, 4.5),
    ("In2.Cu", "copper", 0.035, None),
    ("prepreg 2", "dielectric", 0.2, 4.2),
    ("B.Cu", "copper", 0.035, None),
    ("B.Mask", "mask", 0.02, 3.5),
]
COPPER = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


def rect(x0, y0, x1, y1) -> cu.FillPolygon:
    return cu.FillPolygon(outline=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def pour(layer: str, net: str, *rects) -> cu.Zone:
    fill = [rect(*r) for r in rects]
    return cu.Zone(layer=layer, net=net, kind="pour", fill=fill, area=round(cu.fill_area(fill), 6))


def track(layer, net, w, a, b, tid) -> cu.Track:
    return cu.Track(layer=layer, net=net, width=w, start=a, end=b, id=tid)


def doc(name: str, st: m.Stackup, nets: list[str], tracks, zones, net_classes=(), net_class_of=None) -> dict:
    board = m.Board(
        name=name,
        source=m.Source(kind="other", reader="fixtures/impedance/route/make_route_boards.py"),
        stackup=st,
        nets=[m.Net(name=n, net_class=(net_class_of or {}).get(n, "Default")) for n in nets],
        net_classes=list(net_classes) or [m.NetClass(name="Default", nets=list(nets))],
        outline=m.Outline(board=[(-5.0, -10.0), (35.0, -10.0), (35.0, 10.0), (-5.0, 10.0)]),
    )
    copper = cu.Copper(
        board=name,
        source=m.Source(kind="other", reader="fixtures/impedance/route/make_route_boards.py"),
        layers=[c.layer for c in st.layers if c.kind == "copper"],
        nets=nets,
        tracks=list(tracks),
        zones=list(zones),
    )
    copper.planes = cu.planes(copper.zones, cu.outline_area(board.outline), copper.layers)
    return {"board": board.to_dict(), "copper": copper.to_dict()}


def split() -> dict:
    tr = [
        track("F.Cu", "SIG", 0.35, (0.0, 2.0), (30.0, 2.0), "split-sig"),
        track("F.Cu", "BARE", 0.35, (0.0, -6.0), (30.0, -6.0), "split-bare"),
    ]
    zones = [
        # a 2 mm split across the whole In1.Cu GND plane (x 14..16)
        pour("In1.Cu", "GND", (-5, -2, 14, 10), (16, -2, 35, 10), (-5, -10, 14, -2), (16, -10, 35, -2)),
        # +3V3 on In2.Cu under SIG (y = 2) only: BARE (y = -6) has nothing under the split
        pour("In2.Cu", "+3V3", (-5, -2, 35, 10)),
    ]
    return doc(
        "split",
        stackup(FOUR),
        ["GND", "+3V3", "SIG", "BARE"],
        tr,
        zones,
        net_classes=[
            m.NetClass(name="Default", nets=["GND", "+3V3", "BARE"]),
            m.NetClass(
                name="SE_50_MS",
                nets=["SIG"],
                impedance=m.ImpedanceTarget(
                    kind="single", target=50.0, tolerance_pct=10.0, structure="microstrip", source="name"
                ),
            ),
        ],
        net_class_of={"SIG": "SE_50_MS", "GND": "Default", "+3V3": "Default", "BARE": "Default"},
    )


def cpwg() -> dict:
    w = 0.3
    tr = [
        track("F.Cu", "RF", w, (0.0, 0.0), (10.0, 0.0), "cpwg-a"),
        track("F.Cu", "RF", w, (10.0, 0.0), (20.0, 0.0), "cpwg-b"),
    ]
    g1, g2 = 0.15, 0.4
    e1, e2 = w / 2 + g1, w / 2 + g2
    zones = [
        pour("F.Cu", "GND", (-5, e1, 10, 10), (-5, -10, 10, -e1), (10, e2, 25, 10), (10, -10, 25, -e2)),
        pour("In1.Cu", "GND", (-5, -10, 25, 10)),
    ]
    return doc("cpwg", stackup(FOUR), ["GND", "RF"], tr, zones)


def inner() -> dict:
    layers = [
        ("F.Mask", "mask", 0.02, 3.5),
        ("F.Cu", "copper", 0.035, None),
        ("prepreg 1", "dielectric", 0.2, 4.2),
        ("In1.Cu", "copper", 0.035, None),
        ("core", "dielectric", 1.0, 4.5),
        ("In2.Cu", "copper", 0.035, None),
        ("prepreg 2", "dielectric", 0.2, 4.2),
        ("B.Cu", "copper", 0.035, None),
        ("B.Mask", "mask", 0.02, 3.5),
    ]
    tr = [
        track("In1.Cu", "DATA", 0.15, (0.0, 0.0), (10.0, 0.0), "inner-a"),
        track("In1.Cu", "DATA", 0.15, (10.0, 0.0), (20.0, 0.0), "inner-b"),
    ]
    zones = [pour("F.Cu", "GND", (-5, -10, 10, 10)), pour("In2.Cu", "GND", (-5, -10, 25, 10))]
    return doc("inner", stackup(layers), ["GND", "DATA"], tr, zones)


def pair() -> dict:
    w, s = 0.25, 0.15  # 90 ohm differential by tier 1 (coupled microstrip under the mask)
    yp, yn = (w + s) / 2, -(w + s) / 2
    tr = [
        track("F.Cu", "USB_P", w, (0.0, 1.5), (2.0, yp), "p-in"),
        track("F.Cu", "USB_P", w, (2.0, yp), (18.0, yp), "p-run"),
        track("F.Cu", "USB_P", w, (18.0, yp), (20.0, 1.5), "p-out"),
        track("F.Cu", "USB_N", w, (0.0, -1.5), (2.0, yn), "n-in"),
        track("F.Cu", "USB_N", w, (2.0, yn), (18.0, yn), "n-run"),
        track("F.Cu", "USB_N", w, (18.0, yn), (20.0, -1.5), "n-out"),
    ]
    zones = [pour("In1.Cu", "GND", (-5, -10, 25, 10))]
    nets = ["GND", "USB_P", "USB_N"]
    return doc(
        "pair",
        stackup(FOUR),
        nets,
        tr,
        zones,
        net_classes=[
            m.NetClass(name="Default", nets=["GND"]),
            m.NetClass(
                name="DP_90_MS",
                nets=["USB_P", "USB_N"],
                diff_pair_width=w,
                diff_pair_gap=s,
                impedance=m.ImpedanceTarget(
                    kind="differential", target=90.0, tolerance_pct=10.0, structure="microstrip", source="name"
                ),
            ),
        ],
        net_class_of={"USB_P": "DP_90_MS", "USB_N": "DP_90_MS", "GND": "Default"},
    )


USB = ("/Debugger/D+", "/Debugger/D-")


def royalblue_usb() -> dict:
    """royalblue54L_feather's board and the copper within 3 mm of its USB pair's tracks."""
    pcb = REPO / "fixtures" / "royalblue54L_feather" / "kicad" / "RoyalBlue54L-Feather.kicad_pcb"
    board = read_kicad_pcb(pcb)
    copper = read_kicad_copper(pcb)
    pts = [p for t in copper.tracks if t.net in USB for p in (t.start, t.end)]
    x0, y0 = min(p[0] for p in pts) - 3, min(p[1] for p in pts) - 3
    x1, y1 = max(p[0] for p in pts) + 3, max(p[1] for p in pts) + 3

    def near(box):
        return box[0] <= x1 and box[2] >= x0 and box[1] <= y1 and box[3] >= y0

    def tbox(t):
        return (
            min(t.start[0], t.end[0]),
            min(t.start[1], t.end[1]),
            max(t.start[0], t.end[0]),
            max(t.start[1], t.end[1]),
        )

    copper.tracks = [t for t in copper.tracks if near(tbox(t))]
    copper.vias = [v for v in copper.vias if near((v.at[0], v.at[1], v.at[0], v.at[1]))]
    copper.pads = [p for p in copper.pads if p.polygons and near(cu.bbox([q for pl in p.polygons for q in pl]))]
    copper.zones = [z for z in copper.zones if any(near(cu.bbox(f.outline)) for f in z.fill)]
    copper.keepouts, copper.net_ties = [], []
    used = {x.net for x in (*copper.tracks, *copper.vias, *copper.pads, *copper.zones)}
    copper.nets = [n for n in copper.nets if n in used]
    copper.warnings.append("a subset: the copper within 3 mm of the USB pair (fixtures/impedance/route)")
    board.footprints, board.components = {}, []  # not needed for the analysis
    return {"board": board.to_dict(), "copper": copper.to_dict()}


CASES = {"split": split, "cpwg": cpwg, "inner": inner, "pair": pair}


def outputs() -> dict[str, bytes]:
    out = {f"{k}.json": (json.dumps(f(), separators=(",", ":")) + "\n").encode() for k, f in CASES.items()}
    text = json.dumps(royalblue_usb(), separators=(",", ":")) + "\n"
    out["royalblue-usb.json.gz"] = gzip.compress(text.encode(), compresslevel=9, mtime=0)
    return out


def main() -> int:
    stale = []
    for name, data in outputs().items():
        path = HERE / name
        if name.endswith(".gz"):  # compare the content, not the gzip bytes (zlib versions differ)
            same = path.exists() and gzip.decompress(path.read_bytes()) == gzip.decompress(data)
        else:
            same = path.exists() and path.read_bytes() == data
        if "--check" in sys.argv:
            if not same:
                stale.append(name)
        elif not same:
            path.write_bytes(data)
            print(f"wrote {name} ({len(data) // 1024} KiB)")
    for name in stale:
        print(f"{HERE / name} is out of date: run {Path(__file__).name}", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
