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
- `blue_board.step`: from kipr (`tests/library/fixtures/color/`): one solid with the RP2040-Zero board
  colour (0.090/0.224/0.420), for the STEP colour check.
- `royalblue54L_components.json`: KiCad demo data (`demos/royalblue54L_feather`), placements from
  `kicad-cli pcb export pos` (KiCad 10.0.6), y flipped back to KiCad's y-down; pairs with
  `examples/data/royalblue54L_feather/board.glb`.
- `pic_programmer/`: from the wasm-gerber-viewer fork's `examples/board-diff` (at `92976b5`): Gerber and
  Excellon exports (`kicad-cli pcb export gerbers`, `export drill --excellon-separate-th`, KiCad 10.0.6) of
  KiCad's `demos/pic_programmer` (**KiCad demo data**). `head/` has three edits: mounting hole P101 moved
  from (77.47, 135.89) to (80.47, 133.89) mm, the first two F.Cu and four B.Cu track segments deleted, the
  F.Cu track (141.986, 87.63)-(145.1, 90.744) widened 0.8 to 1.5 mm. Edge_Cuts and B_Silkscreen differ only
  in timestamps.
