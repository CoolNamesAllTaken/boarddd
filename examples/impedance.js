// boarddd impedance demo: click a trace, read its impedance. The app chrome (board and view switches) is this
// file's; the stage is boarddd/view2d, the copper boarddd/copper (from the Gerber X2 files in the browser) or a
// copper@1 document, the panel boarddd/impedance/ui.
import * as v2 from 'boarddd/view2d/index.js';
import * as gerber from 'boarddd/gerber/index.js';
import { copperFromGerbers } from 'boarddd/copper/index.js';
import { stackupFromJob } from 'boarddd/model/index.js';
import { impedancePanel, copperContent } from 'boarddd/impedance/ui/index.js';

const $ = (id) => document.getElementById(id);
const text = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.text(); });
const gz = async (url) => JSON.parse(await new Response((await fetch(url)).body.pipeThrough(new DecompressionStream('gzip'))).text());

const RB_DIR = '../fixtures/royalblue54L_feather/fab';
const RB = ['B_Cu', 'B_Mask', 'B_Silkscreen', 'Edge_Cuts', 'F_Cu', 'F_Mask', 'F_Silkscreen', 'In1_Cu', 'In2_Cu', 'In3_Cu', 'In4_Cu', 'In5_Cu', 'In6_Cu']
  .map((f) => `RoyalBlue54L-Feather-${f}.gbr`).concat(['RoyalBlue54L-Feather-NPTH.drl', 'RoyalBlue54L-Feather-PTH.drl', 'RoyalBlue54L-Feather-job.gbrjob']);

/** royalblue54L: everything from its fab files, as a Gerber-only upload would be. */
async function royalblue() {
  const files = await Promise.all(RB.map(async (name) => ({ name, source: await text(`${RB_DIR}/${name}`) })));
  const copper = copperFromGerbers(files.map((f) => ({ name: f.name, text: f.source })), { name: 'RoyalBlue54L-Feather' });
  const job = files.find((f) => f.name.endsWith('.gbrjob')).source;
  // the board: the gbrjob's stackup; nets from the X2 attributes. The USB pair's 90 Ω target is entered here, as a
  // user would (the Gerbers carry no net classes).
  const usb = ['/Debugger/D+', '/Debugger/D-'];
  const board = {
    schema: 'boarddd/board@1', name: 'RoyalBlue54L-Feather', units: 'mm', frame: 'board', source: { kind: 'gerber' },
    stackup: stackupFromJob(job, copper.layers),
    nets: copper.nets.filter(Boolean).map((n) => ({ name: n, net_class: usb.includes(n) ? 'USB_90' : 'Default', pair: n === usb[0] ? usb[1] : n === usb[1] ? usb[0] : null })),
    net_classes: [{ name: 'USB_90', nets: usb, impedance: { kind: 'differential', target: 90, tolerance_pct: 10, source: 'user', layers: [] } }],
  };
  const gbr = files.filter((f) => !f.name.endsWith('.gbrjob'));
  const bounds = gerber.padBounds(gerber.gerberExtents(gbr.find((f) => /Edge_Cuts/.test(f.name)).source), 1.5);
  const views = {
    top: { label: 'top', layers: ['F.Cu'], content: () => v2.face(v2.faceBoard(gbr, 'top'), { side: 'top', palette: { mask: 'blue', finish: 'enig' } }), flip: false },
    bottom: { label: 'bottom', layers: ['B.Cu'], content: () => v2.face(v2.faceBoard(gbr, 'bottom'), { side: 'bottom', palette: { mask: 'blue', finish: 'enig' } }), flip: true },
    copper: { label: 'copper', layers: null, content: () => copperContent(copper, { }), flip: false },
  };
  return { board, copper, bounds, views, start: usb };
}

/** CM5 MINIMA: its copper@1 and board@1 as boarddd's KiCad readers wrote them (fixtures/impedance/route/cm5.json.gz). */
async function cm5() {
  const { board, copper } = await gz('../fixtures/impedance/route/cm5.json.gz');
  const out = (board.outline?.board ?? []);
  const xs = out.map((p) => p[0]), ys = out.map((p) => p[1]);
  const bounds = { minX: Math.min(...xs) - 1, maxX: Math.max(...xs) + 1, minY: Math.min(...ys) - 1, maxY: Math.max(...ys) + 1 };
  const one = (l) => () => copperContent(copper, { layers: [l] });
  const views = {
    top: { label: 'F.Cu', layers: ['F.Cu'], content: one('F.Cu'), flip: false },
    bottom: { label: 'B.Cu', layers: ['B.Cu'], content: one('B.Cu'), flip: true },
    copper: { label: 'all', layers: null, content: () => copperContent(copper), flip: false },
  };
  return { board, copper, bounds, views, start: ['/CM5/ETH_PI.TRD0_P', '/CM5/ETH_PI.TRD0_N'] };
}

const renderer = gerber.createGerberRenderer(document.createElement('canvas'));
let stage = null, panel = null, data = null, view = 'top';
const cache = new Map();

async function open(which) {
  panel?.destroy(); stage?.destroy();
  $('stage').replaceChildren(); $('panel').replaceChildren();
  if (!cache.has(which)) cache.set(which, await (which === 'cm5' ? cm5() : royalblue()));
  data = cache.get(which);
  stage = v2.createStage($('stage'), { renderer: () => renderer, bounds: data.bounds, background: '#14161a' });
  panel = impedancePanel($('panel'), { board: data.board, copper: data.copper, stage, layers: () => data.views[view].layers });
  const contents = Object.fromEntries(Object.entries(data.views).map(([k, v]) => [k, v.content()]));
  const show = (k) => {
    view = k;
    stage.setFlip(data.views[k].flip);
    stage.setScene([{ layers: [{ content: contents[k] }] }]);
    for (const b of $('views').children) b.setAttribute('aria-pressed', String(b.dataset.v === k));
  };
  $('views').replaceChildren(...Object.entries(data.views).map(([k, v]) => {
    const b = document.createElement('button');
    b.textContent = v.label; b.dataset.v = k; b.title = `show ${v.label}; clicks pick ${v.layers ? v.layers.join(', ') : 'any layer'}`;
    b.onclick = () => show(k);
    return b;
  }));
  show('top');
  window.demo = { stage, panel, data, show, ready: stage.ready() };
}

$('board').onchange = () => open($('board').value);
$('fit').onclick = () => stage?.fit();
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.get('b')) $('board').value = hash.get('b');
await open($('board').value);
if (hash.get('select') !== '0') window.demo.selected = panel.select(data.start);
