# Copper with nets (`boarddd/copper@1`)

`copper.json` is the routed copper of one board revision: tracks (segments and arcs), vias, zone fills,
keepouts, pads as copper, net ties and a per-layer plane summary, every item on its net. It sits next to
`board.json` ([model.md](model.md)) rather than inside it: a pour is thousands of points (royalblue54L_feather's
copper is 2.4 MB against 856 KB for its board), and only some consumers need it (impedance along a route, net
highlighting, DRC-like checks). It is what impedance phase I6 (route → cross-sections) reads.

| | |
|---|---|
| Python | `boarddd.copper` (dataclasses, polygon helpers), `boarddd.io.kicad.read_kicad_copper`, `boarddd.io.gerber_copper.read_gerber_copper`, `boarddd.validate.validate_copper` |
| JS | `boarddd/copper`: `validateCopper`, `assertCopper`, `copperFromGerbers` (Gerber-only uploads in the browser), the polygon helpers; typings in `src/copper/copper.d.ts` |
| schema | `schema/copper.schema.json` (JSON Schema 2020-12), generated with board@1's by `python -m boarddd.model --write` |

```python
from boarddd.io.kicad import read_kicad_copper
from boarddd.io.gerber_copper import read_gerber_copper

copper = read_kicad_copper("board.kicad_pcb")      # or read_gerber_copper("fab/")  (a folder or .zip)
open("copper.json", "w").write(copper.to_json())   # one list item per line
```

```js
import { copperFromGerbers, validateCopper } from 'boarddd/copper';
const copper = copperFromGerbers(files);           // [{ name, text }]: the copper Gerbers (+ drills, + Edge.Cuts)
```

## Conventions

board@1's: millimetres, degrees, the **board frame** (x right, y up, seen from the top, origin = the source's
file origin). Layer ids are board@1 `Layer.id`s (`F.Cu`, `In1.Cu` … `B.Cu`) and `layers` lists the copper top to
bottom; nets are board@1 `Net.name`s, `''` for copper on no net, and `nets` lists those the copper uses. Rings are
implicitly closed; outlines run counter-clockwise, holes clockwise.

## Shape

```jsonc
{
  "schema": "boarddd/copper@1", "board": "RoyalBlue54L-Feather", "units": "mm", "frame": "board",
  "source": { "kind": "kicad_pcb", "files": [ … ], "generator": "pcbnew 9.0", "reader": "…" },
  "layers": ["F.Cu", "In1.Cu", …, "B.Cu"],
  "nets": ["", "GND", "VDD", …],
  "tracks": [ { "layer": "F.Cu", "net": "GND", "width": 0.15, "start": [136.88, -108.13], "end": [136.68, -107.93],
                "mid": null, "id": "05e0…" } ],                      // mid: arcs, the point halfway along
  "vias": [ { "at": [158.66, -105.105], "net": "GND", "diameter": 0.45, "drill": 0.00001, "span": ["F.Cu", "B.Cu"],
              "type": "through", "pad_layers": null, "padstack": null } ],   // drill: the demo's placeholder
  "zones": [ { "layer": "In1.Cu", "net": "GND", "kind": "pour", "area": 986.75,
               "fill": [ { "outline": [[…]], "holes": [[[…]], …] } ],
               "outline": [[…]], "priority": 1, "name": null, "filled": true, "stale": false, "id": "…" } ],
  "keepouts": [ { "layers": ["F.Cu", …], "outline": [[…]], "rules": { "tracks": true, "vias": true, "pours": true, … }, "ref": "U1" } ],
  "pads": [ { "ref": "R5", "number": "1", "net": "VDD", "layers": ["F.Cu"], "at": [170.21, -112.07], "rotation": 180,
              "shape": "roundrect", "size": [0.54, 0.64], "polygons": [[[…]]], "drill": null, "type": "smd" } ],
  "net_ties": [ { "ref": "JP1", "groups": [["1", "2"]], "nets": ["Net-(JP1-A)", "VDD"] } ],
  "planes": [ { "layer": "In1.Cu", "net": "GND", "area": 986.75, "coverage": 0.742, "solid": true } ],
  "warnings": [], "meta": {}
}
```

- **tracks**: a straight segment, or an arc through `mid` (KiCad's own representation). A full circle (a copper
  drawing) is two half arcs (`boarddd.copper.circle_halves`).
- **vias**: `span` is the first and last copper layer of the hole; `type` is `through` (outer to outer), `blind`,
  `buried` or `micro` (KiCad's flag). `pad_layers` lists the layers with a ring when not all of the span has one
  (KiCad "remove unused layers", Gerber); `padstack` gives per-layer ring diameters when they differ (KiCad 10
  padstacks, Gerber), `diameter` then being the largest. `drill` is 0 when unknown.
- **zones**: one entry per zone **per layer** (a multi-layer zone shares its `id`), with the fill as polygons
  with holes. `kind`: `pour` (a KiCad zone), `teardrop`, `shape` (a filled copper drawing: KiCad `gr_`/`fp_`
  polygons, rectangles, circles; Gerber `EtchedComponent` regions) or `region` (Gerber/ODB++ filled copper,
  which does not say what drew it). `filled: false` is a zone with no fill on that layer; `stale` is the fill
  check's verdict (below), `null` when not checked.
- **keepouts**: KiCad rule areas (board and footprint ones, `ref` naming the footprint) with what they forbid.
- **pads**: the copper of each pad: `at` is the centre of the copper shape (KiCad's pad position plus its shape
  offset, which is where a Gerber flash is), `polygons` the loops whose union is the copper (a custom pad: its
  anchor and primitives), `ref`/`number` the footprint pad it is. KiCad gives one entry per pad with its copper
  `layers`; Gerber one per layer. Pads without copper (paste-only, a bare NPTH hole) are left out.
- **net_ties**: KiCad's `net_tie_pad_groups`, with the nets each tie joins.
- **planes**: pour area per (layer, net), teardrops and no-net copper left out, with `coverage` = area / board
  area (outline minus cutouts; `null` without an outline) and `solid` = coverage ≥ 0.5: the reference-plane
  hint for impedance. In1.Cu GND on royalblue: 986.7 mm², 74 %.

`boarddd.validate.validate_copper` / `validateCopper` check the schema plus what it can't say: layer ids and
nets come from the document's own `layers`/`nets`, and spans run top to bottom (shared cases in
`fixtures/copper/`).

## KiCad (`read_kicad_copper`)

KiCad 5 to 10 board files, no pcbnew: `segment` and `arc` tracks, vias (blind/micro flags, KiCad 10
`padstack` modes, `remove_unused_layers`/`keep_end_layers`: a ring where a same-net track ends on the via or
a same-net fill covers it), zones on any copper layers (`*.Cu`, `F&B.Cu` expanded) with their `filled_polygon`s per
layer, teardrops (`attr (teardrop …)`), keepouts (board and footprint rule areas), pads from every footprint
(KiCad's shapes through `io.kicad.geom`, which is checked against pcbnew), net ties, and copper drawings the way
a Gerber plot has them (a filled shape is a `shape` zone, a stroke becomes tracks of its width). Copper text is
left out (a warning counts it).

**Fills are the file's.** KiCad saves the last fill; it is not recomputed. A zone that was never filled (or whose
fill was not saved) has `filled: false` and a warning. With `check_fills=True` (the default) every pour is
checked against the copper of other nets on its layer: a track end or middle, a via centre or a pad centre inside
the fill can only be a fill left from before the copper moved, so the zone gets `stale: true` and a warning
("refill the zones"). KiCad's own One-Air-Max demo has one (a GND fill over a pad on B.Cu): after
`kicad-cli pcb drc --refill-zones --save-board` the check is clean. Teardrops and copper drawings are not checked
(they are not filled around other copper).

## Gerber X2 (`io.gerber_copper`, browser `copperFromGerbers`)

| Gerber | copper@1 |
|---|---|
| `D01` draw, `Conductor` (or `EtchedComponent`) circular aperture | track, width = the aperture's diameter; `G02`/`G03` arcs keep a `mid` (`G74` single quadrant too) |
| `G36`…`G37` region, `Conductor` | zone, `kind: "region"`; the cut-ins are removed (`unfracture`) so it has holes |
| region, `EtchedComponent` | zone, `kind: "shape"` |
| `D03` flash, `ViaPad` | a via ring: rings at one point on several layers are one via over that span; the drill comes from the Excellon files at that point |
| flash or region, a pad function (`SMDPad`, `ComponentPad`, `HeatsinkPad`, …) | pad on that layer; `polygons` from the aperture (standard apertures and macros: circles, outlines, polygons, lines, thermals; KiCad's `RoundRect` as its hull) |
| `%TO.N,<net>` | the net of every following object until `%TD` (KiCad writes it only when the net changes); `N/C` (a pad on no net) is `''` |
| `%TO.P,<ref>,<pin>` | `ref`/`number`; KiCad writes it on outer layers only, so inner-layer flashes take it from the outer pad flashed at the same point on the same net |
| `NonConductor` draws (copper text), the plotted board profile | skipped, counted in a warning |
| `%SR`, `%LM/LR/LS`, `%LPC` objects | not supported: skipped with a warning |

Layer ids come from `%TF.FileFunction` (`Copper,L2,Inr` → `In1.Cu`) or KiCad/Protel file names, the same way
`io.package` names layers, so they match the package's `board.json`. A Gerber without X2 attributes reads as
tracks and regions on no net (with a warning); finding connectivity from geometry is not done here.

The browser's `copperFromGerbers` (`src/copper/gerber.js`, a small tokenizer: the wasm renderer ignores `%TO`)
follows the same rules and gives the same document as the Python reader: python/tests/io/test_gerber_copper.py
compares the two on both fixtures (every number within 2 nm, warnings equal), and they agree on the 11 other
KiCad demos below as well.

### Gerber vs KiCad

The same board read from its `.kicad_pcb` and from kicad-cli 10.0.6's Gerber X2 + Excellon export gives the same
copper (`python/tests/io/copperkit.py`): every track on the same layer, net and width with its ends within 2 µm
and arcs keeping their mid point, every via (place, net, ring, span), every pad on every layer (copper centre,
net, ref/number, area within 2 %) and the filled area per (layer, net) within 0.5 %. What differs by design:
kicad-cli recomputes arc ends from the centre (up to 0.1 µm off) and writes arcs shorter than ~3 µm as straight
draws; KiCad plots a custom pad as several flashes; Gerber has no zone outlines, priorities, keepouts or net ties.

| board (KiCad 10.0.6 demos) | copper layers | nets | tracks | vias | pads (per layer) | (layer, net) fills | filled mm² | worst fill Δ | problems |
|---|---|---|---|---|---|---|---|---|---|
| royalblue54L_feather (fixture) | 8 | 95 | 943 | 183 | 991 | 114 | 6943.9 | < 1e-6 | 0 |
| RoyalBlue54L-NFC-Antenna (fixture) | 2 | 1 | 160 (48 arcs, 3 written as lines) | 2 | 2 | 2 | 1.0 | < 1e-6 | 0 |
| CM5_MINIMA_3 | 6 | 220 | 2084 (200 arcs) | 444 | 737 | 13 | 13866.7 | < 1e-6 | 0 |
| One-Air-Max | 4 | 156 | 1663 (16 arcs) | 410 | 760 (+4 custom-pad flashes) | 35 | 15263.1 | < 1e-6 | 0 |
| StickHub | 2 | 47 | 1293 (180 arcs) | 87 | 273 | 4 | 724.8 | < 1e-6 | 0 |
| complex_hierarchy | 2 | 52 | 364 | 0 | 330 | 52 | 5355.7 | < 1e-6 | 0 |
| ecc83-pp | 2 | 13 | 59 | 0 | 66 | 1 | 1254.4 | < 1e-6 | 0 |
| interf_u | 2 | 173 | 731 | 84 | 696 | 1 | 5569.9 | < 1e-6 | 0 |
| kit-dev-coldfire-xilinx_5213 | 4 | 278 | 2935 | 253 | 1644 | 3 | 30017.9 | < 1e-6 | 0 |
| multichannel_mixer | 2 | 80 | 576 | 29 | 462 | 3 | 17773.9 | < 1e-6 | 0 |
| pic_programmer | 2 | 111 | 370 | 6 | 478 | 1 | 10618.5 | < 1e-6 | 0 |
| tinytapeout-demo | 4 | 114 | 2143 | 405 | 1614 | 5 | 22977.8 | < 1e-6 | 0 |
| video | 4 | 588 | 7932 | 808 | 4854 | 2 | 40959.0 | < 1e-6 | 0 |

The two fixtures run in CI; the other eleven were run locally with the same comparison (the demos are not in the
repository).

## Later

- **ODB++** (`io.odbpp`): line features `r<w>` and arcs in `layers/<layer>/features` are tracks, surfaces are
  regions, pads are pads; nets come from `eda/data` (`NET` records → feature ids per layer). The reader already
  parses `eda/data` for components.
- **IPC-2581** (`io.ipc2581`): `LayerFeature/Set@net` with `Line`/`Arc` + `LineDesc@lineWidth` (tracks),
  `Contour`/`Polygon` (regions), `Pad` (pads); KiCad leaves `LogicalNet` out but sets `Set@net`.
- Per-net loading and a spatial index for big boards (`planes` already lets a viewer pick references without
  reading every fill).

## Tests and fixtures

- `python/tests/test_copper.py`: the schema, round trips, the validator on `fixtures/copper/cases.json` (also
  checked against `jsonschema`), `unfracture` (holes, islands), circle halves, planes, the fill check.
- `python/tests/kicad/test_kicad_copper.py`: royalblue54L_feather against pcbnew's own pad centres, boxes, angles
  and nets (`fixtures/royalblue54L_pcbnew`), its tracks, vias, pours, teardrops, keepouts, net tie and planes; the
  NFC antenna's arcs and its golden `copper.json`; hand-written boards for KiCad 5 syntax, unfilled and stale
  fills, padstacks, unused-layer vias, buried and micro vias, copper drawings, wildcard keepouts.
  `test_kicad_demos.py` reads every KiCad demo's copper when `KICAD_DEMOS` points at them.
- `python/tests/io/test_gerber_copper.py`: Gerber = KiCad on both fixtures, Python = JS, and small Gerbers for
  what KiCad does not write (inch units, trailing zeros, `G74`, full circles, `%LPC`, `N/C`, `\u` escapes, macro
  primitives, partial via spans and per-layer rings).
- `test/copper/`: the same validator cases in JS, `copperFromGerbers` on both fixtures.
- `fixtures/royalblue54L_nfc_antenna/`: KiCad's NFC antenna demo (the royalblue demo's second board, CERN-OHL-P
  v2) and its Gerber export, for track arcs; `copper.json` is rebuilt by `make_copper.py` (`--check` in CI).
