"""boarddd.connectivity: nets from the copper of plain Gerbers (no X2), on boards written to order and a KiCad demo."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("shapely")

from boarddd.connectivity import compare_nets, connect_files  # noqa: E402
from boarddd.io.gerber_copper import read_gerber_copper_files  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class Gerber:
    """A plain RS-274X layer (mm, 4.6), written in drawing order."""

    def __init__(self, negative: bool = False, x2: bool = False):
        self.head = ["%FSLAX46Y46*%", "%MOMM*%"] + (["%IPNEG*%"] if negative else [])
        self.body: list[str] = []
        self.aps: dict[str, int] = {}
        self.macros: list[str] = []
        self.x2 = x2

    def _ap(self, spec: str) -> int:
        if spec not in self.aps:
            self.aps[spec] = 10 + len(self.aps)
            self.head.append(f"%ADD{self.aps[spec]}{spec}*%")
        return self.aps[spec]

    @staticmethod
    def _xy(x: float, y: float) -> str:
        return f"X{round(x * 1e6)}Y{round(y * 1e6)}"

    def net(self, name: str) -> Gerber:
        if self.x2:
            self.body.append(f"%TO.N,{name}*%")
        return self

    def polarity(self, dark: bool) -> Gerber:
        self.body.append("%LPD*%" if dark else "%LPC*%")
        return self

    def track(self, a, b, w: float = 0.2) -> Gerber:
        self.body += [f"D{self._ap(f'C,{w}')}*", f"{self._xy(*a)}D02*", f"G01{self._xy(*b)}D01*"]
        return self

    def circle(self, at, d: float) -> Gerber:
        self.body += [f"D{self._ap(f'C,{d}')}*", f"{self._xy(*at)}D03*"]
        return self

    def rect(self, at, w: float, h: float) -> Gerber:
        self.body += [f"D{self._ap(f'R,{w}X{h}')}*", f"{self._xy(*at)}D03*"]
        return self

    def thermal(self, at, outer: float, inner: float, gap: float) -> Gerber:
        name = f"TH{len(self.macros)}"
        self.macros.append(f"%AM{name}*7,0,0,{outer},{inner},{gap},0*%")
        self.head.append(self.macros[-1])
        return self.circle_with(name, at)

    def circle_with(self, macro: str, at) -> Gerber:
        self.body += [f"D{self._ap(macro)}*", f"{self._xy(*at)}D03*"]
        return self

    def region(self, *loops) -> Gerber:
        self.body.append("G36*")
        for loop in loops:
            self.body.append(f"{self._xy(*loop[0])}D02*")
            self.body += [f"G01{self._xy(*p)}D01*" for p in [*loop[1:], loop[0]]]
        self.body.append("G37*")
        return self

    def text(self) -> bytes:
        return ("\n".join([*self.head, "%LPD*%", *self.body, "M02*"]) + "\n").encode()


def excellon(holes, function: str | None = None) -> bytes:
    """Holes as [(x, y, d)], metric."""
    tools: dict[float, int] = {}
    for _, _, d in holes:
        tools.setdefault(d, len(tools) + 1)
    lines = ["M48"] + ([f"; #@! TF.FileFunction,{function}"] if function else []) + ["METRIC"]
    lines += [f"T{t}C{d:.3f}" for d, t in tools.items()] + ["%", "G90", "G05"]
    for d, t in tools.items():
        lines.append(f"T{t}")
        lines += [f"X{x:.3f}Y{y:.3f}" for x, y, dd in holes if dd == d]
    return ("\n".join([*lines, "M30"]) + "\n").encode()


def outline(x0=-2.0, y0=-2.0, x1=14.0, y1=8.0) -> bytes:
    return (
        Gerber()
        .track((x0, y0), (x1, y0), 0.1)
        .track((x1, y0), (x1, y1), 0.1)
        .track((x1, y1), (x0, y1), 0.1)
        .track((x0, y1), (x0, y0), 0.1)
        .text()
    )


def package(layers: dict[str, Gerber], holes=(), npth=(), extra: dict | None = None) -> dict[str, bytes]:
    files = {f"b-{name}.gbr": g.text() for name, g in layers.items()}
    files["b-Edge_Cuts.gbr"] = outline()
    if holes:
        files["b-PTH.drl"] = excellon(holes)
    if npth:
        files["b-NPTH.drl"] = excellon(npth)
    files.update(extra or {})
    return files


def net_at(doc, x: float, y: float, layer: str) -> str:
    for p in doc.pads:
        if layer in p.layers and abs(p.at[0] - x) < 1e-6 and abs(p.at[1] - y) < 1e-6:
            return p.net
    for v in doc.vias:
        if abs(v.at[0] - x) < 1e-6 and abs(v.at[1] - y) < 1e-6:
            return v.net
    raise AssertionError(f"nothing at {x}, {y} on {layer}")


def two_layer(**kw):
    top = Gerber().rect((0, 0), 1, 1).track((0, 0), (5, 0)).circle((5, 0), 0.6).rect((0, 5), 1, 1)
    bottom = Gerber().circle((5, 0), 0.6).track((5, 0), (10, 0)).rect((10, 0), 1, 1)
    return package({"F_Cu": top, "B_Cu": bottom}, **kw)


def test_a_via_joins_the_layers_and_names_follow_the_pad_count():
    doc, conn = connect_files(two_layer(holes=[(5, 0, 0.3)]))
    assert doc.layers == ["F.Cu", "B.Cu"]
    a, b, c = net_at(doc, 0, 0, "F.Cu"), net_at(doc, 10, 0, "B.Cu"), net_at(doc, 0, 5, "F.Cu")
    assert a == b == "N1" and c == "N2"
    assert conn.names == "geometry"
    assert [(n.name, n.pads, n.layers) for n in conn.nets] == [("N1", 2, ["F.Cu", "B.Cu"]), ("N2", 1, ["F.Cu"])]
    # the 0.3 mm hole with no mask files is a via, not a pad
    assert [(v.at, v.net, v.drill, v.diameter) for v in doc.vias] == [((5.0, 0.0), "N1", 0.3, 0.6)]
    assert {t.net for t in doc.tracks} == {"N1"}
    # the same files, the same names
    again, _ = connect_files(two_layer(holes=[(5, 0, 0.3)]))
    assert [(p.at, p.net) for p in again.pads] == [(p.at, p.net) for p in doc.pads]


def test_a_non_plated_hole_joins_nothing():
    doc, conn = connect_files(two_layer(npth=[(5, 0, 0.3)]))
    assert net_at(doc, 0, 0, "F.Cu") != net_at(doc, 10, 0, "B.Cu")
    assert conn.vias == 0


def test_no_drill_files_is_said():
    _, conn = connect_files(two_layer())
    assert any("no drill files" in w for w in conn.warnings)


def test_a_mask_opening_makes_a_hole_a_pad_and_a_tented_one_a_via():
    mask = Gerber().rect((0, 0), 1.1, 1.1).circle((8, 0), 1.2)  # opening over (8, 0), none over the via at (5, 0)
    top = (
        Gerber().rect((0, 0), 1, 1).track((0, 0), (5, 0)).circle((5, 0), 0.6).track((5, 0), (8, 0)).circle((8, 0), 1.0)
    )
    files = package(
        {"F_Cu": top, "B_Cu": Gerber().circle((5, 0), 0.6), "F_Mask": mask}, holes=[(5, 0, 0.3), (8, 0, 0.4)]
    )
    doc, conn = connect_files(files)
    assert [v.at for v in doc.vias] == [(5.0, 0.0)]
    assert conn.nets[0].pads == 2  # (0, 0) and the plated pad at (8, 0)


def test_clear_polarity_cuts_an_antipad_out_of_a_plane():
    plane = (
        Gerber()
        .region([(-1, -1), (13, -1), (13, 7), (-1, 7)])
        .polarity(False)
        .circle((5, 0), 1.0)  # antipad: the via's net passes through
        .polarity(True)
    )
    top = Gerber().rect((0, 0), 1, 1).track((0, 0), (5, 0)).circle((5, 0), 0.6).rect((8, 0), 1, 1).rect((0, 5), 1, 1)
    files = package({"F_Cu": top, "In1_Cu": plane, "B_Cu": Gerber()}, holes=[(5, 0, 0.3), (8, 0, 0.4)])
    doc, _ = connect_files(files)
    assert net_at(doc, 0, 0, "F.Cu") != net_at(doc, 8, 0, "F.Cu")  # the via does not touch the plane
    plane_net = net_at(doc, 8, 0, "F.Cu")  # the hole at (8, 0) does
    zones = [z for z in doc.zones if z.layer == "In1.Cu"]
    assert [z.net for z in zones] == [plane_net]
    assert len(zones[0].fill[0].holes) == 1  # the antipad


@pytest.mark.parametrize("spokes", [True, False])
def test_a_negative_plane_connects_through_thermal_spokes_only(spokes):
    plane = Gerber(negative=True).circle((5, 0), 1.0)  # an antipad around the via
    if spokes:
        plane.thermal((8, 0), 1.6, 1.0, 0.3)
    else:
        plane.circle((8, 0), 1.6)
    top = Gerber().rect((0, 0), 1, 1).track((0, 0), (5, 0)).circle((5, 0), 0.6).rect((8, 0), 1.2, 1.2)
    files = package({"F_Cu": top, "In1_Cu": plane, "B_Cu": Gerber()}, holes=[(5, 0, 0.3), (8, 0, 0.6)])
    doc, conn = connect_files(files)
    in1 = [n for n in conn.nets if "In1.Cu" in n.layers]
    pad8 = next(n for n in conn.nets if n.name == net_at(doc, 8, 0, "F.Cu"))
    assert net_at(doc, 0, 0, "F.Cu") != pad8.name
    if spokes:
        assert "In1.Cu" in pad8.layers  # the pad is on the plane's net
    else:
        assert "In1.Cu" not in pad8.layers  # cleared all round: the plane is apart
        assert [n.pads for n in in1] == [0]


def test_copper_closer_than_the_tolerance_is_one_piece():
    top = Gerber().rect((0, 0), 1, 1).track((0.5005, 0), (3, 0)).rect((3.5, 0), 1, 1)  # 0.5 um short of the pad
    doc, conn = connect_files(package({"F_Cu": top}))
    assert net_at(doc, 0, 0, "F.Cu") == net_at(doc, 3.5, 0, "F.Cu")
    assert len([n for n in conn.nets if n.pads]) == 1


def test_a_fractured_region_keeps_its_hole_and_a_pad_inside_it_apart():
    # KiCad's fracture: the hole joined to the outline by a cut there and back
    pour = [(-1, -1), (6, -1), (6, 6), (-1, 6), (-1, 2), (2, 2), (2, 3), (3, 3), (3, 2), (2, 2), (-1, 2)]
    top = Gerber().region(pour).rect((2.5, 2.5), 0.4, 0.4).rect((5, 5), 0.5, 0.5).rect((8, 5), 0.5, 0.5)
    doc, _ = connect_files(package({"F_Cu": top}))
    assert net_at(doc, 2.5, 2.5, "F.Cu") != net_at(doc, 5, 5, "F.Cu")
    assert net_at(doc, 5, 5, "F.Cu") != net_at(doc, 8, 5, "F.Cu")
    zone = next(z for z in doc.zones if z.net == net_at(doc, 5, 5, "F.Cu"))
    assert len(zone.fill[0].holes) == 1


def test_x2_names_are_kept_and_a_short_is_reported():
    top = (
        Gerber(x2=True)
        .net("GND")
        .rect((0, 0), 1, 1)
        .track((0, 0), (3, 0))
        .net("VCC")
        .rect((3, 0), 1, 1)  # touches GND's track: one piece of copper
        .net("SIG")
        .rect((0, 5), 1, 1)
    )
    doc, conn = connect_files(package({"F_Cu": top}))
    assert conn.names == "x2"
    shorted = next(n for n in conn.nets if n.merged)
    assert shorted.merged == ["GND", "VCC"] and shorted.name == "GND"
    assert net_at(doc, 0, 5, "F.Cu") == "SIG"


def test_a_blind_drill_file_joins_only_its_span():
    top = Gerber().rect((0, 0), 1, 1).track((0, 0), (5, 0)).circle((5, 0), 0.6)
    in1 = Gerber().circle((5, 0), 0.6).track((5, 0), (7, 0)).rect((7, 0), 0.5, 0.5)
    bottom = Gerber().circle((5, 0), 0.6).rect((5, 0), 1, 1)
    files = package(
        {"F_Cu": top, "In1_Cu": in1, "In2_Cu": Gerber(), "B_Cu": bottom},
        extra={"b-1-2.drl": excellon([(5, 0, 0.2)], "Plated,1,2,Blind")},
    )
    doc, _ = connect_files(files)
    assert net_at(doc, 0, 0, "F.Cu") == net_at(doc, 7, 0, "In1.Cu")
    assert net_at(doc, 0, 0, "F.Cu") != net_at(doc, 5, 0, "B.Cu")


def test_compare_nets_counts_splits_and_merges():
    doc, _ = connect_files(two_layer(holes=[(5, 0, 0.3)]))
    good = {(0, 0, "F.Cu"): "A", (10, 0, "B.Cu"): "A", (0, 5, "F.Cu"): "B"}
    assert compare_nets(good, doc)["pct"] == 100.0
    bad = {(0, 0, "F.Cu"): "A", (10, 0, "B.Cu"): "C", (0, 5, "F.Cu"): "B"}
    r = compare_nets(bad, doc)
    assert r["matched"] == 1 and {m["why"] for m in r["mismatches"]} == {"merged"}


def _strip_x2(data: bytes) -> bytes:
    return re.sub(rb"(?m)^(%T[OADF][^\n]*|G04 #@![^\n]*|;\s*#@![^\n]*)\r?\n", b"", data)


def test_a_kicad_demo_without_x2_has_the_nets_its_x2_export_names():
    fab = FIXTURES / "royalblue54L_feather" / "fab"
    files = {p.name: p.read_bytes() for p in fab.iterdir() if p.is_file()}
    x2 = read_gerber_copper_files(files)
    truth = {(p.at[0], p.at[1], lid): p.net or f"~{p.ref}.{p.number}" for p in x2.pads for lid in p.layers}
    plain = {k: _strip_x2(v) for k, v in files.items() if not k.endswith(".gbrjob")}
    assert not any(b"%TO.N" in v for v in plain.values())
    doc, conn = connect_files(plain)
    assert conn.names == "geometry" and len(doc.layers) == 8
    r = compare_nets(truth, doc)
    # the only difference is JP1, a solder jumper bridged in copper: the copper is one net there
    assert {m["net"] for m in r["mismatches"]} == {"VDD", "Net-(JP1-A)"}
    assert r["matched"] == r["nets"] - 2
