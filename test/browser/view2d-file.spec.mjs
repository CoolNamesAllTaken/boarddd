import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { expect, test } from '@playwright/test';

// boarddd/view2d from file://: an esbuild IIFE bundle (as kipr ships its reports) with the wasm and the
// fixture data in classic scripts. No fetch, no module imports: gerber faces, layers and SVG sheets draw.

const ROOT = new URL('../../', import.meta.url);
const PIC = new URL('test/fixtures/pic_programmer/base/', ROOT);

test('a classic-script bundle renders gerber and SVG content from file://', async ({ page }, testInfo) => {
  const dir = testInfo.outputPath('bundle');
  mkdirSync(dir, { recursive: true });
  await build({
    stdin: {
      contents: `import * as view2d from './src/view2d/index.js';
        import * as gerber from './src/gerber/index.js';
        import * as glue from './third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor.js';
        window.BD = { view2d, gerber, glue };`,
      resolveDir: ROOT.pathname,
    },
    // the vendored gerber core resolves its wasm against import.meta.url at load (kipr defines it the same way)
    banner: { js: 'var BD_SCRIPT_URL = document.currentScript.src;' },
    define: { 'import.meta.url': 'BD_SCRIPT_URL' },
    bundle: true, format: 'iife', outfile: `${dir}/bundle.js`, logLevel: 'error',
  });
  const files = readdirSync(PIC).map((name) => ({ name, source: readFileSync(new URL(name, PIC), 'utf8') }));
  const data = {
    wasm: readFileSync(new URL('third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor_bg.wasm', ROOT)).toString('base64'),
    files,
    svg: readFileSync(new URL('test/fixtures/pic_programmer_sch/base.svg', ROOT), 'utf8'),
  };
  writeFileSync(`${dir}/data.js`, `window.DATA = ${JSON.stringify(data)};`);
  writeFileSync(`${dir}/index.html`, `<!doctype html><meta charset="utf-8">
<style>body{margin:0}#a,#b{width:600px;height:400px}</style><div id="a"></div><div id="b"></div>
<script src="bundle.js"></script><script src="data.js"></script>`);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('requestfailed', (r) => errors.push(`request failed: ${r.url()}`));
  await page.goto(pathToFileURL(`${dir}/index.html`).href);
  expect(errors).toEqual([]);
  const res = await page.evaluate(async () => {
    const { view2d, gerber, glue } = window.BD;
    const { wasm, files, svg } = window.DATA;
    const bytes = Uint8Array.from(atob(wasm), (c) => c.charCodeAt(0));
    const renderer = await gerber.createGerberRenderer(document.createElement('canvas'), { wasmModule: glue, wasmInitInput: { module_or_path: bytes } });
    const edge = files.find((f) => f.name.includes('Edge_Cuts'));
    const a = view2d.createStage(document.getElementById('a'), { renderer, bounds: gerber.padBounds(gerber.gerberExtents(edge.source), 2) });
    a.setScene([{ layers: [{ content: view2d.face(view2d.faceBoard(files, 'top'), { side: 'top' }) }, { content: view2d.layers(view2d.layerStack(files).filter((l) => l.role === 'silk')) }] }]);
    const rect = { minX: 120, maxX: 200, minY: -60, maxY: -5 };
    const b = view2d.createStage(document.getElementById('b'), { bounds: rect, background: '#fff' });
    b.setScene([{ layers: [{ content: view2d.image(svg, rect) }] }]);
    await Promise.all([a.ready(), b.ready()]);
    const ink = (s) => {
      const d = s.capture(0).getContext('2d').getImageData(0, 0, 600, 400).data;
      let n = 0;
      for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
      return n;
    };
    return { protocol: location.protocol, a: ink(a), b: ink(b), tiles: [a.stats().tiles[0].map((t) => !!t.base), b.stats().tiles[0].map((t) => !!t.base)] };
  });
  expect(errors).toEqual([]);
  expect(res.protocol).toBe('file:');
  expect(res.tiles).toEqual([[true, true], [true]]);
  expect(res.a).toBeGreaterThan(50000);
  expect(res.b).toBeGreaterThan(50000);
});
