// fixtures/nets/cases.json: boarddd/nets cases with the JS results, which python's boarddd.nets must match
// (python/tests/test_nets.py) and the JS tests re-run (test/nets/nets.test.mjs).
//
//   node fixtures/nets/make_cases.mjs           # rewrite cases.json
//   node fixtures/nets/make_cases.mjs --check   # fail if it is stale
//
// patterns: net-name sets (synthetic, and CM5 MINIMA's real names) against pattern lists.
// similar:  seeds on CM5 MINIMA's copper (fixtures/impedance/route/cm5.json.gz), on route/pair.json, and on the
//           synthetic `lanes` copper below: three unnamed pairs at two gaps plus single lines (geometric pairing),
//           drawn with arcs, as a Gerber X2 upload would name them.
import { readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { matchNets, similarTracks } from '../../src/nets/index.js';

const here = new URL('.', import.meta.url);
const cm5 = JSON.parse(gunzipSync(readFileSync(new URL('../impedance/route/cm5.json.gz', here))));
const pair = JSON.parse(readFileSync(new URL('../impedance/route/pair.json', here), 'utf8'));

/** Synthetic copper: lanes along x on F.Cu, each with a jog through an arc. */
function lanes() {
  const tracks = [];
  const line = (net, y, w) => {
    tracks.push({ layer: 'F.Cu', net, width: w, start: [0, y], end: [10, y], mid: null, id: null });
    tracks.push({ layer: 'F.Cu', net, width: w, start: [10, y], end: [12, y + 2], mid: [11.2, y + 0.6], id: null });
    tracks.push({ layer: 'F.Cu', net, width: w, start: [12, y + 2], end: [30, y + 2], mid: null, id: null });
  };
  // pairs (w 0.15): A/B gap 0.15, C/D gap 0.15, E/F gap 0.25; singles (w 0.15, 0.3); a B.Cu copy of A at 0.15
  const w = 0.15;
  line('NET_A', 0, w); line('NET_B', 0.3, w);
  line('NET_C', 3, w); line('NET_D', 3.3, w);
  line('NET_E', 6, w); line('NET_F', 6.4, w);
  line('SOLO', 10, w);
  line('WIDE', 13, 0.3);
  line('GND', 16, w);
  tracks.push({ layer: 'B.Cu', net: 'NET_A', width: w, start: [0, 0], end: [5, 0], mid: null, id: null });
  return {
    schema: 'boarddd/copper@1', board: 'lanes', source: { kind: 'gerber' }, units: 'mm', frame: 'board',
    layers: ['F.Cu', 'B.Cu'], nets: ['GND', 'NET_A', 'NET_B', 'NET_C', 'NET_D', 'NET_E', 'NET_F', 'SOLO', 'WIDE'],
    tracks, vias: [], zones: [], pads: [], planes: [{ layer: 'B.Cu', net: 'GND', area: 100, coverage: 0.9, solid: true }],
  };
}

const NAMES = ['/usb/USB_D+', '/usb/USB_D-', 'USB_DP', 'USB_DM', 'RF_A', 'rf_b', 'RF_ANT', 'CLK1', 'CLK22',
  'PCIE_TX0_P', 'PCIE_TX0_N', 'PCIE_TX1_P', 'PCIE_TX1_N', 'ETH_T', 'ETH_C', 'GND', '/a/b/HDMI_D0P', '/a/b/HDMI_D0N',
  'LVDS+', 'LVDS-', 'Net-(U1-Pad3)', 'DDR_DQ[0]', 'DDR_DQ[1]', 'A.B', 'SD_CMD'];
const PATTERNS = [
  ['USB_D*'], ['*_{P,N}'], ['*_P/*_N'], ['rf_*'], ['RF_?'], ['re:^CLK\\d$'], ['re:clk\\d+'], ['ETH_{T,C}'],
  ['[!G]ND'], ['[GH]ND'], ['*D0{P,N}'], ['bad['], ['bad{'], ['re:('], [''], ['USB_D{P,M}'], ['LVDS*'], ['Net-(*'],
  ['DDR_DQ[[]*'], ['A.B'], ['A?B'], ['/usb/*'], ['RF_*', 'USB_D*', 'SD_*'], ['*_{P,N}', 'PCIE_TX0_P'], ['*'],
];

const patterns = [];
for (const p of PATTERNS) patterns.push({ names: NAMES, patterns: p, options: {}, result: matchNets(NAMES, p) });
patterns.push({ names: NAMES, patterns: ['rf_*', 'usb_d*'], options: { caseSensitive: true }, result: matchNets(NAMES, ['rf_*', 'usb_d*'], { caseSensitive: true }) });
for (const nc of cm5.board.net_classes.filter((c) => c.patterns.length)) {
  for (const caseSensitive of [true, false]) {
    patterns.push({ names: cm5.copper.nets, patterns: nc.patterns, options: { caseSensitive }, board: 'cm5', result: matchNets(cm5.copper.nets, nc.patterns, { board: cm5.board, caseSensitive }) });
  }
}
patterns.push({ names: cm5.copper.nets, patterns: ['*.D{0,1}_{P,N}', 're:.*USB.*'], options: {}, board: 'cm5', result: matchNets(cm5.copper.nets, ['*.D{0,1}_{P,N}', 're:.*USB.*'], { board: cm5.board }) });

const copper = { cm5: cm5.copper, pair: pair.copper, lanes: lanes() };
const boards = { cm5: cm5.board, pair: pair.board, lanes: null };
const SEEDS = [
  ['cm5', { layer: 'F.Cu', nets: ['/CM5/ETH_PI.TRD0_P', '/CM5/ETH_PI.TRD0_N'] }],
  ['cm5', { layer: 'F.Cu', nets: ['/CM5/ETH_PI.TRD0_P'] }],
  ['cm5', { layer: 'B.Cu', nets: ['/CM5/ETH_PI.TRD0_P', '/CM5/ETH_PI.TRD0_N'] }],
  ['cm5', { layer: 'B.Cu', nets: ['/CM5/ETH_PI.TRD0_N', '/CM5/ETH_PI.TRD0_P'], width: 0.13 }],
  ['cm5', { layer: 'In1.Cu', nets: ['GND'] }],
  ['cm5', { layer: 'F.Cu', nets: ['/CM5/NOPE'] }],
  ['pair', { layer: 'F.Cu', nets: ['USB_P', 'USB_N'] }],
  ['lanes', { layer: 'F.Cu', nets: ['NET_A', 'NET_B'] }],
  ['lanes', { layer: 'F.Cu', nets: ['NET_E', 'NET_F'] }],
  ['lanes', { layer: 'F.Cu', nets: ['SOLO'] }],
  ['lanes', { layer: 'F.Cu', nets: ['WIDE'] }],
  ['lanes', { layer: 'B.Cu', nets: ['NET_A'] }],
];
const similar = SEEDS.map(([key, seed]) => ({ copper: key, seed, result: similarTracks(copper[key], seed, { board: boards[key] }) }));

const out = `${JSON.stringify({
  about: 'boarddd/nets cases: JS results python must match (generated by make_cases.mjs; do not edit)',
  copper: { lanes: copper.lanes },
  patterns,
  similar,
}, null, 1)}\n`;
const path = new URL('cases.json', here);
if (process.argv.includes('--check')) {
  if (readFileSync(path, 'utf8') !== out) { console.error('fixtures/nets/cases.json is stale: run node fixtures/nets/make_cases.mjs'); process.exit(1); }
} else {
  writeFileSync(path, out);
  console.log(`cases.json: ${patterns.length} pattern cases, ${similar.length} similar`);
}
