// boarddd demo: KiCad's royalblue54L_feather demo board, the board built from its Gerbers + drill
// files (boarddd/board), the components from kicad-cli's GLB matched to their reference designators
// (boarddd/models), in boarddd/scene's viewer. Δ swaps the faces for a copper diff.
import { createViewer } from 'boarddd/scene/index.js';
import { loadGLB, prepareModel } from 'boarddd/models/index.js';
import { buildGerberBoard, readFabFiles, paintCopperDiff } from 'boarddd/board/index.js';

const DATA = new URL('./data/royalblue54L_feather/', import.meta.url);
const status = document.getElementById('status');
const say = (t) => { status.textContent = t; };

async function gerberApi() {
  const mods = await Promise.all(['index', 'board', 'diff', 'drills', 'layers', 'outline', 'raster'].map((m) => import(`wasm-gerber-renderer/${m}.js`)));
  const glue = await import('wasm-gerber-renderer/wasm/wasm_gerber_processor.js');
  const api = Object.assign({}, ...mods);
  const renderer = await api.createGerberRenderer(document.createElement('canvas'), {
    wasmModule: glue,
    wasmInitInput: { module_or_path: new URL('/vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm', location.href) },
    contextAttributes: { preserveDrawingBuffer: true },
  });
  return { api, renderer };
}
const fetchText = async (url) => (await fetch(url)).text();

const dark = matchMedia('(prefers-color-scheme: dark)').matches;
const viewer = createViewer(document.getElementById('stage'), { theme: dark ? 'dark' : 'light', viewCube: true });

try {
  const manifest = await (await fetch(new URL('manifest.json', DATA))).json();
  const files = await Promise.all(manifest.files.map(async (name) => ({ name, text: await fetchText(new URL(name, DATA)) })));
  const components = await (await fetch(new URL(manifest.components, DATA))).json();
  say('Painting the board from its Gerbers…');
  const { api, renderer } = await gerberApi();
  const board = await buildGerberBoard(api, renderer, files, { thickness: 1.6 });
  viewer.add(board.group);
  viewer.setView('iso');

  say('Loading the components (GLB)…');
  const { minX, maxX, minY, maxY } = board.painted.bounds;
  const model = prepareModel(await loadGLB(new URL(manifest.glb, DATA).href), components, {
    boardSize: [maxX - minX - 1, maxY - minY - 1], boardOrigin: [minX + 0.5, -(maxY - 0.5)],
  });
  // the GLB's own board would sit inside ours: keep its components only
  for (const list of Object.values(model.parts)) for (const p of list) p.visible = false;
  viewer.add(model.root);
  viewer.fit('iso');

  const r = model.report, h = board.holes;
  say(`KiCad demo data: royalblue54L_feather (KiCad's demos/). Board from Gerbers: ${h.kept.length} holes punched`
    + `${h.rejected ? `, ${h.rejected} on the edge left painted` : ''}. Components: ${r.matched}/${r.expected} matched`
    + ` (${r.byName} by name, ${r.byPosition} by position).`);
  window.demo = { ok: true, holes: h.kept.length, matched: r.matched, expected: r.expected };

  let diff = null;
  document.getElementById('diff').addEventListener('change', async (e) => {
    if (e.target.checked) {
      // There is no second revision of KiCad's demo: the base is the same board without its NPTH
      // drill file, so those holes show as added (green) and everything else as unchanged.
      diff ??= await paintCopperDiff(api, renderer, {
        base: readFabFiles(api, files.filter((f) => !/NPTH/.test(f.name))), head: board.fab,
      }, board.painted);
      board.setFaces({ top: diff.top, bottom: diff.bottom });
    } else {
      board.setFaces({ top: board.textures.top, bottom: board.textures.bottom });
    }
    viewer.requestRender();
  });
  document.getElementById('parts').addEventListener('change', (e) => { model.root.visible = e.target.checked; viewer.requestRender(); });
} catch (e) {
  say(`Failed: ${e.message}`);
  window.demo = { ok: false, error: String(e.stack || e) };
}
for (const b of document.querySelectorAll('[data-view]')) b.addEventListener('click', () => viewer.setView(b.dataset.view));
