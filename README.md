# boarddd

2D and 3D PCB rendering for the browser, shared by [kipr](https://github.com/CoolNamesAllTaken/kipr) (KiCad
PR review) and gentoo (a PCB fab shop site). Framework-free ES modules, no build step, `.d.ts` typings.

- **`boarddd/gerber`**: the 2D Gerber/Excellon renderer (WebGL2 + wasm): realistic board faces, layer
  and drill diffs, layer roles, board outlines, view math, contour tracing.
- **`boarddd/geom`**: pure geometry, no three.js: coordinate frames, outlines and winding, round
  holes and stadium slots, hole budget, KiCad pad shapes (shape offset, rotation, roundrect, ...).
- **`boarddd/board`**: the board solid from an outline and drills (plated barrels, caps, UVs), face
  textures from Gerbers via `boarddd/gerber`, copper-diff textures.
- **`boarddd/footprint`**: one KiCad footprint on a small board: pads, copper, silk, courtyard.
- **`boarddd/models`**: GLB and STEP loading (occt in a Worker), units/up-axis detection, colour
  space, matching meshes to reference designators.
- **`boarddd/scene`**: `createViewer`: renderer, camera, controls, KiCad-like lighting, render on
  demand, view cube, view presets, capture.
- **`boarddd/model`**: the normalised board model `boarddd/board@1` (`board.json`): typings,
  `validateBoard`, `footprintToBoard`. The schema is `schema/board.schema.json`; see
  [docs/model.md](docs/model.md).

The Python package in [`python/`](python/README.md) (same repository, same version tag) holds the
server-side readers and owns the board model. Readers are server-side (Python), renderers are
browser-side (JS); see [CONTRIBUTING.md](CONTRIBUTING.md).

Status: phase 1.

## API

### `boarddd/gerber`

```js
import { createGerberRenderer, groupBoardLayers, renderFaceRaster, renderLayerDiff, parseExcellon } from 'boarddd/gerber';
const renderer = await createGerberRenderer(canvas);   // loads the committed wasm next to the core
```

| | |
|---|---|
| `createGerberRenderer`, `GerberRenderer`, `renderGerberToCanvas`, `renderGerberToPng`, `calculateFitView`, `projectToCanvas`, `unprojectFromCanvas`, `viewExtent` | the upstream core ([wasm-gerber-viewer](https://github.com/dsafdsaf132/wasm-gerber-viewer), vendored) |
| `board.js`: `renderBoard`, `renderFaceRaster`, `faceRasterSize`, `addBoardLayers`, `selectFace` | a realistic face: laminate, copper, mask, finish, silk, see-through holes |
| `diff.js`: `renderLayerDiff`, `analyzeLayerDiff`, `analyzeBoardDiff`, `measureLayers`, `geometryText`, ... | removed red, added green, unchanged dim, in one shared frame |
| `drills.js`: `parseExcellon`, `diffHoles`, `holesToGerber`, `holeMask`, `cutHoles`, `dropEmptyTools`, `withoutEmptyTools`, ... | holes as data; KiCad's zero-diameter tools dropped before the wasm sees them |
| `layers.js`: `layerRole`, `groupBoardLayers`, `withoutProfile`, `hasGeometry`, `plotsProfile` | which layer a file is; outline strokes plotted on other layers |
| `outline.js`: `boardOutline`, `outlineContours`, `gerberExtents`, `ringsToGerber` | the board shape as polygons (mm) |
| `palette.js`, `view.js`, `contour.js`, `raster.js` | board colours; world ⇄ pixel math and shared frames; raster → outlines; pixel readback |
| `boarddd/gerber/contour-worker.js` | `traceLayer` in a module worker |

The upstream core (`index.js`, `shared.js`) and the Rust crate are vendored from our fork
(CoolNamesAllTaken/wasm-gerber-viewer) into `third_party/wasm-gerber-renderer/` by `scripts/sync-fork.sh`,
MIT © dsafdsaf132 (LICENSE there). The other modules in `src/gerber/` moved here from the fork at `92976b5`
and are boarddd's own. The wasm is built by `scripts/build-wasm.sh` (Rust from the crate's
`rust-toolchain.toml`, wasm-pack 0.14.0) and committed in `third_party/wasm-gerber-renderer/core/wasm/`
with `BUILD.json`; CI checks that its source hash matches the crate and that the crate builds. Consumers
need no Rust. To update: `bash scripts/sync-fork.sh [fork checkout] [ref]`, `bash scripts/build-wasm.sh`,
commit `third_party/` (or run the CI workflow by hand with `commit` on a branch).

### `boarddd/geom` (pure, no three.js)

| | |
|---|---|
| `kicadToBoard(x, y)`, `boardToKicad(x, y)` | KiCad mm (y down) ⇄ board frame (y up) |
| `kicadModelMatrix({offset, rotate, scale})` | a footprint `(model ...)` placement as KiCad's 3D viewer does it (column-major 4×4) |
| `slotPoints(x1, y1, x2, y2, r)`, `ringPoints(cx, cy, r)`, `loopAt(ends, r)` | hole outlines: stadium slots (never ellipses), circles |
| `usableHoles(holes, outline, budget = 400)` | which drills can be punched (clear of the edge and cutouts, not filled; largest first past the budget) |
| `padOutline(pad)`, `padCopperLoops(pad)`, `padDrillSlot(pad)`, `padDrillLoop(pad)`, `padToKicad(pad, p)`, `padCopperSides(pad)`, `padHasCopper(pad)` | KiCad pad semantics: shape offset moves the copper not the hole, rotation, roundrect, chamfers, trapezoid, custom primitives, oval drills along their long axis. Checked against pcbnew. |
| `clearance`, `loopBounds`, `counterClockwise`, `strokeLoops`, `rectOutline`, `outlinesDiffer`, ... | loops |

### `boarddd/board`

```js
import { buildBoard, buildGerberBoard, readFabFiles, paintFaces, paintCopperDiff } from 'boarddd/board';

// a bare board from an outline and drills (board mm, y up): body (top/bottom/walls) + plated barrels
const board = buildBoard({ outline: { board: [[0, 0], [50, 0], [50, 30], [0, 30]] },
                           holes: [{ x: 10, y: 10, diameter: 1, plated: true }, { x: 20, y: 10, x2: 23, y2: 10, diameter: 1 }] });
scene.add(board.group);

// a board painted from its Gerbers + Excellon files. gerber = null: boarddd/gerber (or inject another
// implementation); renderer = null: one shared renderer (defaultRenderer()), or pass your own GerberRenderer
const head = await buildGerberBoard(null, null, files /* [{name, text}] */, { thickness: 1.6 });

// copper diff against another revision, in the same frame (same UVs): swap it onto the faces
const diff = await paintCopperDiff(null, null, { base: readFabFiles(null, baseFiles), head: head.fab }, head.painted);
head.setFaces({ top: diff.top, bottom: diff.bottom });
```

The solid spans z = 0 (bottom face) to z = thickness (top face). Meshes carry `userData.group`
(`board`, `barrels`) for visibility toggles. `dispose()` frees what boarddd created.

### `boarddd/footprint`

```js
import { parseKicadFootprint, buildFootprint } from 'boarddd/footprint';
const fp = parseKicadFootprint(await (await fetch('USB_C_Receptacle.kicad_mod')).text());
const built = buildFootprint(fp, { thickness: 1.6, margin: 1 });   // board = courtyard + 1 mm (or Edge.Cuts)
scene.add(built.group);                     // userData.group: board, copper, barrels, silk, fab, courtyard
const m = new THREE.Matrix4().fromArray(built.modelMatrix(fp.models[0]));   // where its STEP goes
```

`faces: {top, bottom}` paints the board faces with pictures over `uvBounds` (e.g. kipr's per-layer
renders composited), and `decals: {silk, fab, courtyard}` (each `{top, bottom}` pictures over the same
bounds) adds them as transparent sheets in those groups: the way to show text, which the graphics
sheets leave out.

`parseKicadFootprint` reads pads, graphics (flattened to polylines) and models from a `.kicad_mod`
(KiCad 6 to 10, and the old `module` form). Pad objects are also kipr's `geom.json` pads.
Text is not drawn.

### `boarddd/models`

```js
import { loadGLB, loadSTEP, prepareModel } from 'boarddd/models';

// a kicad-cli GLB: oriented (z up, mm), board bodies told apart, components named
const s = prepareModel(await loadGLB('board.glb'), components /* [{ref, x, y, side}], KiCad mm (y down) */,
                       { boardSize: [w, h], boardOrigin: [x0, y0] /* optional */ });
viewer.add(s.root);
s.comps.get('U1');            // {objects, meshes, box, bottom}
s.parts.substrate;            // also mask, copper, silk: hide them under a Gerber-built board
s.report;                     // {method: 'name'|'position'|'mixed', matched, ambiguous, unmatched, up, scale, ...}

// STEP through occt-import-js (LGPL-2.1, not bundled: pass its URLs; it runs in a Worker)
const part = await loadSTEP('part.step', { occt: { js: '.../occt-import-js.js', wasm: '.../occt-import-js.wasm' } });
```

- Matching: names first (`R5`, `R5_1`, `R5 (2)`; never `R11` → `R1`), then positions: the export origin
  is fitted (from the named nodes, else a Hough vote), and a node goes to a part only if it is clearly
  nearer that part than any other node and than any other part; mutual-nearest pairs settle the rest.
  A part with `assembly: true` and a `box` claims the solids inside it (a module).
- Placements already in the model's own frame (y up, e.g. measured by a server from the same STEP):
  `mapNodesToRefs(nodes, comps, { frame: 'board', offset: {x: 0, y: 0}, byName: false, joinExtras: false })`
  skips the flip, the origin fit, name matching and extra-piece joining; module `box` may be 3D
  `[x0, y0, z0, x1, y1, z1]` (nodes carry `cz`).
- Units and up axis are measured (thinnest axis is up; the size that fits `boardSize`, else metres
  below 2 units) unless `units` / `up` are given. The substrate's bottom is seated on z = 0.
- STEP colours are used the way KiCad's viewer uses them (occt returns linear RGB; boarddd re-encodes
  to the file's values), with a polygon offset against coplanar footprint copper.
- Meshes carry `userData.group` (`model`, `board`, `mask`, `copper`, `silk`) and `userData.ref`.
- `readStep(src, { fallback: false })` rejects (`err.workerCrashed`) instead of retrying on the main thread
  when the Worker dies; `stepToObject(data, { center: false })` keeps occt's absolute vertices with each
  group at the origin. STEP materials are shared per colour: clone before changing one mesh's.
- Classic-script bundles: `import.meta.url` is gone, so pass `workerUrl` (e.g. a blob URL of
  `src/models/step_worker.js`) or `occtFactory` to run occt on the main thread.
- Pure helpers (`mapNodesToRefs`, `matchByPosition`, `splitBoardBodies`, `measureBoard`, `detectUp`,
  `detectScale`, `stepColorToLinear`, ...) run under node.

### `boarddd/scene`

```js
import { createViewer } from 'boarddd/scene';
const v = createViewer(el, { controls: 'trackball' /* or 'orbit' */, theme: 'light', onPick: (hit) => {} });
v.add(board.group, s.root);   // board frame, z up
v.setView('top');             // bottom, front (side), back, left, right, iso, isoBottom; fits the content
v.fit();                      // refit from the current direction
v.setTheme('dark');
const png = v.capture({ width: 1200, height: 800, transparent: true });
v.setPanes([[baseGroup], [headGroup]]);   // side by side with ONE camera (null: one view again)
v.requestRender();            // after changing objects yourself
v.dispose();                  // frees GL (content included) and removes the canvas
```

- Renders on demand: no animation loop; frames are drawn while the controls move and then stop.
- KiCad-like look: neutral tone mapping, a generated room environment (no network), ambient + key +
  camera headlight, light/dark gradient backgrounds. Near/far are fitted to the content every frame.
- Panes: each listed object shows only in its own pane; the rest shows in all. `fit()` uses a pane's
  aspect and `pick()` reports the pane under the point and only hits what that pane shows.
- The view cube (top right) shows the orientation; click a face to look from it (`on('cube', face => ...)`
  hears which); `cubeAt(clientX, clientY)` says whether a point is on the cube (for hosts with their own
  pointer handling). Bottom is mirrored
  left-right, like KiCad. Importmaps must map `three/addons/` too.

## Peers

`three` is a peer dependency, imported by the bare specifier `"three"`: map it with an importmap,
or let your bundler resolve it. occt-import-js (for STEP) is passed in by the caller, never imported by
boarddd itself. `boarddd/gerber` loads its wasm from `third_party/wasm-gerber-renderer/core/wasm/`
relative to the module; a bundle that moves the files passes `wasmModuleUrl` (or `wasmModule` +
`wasmInitInput`) to `createGerberRenderer`. Serve `third_party/` alongside `src/`.

```html
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@0.185.0/build/three.module.js",
               "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.185.0/examples/jsm/",
               "boarddd/": "https://cdn.jsdelivr.net/gh/CoolNamesAllTaken/boarddd@main/src/" } }
</script>
```

## Frames

All geometry is in the **board frame**: millimetres, x right, y up (KiCad's y negated; Gerber
coordinates), z up out of the top copper, board bottom face at z = 0 and top face at z = thickness.
`kicadToBoard(x, y)` and `boardToKicad(x, y)` convert points; the viewer uses camera.up = +z.

## Tests

- `npm test`: node tests: `boarddd/gerber` (layer roles, outlines, drills and zero-diameter tools, view
  math, frame backgrounds; ported from the fork), geometry (stadium slots, hole budget, every KiCad pad shape against pcbnew's
  own polygons, kipr's pad-placement golden data), the board solid, the footprint reader/builder.
- `npm run test:browser`: headless Chromium (SwiftShader WebGL2): slotted holes must show through as
  stadiums in a straight-down render of a footprint and of a Gerber-built board; copper diff colours;
  a blue STEP board reads blue from top and bottom; no frames while idle; view cube clicks; dispose;
  `boarddd/gerber` pixel tests (`gerber-board-diff`, `gerber-drill-empty-tools`, ported from the fork).
  `PW_PORT` changes the server port (several checkouts at once).
- `boarddd/model`: `validateBoard` against the shared cases in `fixtures/model/` (pytest runs the same
  file), and the royalblue54L_feather golden `board.json` against boarddd's own `.kicad_mod` parser and
  the drill files.
- Python: `cd python && pip install -e ".[dev]" && pytest` (model, validator, golden board), `ruff`,
  `python -m boarddd.model --check` (generated schema/typings up to date).
- `npm run typecheck`: the `.d.ts` files, plus `test/types/` (type-level use of `boarddd/gerber`).
- Fixtures: `fixtures/` is shared golden data for node, Playwright and pytest (licences in
  `fixtures/LICENSES.md`); `test/fixtures/` holds JS-only inputs (see the READMEs there for sources); `examples/data/` is KiCad demo data
  (KiCad's `demos/royalblue54L_feather`), exported with `scripts/export-demo.sh`.

## Development

```sh
npm install
npm test                      # node --test: test/**/*.test.mjs
npm run test:browser          # playwright, headless Chromium (WebGL2 via SwiftShader)
npm run serve                 # examples at http://127.0.0.1:8417/examples/
npm run check:wasm            # the committed wasm matches third_party/wasm-gerber-renderer/crate
npm run build:wasm            # rebuild it (rustup + wasm-pack, downloaded if missing)
```

## Credits

Ported from gentoo's `viewer3d.js` (PantsForBirds), kipr's `web/project/pcba3d` and
`web/library/js` (CoolNamesAllTaken/kipr). Each module names its sources. `boarddd/gerber` is built
on [wasm-gerber-viewer](https://github.com/dsafdsaf132/wasm-gerber-viewer) by dsafdsaf132 (MIT).
