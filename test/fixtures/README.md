# Test fixtures

- `pad_placement/`: from kipr (see its README): KiCad's pad/hole/model positions for four footprints.
- `pad_shapes/`: one pad of every KiCad shape (`Pad_Shapes_boarddd.kicad_mod`, written for boarddd) and
  pcbnew's own polygons for them (`make_golden.py`).
- `footprints/`: `USB_C_Receptacle_HRO_TYPE-C-31-M-12` from KiCad's stock library (CC-BY-SA 4.0 with
  the KiCad libraries exception; oval shell-tab drills) and `MountingHole_Slotted_boarddd` (written for
  kipr PR #18: NPTH 1.5x3, PTH 3x1.5, PTH 1x2 at 45 deg, PTH 2x1 at 90 deg).
- `slots-board/`: both footprints on a 35 x 20 mm board (`slots.kicad_pcb`), with its Gerbers, drills and
  GLB from `scripts/export-demo.sh` (KiCad 10.0.6). `slots-board-base/`: the same board without H1, for
  the copper-diff test.
