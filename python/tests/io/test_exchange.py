"""IPC-2581 and ODB++ against the .kicad_pcb they were exported from (KiCad's royalblue54L_feather and
pic_programmer demos, exported by fixtures/make_exchange.sh with kicad-cli 10.0.6).

read_ipc2581 / read_odbpp vs read_kicad_pcb: components (refs, positions, rotations, sides, values), every
pad placed on the board, nets, the outline and the drills, within each format's precision (IPC-2581: 6
decimals; KiCad's ODB++: 2, so 0.005 mm per coordinate). docs/readers.md lists the differences pinned here.
"""

import math
from collections import Counter

import pytest

from boarddd.io.ipc2581 import read_ipc2581
from boarddd.io.kicad import read_kicad_pcb
from boarddd.io.odbpp import read_odbpp
from boarddd.io.package import read_package
from boarddd.validate import validate_board

from conftest import FIXTURES

BOARDS = {
    "royalblue": (
        FIXTURES / "royalblue54L_feather" / "kicad" / "RoyalBlue54L-Feather.kicad_pcb",
        FIXTURES / "royalblue54L_feather" / "exchange" / "RoyalBlue54L-Feather",
    ),
    "pic_programmer": (
        FIXTURES / "pic_programmer" / "kicad" / "pic_programmer.kicad_pcb",
        FIXTURES / "pic_programmer" / "exchange" / "pic_programmer",
    ),
}
#: Coordinate tolerance per format, mm: IPC-2581 at KiCad's default 6 decimals, ODB++ at its default 2.
TOL = {"ipc2581": 0.0015, "odbpp": 0.0075}
#: Components an export leaves out: KiCad's ODB++ writes no component for a footprint without pads.
MISSING = {("royalblue", "odbpp"): ["LOGO1"]}
#: Pads read differently, by reason (see docs/readers.md):
#: - offset: KiCad 10.0.6's IPC-2581 export writes a pad's copper offset ((drill (offset))) along board x without
#:   the part's rotation, so the copper of J1's and J2's offset rect pads (bottom side, 270 deg) lands 0.21 mm off;
#:   the ODB++ export places them right;
#: - merged: a paste-only/thermal sub-pad the exporter merges into its neighbour (U2/U5 exposed pads, J2's tab).
OFFSET = {("royalblue", "ipc2581"): {"J1": 16, "J2": 11}}
FEWER_PADS = {
    ("royalblue", "ipc2581"): {"U2": (58, 59), "U5": (66, 67)},
    ("royalblue", "odbpp"): {"J2": (24, 25), "U5": (66, 67)},
}


@pytest.fixture(scope="module", params=list(BOARDS))
def board(request):
    return request.param


_cache: dict = {}


def read(board: str, fmt: str):
    key = (board, fmt)
    if key not in _cache:
        pcb, stem = BOARDS[board]
        if "kicad" not in _cache.get(board, {}):
            _cache.setdefault(board, {})["kicad"] = read_kicad_pcb(pcb)
        _cache[key] = read_ipc2581(f"{stem}-ipc2581.xml.gz") if fmt == "ipc2581" else read_odbpp(f"{stem}-odb.zip")
    return _cache[key], _cache[board]["kicad"]


FORMATS = ["ipc2581", "odbpp"]


def _angle(a: float) -> float:
    return abs((a + 180) % 360 - 180)


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_model_is_valid_and_round_trips(board, fmt):
    b, _ = read(board, fmt)
    d = b.to_dict()
    assert validate_board(d) == []
    assert type(b).from_dict(d).to_dict() == d
    assert b.source.kind == fmt and b.source.generator.endswith("10.0.6")


@pytest.mark.parametrize("fmt", FORMATS)
def test_components_sit_where_the_board_file_puts_them(board, fmt):
    b, k = read(board, fmt)
    kc = {c.ref: c for c in k.components}
    assert sorted(set(kc) - {c.ref for c in b.components}) == MISSING.get((board, fmt), [])
    for c in b.components:
        g = kc[c.ref]
        assert math.hypot(c.x - g.x, c.y - g.y) <= TOL[fmt], c.ref
        assert _angle(c.rotation - g.rotation) < 1e-6, c.ref
        assert (c.side, c.value or None) == (g.side, g.value or None), c.ref
        assert c.footprint in b.footprints


def _place(c, fx, fy):
    """docs/model.md: p = c + R(rotation) q, q = (fx, -fy) top / (fx, fy) bottom."""
    qy = fy if c.side == "bottom" else -fy
    a = math.radians(c.rotation)
    return c.x + math.cos(a) * fx - math.sin(a) * qy, c.y + math.sin(a) * fx + math.cos(a) * qy


def _extent(pad):
    """The copper's footprint-frame box: a custom pad's primitives (plus its anchor), else the pad's size
    about its copper centre (its hole plus the drill offset), rotated."""
    if pad.shape == "custom":
        pts = [(pad.at[0] + x, pad.at[1] + y) for prim in pad.primitives or [] for x, y in prim.pts]
        if pad.size[0] > 0.02:  # KiCad's own anchor (the exports' is a 0.01 mm token)
            w, h = pad.size
            pts += [(pad.at[0] + sx * w / 2, pad.at[1] + sy * h / 2) for sx in (-1, 1) for sy in (-1, 1)]
    else:
        ox, oy = pad.drill.offset if pad.drill else (0.0, 0.0)
        a = math.radians(-pad.at[2])  # KiCad pad angles are counter-clockwise on screen, y down
        c, s = math.cos(a), math.sin(a)
        w, h = pad.size
        pts = []
        for x, y in [(ox + sx * w / 2, oy + sy * h / 2) for sx in (-1, 1) for sy in (-1, 1)]:
            pts.append((pad.at[0] + x * c - y * s, pad.at[1] + x * s + y * c))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs), max(ys) - min(ys)


def _board_pads(b):
    out = {}
    for c in b.components:
        fp = b.footprints[c.footprint]
        for p in fp.pads:
            cx, cy, w, h = _extent(p)
            x, y = _place(c, cx, cy)
            out.setdefault(c.ref, []).append((p.number, x, y, sorted((round(w, 3), round(h, 3))), p.type, p.drill))
    return out


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_pad_lands_on_the_board_files_pad(board, fmt):
    b, k = read(board, fmt)
    ours, theirs = _board_pads(b), _board_pads(k)
    tol = TOL[fmt] * 2 + 1e-3
    fewer, offset = {}, Counter()
    for ref, pads in ours.items():
        kk = theirs[ref]
        if len(pads) != len(kk):
            fewer[ref] = (len(pads), len(kk))
            continue
        for number, x, y, size, ptype, drill in pads:
            same = [q for q in kk if q[0] == number]
            assert same, (ref, number)
            # stacked pads share a number and a position (an exposed pad over its thermal vias): size decides
            q = min(
                same,
                key=lambda q: math.hypot(q[1] - x, q[2] - y) + sum(abs(a - c) for a, c in zip(size, q[3], strict=True)),
            )
            d = math.hypot(q[1] - x, q[2] - y)
            if d > tol:
                offset[ref] += 1
                # the 0.21 mm offset applied along board x instead of the rotated pad x: 0.21 * sqrt(2) off
                assert abs(d - 0.21 * math.sqrt(2)) < tol, (ref, number, d)
                continue
            assert max(abs(a - b_) for a, b_ in zip(size, q[3], strict=True)) <= tol * 2, (ref, number, size, q[3])
            # KiCad's "connect" pads (no hole, no paste/mask) are SMD pads in both exports
            assert ptype == q[4] or (ptype, q[4]) == ("smd", "connect"), (ref, number)
            if drill and q[5]:
                assert max(abs(a - b_) for a, b_ in zip(sorted(drill.size), sorted(q[5].size), strict=True)) <= tol
    assert fewer == FEWER_PADS.get((board, fmt), {})
    assert dict(offset) == OFFSET.get((board, fmt), {})


@pytest.mark.parametrize("fmt", FORMATS)
def test_nets_are_the_board_files(board, fmt):
    b, k = read(board, fmt)
    assert sorted(n.name for n in b.nets) == sorted(n.name for n in k.nets)
    assert len(b.nets) == {"royalblue": 95, "pic_programmer": 111}[board]


def _segment_distance(p, poly):
    best = math.inf
    for a, c in zip(poly, poly[1:] + poly[:1], strict=True):
        dx, dy = c[0] - a[0], c[1] - a[1]
        n = dx * dx + dy * dy
        t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
        best = min(best, math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]))
    return best


def _area(pts):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True)) / 2


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_outline_is_the_board_files(board, fmt):
    """The same counter-clockwise loop: arcs are flattened differently (the exports carry KiCad's own
    segments for royalblue's rounded corners), so every point lies within 0.012 mm of the other loop."""
    b, k = read(board, fmt)
    ours, theirs = b.outline.board, k.outline.board
    assert _area(ours) > 0 and abs(_area(ours) - _area(theirs)) < 0.06
    assert max(_segment_distance(p, theirs) for p in ours) < 0.012
    assert max(_segment_distance(p, ours) for p in theirs) < 0.012
    assert b.outline.cutouts == k.outline.cutouts == []


def _key(d):
    ends = sorted([(d.x, d.y)] + ([(d.x2, d.y2)] if d.x2 is not None else []))
    return ends, d.diameter, d.plated


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_drills_are_the_board_files(board, fmt):
    """Same holes and slots (ends unordered), plating and diameters; KiCad's 0.00001 mm placeholder via drill
    reads 0 from both."""
    b, k = read(board, fmt)
    assert len(b.drills) == len(k.drills)
    pool = [_key(d) for d in k.drills]
    for d in b.drills:
        ends, dia, plated = _key(d)
        j = min(
            range(len(pool)),
            key=lambda i: sum(math.dist(p, q) for p, q in zip(ends, pool[i][0], strict=False)) + abs(pool[i][1] - dia),
        )
        k_ends, k_dia, k_plated = pool.pop(j)
        assert len(ends) == len(k_ends) and all(math.dist(p, q) <= TOL[fmt] for p, q in zip(ends, k_ends, strict=True))
        assert abs(dia - k_dia) <= 0.011 and plated == k_plated
    assert Counter(d.function for d in b.drills)["via"] == Counter(d.function for d in k.drills)["via"]


def test_the_stackups():
    """IPC-2581 carries the build (thickness, per-layer thickness, Er, Df, mask colour); ODB++ carries per-layer
    thickness (to 0.01 mm), Er and Df but no overall thickness or colours."""
    ipc, k = read("royalblue", "ipc2581")
    odb, _ = read("royalblue", "odbpp")
    assert (ipc.stackup.thickness, ipc.stackup.copper_layers) == (k.stackup.thickness, 8) == (1.6, 8)
    assert ipc.stackup.mask_color.top == k.stackup.mask_color.top == "Blue"
    assert odb.stackup.thickness is None and odb.stackup.copper_layers == 8
    for st in (ipc.stackup, odb.stackup):
        diel = [la for la in st.layers if la.kind == "dielectric"]
        assert [la.thickness for la in diel] == [0.1, 0.3, 0.1, 0.3, 0.1, 0.3, 0.1]
        assert {(la.epsilon_r, la.loss_tangent, la.material) for la in diel} == {(4.5, 0.02, "FR4")}
        assert [la.layer for la in st.layers if la.kind == "copper"] == [
            la.id for la in ipc.layers if la.role == "copper"
        ]


def test_layers_use_kicad_ids_and_the_package_order():
    for fmt in FORMATS:
        b, _ = read("royalblue", fmt)
        roles = [(la.id, la.order) for la in b.layers if la.role in ("copper", "mask", "paste", "silk", "outline")]
        assert roles == [
            ("F.Cu", 1),
            ("In1.Cu", 2),
            ("In2.Cu", 3),
            ("In3.Cu", 4),
            ("In4.Cu", 5),
            ("In5.Cu", 6),
            ("In6.Cu", 7),
            ("B.Cu", 8),
            ("Edge.Cuts", 9),
            ("F.Paste", 10),
            ("B.Paste", 10),
            ("F.Silkscreen", 11),
            ("B.Silkscreen", 11),
            ("F.Mask", 12),
            ("B.Mask", 12),
        ]


@pytest.mark.parametrize(
    ("path", "kind", "components"),
    [
        ("royalblue54L_feather/exchange/RoyalBlue54L-Feather-ipc2581.xml.gz", "ipc2581", 71),
        ("royalblue54L_feather/exchange/RoyalBlue54L-Feather-odb.zip", "odbpp", 70),
        ("pic_programmer/exchange", "ipc2581", 63),  # a folder with both: IPC-2581 first
    ],
)
def test_read_package_detects_the_format(path, kind, components):
    b = read_package(FIXTURES / path)
    assert b.source.kind == kind and len(b.components) == components
    roles = {f.role for f in b.source.files}
    assert roles <= {"ipc2581", "odb"}


def test_a_package_with_gerbers_stays_a_gerber_read_and_says_so(tmp_path):
    for f in (FIXTURES / "royalblue54L_feather" / "fab").iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    exchange = FIXTURES / "royalblue54L_feather" / "exchange" / "RoyalBlue54L-Feather-ipc2581.xml.gz"
    (tmp_path / exchange.name).write_bytes(exchange.read_bytes())
    b = read_package(tmp_path)
    assert b.source.kind == "gerber"
    assert any("ipc2581 data next to the Gerbers" in w for w in b.warnings)
