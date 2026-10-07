// Test/screenshot harness (not part of the library): one board or footprint in a bare three.js scene,
// board frame z-up, orthographic top/bottom views for pixel geometry checks and a perspective iso view.
//   ?fp=<url of .kicad_mod>            buildFootprint
//   ?gerber=<dir with manifest.json>   buildGerberBoard (boarddd/gerber injected, harness-gerber.js)
//   &view=top|bottom|iso  &w=..&h=..  &bg=#rrggbb
// Sets window.harness = {ok, error, info} once the frame is drawn.
import * as THREE from 'three';
import { buildFootprint, parseKicadFootprint } from '/src/footprint/index.js';
import { buildGerberBoard } from '/src/board/index.js';
import { gerberApi } from '/test/browser/pages/harness-gerber.js';

const q = new URLSearchParams(location.search);
const W = +(q.get('w') || 800), H = +(q.get('h') || 600);
const BG = q.get('bg') || '#ff00ff';   // magenta: what shows through a hole

async function main() {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setSize(W, H);
  renderer.setPixelRatio(1);
  document.body.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(-30, -50, 100);
  scene.add(sun);
  const info = {};
  let object;
  if (q.get('fp')) {
    const fp = parseKicadFootprint(await (await fetch(q.get('fp'))).text());
    const built = buildFootprint(fp);
    object = built.group;
    info.outline = built.outline;
    info.pads = fp.pads.length;
  } else {
    const dir = q.get('gerber').replace(/\/?$/, '/');
    const manifest = await (await fetch(dir + 'manifest.json')).json();
    const files = await Promise.all(manifest.files.map(async (name) => ({ name, text: await (await fetch(dir + name)).text() })));
    const { api, renderer: gr } = await gerberApi();
    const built = await buildGerberBoard(api, gr, files, { thickness: 1.6 });
    object = built.group;
    info.outline = built.outline;
    info.holes = { kept: built.holes.kept.length, rejected: built.holes.rejected, leftOut: built.holes.leftOut };
    info.texture = built.painted.size;
  }
  scene.add(object);
  const box = new THREE.Box3().setFromObject(object);
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
  info.box = { min: box.min.toArray(), max: box.max.toArray() };
  const view = q.get('view') || 'iso';
  let camera;
  if (view === 'top' || view === 'bottom') {
    // orthographic, 1 board mm = `scale` px, centred on the box (or on &region=minX,minY,maxX,maxY)
    const r = q.get('region')?.split(',').map(Number);
    const [cx, cy, sx, sy] = r ? [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2, r[2] - r[0], r[3] - r[1]] : [c.x, c.y, s.x * 1.1, s.y * 1.1];
    const scale = Math.min(W / sx, H / sy);
    camera = new THREE.OrthographicCamera(-W / 2 / scale, W / 2 / scale, H / 2 / scale, -H / 2 / scale, 0.1, 1000);
    c.x = cx; c.y = cy;
    camera.position.set(c.x, c.y, view === 'top' ? c.z + 100 : c.z - 100);
    camera.up.set(0, 1, 0);
    camera.lookAt(c);
    info.ortho = { scale, cx: c.x, cy: c.y, mirrored: view === 'bottom', w: W, h: H };
  } else {
    camera = new THREE.PerspectiveCamera(30, W / H, 0.1, 5000);
    const d = Math.max(s.x, s.y) * 1.9;
    camera.position.set(c.x + d * 0.35, c.y - d * 0.75, c.z + d * 0.7);
    camera.up.set(0, 0, 1);
    camera.lookAt(c);
  }
  renderer.render(scene, camera);
  // Colours [r, g, b] under board-frame points (x, y, z), from the drawn frame.
  const gl = renderer.getContext();
  window.sample = (points) => points.map(([x, y, z = 0]) => {
    const v = new THREE.Vector3(x, y, z).project(camera);
    const px = Math.round(((v.x + 1) / 2) * W - 0.5), py = Math.round(((v.y + 1) / 2) * H - 0.5);   // GL rows bottom-up
    if (px < 0 || py < 0 || px >= W || py >= H) return null;
    const out = new Uint8Array(4);
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
    return [out[0], out[1], out[2]];
  });
  info.pxPerMm = camera.isOrthographicCamera ? W / (camera.right - camera.left) : null;
  window.harness = { ok: true, info };
}
main().catch((e) => { window.harness = { ok: false, error: String((e && e.stack) || e) }; });
