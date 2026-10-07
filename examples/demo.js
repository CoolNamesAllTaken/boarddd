// boarddd demo: a KiCad demo board built from its Gerbers + drill files (boarddd/board), with the
// copper diff against an edited copy one click away. The viewer here is a placeholder until
// boarddd/scene's createViewer lands.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildGerberBoard, readFabFiles, paintCopperDiff } from 'boarddd/board/index.js';

const DATA = new URL('./data/royalblue54L_feather/', import.meta.url);
const status = document.getElementById('status');

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

async function files(dir) {
  const m = await (await fetch(new URL('manifest.json', dir))).json();
  return { manifest: m, files: await Promise.all(m.files.map(async (name) => ({ name, text: await (await fetch(new URL(name, dir))).text() }))) };
}

// --- placeholder viewer: board frame is z-up
const stage = document.getElementById('stage');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
stage.append(renderer.domElement);
const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 5000);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
const draw = () => renderer.render(scene, camera);
controls.addEventListener('change', draw);
function resize() {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  draw();
}
addEventListener('resize', resize);

let box;
function view(name) {
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
  const d = Math.max(s.x, s.y) * 2.2;
  const dir = { top: [0, -0.001, 1], bottom: [0, -0.001, -1], iso: [0.35, -0.75, 0.7] }[name];
  camera.position.copy(c).add(new THREE.Vector3(...dir).normalize().multiplyScalar(d));
  sun.position.copy(camera.position).add(new THREE.Vector3(-20, 0, 40));
  controls.target.copy(c);
  controls.update();
  draw();
}

try {
  const bg = getComputedStyle(document.body).backgroundColor;
  scene.background = new THREE.Color(bg);
  const { api, renderer: gr } = await gerberApi();
  const head = await files(DATA);
  const board = await buildGerberBoard(api, gr, head.files, { thickness: 1.6 });
  scene.add(board.group);
  box = new THREE.Box3().setFromObject(board.group);
  resize();
  view('iso');
  const h = board.holes;
  status.textContent = `KiCad demo data: royalblue54L_feather (KiCad's demos/). ${h.kept.length} holes punched`
    + (h.rejected ? `, ${h.rejected} left painted (on the edge)` : '') + '.';
  window.demo = { ok: true, holes: h.kept.length };

  // Copper diff: there is no second revision of KiCad's demo, so the base is the same board without its
  // NPTH drill file; those holes show as added (green), everything else as unchanged.
  let diff = null;
  document.getElementById('diff').addEventListener('change', async (e) => {
    if (e.target.checked) {
      diff ??= await paintCopperDiff(api, gr, {
        base: readFabFiles(api, head.files.filter((f) => !/NPTH/.test(f.name))), head: board.fab,
      }, board.painted);
      board.setFaces({ top: diff.top, bottom: diff.bottom });
    } else {
      board.setFaces({ top: board.textures.top, bottom: board.textures.bottom });
    }
    draw();
  });
} catch (e) {
  status.textContent = `Failed: ${e.message}`;
  window.demo = { ok: false, error: String(e.stack || e) };
}
for (const b of document.querySelectorAll('[data-view]')) b.addEventListener('click', () => view(b.dataset.view));
