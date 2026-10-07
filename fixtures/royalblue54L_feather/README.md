# royalblue54L_feather

KiCad's demo board `royalblue54L_feather` (RoyalBlue54L Feather by Lords Boards, CERN-OHL-P v2, see
`LICENSE`): 58.42 × 22.86 mm, 8 copper layers, ENIG, blue mask, 71 footprints (15 on the bottom).

- `kicad/RoyalBlue54L-Feather.kicad_pcb`, `.kicad_pro`: the board and project files from KiCad 10.0.6's `demos/`
  folder, unmodified.
- `fab/`: what `make_fab.sh` exports from it with kicad-cli 10.0.6: all copper, mask, paste, silk and
  Edge.Cuts Gerbers (X2), the gbrjob, PTH/NPTH Excellon (mm, oval holes as G85 slots) and the pos file.
- `board.json`: the golden `boarddd/board@1` model (see [docs/model.md](../../docs/model.md)), built by
  `make_board.py` from the readers: `boarddd.io.kicad.read_kicad_pcb` on `kicad/` (components, footprints,
  outline, stackup, origins, nets, net classes) and `boarddd.io.package.read_package` on `fab/` (layers,
  drills), with the placements checked against `fab/pos.csv`.

Checks on it: the Python tests (`python/tests/test_golden.py`) and the JS tests (`test/model/`):

- the script reproduces `board.json` byte for byte (CI);
- `python/tests/kicad/test_kicad_royalblue.py`: the KiCad half against pcbnew's own pad positions, holes and net
  classes, the board file's drills against the Excellon files, the stackup against the gbrjob kicad-cli wrote;
- every `source.files` hash matches;
- every placed thru-hole pad lands on its drill (this also checks the bottom-side flip-back and the
  placement transform);
- the 22 top-side footprints equal boarddd's own JS `.kicad_mod` parser's output for the same
  footprints.

The demo's 183 vias have a 0.00001 mm placeholder drill, written as `C0.000` in the drill file. They are kept
with diameter 0, with a warning.
