"""Regenerate golden.json: KiCad's own copper polygon of every pad in Pad_Shapes_boarddd.kicad_mod.

    python3 test/fixtures/pad_shapes/make_golden.py   # a Python with KiCad's pcbnew (e.g. KiCad's own)

Needs KiCad 10's `pcbnew` module. Per pad: area (mm^2) and bbox (KiCad mm, y down) of
PAD.GetEffectivePolygon(F_Cu), hole centre and its effective hole (slot) length and width.
"""
import json
import os
import shutil
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
mm = pcbnew.ToMM

tmp = tempfile.mkdtemp()
lib = os.path.join(tmp, "fx.pretty")
os.mkdir(lib)
shutil.copy(os.path.join(HERE, "Pad_Shapes_boarddd.kicad_mod"), lib)
fp = pcbnew.FootprintLoad(lib, "Pad_Shapes_boarddd")
out = []
for p in fp.Pads():
    poly = p.GetEffectivePolygon(pcbnew.F_Cu, pcbnew.ERROR_INSIDE)
    bb = poly.BBox()
    hole = None
    if p.HasHole():
        h = p.GetEffectiveHoleShape()
        hole = {"start": [mm(h.GetSeg().A.x), mm(h.GetSeg().A.y)], "end": [mm(h.GetSeg().B.x), mm(h.GetSeg().B.y)],
                "width": mm(h.GetWidth())}
    out.append({"number": p.GetNumber(), "area": poly.Area() / 1e12,
                "bbox": [mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom())], "hole": hole})
shutil.rmtree(tmp)
with open(os.path.join(HERE, "golden.json"), "w") as fh:
    json.dump(out, fh, indent=1)
print(f"wrote {len(out)} pads")
