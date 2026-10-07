"""Regenerate golden.json: pcbnew's own view of fixtures/royalblue54L_feather (KiCad 10's pcbnew module).

    python3 python/tests/kicad/fixtures/royalblue54L_pcbnew/make_golden.py   # a Python with KiCad's pcbnew (e.g. KiCad's own)

Per pad (board frame of KiCad: mm, y down): reference, number, position, orientation, copper centre (shape
offset), copper bbox, hole centre and size, net. Per net: its effective net class (pcbnew reads the
.kicad_pro next to the board).
"""

import json
import os

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(
    HERE, "..", "..", "..", "..", "..", "fixtures", "royalblue54L_feather", "kicad", "RoyalBlue54L-Feather.kicad_pcb"
)
mm = pcbnew.ToMM


def r(v):
    return round(v, 6) + 0.0


board = pcbnew.LoadBoard(os.path.abspath(PCB))
pads = []
for fp in board.GetFootprints():
    for p in fp.Pads():
        layer = pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
        bb = p.GetEffectiveShape(layer).BBox()
        drill = p.GetDrillSize()
        shape = p.ShapePos(layer)
        pads.append(
            {
                "ref": str(fp.GetReference()),
                "number": str(p.GetNumber()),
                "at": [r(mm(p.GetPosition().x)), r(mm(p.GetPosition().y))],
                "angle": r(p.GetOrientationDegrees()),
                "copper_center": [r(mm(shape.x)), r(mm(shape.y))],
                "copper_bbox": [r(mm(bb.GetLeft())), r(mm(bb.GetTop())), r(mm(bb.GetRight())), r(mm(bb.GetBottom()))],
                "hole": [r(mm(drill.x)), r(mm(drill.y))] if drill.x else None,
                "net": str(p.GetNetname()),
            }
        )
nets = {str(name): str(net.GetNetClassName()) for name, net in board.GetNetInfo().NetsByName().items() if str(name)}
with open(os.path.join(HERE, "golden.json"), "w") as fh:
    json.dump({"pads": pads, "net_classes": dict(sorted(nets.items()))}, fh, indent=0, sort_keys=True)
    fh.write("\n")
print(f"wrote {len(pads)} pads, {len(nets)} nets")
