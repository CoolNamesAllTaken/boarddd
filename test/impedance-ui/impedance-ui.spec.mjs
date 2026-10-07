import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { expect, test } from '@playwright/test';

// boarddd/impedance/ui on examples/impedance.html: royalblue54L_feather from its Gerber X2 export (copper read in
// the browser by copperFromGerbers, stackup from the gbrjob) and CM5 MINIMA from its copper@1. A click picks the
// net and its pair, the panel's Z matches analyzeNet's golden, the profile drives the stage marker, an override
// re-runs, the cross-section draws; a file:// bundle runs the same panel without a Worker.
// IMP_SHOTS=dir saves screenshots for the PR.

const ROOT = new URL('../../', import.meta.url);
const USB = ['/Debugger/D+', '/Debugger/D-'];
const shot = async (page, name) => { if (process.env.IMP_SHOTS) await page.screenshot({ path: `${process.env.IMP_SHOTS}/${name}.png` }); };

async function openDemo(page, hash = 'b=rb&select=0') {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`/examples/impedance.html#${hash}`);
  await page.waitForFunction(() => window.demo?.panel, null, { timeout: 120_000 });
  await page.evaluate(() => window.demo.stage.ready());
  return errors;
}

/** Page coordinates of a board point (mm) on the stage's first pane. */
async function screenOf(page, x, y) {
  return page.evaluate(([x, y]) => {
    const { stage } = window.demo;
    const [px, py] = stage.toScreen(x, y);
    const r = stage.panes[0].el.getBoundingClientRect();
    return [r.left + px, r.top + py];
  }, [x, y]);
}

/** The middle of the longest F.Cu track of a net (board mm). */
const trackMid = (page, net) => page.evaluate((net) => {
  const t = window.demo.data.copper.tracks.filter((t) => t.net === net && t.layer === 'F.Cu' && !t.mid)
    .sort((a, b) => Math.hypot(b.end[0] - b.start[0], b.end[1] - b.start[1]) - Math.hypot(a.end[0] - a.start[0], a.end[1] - a.start[1]))[0];
  return [(t.start[0] + t.end[0]) / 2, (t.start[1] + t.end[1]) / 2, window.demo.data.copper.tracks.indexOf(t)];
}, net);

test('click a trace: its net and pair, Z as analyzeNet gives it, hover tooltip', async ({ page }) => {
  const errors = await openDemo(page);
  const [x, y] = await trackMid(page, USB[0]);
  await page.evaluate(([x, y]) => window.demo.stage.zoomTo({ minX: x - 4, maxX: x + 4, minY: y - 3, maxY: y + 3 }), [x, y]);
  await page.evaluate(() => window.demo.stage.ready());
  const [px, py] = await screenOf(page, x, y);
  await page.mouse.move(px, py);
  await expect(page.locator('.bdi-tip')).toHaveText(/\/Debugger\/D\+ · 0\.1 mm · F\.Cu/);
  await page.mouse.click(px, py);
  await page.waitForFunction(() => window.demo.panel.result, null, { timeout: 60_000 });
  const got = await page.evaluate(() => ({ sel: window.demo.panel.selection, doc: window.demo.panel.result, mode: window.demo.panel.analyzer.mode }));
  expect(got.sel.nets).toEqual(USB);
  expect(got.mode).toBe('worker');
  expect(got.doc.kind).toBe('differential');
  // the golden: analyzeNet on the same pair read from the .kicad_pcb (fixtures/impedance/route/royalblue-usb.json.gz)
  const golden = await page.evaluate(async (nets) => {
    const { analyzeNet } = await import('/src/impedance/index.js');
    const res = await fetch('/fixtures/impedance/route/royalblue-usb.json.gz');
    const { board, copper } = JSON.parse(await new Response(res.body.pipeThrough(new DecompressionStream('gzip'))).text());
    return analyzeNet(board, copper, nets).summary;
  }, USB);
  expect(Math.abs(got.doc.summary.z_weighted - golden.z_weighted) / golden.z_weighted).toBeLessThan(0.01);
  const shown = await page.locator('.bdi-z').textContent();
  expect(Math.abs(parseFloat(shown) - got.doc.summary.z_weighted)).toBeLessThan(0.06);
  await expect(page.locator('.bdi-verdict')).toHaveText(/✗ 90 ±10 %/); // 107 Ω against the 90 Ω entered in the example
  expect(await page.locator('.bdi-hl-track').count()).toBeGreaterThan(20);
  await shot(page, 'select-pair');
  expect(errors).toEqual([]);
});

test('alt-click selects one segment; the profile moves the stage marker; discontinuities zoom', async ({ page }) => {
  await openDemo(page);
  const [x, y, index] = await trackMid(page, USB[1]);
  await page.evaluate(([x, y]) => window.demo.stage.zoomTo({ minX: x - 4, maxX: x + 4, minY: y - 3, maxY: y + 3 }), [x, y]);
  await page.evaluate(() => window.demo.stage.ready());
  const [px, py] = await screenOf(page, x, y);
  await page.keyboard.down('Alt');
  await page.mouse.click(px, py);
  await page.keyboard.up('Alt');
  await page.waitForFunction(() => window.demo.panel.result, null, { timeout: 60_000 });
  const one = await page.evaluate(() => ({ sel: window.demo.panel.selection, doc: window.demo.panel.result, tracks: window.demo.data.copper.tracks }));
  expect(one.sel).toEqual({ nets: [USB[1]], track: index });
  const id = one.tracks[index].id;
  expect(one.doc.sections.every((s) => id == null || s.tracks.includes(id))).toBe(true);
  expect(await page.locator('.bdi-hl-track').count()).toBe(1);

  // the whole pair, then hover the profile at two places: the marker follows the route
  await page.evaluate((nets) => window.demo.panel.select(nets), USB);
  await page.evaluate(() => window.demo.stage.fit());
  const box = await page.locator('.bdi-hit').boundingBox();
  const markerAt = async (f) => {
    await page.mouse.move(box.x + box.width * f, box.y + box.height / 2);
    return page.evaluate(() => { const c = document.querySelector('.bdi-marker'); return c ? [+c.getAttribute('cx'), +c.getAttribute('cy')] : null; });
  };
  const a = await markerAt(0.3), b = await markerAt(0.8);
  expect(a && b).toBeTruthy();
  expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeGreaterThan(3);
  // the marker is on the pair's copper: the route point at that s
  const check = await page.evaluate(([f]) => {
    const { panel } = window.demo;
    const secs = panel.result.sections.filter((s) => s.net === panel.result.nets[0]);
    return { s1: Math.max(...secs.map((s) => s.s1)), section: panel.section?.structure ?? null };
  }, [0.8]);
  expect(check.s1).toBeGreaterThan(15);
  expect(await page.locator('.bdi-hl-section').count()).toBeGreaterThan(0);
  await shot(page, 'profile-hover');
  // a discontinuity: click it, the stage zooms there and rings it
  const before = await page.evaluate(() => window.demo.stage.getView().s);
  await page.locator('.bdi-d-via').first().click();
  const after = await page.evaluate(() => window.demo.stage.getView().s);
  expect(after).toBeGreaterThan(before * 2);
  expect(await page.locator('.bdi-ring').count()).toBe(1);
});

test('an override re-runs; the cross-section draws the solved geometry', async ({ page }) => {
  await openDemo(page, 'b=rb');
  await page.waitForFunction(() => window.demo.panel.result, null, { timeout: 60_000 });
  const base = await page.evaluate(() => ({ runs: window.demo.panel.runs, kinds: [...new Set(window.demo.panel.result.sections.map((s) => s.structure))] }));
  expect(base.kinds).toContain('cpwg');
  // the cross-section of the longest section: copper, a plane, dielectric with εr, dimensions (on hover)
  const xs = page.locator('svg.bdi-xs');
  await expect(xs).toBeVisible();
  expect(await xs.locator('rect.bdi-xs-cu').count()).toBeGreaterThanOrEqual(1);
  expect(await xs.locator('rect.bdi-xs-plane').count()).toBeGreaterThanOrEqual(1);
  await expect(xs.locator('text.bdi-xs-er').first()).toHaveText(/εr 4\.5/);
  await xs.hover();
  await expect(xs.locator('.bdi-xs-dims')).toBeVisible();
  await expect(xs.locator('.bdi-xs-dimtext').first()).toHaveText(/^w 0\.1/);
  await shot(page, 'cross-section');
  await page.selectOption('.bdi-override', 'microstrip');
  await page.waitForFunction((n) => window.demo.panel.runs > n && window.demo.panel.result, base.runs, { timeout: 60_000 });
  const forced = await page.evaluate(() => window.demo.panel.result);
  expect(new Set(forced.sections.map((s) => s.structure))).toEqual(new Set(['microstrip']));
  expect(forced.sections.filter((s) => s.z).every((s) => s.flags.includes('override'))).toBe(true);
  await page.selectOption('.bdi-override', '');
  await page.waitForFunction(() => window.demo.panel.result?.sections.some((s) => s.structure === 'cpwg'), null, { timeout: 60_000 });
});

test('CM5 MINIMA: the 100 Ω Ethernet pair, within its class target', async ({ page }) => {
  const errors = await openDemo(page, 'b=cm5');
  await page.waitForFunction(() => window.demo.panel.result, null, { timeout: 120_000 });
  const doc = await page.evaluate(() => window.demo.panel.result);
  expect(doc.nets).toEqual(['/CM5/ETH_PI.TRD0_P', '/CM5/ETH_PI.TRD0_N']);
  expect(doc.target.value).toBe(100);
  expect(Math.abs(doc.summary.z_weighted - 101.19)).toBeLessThan(0.5); // python/tests/test_impedance_route.py, docs
  await expect(page.locator('.bdi-verdict')).toHaveText(/✓ 100 ±10 %/);
  await shot(page, 'cm5-eth');
  expect(errors).toEqual([]);
});

test('3D hook: a pick on the board solid selects in 2D', async ({ page }) => {
  await openDemo(page);
  const res = await page.evaluate(async (net) => {
    const THREE = await import('three');
    const { boardPointFromPick } = await import('/src/impedance/ui/index.js');
    const t = window.demo.data.copper.tracks.find((t) => t.net === net && t.layer === 'F.Cu');
    const [x, y] = [(t.start[0] + t.end[0]) / 2, (t.start[1] + t.end[1]) / 2];
    // a board solid moved and turned in the scene, as a viewer may place it
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
    mesh.userData.group = 'board';
    mesh.position.set(-150, 100, 0.8);
    mesh.rotation.z = Math.PI / 2;
    mesh.updateMatrixWorld(true);
    const point = new THREE.Vector3(x, y, 0.8).applyMatrix4(mesh.matrixWorld);
    const p = boardPointFromPick({ object: mesh, point });
    await window.demo.panel.selectAt(p.x, p.y);
    return { p, x, y, nets: window.demo.panel.selection.nets };
  }, USB[0]);
  expect(Math.hypot(res.p.x - res.x, res.p.y - res.y)).toBeLessThan(1e-9);
  expect(res.nets).toEqual(USB);
});

test('file:// bundle: the panel runs on the main thread', async ({ page }, testInfo) => {
  const dir = testInfo.outputPath('bundle');
  mkdirSync(dir, { recursive: true });
  await build({
    stdin: {
      contents: `import * as view2d from './src/view2d/index.js';
        import * as ui from './src/impedance/ui/index.js';
        window.BD = { view2d, ui };`,
      resolveDir: ROOT.pathname,
    },
    // the vendored gerber core resolves its wasm against import.meta.url at load (as kipr bundles it)
    banner: { js: 'var BD_SCRIPT_URL = document.currentScript.src;' },
    define: { 'import.meta.url': 'BD_SCRIPT_URL' },
    bundle: true, format: 'iife', outfile: `${dir}/bundle.js`, logLevel: 'error',
  });
  const data = gunzipSync(readFileSync(new URL('fixtures/impedance/route/royalblue-usb.json.gz', ROOT))).toString('utf8');
  writeFileSync(`${dir}/data.js`, `window.DATA = ${data};`);
  writeFileSync(`${dir}/index.html`, `<!doctype html><meta charset="utf-8">
<style>body{margin:0;display:flex}#s{width:700px;height:500px}#p{width:340px}</style><div id="s"></div><div id="p"></div>
<script src="bundle.js"></script><script src="data.js"></script>`);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('requestfailed', (r) => errors.push(`request failed: ${r.url()}`));
  await page.goto(pathToFileURL(`${dir}/index.html`).href);
  const res = await page.evaluate(async (nets) => {
    const { view2d, ui } = window.BD;
    const { board, copper } = window.DATA;
    const pts = copper.tracks.flatMap((t) => [t.start, t.end]);
    const bounds = { minX: Math.min(...pts.map((p) => p[0])), maxX: Math.max(...pts.map((p) => p[0])), minY: Math.min(...pts.map((p) => p[1])), maxY: Math.max(...pts.map((p) => p[1])) };
    const stage = view2d.createStage(document.getElementById('s'), { bounds, background: '#14161a' });
    stage.setScene([{ layers: [{ content: ui.copperContent(copper, { layers: ['F.Cu'] }) }] }]);
    const panel = ui.impedancePanel(document.getElementById('p'), { board, copper, stage });
    await panel.select(nets);
    await stage.ready();
    return { mode: panel.analyzer.mode, z: panel.result?.summary.z_weighted, xs: !!document.querySelector('svg.bdi-xs') };
  }, USB);
  expect(res.mode).toBe('main');
  expect(res.z).toBeGreaterThan(100);
  expect(res.xs).toBe(true);
  expect(errors).toEqual([]);
});
