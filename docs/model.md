# The board model (`boarddd/board@1`)

One JSON document (`board.json`) per board revision. The Python readers write it on the server; the JS renderers read it in the browser. It is the one board model between them:

```
fab package / .kicad_pcb / ODB++ / IPC-2581 / STEP ──► boarddd (Python) readers ──► board.json ──► boarddd (JS) renderers
```

- **Source of truth**: the dataclasses in [`python/src/boarddd/model.py`](../python/src/boarddd/model.py).
- **Generated from them** by `python -m boarddd.model --write` (CI fails if they are stale):
  - [`schema/board.schema.json`](../schema/board.schema.json): JSON Schema 2020-12, `$id` `boarddd/board@1`
  - `src/model/schema.js`: the same object as an ES module
  - `src/model/board.d.ts`: the TypeScript interfaces
- **Validation**: `boarddd.validate.validate_board(data)` (Python) and `validateBoard(board)` from `boarddd/model` (JS). Both return the same `"/json/pointer: message"` list, checked by the shared cases in `fixtures/model/cases.json`. Neither has dependencies; the Python tests also check the schema with `jsonschema`.
- **Golden data**: [`fixtures/royalblue54L_feather/board.json`](../fixtures/royalblue54L_feather/board.json) (KiCad's demo board, built by `make_board.py` from `boarddd.io.kicad.read_kicad_pcb` and `boarddd.io.package.read_package`; see [readers.md](readers.md)) and `fixtures/model/minimal.json`.

```python
from boarddd.model import Board, Source, Component
board = Board(name="demo", source=Source(kind="gerber"), components=[Component(ref="R1", side="top", x=10, y=5, rotation=90)])
open("board.json", "w").write(board.to_json())
board = Board.from_json(open("board.json").read())
```

```js
import { validateBoard, assertBoard, footprintToBoard } from 'boarddd/model';
const board = assertBoard(await (await fetch('board.json')).json());   // throws with the first errors
```

## Conventions

| | |
|---|---|
| Units | millimetres (`units: "mm"`), angles in degrees |
| Board frame (`frame: "board"`) | x right, **y up**, seen from the top. Origin = the source's file origin: what Gerber, Excellon, ODB++ and IPC-2581 store. KiCad readers negate y: board = (kicad_x, −kicad_y), which is also how kicad-cli's Gerbers, drills and pos file are written. `origin.aux` / `origin.grid` record the source's other origins (board frame) so writers can go back. |
| Outline | `board`: the outer loop, counter-clockwise. `cutouts`: clockwise loops. Arcs are flattened and loops are implicitly closed. `approximate: true` when the edge is a fallback (bounding box). |
| Footprints | KiCad footprint semantics: footprint frame, mm, **y down**, library (top-side) orientation, exactly as a `.kicad_mod` holds them. Pad `at[2]` is relative to the footprint. Pads are boarddd's JS `Pad` (`src/geom/pads.js`, golden-tested against pcbnew) plus magpie's `paste`, `mask`, `function`, `holes`. One difference from the JS `Pad`: the copper shape offset lives in `drill.offset` (KiCad's `(drill … (offset))`), or in `offset` for a pad without a hole (castellated SMD pads); `padOffset()` reads both. |
| Placement | A footprint point `(fx, fy)` lands on the board at `p = (x, y) + R(rotation) · q`, where `q = (fx, −fy)` on the top and `q = (fx, fy)` on the bottom. R is counter-clockwise in the board frame. KiCad's flip mirrors the footprint across its own x axis, and `rotation` is the footprint orientation as KiCad stores it and its pos file prints it. `footprintToBoard(component, [fx, fy])` in `boarddd/model` does this. The golden tests place every thru-hole pad on its drill with it, bottom-side parts included. |
| Drills | Round when `x2`/`y2` are null. Otherwise a routed slot from `(x, y)` to `(x2, y2)` of width `diameter`; oval pad holes are written that way too, so the pad centre is the slot's midpoint. A `diameter` of 0 is allowed for placeholder drills (it goes in `warnings`). |
| Layers | `id` uses KiCad canonical names where they apply (`F.Cu`, `In1.Cu`, `Edge.Cuts`, `PTH`). `order` is copper 1..N top to bottom (Gerber `L<n>`); other layers come after. `files` are `source.files` paths. |
| Stackup | `layers` top to bottom. A dielectric the source splits (KiCad `addsublayer`, gbrjob `(1/2)` entries) lists `sublayers`, and its own `thickness` is their sum, `epsilon_r` the series value t / Σ tᵢ/εᵢ, `loss_tangent` the thickness-weighted mean. `dielectric` is `prepreg`/`core` when the source says; `frequency` (Hz) is where Er/Df were given. Copper (`finished_thickness`, `roughness_rq`, `conductivity`, `etch_factor`) and mask (`thickness_over_copper`) details are null unless a source has them (IPC-2581, ODB++). `impedance_controlled` is KiCad's `dielectric_constraints` / gbrjob `ImpedanceControlled`. |
| Nets, classes | `nets[]`: `name`, `net_class` (the effective class; `Default` when none is assigned), `pair` (the partner of a differential pair). `net_classes[]`: rules in mm (`track_width`, `clearance`, `diff_pair_width`, `diff_pair_gap`, `via_*`), `nets`, `patterns`, KiCad `priority` and `tuning_profile`, and an `impedance` target: `kind` single/differential, `target` Ω, optional `tolerance_pct`, `common_mode`, `structure` (`microstrip`, `stripline`, `coplanar`, `coplanar_grounded`), per-layer `layers` (width, gap, reference planes) and `source` (`tuning_profile`, `ipc2581`, `odbpp`, `name`, `user`). Copper geometry (tracks, zones) is not in `board.json`; it goes in a separate `boarddd/copper@1` document (impedance phase I5). |
| Unknown | Optional fields are `null` (or empty lists) when the source doesn't say; readers explain guesses in `warnings`. `meta` is free-form application data that boarddd never interprets. |

## Shape

```jsonc
{
  "schema": "boarddd/board@1", "name": "RoyalBlue54L-Feather", "revision": null,
  "source": { "kind": "kicad_pcb", "generator": "KiCad Pcbnew 10.0.6", "reader": "…", "created": "2025-02-12", "commit": null, "ref": null,
              "files": [{ "path": "fab/RoyalBlue54L-Feather-F_Cu.gbr", "role": "copper", "side": "top", "sha256": "…" }] },
  "units": "mm", "frame": "board", "origin": { "aux": [119.1, -116.41], "grid": [119.1, -104.98], "drill": null },
  "outline": { "board": [[x, y], …], "cutouts": [], "approximate": false },
  "stackup": { "thickness": 1.6, "copper_layers": 8, "finish": "ENIG",
               "mask_color": { "top": "Blue", "bottom": "Blue" }, "silk_color": { "top": "White", "bottom": "White" },
               "layers": [{ "name": "F.Cu", "kind": "copper", "side": "top", "thickness": 0.035, "material": null, "color": null,
                            "epsilon_r": null, "loss_tangent": null, "layer": "F.Cu", "dielectric": null, "sublayers": [], … },
                          { "name": "dielectric 1", "kind": "dielectric", "side": "inner", "thickness": 0.1, "material": "FR4",
                            "epsilon_r": 4.5, "loss_tangent": 0.02, "dielectric": "prepreg", "sublayers": [], "frequency": null, … }, …],
               "impedance_controlled": false },
  "layers": [{ "id": "F.Cu", "role": "copper", "side": "top", "order": 1, "files": ["fab/…-F_Cu.gbr"], "format": "gerber",
               "polarity": "positive", "function": "Copper,L1,Top", "plated": null }, …],
  "drills": [{ "x": 122.06, "y": -110.53, "diameter": 0.3, "plated": true, "x2": null, "y2": null, "tool": "T1",
               "function": "via", "filled": false, "layer": "PTH" }, …],
  "footprints": { "Capacitor_SMD:C_0402_1005Metric": { "name": "…", "attr": ["smd"], "pads": [ /* Pad */ ], "graphics": [ /* Graphic */ ] } },
  "components": [{ "ref": "C1", "side": "top", "x": 151.14, "y": -105.69, "rotation": -90, "value": "100nF",
                   "footprint": "Capacitor_SMD:C_0402_1005Metric", "mount": "smd", "populate": true, "in_bom": true, "in_pos": true,
                   "mpn": [{ "mpn": "…", "manufacturer": "…" }],
                   "models": [{ "path": "${KICAD8_3DMODEL_DIR}/…wrl", "offset": [0, 0, 0], "rotate": [0, 0, 0], "scale": [1, 1, 1], "hide": false }],
                   "height": null, "attributes": { "Datasheet": "…" } }, …],
  "panel": null,
  "nets": [{ "name": "/Debugger/D+", "net_class": "USB_DIFF", "pair": "/Debugger/D-" }, …],
  "net_classes": [{ "name": "USB_DIFF", "nets": ["/Debugger/D+", "/Debugger/D-"], "patterns": [], "track_width": 0.125,
                    "clearance": 0.2032, "diff_pair_width": 0.125, "diff_pair_gap": 0.2032, "via_diameter": 0.8, "via_drill": 0.4,
                    "priority": 0, "tuning_profile": null,
                    "impedance": null /* e.g. { "kind": "differential", "target": 90, "structure": "microstrip", "source": "name", … } */ }, …],
  "warnings": [], "meta": {}
}
```

Every field, its type and its description are in the schema and in `src/model/board.d.ts`.

## What today's producers carry, and where it goes

These three tools write the data boarddd has to cover:

- **kipr** `project-review.json` (`kipr/project/review.py`, `docs/CONTRACT-project.md` v1): KiCad frame, y down, mm. It is diffed: most blocks come as `base` / `head` sides.
- **gentoo** fab manifest (`fab/views.py` `_manifest`, `fab_manifest` / `fab_panel_manifest`) and 3D `step_manifest` (`fab/views_step.py`): Gerber frame, y up, mm.
- **magpie** (`origin/claud/magpie`): `pipeline/board.py` `BoardContext`, the `footprint/model.py` `magpie-footprint/1` model, and the `pcb/` readers. Footprints and placements are y up.

Each tool keeps its own app data (statuses, diffs, URLs, BOM links, fit results) next to the board model, or in `meta`.

| model field | kipr `project-review.json` | gentoo manifests | magpie |
|---|---|---|---|
| `source.commit` / `source.ref` | `head.sha` / `head.ref` (`base` for the other side) | `package.id`, `package.filename` | `BoardContext.key`; `Board.revision` |
| `source.created` | — | `package.created_at` | — |
| `source.kind`, `source.files[]` (`role`, `side`) | `pcb.layers[].base/head.gerber`, `pcb.gbrjob`, `pcb.pos`, `pcba3d.glb/step` paths | `files[]` (`relpath`, `kind`, `function`, `side`, `copper_layer`) | `PackageFile` (`relpath`, `kind`, `side`); `Board.source` (`kicad_pcb`/`ipc2581`/`odbpp`); `classify.Classification` |
| `revision` | `info.head.rev` (title block) | — | `Board.revision` |
| `units`, `frame` | mm, KiCad frame y down (negate y) | `units: 'mm'`, Gerber frame y up | mm; footprints y up, CCW |
| `origin.aux` | — (`gerber_origin_mm` is always `[0, 0]`) | — | `Board.aux_origin`; corpus truth `aux origin` |
| `outline.board` / `cutouts` | — (only the box `board.origin_mm` + `size_mm`) | `outline.board` / `outline.cutouts` | `outline.contours()` / `pick_board()` / `cutouts()` (not on `Board`) |
| `outline.approximate` | box only → `approximate: true` | `frame{min_x…}` fallback → `approximate: true` | `extents()` fallback |
| `stackup.thickness` | `board.thickness_mm` | `board.thickness_mm` | — |
| `stackup.copper_layers` | `board.copper_layers` | `FabOption` (not in the manifest) | `Classification.copper_layer` (max) |
| `stackup.finish` | `board.finish` | `board.surface_finish` (`enig`, `hasl_lf`) | — |
| `stackup.mask_color` / `silk_color` | `board.mask_color` / `silk_color` (F side, lower-case names) | `board.mask_color` / `silkscreen_hex` (`#RRGGBB`, `none`) | — |
| `stackup.layers[]` | — (kipr reads the stackup but writes only the summary) | — | — |
| `layers[]` (`id`, `role`, `side`, `order`, `files`) | `pcb.layers[]` (`id`, `kind`, `side`, `base/head.gerber`), stack order implicit | `files[]` with `drawable`, `kind`, `side`, `copper_layer` | `Classification` (`kind`, `side`, `copper_layer`) |
| `layers[].function` / `polarity` | — | `files[].function` (raw `%TF.FileFunction`) | `Classification.function` |
| `drills[]` (`x`, `y`, `diameter`, `plated`, `x2`, `y2`, `filled`) | — (only the `.drl` paths) | `drills[]` (`x`, `y`, `d`, `plated`, `x2`, `y2`, `filled`) | `excellon.Hole` (`x`, `y`, `diameter`, `plated`, `x2`, `y2`) |
| `drills[].tool` / `function` | — | — (parsed, dropped) | `Hole.function` (`ViaDrill`/`ComponentDrill`/`MechanicalDrill`) |
| `footprints{}.pads[]` | — | — | `footprint.model.Pad` (`kind`→`type`, `shape`, `size`, `center`→`at`, `rotation`→`at[2]`, `roundrect_ratio`, `chamfer_ratio`, `chamfered`→`chamfer`, `delta`→`rect_delta`, `polygon`→`primitives`, `drill`, `holes`, `paste`, `mask`, `layers`, `function`); `pads.Pad` is a bbox only |
| `footprints{}.graphics[]` | — | — | `Footprint.courtyard` / `silk` / `body` (`Graphic`: `layer`, `kind`, `points`, `width`, `filled`) |
| `components[].ref`, `value`, `footprint` | `pcba3d.components[].ref`, `value`, `footprint` | `placements[].ref`, `val`, `footprint` | `Placement.ref/value/footprint`; `Component.reference`, `package_name` |
| `components[].x`, `y`, `rotation`, `side` | `x`, `y` (negate y), `rot`, `side` | `x`, `y`, `rot`, `side` (as the pos file) | `Placement.x/y/rotation/side` (pnp frame) |
| `components[].populate` | `dnp` (negated) | `dnp` (negated) | `Component.populate`; `BomLine.populate` |
| `components[].in_bom` | `bom.rows[].in_bom` | `bom_item_id` / `optional` (app data) | — |
| `components[].mount` | — | — | `Placement.mount`, `Component.mount` |
| `components[].mpn[]` | `bom.rows[].mpn` | through the BOM part (app data) | `Component.mpns`; `BomLine.manufacturer/mpn` |
| `components[].models[]` | `models[]` (`path`, `offset`, `rotate`, `scale`, `hide`); `model` | `model_file` + nudges `dx…rz` (app data: overrides of the source model) | `Footprint.models` (paths) |
| `components[].height` | `bbox_mm` (z extent) | `height_mm` / `height_override_mm` | `Component.height`, `Footprint.height` |
| `components[].attributes` | `bom.rows[].fields` | — | `Component.properties`, `Footprint.fields` |
| `panel.instances[]` | — | `panel.instances` (`_panel_block`) | — |
| `warnings` | `errors[]` (per project) | `fit.reason`, panel `reason` | `Board.warnings` |

The gaps show what the readers add over today's outputs:

- an outline polygon (kipr has a box; magpie's `Board` has none);
- parsed drills (kipr) with tools (gentoo);
- a stackup (only kipr has thickness and colour names; nobody keeps the physical layers);
- pads (only magpie);
- Er/Df, prepreg/core and sublayers (kipr drops them), nets, net classes and impedance targets (nobody).

App-only data has no field in the model: diff `status` / `semantic_changes`, gentoo's catalog / BOM links / model nudges / STEP fit, magpie's pin-1 marks and footprint identity. It stays in the apps or goes into `meta`.

## Changing the model

- Edit `model.py`, run `python -m boarddd.model --write`, then update the golden data and this page.
- Adding an optional field is compatible; renaming or retyping one is not, and needs `boarddd/board@2`.
- New schema keywords must be implemented in both validators (`python/src/boarddd/validate.py`, `src/model/index.js`), with a case in `fixtures/model/cases.json`.
