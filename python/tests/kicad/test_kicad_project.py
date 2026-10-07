"""Net classes from .kicad_pro: assignment (explicit, patterns, priority, Default inheritance) and impedance
targets (KiCad 10 tuning profiles, then the class name convention)."""

import json

import pytest

from boarddd import model as m
from boarddd.io.kicad.project import assign_nets, impedance_from_name, read_kicad_pro


@pytest.mark.parametrize(
    "name, want",
    [
        ("SE_50_CP", ("single", 50, None, "coplanar")),
        ("SE_50", ("single", 50, None, None)),
        ("DP_90_MS", ("differential", 90, None, "microstrip")),
        ("DP_100_SL", ("differential", 100, None, "stripline")),
        ("BAL_D90_C30_CPWG", ("differential", 90, 30, "coplanar_grounded")),
        ("50ohm", ("single", 50, None, None)),
        ("100ohm", ("single", 100, None, None)),  # differential only once its nets turn out to be pairs
        ("85Ohm-diff_PCIE", ("differential", 85, None, None)),
        ("90Ohm-diff_USB_2.0", ("differential", 90, None, None)),
        ("zse_50r", ("single", 50, None, None)),
        ("SE_37.5_MS", ("single", 37.5, None, "microstrip")),
    ],
)
def test_name_convention(name, want):
    t = impedance_from_name(name)
    assert (t.kind, t.target, t.common_mode, t.structure) == want and t.source == "name"


@pytest.mark.parametrize("name", ["Default", "USB_DIFF", "Power", "DDR4_BYTE0", "pwrhi", "FPGA_HP", "usbdiff", "USB_2.0"])
def test_names_without_a_target(name):
    assert impedance_from_name(name) is None


PRO = {
    "net_settings": {
        "classes": [
            {"name": "Default", "track_width": 0.2, "clearance": 0.15, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
             "via_diameter": 0.6, "via_drill": 0.3, "priority": 2147483647, "tuning_profile": ""},
            {"name": "90ohm", "track_width": 0.147, "diff_pair_gap": 0.154, "priority": 2},
            {"name": "50ohm", "track_width": 0.13, "priority": 1},
            {"name": "RF", "priority": 0, "tuning_profile": "CPWG 50"},
            {"name": "USB", "priority": 3, "tuning_profile": "USB 90"},
        ],
        "netclass_patterns": [
            {"netclass": "90ohm", "pattern": "/*USB_*"},
            {"netclass": "50ohm", "pattern": "/RF*"},
            {"netclass": "RF", "pattern": "/RF/ANT"},
        ],
        "netclass_assignments": {"/CM5/USB3_D+": ["USB"], "/CM5/USB3_D-": ["USB"]},
    },
    # KiCad 10's own encoding (common/project/tuning_profiles.cpp): type 0/1, lengths in nm
    "tuning_profiles": {
        "meta": {"version": 0},
        "tuning_profiles_impedance_geometric": [
            {"profile_name": "CPWG 50", "type": 0, "target_impedance": 50.0, "enable_time_domain_tuning": False,
             "via_prop_delay": 0, "via_overrides": [],
             "layer_entries": [{"signal_layer": "F.Cu", "top_reference_layer": "UNDEFINED",
                                "bottom_reference_layer": "In1.Cu", "width": 300000, "diff_pair_gap": 0, "delay": 0}]},
            {"profile_name": "USB 90", "type": 1, "target_impedance": 90.0, "enable_time_domain_tuning": False,
             "via_prop_delay": 0, "via_overrides": [],
             "layer_entries": [
                 {"signal_layer": "F.Cu", "top_reference_layer": "UNDEFINED", "bottom_reference_layer": "In1.Cu",
                  "width": 190000, "diff_pair_gap": 180000, "delay": 0},
                 {"signal_layer": "In2.Cu", "top_reference_layer": "In1.Cu", "bottom_reference_layer": "In3.Cu",
                  "width": 150000, "diff_pair_gap": 125000, "delay": 0}]},
        ],
    },
}
NETS = ["GND", "/CM5/USB_D+", "/CM5/USB_D-", "/RF/ANT", "/RF/LNA_IN", "/CM5/USB3_D+", "/CM5/USB3_D-"]


@pytest.fixture
def classes():
    nets = [m.Net(name=n, pair={"+": n[:-1] + "-", "-": n[:-1] + "+"}.get(n[-1])) for n in NETS]
    warnings = []
    out = {c.name: c for c in assign_nets(nets, read_kicad_pro(json.dumps(PRO)), warnings)}
    assert warnings == []
    return out, {n.name: n.net_class for n in nets}


def test_assignment(classes):
    cls, of = classes
    assert of == {
        "GND": "Default",
        "/CM5/USB_D+": "90ohm",
        "/CM5/USB_D-": "90ohm",
        "/RF/ANT": "RF",  # matches 50ohm's '/RF*' too: RF has the lower priority number
        "/RF/LNA_IN": "50ohm",
        "/CM5/USB3_D+": "USB",  # explicit, and 90ohm's pattern
        "/CM5/USB3_D-": "USB",
    }
    assert cls["50ohm"].nets == ["/RF/ANT", "/RF/LNA_IN"] and cls["50ohm"].patterns == ["/RF*"]
    # a class without a value takes Default's (KiCad 8+)
    assert (cls["90ohm"].track_width, cls["90ohm"].diff_pair_gap, cls["90ohm"].clearance) == (0.147, 0.154, 0.15)
    assert cls["RF"].track_width == 0.2


def test_targets(classes):
    cls, _ = classes
    assert cls["Default"].impedance is None
    assert cls["50ohm"].impedance == m.ImpedanceTarget(kind="single", target=50, source="name")
    # '90ohm' says no 'diff', but all of its nets are pair halves
    assert cls["90ohm"].impedance.kind == "differential" and cls["90ohm"].impedance.target == 90
    rf = cls["RF"].impedance
    assert (rf.kind, rf.target, rf.source) == ("single", 50.0, "tuning_profile")
    assert rf.layers == [m.ImpedanceLayer(layer="F.Cu", width=0.3, gap=None, ref_top=None, ref_bottom="In1.Cu")]
    usb = cls["USB"].impedance
    assert (usb.kind, usb.target, cls["USB"].tuning_profile) == ("differential", 90.0, "USB 90")
    assert [(e.layer, e.width, e.gap, e.ref_top, e.ref_bottom) for e in usb.layers] == [
        ("F.Cu", 0.19, 0.18, None, "In1.Cu"),
        ("In2.Cu", 0.15, 0.125, "In1.Cu", "In3.Cu"),
    ]


def test_missing_profile_warns():
    pro = json.loads(json.dumps(PRO))
    pro["net_settings"]["classes"][3]["tuning_profile"] = "nope"
    warnings = []
    out = {c.name: c for c in assign_nets([m.Net(name="/RF/ANT")], read_kicad_pro(pro), warnings)}
    assert any("nope" in w for w in warnings)
    assert out["RF"].impedance is None  # 'RF' names no value either
