// Test harness for boarddd/view2d (not part of the library): loads the fixture boards and sheets once,
// one shared renderer, and mounts stages into #host (fixed size) for the specs.
import * as view2d from '/src/view2d/index.js';
import * as gerber from '/src/gerber/index.js';

const PIC = ['B_Mask.gbr', 'bottom_layer.gbr', 'B_Silkscreen.gbr', 'Edge_Cuts.gbr', 'F_Mask.gbr', 'F_Silkscreen.gbr', 'NPTH.drl', 'PTH.drl', 'top_layer.gbr']
  .map((f) => `pic_programmer-${f}`);
const RB = ['B_Cu', 'B_Mask', 'B_Paste', 'B_Silkscreen', 'Edge_Cuts', 'F_Cu', 'F_Mask', 'F_Paste', 'F_Silkscreen', 'In1_Cu', 'In2_Cu', 'In3_Cu', 'In4_Cu', 'In5_Cu', 'In6_Cu']
  .map((f) => `RoyalBlue54L-Feather-${f}.gbr`).concat(['RoyalBlue54L-Feather-NPTH.drl', 'RoyalBlue54L-Feather-PTH.drl']);

const text = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.text(); });
const load = (dir, names) => Promise.all(names.map(async (name) => ({ name, source: await text(`${dir}/${name}`) })));

/** Board bounds from Edge.Cuts plus a margin (mm). */
function boardBounds(files, margin = 2) {
  const edge = files.find((f) => /Edge_Cuts/.test(f.name));
  const b = gerber.gerberExtents(edge.source);
  return gerber.padBounds(b, margin);
}

let stage = null;
let compare = null;

async function setup() {
  const [picBase, picHead, rb, sheetBase, sheetHead] = await Promise.all([
    load('/test/fixtures/pic_programmer/base', PIC),
    load('/test/fixtures/pic_programmer/head', PIC),
    load('/fixtures/royalblue54L_feather/fab', RB),
    text('/test/fixtures/pic_programmer_sch/base.svg'),
    text('/test/fixtures/pic_programmer_sch/head.svg'),
  ]);
  const renderer = await gerber.createGerberRenderer(document.createElement('canvas'), { contextAttributes: { preserveDrawingBuffer: true } });
  const host = document.getElementById('host');
  window.v2 = {
    view2d, gerber, renderer, host,
    files: { picBase, picHead, rb },
    sheets: { base: sheetBase, head: sheetHead, rect: { minX: 120, maxX: 200, minY: -60, maxY: -5 } },
    bounds: { pic: gerber.unionBounds(boardBounds(picBase), boardBounds(picHead)), rb: boardBounds(rb) },
    file: (list, part) => list.find((f) => f.name.includes(part)),
    /** A fresh stage in #host (the previous one is destroyed). */
    mount(options = {}) {
      compare?.destroy();
      stage?.destroy();
      compare = null;
      stage = view2d.createStage(host, { renderer, dpr: 1, background: '#202020', ...options });
      window.stage = stage;
      return stage;
    },
    compare(options) {
      compare = view2d.createCompare(stage, options);
      window.cmp = compare;
      return compare;
    },
    /** Host CSS px of world point (x, y) in pane `pane` (what a screenshot of #host is indexed by). */
    at(pane, x, y) {
      const h = host.getBoundingClientRect();
      const p = stage.panes[pane].el.getBoundingClientRect();
      const [px, py] = stage.toScreen(x, y, pane);
      return [p.left - h.left + px, p.top - h.top + py];
    },
    holes: (list, part) => gerber.parseExcellon(list.find((f) => f.name.includes(part)).source),
    /** RGBA of a pane-local CSS pixel, from the pane's capture (content only). */
    capturePixel(pane, x, y) {
      const c = stage.capture(pane);
      return [...c.getContext('2d').getImageData(Math.round(x), Math.round(y), 1, 1).data];
    },
  };
}

window.v2ready = setup().then(() => true, (e) => { window.v2error = String(e?.stack || e); return false; });
