// A page that loads a vendor.mjs output with plain relative imports (no importmap, no bundler) and
// renders a board with the vendored three.js: what kipr's file:// viewers and CSP-locked pages need.
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ORIGIN = 'http://vendored.test';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.wasm': 'application/wasm' };

const PAGE = `<!doctype html><meta charset="utf-8"><title>vendored boarddd</title>
<style>html,body{margin:0}#view{width:480px;height:320px}</style>
<div id="view"></div><script type="module" src="./app.js"></script>`;

const APP = `import * as THREE from './three/three.module.js';
import { buildBoard } from './boarddd/src/board/index.js';
import { createViewer } from './boarddd/src/scene/index.js';
import { kicadToBoard } from './boarddd/src/geom/index.js';
import { loadGLB } from './boarddd/src/models/index.js';

const board = buildBoard({ outline: { board: [[0, 0], [40, 0], [40, 25], [0, 25]] }, holes: [{ x: 20, y: 12.5, diameter: 3, plated: true }] });
const v = createViewer(document.getElementById('view'), { theme: 'light' });
v.add(board.group);
v.setView('top');
const img = new Image();
img.src = v.capture({ width: 240, height: 160 });
await img.decode();
const c = document.createElement('canvas');
c.width = img.width; c.height = img.height;
const g = c.getContext('2d');
g.drawImage(img, 0, 0);
const px = g.getImageData(0, 0, c.width, c.height).data;
const bg = [...px.slice(0, 3)];
let board_ = 0;
for (let i = 0; i < px.length; i += 4) if (Math.abs(px[i] - bg[0]) + Math.abs(px[i + 1] - bg[1]) + Math.abs(px[i + 2] - bg[2]) > 60) board_++;
window.result = {
  oneThree: board.group instanceof THREE.Group,
  revision: THREE.REVISION,
  boardFraction: board_ / (c.width * c.height),
  kicad: kicadToBoard(1, 2),
  glb: typeof loadGLB,
};`;

test('a vendored dir loads with relative imports only and renders a board', async ({ page }) => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'boarddd-vendor-pw-'));
  try {
    const three = path.join(tmp, 'three.tgz');
    const stage = path.join(tmp, 'pack');
    for (const e of ['package.json', 'LICENSE', 'build', 'examples/jsm/controls', 'examples/jsm/loaders', 'examples/jsm/environments', 'examples/jsm/utils']) {
      fs.cpSync(path.join(ROOT, 'node_modules/three', e), path.join(stage, 'package', e), { recursive: true });
    }
    execFileSync('tar', ['-czf', three, '-C', stage, 'package']);
    const site = path.join(tmp, 'site');
    execFileSync(process.execPath, [path.join(ROOT, 'scripts/vendor.mjs'), '--allow-dirty', '-q',
      '--out', path.join(site, 'boarddd'), '--three-dir', path.join(site, 'three'), '--three', three]);
    fs.writeFileSync(path.join(site, 'index.html'), PAGE);
    fs.writeFileSync(path.join(site, 'app.js'), APP);

    const requests = [], errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    await page.route(`${ORIGIN}/**`, (route) => {
      const rel = decodeURIComponent(new URL(route.request().url()).pathname).slice(1);
      const file = path.join(site, rel);
      requests.push(rel);
      if (!file.startsWith(site + path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: 'not found' });
      return route.fulfill({ body: fs.readFileSync(file), contentType: TYPES[path.extname(file)] || 'application/octet-stream' });
    });
    await page.goto(`${ORIGIN}/index.html`);
    const r = await page.waitForFunction(() => window.result, null, { timeout: 30_000 }).then((h) => h.jsonValue());

    expect(errors).toEqual([]);
    expect(await page.locator('script[type=importmap]').count()).toBe(0);
    expect(r.oneThree).toBe(true);
    expect(r.revision).toBe(JSON.parse(fs.readFileSync(path.join(ROOT, 'node_modules/three/package.json'), 'utf8')).version.split('.')[1]);
    expect(r.kicad).toEqual([1, -2]);
    expect(r.glb).toBe('function');
    // the top view is mostly board
    expect(r.boardFraction).toBeGreaterThan(0.3);
    expect(requests).toContain('three/addons/loaders/GLTFLoader.js');
    expect(requests).toContain('three/addons/utils/SkeletonUtils.js');
    expect(requests.filter((p) => p.startsWith('three/')).length).toBe(new Set(requests.filter((p) => p.startsWith('three/'))).size);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
