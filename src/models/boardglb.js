// A board GLB written by boarddd.step.glb (python): one node per placed instance, one mesh per
// distinct product shared by its instances, a primitive per colour, Draco or quantized, one file per
// level of detail. Loaded into the STEP frame (mm, z up) in the same shape as stepToObject(...,
// {center: false}): a Group per instance at the origin, its meshes carrying the placement, so a host
// that reads STEP files can take either.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { stepMaterial } from './step.js';

export const BOARD_GLB_ROOT = 'boarddd-board';

async function bytesOf(source, signal, onProgress) {
  if (source instanceof ArrayBuffer) return source;
  if (source instanceof Uint8Array) return source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength);
  const res = await fetch(source, { signal, credentials: 'same-origin' });
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${source}`);
  const total = Number(res.headers.get('X-Content-Length') || res.headers.get('Content-Length')) || 0;
  if (!onProgress || !res.body) return res.arrayBuffer();
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(received, total);
  }
  const out = new Uint8Array(received);
  let at = 0;
  for (const chunk of chunks) { out.set(chunk, at); at += chunk.length; }
  return out.buffer;
}

async function parse(source, { dracoLoader = null, signal, onProgress } = {}) {
  const buffer = await bytesOf(source, signal, onProgress);
  const loader = new GLTFLoader();
  if (dracoLoader) loader.setDRACOLoader(dracoLoader);
  return new Promise((resolve, reject) => {
    loader.parse(buffer, '', resolve, (e) => reject(e instanceof Error ? e : new Error(String(e?.message || e))));
  });
}

/** `mesh:primitive` of a loaded three Mesh, the same in every level of detail of one board. */
function lodKey(parser, mesh) {
  const found = parser?.associations?.get(mesh);
  if (!found || found.meshes === undefined) return null;
  return `${found.meshes}:${found.primitives ?? 0}`;
}

/**
 * Load a board GLB. source: URL | ArrayBuffer | Uint8Array.
 * opts: dracoLoader (three's DRACOLoader with its decoder path set, for a Draco-compressed file:
 *   boarddd does not import it, so hosts that never load one vendor nothing more), polygonOffset,
 *   signal, onProgress(received, total) while downloading.
 * Resolves to {object, nodes: [{name, product, group}], meta, triangles}: `object` is a Group in the
 * STEP frame (mm, z up) holding one Group per instance (name = the STEP's instance name, KiCad's
 * designator; userData.product). Meshes share geometry between instances of one product and
 * materials per colour (stepMaterial of the STEP's colour, as the STEP path makes them): clone a
 * material before changing one mesh's. Each mesh's userData.lodKey ties it to the same mesh in
 * another level of detail (upgradeBoardGLB).
 */
export async function loadBoardGLB(source, opts = {}) {
  const { polygonOffset = true } = opts;
  const gltf = await parse(source, opts);
  const root = gltf.scene.getObjectByName(BOARD_GLB_ROOT);
  if (!root) throw new Error('not a boarddd board GLB (no boarddd-board root node)');
  const meta = { ...(root.userData || {}) };
  const materials = new Map();
  const materialFor = (gltfMaterial) => {
    const rgb = gltfMaterial?.userData?.step_color || null;
    const k = rgb ? rgb.join(',') : 'none';
    if (!materials.has(k)) {
      const m = stepMaterial(rgb, { polygonOffset });
      if (gltfMaterial?.transparent) { m.transparent = true; m.opacity = gltfMaterial.opacity; }
      materials.set(k, m);
    }
    return materials.get(k);
  };
  const object = new THREE.Group();
  object.name = BOARD_GLB_ROOT;
  object.userData = { meta };
  const nodes = [];
  let triangles = 0;
  for (const child of root.children.slice()) {
    root.remove(child);
    child.traverse((n) => {
      if (!n.isMesh) return;
      const old = n.material;
      n.material = materialFor(old);
      old?.dispose?.();
      n.userData.lodKey = lodKey(gltf.parser, n);
      const index = n.geometry.index;
      triangles += (index ? index.count : n.geometry.attributes.position.count) / 3;
    });
    const group = new THREE.Group();
    // The name as written: three sanitizes node names ('=>[0:1:1:3]' loses its brackets).
    const at = gltf.parser?.associations?.get(child)?.nodes;
    group.name = (at !== undefined ? gltf.parser.json.nodes[at]?.name : child.name) || '';
    group.userData.product = child.userData?.product || '';
    group.add(child);
    group.updateMatrixWorld(true);
    object.add(group);
    nodes.push({ name: group.name, product: group.userData.product, group });
  }
  object.userData.triangles = triangles;
  return { object, nodes, meta, triangles };
}

/**
 * Swap another level of detail of the same board into loaded meshes: every mesh under `roots`
 * with a userData.lodKey takes that primitive's geometry from `source`. The replaced geometries
 * are disposed. Resolves to {swapped, triangles} (triangles drawn after the swap).
 */
export async function upgradeBoardGLB(roots, source, opts = {}) {
  const gltf = await parse(source, opts);
  const byKey = new Map();
  gltf.scene.traverse((n) => {
    if (!n.isMesh) return;
    const key = lodKey(gltf.parser, n);
    if (key && !byKey.has(key)) byKey.set(key, n.geometry);
  });
  const old = new Set();
  let swapped = 0, triangles = 0;
  for (const root of [roots].flat()) {
    root.traverse((n) => {
      if (!n.isMesh) return;
      const geometry = byKey.get(n.userData.lodKey);
      if (geometry && n.geometry !== geometry) {
        old.add(n.geometry);
        n.geometry = geometry;
        swapped++;
      }
      const index = n.geometry.index;
      triangles += (index ? index.count : n.geometry.attributes.position.count) / 3;
    });
  }
  for (const g of old) g.dispose();
  // Only the geometries are taken: its materials are the loader's, never drawn.
  gltf.scene.traverse((n) => { if (n.isMesh) n.material?.dispose?.(); });
  return { swapped, triangles };
}
