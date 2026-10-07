# royalblue54L_feather

KiCad's demo board `royalblue54L_feather` (RoyalBlue54L Feather by Lords Boards, CERN-OHL-P v2, see
`LICENSE`): 58.42 × 22.86 mm, 8 copper layers, ENIG, blue mask, 71 footprints (15 on the bottom).

- `kicad/RoyalBlue54L-Feather.kicad_pcb`: the board file from KiCad 10.0.6's `demos/` folder, unmodified.
- `fab/`: what `make_fab.sh` exports from it with kicad-cli 10.0.6: all copper, mask, paste, silk and
  Edge.Cuts Gerbers (X2), the gbrjob, PTH/NPTH Excellon (mm, oval holes as G85 slots) and the pos file.
- `board.json`: the golden `boarddd/board@1` model (see [docs/model.md](../../docs/model.md)), built by
  `make_board.py`. That script is a temporary, hand-written stand-in: it reads the `.kicad_pcb` for the
  components, footprints, outline, stackup and origins, the gbrjob for the layer files, and the
  Excellon files for the drills, and checks the placements against `fab/pos.csv`. The phase F readers
  (`boarddd.io.*`) must reproduce `board.json`; then the script goes.

Checks on it: the Python tests (`python/tests/test_golden.py`) and the JS tests (`test/model/`):

- the script reproduces `board.json` byte for byte (CI);
- every `source.files` hash matches;
- every placed thru-hole pad lands on its drill (this also checks the bottom-side flip-back and the
  placement transform);
- the 22 top-side footprints equal boarddd's own JS `.kicad_mod` parser's output for the same
  footprints.

The demo's 183 vias have a 0.00001 mm placeholder drill, written as `C0.000` in the drill file. They are kept
with diameter 0, with a warning.
