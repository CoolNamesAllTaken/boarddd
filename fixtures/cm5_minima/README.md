# cm5_minima

KiCad's demo board **CM5 MINIMA REV3** (a Raspberry Pi Compute Module 5 carrier, by its author as shipped in KiCad
10.0.6's `demos/cm5_minima`; CERN-OHL-S v2, see `LICENSE`): 6 copper layers, a stackup with Er per layer
(prepreg 4.45, core 4.6), and the one demo with **controlled-impedance net classes** (`100ohm` differential:
Ethernet, HDMI, CSI; `90ohm`: USB, PCIe) and 32 differential pairs routed over solid planes.

- `kicad/CM5_MINIMA_3.kicad_pcb.gz`: the board file, unmodified (gzip of the demo's file);
  `kicad/CM5_MINIMA_3.kicad_pro`: its project file (net classes), unmodified.

Used by `python/tests/test_impedance_route.py`: the route analysis of its 100 Ω and 90 Ω classes against kipr's
per-class check, and Python = JS on its pairs (docs/impedance.md "Along a route").
