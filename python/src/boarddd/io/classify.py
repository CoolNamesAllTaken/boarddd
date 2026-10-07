"""
Working out what each file in a manufacturing package is.

Four sources of evidence, best first.

1. The gerber standard has files declare their own purpose in a `%TF.FileFunction` header,
   and when it is there it is the answer -- it survives renaming, and it distinguishes the
   four copper layers of a stackup, which a filename convention only does by accident.
   KiCad, Eagle 9 and Fusion write it; Altium can be asked to.
2. Content that announces itself without a name: an Excellon drill file opens with `M48`,
   whatever it is called -- and Altium calls it `.txt`.
3. The filename, read against every export convention we know. Each EDA tool has its own:
   KiCad's `-F_Cu.gbr`, Altium's `.GTL`, Eagle's `copper_top.gbr` or `.cmp`, DipTrace's
   `TopMask.gbr`, Allegro's `.art`, gEDA's `.frontmask.gbr`. The conventions are separate
   tables so a package can be read with the one it came from first, and so the page that
   explains the matching can say which tool a pattern belongs to.
4. Failing all that, the content again: a CSV whose header row names a designator and
   coordinates is a placement file; one that names designators and parts is a BOM.

The rules are DATA, and the page that explains them reads the same data the matcher uses. A
hand-written list of "what we look for" would be wrong within a month of the first new export
tool, and wrong in the least visible way: still confidently displayed.

Pure: no settings, no filesystem.

Source: `magpie/pcb/classify.py` + `magpie/pcb/kinds.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from boarddd.io import bom as bom_parser
from boarddd.io import pos as posfile

__all__ = [
    "FabFile",
    "Classification",
    "Convention",
    "CONVENTIONS",
    "Rule",
    "RULES",
    "FUNCTIONS",
    "classify",
    "describe",
    "convention_choices",
    "convention_label",
]


class FabFile:
    """
    What a file in a package is: the kind constants `classify` sorts files into, their labels
    and groupings. The string values are stable (applications store them), and the class keeps
    the name it had where it came from, so a stored `kind` compares equal to these.
    """

    # Gerbers the viewer can draw.
    GERBER_COPPER = "gerber_copper"
    GERBER_MASK = "gerber_mask"
    GERBER_PASTE = "gerber_paste"
    GERBER_SILK = "gerber_silk"
    GERBER_EDGE = "gerber_edge"
    GERBER_FAB = "gerber_fab"
    GERBER_DOC = "gerber_doc"
    # Everything else.
    GERBER_JOB = "gerber_job"
    DRILL = "drill"
    DRILL_MAP = "drill_map"
    MODEL3D = "model3d"
    MODEL_GLB = "model_glb"
    PLACEMENT = "placement"
    BOM = "bom"
    IBOM = "ibom"
    NETLIST = "netlist"
    DOCUMENT = "document"
    OTHER = "other"

    KIND_CHOICES = [
        (GERBER_COPPER, "Copper"),
        (GERBER_MASK, "Solder mask"),
        (GERBER_PASTE, "Solder paste"),
        (GERBER_SILK, "Silkscreen"),
        (GERBER_EDGE, "Board outline"),
        (GERBER_FAB, "Assembly drawing"),
        (GERBER_DOC, "User drawing"),
        (GERBER_JOB, "Job file"),
        (DRILL, "Drill"),
        (DRILL_MAP, "Drill map"),
        (PLACEMENT, "Placement"),
        (MODEL3D, "3D model"),
        (MODEL_GLB, "Component model"),
        (BOM, "BOM"),
        (IBOM, "Interactive BOM"),
        (NETLIST, "Netlist"),
        (DOCUMENT, "Attachment"),
        (OTHER, "Other"),
    ]

    #: The kinds the viewer offers as drawable layers, in stacking order (first drawn first).
    DRAWABLE = [GERBER_EDGE, GERBER_COPPER, GERBER_MASK, GERBER_PASTE, GERBER_SILK, GERBER_FAB, GERBER_DOC]

    #: Kinds where which face the file belongs to is a real question. Everywhere else the
    #: answer is "neither" -- a drill file, a BOM and a STEP model are not sided, and offering
    #: Top/Bottom/Inner against them is an invitation to answer wrongly.
    SIDED = [GERBER_COPPER, GERBER_MASK, GERBER_PASTE, GERBER_SILK, GERBER_FAB, GERBER_DOC, PLACEMENT]


#: %TF.FileFunction,<function>,<args>  -- e.g. Copper,L1,Top / SolderMask,Bot / Profile,NP
_TF_FUNCTION = re.compile(r"%TF\.FileFunction,([^*%]+)\*%")
_HEAD_BYTES = 8192

#: The X2 function names, and what each is. The header is the standard's own vocabulary, so
#: this table is the same for every tool that writes one.
FUNCTIONS: dict[str, str] = {
    "Copper": FabFile.GERBER_COPPER,
    "Plated": FabFile.DRILL,
    "NonPlated": FabFile.DRILL,
    "MixedPlating": FabFile.DRILL,
    "SolderMask": FabFile.GERBER_MASK,
    "Soldermask": FabFile.GERBER_MASK,
    "SolderPaste": FabFile.GERBER_PASTE,
    "Paste": FabFile.GERBER_PASTE,
    "Legend": FabFile.GERBER_SILK,
    "Profile": FabFile.GERBER_EDGE,
    "AssemblyDrawing": FabFile.GERBER_FAB,
    "Other": FabFile.GERBER_DOC,
    "Drawing": FabFile.GERBER_DOC,
    "Comment": FabFile.GERBER_DOC,
    "FabricationDrawing": FabFile.GERBER_DOC,
    "Drillmap": FabFile.DRILL_MAP,
    "Component": FabFile.GERBER_FAB,
    "Glue": FabFile.GERBER_DOC,
    "Carbonmask": FabFile.GERBER_DOC,
    "Goldmask": FabFile.GERBER_DOC,
    "Heatsinkmask": FabFile.GERBER_DOC,
    "Peelablemask": FabFile.GERBER_DOC,
    "Silvermask": FabFile.GERBER_DOC,
    "Tinmask": FabFile.GERBER_DOC,
    "Depthrout": FabFile.GERBER_DOC,
    "Vcut": FabFile.GERBER_DOC,
    "Viafill": FabFile.GERBER_DOC,
    "Pads": FabFile.GERBER_DOC,
    "Keep-out": FabFile.GERBER_DOC,
    "Keepout": FabFile.GERBER_DOC,
    "ArrayDrawing": FabFile.GERBER_DOC,
}
_FUNCTION_KINDS = {name.replace(" ", "").lower(): kind for name, kind in FUNCTIONS.items()}

#: An Excellon file opens with M48 (header start), usually within a few comment lines.
_EXCELLON = re.compile(r"^\s*M48\s*$", re.M)
#: A gerber that never said what it is: the format statement every RS-274X file has.
_GERBER = re.compile(r"%(FS[LTD]?A?X\d|MO(MM|IN))", re.I)
#: The extensions text is read for a table under, before a document rule can claim them.
_TABULAR = (".csv", ".txt", ".tsv", ".tab", ".pos", ".xy", ".mnt", ".mnb", ".rpt", ".dat", ".prn", ".asc", "")
_SPREADSHEET = (".xlsx", ".xlsm", ".xls", ".ods")
#: What a document rule catches. Kept out of the convention tables because it is the fallback
#: for anything text-like the sniffing could not read, and must run after the sniffing.
DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".dxf",
    ".xlsx",
    ".xls",
    ".txt",
    ".md",
    ".doc",
    ".docx",
    ".rtf",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".rep",
    ".ldp",
    ".drr",
    ".apr",
    ".extrep",
    ".dri",
    ".gpi",
    ".log",
    ".html",
    ".htm",
)


@dataclass(frozen=True)
class Convention:
    """One tool's way of naming its exports."""

    key: str
    label: str
    blurb: str
    #: Conventions to try next when this one is chosen and its own rules say nothing. EasyEDA
    #: names its layers with Protel extensions; choosing EasyEDA should read those too.
    also: tuple[str, ...] = ()


CONVENTIONS: tuple[Convention, ...] = (
    Convention(
        "kicad",
        "KiCad",
        "KiCad 6 and later (-F_Cu.gbr, -Edge_Cuts.gbr, -pos.csv, -bom.csv), and KiCad 5's dotted names (-F.Cu.gbr).",
    ),
    Convention(
        "protel",
        "Altium / Protel extensions",
        "Layers told apart by extension: .GTL, .GBL, .G1, .GTS, .GTO, .GTP, .GKO, "
        ".GM1. Altium's own, and what KiCad's Protel option and EasyEDA write.",
    ),
    Convention(
        "easyeda",
        "EasyEDA / JLCPCB",
        "Gerber_TopLayer.GTL, Drill_PTH_Through.DRL, BOM_*.csv, PickAndPlace_*.csv.",
        also=("protel",),
    ),
    Convention(
        "eagle",
        "Eagle / Fusion 360 / LibrePCB",
        "Layers named in words: copper_top.gbr, soldermask_bottom.gbr, profile.gbr, "
        "COPPER-TOP.gbr; Eagle's older CAM extensions .cmp, .sol, .stc, .plc, .drd, "
        ".mnt.",
    ),
    Convention("diptrace", "DipTrace", "Top.gbr, TopMask.gbr, TopSilk.gbr, TopPaste.gbr, BoardOutline.gbr."),
    Convention(
        "orcad", "OrCAD / Allegro", "Artwork films as .art: TOP.art, SOLDERMASK_TOP.art, OUTLINE.art; place_txt."
    ),
    Convention(
        "geda", "gEDA / pcb-rnd", ".front.gbr, .frontmask.gbr, .frontsilk.gbr, .outline.gbr, .plated-drill.cnc, .xy."
    ),
    Convention(
        "generic",
        "Any tool",
        "Words that mean the same everywhere: bom, pick-and-place, centroid, outline, "
        "drill; and the standard's own extensions, .gbrjob, .drl, .step.",
    ),
)
_CONVENTION_BY_KEY = {c.key: c for c in CONVENTIONS}


def convention_choices() -> list[tuple[str, str]]:
    """(key, label) for a form, without the catch-all -- nobody exports 'from any tool'."""
    return [(c.key, c.label) for c in CONVENTIONS if c.key != "generic"]


def convention_label(key: str) -> str:
    found = _CONVENTION_BY_KEY.get(key)
    return found.label if found else ""


@dataclass(frozen=True)
class Rule:
    """
    How one kind of file is recognized by name, and how to say so in English.

    `pattern` is a regex searched against the lowercased filename (not the path). It may
    name groups: `side` (f/b/top/bottom/front/back...), `layer` (a copper layer number) or
    `inner` (an inner-layer index, numbered from the first inner layer). `side` on the rule
    itself fixes the answer for patterns that carry none. `shown` is the pattern as the help
    page prints it.
    """

    kind: str
    convention: str
    shown: str
    pattern: str
    side: str = ""
    note: str = ""

    @property
    def regex(self) -> re.Pattern:
        return _compiled(self.pattern)


_COMPILED: dict[str, re.Pattern] = {}


def _compiled(pattern: str) -> re.Pattern:
    found = _COMPILED.get(pattern)
    if found is None:
        found = _COMPILED[pattern] = re.compile(pattern)
    return found


_S = r"(?P<side>f|b|t|front|back|top|bot|bottom)"  # a side word, any tool's spelling
_TB = r"(?P<side>top|bottom|bot)"
_EXT = r"\.[a-z0-9]{1,7}$"  # any extension, to the end


def _k(kind, shown, pattern, side="", note=""):
    return Rule(kind, "kicad", shown, pattern, side, note)


def _p(kind, shown, pattern, side="", note=""):
    return Rule(kind, "protel", shown, pattern, side, note)


def _ez(kind, shown, pattern, side="", note=""):
    return Rule(kind, "easyeda", shown, pattern, side, note)


def _e(kind, shown, pattern, side="", note=""):
    return Rule(kind, "eagle", shown, pattern, side, note)


def _d(kind, shown, pattern, side="", note=""):
    return Rule(kind, "diptrace", shown, pattern, side, note)


def _o(kind, shown, pattern, side="", note=""):
    return Rule(kind, "orcad", shown, pattern, side, note)


def _g(kind, shown, pattern, side="", note=""):
    return Rule(kind, "geda", shown, pattern, side, note)


def _any(kind, shown, pattern, side="", note=""):
    return Rule(kind, "generic", shown, pattern, side, note)


#: Every name rule, grouped by convention. Order matters twice: the chosen convention's rules
#: run first, and within the whole table the specific patterns come before the general ones
#: -- `soldermask_top` is tried before DipTrace's bare `top`, and `.gm1` before `.gm<n>`.
RULES: tuple[Rule, ...] = (
    # ─── KiCad ───────────────────────────────────────────────────────────────
    # A `-tail` after the layer name is tolerated: the fixtures carry `-F_Paste-profile.gbr`,
    # and KiBot's variants write `-F_Cu-panel.gbr`.
    _k(FabFile.GERBER_COPPER, "*-F_Cu.gbr, *-B_Cu.gbr", rf"-{_S}[_.]cu(-[a-z0-9]+)?{_EXT}"),
    _k(FabFile.GERBER_COPPER, "*-In1_Cu.gbr", rf"-in(?P<inner>\d+)[_.]cu(-[a-z0-9]+)?{_EXT}", "inner"),
    _k(FabFile.GERBER_MASK, "*-F_Mask.gbr", rf"-{_S}[_.]mask(-[a-z0-9]+)?{_EXT}"),
    _k(FabFile.GERBER_PASTE, "*-F_Paste.gbr", rf"-{_S}[_.]paste(-[a-z0-9]+)?{_EXT}"),
    _k(FabFile.GERBER_SILK, "*-F_Silkscreen.gbr, *-F.SilkS.gbr", rf"-{_S}[_.](silkscreen|silks)(-[a-z0-9]+)?{_EXT}"),
    _k(FabFile.GERBER_EDGE, "*-Edge_Cuts.gbr", rf"-edge[_.]cuts(-[a-z0-9]+)?{_EXT}"),
    _k(FabFile.GERBER_FAB, "*-F_Fab.gbr", rf"-{_S}[_.]fab(-[a-z0-9]+)?{_EXT}"),
    _k(
        FabFile.GERBER_DOC,
        "*-User_Comments.gbr, *-Dwgs_User.gbr",
        rf"-(user[_.](comments|drawings|eco\d|\d+)|cmts[_.]user|dwgs[_.]user|eco\d[_.]user"
        rf"|margin){_EXT}",
    ),
    _k(FabFile.DRILL_MAP, "*-drl_map.*", r"-drl_map"),
    _k(FabFile.PLACEMENT, "*-top-pos.csv, *-bottom-pos.csv", rf"[-_]{_TB}[-_]pos{_EXT}"),
    _k(FabFile.PLACEMENT, "*-pos.csv, *-pos-openpnp.csv, *-cpl.csv", rf"[-_](pos|cpl)([-_][a-z0-9]+)*{_EXT}"),
    _k(FabFile.BOM, "*-bom.csv", rf"[-_]bom{_EXT}"),
    _k(FabFile.NETLIST, "*-netlist.ipc", rf"-netlist{_EXT}"),
    # ─── Protel extensions: Altium, and everything that copies it ────────────
    _p(FabFile.GERBER_COPPER, "*.GTL", r"\.gtl$", "top"),
    _p(FabFile.GERBER_COPPER, "*.GBL", r"\.gbl$", "bottom"),
    _p(FabFile.GERBER_COPPER, "*.G1 … *.G30 (inner), *.GP1 (plane)", r"\.gp?(?P<inner>\d{1,2})$", "inner"),
    _p(FabFile.GERBER_MASK, "*.GTS", r"\.gts$", "top"),
    _p(FabFile.GERBER_MASK, "*.GBS", r"\.gbs$", "bottom"),
    _p(FabFile.GERBER_PASTE, "*.GTP", r"\.gtp$", "top"),
    _p(FabFile.GERBER_PASTE, "*.GBP", r"\.gbp$", "bottom"),
    _p(FabFile.GERBER_SILK, "*.GTO", r"\.gto$", "top"),
    _p(FabFile.GERBER_SILK, "*.GBO", r"\.gbo$", "bottom"),
    _p(
        FabFile.GERBER_EDGE,
        "*.GKO, *.GM1",
        r"\.(gko|gm1)$",
        note="Keep-out, or mechanical 1: where Altium and EasyEDA put the outline.",
    ),
    _p(FabFile.DRILL_MAP, "*.GD1, *.GG1", r"\.g[dg]\d*$"),
    _p(
        FabFile.GERBER_DOC,
        "*.GM2 … *.GM32, *.GPT, *.GPB, *.GML",
        r"\.(gm\d+|gpt|gpb|gml)$",
        note="Other mechanical layers and the pad masters.",
    ),
    # ─── EasyEDA / JLCPCB ─────────────────────────────────────────────────────
    _ez(FabFile.GERBER_EDGE, "Gerber_BoardOutlineLayer.*", r"boardoutlinelayer"),
    _ez(FabFile.DRILL, "Drill_PTH_Through.DRL, Drill_NPTH_Through.DRL", r"drill_(n?pth|pth_through|npth_through)"),
    _ez(FabFile.DRILL_MAP, "Drill_Map*", r"drill[-_ ]?map"),
    _ez(FabFile.PLACEMENT, "PickAndPlace_*.csv, *_CPL.csv", rf"(pickandplace|[-_]cpl){_EXT}"),
    _ez(FabFile.BOM, "BOM_*.csv", r"^bom[-_ ]"),
    # ─── Eagle, Fusion 360, LibrePCB: layers named in words ───────────────────
    _e(FabFile.GERBER_COPPER, "copper_top.gbr, COPPER-TOP.gbr", rf"copper[-_ ]{_TB}{_EXT}"),
    _e(
        FabFile.GERBER_COPPER,
        "COPPER-IN1.gbr, copper_inner2.gbr",
        rf"copper[-_ ](in|inner|internal|l)[-_ ]?(?P<inner>\d+){_EXT}",
        "inner",
    ),
    _e(FabFile.GERBER_MASK, "soldermask_top.gbr", rf"(solder[-_ ]?mask|stopmask)[-_ ]{_TB}{_EXT}"),
    _e(FabFile.GERBER_PASTE, "solderpaste_top.gbr", rf"(solder[-_ ]?paste|cream)[-_ ]{_TB}{_EXT}"),
    _e(FabFile.GERBER_SILK, "silkscreen_top.gbr", rf"silk(screen)?[-_ ]{_TB}{_EXT}"),
    _e(FabFile.GERBER_FAB, "assembly_top.gbr", rf"(assembly|assy)[-_ ]{_TB}{_EXT}"),
    _e(
        FabFile.GERBER_EDGE,
        "profile.gbr, OUTLINES.gbr",
        rf"(^|[-_ ])(profile|outlines?|board[-_ ]?outline|dimension){_EXT}",
    ),
    _e(FabFile.DRILL, "drill_1_16.xln, DRILLS-PTH.drl", rf"drills?[-_ ](\d+[-_ ]\d+|n?pth){_EXT}"),
    _e(FabFile.PLACEMENT, "PnP_TOP.csv, PnP_BOTTOM.csv", rf"pnp[-_ ]{_TB}{_EXT}"),
    _e(FabFile.GERBER_COPPER, "*.cmp", r"\.cmp$", "top"),
    _e(FabFile.GERBER_COPPER, "*.sol", r"\.sol$", "bottom"),
    _e(FabFile.GERBER_COPPER, "*.ly2 … *.ly15", r"\.ly(?P<layer>\d+)$", "inner"),
    _e(FabFile.GERBER_MASK, "*.stc", r"\.stc$", "top"),
    _e(FabFile.GERBER_MASK, "*.sts", r"\.sts$", "bottom"),
    _e(FabFile.GERBER_PASTE, "*.crc", r"\.crc$", "top"),
    _e(FabFile.GERBER_PASTE, "*.crs", r"\.crs$", "bottom"),
    _e(FabFile.GERBER_SILK, "*.plc", r"\.plc$", "top"),
    _e(FabFile.GERBER_SILK, "*.pls", r"\.pls$", "bottom"),
    _e(FabFile.GERBER_EDGE, "*.dim, *.oln", r"\.(dim|oln)$"),
    _e(FabFile.GERBER_DOC, "*.mil", r"\.mil$", note="The milling layer."),
    _e(FabFile.DRILL, "*.drd", r"\.drd$"),
    _e(
        FabFile.PLACEMENT,
        "*.mnt (top), *.mnb (bottom)",
        r"\.mn(?P<side>t|b)$",
        note="mountsmd.ulp: no header row, one side per file.",
    ),
    # ─── DipTrace ─────────────────────────────────────────────────────────────
    _d(FabFile.GERBER_MASK, "TopMask.gbr, BottomMask.gbr", rf"{_TB}[-_ ]?mask{_EXT}"),
    _d(FabFile.GERBER_PASTE, "TopPaste.gbr", rf"{_TB}[-_ ]?paste{_EXT}"),
    _d(FabFile.GERBER_SILK, "TopSilk.gbr", rf"{_TB}[-_ ]?silk(screen)?{_EXT}"),
    _d(FabFile.GERBER_FAB, "TopAssy.gbr", rf"{_TB}[-_ ]?(assy|assembly){_EXT}"),
    _d(FabFile.GERBER_EDGE, "BoardOutline.gbr", rf"board[-_ ]?outline{_EXT}"),
    _d(FabFile.GERBER_COPPER, "Top.gbr, Bottom.gbr", rf"(^|[-_ ]){_TB}{_EXT}"),
    _d(FabFile.GERBER_COPPER, "Inner1.gbr", rf"(^|[-_ ])inner[-_ ]?(?P<inner>\d+){_EXT}", "inner"),
    # ─── OrCAD / Allegro ──────────────────────────────────────────────────────
    _o(FabFile.GERBER_MASK, "SOLDERMASK_TOP.art", rf"(solder|s)mask[-_ ]?{_TB}\.art$"),
    _o(FabFile.GERBER_PASTE, "PASTEMASK_TOP.art", rf"paste(mask)?[-_ ]?{_TB}\.art$"),
    _o(FabFile.GERBER_SILK, "SILKSCREEN_TOP.art", rf"silk(screen)?[-_ ]?{_TB}\.art$"),
    _o(FabFile.GERBER_FAB, "ASSEMBLY_TOP.art", rf"(assembly|assy)[-_ ]?{_TB}\.art$"),
    _o(FabFile.GERBER_EDGE, "OUTLINE.art", r"outline\.art$"),
    _o(FabFile.GERBER_COPPER, "TOP.art, BOTTOM.art", rf"(^|[-_ ]){_TB}\.art$"),
    _o(FabFile.GERBER_COPPER, "L2.art, INT1.art", r"(^|[-_ ])(l|int|inner|in)(?P<inner>\d+)\.art$", "inner"),
    _o(FabFile.GERBER_DOC, "*.art", r"\.art$", note="Any other artwork film."),
    _o(FabFile.PLACEMENT, "place_txt.txt", r"place[-_ ]?txt"),
    # ─── gEDA / pcb-rnd ───────────────────────────────────────────────────────
    _g(FabFile.GERBER_MASK, "*.frontmask.gbr", rf"\.(?P<side>front|back)mask{_EXT}"),
    _g(FabFile.GERBER_PASTE, "*.frontpaste.gbr", rf"\.(?P<side>front|back)paste{_EXT}"),
    _g(FabFile.GERBER_SILK, "*.frontsilk.gbr", rf"\.(?P<side>front|back)silk{_EXT}"),
    _g(FabFile.GERBER_COPPER, "*.front.gbr, *.back.gbr", rf"\.(?P<side>front|back){_EXT}"),
    _g(FabFile.GERBER_COPPER, "*.group1.gbr", rf"\.group(?P<inner>\d+){_EXT}", "inner"),
    _g(FabFile.GERBER_EDGE, "*.outline.gbr", rf"\.outline{_EXT}"),
    _g(FabFile.GERBER_FAB, "*.fab.gbr", rf"\.fab{_EXT}"),
    _g(FabFile.DRILL, "*.plated-drill.cnc, *.unplated-drill.cnc", r"drill\.cnc$"),
    _g(FabFile.PLACEMENT, "*.xy", r"\.xy$"),
    # ─── Any tool ─────────────────────────────────────────────────────────────
    _any(
        FabFile.GERBER_JOB, "*.gbrjob", r"\.gbrjob$", note="The job file. Where the board's real dimensions come from."
    ),
    _any(FabFile.DRILL_MAP, "drill map, drill drawing, drill guide", r"drill[-_ ]?(map|drawing|guide|dwg)"),
    _any(
        FabFile.DRILL,
        "*.drl, *.xln, *.exc, *.ncd, *.cnc; or any file that opens with M48",
        r"\.(drl|xln|exc|ncd|cnc|tap|nc)$",
    ),
    _any(
        FabFile.GERBER_EDGE,
        "outline, profile, edge, keepout + a gerber extension",
        r"(^|[-_ .])(outline|profile|edge|edges|keep[-_ ]?out|board[-_ ]?shape|contour)"
        r"(?=[-_ .]|$).*\.(gbr|ger|gerber|pho|art|gko|gm1|gbx)$",
    ),
    _any(
        FabFile.IBOM,
        "*ibom*.html",
        r"ibom.*\.html?$",
        note="An interactive BOM. Needs both: 'ibom' in the name and an HTML extension.",
    ),
    _any(
        FabFile.BOM,
        "bom, bill of materials, parts list + .csv/.txt/.xlsx",
        r"(^|[-_ .()])(bom|bill[-_ ]?of[-_ ]?materials?|parts?[-_ ]?list|partlist)"
        r"(?=[-_ .()]|$).*\.(csv|txt|tsv|tab|xlsx|xlsm|xls|ods)$",
    ),
    _any(
        FabFile.PLACEMENT,
        "pick and place, pnp, cpl, centroid, placement, position, xy, mount + .csv/.txt",
        r"(^|[-_ .()])(pick[-_ ]?(and|&|n)?[-_ ]?place|pickplace|pnp|cpl|centroid|centroids"
        r"|placement|placements|position|positions|xy|mount|mountsmd|smt|insertion)"
        r"(?=[-_ .()]|$).*\.(csv|txt|tsv|tab|pos|xy|rpt|dat|prn)$",
    ),
    _any(FabFile.NETLIST, "*.ipc", r"\.ipc$"),
    _any(
        FabFile.MODEL3D,
        "*.step, *.stp",
        r"\.(step|stp)$",
        note="A 3D model of the assembled board. Component heights are measured from it.",
    ),
    _any(
        FabFile.MODEL_GLB,
        "*.glb",
        r"\.glb$",
        note="One component's own model, cut out of the board's and kept against its part.",
    ),
    _any(
        FabFile.DOCUMENT,
        ", ".join("*" + e for e in DOCUMENT_EXTENSIONS[:8]) + ", …",
        r"^$",
        note="Not a layer: a document that travels with the package. Read only after "
        "the sniffing above has failed to find a table in it.",
    ),
    _any(FabFile.OTHER, "", r"^$", note="Anything none of the above recognized."),
)

#: Fixed layer numbers a side implies. Bottom is left None: how many layers deep it is
#: depends on the stackup, which a filename does not know.
_SIDE_WORDS = {
    "f": "top",
    "t": "top",
    "top": "top",
    "front": "top",
    "b": "bottom",
    "bot": "bottom",
    "bottom": "bottom",
    "back": "bottom",
}


@dataclass(frozen=True)
class Classification:
    kind: str
    function: str = ""
    side: str = ""
    copper_layer: int | None = None
    #: The convention whose rule recognized the name, or '' for the header and the content.
    convention: str = ""


def describe() -> list[dict]:
    """
    The matching rules, in the shape a template needs: one entry per kind, with the header
    names that mean it and, per convention, the name patterns that do.

    Built from RULES rather than written out, so the page explaining how files are recognized
    cannot describe a rule that no longer exists.
    """
    labels = dict(FabFile.KIND_CHOICES)
    kinds = [value for value, _label in FabFile.KIND_CHOICES]
    out = []
    for kind in kinds:
        rules = [rule for rule in RULES if rule.kind == kind]
        by_convention: dict[str, list[str]] = {}
        for rule in rules:
            if rule.shown:
                by_convention.setdefault(rule.convention, []).append(rule.shown)
        out.append(
            {
                "kind": kind,
                "label": labels.get(kind, kind),
                "functions": [name for name, k in FUNCTIONS.items() if k == kind],
                "patterns": [
                    {"convention": c.key, "label": c.label, "shown": by_convention[c.key]}
                    for c in CONVENTIONS
                    if c.key in by_convention
                ],
                "note": " ".join(rule.note for rule in rules if rule.note),
            }
        )
    return out


def _head(data: bytes) -> str:
    return data[:_HEAD_BYTES].decode("utf-8", errors="replace")


def _split(name: str) -> tuple[str, str]:
    lowered = name.lower()
    stem, dot, extension = lowered.rpartition(".")
    if not dot:
        return lowered, ""
    return stem, f".{extension}"


def _side_and_layer(parts: list[str]) -> tuple[str, int | None]:
    side, layer = "", None
    for part in parts:
        lowered = part.lower()
        if lowered in ("top", "t"):
            side = "top"
        elif lowered in ("bot", "bottom", "b"):
            side = "bottom"
        elif lowered in ("inr", "inner"):
            side = "inner"
        elif re.fullmatch(r"l\d+", lowered):
            layer = int(lowered[1:])
    return side, layer


def _from_function(head: str) -> Classification | None:
    match = _TF_FUNCTION.search(head)
    if not match:
        return None
    raw = match.group(1).strip()
    parts = [p.strip() for p in raw.split(",")]
    kind = _FUNCTION_KINDS.get(parts[0].replace(" ", "").lower())
    if kind is None:
        return None
    side, layer = _side_and_layer(parts[1:])
    if kind == FabFile.GERBER_COPPER and side == "top" and layer is None:
        layer = 1
    return Classification(kind=kind, function=raw, side=side, copper_layer=layer)


def _ordered_rules(convention: str) -> list[Rule]:
    """The name rules, the chosen convention's (and its `also`) first, then the rest."""
    chosen = _CONVENTION_BY_KEY.get(convention or "")
    if chosen is None:
        return list(RULES)
    first = (chosen.key,) + chosen.also
    return [rule for key in first for rule in RULES if rule.convention == key] + [
        rule for rule in RULES if rule.convention not in first
    ]


def _apply(rule: Rule, match: re.Match) -> Classification:
    groups = match.groupdict()
    side = rule.side or _SIDE_WORDS.get((groups.get("side") or "").lower(), "")
    layer = None
    if groups.get("layer"):
        layer = int(groups["layer"])
    elif groups.get("inner"):
        layer = int(groups["inner"]) + 1
    elif rule.kind == FabFile.GERBER_COPPER and side == "top":
        layer = 1
    if rule.kind not in FabFile.SIDED:
        side = ""
    if rule.kind != FabFile.GERBER_COPPER:
        layer = None
    return Classification(kind=rule.kind, side=side, copper_layer=layer, convention=rule.convention)


def _from_name(name: str, convention: str = "") -> Classification | None:
    lowered = name.lower()
    for rule in _ordered_rules(convention):
        if rule.kind in (FabFile.DOCUMENT, FabFile.OTHER):
            continue
        match = rule.regex.search(lowered)
        if match:
            return _apply(rule, match)
    return None


def _from_content(name: str, head: str) -> Classification:
    """What the bytes say, for a file whose name said nothing."""
    _stem, extension = _split(name)

    if extension in _TABULAR:
        # Placements first: a placement file has a Val column too, so read as a BOM it would
        # pass. A BOM never has coordinates.
        if posfile.looks_like_placements(head):
            return Classification(kind=FabFile.PLACEMENT)
        if bom_parser.looks_like_bom(head):
            return Classification(kind=FabFile.BOM)
    if extension in _SPREADSHEET:
        # A spreadsheet is a zip; its columns are not in the head. The name said nothing about
        # a BOM, so it travels as a document and can be pointed at as one.
        return Classification(kind=FabFile.DOCUMENT)
    if _GERBER.search(head) and extension not in DOCUMENT_EXTENSIONS:
        # A gerber that says nothing about which layer it is. Drawable, at least.
        return Classification(kind=FabFile.GERBER_DOC)
    if extension in DOCUMENT_EXTENSIONS:
        return Classification(kind=FabFile.DOCUMENT)
    return Classification(kind=FabFile.OTHER)


def classify(relpath: str, data: bytes, convention: str = "") -> Classification:
    """
    What this file is: from its header, then its self-announcing content, then its name,
    then its content again.

    `convention` names the tool the package came from (a CONVENTIONS key). Its rules are
    tried first, which settles the names two tools spell the same and mean differently. Blank
    means every convention in the table's own order, which is what auto-detection is.
    """
    name = relpath.rsplit("/", 1)[-1]
    head = _head(data)

    from_function = _from_function(head)
    if from_function is not None:
        # The header says what it is; the name still knows which side a bare "Copper,L4" is on
        # for stackups whose header omits Top/Bot.
        if not from_function.side and from_function.kind in FabFile.SIDED:
            from_name = _from_name(name, convention)
            if from_name is not None and from_name.kind == from_function.kind and from_name.side:
                return Classification(
                    kind=from_function.kind,
                    function=from_function.function,
                    side=from_name.side,
                    copper_layer=from_function.copper_layer or from_name.copper_layer,
                )
        return from_function

    # Excellon announces itself whatever the file is called -- and Altium calls it .txt, which
    # a name rule would file as a document.
    if _EXCELLON.search(head):
        return Classification(kind=FabFile.DRILL)

    from_name = _from_name(name, convention)
    if from_name is not None:
        return from_name
    return _from_content(name, head)
