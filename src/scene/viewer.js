// createViewer: renderer, camera, controls, KiCad-like lighting, render on demand, view cube,
// presets, capture, dispose. Works in the board frame (mm, z up, camera.up = +z).
//
// Ported from kipr web/library/js/view3d.js (lighting, tone mapping, gradient background,
// per-draw near/far), kipr web/project/pcba3d/viewer.js (render on demand, trackball, view cube
// clicks, pick) and gentoo fab/static/fab/viewer3d.js (trackball without damping, view cube).
//
// No continuous animation loop: a frame is requested only when something changed (controls,
// resize, content, a call), and the loop stops as soon as the controls are still.

import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ViewCube } from './viewcube.js';
import { BACKGROUNDS, gradientTexture, roomEnvironment, kicadLights } from './lighting.js';
import { VIEWS, fitCamera, clipPlanes } from './views.js';

const CLICK_SLOP_PX = 5;

function disposeTree(root) {
  const seen = new Set();
  root.traverse((n) => {
    n.geometry?.dispose();
    for (const m of [n.material, n.userData?.orig].flat().filter(Boolean)) {
      if (seen.has(m)) continue;
      seen.add(m);
      for (const k of Object.keys(m)) if (m[k]?.isTexture) m[k].dispose();
      m.dispose?.();
    }
  });
}

function makeControls(kind, camera, el) {
  if (kind === 'orbit') {
    const c = new OrbitControls(camera, el);
    c.enableDamping = true;
    c.dampingFactor = 0.12;
    c.screenSpacePanning = true;
    return c;
  }
  // Trackball: no poles, so the board can be turned over the edge; no coasting (staticMoving).
  const c = new TrackballControls(camera, el);
  c.staticMoving = true;
  c.rotateSpeed = 4.0;
  c.zoomSpeed = 1.6;
  c.panSpeed = 0.8;
  return c;
}

/**
 * Create a viewer in `el` (sized by the page; the canvas fills it).
 * opts: controls 'trackball' | 'orbit'; theme 'light' | 'dark'; background [top, bottom] CSS
 *   colours, one colour, or null for transparent; viewCube (true); fov (30); pixelRatio;
 *   environment (true: RoomEnvironment reflections); onPick({object, ref, point} | null);
 *   onRender(); antialias (true).
 */
export function createViewer(el, opts = {}) {
  const {
    controls: controlsKind = 'trackball', fov = 30, viewCube = true, environment = true,
    antialias = true, onPick = null, onRender = null,
  } = opts;
  let theme = opts.theme === 'dark' ? 'dark' : 'light';
  let backgroundSpec = opts.background === undefined ? 'theme' : opts.background;

  const renderer = new THREE.WebGLRenderer({ antialias, alpha: true });
  renderer.setPixelRatio(opts.pixelRatio || Math.min(globalThis.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1.0;
  const canvas = renderer.domElement;
  canvas.style.display = 'block';
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.touchAction = 'none';
  el.appendChild(canvas);

  const scene = new THREE.Scene();
  const envTarget = environment ? roomEnvironment(renderer) : null;
  if (envTarget) {
    scene.environment = envTarget.texture;
    scene.environmentIntensity = 0.15;
    if (scene.environmentRotation) scene.environmentRotation.x = Math.PI / 2;   // room's y-up -> z-up
  }
  const lights = kicadLights();
  scene.add(lights.group);
  const content = new THREE.Group();
  content.name = 'boarddd-content';
  scene.add(content);

  const camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 10000);
  camera.up.set(0, 0, 1);
  camera.position.set(0, -100, 100);

  let bgTexture = null;
  function applyBackground() {
    bgTexture?.dispose();
    bgTexture = null;
    const spec = backgroundSpec === 'theme' ? BACKGROUNDS[theme] : backgroundSpec;
    renderer.setClearColor(0x000000, spec ? 1 : 0);
    if (!spec) scene.background = null;
    else if (Array.isArray(spec)) scene.background = bgTexture = gradientTexture(spec);
    else scene.background = new THREE.Color(spec);
  }
  applyBackground();

  const cube = viewCube ? new ViewCube(theme) : null;
  const raycaster = new THREE.Raycaster();
  const listeners = { render: new Set(), view: new Set(), cube: new Set() };
  const stats = { frames: 0, requests: 0 };
  let width = 1, height = 1;
  let frame = 0, dirty = true, disposed = false;
  let controls = null;

  function requestRender() {
    if (disposed) return;
    dirty = true;
    if (!frame) { stats.requests++; frame = requestAnimationFrame(tick); }
  }
  // Keep the loop alive only while the controls report motion.
  const onControlsChange = () => { requestRender(); for (const f of listeners.view) f(); };

  function setControls(kind) {
    const target = controls ? controls.target.clone() : new THREE.Vector3();
    if (controls) { controls.removeEventListener('change', onControlsChange); controls.dispose(); }
    controls = makeControls(kind, camera, canvas);
    controls.target.copy(target);
    controls.addEventListener('change', onControlsChange);
    controls.handleResize?.();
    controls.update();
    api.controls = controls;
    requestRender();
  }

  function contentSphere() {
    const box = new THREE.Box3().setFromObject(content);
    if (box.isEmpty()) return { center: [0, 0, 0], radius: 50 };
    const s = box.getBoundingSphere(new THREE.Sphere());
    return { center: s.center.toArray(), radius: Math.max(s.radius, 0.5) };
  }

  function draw({ cubeToo = true } = {}) {
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, width, height);
    camera.aspect = width / height;
    const { near, far } = clipPlanes(camera.position.toArray(), contentSphere());
    camera.near = near;
    camera.far = far;
    camera.updateProjectionMatrix();
    lights.update(camera, controls.target);
    renderer.render(scene, camera);
    if (cube && cubeToo) cube.draw(renderer, camera, controls.target, width, height);
    stats.frames++;
    onRender?.();
    for (const f of listeners.render) f();
  }

  function tick() {
    frame = 0;
    if (disposed) return;
    // Trackball applies pointer motion here and fires 'change' (-> another frame); orbit damping
    // returns true while it is still settling.
    const moving = controls.update();
    if (moving === true) requestRender();
    if (!dirty) return;
    dirty = false;
    draw();
  }

  function resize() {
    const r = el.getBoundingClientRect();
    const w = Math.max(1, Math.floor(r.width)), h = Math.max(1, Math.floor(r.height));
    if (w === width && h === height && canvas.width) return;
    width = w; height = h;
    renderer.setSize(w, h, false);
    controls?.handleResize?.();
    requestRender();
  }

  /* Pointer: view cube hover/click, pick on click, and frames while the user interacts. */
  const local = (e) => { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  let pressed = null, cubeDown = false;
  const onDown = (e) => {
    const p = local(e);
    pressed = { x: e.clientX, y: e.clientY, id: e.pointerId };
    cubeDown = !!cube && cube.faceAt(p.x, p.y, width, height) !== null;
    // Before the controls' own listener (capture phase): a press on the cube never starts a drag.
    if (cubeDown) { e.stopImmediatePropagation(); e.preventDefault(); }
    requestRender();
  };
  const onMove = (e) => {
    if (cube) {
      const p = local(e);
      const face = e.buttons ? null : cube.faceAt(p.x, p.y, width, height);
      if (cube.setHover(face)) requestRender();
      canvas.style.cursor = face ? 'pointer' : '';
    }
    if (e.buttons) requestRender();
  };
  const onUp = (e) => {
    const press = pressed;
    pressed = null;
    requestRender();
    if (!press || press.id !== e.pointerId) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) > CLICK_SLOP_PX) return;
    const p = local(e);
    if (cubeDown) {
      cubeDown = false;
      const face = cube.faceAt(p.x, p.y, width, height);
      if (face) {
        api.setView(face.toLowerCase(), { fit: false });
        for (const f of listeners.cube) f(face.toLowerCase());
      }
      return;
    }
    if (onPick) onPick(api.pick(e.clientX, e.clientY));
  };
  const onLeave = () => { if (cube?.setHover(null)) requestRender(); };
  const onWheel = () => requestRender();
  canvas.addEventListener('pointerdown', onDown, true);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('wheel', onWheel, { passive: true });
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => resize()) : null;
  resizeObserver?.observe(el);

  const api = {
    renderer, scene, camera, content, canvas, controls: null, stats,
    get theme() { return theme; },

    /** Add objects (board-frame) to the content; returns the first. */
    add(...objects) { content.add(...objects); requestRender(); return objects[0]; },
    /** Remove objects; dispose: true frees their GPU memory too. */
    remove(object, { dispose = false } = {}) { content.remove(object); if (dispose) disposeTree(object); requestRender(); },
    /** Remove everything from the content (disposing it unless dispose: false). */
    clear({ dispose = true } = {}) {
      for (const o of content.children.slice()) { content.remove(o); if (dispose) disposeTree(o); }
      requestRender();
    },

    requestRender,
    /** Draw now, synchronously (e.g. before reading pixels). */
    render() { draw(); },

    /**
     * Look from a preset ('top' | 'bottom' | 'front' | 'side' | 'back' | 'left' | 'right' | 'iso' |
     * 'isoBottom') or a {dir, up} pair. fit (default true) frames the content (or `box`); with
     * fit: false the current distance and target are kept.
     */
    setView(view, { fit = true, box = null, pad = 1.05 } = {}) {
      const v = typeof view === 'string' ? VIEWS[view] : view;
      if (!v) throw new Error(`unknown view ${view}`);
      if (fit) return api.fit(v, box, pad);
      const d = camera.position.distanceTo(controls.target) || 100;
      camera.position.copy(controls.target).addScaledVector(new THREE.Vector3(...v.dir).normalize(), d);
      camera.up.set(...v.up);
      camera.lookAt(controls.target);
      controls.update();
      onControlsChange();
      return api;
    },

    /** Frame `box` (default: the content) from view `v` (default: the current direction). */
    fit(v = null, box = null, pad = 1.05) {
      if (typeof v === 'string') v = VIEWS[v];
      const b = box || new THREE.Box3().setFromObject(content);
      if (b.isEmpty()) b.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(50, 50, 2));
      const view = v || { dir: camera.position.clone().sub(controls.target).toArray(), up: camera.up.toArray() };
      const f = fitCamera({ min: b.min.toArray(), max: b.max.toArray() }, view, camera.fov, width / height, pad);
      controls.target.set(...f.target);
      camera.position.set(...f.position);
      camera.up.set(...f.up);
      camera.lookAt(controls.target);
      controls.update();
      onControlsChange();
      return api;
    },

    setTheme(t) {
      theme = t === 'dark' ? 'dark' : 'light';
      cube?.setTheme(theme);
      applyBackground();
      requestRender();
    },
    /** [top, bottom] gradient, one colour, null (transparent) or 'theme'. */
    setBackground(spec) { backgroundSpec = spec; applyBackground(); requestRender(); },
    setControls,

    /** Content object under a client point: {object, ref, point} (ref: nearest userData.ref up the tree) or null. */
    pick(clientX, clientY) {
      const r = canvas.getBoundingClientRect();
      const ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
      raycaster.setFromCamera(ndc, camera);
      const hit = raycaster.intersectObject(content, true).find((h) => {
        for (let o = h.object; o; o = o.parent) if (!o.visible) return false;
        return true;
      });
      if (!hit) return null;
      let ref = null;
      for (let o = hit.object; o && ref === null; o = o.parent) ref = o.userData?.ref ?? null;
      return { object: hit.object, ref, point: hit.point.toArray() };
    },

    /**
     * The view cube under a client point: a face name ('top', ...), '' for the cube's corner but not
     * the cube, null elsewhere (or without a cube). For hosts that must not treat it as the scene.
     */
    cubeAt(clientX, clientY) {
      if (!cube) return null;
      const p = local({ clientX, clientY });
      const face = cube.faceAt(p.x, p.y, width, height);
      return face ? face.toLowerCase() : face;
    },

    /** Canvas point (CSS px) of a view cube face's middle, or null. */
    cubeFacePoint(face) {
      if (!cube) return null;
      draw();
      return cube.facePoint(face[0].toUpperCase() + face.slice(1), width, height);
    },

    /**
     * PNG (or `type`) data URL of the view. width/height: output pixels (default: the canvas);
     * transparent: no background; viewCube: include the cube (default false).
     */
    capture({ width: w = null, height: h = null, transparent = false, viewCube: withCube = false, type = 'image/png' } = {}) {
      const saved = { ratio: renderer.getPixelRatio(), width, height, background: scene.background };
      if (w && h) { renderer.setPixelRatio(1); width = w; height = h; renderer.setSize(w, h, false); }
      if (transparent) { scene.background = null; renderer.setClearColor(0x000000, 0); }
      draw({ cubeToo: withCube });
      const url = canvas.toDataURL(type);
      if (transparent) { scene.background = saved.background; renderer.setClearAlpha(saved.background ? 1 : 0); }
      if (w && h) {
        renderer.setPixelRatio(saved.ratio);
        width = saved.width; height = saved.height;
        renderer.setSize(width, height, false);
      }
      requestRender();
      return url;
    },

    /** Same as capture(), as a Blob. */
    async captureBlob(o = {}) {
      return (await fetch(api.capture(o))).blob();
    },

    resize,

    /** 'render' (after each frame), 'view' (camera moved) or 'cube' (a cube face was clicked; fn(face)); returns an unsubscribe function. */
    on(event, fn) { listeners[event].add(fn); return () => listeners[event].delete(fn); },

    /** Stop drawing, free every GL resource (content included unless content: false) and remove the canvas. */
    dispose({ content: freeContent = true } = {}) {
      if (disposed) return;
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      resizeObserver?.disconnect();
      canvas.removeEventListener('pointerdown', onDown, true);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointerleave', onLeave);
      canvas.removeEventListener('wheel', onWheel);
      controls.removeEventListener('change', onControlsChange);
      controls.dispose();
      if (freeContent) disposeTree(content);
      scene.remove(content);
      lights.dispose();
      envTarget?.dispose();
      bgTexture?.dispose();
      cube?.dispose();
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
      for (const s of Object.values(listeners)) s.clear();
    },
  };

  setControls(controlsKind);
  resize();
  api.setView('iso');
  return api;
}
