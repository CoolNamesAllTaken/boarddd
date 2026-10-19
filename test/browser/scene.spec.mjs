import { test, expect } from '@playwright/test';

async function open(page, query = '') {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`/test/browser/pages/scene.html${query}`);
  await page.waitForFunction(() => window.ready, null, { timeout: 90_000 });
  expect(await page.evaluate(() => window.errors)).toEqual([]);
  return errors;
}

// Mean RGB of the middle 20 % of the canvas, after a synchronous render.
const centre = (page) => page.evaluate(() => {
  window.v.render();
  const src = window.v.canvas;
  const c = document.createElement('canvas');
  c.width = src.width; c.height = src.height;
  const ctx = c.getContext('2d');
  ctx.drawImage(src, 0, 0);
  const w = Math.floor(c.width * 0.2), h = Math.floor(c.height * 0.2);
  const d = ctx.getImageData((c.width - w) >> 1, (c.height - h) >> 1, w, h).data;
  const s = [0, 0, 0];
  for (let i = 0; i < d.length; i += 4) { s[0] += d[i]; s[1] += d[i + 1]; s[2] += d[i + 2]; }
  return s.map((x) => Math.round(x / (d.length / 4)));
});

// As kipr tests/library/viewer/color_check.py: the RP2040-Zero board colour (0.090/0.224/0.420),
// which KiCad's 3D viewer shows as a mid blue, must read clearly blue from both sides.
test('lighting: a blue STEP board (via occt in a Worker) reads blue from top and bottom', async ({ page }) => {
  const errors = await open(page, '?model=step');
  const out = {};
  for (const view of ['top', 'bottom']) {
    await page.evaluate((name) => window.v.setView(name), view);
    const [r, g, b] = await centre(page);
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    out[view] = [r, g, b];
    expect(b, `${view} ${r},${g},${b}`).toBeGreaterThan(r + 40);
    expect(b, `${view} ${r},${g},${b}`).toBeGreaterThan(g + 20);
    expect(lum, `${view} ${r},${g},${b}`).toBeGreaterThan(80);
  }
  console.log('colour', JSON.stringify(out));
  expect(errors).toEqual([]);
});

test('render on demand: no frames while idle; a drag draws, then it stops again', async ({ page }) => {
  await open(page);
  await page.waitForTimeout(500);
  const idle0 = await page.evaluate(() => ({ raf: window.rafCalls, frames: window.v.stats.frames }));
  await page.waitForTimeout(1500);
  const idle1 = await page.evaluate(() => ({ raf: window.rafCalls, frames: window.v.stats.frames }));
  expect(idle1).toEqual(idle0);

  const box = await page.locator('#v canvas').boundingBox();
  await page.mouse.move(box.x + 200, box.y + 300);
  await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(box.x + 200 + i * 15, box.y + 300 - i * 5);
  await page.mouse.up();
  await page.waitForTimeout(300);
  const after = await page.evaluate(() => ({ raf: window.rafCalls, frames: window.v.stats.frames }));
  expect(after.frames).toBeGreaterThan(idle1.frames);
  await page.waitForTimeout(1000);
  const later = await page.evaluate(() => ({ raf: window.rafCalls, frames: window.v.stats.frames }));
  expect(later).toEqual(after);

  // Wheel zoom also draws on demand.
  await page.mouse.move(box.x + 300, box.y + 250);
  await page.mouse.wheel(0, -200);
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => window.v.stats.frames)).toBeGreaterThan(later.frames);
});

test('orbit controls with damping settle and stop requesting frames', async ({ page }) => {
  await open(page, '?controls=orbit');
  const box = await page.locator('#v canvas').boundingBox();
  await page.mouse.move(box.x + 200, box.y + 300);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) await page.mouse.move(box.x + 200 + i * 20, box.y + 300);
  await page.mouse.up();
  // Damping coasts for ~100 frames (slow under SwiftShader), then the loop must stop for good.
  let a = -1, b = await page.evaluate(() => window.rafCalls);
  for (let i = 0; i < 20 && a !== b; i++) {
    a = b;
    await page.waitForTimeout(1000);
    b = await page.evaluate(() => window.rafCalls);
  }
  expect(b).toBe(a);
  await page.waitForTimeout(1000);
  expect(await page.evaluate(() => window.rafCalls)).toBe(b);
});

test('view cube: clicking faces looks from that side', async ({ page }) => {
  await open(page);
  const box = await page.locator('#v canvas').boundingBox();
  const dir = () => page.evaluate(() => {
    const { camera, controls } = window.v;
    return camera.position.clone().sub(controls.target).normalize().toArray().map((x) => Math.round(x * 100) / 100);
  });
  await page.evaluate(() => { window.cubeClicks = []; window.v.on('cube', (f) => window.cubeClicks.push(f)); });
  for (const [face, want] of [['top', [0, 0, 1]], ['front', [0, -1, 0]], ['right', [1, 0, 0]]]) {
    // From iso, these three faces are visible.
    await page.evaluate(() => window.v.setView('iso'));
    const p = await page.evaluate((f) => window.v.cubeFacePoint(f), face);
    expect(p, face).not.toBeNull();
    expect(await page.evaluate(([x, y]) => window.v.cubeAt(x, y), [box.x + p.x, box.y + p.y])).toBe(face);
    const before = await page.evaluate(() => window.v.stats.frames);
    await page.mouse.click(box.x + p.x, box.y + p.y);
    await page.waitForTimeout(200);
    expect((await dir()).map((x) => x + 0)).toEqual(want);
    expect(await page.evaluate(() => window.v.stats.frames)).toBeGreaterThan(before);
  }
  expect(await page.evaluate(() => window.cubeClicks)).toEqual(['top', 'front', 'right']);
  // From the top, the bottom face is hidden, and a click outside the cube does not change the view.
  expect(await page.evaluate(([x, y]) => window.v.cubeAt(x, y), [box.x + 20, box.y + 20])).toBeNull();
  await page.mouse.click(box.x + 20, box.y + 20);
  await page.waitForTimeout(200);
  expect((await dir()).map((x) => x + 0)).toEqual([1, 0, 0]);
});

test('pick: a click on a part reports its ref', async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.v.setView('top'));
  const hit = await page.evaluate(() => {
    const { camera, canvas, THREE } = { ...window.v, THREE: window.THREE };
    const p = new THREE.Vector3(10, 10, 3.6).project(camera);
    const r = canvas.getBoundingClientRect();
    return window.v.pick(r.left + ((p.x + 1) / 2) * r.width, r.top + ((1 - p.y) / 2) * r.height);
  });
  expect(hit.ref).toBe('U1');
});

test('capture: PNG at the requested size, transparent when asked', async ({ page }) => {
  await open(page);
  const out = await page.evaluate(async () => {
    const url = window.v.capture({ width: 320, height: 200, transparent: true });
    const img = new Image();
    img.src = url;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0);
    return { w: img.width, h: img.height, corner: [...ctx.getImageData(1, 1, 1, 1).data], png: url.startsWith('data:image/png') };
  });
  expect(out).toEqual({ w: 320, h: 200, corner: [0, 0, 0, 0], png: true });
  // The live canvas is back at its own size.
  expect(await page.evaluate(() => [window.v.canvas.width, window.v.canvas.height].join('x'))).toMatch(/^\d+x\d+$/);
});

test('theme: dark background is darker than light', async ({ page }) => {
  await open(page);
  const corner = () => page.evaluate(() => {
    window.v.render();
    const c = document.createElement('canvas');
    c.width = 4; c.height = 4;
    const ctx = c.getContext('2d');
    ctx.drawImage(window.v.canvas, 0, 0, 4, 4, 0, 0, 4, 4);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return d[0] + d[1] + d[2];
  });
  const light = await corner();
  await page.evaluate(() => window.v.setTheme('dark'));
  expect(await corner()).toBeLessThan(light - 150);
});

test('dispose frees GL, removes the canvas and stops drawing', async ({ page }) => {
  await open(page, '?model=step');
  const r = await page.evaluate(async () => {
    const { v } = window;
    const gl = v.renderer.getContext();
    const before = { ...v.renderer.info.memory };
    v.dispose();
    const mem = { ...v.renderer.info.memory };
    const raf = window.rafCalls;
    await new Promise((res) => setTimeout(res, 300));
    v.requestRender();
    await new Promise((res) => setTimeout(res, 300));
    return { before, mem, lost: gl.isContextLost(), attached: document.contains(v.canvas), rafDelta: window.rafCalls - raf };
  });
  expect(r.before.geometries).toBeGreaterThan(0);
  expect(r.mem.geometries).toBe(0);
  // three's PMREMGenerator keeps one internal texture after dispose(); the context loss frees it.
  expect(r.mem.textures).toBeLessThanOrEqual(1);
  expect(r.lost).toBe(true);
  expect(r.attached).toBe(false);
  expect(r.rafDelta).toBe(0);
});

test('panes: one camera, each pane shows only its own objects; pick knows the pane', async ({ page }) => {
  await open(page);
  const out = await page.evaluate(() => {
    const { v, THREE } = window;
    v.clear();
    const box = (color, ref) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(20, 20, 2), new THREE.MeshBasicMaterial({ color }));
      m.position.set(0, 0, 1);
      m.userData.ref = ref;
      return m;
    };
    const left = box(0xff0000, 'L'), right = box(0x00ff00, 'R');
    const shared = new THREE.Mesh(new THREE.BoxGeometry(60, 60, 0.5), new THREE.MeshBasicMaterial({ color: 0x0000ff }));
    v.add(shared, left, right);
    v.setPanes([[left], [right]]);
    v.setView('top');
    v.render();
    const src = v.canvas;
    const c = document.createElement('canvas');
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(src, 0, 0);
    const px = (fx, fy) => [...ctx.getImageData(Math.floor(c.width * fx), Math.floor(c.height * fy), 1, 1).data].slice(0, 3);
    const r = src.getBoundingClientRect();
    const at = (fx) => v.pick(r.left + r.width * fx, r.top + r.height / 2);
    const res = {
      leftMid: px(0.25, 0.5), rightMid: px(0.75, 0.5),
      pickLeft: at(0.25), pickRight: at(0.75),
      rects: [v.paneRect(0), v.paneRect(1)],
      visible: [left.visible, right.visible],
    };
    v.setPanes(null);
    v.render();
    res.single = v.panes;
    res.pickSingle = at(0.5);
    return { ...res, pickLeft: res.pickLeft && { ref: res.pickLeft.ref, pane: res.pickLeft.pane },
      pickRight: res.pickRight && { ref: res.pickRight.ref, pane: res.pickRight.pane },
      pickSingle: res.pickSingle && { pane: res.pickSingle.pane } };
  });
  // red only on the left, green only on the right (tone mapping keeps the hue dominant)
  expect(out.leftMid[0]).toBeGreaterThan(out.leftMid[1] + 100);
  expect(out.rightMid[1]).toBeGreaterThan(out.rightMid[0] + 100);
  expect(out.pickLeft).toEqual({ ref: 'L', pane: 0 });
  expect(out.pickRight).toEqual({ ref: 'R', pane: 1 });
  expect(out.rects[0].x).toBe(0);
  expect(out.rects[1].x).toBe(out.rects[0].width);
  expect(out.visible).toEqual([true, true]);   // visibility restored after drawing
  expect(out.single).toBeNull();
  expect(out.pickSingle.pane).toBe(0);
});

// Client point of a board-frame point, for pointer events.
const screenOf = (page, x, y, z) => page.evaluate(([px, py, pz]) => {
  const { camera, canvas } = window.v;
  const p = new window.THREE.Vector3(px, py, pz).project(camera);
  const r = canvas.getBoundingClientRect();
  return { x: r.left + ((p.x + 1) / 2) * r.width, y: r.top + ((1 - p.y) / 2) * r.height };
}, [x, y, z]);

test('flyTo: eases to frame a part, keeps the direction, and resolves there', async ({ page }) => {
  await open(page);
  const out = await page.evaluate(async () => {
    const v = window.v;
    const before = v.camera.position.clone().sub(v.controls.target).normalize();
    const part = v.content.children.find((o) => o.userData.ref === 'U1');
    const box = new window.THREE.Box3().setFromObject(part);
    const frames = v.stats.frames;
    await v.flyTo(box, { minSize: 4 });
    const after = v.camera.position.clone().sub(v.controls.target).normalize();
    const mid = box.getCenter(new window.THREE.Vector3());
    return { dot: before.dot(after), off: v.controls.target.distanceTo(mid),
             distance: v.camera.position.distanceTo(v.controls.target), frames: v.stats.frames - frames };
  });
  expect(out.dot).toBeGreaterThan(0.999);
  expect(out.off).toBeLessThan(1e-6);
  expect(out.distance).toBeLessThan(60);          // closer than the whole board
  expect(out.frames).toBeGreaterThan(1);          // eased, not a jump
});

test('flyTo: a press cuts the flight short', async ({ page }) => {
  await open(page);
  const box = await page.locator('#v canvas').boundingBox();
  const moved = page.evaluate(() => window.v.flyTo({ min: [9, 9, 1.6], max: [11, 11, 3.6] }, { duration: 5000 })
    .then(() => window.v.controls.target.toArray()));
  await page.waitForTimeout(150);
  await page.mouse.move(box.x + 50, box.y + 50);
  await page.mouse.down();
  await page.mouse.up();
  const target = await moved;
  expect(Math.hypot(target[0] - 10, target[1] - 10)).toBeGreaterThan(0.5);
});

test('captureObjects: the part alone, framed, and the scene put back', async ({ page }) => {
  await open(page);
  const out = await page.evaluate(async () => {
    const v = window.v;
    const part = v.content.children.find((o) => o.userData.ref === 'U1');
    const board = v.content.children.find((o) => o !== part);
    const pose = v.camera.position.toArray().join();
    const url = v.captureObjects([part], { width: 200, height: 200 });
    const img = new Image();
    img.src = url;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let opaque = 0, blue = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] > 200) opaque++;
      if (d[i + 3] > 200 && d[i + 2] > d[i] + 40) blue++;
    }
    return { w: img.width, share: opaque / (d.length / 4), blue, boardShown: board.visible,
             poseKept: v.camera.position.toArray().join() === pose };
  });
  expect(out.w).toBe(200);
  expect(out.share).toBeGreaterThan(0.2);   // framed: the part fills a good share of the picture
  expect(out.blue).toBe(0);                 // the blue board is not in it
  expect(out.boardShown).toBe(true);
  expect(out.poseKept).toBe(true);
});

test('context: a right click reports the part under it', async ({ page }) => {
  await page.goto('/test/browser/pages/scene.html');
  await page.waitForFunction(() => window.ready);
  await page.evaluate(() => {
    window.picks = [];
    window.contexts = [];
    window.v.on('context', (hit, where) => window.contexts.push({ ref: hit && hit.ref, type: where.pointerType }));
    window.v.setView('top');
  });
  const at = await screenOf(page, 10, 10, 3.6);
  await page.mouse.click(at.x, at.y, { button: 'right' });
  await page.waitForTimeout(100);
  expect(await page.evaluate(() => window.contexts)).toEqual([{ ref: 'U1', type: 'mouse' }]);
});

test('context: a touch held still is a long-press; a moving one is not', async ({ page }) => {
  await page.goto('/test/browser/pages/scene.html');
  await page.waitForFunction(() => window.ready);
  await page.evaluate(() => {
    window.contexts = [];
    window.v.on('context', (hit) => window.contexts.push(hit && hit.ref));
    window.v.setView('top');
  });
  const at = await screenOf(page, 10, 10, 3.6);
  const touch = (type, x, y) => page.evaluate(([t, px, py]) => window.v.canvas.dispatchEvent(new PointerEvent(t, {
    pointerId: 7, pointerType: 'touch', clientX: px, clientY: py, bubbles: true, isPrimary: true, button: 0 })), [type, x, y]);
  // Held: the finger stays down until the menu would show (a busy CI page runs the timer late).
  await touch('pointerdown', at.x, at.y);
  await page.waitForFunction(() => window.contexts.length === 1, null, { timeout: 10_000 });
  await touch('pointerup', at.x, at.y);
  expect(await page.evaluate(() => window.contexts)).toEqual(['U1']);
  await page.waitForTimeout(800);
  await touch('pointerdown', at.x, at.y);
  await touch('pointermove', at.x + 30, at.y);
  await page.waitForTimeout(2000);
  await touch('pointerup', at.x + 30, at.y);
  expect(await page.evaluate(() => window.contexts)).toEqual(['U1']);
});
