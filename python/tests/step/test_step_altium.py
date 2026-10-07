"""Altium's way of writing the board tree (root `PCB`, designators on products, Altium 24's components at the
origin with their models placed inside), rebuilt from the KiCad fixture, splits like the KiCad board."""

import json
from pathlib import Path

import pytest
from stepkit import TINY, TINY_POS, require_occ

require_occ()

from boarddd.step import occ  # noqa: E402
from boarddd.step.split import split  # noqa: E402


def altium(kicad_step: Path, out: Path, *, placed_inside: bool) -> Path:
    """
    A KiCad STEP rebuilt as Altium writes it: root product `PCB`, the board body as product
    `Board`, each component a product named by its designator under a numbered instance.
    `placed_inside`: the designator's product at the origin with its model placed inside it,
    as Altium 24 itself writes; otherwise the product placed.
    """
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TopLoc import TopLoc_Location

    doc = occ.read(kicad_step)
    tool = doc.shapes

    def name(label, text):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(text))

    old = max(occ.free_shapes(doc), key=lambda label: len(occ.components(label)))
    root = tool.NewShape()
    name(root, "PCB")
    for number, instance in enumerate(occ.components(old), 47):
        ref, product = occ.label_name(instance), occ.referred(instance)
        location = tool.GetLocation_s(instance)
        if ref.startswith("=>"):
            name(product, "Board")
            component = tool.AddComponent(root, product, location)
        else:
            holder = tool.NewShape()
            name(holder, ref)
            inner = tool.AddComponent(holder, product, location if placed_inside else TopLoc_Location())
            name(inner, "")
            component = tool.AddComponent(root, holder, TopLoc_Location() if placed_inside else location)
        name(component, "" if placed_inside else str(number))
    tool.RemoveShape(old, True)
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    assert writer.Transfer(doc.doc, STEPControl_AsIs)
    writer.Write(str(out))
    return out


@pytest.fixture(scope="module")
def kicad():
    return split(TINY, pos=TINY_POS, cache=False)


@pytest.mark.parametrize("placed_inside", [False, True])
@pytest.mark.parametrize("text", [True, False])
def test_altium_structure_splits_like_kicad(tmp_path, kicad, placed_inside, text):
    step = altium(TINY, tmp_path / "altium.step", placed_inside=placed_inside)
    assembly = split(step, pos=TINY_POS, cache=False, text=text)
    assert assembly.read_from == ("text" if text else "occt")
    assert sorted(assembly.components) == sorted(kicad.components)
    assert all(c.name_source == "product" for c in assembly.components.values())
    assert assembly.board.name == "Board" and assembly.board.thickness == pytest.approx(1.51)
    assert assembly.pos_fit.ok and assembly.pos_fit.residual_mm < 1e-3
    for ref, component in assembly.components.items():
        theirs = kicad.components[ref]
        assert component.transform == pytest.approx(theirs.transform, abs=1e-6), ref
        assert component.fingerprint.frame_key == theirs.fingerprint.frame_key, ref
        assert component.measurements.height == pytest.approx(theirs.measurements.height, abs=1e-6), ref
        assert component.offset == pytest.approx(theirs.offset, abs=1e-6), ref
        assert ("frame_from_solids" in component.flags) is False


def test_without_a_pos_file_the_solids_say_where(tmp_path, kicad):
    step = altium(TINY, tmp_path / "altium.step", placed_inside=True)
    assembly = split(step, cache=False)
    r1 = assembly.components["R1"]
    assert "frame_from_solids" in r1.flags
    assert r1.transform[:2, 3] == pytest.approx(kicad.components["R1"].transform[:2, 3], abs=1e-6)
    assert r1.measurements.size == pytest.approx(kicad.components["R1"].measurements.size, abs=1e-6)


def test_kicad_boards_read_as_before(kicad):
    # The Altium rules must not touch a KiCad board: designators from the instance names.
    assert all(c.name_source == "instance" for c in kicad.components.values())
    assert not any("frame_from_solids" in c.flags for c in kicad.components.values())
    assert json.dumps(kicad.board.to_dict()) == json.dumps(
        split(TINY, pos=TINY_POS, text=False, cache=False).board.to_dict()
    )
