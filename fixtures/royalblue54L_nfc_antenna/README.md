# royalblue54L_nfc_antenna

The NFC antenna board from KiCad's `royalblue54L_feather` demo (`demos/royalblue54L_feather/nfc_antenna/`, by Lords
Boards, CERN-OHL-P v2, see `LICENSE`): a two-layer flex antenna whose coil is routed with **track arcs** (48 of its
160 tracks), the case the copper readers need that royalblue54L_feather itself lacks.

- `kicad/RoyalBlue54L-NFC-Antenna.kicad_pcb`, `.kicad_pro`: from KiCad 10.0.6's demo folder, unmodified.
- `fab/`: what `make_fab.sh` exports from it with kicad-cli 10.0.6: F/B copper and mask, Edge.Cuts (Gerber X2), the
  gbrjob and PTH/NPTH Excellon.
- `copper.json`: the golden `boarddd/copper@1` document (see [docs/copper.md](../../docs/copper.md)), built by
  `make_copper.py` from `boarddd.io.kicad.read_kicad_copper`; CI checks it is reproducible.

Checks on it (`python/tests/io/test_gerber_copper.py`, `test/copper/`): the Gerber reader (Python and the browser's
`copperFromGerbers`) reads `fab/` into the same tracks, arcs (mid points within 2 um; kicad-cli writes three
arcs shorter than 3 um as straight draws), vias, pads and copper polygons.
