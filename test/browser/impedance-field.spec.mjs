import { mkdirSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { expect, test } from '@playwright/test';

// boarddd/impedance tier 2 in the browser: the field solver on the main thread (also from a file:// IIFE
// bundle, where module workers are unavailable) and in its module worker, with timings for typical cases.
// The < 300 ms target is for a desktop browser; CI runners and SwiftShader hosts get a looser bound here.

const ROOT = new URL('../../', import.meta.url);
const CASES = [
  ['microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4 }],
  ['coated_microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }],
  ['stripline', { w: 0.1, h1: 0.2, h2: 0.3, t: 0.018, er: 4.1 }],
  ['cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }],
  ['coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }],
  ['coupled_stripline', { w: 0.1, s: 0.15, h1: 0.15, h2: 0.3, t: 0.018, er: 4.1 }],
];
const LIMIT_MS = process.env.CI ? 1500 : 1000;

/** Median of 5 runs after one warm-up, per case. */
const timeAll = async (run, cases) => {
  const out = [];
  for (const [model, params] of cases) {
    await run(model, params);
    const ms = [];
    let r;
    for (let i = 0; i < 5; i++) { const t0 = performance.now(); r = await run(model, params); ms.push(performance.now() - t0); }
    ms.sort((a, b) => a - b);
    out.push({ model, Z: r.Z0 ?? r.Zdiff, error_pct: r.error_pct, ms: ms[2] });
  }
  return out;
};

test('main thread and module worker agree, typical cases are fast', async ({ page }, testInfo) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('test/browser/pages/blank.html');
  const res = await page.evaluate(async ({ CASES, timeAllSrc }) => {
    const timeAll = (0, eval)(`(${timeAllSrc})`);
    const z = await import('/src/impedance/index.js');
    const main = await timeAll((m, p) => z.fieldCalculate(m, p), CASES);
    const worker = new Worker('/src/impedance/fieldsolver-worker.js', { type: 'module' });
    let id = 0;
    const call = (msg) => new Promise((resolve, reject) => {
      const me = ++id;
      const on = ({ data }) => { if (data.id !== me) return; worker.removeEventListener('message', on); data.error ? reject(new Error(data.error)) : resolve(data.result); };
      worker.addEventListener('message', on);
      worker.postMessage({ id: me, ...msg });
    });
    const inWorker = await timeAll((model, params) => call({ model, params }), CASES);
    const section = z.sectionFor('microstrip', CASES[0][1]);
    const viaSection = await call({ section, opts: { tol: 0.005 } });
    let bad = null;
    try { await call({ section: { conductors: [] } }); } catch (e) { bad = e.message; }
    worker.terminate();
    return { main, inWorker, viaSection: viaSection.Z0, bad, ua: navigator.userAgent };
  }, { CASES, timeAllSrc: timeAll.toString() });
  expect(errors).toEqual([]);
  testInfo.annotations.push({ type: 'timings', description: JSON.stringify({ main: res.main, worker: res.inWorker }) });
  console.log('field solver timings (ms, median of 5):');
  for (let i = 0; i < CASES.length; i++) console.log(`  ${CASES[i][0].padEnd(20)} main ${res.main[i].ms.toFixed(0).padStart(5)}  worker ${res.inWorker[i].ms.toFixed(0).padStart(5)}  Z ${res.main[i].Z.toFixed(2)}`);
  for (let i = 0; i < CASES.length; i++) {
    expect(res.inWorker[i].Z).toBeCloseTo(res.main[i].Z, 9);
    expect(res.main[i].error_pct).toBeLessThan(1);
    expect(res.main[i].ms).toBeLessThan(LIMIT_MS);
  }
  expect(res.viaSection).toBeGreaterThan(50);
  expect(res.bad).toMatch(/no signal/);
});

test('a classic-script bundle solves on the main thread from file://', async ({ page }, testInfo) => {
  const dir = testInfo.outputPath('bundle');
  mkdirSync(dir, { recursive: true });
  await build({
    stdin: { contents: "import * as impedance from './src/impedance/index.js'; window.BD = { impedance };", resolveDir: ROOT.pathname },
    bundle: true, format: 'iife', outfile: `${dir}/bundle.js`, logLevel: 'error',
  });
  writeFileSync(`${dir}/index.html`, '<!doctype html><meta charset="utf-8"><script src="bundle.js"></script>');
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(pathToFileURL(`${dir}/index.html`).href);
  const res = await page.evaluate(() => {
    const z = window.BD.impedance;
    const r = z.fieldCalculate('coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4 });
    return { protocol: location.protocol, Zdiff: r.Zdiff, closed: z.coupledMicrostrip({ w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4 }).Zdiff };
  });
  expect(errors).toEqual([]);
  expect(res.protocol).toBe('file:');
  expect(Math.abs(res.Zdiff / res.closed - 1)).toBeLessThan(0.03);
});
