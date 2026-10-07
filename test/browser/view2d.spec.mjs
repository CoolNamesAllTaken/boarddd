import { writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

// boarddd/view2d in Chromium against the real wasm renderer: every compare mode on pic_programmer
// base/head (KiCad demo; head moves mounting hole P101 and edits copper) and royalblue54L, ink diff of
// schematic sheets, measure, pick, overlays, view state, resharpen and render on demand.
// Pixels come from real screenshots of #host (DOM transforms included), read back in the page.

const BG = [32, 32, 32]; // pane background (#202020): what shows where nothing is drawn
// pic_programmer mounting holes (4.3 mm, NPTH): two that stay, and P101 that moves
const TL = [77.47, -44.45];
const TR = [229.87, -44.45];
const P101_BASE = [77.47, -135.89];
const P101_HEAD = [80.47, -133.89];
const REMOVED = [75.97, -135.89]; // in the base hole only
const ADDED = [81.97, -133.89]; // in the head hole only
const BOTH = [78.97, -134.89]; // in both

const near = (p, c, tol = 24) => c.every((v, i) => Math.abs(p[i] - v) <= tol);
const isRed = (p) => p[0] > 150 && p[1] < 110 && p[2] < 110;
const isGreen = (p) => p[1] > 150 && p[0] < 140 && p[2] < 140;
const isBlue = (p) => p[2] > 150 && p[0] < 110 && p[1] < 110;

async function grab(page) {
  const png = await page.locator('#host').screenshot();
  await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width;
    c.height = img.height;
    const g = c.getContext('2d');
    g.drawImage(img, 0, 0);
    window.shot = g.getImageData(0, 0, c.width, c.height);
  }, png.toString('base64'));
  return png;
}

/** RGBA in the last grab at world point (x, y) of pane `pane`. */
const pixel = (page, pane, [x, y]) => page.evaluate(([pane, x, y]) => {
  const [hx, hy] = window.v2.at(pane, x, y);
  const s = window.shot;
  const i = (Math.round(hy) * s.width + Math.round(hx)) * 4;
  return [...s.data.slice(i, i + 4)];
}, [pane, x, y]);

/** Pixels of the last grab inside a world box (pane 0) that pass `test` (a predicate source). */
const countIn = (page, box, pred) => page.evaluate(([box, pred]) => {
  const f = new Function('p', `return (${pred})(p)`);
  const [x0, y0] = window.v2.at(0, box.minX, box.maxY);
  const [x1, y1] = window.v2.at(0, box.maxX, box.minY);
  const s = window.shot;
  let n = 0;
  for (let y = Math.max(0, Math.floor(Math.min(y0, y1))); y <= Math.min(s.height - 1, Math.ceil(Math.max(y0, y1))); y++) {
    for (let x = Math.max(0, Math.floor(Math.min(x0, x1))); x <= Math.min(s.width - 1, Math.ceil(Math.max(x0, x1))); x++) {
      const i = (y * s.width + x) * 4;
      if (f([s.data[i], s.data[i + 1], s.data[i + 2], s.data[i + 3]])) n++;
    }
  }
  return n;
}, [box, pred.toString()]);

// V2_SHOTS=dir also writes each named screenshot there (PR media).
async function shotFor(page, testInfo, name) {
  const body = await page.locator('#host').screenshot();
  await testInfo.attach(name, { body, contentType: 'image/png' });
  if (process.env.V2_SHOTS) writeFileSync(`${process.env.V2_SHOTS}/${name}.png`, body);
}

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (e) => { throw e; });
  await page.goto('/test/browser/pages/view2d.html');
  expect(await page.evaluate(() => window.v2ready), await page.evaluate(() => window.v2error)).toBe(true);
});

/** A compare stage of pic_programmer's NPTH drills: base red, head blue (or given content). */
async function npthCompare(page, mode, extra = {}) {
  await page.evaluate(async ([mode, extra]) => {
    const { view2d, files, file, bounds } = window.v2;
    window.v2.mount({ bounds: bounds.pic });
    const one = (list, color) => view2d.layers([{ ...file(list, 'NPTH'), kind: 'drill', color, alpha: 1 }]);
    window.v2.compare({ base: one(files.picBase, [1, 0, 0]), head: one(files.picHead, [0, 0, 1]), mode, ...extra });
    await window.stage.ready();
  }, [mode, extra]);
}

test('top and bottom faces: realistic, holes see-through, bottom mirrored', async ({ page }, testInfo) => {
  const info = await page.evaluate(async () => {
    const { view2d, files, bounds, holes } = window.v2;
    const s = window.v2.mount({ bounds: bounds.rb });
    const top = view2d.face(view2d.faceBoard(files.rb, 'top'), { side: 'top', palette: { mask: 'blue', finish: 'enig' } });
    s.setScene([{ label: 'top', layers: [{ content: top }] }]);
    await s.ready();
    // a round hole whose mirror image (about the bounds centre) is board, not another hole
    const all = [...holes(files.rb, 'NPTH'), ...holes(files.rb, '-PTH')].filter((h) => h.x2 == null);
    const cx = bounds.rb.minX + bounds.rb.maxX;
    const hole = all.filter((h) => h.diameter >= 0.9).find((h) => all.every((o) => Math.hypot(o.x - (cx - h.x), o.y - h.y) > o.diameter / 2 + 1.5));
    return { hole: [hole.x, hole.y], d: hole.diameter, bounds: bounds.rb };
  });
  const [hx, hy] = info.hole;
  const b = info.bounds;
  const mirrored = [b.minX + b.maxX - hx, hy]; // where the hole would be if the view were not mirrored
  const inside = [hx + info.d, hy]; // board next to the hole
  await grab(page);
  await shotFor(page, testInfo, 'face-top');
  expect(near(await pixel(page, 0, info.hole), BG)).toBe(true);
  const board = await pixel(page, 0, inside);
  expect(near(board, BG)).toBe(false);
  expect(board[2]).toBeGreaterThan(board[0]); // blue mask

  await page.evaluate(async () => {
    const { view2d, files } = window.v2;
    const s = window.stage;
    s.setFlip(true);
    s.setScene([{ label: 'bottom', layers: [{ content: view2d.face(view2d.faceBoard(files.rb, 'bottom'), { side: 'bottom', palette: { mask: 'blue' } }) }] }]);
    await s.ready();
  });
  await grab(page);
  await shotFor(page, testInfo, 'face-bottom');
  expect(near(await pixel(page, 0, info.hole), BG)).toBe(true); // toScreen mirrors too: still the hole
  const [mx] = await page.evaluate(([x, y]) => window.stage.toScreen(x, y), info.hole);
  const [ux] = await page.evaluate(([x, y]) => { window.stage.setFlip(false); const p = window.stage.toScreen(x, y); window.stage.setFlip(true); return p; }, info.hole);
  expect(Math.abs(mx + ux - 800)).toBeLessThan(1); // mirrored about the pane centre
  expect(near(await pixel(page, 0, mirrored), BG)).toBe(false);
});

test('layer stack in per-layer colours, and a single-layer view', async ({ page }, testInfo) => {
  const res = await page.evaluate(async () => {
    const { view2d, files, bounds, file } = window.v2;
    const s = window.v2.mount({ bounds: bounds.pic });
    const stack = view2d.layerStack(files.picBase);
    s.setScene([{ label: 'layers', layers: [{ content: view2d.layers(stack) }] }]);
    await s.ready();
    const cu = stack.find((l) => l.role === 'copper' && l.side === 'top');
    return { visible: stack.filter((l) => l.visible).map((l) => l.role), color: cu.color };
  });
  expect(res.visible).toEqual(['silk', 'copper', 'copper', 'silk', 'outline', 'drill', 'drill']);
  await grab(page);
  await shotFor(page, testInfo, 'layers');
  expect(near(await pixel(page, 0, TL), [0.55 * 255, 0.85 * 255, 0.95 * 255], 30)).toBe(true); // NPTH colour
  expect(near(await pixel(page, 0, [72.4, -90]), BG)).toBe(true); // off the board (edge at x 73.66)

  await page.evaluate(async () => {
    const { view2d, files, file } = window.v2;
    window.stage.setScene([{ label: 'F.Cu', layers: [{ content: view2d.layers([{ ...file(files.picBase, 'top_layer'), color: [1, 0, 0], alpha: 1 }]) }] }]);
    await window.stage.ready();
  });
  await grab(page);
  await shotFor(page, testInfo, 'single-layer');
  expect(await countIn(page, { minX: 80, maxX: 230, minY: -139, maxY: -42 }, (p) => p[0] > 200 && p[1] < 60)).toBeGreaterThan(5000);
  expect(near(await pixel(page, 0, TL), BG)).toBe(true); // a mounting hole has no copper on F.Cu
});

test('side by side: two panes, one view; a drag in one pans both', async ({ page }, testInfo) => {
  await npthCompare(page, 'side');
  await grab(page);
  await shotFor(page, testInfo, 'side');
  expect(isRed(await pixel(page, 0, REMOVED))).toBe(true);
  expect(near(await pixel(page, 1, REMOVED), BG)).toBe(true);
  expect(isBlue(await pixel(page, 1, ADDED))).toBe(true);
  expect(near(await pixel(page, 0, ADDED), BG)).toBe(true);
  const before = await page.evaluate(() => [window.v2.at(0, 77.47, -44.45), window.v2.at(1, 77.47, -44.45)]);
  await page.mouse.move(200, 250);
  await page.mouse.down();
  await page.mouse.move(240, 280, { steps: 4 });
  await page.mouse.up();
  await page.evaluate(() => window.stage.ready());
  const after = await page.evaluate(() => [window.v2.at(0, 77.47, -44.45), window.v2.at(1, 77.47, -44.45)]);
  for (const i of [0, 1]) {
    expect(after[i][0] - before[i][0]).toBeCloseTo(40, 0);
    expect(after[i][1] - before[i][1]).toBeCloseTo(30, 0);
  }
  await grab(page);
  expect(isRed(await pixel(page, 0, TL))).toBe(true);
  expect(isBlue(await pixel(page, 1, TL))).toBe(true);
});

test('diff: GPU layer diff with regions, over a faint face underlay', async ({ page }, testInfo) => {
  const info = await page.evaluate(async () => {
    const { view2d, files, file, bounds } = window.v2;
    window.v2.mount({ bounds: bounds.pic });
    const npth = (l) => ({ source: file(l, 'NPTH').source, name: 'NPTH' });
    const under = view2d.face(view2d.faceBoard(files.picHead, 'top'), { side: 'top' });
    const c = window.v2.compare({ base: under, head: under, diff: view2d.diff(npth(files.picBase), npth(files.picHead), { regions: true }), underlay: under, mode: 'diff' });
    await window.stage.ready();
    return { modes: c.modes, info: window.stage.info(0, 1), layers: window.stage.stats().tiles[0].length };
  });
  expect(info.modes).toEqual(['side', 'diff', 'onion', 'swipe', 'base', 'head']);
  expect(info.layers).toBe(2);
  expect(info.info.regions.length).toBe(1);
  const r = info.info.regions[0];
  expect(r.minX).toBeLessThan(76); expect(r.maxX).toBeGreaterThan(82);
  expect(r.minY).toBeLessThan(-137); expect(r.maxY).toBeGreaterThan(-132);
  expect(info.info.counts.removed).toBeGreaterThan(0);
  expect(info.info.counts.added).toBeGreaterThan(0);
  await grab(page);
  await shotFor(page, testInfo, 'diff');
  expect(isRed(await pixel(page, 0, REMOVED))).toBe(true);
  expect(isGreen(await pixel(page, 0, ADDED))).toBe(true);
  const both = await pixel(page, 0, BOTH);
  expect(isRed(both) || isGreen(both)).toBe(false);
});

test('onion skin: head over base at the set opacity, changed live', async ({ page }, testInfo) => {
  await npthCompare(page, 'onion', { opacity: 0.5 });
  await grab(page);
  await shotFor(page, testInfo, 'onion');
  const both = await pixel(page, 0, BOTH);
  expect(both[0]).toBeGreaterThan(100); expect(both[2]).toBeGreaterThan(100); // red under half blue
  const added = await pixel(page, 0, ADDED);
  expect(added[2]).toBeGreaterThan(110); expect(added[2]).toBeLessThan(170); expect(added[0]).toBeLessThan(60);
  expect(isRed(await pixel(page, 0, REMOVED))).toBe(true);
  const renders = await page.evaluate(() => window.stage.stats().renders);
  await page.evaluate(() => window.cmp.setOpacity(1));
  await grab(page);
  expect(isBlue(await pixel(page, 0, BOTH))).toBe(true);
  expect(await page.evaluate(() => window.stage.stats().renders)).toBe(renders); // no re-render for a slider
});

test('swipe: base left of the divider, head right; the handle drags', async ({ page }, testInfo) => {
  await npthCompare(page, 'swipe', { swipe: 0.5 });
  await grab(page);
  await shotFor(page, testInfo, 'swipe');
  expect(isRed(await pixel(page, 0, TL))).toBe(true); // left half: base
  expect(isBlue(await pixel(page, 0, TR))).toBe(true); // right half: head over base
  const handle = page.locator('.bd2-swipe');
  const box = await handle.boundingBox();
  expect(box.x + box.width / 2).toBeCloseTo(400, 0);
  const tlx = (await page.evaluate(() => window.v2.at(0, 77.47, -44.45)))[0];
  await page.mouse.move(box.x + box.width / 2, 250);
  await page.mouse.down();
  await page.mouse.move(tlx - 30, 250, { steps: 5 });
  await page.mouse.up();
  const sw = await page.evaluate(() => window.cmp.swipe);
  expect(sw).toBeCloseTo((tlx - 30) / 800, 2);
  await grab(page);
  expect(isBlue(await pixel(page, 0, TL))).toBe(true);
  const view = await page.evaluate(() => window.stage.getRegion());
  expect(view).toBeNull(); // dragging the handle does not pan
});

test('base only and head only', async ({ page }, testInfo) => {
  await npthCompare(page, 'base');
  await grab(page);
  await shotFor(page, testInfo, 'base-only');
  expect(isRed(await pixel(page, 0, REMOVED))).toBe(true);
  expect(near(await pixel(page, 0, ADDED), BG)).toBe(true);
  await page.evaluate(async () => { window.cmp.setMode('head'); await window.stage.ready(); });
  await grab(page);
  await shotFor(page, testInfo, 'head-only');
  expect(isBlue(await pixel(page, 0, ADDED))).toBe(true);
  expect(near(await pixel(page, 0, REMOVED), BG)).toBe(true);
  // a side that does not exist says so
  await page.evaluate(async () => { window.cmp.set({ base: null }); window.cmp.setMode('side'); await window.stage.ready(); });
  await expect(page.locator('.bd2-missing')).toHaveText('not in base');
  expect(await page.evaluate(() => window.cmp.modes)).toEqual(['side', 'diff', 'onion', 'swipe', 'head']); // the diff: all added
});

test('ink diff of SVG sheets: R7 10K -> 4.7K is red and green, in one region', async ({ page }, testInfo) => {
  const info = await page.evaluate(async () => {
    const { view2d, sheets } = window.v2;
    const s = window.v2.mount({ bounds: sheets.rect, background: '#ffffff' });
    const c = window.v2.compare({ base: view2d.image(sheets.base, sheets.rect), head: view2d.image(sheets.head, sheets.rect), mode: 'diff' });
    await s.ready();
    return { info: s.info(0, 0), modes: c.modes };
  });
  expect(info.modes).toContain('diff');
  expect(info.info.counts.removed).toBeGreaterThan(20);
  expect(info.info.counts.added).toBeGreaterThan(20);
  expect(info.info.regions.length).toBe(1);
  const r = info.info.regions[0];
  // R7 sits at sheet (156.97, 25.4) mm: world y is the sheet's y negated
  expect(r.minX).toBeGreaterThan(150); expect(r.maxX).toBeLessThan(165);
  expect(r.minY).toBeGreaterThan(-32); expect(r.maxY).toBeLessThan(-18);
  await grab(page);
  await shotFor(page, testInfo, 'inkdiff');
  expect(await countIn(page, r, (p) => p[0] > 180 && p[1] < 90)).toBeGreaterThan(10);
  expect(await countIn(page, r, (p) => p[1] > 130 && p[0] < 90)).toBeGreaterThan(10);
  // a side over its own rect: head drawn 10 mm to the right of its true place, the R7 change moves along
  const moved = await page.evaluate(async () => {
    const { view2d, sheets } = window.v2;
    const r = sheets.rect;
    const s = window.v2.mount({ bounds: r, background: '#ffffff' });
    const shifted = { minX: r.minX + 10, maxX: r.maxX + 10, minY: r.minY, maxY: r.maxY };
    s.setScene([{ layers: [{ content: view2d.inkdiff(sheets.base, { src: sheets.head, rect: shifted }, r) }] }]);
    await s.ready();
    return s.info(0, 0);
  });
  expect(moved.regions.length).toBeGreaterThan(1); // everything moved: many changed areas
  expect(moved.counts.added).toBeGreaterThan(info.info.counts.added * 10);
  // side by side: the sheets themselves
  await page.evaluate(async () => { window.cmp.setMode('side'); await window.stage.ready(); });
  await grab(page);
  await shotFor(page, testInfo, 'sheets-side');
  expect(await countIn(page, { minX: 120, maxX: 200, minY: -60, maxY: -5 }, (p) => p[0] < 120 && p[3] > 0)).toBeGreaterThan(500);
});

test('measure: two clicks give the distance in mm; the line is drawn', async ({ page }) => {
  await npthCompare(page, 'head');
  const events = await page.evaluate(() => {
    window.measured = [];
    window.stage.on('measure', (e) => window.measured.push(e.result));
    window.stage.setTool('measure');
    return [window.v2.at(0, 77.47, -44.45), window.v2.at(0, 229.87, -44.45)];
  });
  await page.mouse.click(events[0][0], events[0][1]);
  await page.mouse.click(events[1][0], events[1][1]);
  const m = await page.evaluate(() => window.stage.getMeasure());
  const mmPerPx = await page.evaluate(() => window.stage.mmPerPx());
  expect(Math.abs(m.distance - 152.4)).toBeLessThan(2 * mmPerPx);
  expect(Math.abs(m.dy)).toBeLessThan(2 * mmPerPx);
  expect(await page.evaluate(() => window.measured.filter(Boolean).length)).toBe(1);
  await expect(page.locator('.bd2-measure line')).toHaveCount(1);
  await expect(page.locator('.bd2-measure text')).toHaveText(/^15\d\.\d{3} mm$/);
  expect(await page.evaluate(() => window.v2.view2d.measureText(window.stage.getMeasure()))).toMatch(/^Δx 15\d\.\d{3} {2}Δy -?\d\.\d{3} {2}d 15\d\.\d{3} mm$/);
  // a third click starts again; pan tool clears it
  await page.mouse.click(events[0][0], events[0][1]);
  expect(await page.evaluate(() => window.stage.getState().measure.length)).toBe(1);
  await page.evaluate(() => window.stage.setTool('pan'));
  await expect(page.locator('.bd2-measure circle')).toHaveCount(0);
});

test('pick: a click hit-tests the holes, also mirrored', async ({ page }) => {
  await npthCompare(page, 'head');
  const target = await page.evaluate(() => {
    const { view2d, files, holes } = window.v2;
    const shapes = [...holes(files.picHead, '-PTH'), ...holes(files.picHead, 'NPTH')].map((h, i) => view2d.circleShape(h.x, h.y, h.diameter, { i, x: h.x, y: h.y }));
    const index = view2d.createHitIndex(shapes);
    window.picked = [];
    window.stage.on('click', (e) => window.picked.push(index.at(e.x, e.y, 4 * window.stage.mmPerPx())?.data ?? null));
    const pth = shapes.find((s) => s.d >= 1.25 && s.d < 2); // a 1.27 mm THT hole
    return pth.data;
  });
  for (const flip of [false, true]) {
    await page.evaluate((f) => window.stage.setFlip(f), flip);
    const [x, y] = await page.evaluate(([x, y]) => window.v2.at(0, x, y), [target.x + 0.3, target.y]);
    await page.mouse.click(x, y);
  }
  const [x, y] = await page.evaluate(() => window.v2.at(0, 72.4, -90)); // off the board
  await page.mouse.click(x, y);
  const picked = await page.evaluate(() => window.picked);
  expect(picked).toEqual([target, target, null]);
});

test('overlays: world boxes follow the view, screen markers are redrawn per view', async ({ page }) => {
  await npthCompare(page, 'side');
  const res = await page.evaluate(async () => {
    const s = window.stage;
    let screenDraws = 0;
    s.addOverlay({ space: 'world', className: 'boxes', draw: (g, ctx) => ctx.svg('rect', { x: 75, y: -138, width: 8, height: 6, fill: 'none', stroke: '#0f0', class: 'box' }, g) });
    s.addOverlay({ space: 'screen', draw: (g, ctx) => { screenDraws++; const [x, y] = ctx.toScreen(229.87, -44.45); ctx.svg('circle', { cx: x, cy: y, r: 6, class: 'tick' }, g); } });
    await s.ready();
    const check = () => {
      const out = [];
      for (const [i, p] of s.panes.entries()) {
        const r = p.el.querySelector('.box').getBoundingClientRect();
        const pr = p.el.getBoundingClientRect();
        const [x0, y0] = s.toScreen(75, -132, i);
        const [x1, y1] = s.toScreen(83, -138, i);
        out.push([r.left - pr.left - x0, r.top - pr.top - y0, r.width - (x1 - x0), r.height - (y1 - y0)]);
        const c = p.el.querySelector('.tick').getBoundingClientRect();
        const [cx, cy] = s.toScreen(229.87, -44.45, i);
        out.push([c.left + c.width / 2 - pr.left - cx, c.top + c.height / 2 - pr.top - cy]);
      }
      return out;
    };
    const a = check();
    const draws = screenDraws;
    s.setView({ ...s.getView(), s: s.getView().s * 3 });
    await s.ready();
    return { a, b: check(), draws, after: screenDraws, panes: s.panes.length };
  });
  for (const d of [...res.a, ...res.b].flat()) expect(Math.abs(d)).toBeLessThan(1.5);
  expect(res.after).toBeGreaterThan(res.draws);
});

test('view state: get / format / parse / set restores the same picture', async ({ page }) => {
  const params = await page.evaluate(async () => {
    const { view2d, files, file, bounds } = window.v2;
    const content = (l, c) => view2d.layers([{ ...file(l, 'top_layer'), color: c, alpha: 1 }]);
    window.mk = async (state) => {
      window.v2.mount({ bounds: bounds.pic });
      const c = window.v2.compare({ base: content(files.picBase, [1, 0, 0]), head: content(files.picHead, [0, 0, 1]), mode: 'side' });
      if (state) c.setState(state);
      await window.stage.ready();
      return c;
    };
    const c = await window.mk();
    c.setMode('swipe');
    c.setSwipe(0.3);
    window.stage.zoomTo({ minX: 140, maxX: 150, minY: -92, maxY: -86 });
    await window.stage.ready();
    return view2d.formatViewState(c.getState());
  });
  expect(Object.keys(params).sort()).toEqual(['mode', 'sw', 'z']);
  const one = await grab(page);
  const back = await page.evaluate(async (p) => {
    const st = window.v2.view2d.parseViewState(new URLSearchParams(p));
    const c = await window.mk(st);
    return { state: c.getState(), mode: c.mode };
  }, params);
  expect(back.mode).toBe('swipe');
  expect(back.state.swipe).toBe(0.3);
  const two = await grab(page);
  expect(Buffer.compare(one, two)).toBe(0);
  // a region given before the panes exist (createStage option) is applied once they have a size
  const same = await page.evaluate(async (p) => {
    const { view2d, bounds } = window.v2;
    const region = view2d.parseRegion(p.z);
    const s = window.v2.mount({ bounds: bounds.pic, region });
    const early = view2d.sameRegion(s.getRegion(), region);
    s.setScene([{ layers: [] }, { layers: [] }]);
    return [early, view2d.sameRegion(s.getRegion(), region)];
  }, params);
  expect(same).toEqual([true, true]);
});

test('render on demand: still is free; zoom re-renders sharper once settled', async ({ page }) => {
  await npthCompare(page, 'head', {});
  const s0 = await page.evaluate(() => window.stage.stats());
  await page.waitForTimeout(600);
  const s1 = await page.evaluate(() => window.stage.stats());
  expect(s1.renders).toBe(s0.renders);
  expect(s1.frames).toBe(s0.frames);
  // a pan at the same zoom moves tiles only
  await page.evaluate(async () => { const v = window.stage.getView(); window.stage.setView({ ...v, cx: v.cx + 5 }); await window.stage.ready(); });
  expect((await page.evaluate(() => window.stage.stats())).renders).toBe(s0.renders);
  const base0 = s0.tiles[0][0].base;
  // zoom far in: past the whole-board budget, a detail tile of the visible area at screen resolution
  const s2 = await page.evaluate(async () => {
    const st = window.stage;
    st.setView({ cx: 77.47 + 2.15, cy: -44.45, s: 400 }); // on the hole's right edge
    await st.ready();
    return st.stats();
  });
  expect(s2.renders).toBeGreaterThan(s0.renders);
  const t = s2.tiles[0][0];
  expect(t.base.r).toBeGreaterThan(base0.r);
  expect(t.base.width).toBeLessThanOrEqual(4096);
  expect(t.detail.r).toBeGreaterThanOrEqual(400 * 0.87);
  expect(t.detail.rect.maxX - t.detail.rect.minX).toBeLessThan(5);
  await grab(page);
  expect(isBlue(await pixel(page, 0, [77.47 + 2.15 - 0.05, -44.45]))).toBe(true); // 20 px either side of the edge
  expect(near(await pixel(page, 0, [77.47 + 2.15 + 0.05, -44.45]), BG)).toBe(true);
  // back out: the detail tile goes
  const s3 = await page.evaluate(async () => { window.stage.fit(); await window.stage.ready(); return window.stage.stats(); });
  expect(s3.tiles[0][0].detail).toBeNull();
});

test('a broken source reports an error event, the rest of the scene still draws', async ({ page }) => {
  const res = await page.evaluate(async () => {
    const { view2d, files, file, bounds } = window.v2;
    const s = window.v2.mount({ bounds: bounds.pic });
    const errors = [];
    s.on('error', (e) => errors.push(String(e.error?.message || e.error)));
    s.setScene([{ layers: [
      { content: view2d.layers([{ ...file(files.picBase, 'NPTH'), kind: 'drill', color: [1, 0, 0] }]) },
      { content: view2d.image('<svg broken', { minX: 0, maxX: 1, minY: 0, maxY: 1 }) },
    ] }]);
    await s.ready();
    return { errors, tiles: s.stats().tiles[0].map((t) => !!t.base) };
  });
  expect(res.errors.length).toBe(1);
  expect(res.tiles).toEqual([true, false]);
});
