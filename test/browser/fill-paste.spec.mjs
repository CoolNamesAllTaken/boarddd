// Filled-and-capped holes and solder paste on a Gerber board (boarddd/board fillFab, buildPaste) and on a
// footprint (buildFootprint fillUpTo / paste), in headless Chromium against the real WASM renderer.
import { test, expect } from '@playwright/test';
import { readdirSync } from 'node:fs';

const MAGENTA = ([r, g, b]) => r > 200 && g < 60 && b > 200;
// pic_programmer's first via (T1, 0.6 mm ViaDrill) and a 0.8 mm component hole, board frame
const VIA = [200.025, -52.07];

async function harness(page, url) {
  await page.goto(url);
  const h = await page.waitForFunction(() => window.harness, null, { timeout: 120_000 }).then((x) => x.jsonValue());
  expect(h.error).toBeUndefined();
  return h.info;
}

for (const view of ['top', 'bottom']) {
  test(`gerber board: a via is open, and filled + capped up to its drill (${view})`, async ({ page }) => {
    const region = `&region=${VIA[0] - 2},${VIA[1] - 2},${VIA[0] + 2},${VIA[1] + 2}`;
    const base = `/test/browser/pages/view.html?gerber=/test/fixtures/pic_programmer/base/&view=${view}&w=400&h=400${region}`;
    const open = await harness(page, base);
    expect(open.filled).toBe(0);
    const [hole] = await page.evaluate((p) => window.sample([[...p, 0.8]]), VIA);
    expect(MAGENTA(hole)).toBe(true);

    const filled = await harness(page, `${base}&fill=0.6`);
    expect(filled.filled).toBeGreaterThanOrEqual(6);   // the six 0.6 mm vias
    expect(filled.holes.kept).toBe(open.holes.kept - filled.filled);
    const [cap] = await page.evaluate((p) => window.sample([[...p, 0.8]]), VIA);
    expect(MAGENTA(cap), JSON.stringify(cap)).toBe(false);
  });
}

test('gerber board: fillFab drops filled holes from the painted drill files only', async ({ page }) => {
  await page.goto('/test/browser/pages/blank-three.html');
  const r = await page.evaluate(async () => {
    const gerber = await import('/src/gerber/index.js');
    const { readFabFiles, fillFab } = await import('/src/board/index.js');
    const dir = '/test/fixtures/pic_programmer/base/';
    const { files } = await (await fetch(`${dir}manifest.json`)).json();
    const list = await Promise.all(files.map(async (name) => ({ name, text: await (await fetch(dir + name)).text() })));
    const fab = readFabFiles(gerber, list);
    const filled = fillFab(gerber, fab, 0.75);
    const pth = filled.grouped.drills.find((d) => /-PTH/.test(d.name));
    const painted = gerber.parseExcellon(pth.source);
    return {
      before: fab.holes.length, after: filled.holes.length, filled: filled.holes.filter((h) => h.filled).length,
      vias: fab.holes.filter((h) => h.via).length,
      painted: painted.length, plated: fab.holes.filter((h) => h.plated).length,
      biggestPainted: Math.min(...painted.map((h) => h.diameter)), same: fillFab(gerber, fab, 0) === fab,
    };
  });
  expect(r.after).toBe(r.before);
  expect(r.vias).toBe(6);
  expect(r.filled).toBeGreaterThan(r.vias);             // 0.6 mm vias and 0.75 mm pads
  expect(r.painted).toBe(r.plated - r.filled);
  expect(r.biggestPainted).toBeGreaterThan(0.75);
  expect(r.same).toBe(true);
});

test('gerber board: paste traced off the faces that have it and extruded off them', async ({ page }) => {
  const dir = 'fixtures/royalblue54L_feather/fab/';
  const names = readdirSync(dir).filter((n) => /\.(gbr|drl)$/.test(n) && !/In\d_Cu/.test(n));
  await page.goto('/test/browser/pages/blank-three.html');
  const r = await page.evaluate(async ({ dir, names }) => {
    const THREE = await import('three');
    const gerber = await import('/src/gerber/index.js');
    const { buildGerberBoard, buildPaste } = await import('/src/board/index.js');
    const renderer = await gerber.createGerberRenderer(document.createElement('canvas'), { contextAttributes: { preserveDrawingBuffer: true } });
    const files = await Promise.all(names.map(async (name) => ({ name, text: await (await fetch(`/${dir}${name}`)).text() })));
    const board = await buildGerberBoard(gerber, renderer, files, { thickness: 1.6 });
    const t0 = performance.now();
    const paste = await buildPaste(gerber, renderer, board.fab, board.painted, { thickness: 1.6 });
    const ms = performance.now() - t0;
    const box = (m) => (m ? new THREE.Box3().setFromObject(m) : null);
    const top = box(paste.meshes.top), bottom = box(paste.meshes.bottom);
    const outline = new THREE.Box3().setFromObject(board.body);
    const groups = [...new Set(paste.group.children.map((m) => m.userData.group))];
    paste.dispose();
    return {
      ms, groups,
      top: top && { min: top.min.toArray(), max: top.max.toArray() },
      bottom: bottom && { min: bottom.min.toArray(), max: bottom.max.toArray() },
      board: { min: outline.min.toArray(), max: outline.max.toArray() },
      triangles: paste.meshes.top.geometry.attributes.position.count / 3,
    };
  }, { dir, names });
  expect(r.groups).toEqual(['paste']);
  expect(r.top.min[2]).toBeCloseTo(1.601, 3);
  expect(r.top.max[2]).toBeCloseTo(1.721, 3);
  expect(r.bottom).toBeNull();                          // B_Paste has no apertures: no mesh
  for (const k of [0, 1]) {
    expect(r.top.min[k]).toBeGreaterThanOrEqual(r.board.min[k]);
    expect(r.top.max[k]).toBeLessThanOrEqual(r.board.max[k]);
  }
  expect(r.triangles).toBeGreaterThan(100);
  console.log(`paste traced in ${Math.round(r.ms)} ms`);
});

test('footprint: fillUpTo caps small plated pad holes, paste only when asked', async ({ page }) => {
  const fp = '/test/fixtures/pad_placement/USB_C_Receptacle_CNCTech_C-ARA1-AK51X.kicad_mod';   // 0.4 mm round PTH pads
  const plain = await harness(page, `/test/browser/pages/view.html?fp=${fp}`);
  expect(plain.paste).toBe(0);
  const withPaste = await harness(page, `/test/browser/pages/view.html?fp=${fp}&paste=1`);
  expect(withPaste.paste).toBeGreaterThan(5);
  const filled = await harness(page, `/test/browser/pages/view.html?fp=${fp}&fill=0.4`);
  expect(filled.barrels).toBeLessThan(plain.barrels);
});
