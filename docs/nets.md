# Nets by group (`boarddd/nets`, `boarddd.nets`)

Choose controlled-impedance nets as groups, from any EDA tool. Same code in JS (`src/nets/`) and Python
(`python/src/boarddd/nets.py`); `fixtures/nets/cases.json` keeps them in step.

## Patterns

```js
import { matchNets } from 'boarddd/nets';
matchNets(names, ['USB_D*', '*_{P,N}']);   // { nets, pairs: [[p, n], ...], patterns: [{ text, nets, pairs, error }] }
```

| pattern | matches |
|---|---|
| `USB_D*` | `*` any run, `?` one character, `[abc]` / `[!abc]` one of / none of |
| `RF_{A,B}` | alternatives |
| `*_{P,N}` or `*_P/*_N` | pairs: halves whose wildcards caught the same text; the first half is + / P |
| `re:^CLK\d+$` | a regular expression, full match (keep to what JS and Python share) |

- Case-insensitive; `caseSensitive: true` matches as KiCad's netclass patterns do.
- A pattern without `/` also matches the last part of a hierarchical name: `USB_D*` matches `/usb/USB_D+`.
- Other matches pair by board@1 `Net.pair`, else by name (`+`/`-`, `_P`/`_N`, `P`/`N`): `pairsAmong`.
- A bad pattern matches nothing and says why (`error`).

## Tracks like a picked one

```js
import { similarTracks } from 'boarddd/nets';
similarTracks(copper, { layer: 'F.Cu', nets: ['/ETH.D0_P', '/ETH.D0_N'] }, { board });
// { kind: 'differential', width: 0.13, gap: 0.25, pairs: [{ nets, gap, length }], nets: [...] }
```

- One net: every net with tracks of the same width on the layer (±max(1 µm, 0.5 %)), longest first. Solid-plane
  nets are flagged `plane` (left out by default in the UI).
- A pair: the pairs at that width whose gap matches (±max(2 µm, 2 %)). The gap is edge to edge where the two
  run parallel (within 10°), the value most of the coupled length has. Pairs come from `Net.pair` or the naming
  rule, else from the copper (an unpaired net's parallel neighbour), so Gerber X2 uploads work too.
- Works on any copper@1: KiCad, Gerber X2 (`%TO.N`), Eagle (`boarddd.io.eagle.read_eagle_copper`).

UI: `netGroup` in `boarddd/impedance/ui` and the impedance panel's group box ([impedance.md](impedance.md#ui-boardddimpedanceui)).

## From the CAD files

Each reader gives board@1 `nets` (with `pair`) and `net_classes` (with `impedance` when the file names a target).

| file | reader | classes | targets |
|---|---|---|---|
| KiCad `.kicad_pro` | `io.kicad.project` | `net_settings.classes`, `netclass_patterns`, assignments | tuning profiles, class names (`DP_90`, `50ohm`) |
| Eagle `.brd` (XML) | `io.eagle.read_eagle_brd` | `classes` (width, clearance, drill) | class names |
| IPC-2581 rev B/C | `io.ipc2581.read_net_classes` | one per impedance `Spec` referenced by `LogicalNet` or `Set` (`SpecRef`) | `Impedance`: value, tolerance, transmission type (single, edge / broadside coupled, coplanar), width, spacing or pitch, ground spacing, reference planes; `netPair` |
| ODB++ | `io.odbpp.read_net_classes` | `.electrical_class` (else `.net_type`); one per `impedance.xml` Descriptor | class names, `.dpair_gap`, `.diff_pair`; Descriptor: target, tolerance, width, spacing, reference planes, nets from the lines carrying `.imp_constraint_id` |
| impedance file | `io.impedance_file` | one per class row | below |

`read_ipc2581` and `read_odbpp` include them. Eagle's binary (pre-6) boards are refused.

## Impedance file

A CSV, JSON or simple spreadsheet next to the board files (`impedance.csv`, `board-impedance.json`,
`impedance_control.xlsx`): one row per class, or per class and layer.

```csv
class,target,tolerance,kind,nets,layer,width,gap
USB,90,10,diff,"USB_D{P,N}",F.Cu,0.2,0.15
RF,50R,5 ohm,single,RF_* ANT_*,,,
```

```json
{ "classes": [ { "class": "PCIE", "target": 85, "tolerance": 10, "kind": "differential",
                 "nets": ["*PCIE*_{P,N}"], "layer": "In2.Cu", "width": 0.11, "gap": 0.13 } ] }
```

| column | also | value |
|---|---|---|
| `target` (required) | ohms, impedance, z0, zdiff | `90`, `90R`, `90 ohm` |
| `class` | name, net class, group | the label |
| `tolerance` | tol, ± | `10`, `±10 %` (percent), `5 ohm` |
| `kind` | type | single / se, diff / differential / dp / pair; blank: from the class name, or differential when all its nets pair up |
| `nets` | patterns, signals | patterns, separated by spaces, `;` or `\|`; quote a cell with a comma |
| `layer` | | `F.Cu`, `top`, `L3`, `In2.Cu` |
| `structure` | | microstrip, stripline, CPW, CPWG |
| `width`, `gap`, `coplanar_gap`, `reference` | spacing, ground gap, ref | mm, or with a unit (`4 mil`); a header unit applies (`Line width (mil)`) |

Columns are found by name in any order; rows above the header (a title) are skipped. A fab sheet without nets
gives targets by layer.

## Tests

`test/nets/nets.test.mjs`, `python/tests/test_nets.py` (the shared cases, KiCad's own class assignment on CM5
MINIMA, the 100 Ω class found from one pair), `python/tests/io/test_net_classes.py` (every reader on
`fixtures/cad/`, made by `make_cad.py`).
