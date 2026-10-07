"""boarddd.io.odbpp: archives, compression, symbols, conventions, and KiCad's demo boards.

Ported from magpie's tests/pcb/test_odbpp.py (magpie commit 3a0374d3). `read_components` is magpie's
`read_odbpp` (component level, `boarddd.io.eda` types); `read_odbpp` now returns a `boarddd.model.Board`.
The KiCad jobs are KiCad 10.0.6's exports of the royalblue54L_feather and pic_programmer demos
(fixtures/make_exchange.sh); magpie's Altium and DipTrace jobs are not boarddd fixture sources and their
tests are not ported.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile

import pytest

from boarddd.io.eda import Placement
from boarddd.io.odbpp import read_components, read_odbpp, symbol_shape, uncompress_z
from boarddd.model import Board
from boarddd.validate import validate_board

from conftest import FIXTURES

KICAD_ODB = FIXTURES / "royalblue54L_feather" / "exchange" / "RoyalBlue54L-Feather-odb.zip"
PIC_ODB = FIXTURES / "pic_programmer" / "exchange" / "pic_programmer-odb.zip"


# ─── .Z (Unix compress) ──────────────────────────────────────────────────────


def compress_z(data: bytes, max_bits: int = 16) -> bytes:
    """A plain LZW encoder in `compress`'s format (block mode, no CLEAR), for the tests."""
    table = {bytes([i]): i for i in range(256)}
    next_code, bits = 257, 9
    out, acc, nacc = bytearray(b"\x1f\x9d" + bytes([0x80 | max_bits])), 0, 0
    codes: list[tuple[int, int]] = []
    w = b""
    for ch in data:
        wc = w + bytes([ch])
        if wc in table:
            w = wc
            continue
        codes.append((table[w], bits))
        if next_code < (1 << max_bits):
            table[wc] = next_code
            next_code += 1
            if next_code > (1 << bits) and bits < max_bits:
                codes.append((None, bits))  # marks the width change
                bits += 1
        w = bytes([ch])
    if w:
        codes.append((table[w], bits))
    # compress pads each run of codes of one width to a multiple of that width in bytes.
    group = bytearray()
    for code, width in codes:
        if code is None:
            if nacc:
                group.append(acc & 0xFF)
                acc, nacc = 0, 0
            while len(group) % width:
                group.append(0)
            out += group
            group = bytearray()
            continue
        acc |= code << nacc
        nacc += width
        while nacc >= 8:
            group.append(acc & 0xFF)
            acc >>= 8
            nacc -= 8
    if nacc:
        group.append(acc & 0xFF)
    out += group
    return bytes(out)


@pytest.mark.parametrize(
    "data", [b"", b"a", b"TOBEORNOTTOBEORTOBEORNOT" * 3, bytes(range(256)) * 40, b"P 1.0 2.0 0 P 0 8 90.0;\n" * 3000]
)
def test_uncompress_z_round_trip(data):
    assert uncompress_z(compress_z(data)) == data


def test_uncompress_z_rejects_other_data():
    with pytest.raises(ValueError):
        uncompress_z(b"PK\x03\x04")


# ─── Archives and compressed members ─────────────────────────────────────────


def _kicad_members() -> dict[str, bytes]:
    with zipfile.ZipFile(KICAD_ODB) as z:
        return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}


def _tgz(members: dict[str, bytes], root: str = "job") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _signature(board) -> list:
    return [(c.reference, c.placement, c.footprint.pads) for c in board.components]


def test_zip_tgz_directory_and_compressed_members_read_the_same(tmp_path):
    reference = _signature(read_components(KICAD_ODB))
    members = _kicad_members()
    assert _signature(read_components(_tgz(members))) == reference
    squeezed = {}
    for name, data in members.items():
        if name.endswith("eda/data"):
            squeezed[name + ".Z"] = compress_z(data)
        elif name.endswith("/features") or name.endswith("/components"):
            squeezed[name + ".gz"] = gzip.compress(data)
        else:
            squeezed[name] = data
    assert _signature(read_components(_tgz(squeezed))) == reference
    for name, data in members.items():
        path = tmp_path / "job" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert _signature(read_components(tmp_path / "job")) == reference


def test_not_an_odb_job():
    with pytest.raises(ValueError):
        read_components(b"not an archive at all")


# ─── Symbols ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,shape,size,extra",
    [
        ("r750", "circle", (0.75, 0.75), {}),
        ("s500", "rect", (0.5, 0.5), {}),
        ("rect1150.0x1800.0", "rect", (1.15, 1.8), {}),
        ("rect1150.0x1800.0xr250.0", "roundrect", (1.15, 1.8), {"roundrect_ratio": 0.217391}),
        ("rect1000x1000xc200x13", "chamfered", (1.0, 1.0), {"chamfered": ("top_right", "bottom_left")}),
        ("oval1200x1750", "oval", (1.2, 1.75), {}),
        ("el1000x500", "oval", (1.0, 0.5), {}),
        ("donut_r1000x500", "circle", (1.0, 1.0), {}),
        ("di1000x600", "polygon", (1.0, 0.6), {}),
        ("oct1000x1000x200", "polygon", (1.0, 1.0), {}),
    ],
)
def test_standard_symbols_in_microns(name, shape, size, extra):
    s = symbol_shape(name, 0.001)
    assert s.shape == shape and s.size == pytest.approx(size)
    for key, value in extra.items():
        assert getattr(s, key) == (pytest.approx(value) if isinstance(value, float) else value)


def test_symbols_in_mils():
    assert symbol_shape("r20", 0.0254).size == pytest.approx((0.508, 0.508))


def test_unknown_symbol():
    assert symbol_shape("moire1x2x3", 0.001) is None


# ─── Conventions, on a hand-written job ──────────────────────────────────────

MATRIX = """STEP {
   COL=1
   NAME=PCB
}
""" + "".join(
    f"""
LAYER {{
   ROW={row}
   CONTEXT=BOARD
   TYPE={kind}
   NAME={name}
   POLARITY=POSITIVE
}}
"""
    for row, kind, name in (
        (1, "COMPONENT", "COMP_+_TOP"),
        (2, "SOLDER_PASTE", "TOP_PASTE"),
        (3, "SIGNAL", "TOP"),
        (4, "SIGNAL", "BOTTOM"),
        (5, "SOLDER_PASTE", "BOTTOM_PASTE"),
        (6, "COMPONENT", "COMP_+_BOT"),
    )
)

PINS = [("1", -1.5, 0.0), ("2", 1.5, 0.5)]  # an asymmetric two-pin package, top view


def _job(rotation: float, *, mirrored_package: bool, source: str) -> bytes:
    """One bottom-side component, toeprints placed by the convention under test."""
    flip = -1 if mirrored_package else 1
    model_rotation = -rotation if mirrored_package else 180 + rotation
    placement = Placement("U1", 10.0, 5.0, model_rotation % 360, "bottom")
    eda = "HDR test\nUNITS=MM\nLYR top bottom\n# PKG 0\nPKG TWO 1.0 -2 -1 2 1;\n"
    for name, x, y in PINS:
        eda += f"PIN {name} S {x} {y * flip} 0 E S\nCR {x} {y * flip} 0.3\n"
    comp = f"UNITS=MM\nCMP 0 10.0 5.0 {rotation} {'M' if mirrored_package else 'N'} U1 PART ;\n"
    comp += "PRP MPN 'ABC-1'\n"
    for i, (name, x, y) in enumerate(PINS):
        bx, by = placement.to_board((x, y))
        comp += f"TOP {i} {bx} {by} 0 N 0 0 {name}\n"
    members = {
        "matrix/matrix": MATRIX.encode(),
        "misc/info": f"ODB_SOURCE={source}\n".encode(),
        "steps/pcb/eda/data": eda.encode(),
        "steps/pcb/layers/comp_+_bot/components": comp.encode(),
    }
    return _tgz(members)


@pytest.mark.parametrize("rotation", [0, 30, 90, 180, 315])
@pytest.mark.parametrize("mirrored_package,source", [(False, "Xpedition xPCB Layout"), (True, "KiCad EDA 10.0.6")])
def test_bottom_side_conventions(rotation, mirrored_package, source):
    """Top-view packages read as 180 + r (the format's convention, as Xpedition, Valor and
    Altium write it); KiCad's mirrored packages as -r. Either way the footprint comes back
    as the top view, and the pins land on the toeprints."""
    b = read_components(_job(rotation, mirrored_package=mirrored_package, source=source))
    u1 = b.components[0]
    assert b.warnings == [] and u1.placement.side == "bottom"
    want = (-rotation if mirrored_package else 180 + rotation) % 360
    assert u1.placement.rotation == pytest.approx(want)
    centers = {p.number: p.center for p in u1.footprint.pads}
    assert centers == {"1": pytest.approx((-1.5, 0.0)), "2": pytest.approx((1.5, 0.5))}
    assert u1.mpns == ("ABC-1",)


def test_kicad_demo_board():
    b = read_components(KICAD_ODB)
    assert (b.name, b.units, b.revision, b.warnings) == ("pcb", "MM", "8.1", [])
    assert len(b.components) == 70  # LOGO1 has no pads: nothing in the job places it
    c1 = b.by_reference()["C1"]
    assert c1.placement.rotation == pytest.approx(270.0)  # KiCad's -90; the file is clockwise
    assert c1.footprint.pads[0].shape == "roundrect"
    assert c1.footprint.pads[0].roundrect_ratio == pytest.approx(0.25)
    assert all(p.layers == ("F.Cu", "F.Paste", "F.Mask") for p in c1.footprint.pads)
    j1 = b.by_reference()["J1"]
    assert j1.placement.side == "bottom" and j1.placement.rotation == pytest.approx(270.0)
    assert j1.mount == "tht" and all(p.drill and p.drill.plated for p in j1.footprint.pads)
    assert not j1.populate and not b.by_reference()["R2"].populate


def test_kicad_demo_board_through_hole():
    b = read_components(PIC_ODB)
    assert (b.name, b.warnings) == ("pcb", [])
    assert len(b.components) == 63
    u2 = b.by_reference()["U2"]
    assert u2.mount == "tht" and u2.placement.rotation == pytest.approx(90.0)
    assert all(p.drill.size == pytest.approx((0.8, 0.8)) for p in u2.footprint.pads)
    assert b.by_reference()["JP1"].placement.side == "bottom"


@pytest.mark.parametrize("path", [KICAD_ODB, PIC_ODB], ids=["royalblue", "pic_programmer"])
def test_the_board_model_is_valid_and_round_trips(path):
    d = read_odbpp(path).to_dict()
    assert validate_board(d) == []
    assert Board.from_dict(d).to_dict() == d


# ─── Attributes, invented pad names, slots and precision ─────────────────────


def _attributed_job(attrs: str, *, pin: str = "1", source: str = "KiCad EDA 10.0.6") -> bytes:
    eda = (
        "HDR test\nUNITS=MM\nLYR top\n# PKG 0\nPKG TWO 1.0 -2 -1 2 1;\n"
        f"PIN {pin} S -1.5 0.0 0 E S\nCR -1.5 0.0 0.3\nPIN 2 S 1.5 0.5 0 E S\nCR 1.5 0.5 0.3\n"
    )
    comp = (
        "UNITS=MM\n@0 .comp_height\n@1 .no_pop\n"
        f"CMP 0 10.00 5.00 0 N U1 PART ;{attrs}\n"
        f"TOP 0 8.50 5.00 0 N 0 0 {pin}\nTOP 1 11.50 5.50 0 N 0 1 2\n"
    )
    members = {
        "matrix/matrix": MATRIX.encode(),
        "misc/info": f"ODB_SOURCE={source}\n".encode(),
        "steps/pcb/eda/data": eda.encode(),
        "steps/pcb/layers/comp_+_top/components": comp.encode(),
    }
    return _tgz(members)


def test_no_pop_and_component_height():
    u1 = read_components(_attributed_job("0=1.25,1")).components[0]
    assert not u1.populate and u1.height == pytest.approx(1.25)
    u1 = read_components(_attributed_job("")).components[0]
    assert u1.populate and u1.height is None


def test_invented_pad_names_and_resolution():
    pads = read_components(_attributed_job("", pin="PAD3")).components[0].footprint.pads
    assert any(p.number is None and p.name == "PAD3" for p in pads)
    other = read_components(_attributed_job("", pin="PAD3", source="Xpedition")).components[0]
    assert any(p.number == "PAD3" for p in other.footprint.pads)
    source = other.footprint.source
    assert source.resolution == pytest.approx(0.01) and "2 decimals, MM" in source.resolution_basis
