"""boarddd.nets (patterns, tracks like a picked one) against its JS twin boarddd/nets: every case of
fixtures/nets/cases.json (made by make_cases.mjs from the JS) gives the same answer here; plus KiCad's own class
assignment on CM5 MINIMA and a few rules spelled out."""

from __future__ import annotations

import gzip
import json

import pytest

from boarddd import nets as bn
from boarddd.io.kicad import read_kicad_pcb
from boarddd.io.kicad.project import read_kicad_pro

from conftest import FIXTURES, load

CASES = load("nets/cases.json")
CM5 = json.loads(gzip.decompress((FIXTURES / "impedance/route/cm5.json.gz").read_bytes()))
COPPER = {"cm5": CM5["copper"], "pair": load("impedance/route/pair.json")["copper"], **CASES["copper"]}
BOARDS = {"cm5": CM5["board"], "pair": load("impedance/route/pair.json")["board"], "lanes": None}


def _no_errors(result: dict) -> dict:
    """Error texts differ between JS and Python regex engines: compare whether there is one."""
    return {**result, "patterns": [{**p, "error": p["error"] is not None} for p in result["patterns"]]}


@pytest.mark.parametrize("case", CASES["patterns"], ids=lambda c: "|".join(c["patterns"]) or "empty")
def test_patterns_match_js(case):
    board = BOARDS.get(case.get("board"))
    got = bn.match_nets(case["names"], case["patterns"], board=board, case_sensitive=case["options"].get("caseSensitive", False))
    assert _no_errors(got) == _no_errors(case["result"])


@pytest.mark.parametrize("case", CASES["similar"], ids=lambda c: f"{c['copper']}-{c['seed']['layer']}-{'|'.join(c['seed']['nets'])}")
def test_similar_matches_js(case):
    got = bn.similar_tracks(COPPER[case["copper"]], case["seed"], board=BOARDS[case["copper"]])
    assert got == case["result"]


def test_kicad_patterns_give_kicads_classes():
    """CM5 MINIMA's netclass_patterns, matched case-sensitively, give the nets KiCad's rules put in each class."""
    project = read_kicad_pro(FIXTURES / "cm5_minima/kicad/CM5_MINIMA_3.kicad_pro")
    names = [n["name"] for n in CM5["board"]["nets"]]
    for nc in CM5["board"]["net_classes"]:
        if not nc["patterns"]:
            continue
        got = bn.match_nets(names, nc["patterns"], case_sensitive=True)["nets"]
        assert sorted(got) == sorted(nc["nets"]), nc["name"]
    assert project.patterns


def test_select_similar_pair_is_the_100_ohm_class():
    """A 100 Ω Ethernet pair picked on F.Cu: select-similar finds exactly the 13 pairs of the 100ohm class."""
    s = bn.similar_tracks(CM5["copper"], {"layer": "F.Cu", "nets": ["/CM5/ETH_PI.TRD0_P", "/CM5/ETH_PI.TRD0_N"]}, board=CM5["board"])
    klass = next(c for c in CM5["board"]["net_classes"] if c["name"] == "100ohm")
    assert (s["width"], s["gap"]) == (0.13, 0.25)
    assert sorted(n for p in s["pairs"] for n in p["nets"]) == sorted(klass["nets"])
    assert s["pairs"][0]["nets"] == ["/CM5/ETH_PI.TRD0_P", "/CM5/ETH_PI.TRD0_N"]


def test_rules():
    m = bn.match_nets(["/usb/USB_D+", "/usb/USB_D-", "usb_vbus"], ["USB_D*"])
    assert m["nets"] == ["/usb/USB_D+", "/usb/USB_D-"] and m["pairs"] == [["/usb/USB_D+", "/usb/USB_D-"]]
    assert bn.match_nets(["TX_T", "TX_C", "RX_T"], ["*_{T,C}"])["pairs"] == [["TX_T", "TX_C"]]
    assert bn.match_nets(["TX_T", "TX_C"], ["*_T/*_C"])["pairs"] == [["TX_T", "TX_C"]]
    assert bn.parse_pattern("re:(").error and bn.parse_pattern("a[").error and bn.parse_pattern(" ").error
    assert bn.matches_pattern("rf_*", "RF_OUT") and not bn.matches_pattern("rf_*", "RF_OUT", case_sensitive=True)
    assert bn.pairs_among(["D-", "D+"]) == [("D+", "D-")]
    assert bn.pairs_among(["A", "B"], board={"nets": [{"name": "A", "pair": "B"}]}) == [("A", "B")]


def test_kicad_reader_still_assigns(tmp_path):
    """The KiCad reader's class assignment is unchanged by boarddd.nets (it keeps fnmatch + regex, case-sensitive)."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_bytes(gzip.decompress((FIXTURES / "cm5_minima/kicad/CM5_MINIMA_3.kicad_pcb.gz").read_bytes()))
    board = read_kicad_pcb(pcb, read_kicad_pro(FIXTURES / "cm5_minima/kicad/CM5_MINIMA_3.kicad_pro"))
    assert {c.name: len(c.nets) for c in board.net_classes}["100ohm"] == 26
