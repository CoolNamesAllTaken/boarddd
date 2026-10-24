// KiCad-like lighting and background, without network access. Ported from kipr
// web/library/js/view3d.js (kipr PR #14), moved from its y-up scene into the z-up board frame.

import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export const BACKGROUNDS = {
  light: ['#d2d4ea', '#8a8ca6'],
  dark: ['#3a3d4a', '#15161b'],
};

/** Vertical two-stop gradient [top, bottom] like KiCad's 3D viewer background. */
export function gradientTexture([top, bottom]) {
  const c = document.createElement('canvas');
  c.width = 2;
  c.height = 256;
  const ctx = c.getContext('2d');
  const g = ctx.createLinearGradient(0, 0, 0, c.height);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, c.width, c.height);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/**
 * Generated studio environment (PMREM of RoomEnvironment, no download) so metal pads and
 * connector shells have something to reflect. Returns the PMREM render target: use its .texture,
 * dispose() the target itself (disposing only the texture leaks GPU memory).
 */
export function roomEnvironment(renderer) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const target = pmrem.fromScene(room, 0.04);
  room.dispose?.();
  pmrem.dispose();
  return target;
}

/**
 * Even fill from every side, a weak key from above and a headlight that follows the camera
 * (call update() before each draw), so the bottom view is lit as well as the top.
 * Returns {group, headlight, update(camera, target), dispose()}.
 */
export function kicadLights() {
  const group = new THREE.Group();
  group.name = 'boarddd-lights';
  group.add(new THREE.AmbientLight(0xffffff, 0.6));
  const key = new THREE.DirectionalLight(0xffffff, 0.35);
  key.position.set(40, -50, 80);
  group.add(key);
  const headlight = new THREE.DirectionalLight(0xffffff, 1.4);
  group.add(headlight, headlight.target);
  return {
    group,
    headlight,
    update(camera, target) {
      headlight.position.copy(camera.position);
      headlight.target.position.copy(target);
      headlight.target.updateMatrixWorld();
    },
    dispose() { for (const l of group.children) l.dispose?.(); },
  };
}

const _p = new THREE.Vector3();
const _q = new THREE.Quaternion();
const _s = new THREE.Vector3();

/**
 * Lights fixed to the camera, so whatever faces the viewer is lit, the underside included: a
 * headlight from just above-left of the eye (faces still read as 3D) and a hemisphere fill whose
 * sky is the screen's up. update(camera, scene) before each draw, once per camera when one scene is
 * drawn by several; with a scene it also turns scene.environment with the camera, so metals reflect
 * the same studio from any side. Add `group` to the scene root (it must not be moved).
 * opts: headlight (2.0), fill (0.8), sky / ground colours, offset ([-0.35, 0.45, 1]: the
 * headlight's direction in camera space, x right, y up, z toward the viewer).
 * Returns {group, headlight, fill, update(camera, scene?), dispose()}.
 */
export function cameraLights(opts = {}) {
  const { headlight: headI = 2.0, fill: fillI = 0.8, sky = 0xffffff, ground = 0xb8bcc4, offset = [-0.35, 0.45, 1] } = opts;
  const dir = new THREE.Vector3(...offset).normalize();
  const group = new THREE.Group();
  group.name = 'boarddd-camera-lights';
  const fill = new THREE.HemisphereLight(sky, ground, fillI);
  const headlight = new THREE.DirectionalLight(0xffffff, headI);
  group.add(fill, headlight, headlight.target);
  return {
    group,
    headlight,
    fill,
    update(camera, scene) {
      camera.updateMatrixWorld();
      camera.matrixWorld.decompose(_p, _q, _s);
      fill.position.set(0, 1, 0).applyQuaternion(_q);
      headlight.position.copy(dir).applyQuaternion(_q);
      headlight.target.position.set(0, 0, 0);
      group.updateMatrixWorld(true);
      // The shader samples the environment at R(rotation)^T * direction: the camera's own rotation
      // puts the room (y up) in camera space, its ceiling at the top of the screen.
      if (scene?.environment && scene.environmentRotation) scene.environmentRotation.setFromQuaternion(_q);
    },
    dispose() { fill.dispose(); headlight.dispose(); },
  };
}

/**
 * The camera-relative lighting set up on a renderer and scene in one call (what createViewer's
 * lighting: 'camera' does, for pages with their own three.js scenes): cameraLights, a studio
 * environment that turns with the camera for metal reflections, and neutral tone mapping.
 * opts: cameraLights' options, environment (true), environmentIntensity (0.8), exposure (1).
 * Call update(camera) before each renderer.render; dispose() when done.
 */
export function cameraLighting(renderer, scene, opts = {}) {
  const { environment = true, environmentIntensity = 0.8, exposure = 1 } = opts;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = exposure;
  const lights = cameraLights(opts);
  scene.add(lights.group);
  const envTarget = environment ? roomEnvironment(renderer) : null;
  if (envTarget) {
    scene.environment = envTarget.texture;
    scene.environmentIntensity = environmentIntensity;
  }
  return {
    lights,
    update(camera) { lights.update(camera, scene); },
    dispose() {
      lights.group.removeFromParent();
      lights.dispose();
      if (envTarget) {
        if (scene.environment === envTarget.texture) scene.environment = null;
        envTarget.dispose();
      }
    },
  };
}
