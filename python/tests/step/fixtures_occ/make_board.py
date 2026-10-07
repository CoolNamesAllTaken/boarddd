"""
Builds `tiny.kicad_pcb`, the test board behind tiny.step / tiny-pos.csv, from KiCad's stock footprints
and 3D models (KICAD10_FOOTPRINT_DIR). Run with a Python that has KiCad's pcbnew (KiCad 10):

    python3 python/tests/step/fixtures_occ/make_board.py OUT_DIR
    kicad-cli pcb export step -f -o tiny.step OUT_DIR/tiny.kicad_pcb
    kicad-cli pcb export pos --format csv --units mm --side both -o tiny-pos.csv OUT_DIR/tiny.kicad_pcb

What is on it, and why:
  R1-R3  R_0402 on top at 0/90/45 degrees: repeated instances of one model
  R4     R_0603: a different model of the same kind
  R5     R_0402 on the bottom at 30 degrees: the turned-over frame
  U1     QFN-16 3x3 EP1.7 on top
  U2     the same footprint with the EP1.8 model: a model version that differs only underneath
  U3     QFN-16 EP1.7 on the bottom at 180 degrees
  C1     CP_Elec_4x5.4: an electrolytic can
  D1     LED_D3.0mm (THT): a domed top, no planar pick surface
  Q1     SOT-23 with its model offset by (0.5, 0.2, 0) mm: the offset has to be recovered

Source: magpie `tests/step/fixtures_occ/make_board.py` at internal `claud/magpie` `3a0374d3` (synthetic).
"""

import os
import sys

import pcbnew

ROOT = os.environ.get("KICAD10_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
PARTS = [
    # ref, library, footprint, x, y, rotation, bottom, model override, model offset
    ("R1", "Resistor_SMD", "R_0402_1005Metric", 5, 5, 0, False, None, None),
    ("R2", "Resistor_SMD", "R_0402_1005Metric", 8, 5, 90, False, None, None),
    ("R3", "Resistor_SMD", "R_0402_1005Metric", 11, 5, 45, False, None, None),
    ("R4", "Resistor_SMD", "R_0603_1608Metric", 14, 5, 0, False, None, None),
    ("R5", "Resistor_SMD", "R_0402_1005Metric", 17, 5, 30, True, None, None),
    ("U1", "Package_DFN_QFN", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", 5, 12, 0, False, None, None),
    (
        "U2",
        "Package_DFN_QFN",
        "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
        11,
        12,
        90,
        False,
        "QFN-16-1EP_3x3mm_P0.5mm_EP1.8x1.8mm.step",
        None,
    ),
    ("U3", "Package_DFN_QFN", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", 17, 12, 180, True, None, None),
    ("C1", "Capacitor_SMD", "CP_Elec_4x5.4", 6, 20, 0, False, None, None),
    ("D1", "LED_THT", "LED_D3.0mm", 12, 20, 0, False, None, None),
    ("Q1", "Package_TO_SOT_SMD", "SOT-23", 17, 20, 0, False, None, (0.5, 0.2, 0.0)),
]


def main(out: str) -> None:
    board = pcbnew.BOARD()
    for ref, library, name, x, y, rotation, bottom, model, offset in PARTS:
        footprint = pcbnew.FootprintLoad(os.path.join(ROOT, f"{library}.pretty"), name)
        footprint.SetReference(ref)
        footprint.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        footprint.SetOrientationDegrees(rotation)
        if model or offset:
            # Models() hands back copies; replace the model rather than edit it.
            original = list(footprint.Models())[0]
            replacement = pcbnew.FP_3DMODEL()
            replacement.m_Filename = (
                original.m_Filename.rsplit("/", 1)[0] + "/" + model if model else original.m_Filename
            )
            replacement.m_Offset = pcbnew.VECTOR3D(*(offset or (0.0, 0.0, 0.0)))
            footprint.Models().clear()
            footprint.Add3DModel(replacement)
        board.Add(footprint)
        if bottom:
            footprint.Flip(footprint.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    outline = pcbnew.PCB_SHAPE(board)
    outline.SetShape(pcbnew.SHAPE_T_RECT)
    outline.SetStart(pcbnew.VECTOR2I_MM(0, 0))
    outline.SetEnd(pcbnew.VECTOR2I_MM(22, 25))
    outline.SetLayer(pcbnew.Edge_Cuts)
    board.Add(outline)
    os.makedirs(out, exist_ok=True)
    pcbnew.SaveBoard(os.path.join(out, "tiny.kicad_pcb"), board)


if __name__ == "__main__":
    main(sys.argv[1])
