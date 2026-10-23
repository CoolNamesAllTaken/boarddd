import { expect, test } from '@playwright/test';

// boarddd/impedance/ui layerView on examples/layers.html: CM5 MINIMA's copper read from its plain Gerbers (no X2:
// nets N1, N2... from boarddd.connectivity), its KiCad stackup; and on test/browser/pages/layers.html over the
// synthetic route boards (a split plane, CPWG, stripline). The stepper walks the stackup; a click toggles a net in
// the selection (a drag or a long press only pans), nets pair from the copper, the form gives Z per geometry, the
// reference planes come from the route, and selecting never moves the view. LV_SHOTS=dir saves screenshots.

const shot = async (page, name) => { if (process.env.LV_SHOTS) await page.screenshot({ path: `${process.env.LV_SHOTS}/${name}.png` }); };
const PAIR = ['/CM5/ETH_PI.TRD0_P', '/CM5/ETH_PI.TRD0_N'];

async function open(page) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/examples/layers.html');
  await page.waitForFunction(() => window.demo?.view, null, { timeout: 120_000 });
  await page.evaluate(() => window.demo.view.stage.ready());
  return errors;
}

/** The middle of a net's longest F.Cu track in the KiCad file's copper (the same board frame as the Gerbers). */
const kicadTrack = (page, net) => page.evaluate(async (net) => {
  const res = await fetch('/fixtures/impedance/route/cm5.json.gz');
  const { copper } = JSON.parse(await new Response(res.body.pipeThrough(new DecompressionStream('gzip'))).text());
  const t = copper.tracks.filter((t) => t.net === net && t.layer === 'F.Cu' && !t.mid)
    .sort((a, b) => Math.hypot(b.end[0] - b.start[0], b.end[1] - b.start[1]) - Math.hypot(a.end[0] - a.start[0], a.end[1] - a.start[1]))[0];
  const by = new Map(); // where most of the net is routed, and its width there
  for (const u of copper.tracks.filter((u) => u.net === net)) {
    const k = `${u.layer}|${u.width}`;
    by.set(k, (by.get(k) ?? 0) + Math.hypot(u.end[0] - u.start[0], u.end[1] - u.start[1]));
  }
  const [main] = [...by].sort((a, b) => b[1] - a[1])[0][0].split('|');
  const [, mainWidth] = [...by].sort((a, b) => b[1] - a[1])[0][0].split('|');
  return { x: (t.start[0] + t.end[0]) / 2, y: (t.start[1] + t.end[1]) / 2, width: t.width, main, mainWidth: Number(mainWidth) };
}, net);

async function screenOf(page, x, y) {
  return page.evaluate(([x, y]) => {
    const { stage } = window.demo.view;
    const [px, py] = stage.toScreen(x, y);
    const r = stage.panes[0].el.getBoundingClientRect();
    return [r.left + px, r.top + py];
  }, [x, y]);
}

async function zoomTo(page, { x, y }, r = 3) {
  await page.evaluate(([x, y, r]) => window.demo.view.stage.zoomTo({ minX: x - r, maxX: x + r, minY: y - r, maxY: y + r }), [x, y, r]);
  await page.evaluate(() => window.demo.view.stage.ready());
}

test('the stepper walks the stackup top to bottom: one layer at a time, the ones above ghosted', async ({ page }) => {
  const errors = await open(page);
  const stops = await page.evaluate(() => window.demo.view.stops.map((s) => s.kind === 'copper' ? s.layer : 'd'));
  expect(stops).toEqual(['F.Cu', 'd', 'In1.Cu', 'd', 'In2.Cu', 'd', 'In3.Cu', 'd', 'In4.Cu', 'd', 'B.Cu']);
  await expect(page.locator('.bdi-lv-caption')).toHaveText('F.Cu');
  await page.locator('.bdi-lv').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.bdi-lv-caption')).toContainText('εr');
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.bdi-lv-caption')).toHaveText('In1.Cu');
  // the scene is bottom first: B.Cu ... F.Cu, then the dielectric sheet
  const op = await page.evaluate(() => [...window.demo.view.stage.panes[0].el.querySelectorAll('.bd2-slot')].map((s) => s.style.opacity || '1'));
  expect(op).toEqual(['0', '0', '0', '0', '1', '0.16', '0']);
  await page.locator('.bdi-lv-step').last().click();
  await expect(page.locator('.bdi-lv-caption')).toContainText('εr');
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('ArrowUp');
  await expect(page.locator('.bdi-lv-caption')).toHaveText('F.Cu');
  await expect(page.locator('.bdi-lv-step').first()).toBeDisabled();
  await page.locator('.bdi-lv-stop', { hasText: 'B' }).last().click();
  await expect(page.locator('.bdi-lv-caption')).toHaveText('B.Cu');
  await shot(page, 'stepper-bottom');
  expect(errors).toEqual([]);
});


const view = (page) => page.evaluate(() => window.demo.view.stage.getView());
/** What the camera is: the view, and every drawn tile's CSS transform. */
const camera = (page) => page.evaluate(() => ({
  view: window.demo.view.stage.getView(),
  width: window.demo.view.stage.panes[0].el.clientWidth,
  height: window.demo.view.stage.panes[0].el.clientHeight,
  world: window.demo.view.stage.panes[0].el.querySelector('.bd2-world').getAttribute('transform'),
}));
const nets = (page) => page.evaluate(() => window.demo.view.selection?.nets ?? []);
const touch = (page, type, [x, y], id = 7) => page.evaluate(([type, x, y, id]) => {
  window.demo.view.stage.panes[0].el.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch', clientX: x, clientY: y, button: 0, bubbles: true, isPrimary: true }));
}, [type, x, y, id]);

test('a click toggles a net; a second net adds to the selection; Delete, Esc; the view never moves', async ({ page }) => {
  const errors = await open(page);
  const p = await kicadTrack(page, PAIR[0]);
  const n = await kicadTrack(page, PAIR[1]);
  await zoomTo(page, p, 4);
  const before = await camera(page);
  const at = await screenOf(page, p.x, p.y);
  const atN = await screenOf(page, n.x, n.y);
  await page.mouse.click(...at);
  await expect(page.locator('.bdi-lv-count')).toHaveText('1 net');
  const [first] = await nets(page);
  expect(first).toMatch(/^N\d+$/);
  expect(await page.evaluate(([x, y]) => window.demo.view.netAt(x, y), [p.x, p.y])).toBe(first); // no toggle
  await expect(page.locator('.bdi-lv-count')).toHaveText('1 net');
  expect(await page.locator('.bdi-hl-track').count()).toBeGreaterThan(2);
  expect(await camera(page)).toEqual(before);
  await page.mouse.click(...atN);
  await expect(page.locator('.bdi-lv-count')).toHaveText('2 nets');
  // the partner runs beside it: the two make a pair by themselves
  await expect(page.locator('.bdi-lv-unit .bdi-ctl-net')).toHaveText([/^⇄ N\d+ N\d+$/]);
  expect(await camera(page)).toEqual(before);
  // the same trace again: dropped (two quick clicks there are no double-click fit either)
  await page.mouse.click(...at);
  expect(await nets(page)).toHaveLength(1);
  await page.mouse.click(...at);
  expect(await nets(page)).toHaveLength(2);
  await page.mouse.dblclick(...atN); // drop and add again; no fit
  expect(await nets(page)).toHaveLength(2);
  expect(await camera(page)).toEqual(before);
  // layers: the camera stays
  await page.locator('.bdi-lv').focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.bdi-lv-caption')).toHaveText('In1.Cu');
  expect(await camera(page)).toEqual(before);
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Delete'); // the last added
  expect(await nets(page)).toHaveLength(1);
  await page.keyboard.press('Escape');
  expect(await nets(page)).toEqual([]);
  await expect(page.locator('.bdi-lv-count')).toBeHidden();
  expect(await camera(page)).toEqual(before);
  // the target button is the explicit way to the selection; ⤢ the board
  await page.mouse.click(...at);
  await page.locator('.bdi-lv-zoomsel').click();
  expect((await view(page)).s).not.toBe(before.view.s);
  await page.locator('.bdi-lv-fit').click();
  expect(await page.evaluate(() => window.demo.view.state.region)).toBeNull();
  expect(errors).toEqual([]);
});

test('a drag or a long press pans and selects nothing; a tap toggles', async ({ page }) => {
  await open(page);
  const p = await kicadTrack(page, PAIR[0]);
  await zoomTo(page, p, 4);
  const at = await screenOf(page, p.x, p.y);
  // a long mouse press: no pick
  await page.mouse.move(...at);
  await page.mouse.down();
  await page.waitForTimeout(450);
  await page.mouse.up();
  expect(await nets(page)).toEqual([]);
  // a drag pans, no pick
  const v0 = await view(page);
  await page.mouse.move(...at);
  await page.mouse.down();
  await page.mouse.move(at[0] + 30, at[1] + 10, { steps: 5 });
  await page.mouse.up();
  expect(await nets(page)).toEqual([]);
  expect(await view(page)).not.toEqual(v0);
  // touch: a tap toggles, a long press does not
  const at2 = await screenOf(page, p.x, p.y);
  await touch(page, 'pointerdown', at2);
  await touch(page, 'pointerup', at2);
  expect(await nets(page)).toHaveLength(1);
  await touch(page, 'pointerdown', at2, 8);
  await page.waitForTimeout(700);
  await touch(page, 'pointerup', at2, 8);
  expect(await nets(page)).toHaveLength(1);
  // the browser's menu is the browser's: a right click picks nothing
  await page.mouse.click(...at2, { button: 'right' });
  expect(await nets(page)).toHaveLength(1);
});

test('the form: a pair at 100 Ω, Z and the width for it, Save emits one item per net or pair', async ({ page }) => {
  const errors = await open(page);
  const p = await kicadTrack(page, PAIR[0]);
  const n = await kicadTrack(page, PAIR[1]);
  const pn = await page.evaluate(([p, n]) => [window.demo.view.pickAt(p.x, p.y)?.net, window.demo.view.pickAt(n.x, n.y)?.net], [p, n]);
  await page.evaluate(() => window.demo.view.clear());
  await page.evaluate((nets) => window.demo.view.pick(nets), pn);
  await expect(page.locator('.bdi-lv-unit .bdi-ctl-net')).toContainText('⇄');
  await expect(page.locator('.bdi-ctl-single')).toBeHidden();
  await page.locator('.bdi-ctl-target-diff').fill('100');
  await expect(page.locator('.bdi-ctl-z').first()).toHaveText(/≈ \d+\.\d Ω/);
  await expect(page.locator('.bdi-ctl-sug').first()).toHaveText(/100 Ω at w 0\.\d+ mm|✓ w/);
  const draft = await page.evaluate(() => window.demo.view.draft);
  expect(draft.items).toHaveLength(1);
  const [it] = draft.items;
  expect(it.kind).toBe('differential');
  expect(it.layer).toBe(p.main); // where most of the route is (the pair breaks out on F.Cu, runs on B.Cu)
  expect(Math.abs(it.width - p.mainWidth)).toBeLessThan(1e-3); // the Gerber track is the KiCad track
  expect(it.gap).toBeGreaterThan(0);
  expect(it.target).toBe(100);
  expect(it.tier1.z).toBeGreaterThan(60);
  expect(it.anchors).toHaveLength(2);
  // a third net of another width: one more item, Z per geometry with the range and the worst
  const other = await page.evaluate(([p0, n0]) => {
    const c = window.demo.data.copper;
    const w0 = c.tracks.find((t) => t.net === p0).width;
    return c.tracks.find((t) => t.net && t.net !== p0 && t.net !== n0 && t.layer === 'F.Cu' && Math.abs(t.width - w0) > 0.02 && !/GND|^N1$/.test(t.net)).net;
  }, pn);
  await page.evaluate((net) => window.demo.view.add(net), other);
  await expect(page.locator('.bdi-ctl-single')).toBeVisible();
  await page.locator('.bdi-ctl-target').fill('50');
  await expect(page.locator('.bdi-ctl-geo')).toHaveCount(2);
  await expect(page.locator('.bdi-ctl-range')).toHaveText(/≈ \d+\.\d–\d+\.\d Ω · worst \d+\.\d Ω/);
  await expect(page.locator('.bdi-ctl-ok')).toHaveText('✓ Save 2');
  const control = page.evaluate(() => new Promise((r) => window.demo.view.on('control', r)));
  await page.locator('.bdi-ctl-ok').click();
  const got = await control;
  expect(got.items.map((i) => [...i.nets].sort())).toEqual([[...pn].sort(), [other]]);
  expect(got.items.map((i) => i.target)).toEqual([100, 50]);
  await shot(page, 'pair-and-one');
  await expect(page.locator('.bdi-lv-stop.has-net')).not.toHaveCount(0);
  expect(errors).toEqual([]);
});

/** The synthetic route board page: a layerView over fixtures/impedance/route/<f>.json. */
async function openRoute(page, f) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`/test/browser/pages/layers.html?f=${f}`);
  await page.waitForFunction(() => window.demo?.view, null, { timeout: 60_000 });
  await page.evaluate(() => window.demo.view.stage.ready());
  return errors;
}
const fromRoute = (page, net) => page.waitForFunction((net) => window.demo.view.reference([net])?.from === 'route', net, { timeout: 60_000 });

test('the reference plane: its cross-section, a split it crosses (show), the tint, and an override', async ({ page }) => {
  const errors = await openRoute(page, 'split');
  const before = await camera(page);
  await page.evaluate(() => window.demo.view.add('SIG'));
  await fromRoute(page, 'SIG');
  const unit = page.locator('.bdi-lv-unit');
  await expect(unit.locator('.bdi-lv-reftext')).toHaveText('over In1.Cu (GND) · 0.2 mm');
  await expect(unit.locator('.bdi-lv-xs svg .bdi-xs-plane')).toHaveCount(1);
  // the trace leaves In1.Cu's GND at x = 14 and runs over In2.Cu's +3V3: both said, with the spot
  const warns = unit.locator('.bdi-lv-warn');
  await expect(warns.filter({ hasText: 'crosses a split in In1.Cu at 14.0, 2.0' })).toHaveCount(1);
  await expect(warns.filter({ hasText: 'reference changes: In1.Cu (GND) → In2.Cu (+3V3)' })).toHaveCount(1);
  await warns.first().locator('.bdi-lv-show').click();
  await expect(page.locator('.bdi-hl .bdi-ring')).toHaveCount(1);
  await expect(page.locator('.bdi-hl .bdi-hl-plane')).not.toHaveCount(0);
  await expect(unit.locator('.bdi-lv-tint')).toHaveAttribute('aria-pressed', 'true');
  expect(await camera(page)).toEqual(before); // the spot was on screen
  await unit.locator('.bdi-lv-tint').click();
  await expect(page.locator('.bdi-hl .bdi-hl-plane')).toHaveCount(0);
  await unit.locator('.bdi-lv-xs').hover();
  await expect(page.locator('.bdi-hl .bdi-hl-plane[data-layer="In1.Cu"]')).not.toHaveCount(0);
  await expect(page.locator('.bdi-hl .bdi-hl-plane[data-layer="In2.Cu"]')).toHaveCount(0);
  await shot(page, 'split-reference');
  // auto said In1.Cu; pick In2.Cu: shown, used by tier 1, and in the draft
  await expect(unit.locator('.bdi-lv-refsel option').first()).toHaveText('auto: In1.Cu');
  const z0 = await page.evaluate(() => window.demo.view.draft.items[0].tier1.z);
  await unit.locator('.bdi-lv-refsel').selectOption('In2.Cu');
  await page.waitForFunction(() => window.demo.view.draft.items[0].reference.bottom?.layer === 'In2.Cu', null, { timeout: 60_000 });
  await expect(page.locator('.bdi-lv-unit .bdi-lv-reftext')).toHaveText(/^over In2\.Cu \(\+3V3\) · 1\.235 mm$/);
  const d = await page.evaluate(() => window.demo.view.draft.items[0]);
  expect(d.reference.chosen).toBe(true);
  expect(d.tier1.z).toBeGreaterThan(z0 + 20); // over a plane 6x further away
  expect(await camera(page)).toEqual(before);
  expect(errors).toEqual([]);
});

test('the reference of a stripline (both planes) and of CPWG (the ground beside it)', async ({ page }) => {
  await openRoute(page, 'inner');
  await page.evaluate(() => window.demo.view.add('DATA'));
  await fromRoute(page, 'DATA');
  await expect(page.locator('.bdi-lv-reftext')).toHaveText(/^between F\.Cu \(GND\) · 0\.2 mm and In2\.Cu \(GND\) · [\d.]+ mm$/);
  await expect(page.locator('.bdi-lv-xs svg .bdi-xs-plane')).toHaveCount(2);
  await openRoute(page, 'cpwg');
  await page.evaluate(() => window.demo.view.add('RF'));
  await fromRoute(page, 'RF');
  await expect(page.locator('.bdi-lv-reftext')).toHaveText(/^over In1\.Cu \(GND\) · 0\.2 mm · ground beside it, g 0\.155 mm$/);
});

test('nets by name: fuzzy type-ahead, ↓ Enter, several in a row, and the pair field suggests the partner', async ({ page }) => {
  const errors = await openRoute(page, 'split');
  const before = await camera(page);
  const box = page.locator('.bdi-lv-find .bdi-np-input');
  await box.fill('sg');
  await expect(page.locator('.bdi-lv-find .bdi-np-opt').first()).toContainText('SIG');
  await expect(page.locator('.bdi-lv-find .bdi-np-opt').first().locator('.bdi-np-meta')).toHaveText(/F\.Cu/);
  await box.press('Enter');
  expect(await nets(page)).toEqual(['SIG']);
  await expect(box).toHaveValue('');
  await box.fill('ba');
  await box.press('Enter');
  expect(await nets(page)).toEqual(['SIG', 'BARE']);
  await box.fill('3');
  await box.press('ArrowDown');
  await box.press('ArrowUp');
  await expect(page.locator('.bdi-lv-find .bdi-np-opt.is-active')).toHaveText(/\+3V3/);
  await box.press('Escape');
  await expect(page.locator('.bdi-lv-find .bdi-np-list')).toBeHidden();
  expect(await camera(page)).toEqual(before);
  // the pair field: CM5's auto-named nets, the one running beside it first
  await open(page);
  const p = await kicadTrack(page, PAIR[0]);
  const n = await kicadTrack(page, PAIR[1]);
  const pn = await page.evaluate(([p, n]) => { const v = window.demo.view; const a = v.pickAt(p.x, p.y).net; v.clear(); return [a, v.pickAt(n.x, n.y).net]; }, [p, n]);
  await page.evaluate((a) => { window.demo.view.clear(); window.demo.view.add(a); }, pn[0]);
  await page.locator('.bdi-lv-unit .bdi-ctl-pairwith').click();
  const pairBox = page.locator('.bdi-lv-unit .bdi-np-input');
  await pairBox.focus();
  await expect(page.locator('.bdi-lv-unit .bdi-np-opt').first()).toHaveAttribute('data-net', pn[1]);
  await pairBox.press('ArrowDown');
  await pairBox.press('Enter');
  expect((await page.evaluate(() => window.demo.view.selection.pairs)).map((q) => [...q].sort())).toEqual([[...pn].sort()]);
  expect(errors).toEqual([]);
});

test('the view an app kept: region and layer on create', async ({ page }) => {
  await openRoute(page, 'split');
  const kept = await page.evaluate(async () => {
    const { view } = window.demo;
    view.stage.zoomTo({ minX: 5, maxX: 9, minY: 0, maxY: 4 });
    view.show('In1.Cu');
    await view.stage.ready();
    const state = view.state;
    view.destroy();
    const { layerView } = await import('/src/impedance/ui/index.js');
    const again = layerView(document.getElementById('view'), { board: window.demo.board, copper: window.demo.copper, ...state });
    await again.stage.ready();
    return { state, now: again.state };
  });
  expect(kept.now.layer).toBe('In1.Cu');
  expect(kept.now.region.cx).toBeCloseTo(kept.state.region.cx, 6);
  expect(kept.now.region.w).toBeCloseTo(kept.state.region.w, 6);
});

test('a still view draws no frames (no redraw loop between the highlight and the view event)', async ({ page }) => {
  await openRoute(page, 'split');
  await page.evaluate(() => window.demo.view.add('SIG'));
  await page.evaluate(() => window.demo.view.stage.ready());
  const frames = await page.evaluate(async () => {
    const { stage } = window.demo.view;
    const a = stage.stats().frames;
    await new Promise((r) => setTimeout(r, 500));
    return stage.stats().frames - a;
  });
  expect(frames).toBeLessThan(3);
});

test('Z along the route draws without a net-class target, and takes the form\'s target (band, verdict colors)', async ({ page }) => {
  const errors = await openRoute(page, 'cpwg');
  await page.evaluate(() => window.demo.view.add('RF'));
  const prof = page.locator('.bdi-lv-profile');
  await expect(prof).toBeVisible();
  await expect(prof.locator('.bdi-rp')).toHaveAttribute('data-state', 'ready', { timeout: 60_000 });
  const segs = prof.locator('.bdi-seg');
  await expect(segs).not.toHaveCount(0);
  // every segment is stroked: a class with no target used to draw `stroke: none`
  const strokes = await segs.evaluateAll((ls) => ls.map((l) => getComputedStyle(l).stroke));
  expect(strokes.every((s) => s && s !== 'none')).toBe(true);
  // held to the form's target: the band and the target line, segments coloured by verdict
  await expect(prof.locator('.bdi-band')).toHaveCount(1);
  expect(await segs.evaluateAll((ls) => ls.every((l) => /bdi-(ok|warn|bad)/.test(l.getAttribute('class'))))).toBe(true);
  const band = () => prof.locator('.bdi-band').evaluate((r) => +r.getAttribute('y'));
  const y50 = await band();
  // a new target in the form moves the band (no new analysis)
  await page.locator('.bdi-ctl-target').fill('75');
  await page.locator('.bdi-ctl-target').dispatchEvent('input');
  await expect.poll(band, { timeout: 10_000 }).not.toBe(y50);
  await shot(page, 'route-profile');
  expect(errors).toEqual([]);
});

/** The screen point of a board point on the layer view's stage. */
const onStage = (page, [x, y]) => page.evaluate(([x, y]) => {
  const { stage } = window.demo.view;
  const [px, py] = stage.toScreen(x, y);
  const r = stage.panes[0].el.getBoundingClientRect();
  return [r.left + px, r.top + py];
}, [x, y]);

test('Z along the whole route: two nets through R1 and their vias as one path under the board; hover syncs both ways', async ({ page }) => {
  const errors = await openRoute(page, 'chain');
  await page.evaluate(() => { window.demo.view.add('SIG_B'); window.demo.view.add('SIG_A'); });
  const prof = page.locator('.bdi-lv-profile');
  await expect(prof.locator('.bdi-rp')).toHaveAttribute('data-state', 'ready', { timeout: 60_000 });
  // under the board, as wide as it
  const [stageBox, profBox] = [await page.locator('.bdi-lv-main').boundingBox(), await prof.boundingBox()];
  expect(profBox.y).toBeGreaterThanOrEqual(stageBox.y + stageBox.height - 1);
  expect(profBox.y - (stageBox.y + stageBox.height)).toBeLessThan(20);
  expect(Math.abs(profBox.width - stageBox.width)).toBeLessThan(2);
  expect((await prof.locator('.bdi-rp-chart').boundingBox()).height).toBeGreaterThanOrEqual(220);
  // the whole path: both nets, the part between them, the vias, the ends
  const path = await page.evaluate(() => { const p = window.demo.view.profile.path; return { length: p.length, nets: p.units.map((u) => u.nets[0]), marks: p.marks.map((m) => [m.type, m.label]) }; });
  expect(path.nets).toEqual(['SIG_B', 'SIG_A']); // the first picked is where it starts
  expect(path.length).toBeCloseTo(38.4, 6);
  for (const label of ['J1', 'R1', 'U1']) await expect(prof.locator('.bdi-rp-mlabel', { hasText: label })).toHaveCount(1);
  await expect(prof.locator('.bdi-rp-mark.is-via')).toHaveCount(2);
  await expect(prof.locator('.bdi-rp-sum')).toContainText('38.4 mm');
  const units = await prof.locator('.bdi-rp-net').allTextContents();
  expect(units).toEqual(['SIG_B', 'SIG_A']);
  // hover SIG_B's F.Cu run at x = 18: the cut across it on the board, the plot's cursor 22 mm along (J1 → 16 → 4 → 2)
  await page.evaluate(() => window.demo.view.stage.zoomTo({ minX: 0, maxX: 24, minY: 0, maxY: 10 }));
  await page.evaluate(() => window.demo.view.stage.ready());
  await page.mouse.move(...(await onStage(page, [18, 4])));
  await expect(page.locator('.bdi-hl .bdi-cross')).toHaveCount(1);
  const cut = await page.locator('.bdi-hl .bdi-cross').getAttribute('d');
  const [x0, y0, x1, y1] = cut.match(/^M([-\d.e]+),([-\d.e]+)L([-\d.e]+),([-\d.e]+)/).slice(1).map(Number);
  expect(x0).toBeCloseTo(18, 3); expect(x1).toBeCloseTo(18, 3);  // across the trace (it runs along x)
  expect(Math.abs(y1 - y0)).toBeGreaterThan(0.5);
  const now = await prof.locator('.bdi-rp-chart').getAttribute('aria-valuenow');
  expect(Number(now)).toBeCloseTo(22, 1);
  await expect(prof.locator('.bdi-rp-cursor')).toHaveAttribute('visibility', 'visible');
  await expect(prof.locator('.bdi-rp-tip')).toContainText('Ω');
  await expect(prof.locator('.bdi-rp-tip')).toContainText('F.Cu');
  await expect(prof.locator('.bdi-rp-tip')).toContainText('w 0.3 mm');
  await expect(prof.locator('.bdi-rp-tip')).toContainText('over In1.Cu (GND)');
  // the cursor follows the pointer: the run back to J1, 6 mm from it
  await page.mouse.move(...(await onStage(page, [10, 8])));
  expect(Number(await prof.locator('.bdi-rp-chart').getAttribute('aria-valuenow'))).toBeCloseTo(6, 1);
  await shot(page, 'route-hover-board');
  // off the traces: the cut and cursor go
  await page.mouse.move(...(await onStage(page, [2, 1])));
  await expect(page.locator('.bdi-hl .bdi-cross')).toHaveCount(0);
  await expect(prof.locator('.bdi-rp-tip')).toBeHidden();
  // the plot the other way: hovering 13 mm along puts the dot on SIG_A's B.Cu run near R1 (path SIG_B then SIG_A)
  const box = await prof.locator('.bdi-hit').boundingBox();
  const at = (s) => [box.x + (box.width * s) / path.length, box.y + box.height / 2];
  await page.mouse.move(...at(26.4 + 2));
  await expect(page.locator('.bdi-hl .bdi-marker')).toHaveCount(1);
  const dot = await page.locator('.bdi-hl .bdi-marker').evaluate((c) => [+c.getAttribute('cx'), +c.getAttribute('cy')]);
  expect(dot[0]).toBeCloseTo(10, 0); expect(dot[1]).toBeCloseTo(4, 3);
  await expect(page.locator('.bdi-hl .bdi-cross')).toHaveCount(1);
  await expect(prof.locator('.bdi-rp-tip')).toContainText('B.Cu');
  await expect(prof.locator('.bdi-rp-tip')).toContainText('under In2.Cu (+3V3)');
  await shot(page, 'route-hover-plot');
  // the keyboard: ← → move the cursor (and the dot)
  await prof.locator('.bdi-rp-chart').focus();
  const before = Number(await prof.locator('.bdi-rp-chart').getAttribute('aria-valuenow'));
  await page.keyboard.press('ArrowRight');
  expect(Number(await prof.locator('.bdi-rp-chart').getAttribute('aria-valuenow'))).toBeCloseTo(before + 0.384, 2);
  await page.keyboard.press('Home');
  expect(Number(await prof.locator('.bdi-rp-chart').getAttribute('aria-valuenow'))).toBe(0);
  expect(errors).toEqual([]);
});

test('a trace onto a wide pad: the plot shades and names the pads, the tooltip and cut use the pad\'s copper', async ({ page }) => {
  const errors = await openRoute(page, 'launch');
  await page.evaluate(() => window.demo.view.add('RF'));
  const prof = page.locator('.bdi-lv-profile');
  await expect(prof.locator('.bdi-rp')).toHaveAttribute('data-state', 'ready', { timeout: 60_000 });
  await expect(prof.locator('.bdi-rp-cu.is-pad')).toHaveCount(3);
  await expect(prof.locator('.bdi-rp-cu.is-teardrop')).toHaveCount(1);
  // named where there is room (TP1.1's 0.5 mm is not: its shading's tooltip names it)
  expect(await prof.locator('.bdi-rp-culabel').evaluateAll((ts) => ts.map((t) => t.firstChild.textContent))).toEqual(['J1.1', 'TP2.1']);
  await expect(prof.locator('.bdi-rp-cu.is-pad').last().locator('title')).toContainText('pad TP1.1');
  await expect(prof.locator('.bdi-rp-cu.is-pad').first().locator('title')).toContainText('pad J1.1: the conductor is its copper, up to 1.5 mm wide (track 0.3 mm)');
  // hover the track where it runs over J1.1 (x 0..3): 1.5 mm wide, 0.3 mm to the pour, In1.Cu cut out under it
  await page.evaluate(() => window.demo.view.stage.zoomTo({ minX: -1, maxX: 9, minY: -3, maxY: 3 }));
  await page.evaluate(() => window.demo.view.stage.ready());
  await page.mouse.move(...(await onStage(page, [2.5, 0])));
  const tip = prof.locator('.bdi-rp-tip');
  await expect(tip).toContainText('w 1.5 mm (pad J1.1)');
  await expect(tip).toContainText('ground 0.301 mm');
  await expect(tip).toContainText('over In2.Cu (GND)');
  const want = await page.evaluate(() => window.demo.view.profile.path.sections.find((x) => x.s0 === 0).z);
  await expect(tip.locator('.bdi-rp-tz')).toContainText(`${want.toFixed(1)} Ω`);
  // the cut spans the pad and its gaps (not the 0.3 mm track's)
  const cut = await page.locator('.bdi-hl .bdi-cross').getAttribute('d');
  const [, y0, , y1] = cut.match(/^M([-\d.e]+),([-\d.e]+)L([-\d.e]+),([-\d.e]+)/).slice(1).map(Number);
  expect(Math.abs(y1 - y0)).toBeGreaterThan(4);
  // off the pad, the track alone
  await page.mouse.move(...(await onStage(page, [5, 0])));
  await expect(tip).toContainText('w 0.3 mm');
  await expect(tip).not.toContainText('pad');
  await page.mouse.move(...(await onStage(page, [8.2, 0])));
  await expect(tip).toContainText('w 1 mm (pad TP2.1)');
  await shot(page, 'route-pad');
  expect(errors).toEqual([]);
});

test('a pair through AC caps is one path; a tap on a selected trace shows its place instead of dropping it', async ({ page }) => {
  const errors = await openRoute(page, 'chain');
  await page.evaluate(() => { const v = window.demo.view; for (const n of ['TX_P', 'TX_N', 'TX_CP', 'TX_CN']) v.add(n); v.pair('TX_P', 'TX_N'); v.pair('TX_CP', 'TX_CN'); });
  const prof = page.locator('.bdi-lv-profile');
  await expect(prof.locator('.bdi-rp')).toHaveAttribute('data-state', 'ready', { timeout: 60_000 });
  expect(await page.evaluate(() => window.demo.view.profile.path.units.map((u) => [u.nets.join(' '), u.key]))).toEqual([['TX_P TX_N', 'Zdiff'], ['TX_CP TX_CN', 'Zdiff']]);
  await expect(prof.locator('.bdi-rp-mlabel.is-part')).toHaveText(/^C1 C2C1, C2: the route goes through them/); // the label, then its tooltip
  await expect(prof.locator('.bdi-rp-net')).toHaveText(['⇄ TX_P TX_N', '⇄ TX_CP TX_CN']);
  await page.evaluate(() => window.demo.view.stage.zoomTo({ minX: 12, maxX: 18, minY: -6, maxY: -2 }));
  await page.evaluate(() => window.demo.view.stage.ready());
  const p = await onStage(page, [15, -4.2]); // TX_CN
  await touch(page, 'pointerdown', p);
  await touch(page, 'pointerup', p);
  await expect(page.locator('.bdi-hl .bdi-cross')).toHaveCount(1);
  expect(Number(await prof.locator('.bdi-rp-chart').getAttribute('aria-valuenow'))).toBeCloseTo(13.5, 1);
  await expect(prof.locator('.bdi-rp-tip')).toContainText(/gap 0\.15\d* mm/);
  expect(await nets(page)).toHaveLength(4); // still selected
  await shot(page, 'route-pair');
  expect(errors).toEqual([]);
});

test('a preview: other nets on their layer, lighter, the selection, plot and layer event untouched; null restores', async ({ page }) => {
  const errors = await openRoute(page, 'chain');
  await page.evaluate(() => { const { view } = window.demo; window.demo.layers = []; view.on('layer', (s) => window.demo.layers.push(s.layer)); view.add('SIG_A'); view.show('B.Cu'); window.demo.layers = []; });
  await page.locator('.bdi-lv-profile .bdi-rp[data-state=ready]').waitFor({ timeout: 60_000 });
  const before = await camera(page);
  const plot = await page.locator('.bdi-lv-profile').innerHTML();
  const drawn = (sel) => page.evaluate((sel) => [...document.querySelectorAll(`.bdi-hl${sel} .bdi-hl-track`)].length, sel);
  const shown = await drawn(':not(.is-preview)');
  // a pair, on screen: its layer (where it is routed), only its nets drawn, lighter; the camera stays
  await page.evaluate(() => window.demo.view.preview(['TX_P', 'TX_N']));
  await page.evaluate(() => window.demo.view.stage.ready());
  await expect(page.locator('.bdi-lv-caption')).toHaveText('F.Cu');
  await expect(page.locator('.bdi-lv')).toHaveClass(/is-preview/);
  expect(await page.evaluate(() => window.demo.view.previewing)).toEqual(['TX_P', 'TX_N']);
  const txTracks = await page.evaluate(() => window.demo.copper.tracks.filter((t) => t.net === 'TX_P' || t.net === 'TX_N').length);
  expect(await drawn('.is-preview')).toBe(txTracks);
  const opacity = await page.evaluate(() => getComputedStyle(document.querySelector('.bdi-hl.is-preview .bdi-hl-track:not(.bdi-hl-other)')).opacity);
  expect(Number(opacity)).toBeLessThan(1);
  expect(await page.evaluate(() => window.demo.view.selection.nets)).toEqual(['SIG_A']);
  expect(await page.locator('.bdi-lv-profile').innerHTML()).toBe(plot);
  expect(await camera(page)).toEqual(before);
  // another, on a given layer; then null: the layer and the selection's highlight as they were, no 'layer' event
  await page.evaluate(() => window.demo.view.preview(['SIG_B'], { layer: 'B.Cu' }));
  await expect(page.locator('.bdi-lv-caption')).toHaveText('B.Cu');
  await page.evaluate(() => window.demo.view.preview(null));
  await page.evaluate(() => window.demo.view.stage.ready());
  await expect(page.locator('.bdi-lv-caption')).toHaveText('B.Cu');
  await expect(page.locator('.bdi-lv')).not.toHaveClass(/is-preview/);
  expect(await page.evaluate(() => window.demo.view.previewing)).toBeNull();
  expect(await drawn(':not(.is-preview)')).toBe(shown);
  expect(await page.evaluate(() => window.demo.layers)).toEqual([]);
  // off screen: the camera goes to it, and back after
  await page.evaluate(() => {
    const { view, copper } = window.demo;
    const t = copper.tracks.find((x) => x.net === 'SIG_A');
    view.stage.zoomTo({ minX: t.start[0] - 0.2, maxX: t.start[0] + 0.2, minY: t.start[1] - 0.2, maxY: t.start[1] + 0.2 });
  });
  await page.evaluate(() => window.demo.view.stage.ready());
  const zoomed = await view(page);
  await page.evaluate(() => window.demo.view.preview(['TX_P', 'TX_N']));
  expect(await view(page)).not.toEqual(zoomed);
  await page.evaluate(() => window.demo.view.preview(null));
  expect(await view(page)).toEqual(zoomed);
  expect(errors).toEqual([]);
});
