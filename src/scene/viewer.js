// createViewer: renderer, camera, controls, KiCad-like lighting, render on demand, view cube,
// presets, capture, dispose. Works in the board frame (mm, z up, camera.up = +z).
//
// Ported from kipr web/library/js/view3d.js (lighting, tone mapping, gradient background,
// per-draw near/far), kipr web/project/pcba3d/viewer.js (render on demand, trackball, view cube
// clicks, pick) and gentoo fab/static/fab/viewer3d.js (trackball without damping, view cube).
//
// No continuous animation loop: a frame is requested only when something changed (controls,
// resize, content, a call), and the loop stops as soon as the controls are still.
//
// Panes (setPanes): the canvas split into side-by-side views drawn with the ONE camera, e.g. a
// base and a head revision, so the two can never drift apart and a drag anywhere turns both
// (kipr web/project/pcba3d/viewer.js and web/library/js/view3d.js side-by-side modes).

import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ViewCube } from './viewcube.js';
import { BACKGROUNDS, gradientTexture, roomEnvironment, kicadLights } from './lighting.js';
import { VIEWS, fitCamera, clipPlanes } from './views.js';

const CLICK_SLOP_PX = 5;
const FLY_MS = 380;
const FOCUS_PAD = 1.4;
const LONG_PRESS_MS = 550;

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
 *   onContextPick(hit | null, {clientX, clientY, pointerType}) for a right click or a touch
 *   long-press; onRender(); antialias (true); focusOnPick (true): a click on content with a
 *   userData.ref makes it the orbit centre (focus); a click on nothing keeps the centre.
 */
export function createViewer(el, opts = {}) {
  const {
    controls: controlsKind = 'trackball', fov = 30, viewCube = true, environment = true,
    antialias = true, onPick = null, onContextPick = null, onRender = null, preserveDrawingBuffer = false,
    focusOnPick = true,
  } = opts;
  let theme = opts.theme === 'dark' ? 'dark' : 'light';
  let backgroundSpec = opts.background === undefined ? 'theme' : opts.background;

  const renderer = new THREE.WebGLRenderer({ antialias, alpha: true, preserveDrawingBuffer });
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
  const listeners = { render: new Set(), view: new Set(), cube: new Set(), context: new Set() };
  const stats = { frames: 0, requests: 0 };
  let width = 1, height = 1;
  let frame = 0, dirty = true, disposed = false;
  let controls = null;
  let panes = null;              // null, or [[Object3D, ...], ...]: what only that pane shows
  let flight = null;             // the camera move flyTo is making, while it makes it

  /** Pane i's rectangle in CSS px (x from the left), or the whole canvas. */
  function paneRect(i) {
    if (!panes) return { x: 0, width };
    const x0 = Math.round((i * width) / panes.length), x1 = Math.round(((i + 1) * width) / panes.length);
    return { x: x0, width: Math.max(1, x1 - x0) };
  }
  const aspect = () => paneRect(0).width / height;
  /** Show only pane i's own objects (plus everything not in any pane); returns a restore function. */
  function showPane(i) {
    if (!panes) return () => {};
    const saved = [];
    panes.forEach((list, k) => {
      if (k === i) return;
      for (const o of list) if (!panes[i].includes(o)) { saved.push([o, o.visible]); o.visible = false; }
    });
    return () => { for (const [o, v] of saved) o.visible = v; };
  }

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
    const { near, far } = clipPlanes(camera.position.toArray(), contentSphere());
    camera.near = near;
    camera.far = far;
    lights.update(camera, controls.target);
    if (!panes) {
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    } else {
      renderer.setScissorTest(true);
      panes.forEach((_, i) => {
        const r = paneRect(i);
        renderer.setViewport(r.x, 0, r.width, height);
        renderer.setScissor(r.x, 0, r.width, height);
        camera.aspect = r.width / height;
        camera.updateProjectionMatrix();
        const restore = showPane(i);
        renderer.render(scene, camera);
        restore();
      });
      renderer.setScissorTest(false);
      renderer.setViewport(0, 0, width, height);
    }
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
  // Context: a right click, or a touch held still for LONG_PRESS_MS. One gesture can raise both
  // (mobile Chrome sends contextmenu for a long-press), so a second within a moment is dropped.
  let longPress = null, contextAt = -Infinity, deferredContext = null;
  const fireContext = (clientX, clientY, pointerType) => {
    const now = performance.now();
    if (now - contextAt < 700) return;
    contextAt = now;
    const hit = api.pick(clientX, clientY);
    const where = { clientX, clientY, pointerType };
    onContextPick?.(hit, where);
    for (const f of listeners.context) f(hit, where);
  };
  const cancelLongPress = () => { if (longPress) { clearTimeout(longPress.timer); longPress = null; } };
  const onDown = (e) => {
    const p = local(e);
    if (pressed && pressed.id !== e.pointerId) cancelLongPress();   // a second finger: a pinch
    if (flight) { cancelAnimationFrame(flight.frame); flight.done(); }   // taking hold stops a flight
    pressed = { x: e.clientX, y: e.clientY, id: e.pointerId, button: e.button, long: false };
    cubeDown = !!cube && cube.faceAt(p.x, p.y, width, height) !== null;
    // Before the controls' own listener (capture phase): a press on the cube never starts a drag.
    if (cubeDown) { e.stopImmediatePropagation(); e.preventDefault(); }
    else if (e.pointerType === 'touch' || e.pointerType === 'pen') {
      cancelLongPress();
      const press = pressed;
      longPress = { id: e.pointerId, timer: setTimeout(() => {
        longPress = null;
        if (pressed !== press) return;
        press.long = true;
        fireContext(press.x, press.y, e.pointerType);
      }, LONG_PRESS_MS) };
    }
    requestRender();
  };
  const onMove = (e) => {
    if (longPress && longPress.id === e.pointerId && pressed
        && Math.hypot(e.clientX - pressed.x, e.clientY - pressed.y) > CLICK_SLOP_PX) cancelLongPress();
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
    cancelLongPress();
    requestRender();
    if (!press || press.id !== e.pointerId) return;
    const still = Math.hypot(e.clientX - press.x, e.clientY - press.y) <= CLICK_SLOP_PX;
    if (deferredContext) {
      const asked = deferredContext;
      deferredContext = null;
      if (still) fireContext(asked.clientX, asked.clientY, e.pointerType || 'mouse');
      return;
    }
    // A long-press was the context gesture, not a click; a right button never picks.
    if (!still || press.long || press.button === 2) return;
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
    const hit = (onPick || focusOnPick) ? api.pick(e.clientX, e.clientY) : null;
    if (focusOnPick && hit && hit.ref !== null) api.focus(hit.ref);
    if (onPick) onPick(hit);
  };
  const onLeave = () => { if (cube?.setHover(null)) requestRender(); };
  const onCancel = () => { cancelLongPress(); deferredContext = null; };
  // Some systems send contextmenu as the right button goes down (a drag that pans may follow:
  // wait for the release), others after it comes up (fire now).
  const onContextMenu = (e) => {
    e.preventDefault();
    if (pressed && pressed.button === 2) { deferredContext = { clientX: e.clientX, clientY: e.clientY }; return; }
    if (pressed && pressed.long) return;
    fireContext(e.clientX, e.clientY, pressed ? 'touch' : 'mouse');
  };
  const onWheel = () => requestRender();
  canvas.addEventListener('pointerdown', onDown, true);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('pointercancel', onCancel);
  canvas.addEventListener('contextmenu', onContextMenu);
  canvas.addEventListener('wheel', onWheel, { passive: true });
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => resize()) : null;
  resizeObserver?.observe(el);

  /** Ease target, position and up to `end` over `duration` ms (0, or reduced motion: at once). */
  function fly(end, duration) {
    const start = { target: controls.target.clone(), position: camera.position.clone(), up: camera.up.clone() };
    if (flight) { cancelAnimationFrame(flight.frame); flight.done(); }
    const still = duration <= 0 || globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    return new Promise((resolve) => {
      const t0 = performance.now();
      const me = { frame: 0, done: () => { if (flight === me) flight = null; resolve(api); } };
      flight = me;
      const step = () => {
        const k = still ? 1 : Math.min(1, (performance.now() - t0) / duration);
        const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
        controls.target.lerpVectors(start.target, end.target, e);
        // Along the arc rather than the chord, so the distance eases too and nothing clips through.
        const from = start.position.clone().sub(start.target), to = end.position.clone().sub(end.target);
        const length = from.length() + (to.length() - from.length()) * e;
        const way = from.normalize().lerp(to.normalize(), e);
        if (way.lengthSq() < 1e-9) way.copy(to);
        camera.position.copy(controls.target).addScaledVector(way.normalize(), length);
        camera.up.lerpVectors(start.up, end.up, e).normalize();
        camera.lookAt(controls.target);
        controls.update();
        onControlsChange();
        if (k < 1 && flight === me) me.frame = requestAnimationFrame(step);
        else me.done();
      };
      step();
    });
  }

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
      const f = fitCamera({ min: b.min.toArray(), max: b.max.toArray() }, view, camera.fov, aspect(), pad);
      controls.target.set(...f.target);
      camera.position.set(...f.position);
      camera.up.set(...f.up);
      camera.lookAt(controls.target);
      controls.update();
      onControlsChange();
      return api;
    },

    /**
     * Ease the camera to frame `box` (a Box3 or {min, max}), keeping the current direction unless
     * `view` says otherwise. `minSize` (mm) keeps a tiny part from filling the screen; a pointer
     * press, another flyTo or `duration: 0` (and prefers-reduced-motion) cut it short. Resolves
     * when the camera is there.
     */
    flyTo(box, { duration = FLY_MS, pad = 1.6, minSize = 0, view = null } = {}) {
      const b = box?.isBox3 ? box.clone() : new THREE.Box3(new THREE.Vector3(...box.min), new THREE.Vector3(...box.max));
      if (b.isEmpty()) return Promise.resolve(api);
      if (minSize > 0) {
        const size = b.getSize(new THREE.Vector3()), mid = b.getCenter(new THREE.Vector3());
        b.setFromCenterAndSize(mid, size.max(new THREE.Vector3(minSize, minSize, Math.min(minSize, size.z || minSize))));
      }
      const v = typeof view === 'string' ? VIEWS[view] : view;
      const dir = v || { dir: camera.position.clone().sub(controls.target).toArray(), up: camera.up.toArray() };
      const f = fitCamera({ min: b.min.toArray(), max: b.max.toArray() }, dir, camera.fov, aspect(), pad);
      return fly({ target: new THREE.Vector3(...f.target), position: new THREE.Vector3(...f.position), up: new THREE.Vector3(...f.up) }, duration);
    },

    /**
     * Orbit around a part: the orbit centre (controls.target) moves to the middle of `what`'s
     * bounding box, eased, and the camera keeps its direction and distance; it backs off only when
     * the part would not fit on screen from there. `what`: a ref (every content object whose
     * userData.ref is it, case-insensitive as a fallback), an Object3D or an array of them, a Box3
     * or {min, max}; null goes back to the content's centre. opts: animate (true), duration,
     * frame ('auto'; true always frames it, like flyTo; false never changes the distance), pad
     * (1.4), minSize (mm, with frame: true), view (a direction to look from instead of the current
     * one). Resolves with the viewer once there, or with null when `what` matched nothing (the
     * camera does not move).
     */
    focus(what, { animate = true, duration = FLY_MS, frame = 'auto', pad = FOCUS_PAD, minSize = 0, view = null } = {}) {
      const b = what === null || what === undefined ? new THREE.Box3().setFromObject(content) : api.boxOf(what);
      if (!b || b.isEmpty()) return Promise.resolve(null);
      if (frame === true && minSize > 0) {
        const size = b.getSize(new THREE.Vector3()), mid = b.getCenter(new THREE.Vector3());
        b.setFromCenterAndSize(mid, size.max(new THREE.Vector3(minSize, minSize, Math.min(minSize, size.z || minSize))));
      }
      const v = typeof view === 'string' ? VIEWS[view] : view;
      const offset = camera.position.clone().sub(controls.target);
      const dir = v || { dir: offset.toArray(), up: camera.up.toArray() };
      const f = fitCamera({ min: b.min.toArray(), max: b.max.toArray() }, dir, camera.fov, aspect(), pad);
      const kept = offset.length() || f.distance;
      const distance = frame === true ? f.distance : frame === false ? kept : Math.max(kept, f.distance);
      const back = new THREE.Vector3(...dir.dir).normalize();
      const target = new THREE.Vector3(...f.target);
      const end = { target, position: target.clone().addScaledVector(back, distance), up: new THREE.Vector3(...f.up) };
      return fly(end, animate ? duration : 0);
    },

    /**
     * World bounding box of a ref (every content object with that userData.ref; the visible ones
     * when any are), an Object3D, an array of them, or a Box3 / {min, max}; null when nothing matches.
     */
    boxOf(what) {
      if (what?.isBox3) return what.clone();
      if (what && Array.isArray(what.min) && Array.isArray(what.max)) return new THREE.Box3(new THREE.Vector3(...what.min), new THREE.Vector3(...what.max));
      let list;
      if (typeof what === 'string') {
        const find = (same) => { const out = []; content.traverse((n) => { if (n.userData?.ref != null && same(String(n.userData.ref))) out.push(n); }); return out; };
        list = find((r) => r === what);
        if (!list.length) list = find((r) => r.toUpperCase() === what.toUpperCase());
        const shown = list.filter((n) => { for (let o = n; o; o = o.parent) if (!o.visible) return false; return true; });
        if (shown.length) list = shown;
      } else list = [what].flat().filter((o) => o?.isObject3D);
      const box = new THREE.Box3();
      for (const o of list) box.union(new THREE.Box3().setFromObject(o));
      return box.isEmpty() ? null : box;
    },

    /**
     * PNG data URL of `objects` alone, framed: everything else in the content is hidden for the
     * frame, then put back with the camera. For a picture of one part (a catalog image, a
     * thumbnail). opts: width / height (512), view (the current direction, or a preset name),
     * pad (1.15), transparent (true), type.
     */
    captureObjects(objects, { width: w = 512, height: h = 512, view = null, pad = 1.15, transparent = true, type = 'image/png' } = {}) {
      const list = [objects].flat().filter(Boolean);
      if (!list.length) return null;
      const keep = new Set();
      for (const o of list) {
        o.traverse((n) => keep.add(n));
        for (let up = o.parent; up; up = up.parent) keep.add(up);
      }
      const saved = [];
      content.traverse((n) => {
        const want = keep.has(n);
        if (n.visible !== want) { saved.push([n, n.visible]); n.visible = want; }
      });
      const pose = { target: controls.target.clone(), position: camera.position.clone(), up: camera.up.clone() };
      const box = new THREE.Box3();
      for (const o of list) box.expandByObject(o);
      let url = null;
      try {
        if (!box.isEmpty()) {
          const v = typeof view === 'string' ? VIEWS[view] : view;
          const dir = v || { dir: camera.position.clone().sub(controls.target).toArray(), up: camera.up.toArray() };
          const f = fitCamera({ min: box.min.toArray(), max: box.max.toArray() }, dir, camera.fov, w / h, pad);
          controls.target.set(...f.target);
          camera.position.set(...f.position);
          camera.up.set(...f.up);
          camera.lookAt(controls.target);
          url = api.capture({ width: w, height: h, transparent, type });
        }
      } finally {
        for (const [n, was] of saved) n.visible = was;
        controls.target.copy(pose.target);
        camera.position.copy(pose.position);
        camera.up.copy(pose.up);
        camera.lookAt(controls.target);
        controls.update();
        requestRender();
      }
      return url;
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

    /**
     * Content object under a client point: {object, ref, point, pane} (ref: nearest userData.ref up
     * the tree; pane: the index of the pane under the point, 0 without panes) or null. Only what that
     * pane shows can be hit. `filter(object)` may narrow the candidates further.
     */
    pick(clientX, clientY, { filter = null } = {}) {
      const r = canvas.getBoundingClientRect();
      const px = ((clientX - r.left) / Math.max(r.width, 1)) * width;
      let pane = 0;
      if (panes) while (pane < panes.length - 1 && px >= paneRect(pane + 1).x) pane++;
      const pr = paneRect(pane);
      const ndc = new THREE.Vector2(((px - pr.x) / pr.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
      camera.aspect = pr.width / height;
      camera.updateProjectionMatrix();
      raycaster.setFromCamera(ndc, camera);
      const restore = showPane(pane);
      const hit = raycaster.intersectObject(content, true).find((h) => {
        for (let o = h.object; o; o = o.parent) if (!o.visible) return false;
        return !filter || filter(h.object);
      });
      restore();
      if (!hit) return null;
      let ref = null;
      for (let o = hit.object; o && ref === null; o = o.parent) ref = o.userData?.ref ?? null;
      return { object: hit.object, ref, point: hit.point.toArray(), pane };
    },

    /**
     * Split the view into side-by-side panes drawn with the one camera: `list` is an array (left to
     * right) of arrays of content objects that only that pane shows; objects in no pane show in
     * every pane. null (or fewer than two panes) goes back to one view. Keeps the camera; call fit()
     * to reframe for the new aspect.
     */
    setPanes(list) {
      panes = Array.isArray(list) && list.length > 1 ? list.map((l) => (Array.isArray(l) ? l.filter(Boolean) : [l].filter(Boolean))) : null;
      requestRender();
      return api;
    },
    get panes() { return panes ? panes.map((l) => l.slice()) : null; },
    /** Pane i's rectangle in canvas CSS px: {x, y, width, height}. */
    paneRect(i = 0) { const r = paneRect(i); return { x: r.x, y: 0, width: r.width, height }; },

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

    /**
     * 'render' (after each frame), 'view' (camera moved), 'cube' (a cube face was clicked; fn(face))
     * or 'context' (right click / long-press; fn(hit | null, {clientX, clientY, pointerType}));
     * returns an unsubscribe function.
     */
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
      canvas.removeEventListener('pointercancel', onCancel);
      canvas.removeEventListener('contextmenu', onContextMenu);
      canvas.removeEventListener('wheel', onWheel);
      cancelLongPress();
      if (flight) cancelAnimationFrame(flight.frame);
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
