"""boarddd.io.ipc2581: units, primitives, transforms, BOM data, and KiCad's demo boards.

Ported from magpie's tests/pcb/test_ipc2581.py (magpie commit 3a0374d3). `read_components` is magpie's
`read_ipc2581` (component level, `boarddd.io.eda` types); `read_ipc2581` now returns a `boarddd.model.Board`.
The KiCad fixture is KiCad 10.0.6's export of the royalblue54L_feather and pic_programmer demos
(fixtures/make_exchange.sh); magpie's Altium rev A export is not a boarddd fixture source and its test is not
ported.
"""

from __future__ import annotations

import ast
import gzip
import sys
import xml.etree.ElementTree as ET

import pytest

from boarddd.io.eda import Footprint
from boarddd.io.ipc2581 import primitive, read_components, read_ipc2581
from boarddd.model import Board
from boarddd.validate import validate_board

from conftest import FIXTURES, REPO

RB_IPC = FIXTURES / "royalblue54L_feather" / "exchange" / "RoyalBlue54L-Feather-ipc2581.xml.gz"
PIC_IPC = FIXTURES / "pic_programmer" / "exchange" / "pic_programmer-ipc2581.xml.gz"


def doc(
    units: str = "MILLIMETER",
    scale: float = 1.0,
    *,
    components: str = "",
    packages: str = "",
    features: str = "",
    bom: str = "",
    extra_dict: str = "",
) -> bytes:
    """A small IPC-2581 rev C document in `units`; lengths given in mm are divided by `scale`."""
    return f'''<?xml version="1.0"?>
<IPC-2581 revision="C" xmlns="http://webstds.ipc.org/2581">
  <Content roleRef="Owner">
    <DictionaryStandard units="{units}">
      <EntryStandard id="RR"><RectRound width="{1.0 / scale}" height="{2.0 / scale}" radius="{0.25 / scale}" upperRight="true" upperLeft="true" lowerRight="true" lowerLeft="true"/></EntryStandard>
      <EntryStandard id="C"><Circle diameter="{0.5 / scale}"/></EntryStandard>
      <EntryStandard id="R"><RectCenter width="{0.6 / scale}" height="{0.4 / scale}"/></EntryStandard>
      <EntryStandard id="O"><Oval width="{1.2 / scale}" height="{0.6 / scale}"/></EntryStandard>
      <EntryStandard id="CH"><RectCham width="{1.0 / scale}" height="{1.0 / scale}" chamfer="{0.2 / scale}" upperLeft="true"/></EntryStandard>
      {extra_dict}
    </DictionaryStandard>
    <DictionaryUser units="{units}">
      <EntryUser id="TRI"><UserSpecial><Contour><Polygon>
        <PolyBegin x="0" y="0"/><PolyStepSegment x="{2 / scale}" y="0"/>
        <PolyStepSegment x="0" y="{1 / scale}"/><PolyStepSegment x="0" y="0"/>
      </Polygon></Contour></UserSpecial></EntryUser>
    </DictionaryUser>
  </Content>
  {bom}
  <Ecad name="t">
    <CadHeader units="{units}"/>
    <CadData>
      <Layer name="TOP" layerFunction="CONDUCTOR" side="TOP"/>
      <Layer name="BOT" layerFunction="CONDUCTOR" side="BOTTOM"/>
      <Layer name="TPASTE" layerFunction="SOLDERPASTE" side="TOP"/>
      <Layer name="BPASTE" layerFunction="SOLDERPASTE" side="BOTTOM"/>
      <Step name="board">
        {packages}
        {components}
        {features}
      </Step>
    </CadData>
  </Ecad>
</IPC-2581>'''.encode()


PKG = """<Package name="P2">
  <Outline><Polygon><PolyBegin x="-2" y="-1"/><PolyStepSegment x="2" y="-1"/>
    <PolyStepSegment x="2" y="1"/><PolyStepSegment x="-2" y="1"/><PolyStepSegment x="-2" y="-1"/>
  </Polygon></Outline>
  <Pin number="1"><Location x="{a}" y="0"/><StandardPrimitiveRef id="RR"/></Pin>
  <Pin number="2"><Xform rotation="90"/><Location x="{b}" y="{c}"/><StandardPrimitiveRef id="RR"/></Pin>
</Package>"""


def test_units_are_converted():
    for units, scale in (("MILLIMETER", 1.0), ("INCH", 25.4), ("MICRON", 0.001)):
        b = read_components(
            doc(
                units,
                scale,
                packages=PKG.format(a=-1.5 / scale, b=1.5 / scale, c=0.25 / scale),
                components=f'<Component refDes="U1" packageRef="P2" layerRef="TOP">'
                f'<Location x="{10 / scale}" y="{5 / scale}"/></Component>',
            )
        )
        u1 = b.by_reference()["U1"]
        assert (u1.placement.x, u1.placement.y) == pytest.approx((10, 5))
        p1, p2 = u1.footprint.pads
        assert p1.center == pytest.approx((-1.5, 0.0)) and p1.size == pytest.approx((1.0, 2.0))
        assert p1.shape == "roundrect" and p1.roundrect_ratio == pytest.approx(0.25)
        assert p2.center == pytest.approx((1.5, 0.25)) and p2.rotation == 90.0
        assert u1.footprint.courtyard and u1.footprint.courtyard[0].kind == "polygon"


@pytest.mark.parametrize(
    "xml,shape,size",
    [
        ('<Circle diameter="0.5"/>', "circle", (0.5, 0.5)),
        ('<RectCenter width="0.6" height="0.4"/>', "rect", (0.6, 0.4)),
        ('<Oval width="1.2" height="0.6"/>', "oval", (1.2, 0.6)),
        ('<RectRound width="1" height="2" radius="0.25"/>', "roundrect", (1.0, 2.0)),
        ('<RectCham width="1" height="1" chamfer="0.2" upperLeft="true"/>', "chamfered", (1.0, 1.0)),
        ('<RectCorner lowerLeftX="-1" lowerLeftY="0" upperRightX="1" upperRightY="0.5"/>', "rect", (2.0, 0.5)),
        ('<Octagon length="1"/>', "polygon", (1.0, 1.0)),
        (
            '<Contour><Polygon><PolyBegin x="0" y="0"/><PolyStepSegment x="2" y="0"/>'
            '<PolyStepSegment x="0" y="1"/><PolyStepSegment x="0" y="0"/></Polygon></Contour>',
            "polygon",
            (2.0, 1.0),
        ),
    ],
)
def test_primitives(xml, shape, size):
    s = primitive(ET.fromstring(xml), 1.0)
    assert s.shape == shape and s.size == pytest.approx(size)


def test_chamfered_corners_and_ratio():
    s = primitive(
        ET.fromstring('<RectCham width="1" height="2" chamfer="0.25" upperLeft="true" lowerRight="true"/>'), 1.0
    )
    assert s.chamfered == ("top_left", "bottom_right") and s.chamfer_ratio == pytest.approx(0.25)


def test_contour_with_arcs_keeps_its_extent():
    s = primitive(
        ET.fromstring(
            '<Contour><Polygon><PolyBegin x="-1" y="0"/>'
            '<PolyStepCurve x="1" y="0" centerX="0" centerY="0" clockwise="true"/>'
            '<PolyStepSegment x="-1" y="0"/></Polygon></Contour>'
        ),
        1.0,
    )
    assert s.size == pytest.approx((2.0, 1.0), abs=1e-6)
    assert s.offset == pytest.approx((0.0, 0.5), abs=1e-6)


def test_polygon_pads_bake_their_rotation_and_offset():
    pkg = """<Package name="T"><Pin number="1"><Xform rotation="90"/><Location x="1" y="1"/>
             <UserPrimitiveRef id="TRI"/></Pin></Package>"""
    b = read_components(
        doc(
            packages=pkg,
            components='<Component refDes="X1" packageRef="T" layerRef="TOP"><Location x="0" y="0"/></Component>',
        )
    )
    pad = b.components[0].footprint.pads[0]
    assert pad.shape == "polygon" and pad.rotation == 0.0
    # The triangle (0,0)-(2,0)-(0,1) turned 90 degrees about the pin, then moved to (1, 1).
    xs = [pad.center[0] + x for x, _ in pad.polygon]
    ys = [pad.center[1] + y for _, y in pad.polygon]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((0.0, 1.0, 1.0, 3.0))


def _two_pin(ref: str, layer: str, rotation: float, mirror: bool, x: float, y: float) -> str:
    mirror_attr = ' mirror="true"' if mirror else ""
    return (
        f'<Component refDes="{ref}" packageRef="P2" layerRef="{layer}">'
        f'<Xform rotation="{rotation}"{mirror_attr}/><Location x="{x}" y="{y}"/></Component>'
    )


def _features(ref: str, layer: str, placement, pads) -> str:
    """LayerFeature pads where the model's transform puts them."""
    sets = "".join(
        f'<Pad><Location x="{placement.to_board(p.center)[0]}" y="{placement.to_board(p.center)[1]}"/>'
        f'<StandardPrimitiveRef id="RR"/><PinRef componentRef="{ref}" pin="{p.number}"/></Pad>'
        for p in pads
    )
    return f'<LayerFeature layerRef="{layer}"><Set>{sets}</Set></LayerFeature>'


@pytest.mark.parametrize("rotation", [0, 30, 90, 180, 295.7])
def test_bottom_side_rotation_convention(rotation):
    """mirror="true": rotated, then mirrored about the board's y axis (KiCad's reading, settled
    against magpie's synthetic corpus). Pads drawn at the model's positions give no warning."""
    pkg = PKG.format(a=-1.5, b=1.5, c=0.25)
    first = read_components(doc(packages=pkg, components=_two_pin("U1", "BOT", rotation, True, 10, 5)))
    u1 = first.components[0]
    assert u1.placement.side == "bottom"
    assert u1.placement.rotation == pytest.approx((180 - rotation) % 360)
    again = read_components(
        doc(
            packages=pkg,
            components=_two_pin("U1", "BOT", rotation, True, 10, 5),
            features=_features("U1", "BOT", u1.placement, u1.footprint.pads),
        )
    )
    assert again.warnings == []
    assert {p.layers for p in again.components[0].footprint.pads} == {("F.Cu",)}


def test_a_wrong_convention_is_reported():
    pkg = PKG.format(a=-1.5, b=1.5, c=0.25)
    u1 = read_components(doc(packages=pkg, components=_two_pin("U1", "BOT", 30, True, 10, 5))).components[0]
    shifted = Footprint(
        name="x",
        pads=tuple(p.__class__(**{**p.__dict__, "center": (p.center[0], -p.center[1])}) for p in u1.footprint.pads),
    )
    b = read_components(
        doc(
            packages=pkg,
            components=_two_pin("U1", "BOT", 30, True, 10, 5),
            features=_features("U1", "BOT", u1.placement, shifted.pads),
        )
    )
    assert any("transform convention" in w for w in b.warnings)


def test_bom_part_numbers_and_dnp():
    bom = """<Bom name="b"><BomItem OEMDesignNumberRef="PN-1">
               <RefDes name="U1" packageRef="P2" populate="false"/>
               <Characteristics category="ELECTRICAL">
                 <Textual textualCharacteristicName="MPN" textualCharacteristicValue="LM358DR"/>
                 <Textual textualCharacteristicName="Manufacturer" textualCharacteristicValue="TI"/>
               </Characteristics></BomItem></Bom>
             <Avl name="a"><AvlItem OEMDesignNumber="PN-1"><AvlVmpn><AvlMpn name="LM358DRG4"/></AvlVmpn></AvlItem></Avl>"""
    b = read_components(
        doc(packages=PKG.format(a=-1, b=1, c=0), bom=bom, components=_two_pin("U1", "TOP", 0, False, 0, 0))
    )
    u1 = b.components[0]
    assert u1.mpns == ("LM358DR", "LM358DRG4") and not u1.populate
    assert u1.properties["Manufacturer"] == "TI" and u1.footprint.fields["MPN"] == "LM358DR"


def test_gzip_input():
    raw = doc(packages=PKG.format(a=-1, b=1, c=0), components=_two_pin("U1", "TOP", 0, False, 0, 0))
    assert read_components(gzip.compress(raw)).components[0].reference == "U1"


def test_not_ipc2581():
    with pytest.raises(ValueError):
        read_components(b'<?xml version="1.0"?><Other/>')


@pytest.mark.parametrize("path", [RB_IPC, PIC_IPC], ids=["royalblue", "pic_programmer"])
def test_the_board_model_is_valid_and_round_trips(path):
    d = read_ipc2581(path).to_dict()
    assert validate_board(d) == []
    assert Board.from_dict(d).to_dict() == d


# ─── KiCad specifics (KiCad's demo boards) ───────────────────────────────────


def test_kicad_demo_board():
    b = read_components(RB_IPC)
    assert (b.name, b.revision, b.units, b.warnings) == ("RoyalBlue54L-Feather", "C", "MILLIMETER", [])
    assert len(b.components) == 71
    j1 = b.by_reference()["J1"]
    # The 1x16 header on the bottom: KiCad's -90 degrees, through-hole, 1 mm holes, a DNP part.
    assert j1.placement.side == "bottom" and j1.placement.rotation == pytest.approx(270.0)
    assert j1.mount == "tht" and not j1.populate
    assert all(p.kind == "tht" and p.drill.size == (1.0, 1.0) for p in j1.footprint.pads)
    assert all(p.layers == ("*.Cu", "*.Mask") for p in j1.footprint.pads)
    c1 = b.by_reference()["C1"]
    assert c1.placement.side == "top" and c1.placement.rotation == pytest.approx(270.0)
    assert c1.footprint.pads[0].shape == "roundrect" and c1.footprint.pads[0].roundrect_ratio == pytest.approx(0.25)
    assert all(p.layers == ("F.Cu", "F.Paste", "F.Mask") and p.paste for p in c1.footprint.pads)
    assert c1.properties == {"LCSC": "C307331", "Value": "100nF"}
    assert not b.by_reference()["R2"].populate


def test_kicad_demo_board_through_hole():
    b = read_components(PIC_IPC)
    assert (b.name, b.warnings) == ("pic_programmer", [])
    assert len(b.components) == 63
    assert {c.mount for c in b.components} == {"tht", ""}
    jp1 = b.by_reference()["JP1"]
    assert jp1.placement.side == "bottom" and len(jp1.footprint.pads) == 2


@pytest.mark.parametrize("module", ["ipc2581", "odbpp", "eda"])
def test_readers_stay_pure(module):
    """Bytes or a path in, components out: no web framework, no network, no file writes; stdlib and boarddd only."""
    import io
    import tokenize

    src = (REPO / "python" / "src" / "boarddd" / "io" / f"{module}.py").read_text()
    code = "".join(
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    for forbidden in ("django", "requests", "urllib", "socket", "'w'", '"w"', "'wb'", "write_"):
        assert forbidden not in code, forbidden
    tree = ast.parse(src)
    roots = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    roots |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
    }
    assert roots - {"boarddd", "__future__"} <= set(sys.stdlib_module_names), roots


# ─── Shared board type, heights, invented pad names, precision ───────────────


def _one(units="MILLIMETER", scale=1.0, pin="1", bom="", comp_attrs="", pkg_attrs=""):
    pkg = f'''<Package name="P1"{pkg_attrs}><Pin number="{pin}"><Location x="0.123456" y="0"/>
              <StandardPrimitiveRef id="R"/></Pin><Pin number="2"><Location x="1.5" y="0"/>
              <StandardPrimitiveRef id="R"/></Pin></Package>'''
    return read_components(
        doc(
            units,
            scale,
            packages=pkg,
            bom=bom,
            components=f'<Component refDes="U1" packageRef="P1" layerRef="TOP"'
            f'{comp_attrs}><Location x="0" y="0"/></Component>',
        )
    )


def test_the_board_is_the_shared_model_type():
    from boarddd.io import eda as model
    from boarddd.io import ipc2581, odbpp

    board = _one()
    assert isinstance(board, model.Board) and isinstance(board.components[0], model.Component)
    assert ipc2581.Board is model.Board and odbpp.Component is model.Component
    assert board.components[0].footprint.source.kind == "ipc2581"


def test_heights_in_file_units():
    u1 = _one("INCH", 25.4, comp_attrs=' height="0.05" standoff="0.004"', pkg_attrs=' height="0.045"').components[0]
    assert u1.height == pytest.approx(1.27) and u1.standoff == pytest.approx(0.1016)
    assert u1.footprint.height == pytest.approx(1.143)
    plain = _one().components[0]
    assert (plain.height, plain.standoff, plain.footprint.height) == (None, None, None)


def test_kicads_invented_pad_names_are_no_numbers():
    kicad = '<LogisticHeader><SoftwarePackage name="KiCad" revision="10.0.6" vendor="KiCad EDA"/></LogisticHeader>'
    pads = _one(pin="PAD0", bom=kicad).components[0].footprint.pads
    assert (pads[0].number, pads[0].name) == (None, "PAD0")
    # From another exporter a pin really named PAD0 keeps its name as its number.
    other = _one(pin="PAD0").components[0].footprint.pads
    assert (other[0].number, other[0].name) == ("PAD0", "")


def test_resolution_from_the_numbers():
    source = _one().components[0].footprint.source
    assert source.resolution == pytest.approx(1e-6)
    assert "6 decimals" in source.resolution_basis
    inch = _one("INCH", 25.4).components[0].footprint.source
    assert inch.resolution > 1e-6  # decimals of an inch, in mm
