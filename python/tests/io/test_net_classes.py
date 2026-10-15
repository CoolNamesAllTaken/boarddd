"""Net classes and impedance targets from the CAD files (docs/nets.md "From the CAD files"): IPC-2581 Specs (rev C and
rev B forms), ODB++ net attributes and impedance.xml, Eagle classes and copper, impedance files (CSV, JSON, xlsx).
Fixtures: fixtures/cad (make_cad.py, built on KiCad's pic_programmer demo exports and synthetic files)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from boarddd import nets as bn
from boarddd.io import eagle, impedance_file, ipc2581, odbpp
from boarddd.validate import validate_board, validate_copper

from conftest import FIXTURES

CAD = FIXTURES / "cad"


def _by_name(classes):
    return {c.name: c for c in classes}


def test_fixtures_are_current():
    run = subprocess.run([sys.executable, str(CAD / "make_cad.py"), "--check"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_ipc2581_specs():
    nets, classes, warnings = ipc2581.read_net_classes(CAD / "pic_programmer-impedance-ipc2581.xml.gz")
    assert warnings == []
    c = _by_name(classes)
    assert set(c) == {"SE50", "DP90", "CPW55", "Z75"}
    se = c["SE50"].impedance
    assert (c["SE50"].nets, se.kind, se.target, se.tolerance_pct, se.structure) == (
        ["/VPP_ON"],
        "single",
        50,
        10,
        "microstrip",
    )
    assert [(la.layer, la.width, la.ref_top) for la in se.layers] == [("F.Cu", 0.5, "B.Cu")]
    dp = c["DP90"]
    assert dp.impedance.kind == "differential" and dp.impedance.layers == []  # LogicalNet: the whole net
    assert (dp.diff_pair_width, dp.diff_pair_gap, dp.impedance.tolerance_pct) == (
        0.2032,
        0.1968,
        10,
    )  # 8 mil, pitch 0.4
    cpw = c["CPW55"].impedance
    assert (cpw.structure, cpw.layers[0].layer, cpw.layers[0].coplanar_gap, cpw.layers[0].gap) == (
        "coplanar_grounded",
        "B.Cu",
        0.15,
        None,
    )
    z75 = c["Z75"]  # rev B: Impedance per type, with Property
    assert (z75.nets, z75.impedance.target, z75.impedance.tolerance_pct, z75.track_width) == (
        ["/CLOCK-RB6"],
        75,
        10,
        0.25,
    )
    pairs = {n.name: n.pair for n in nets if n.pair}
    assert pairs == {"/PC-CLOCK-OUT": "/PC-DATA-OUT", "/PC-DATA-OUT": "/PC-CLOCK-OUT"}


def test_ipc2581_full_reader_carries_them():
    board = ipc2581.read_ipc2581(CAD / "pic_programmer-impedance-ipc2581.xml.gz")
    assert {c.name for c in board.net_classes} == {"SE50", "DP90", "CPW55", "Z75"}
    assert validate_board(board.to_dict()) == []
    plain = ipc2581.read_ipc2581(FIXTURES / "pic_programmer/exchange/pic_programmer-ipc2581.xml.gz")
    assert plain.net_classes == [] and [n.name for n in plain.nets] == [n.name for n in board.nets]


def test_odbpp_attributes_and_impedance_xml():
    nets, classes, warnings = odbpp.read_net_classes(CAD / "pic_programmer-impedance-odb.zip")
    assert warnings == []
    c = _by_name(classes)
    assert c["DP_90"].nets == ["/PC-CLOCK-OUT", "/PC-DATA-OUT"] and c["DP_90"].impedance.kind == "differential"
    assert c["DP_90"].diff_pair_gap == 0.15 and c["SE_50"].impedance.target == 50
    one, two = c["impedance_1"], c["impedance_2"]
    assert (one.nets, one.impedance.kind, one.impedance.tolerance_pct, one.track_width) == (
        ["/VPP_ON"],
        "single",
        5,
        0.5,
    )
    assert one.impedance.layers[0].layer == "B.Cu"
    assert (two.nets, two.impedance.kind, two.impedance.tolerance_pct) == (
        ["/PC-CLOCK-OUT", "/PC-DATA-OUT"],
        "differential",
        10,
    )
    assert (two.diff_pair_width, two.diff_pair_gap) == (0.2032, 0.1524)  # mil, the default unit
    by = {n.name: n for n in nets}
    assert by["/PC-CLOCK-OUT"].pair == "/PC-DATA-OUT" and by["/VPP_ON"].net_class == "SE_50"


def test_odbpp_from_expanded_files():
    import zipfile

    with zipfile.ZipFile(CAD / "pic_programmer-impedance-odb.zip") as z:
        files = {f"upload/pic/{n}": z.read(n) for n in z.namelist() if not n.endswith("/")}
    _, classes, _ = odbpp.read_net_classes(odbpp.Job.from_files(files))
    assert "impedance_2" in {c.name for c in classes}


def test_odbpp_full_reader_and_names():
    board = odbpp.read_odbpp(CAD / "pic_programmer-impedance-odb.zip")
    assert {"DP_90", "SE_50", "impedance_1", "impedance_2"} <= {c.name for c in board.net_classes}
    assert "/PC-CLOCK-OUT" in {n.name for n in board.nets}  # not '/PC-CLOCK-OUT;0=0,...'
    plain = odbpp.read_odbpp(FIXTURES / "pic_programmer/exchange/pic_programmer-odb.zip")
    assert plain.net_classes == [] and sorted(n.name for n in plain.nets) == sorted(n.name for n in board.nets)


def test_eagle_board_and_copper():
    board = eagle.read_eagle_brd(CAD / "usb.brd")
    assert board.source.kind == "eagle" and validate_board(board.to_dict()) == []
    c = _by_name(board.net_classes)
    assert c["DP_90"].nets == ["USB_D+", "USB_D-"] and c["DP_90"].impedance.kind == "differential"
    assert (c["DP_90"].diff_pair_width, c["DP_90"].clearance) == (0.2, 0.15)
    assert c["50ohm"].impedance.target == 50 and c["50ohm"].track_width == 0.35 and c["Default"].impedance is None
    copper = eagle.read_eagle_copper(CAD / "usb.brd")
    assert validate_copper(copper.to_dict()) == []
    assert copper.layers == ["F.Cu", "B.Cu"] and copper.vias[0].span == ("F.Cu", "B.Cu")
    arc = next(t for t in copper.tracks if t.mid)
    assert arc.mid == pytest.approx((13.414214, 5.760786))  # 90° counter-clockwise from (12, 5.175) to (14, 7.175)
    s = bn.similar_tracks(copper.to_dict(), {"layer": "F.Cu", "nets": ["USB_D+", "USB_D-"]})
    assert s["pairs"][0]["nets"] == ["USB_D+", "USB_D-"] and s["gap"] == pytest.approx(0.15, abs=0.002)
    with pytest.raises(ValueError):
        eagle.read_eagle_brd(b"\x10\x80binary eagle 5")


NAMES = [
    "USB_DP",
    "USB_DN",
    "RF_OUT",
    "ANT_1",
    "CLK1",
    "DDR_DQ0",
    "DDR_DQS0",
    "PCIE_TX_P",
    "PCIE_TX_N",
    "LVDS+",
    "LVDS-",
]
LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


def test_impedance_csv():
    classes, warnings = impedance_file.read_impedance_file(CAD / "impedance.csv", names=NAMES, layers=LAYERS)
    assert warnings == []
    c = _by_name(classes)
    usb = c["USB"]
    assert (usb.nets, usb.impedance.kind, usb.impedance.tolerance_pct, usb.impedance.source) == (
        ["USB_DP", "USB_DN"],
        "differential",
        10,
        "file",
    )
    assert [(la.layer, la.width, la.gap) for la in usb.impedance.layers] == [("F.Cu", 0.2, 0.15), ("B.Cu", 0.18, 0.17)]
    assert c["RF"].nets == ["RF_OUT", "ANT_1"] and c["RF"].impedance.tolerance_pct == 10  # 5 ohm of 50
    assert c["DDR"].impedance.layers[0].layer == "In2.Cu" and c["DDR"].track_width == 0.1016  # L3, 4 mil
    assert "CLK" not in c  # no target


def test_impedance_json_and_xlsx():
    classes, warnings = impedance_file.read_impedance_file(CAD / "impedance.json", names=NAMES, layers=LAYERS)
    c = _by_name(classes)
    assert warnings == []
    assert c["PCIE"].nets == ["PCIE_TX_P", "PCIE_TX_N"] and c["PCIE"].impedance.layers[0].ref_bottom == "In3.Cu"
    assert c["RF"].impedance.structure == "coplanar_grounded"
    assert c["LVDS"].impedance.kind == "differential"  # a Zdiff column, and the nets pair up
    rows, warnings = impedance_file.read_impedance_file(CAD / "impedance_control.xlsx", layers=LAYERS)
    assert warnings == []
    got = [
        (
            r.impedance.kind,
            r.impedance.target,
            r.impedance.layers[0].layer,
            r.impedance.layers[0].width,
            r.impedance.layers[0].gap,
        )
        for r in rows
    ]
    assert got == [
        ("single", 50, "F.Cu", 0.3048, None),
        ("differential", 90, "F.Cu", 0.1524, 0.1524),
        ("differential", 100, "B.Cu", 0.127, 0.1778),
    ]


def test_impedance_file_rules():
    text = "Net class;Z0 (ohm);Tol;Nets\nRF;50;±5 %;RF_* bad[\n;abc;;X\n"
    classes, warnings = impedance_file.read_impedance_file(text, names=["RF_A"])
    assert [c.name for c in classes] == ["RF"] and classes[0].nets == ["RF_A"]
    assert any("bad[" in w for w in warnings) and any("abc" in w for w in warnings)
    assert impedance_file.read_impedance_file("a,b\n1,2\n")[1] == [
        "No column for the target impedance (target, ohms, impedance, Z0, Zdiff)."
    ]
    assert impedance_file.is_impedance_file_name("fab/board-impedance.csv")
    assert impedance_file.is_impedance_file_name("impedance_control.xlsx")
    assert not impedance_file.is_impedance_file_name("board-bom.csv")
