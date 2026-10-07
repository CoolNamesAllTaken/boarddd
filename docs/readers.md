# Python readers (`boarddd.io`, `boarddd.step`)

Server-side readers that turn a fab package or KiCad files into the board model ([model.md](model.md)).
Stdlib only; `.xlsx`/`.xls` BOMs need the `[xlsx]` extra (openpyxl, xlrd). The OCP-backed STEP engine is the
`[step]` extra: see [step.md](step.md).

```python
from boarddd.io.package import read_package

board = read_package("fab/")            # a folder, or read_package("export.zip")
open("board.json", "w").write(board.to_json())
```

| module | reads | source (magpie `src/magpie/…` at `3a0374d3`) |
|---|---|---|
| `io.package` | a folder, zip or single file → `Board` (the one entry point; detects IPC-2581 and ODB++) | boarddd (new) |
| `io.ipc2581` | IPC-2581 rev A/B/C (`.xml`/`.cvg`, gzip accepted) → `Board`: components + packages (`read_components`), profile, holes and slots, layers, stackup with Er/Df, nets | `pcb/ipc2581.py`; `read_ipc2581` (board level) new (F3) |
| `io.odbpp` | ODB++ v7/v8 (dir, `.zip`, `.tgz`/`.tar`, `.Z`/`.gz` members) → `Board`: components + packages (`read_components`), profile, drill-layer holes and slots, matrix layers, per-layer stackup values, nets | `pcb/odbpp.py`; `read_odbpp` (board level) new (F3); two fixes, below |
| `io.eda` | the component model both readers build, and `to_model` onto `boarddd.model` | `footprint/model.py` (subset); `to_model` new |
| `io.layers` | layer ids (KiCad names) and `order`, shared by package, IPC-2581 and ODB++ | boarddd (new) |
| `io.archive` | zips, safely (zip slip, bombs, symlinks, member floods) | `pcb/extract.py` |
| `io.classify` | what each file is: X2 `%TF.FileFunction`, `M48`, eight EDA naming conventions, content sniffing; `FabFile` kinds | `pcb/classify.py` + `pcb/kinds.py` |
| `io.gerber` | the board profile KiCad plots onto other layers (`plots_profile`, `without_profile`) | `pcb/gerber.py` |
| `io.pads` | flashes/regions as pad boxes; the pad pattern around a placement | `pcb/pads.py` |
| `io.excellon` | holes and slots (G85 and rout mode), implied decimals, plating; `distinct` | `pcb/excellon.py` (+ `Hole.tool`, `parse(keep_empty=)`) |
| `io.outline` | Edge.Cuts strokes stitched into loops; `pick_board` by the gbrjob size; `cutouts` | `pcb/outline.py` |
| `io.gbrjob` | board size, project name, panel naming; `read_stackup`: GeneralSpecs + MaterialStackup (Er/Df strings, KiCad's `(1/2)` sublayers, `ImpedanceControlled`, conductivity) | `pcb/jobfile.py`; `read_stackup` new (I1) |
| `io.pos` | pick-and-place in any tool's layout (header search, units, fixed-width) | `pcb/posfile.py` |
| `io.bom` | BOMs in any tool's layout; DNP; consolidation | `pcb/bom.py` |
| `step.text` | a STEP's assembly tree as text: refdes, placements, sides, extents | `step/stepmeta.py` |
| `step.slim` | a STEP without the board dressing (pads, silk, mask) for the browser | `step/stepslim.py` |
| `step.modelfile` | whether a GLB/glTF/STEP/STL is what its name says | `step/modelfile.py` |
| `step.registration` | the rigid transform from a STEP's frame onto the placements | `pcb/registration.py` |

The KiCad readers are in [KiCad files](#kicad-files-boardddiokicad) below.

Each copied module names its source file and commit in its docstring. Magpie switches to these modules
in phase G2, which re-diffs magpie's later changes first. magpie's `step/steptext.py` (the numpy index)
is `step.index` in the `[step]` extra ([step.md](step.md)), and `footprint/gerbers.py` stays in magpie for now.

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

`read_package("fixtures/royalblue54L_feather/fab")` against the golden, which `make_board.py` builds from
`read_kicad_pcb` on `kicad/` plus `read_package` on `fab/` (layers, drills and their warning come from the
latter). `python/tests/io/test_package.py` pins all of this.

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
| `layers` | identical (paths relative to `fab/`); `function` is the file's own `%TF` (`Profile,NP`, `Soldermask,Top`) | | |
| `drills` | identical: 278 holes, 183 zero-diameter placeholder vias, tools, functions | | |
| `footprints` | `{}` | 27 footprints with pads and graphics | not in a fab package (`io.kicad`) |
| `components` | the 50 parts in the pos file: ref, side, x, y, rotation, value identical | 71 parts (21 not in the pos file) | the pos file lists only placed parts |
| `components[].footprint` | the pos `Package` (`C_0402_1005Metric`) | the library id (`Capacitor_SMD:C_0402_1005Metric`) | the pos file drops the library |
| `components[].populate` | true for all | R2, R3 are DNP | a pos file has no DNP column (a BOM would add it) |
| `components[].mount`, `models`, `attributes`, `in_bom` | null / empty / default | from the board file | not in a fab package |
| `warnings` | one: the 183 zero-diameter holes | the same | |
| `nets`, `net_classes`, stackup Er/Df, prepreg/core | empty / null | 95 nets, `Default` + `USB_DIFF`; 4.5 / 0.02 | from the board and project files |

## KiCad files (`boarddd.io.kicad`)

No pcbnew or kicad-cli needed; KiCad 5 to 10 formats.

```python
from boarddd.io.kicad import read_kicad_pcb, read_kicad_mod, read_kicad_pro, read_kicad_sym

board = read_kicad_pcb("board.kicad_pcb")      # + the .kicad_pro next to it, if there is one
fp = read_kicad_mod("R_0603.kicad_mod", name="Resistor_SMD:R_0603_1608Metric")
symbols = read_kicad_sym("Device.kicad_sym")    # {name: Symbol}, parse only
```

| module | reads | source |
|---|---|---|
| `io.kicad.sexpr` | the s-expression parser: spans, `Atom` vs quoted strings, `\|base64\|` data, `dumps` | kipr `common/sexpr.py` (magpie's tokenizer dropped) |
| `io.kicad.pcb` | `read_kicad_pcb` → `Board`: outline (Edge.Cuts incl. footprint cut-outs, ends within 10 µm joined, 72 segments per turn), stackup, origins, footprints, components, drills (pad holes, oval holes as slots, vias), nets with diff pairs; `load` → kipr's item view for diffs (tracks, vias, zones, keys, boxes) | kipr `project/pcb.py` + `geom.py`, magpie `pcb/kicad_pcb.read_board`, `make_board.py` |
| `io.kicad.footprint` | `read_kicad_mod`, `read_footprint` → model `Footprint` (library form: bottom instances flipped back, pad angles relative); castellated shape offsets (`Pad.offset`), heatsink pads, drawn paste openings; `KicadFootprint`, kipr's raw parse for renderers | `make_board.py` (= the JS parser), magpie `kicad_pcb.read_footprint`, kipr `library/render/fp.py` |
| `io.kicad.project` | `.kicad_pro` net classes: explicit assignments, patterns (wildcards or regex), priority, values inherited from Default; impedance targets | new (I1) |
| `io.kicad.symbol` | `.kicad_sym`: symbols, units, De Morgan, pins, graphics, `extends` | kipr `library/render/sym.py` (parse half) |
| `io.kicad.geom` | arc/stroke flattening and pad outlines, shared with the JS ports | `src/geom/*.js`, `src/footprint/kicad_mod.js` |

What the reader decides:

- **Layers** stay empty: drawable layers are fab outputs (`read_package`). `drills` come from the board file
  and match kicad-cli's Excellon to its 3 decimals.
- **Footprints**: a library id's definition is its first top-side instance (else bottom); an instance with
  different pads or drawings gets its own key `lib_id#ref` and a warning. Repeated references (logos,
  `G***`) get a `#n` suffix, the original kept in `attributes.Reference`.
- **Stackup**: a dielectric with `addsublayer` lists its `sublayers`; the layer's thickness is their sum,
  `epsilon_r` the series value (t / Σ tᵢ/εᵢ) and `loss_tangent` the thickness-weighted mean.
  `(thickness … locked)` → `locked`, `spec_frequency`/`dielectric_model` (KiCad master) → `frequency`,
  `dielectric_model`, `dielectric_constraints` → `stackup.impedance_controlled`.
- **Nets**: the `(net N "name")` table (KiCad ≤ 9) or every name items use (KiCad 10). `pair`: KiCad's rule,
  a `+`/`-` or `P`/`N` (`_P`/`_N`) suffix whose partner exists.
- **Impedance targets**, first that applies: the class's KiCad 10 tuning profile (`type` 0/1, widths and gaps in
  nm, as `common/project/tuning_profiles.cpp` writes them; reference planes per layer); else the class name
  (`SE_50_CP`, `DP_90_MS`, `BAL_D90_C30_CPWG`, `50ohm`, `85Ohm-diff_PCIE`, `zse_50r`; MS/SL/CP(W)/CPWG give the
  structure). A value-only name (`90ohm`) whose nets are all pair halves is differential. KiCad 5 boards keep
  classes in the board file (`net_class`), read too.

Tests (`python/tests/kicad/`): kipr's pcbnew golden set (`fixtures/pad_placement`: copper centres, bboxes,
holes, models, trapezoid corners), `test/fixtures/pad_shapes` (area, bbox, effective hole of every shape), a
pcbnew golden of the royalblue board (`fixtures/royalblue54L_pcbnew`: all 428 pads placed through the model's
transform, net classes), the stackup kicad-cli wrote into the gbrjob, hand-written KiCad 5 and 10 boards,
project/tuning-profile cases, and every KiCad demo board when `KICAD_DEMOS` (default `/usr/share/kicad/demos`) is
there. Each golden folder has its `make_golden.py` (`kicad-python`, KiCad 10.0.6).

## IPC-2581 and ODB++ (`io.ipc2581`, `io.odbpp`)

```python
from boarddd.io.ipc2581 import read_ipc2581
from boarddd.io.odbpp import read_odbpp

board = read_ipc2581("board-ipc2581.xml.gz")     # or read_odbpp("board-odb.zip" | "job.tgz" | "job/")
board = read_package("export/")                  # the same, when the folder/zip holds no Gerber copper
```

Both read magpie's component model (`read_components`: placement, package with pads/paste/mask/courtyard/fab/silk,
BOM properties) and convert it with `io.eda.to_model`: a placement is the model's transform as it is; footprints
flip to KiCad semantics (y down, pad position = its hole, copper offset in `drill.offset`); a package shared by
several components becomes one footprint (instances that differ beyond the file's coordinate step become
`<name>#2` with a warning). The board level is boarddd's own:

| board field | IPC-2581 | ODB++ |
|---|---|---|
| `name` | the Step's name | the file/folder name (KiCad's `JOB_NAME` is `job`) |
| `source.generator`, `created` | `SoftwarePackage` name + revision | `misc/info` `SAVE_APP`, `CREATION_DATE` |
| `outline` | `Step/Profile` polygon (+ `Cutout`s), arcs at 48 points per turn | the step `profile` surface (islands, holes), arcs likewise |
| `drills` | `Hole`s and `SlotCavity`s of the DRILL/ROUT layers; `VIA` → via, `PLATED` → component | `P` features of the drill layers (`oval` symbols are slots), `.drill` attribute for plating and vias |
| `layers` | `CadData/Layer` by `layerFunction`; KiCad ids, the package order | the matrix by `TYPE` (DOCUMENT layers by name); KiCad ids, the package order |
| `stackup` | `Stackup` thicknesses, `overallThickness`; `CadHeader/Spec` materials, colours, Er, Df | each layer's `attrlist`: thickness (KiCad writes 0.01 mm), Er, Df, material; no overall thickness or colours |
| `nets` | net names of the `LayerFeature` sets (+ `LogicalNet`/`PhyNet`) | `NET` records of `eda/data` |

`read_package` picks the format by content: an IPC-2581 document (`<IPC-2581`, gzip accepted), an ODB++ archive
(a zip or tar with `matrix/matrix`) or an unpacked ODB++ tree inside the package. With Gerber copper present the
Gerbers stay the source and a warning names the IPC-2581/ODB++ file. Nested archives inside a *zipped* package are
skipped by `io.archive` (by design), so ship an ODB++ zip as the package itself or inside a folder.

Fixes to magpie's ODB++ reader (found by the cross-check below): a pad's copper is the copper feature nearest its
pin, not the first one listed (an exposed pad's thermal-via pins list the pad's copper too); and copper drawn away
from its hole (KiCad's `(drill (offset))`) stays where the feature is, with the hole as the drill offset (it was
moved onto the hole).

### Against the `.kicad_pcb` (KiCad 10.0.6 exports of the royalblue54L_feather and pic_programmer demos)

`python/tests/io/test_exchange.py` reads both exports of both boards and compares them with `read_kicad_pcb`:

| | IPC-2581 | ODB++ |
|---|---|---|
| components | all (71, 63); position, rotation, side, value identical | royalblue: 70 (LOGO1, a footprint without pads, is not exported); within 0.0075 mm (2 decimals) |
| pads (placed on the board) | identical but: J1/J2 offset rect pads (27) 0.30 mm off, KiCad's exporter writing the copper offset along board x unrotated; U2/U5 one sub-pad fewer (merged) | identical within 0.016 mm but: J2, U5 one sub-pad fewer (merged) |
| custom pads | the outline as a polygon on a 0.01 mm anchor (KiCad's anchor shape is merged into it) | likewise |
| `connect` pads | read as `smd` | likewise |
| nets | identical (95, 111) | identical |
| outline | within 5 µm; same area to 0.02 mm² | within 10 µm |
| drills | identical: 278 and 251, 4 slots (ends in the other order), placeholder vias at 0 | within 0.0075 mm |
| stackup | thickness 1.6, Er 4.5 / Df 0.02, FR4, mask colour | per-layer thickness, Er, Df; no overall thickness (would sum to 1.56 from rounded values) or colours |
| footprints | keyed by the export's package names: KiCad's with a `_<n>` suffix and no library (`D_DO-35_SOD27_P7.62mm_Horizontal_9`); KiCad writes one package per differing instance group, so pic_programmer has 23 packages for 21 footprints (3 for the D_DO-35 P7.62) | the package names without the suffix (`D_DO-35_SOD27_P7.62mm_Horizontal`), no library; 26 for royalblue (27 less LOGO1), 21 for pic_programmer |

## ODB++ in the browser (`boarddd/gerber` `loadOdbJob`)

```js
import { createGerberRenderer, loadOdbJob, groupBoardLayers, renderBoard } from 'boarddd/gerber';
const layers = await loadOdbJob(file, { renderer });          // .zip, .tgz/.tar.gz, .tar, or a dropped folder
await renderBoard(renderer, groupBoardLayers(layers.map((l) => ({ name: l.name, content: l.source }))));
```

The upstream ODB++ loader (`js/src/odb` of the fork at `FORK_COMMIT`) is vendored by `scripts/sync-fork.sh` into
`third_party/wasm-gerber-renderer/odb/`; `src/gerber/odb.js` wraps it, and `src/gerber/odb-zip.js` reads ZIPs with
the platform `DecompressionStream("deflate-raw")` (upstream's ZIP reader needs `node:zlib`). TAR/TGZ and folders use
upstream's readers; `.Z` members use the renderer's wasm. Each layer comes back as `{ name, kind, source }`, the
source an `%ODB++LAYER%` envelope the wasm reads like Gerber/Excellon text, named Gerber-style (`f.cu.gtl`) so name
classification works; `groupBoardLayers` / `renderBoard` take them like Gerbers (`hasGeometry`, `layerRole` and
`renderBoard`'s empty-drill check read the envelopes). `test/browser/gerber-odb.spec.mjs` draws royalblue's ODB++
copper and mask over its Gerbers (identical within 2 px) and the whole board both ways (99.7 % of board pixels
equal). Known renderer limit: the wasm draws an ODB++ oval drill pad (a KiCad slot) as a round hole.

## Tests and fixtures

`python/tests/io/` and `python/tests/step/` hold magpie's tests, ported onto public data only: the
royalblue54L_feather fab export and its IPC-2581/ODB++ exports, `fixtures/pic_programmer` (board + exports),
`test/fixtures/pic_programmer` and `slots-board`, and data generated
from them under `fixtures/generated/` (each folder has its make script; see
[fixtures/LICENSES.md](../fixtures/LICENSES.md)). `test_parity_js.py` checks `io.excellon` against
`boarddd/gerber` `parseExcellon` and `io.outline` against `boardOutline` on the same files (needs `node`).
