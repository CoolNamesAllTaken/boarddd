# pic_programmer

KiCad's demo board `pic_programmer` (2 copper layers, 63 footprints, 7 on the bottom), for the IPC-2581 and
ODB++ readers' cross-check against the board file (`python/tests/io/test_exchange.py`).

- `kicad/pic_programmer.kicad_pcb`: the board file from KiCad 10.0.6's `demos/` folder, unmodified.
- `exchange/`: its IPC-2581 (`-ipc2581.xml.gz`, rev C, mm, gzipped) and ODB++ (`-odb.zip`) exports, written by
  `../make_exchange.sh` with kicad-cli 10.0.6.

The same demo's Gerber/Excellon exports (and an edited head revision) are in `test/fixtures/pic_programmer/`.
