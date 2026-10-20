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
