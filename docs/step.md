# The `[step]` extra: boarddd's OpenCascade STEP engine

```bash
pip install "boarddd[step] @ git+https://github.com/CoolNamesAllTaken/boarddd@vX.Y.Z#subdirectory=python"
```

`boarddd.step` has two halves:

| | modules | needs |
|---|---|---|
| text | `text` (assembly tree, refdes, placements, extents), `slim` (a STEP without the board dressing), `modelfile` (sniffing), `registration` (STEP frame ⇄ placements) | stdlib |
| engine | `occ`, `index`, `split`, `measure`, `fingerprint`, `work`, `cache`, `hlr`, `cli` | the `[step]` extra: cadquery-ocp, numpy, shapely |

Importing an engine module without the extra raises an `ImportError` that names it:
`boarddd.step.split needs the [step] extra (OCP, numpy, shapely not installed): pip install 'boarddd[step]'`.
The text half never needs it.

## What it does

```python
from boarddd.step.split import split

assembly = split("board.step", pos="board-pos.csv")   # pos: any pick-and-place file boarddd.io.pos reads
assembly.board.thickness, assembly.seat_z               # 1.51, 0.085 for a KiCad 1.6 mm board
for ref, c in assembly.components.items():
    c.side, c.transform, c.offset                       # component frame -> board, the model's own offset
    c.measurements.size, c.measurements.own_height, c.measurements.pick   # exact B-rep numbers
    c.fingerprint.frame_key                             # which model version this is
    c.step_bytes(), c.glb_bytes()                       # the model alone, component frame (GLB: metres, Y up)
```

```bash
python -m boarddd.step.cli split board.step --pos pos.csv --out parts/    # per-ref .step/.glb + measurements.json
```

- **`split`** reads a board STEP (KiCad, Altium and other exporters' trees) into its board body and components.
  A board is read from its text when it can be (`index`: a numpy byte-offset index, each distinct product cut
  out into a STEP of its own), so the tracks and zones of a big export are never handed to OpenCascade and a
  model seen on an earlier board comes from the cache. With a pick-and-place file, each component frame is
  its footprint anchor and rotation and the model's own offset is recovered; the fit is
  `boarddd.step.registration` (the same fit the 3D view's alignment uses), with a 0.01 mm outlier floor
  because a STEP and a pos file from one export agree to the micron.
- **`measure`**: bounding box, height above the board and above the seat, volume, area, centroid, inertia,
  face types and the pick surface (the largest planar top face, with the largest circle and rectangle a
  nozzle can use), from the exact B-rep.
- **`fingerprint`**: `shape_key` (what the solid is), `frame_key` (where it sits in its footprint),
  `brep_key` (every face) and `color_key`; `same(a, b)` compares with tolerances.
- **`hlr`**: four-view SVG line drawings and overlay diffs of two models (OCCT hidden-line removal); CSS
  variables `--boarddd-step-*` theme them.
- **`work`**: models are worked out in a process pool (`BOARDDD_STEP_WORKERS`, default 3). Applications add
  per-model work with `work.register_hook(name, function)`: the result is in `model.extras[name]` and in the
  cache (magpie's footprint check registers its contacts this way).
- **`cache`**: a content-addressed, write-once model cache (`BOARDDD_STEP_CACHE`, or `cache=`), safe for
  concurrent writers.

Settings (environment): `BOARDDD_STEP_WORKERS`, `BOARDDD_STEP_CACHE`, `BOARDDD_STEP_MAX_MB` (2048),
`BOARDDD_STEP_MAX_ENTITIES` (40 million), `BOARDDD_STEP_MAX_OCCT_MB` (400), `BOARDDD_GL_LIBDIR`. magpie's
`MAGPIE_*` names are still read, and the cache keeps magpie's entry layout and version, so a magpie cache stays
valid.

## libGL

The cadquery-ocp wheel links `libGL.so.1` although nothing is drawn. Install it (`apt-get install -y
--no-install-recommends libgl1`, as CI does), or without root run `python -m boarddd.step.occ fetch-gl`: it
unpacks Debian's three libglvnd libraries (about 170 KB) into `<venv>/lib/boarddd-gl`, and `occ.load()`
preloads them. `python -m boarddd.step.occ check` says whether OCP loads.

## Licences

boarddd is MIT. **OpenCascade (OCCT) is LGPL-2.1** (with the OCCT exception) and reaches boarddd only as the
`cadquery-ocp` wheel that `pip install boarddd[step]` fetches from PyPI: it is never vendored, copied into
`python/src`, or statically linked, and the default install does not include it. Applications that ship it
(a Docker image) take on the LGPL's terms for that wheel, as they would without boarddd: keep it replaceable
and point to its source. numpy and shapely are BSD. The browser path uses occt-import-js (LGPL-2.1, also
external, see the README).

kipr keeps cascadio for its GLB export for now (plan decision D8); magpie keeps its footprint check, polarity
and model recommendation and will use this engine (phase G2).

## Where it came from

Copied from magpie (internal `claud/magpie` `3a0374d3`, `infrastructure/libraries/magpie/src/magpie/step/`):
`occ`, `split`, `work`, `cache`, `measure`, `fingerprint`, `cli` (its `split` command), `steptext` → `index`,
`render` → `hlr`. Changes: magpie's own pos-file reader and fit (`split_pos`) are replaced by `boarddd.io.pos`
and `boarddd.step.registration` (one fit, with an `outlier_floor_mm` option); the footprint-check contacts
became a hook; settings renamed. Each module's docstring names its source.

## Tests

`python/tests/step/test_step_*.py` skip without the extra; CI's `step` job installs it (pip cache) and sets
`BOARDDD_REQUIRE_STEP=1`, so a missing extra fails instead of skipping. Fixtures (all public):

- `python/tests/step/fixtures_occ/tiny.step` + `tiny-pos.csv`: magpie's synthetic board of KiCad stock
  footprints and models (`make_board.py`): repeated instances, a bottom-side part, a QFN with two
  exposed-pad versions, a model offset, a domed LED;
- `fixtures_occ/library/`: three KiCad stock models (R_0402, C_0603, L_0603: identical solids, different
  colours);
- `fixtures/generated/step/royalblue54L_feather.step.gz`: KiCad's royalblue54L_feather demo exported with
  its stock models (`fixtures/generated/step/make.sh`): 47 components, 17 distinct models, checked against
  the board model (`board.json`: every component's side, position and rotation) and against boarddd's
  browser path on the same file (`js_step_dump.mjs`: occt-import-js + `src/models` matching finds the same
  47 designators, the same board surfaces, and component boxes within 0.05 mm);
- `fixtures/generated/step/royalblue54L_feather-excerpt.step`: the same export with the shells emptied, for
  the geometry-free path.
