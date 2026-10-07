# Python readers (`boarddd.io`, `boarddd.step`)

Server-side readers that turn a fab package into the board model ([model.md](model.md)). Stdlib only;
`.xlsx`/`.xls` BOMs need the `[xlsx]` extra (openpyxl, xlrd). The KiCad readers (`boarddd.io.kicad`),
IPC-2581/ODB++ (`io.ipc2581`, `io.odbpp`) and the OCP-backed STEP half (`[step]`) are later phases.

```python
from boarddd.io.package import read_package

board = read_package("fab/")            # a folder, or read_package("export.zip")
open("board.json", "w").write(board.to_json())
```

| module | reads | source (magpie `src/magpie/…` at `3a0374d3`) |
|---|---|---|
| `io.package` | a folder or zip → `Board` (the one entry point) | boarddd (new) |
| `io.archive` | zips, safely (zip slip, bombs, symlinks, member floods) | `pcb/extract.py` |
| `io.classify` | what each file is: X2 `%TF.FileFunction`, `M48`, eight EDA naming conventions, content sniffing; `FabFile` kinds | `pcb/classify.py` + `pcb/kinds.py` |
| `io.gerber` | the board profile KiCad plots onto other layers (`plots_profile`, `without_profile`) | `pcb/gerber.py` |
| `io.pads` | flashes/regions as pad boxes; the pad pattern around a placement | `pcb/pads.py` |
| `io.excellon` | holes and slots (G85 and rout mode), implied decimals, plating; `distinct` | `pcb/excellon.py` (+ `Hole.tool`, `parse(keep_empty=)`) |
| `io.outline` | Edge.Cuts strokes stitched into loops; `pick_board` by the gbrjob size; `cutouts` | `pcb/outline.py` |
| `io.gbrjob` | board size, project name, panel naming | `pcb/jobfile.py` |
| `io.pos` | pick-and-place in any tool's layout (header search, units, fixed-width) | `pcb/posfile.py` |
| `io.bom` | BOMs in any tool's layout; DNP; consolidation | `pcb/bom.py` |
| `step.text` | a STEP's assembly tree as text: refdes, placements, sides, extents | `step/stepmeta.py` |
| `step.slim` | a STEP without the board dressing (pads, silk, mask) for the browser | `step/stepslim.py` |
| `step.modelfile` | whether a GLB/glTF/STEP/STL is what its name says | `step/modelfile.py` |
| `step.registration` | the rigid transform from a STEP's frame onto the placements | `pcb/registration.py` |

Each copied module names its source file and commit in its docstring. Magpie switches to these modules
in phase G2, which re-diffs magpie's later changes first. magpie's `step/steptext.py` (the numpy index)
goes with the `[step]` extra (F4), and `footprint/gerbers.py` stays in magpie for now.

## `read_package`: where each field comes from

| board field | from |
|---|---|
| `name`, `revision` | gbrjob `ProjectId` (KiCad's `rev?` placeholder means no revision); else the folder/zip name |
| `source` | every file: role (from `classify`), side, SHA-256; `generator`/`created` from the gbrjob header |
| `layers` | every Gerber and drill file. `id`: KiCad names from the X2 function (`L1`→`F.Cu`, `L<n>`→`In<n-1>.Cu`, last→`B.Cu`, `Profile`→`Edge.Cuts`, `PTH`/`NPTH`). `order`: copper 1..N; then outline, paste, silk, mask at N+1…N+4; PTH N+7, NPTH N+8; fab, user after. `function` is the file's own `%TF.FileFunction`; `polarity` the file's `%TF.FilePolarity` (else the gbrjob's) |
| `outline` | the outline Gerber's loops; the board is the loop matching the gbrjob size, else the largest; CCW board, CW cutouts |
| `stackup` | gbrjob `GeneralSpecs` (thickness, layer count, finish) and `MaterialStackup` (names, kinds, thicknesses, materials, colours) |
| `drills` | every Excellon file; zero-diameter placeholder tools kept as 0 with a warning; a hole repeated by a later file kept once (two hits in one file are both kept) |
| `components` | the pick-and-place file(s); a BOM adds MPNs, DNP and missing values |
| left empty | `footprints`, `origin`, models, `mount`, heights, nets: a fab package does not carry them |

## Reproducing the golden `fixtures/royalblue54L_feather/board.json`

`read_package("fixtures/royalblue54L_feather/fab")` against the golden, which `make_board.py` built by hand
from the `.kicad_pcb` (plus the same fab files). `python/tests/io/test_package.py` pins all of this.

| field | read_package (fab/ only) | golden | why |
|---|---|---|---|
| `name`, `revision` | same | | |
| `source.kind` | `gerber` | `kicad_pcb` | the input format |
| `source.files` | the 19 fab files, same roles/sides/hashes; paths relative to `fab/` | also `kicad/….kicad_pcb`; paths relative to the fixture | the package root |
| `source.generator`, `created` | `KiCad Pcbnew 10.0.6`; the gbrjob's `2026-10-07T08:23:33+00:00` | adds `pcbnew 9.0 (board file)`; the title block date `2025-02-12` | the fab files don't know the board file's version or title block |
| `origin` | all null | `aux` (119.1, −116.41), `grid` (119.1, −104.98) | Gerbers carry no origins |
| `outline` | same loop, start point and box; 59 points; area 0.018 mm² smaller | 76 points | KiCad's Gerber arcs flattened at 48 points per turn vs the golden's 72 per arc; every point within 5 µm of the other polygon |
| `stackup` | thickness, copper count, finish, colours; every physical layer's kind, side, thickness, material, colour and drawing layer | same | |
| `stackup.layers[].name` | the gbrjob's (`Top Silk Screen`, `F.Cu/In1.Cu`) | the board file's (`F.SilkS`, `dielectric 1`) | source names |
| `stackup.layers[].epsilon_r`, `loss_tangent` | null | 4.5, 0.02 on dielectrics | this gbrjob doesn't list them |
| `layers` | same ids, roles, sides, order, format, polarity, plating | | |
| `layers[].function` | the file's `%TF`: `Profile,NP`, `Paste,Top`, `Soldermask,Top` | the gbrjob's: `Profile`, `SolderPaste,Top`, `SolderMask,Top` | the model documents the raw `%TF.FileFunction` |
| `drills` | identical: 278 holes, 183 zero-diameter placeholder vias, tools, functions | | |
| `footprints` | `{}` | 27 footprints with pads and graphics | not in a fab package (phase F2's KiCad reader) |
| `components` | the 50 parts in the pos file: ref, side, x, y, rotation, value identical | 71 parts (21 not in the pos file) | the pos file lists only placed parts |
| `components[].footprint` | the pos `Package` (`C_0402_1005Metric`) | the library id (`Capacitor_SMD:C_0402_1005Metric`) | the pos file drops the library |
| `components[].populate` | true for all | R2, R3 are DNP | a pos file has no DNP column (a BOM would add it) |
| `components[].mount`, `models`, `attributes`, `in_bom` | null / empty / default | from the board file | not in a fab package |
| `warnings` | one: the 183 zero-diameter holes | one: the same, other wording | |

## Tests and fixtures

`python/tests/io/` and `python/tests/step/` hold magpie's tests, ported onto public data only: the
royalblue54L_feather fab export, `test/fixtures/pic_programmer` and `slots-board`, and data generated
from them under `fixtures/generated/` (each folder has its make script; see
[fixtures/LICENSES.md](../fixtures/LICENSES.md)). `test_parity_js.py` checks `io.excellon` against
`boarddd/gerber` `parseExcellon` and `io.outline` against `boardOutline` on the same files (needs `node`).
