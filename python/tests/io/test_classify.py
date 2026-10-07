"""
Classifying the files in a package (`boarddd.io.classify`).

Run against a real KiCad export (fixtures/royalblue54L_feather/fab) as well as names written
to order, because the failure this guards against is a file being understood in a way that
only holds for a file somebody invented.

Ported from magpie `tests/pcb/test_formats.py` (classification) and
`tests/pcb/test_file_kinds.py` at 3a0374d3.
"""

from __future__ import annotations

import pytest

from boarddd.io import classify
from boarddd.io.classify import FabFile

from conftest import RB_FAB, TEST_FIXTURES

GERBER_HEAD = "%TFGenerationSoftware,KiCad,Pcbnew,10.0.1*%\n%TF.FileFunction,{function}*%\n%FSLAX46Y46*%\n%MOMM*%\n"

#: A BOM in KiCad's column layout, written for these tests (royalblue54L_feather's values).
BOM_CSV = (
    '"Reference","Value","Footprint","Quantity","Manufacturer","MPN"\n'
    '"C1","100nF","Capacitor_SMD:C_0402_1005Metric","1","",""\n'
    '"C2,C3,C4","10uF","Capacitor_SMD:C_0603_1608Metric","3","",""\n'
)

# ─── Classification ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "function,kind,side,layer",
    [
        ("Copper,L1,Top", FabFile.GERBER_COPPER, "top", 1),
        ("Copper,L4,Bot", FabFile.GERBER_COPPER, "bottom", 4),
        ("Copper,L2,Inr", FabFile.GERBER_COPPER, "inner", 2),
        ("SolderMask,Top", FabFile.GERBER_MASK, "top", None),
        ("SolderPaste,Bot", FabFile.GERBER_PASTE, "bottom", None),
        ("Legend,Top", FabFile.GERBER_SILK, "top", None),
        ("Profile,NP", FabFile.GERBER_EDGE, "", None),
        ("AssemblyDrawing,Top", FabFile.GERBER_FAB, "top", None),
        ("Other,User", FabFile.GERBER_DOC, "", None),
    ],
)
def test_the_file_function_header_is_believed(function, kind, side, layer):
    """
    The gerber standard has files declare their own purpose. It survives renaming and it
    distinguishes four copper layers, which a filename convention only does by accident.
    """
    found = classify.classify("whatever.gbr", GERBER_HEAD.format(function=function).encode())

    assert (found.kind, found.side, found.copper_layer) == (kind, side, layer)
    assert found.function == function


def test_the_real_export_classifies_end_to_end(sample):
    """Every file of the royalblue54L_feather export, by its X2 header, its content or its name."""
    found = {path.name: classify.classify(f"fab/{path.name}", path.read_bytes()) for path in sorted(RB_FAB.iterdir())}

    assert found["RoyalBlue54L-Feather-Edge_Cuts.gbr"].kind == FabFile.GERBER_EDGE
    copper = found["RoyalBlue54L-Feather-F_Cu.gbr"]
    assert (copper.kind, copper.side, copper.copper_layer) == (FabFile.GERBER_COPPER, "top", 1)
    bottom = found["RoyalBlue54L-Feather-B_Cu.gbr"]
    assert (bottom.kind, bottom.side, bottom.copper_layer) == (FabFile.GERBER_COPPER, "bottom", 8)
    inner = [c for c in found.values() if c.kind == FabFile.GERBER_COPPER and c.side == "inner"]
    assert sorted(c.copper_layer for c in inner) == [2, 3, 4, 5, 6, 7]
    assert found["RoyalBlue54L-Feather-F_Mask.gbr"].kind == FabFile.GERBER_MASK
    assert found["RoyalBlue54L-Feather-B_Paste.gbr"].kind == FabFile.GERBER_PASTE
    assert found["RoyalBlue54L-Feather-F_Silkscreen.gbr"].kind == FabFile.GERBER_SILK
    assert found["RoyalBlue54L-Feather-PTH.drl"].kind == FabFile.DRILL
    assert found["RoyalBlue54L-Feather-NPTH.drl"].kind == FabFile.DRILL
    assert found["pos.csv"].kind == FabFile.PLACEMENT
    assert found["RoyalBlue54L-Feather-job.gbrjob"].kind == FabFile.GERBER_JOB
    assert classify.classify("assembly/board-bom.csv", BOM_CSV.encode()).kind == FabFile.BOM


def test_a_renamed_layer_is_still_known_by_its_header():
    """pic_programmer names its copper `top_layer`/`bottom_layer`; the X2 header says which is which."""
    head = TEST_FIXTURES / "pic_programmer" / "head"
    top = classify.classify("pic_programmer-top_layer.gbr", (head / "pic_programmer-top_layer.gbr").read_bytes())
    bottom = classify.classify(
        "pic_programmer-bottom_layer.gbr", (head / "pic_programmer-bottom_layer.gbr").read_bytes()
    )
    assert (top.kind, top.side, top.copper_layer) == (FabFile.GERBER_COPPER, "top", 1)
    assert (bottom.kind, bottom.side, bottom.copper_layer) == (FabFile.GERBER_COPPER, "bottom", 2)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("board-F_Cu.gbr", FabFile.GERBER_COPPER),
        ("board-B_Silkscreen.gbr", FabFile.GERBER_SILK),
        ("board-Edge_Cuts.gbr", FabFile.GERBER_EDGE),
        ("board.drl", FabFile.DRILL),
        ("board-drl_map.pdf", FabFile.DRILL_MAP),
        ("board-netlist.ipc", FabFile.NETLIST),
        ("board-ibom.html", FabFile.IBOM),
        ("board-assembly.pdf", FabFile.DOCUMENT),
        ("impedance_control.xlsx", FabFile.DOCUMENT),
    ],
)
def test_a_headerless_file_falls_back_to_its_name(name, kind):
    assert classify.classify(name, b"not a gerber").kind == kind


def test_a_csv_with_no_helpful_name_is_read():
    """Renamed by whoever mailed it; the header still says which file it is."""
    assert classify.classify("attachment1.csv", (RB_FAB / "pos.csv").read_bytes()).kind == FabFile.PLACEMENT
    assert classify.classify("attachment2.csv", BOM_CSV.encode()).kind == FabFile.BOM


# ─── Other tools' names ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,kind,side,layer",
    [
        # KiCad 5 spelled the layers with dots, and KiBot appends a variant.
        ("board-F.Cu.gbr", FabFile.GERBER_COPPER, "top", 1),
        ("board-In2_Cu.gbr", FabFile.GERBER_COPPER, "inner", 3),
        ("board-F.SilkS.gbr", FabFile.GERBER_SILK, "top", None),
        ("board-F_Paste-profile.gbr", FabFile.GERBER_PASTE, "top", None),
        ("board-top-pos.csv", FabFile.PLACEMENT, "top", None),
        # Altium, and everything else that uses the Protel extensions.
        ("PCB1.GTL", FabFile.GERBER_COPPER, "top", 1),
        ("PCB1.GBL", FabFile.GERBER_COPPER, "bottom", None),
        ("PCB1.G2", FabFile.GERBER_COPPER, "inner", 3),
        ("PCB1.GBS", FabFile.GERBER_MASK, "bottom", None),
        ("PCB1.GTP", FabFile.GERBER_PASTE, "top", None),
        ("PCB1.GBO", FabFile.GERBER_SILK, "bottom", None),
        ("PCB1.GKO", FabFile.GERBER_EDGE, "", None),
        ("PCB1.GM1", FabFile.GERBER_EDGE, "", None),
        ("PCB1.GM13", FabFile.GERBER_DOC, "", None),
        ("PCB1.GD1", FabFile.DRILL_MAP, "", None),
        ("PCB1.DRR", FabFile.DOCUMENT, "", None),
        # EasyEDA / JLCPCB.
        ("Gerber_TopLayer.GTL", FabFile.GERBER_COPPER, "top", 1),
        ("Gerber_BoardOutlineLayer.GKO", FabFile.GERBER_EDGE, "", None),
        ("Drill_PTH_Through.DRL", FabFile.DRILL, "", None),
        ("BOM_Board1_2026-09-11.csv", FabFile.BOM, "", None),
        ("PickAndPlace_PCB1_2026-09-11.csv", FabFile.PLACEMENT, "", None),
        # Eagle 9 / Fusion 360, LibrePCB.
        ("copper_top.gbr", FabFile.GERBER_COPPER, "top", 1),
        ("soldermask_bottom.gbr", FabFile.GERBER_MASK, "bottom", None),
        ("solderpaste_top.gbr", FabFile.GERBER_PASTE, "top", None),
        ("silkscreen_top.gbr", FabFile.GERBER_SILK, "top", None),
        ("profile.gbr", FabFile.GERBER_EDGE, "", None),
        ("drill_1_16.xln", FabFile.DRILL, "", None),
        ("PROJ_COPPER-TOP.gbr", FabFile.GERBER_COPPER, "top", 1),
        ("PROJ_COPPER-IN1.gbr", FabFile.GERBER_COPPER, "inner", 2),
        ("PROJ_OUTLINES.gbr", FabFile.GERBER_EDGE, "", None),
        ("PROJ_DRILLS-PTH.drl", FabFile.DRILL, "", None),
        ("PROJ_PnP_BOTTOM.csv", FabFile.PLACEMENT, "bottom", None),
        # Eagle's older CAM extensions.
        ("board.cmp", FabFile.GERBER_COPPER, "top", 1),
        ("board.sol", FabFile.GERBER_COPPER, "bottom", None),
        ("board.stc", FabFile.GERBER_MASK, "top", None),
        ("board.crs", FabFile.GERBER_PASTE, "bottom", None),
        ("board.plc", FabFile.GERBER_SILK, "top", None),
        ("board.dim", FabFile.GERBER_EDGE, "", None),
        ("board.drd", FabFile.DRILL, "", None),
        ("board.mnt", FabFile.PLACEMENT, "top", None),
        ("board.mnb", FabFile.PLACEMENT, "bottom", None),
        # DipTrace.
        ("Board_Top.gbr", FabFile.GERBER_COPPER, "top", 1),
        ("Board_Bottom.gbr", FabFile.GERBER_COPPER, "bottom", None),
        ("Board_TopMask.gbr", FabFile.GERBER_MASK, "top", None),
        ("Board_BottomSilk.gbr", FabFile.GERBER_SILK, "bottom", None),
        ("Board_BoardOutline.gbr", FabFile.GERBER_EDGE, "", None),
        # OrCAD / Allegro.
        ("TOP.art", FabFile.GERBER_COPPER, "top", 1),
        ("BOTTOM.art", FabFile.GERBER_COPPER, "bottom", None),
        ("L2.art", FabFile.GERBER_COPPER, "inner", 3),
        ("SOLDERMASK_TOP.art", FabFile.GERBER_MASK, "top", None),
        ("OUTLINE.art", FabFile.GERBER_EDGE, "", None),
        ("FAB_NOTES.art", FabFile.GERBER_DOC, "", None),
        ("place_txt.txt", FabFile.PLACEMENT, "", None),
        # gEDA / pcb-rnd.
        ("board.front.gbr", FabFile.GERBER_COPPER, "top", 1),
        ("board.backmask.gbr", FabFile.GERBER_MASK, "bottom", None),
        ("board.outline.gbr", FabFile.GERBER_EDGE, "", None),
        ("board.plated-drill.cnc", FabFile.DRILL, "", None),
        ("board.xy", FabFile.PLACEMENT, "", None),
        # Words that mean the same everywhere.
        ("Bill of Materials.xlsx", FabFile.BOM, "", None),
        ("parts list.csv", FabFile.BOM, "", None),
        ("centroid.txt", FabFile.PLACEMENT, "", None),
        ("board pick and place.csv", FabFile.PLACEMENT, "", None),
        ("outline.gbr", FabFile.GERBER_EDGE, "", None),
        ("drill map.pdf", FabFile.DRILL_MAP, "", None),
        ("notes.txt", FabFile.DOCUMENT, "", None),
        ("impedance_control.xlsx", FabFile.DOCUMENT, "", None),
        ("weird.bin", FabFile.OTHER, "", None),
    ],
)
def test_every_tools_names_are_read(name, kind, side, layer):
    """
    One table, every convention. The point of the table being data is that adding a tool
    is adding rows here and rows there, and this is where a rule that shadows another
    shows up -- `soldermask_top` has to beat DipTrace's bare `top`.
    """
    found = classify.classify(name, b"not a gerber")
    assert (found.kind, found.side, found.copper_layer) == (kind, side, layer)


def test_a_chosen_convention_is_tried_first():
    """
    `.GM1` is where Altium puts the outline and where nobody else puts anything, so the
    table reads it as the outline whatever was chosen; the chosen convention matters for a
    name two tools spell alike. `TOP.art` is Allegro's copper and, read as words, could be
    DipTrace's -- both answer copper, but the answer should come from the tool named.
    """
    assert classify.classify("TOP.art", b"", "orcad").convention == "orcad"
    assert classify.classify("Board_Top.gbr", b"", "diptrace").convention == "diptrace"
    assert classify.classify("board-F_Cu.gbr", b"", "protel").kind == FabFile.GERBER_COPPER, (
        "a convention that says nothing about a name does not stop the others answering"
    )


def test_an_unknown_convention_reads_like_no_convention():
    assert classify.classify("board-F_Cu.gbr", b"", "no-such-tool").kind == FabFile.GERBER_COPPER


def test_a_drill_file_announces_itself_whatever_it_is_called():
    """Altium writes Excellon to a .txt, which by name alone is a document."""
    excellon = b"M48\n;FILE_FORMAT=2:4\nFMAT,2\nMETRIC\nT1C0.300\n%\nT1\nX1Y1\nM30\n"
    assert classify.classify("PCB1.TXT", excellon).kind == FabFile.DRILL
    assert classify.classify("PCB1.TXT", b"just some notes").kind == FabFile.DOCUMENT


def test_a_placement_table_in_a_txt_is_a_placement_file():
    """Altium's pick-and-place export is a fixed-width .txt with a title block above it."""
    assert classify.classify("Pick Place for PCB1.txt", ALTIUM_TXT.encode()).kind == FabFile.PLACEMENT
    assert classify.classify("PCB1.txt", ALTIUM_TXT.encode()).kind == FabFile.PLACEMENT, "even with nothing in the name"


def test_a_gerber_that_says_nothing_is_still_drawable():
    """An RS-274X file with no X2 header and an unhelpful name: a user drawing at worst."""
    assert classify.classify("layer7.gbr", b"%FSLAX46Y46*%\n%MOMM*%\nG04 hi*\n").kind == FabFile.GERBER_DOC


def test_the_x2_header_takes_the_side_from_the_name_when_it_omits_it():
    head = GERBER_HEAD.format(function="Copper,L4").encode()
    found = classify.classify("PCB1.GBL", head)
    assert (found.kind, found.side, found.copper_layer) == (FabFile.GERBER_COPPER, "bottom", 4)


def test_the_help_lists_every_convention_and_names_the_tool_for_each_pattern():
    copper = next(e for e in classify.describe() if e["kind"] == FabFile.GERBER_COPPER)
    by_label = {group["label"]: group["shown"] for group in copper["patterns"]}

    assert "*-F_Cu.gbr, *-B_Cu.gbr" in by_label["KiCad"]
    assert "*.GTL" in by_label["Altium / Protel extensions"]
    assert "Copper" in copper["functions"]
    assert {c.key for c in classify.CONVENTIONS} >= {r.convention for r in classify.RULES}, (
        "every rule belongs to a convention the help can name"
    )


ALTIUM_TXT = (
    "Altium Designer Pick and Place Locations\n"
    "C:\\\\projects\\\\PCB1.PcbDoc\n"
    "\n"
    "========================================================================\n"
    "File Design Information:\n"
    "\n"
    "Date:       11/09/26\n"
    "Units used:   mm\n"
    "\n"
    "Designator Comment    Layer       Footprint   Center-X(mm) Center-Y(mm) Rotation Description\n"
    "C1         100nF      TopLayer    0402        12.7000      5.0800       90       Cap 100 nF\n"
    "R1         10k 1%     BottomLayer 0603        -3.1750      2.5400       180      Res\n"
)


# ─── The help


def test_the_help_is_built_from_the_rules_themselves():
    """
    Not a hand-written list. One that could describe a rule which no longer exists would be
    wrong in the least visible way: still confidently displayed.
    """
    described = {entry["kind"] for entry in classify.describe()}

    assert described == {rule.kind for rule in classify.RULES}
    copper = next(e for e in classify.describe() if e["kind"] == FabFile.GERBER_COPPER)
    assert "Copper" in copper["functions"]
    shown = {s for group in copper["patterns"] for s in group["shown"]}
    assert "*-F_Cu.gbr, *-B_Cu.gbr" in shown
    assert "*.GTL" in shown


def test_every_kind_a_file_can_have_is_explained():
    """A kind offered in the dropdown with no entry in the reference is a dead end."""
    explained = {entry["kind"] for entry in classify.describe()}

    assert {value for value, _label in FabFile.KIND_CHOICES} <= explained
