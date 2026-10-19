import { expect, test } from '@playwright/test';

// boarddd/impedance/ui layerView on examples/layers.html: CM5 MINIMA's copper read from its plain Gerbers (no X2:
// nets N1, N2... from boarddd.connectivity), its KiCad stackup. The stepper walks the stackup; a right click on one
// line of the Ethernet pair picks its net on every layer and finds its partner from the copper; the form gives Z
// and the width for a target, and its draft matches the KiCad file's own geometry. LV_SHOTS=dir saves screenshots.

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

test('right click a trace: its net on every layer, its partner from the copper, Z and the width for a target', async ({ page }) => {
  const errors = await open(page);
  const p = await kicadTrack(page, PAIR[0]);
  const n = await kicadTrack(page, PAIR[1]);
  await zoomTo(page, p);
  const [px, py] = await screenOf(page, p.x, p.y);
  await page.mouse.click(px, py, { button: 'right' });
  await expect(page.locator('.bdi-ctl')).toBeVisible();
  const sel = await page.evaluate(() => window.demo.view.selection);
  expect(sel.nets).toHaveLength(1);
  expect(sel.nets[0]).toMatch(/^N\d+$/);
  expect(await page.locator('.bdi-hl-track').count()).toBeGreaterThan(2);
  // the partner: the auto-named net that runs beside it, the one the KiCad file calls TRD0_N
  const partner = page.locator('.bdi-ctl-partner');
  await expect(partner).toBeVisible();
  const partnerNet = (await partner.textContent()).match(/N\d+/)[0];
  const atN = await page.evaluate(([x, y]) => window.demo.view.pickAt(x, y)?.net, [n.x, n.y]);
  expect(atN).toBe(partnerNet);
  await page.evaluate((nets) => window.demo.view.pick(nets), sel.nets); // back to the first net
  await page.locator('.bdi-ctl-partner').click();
  await expect(page.locator('.bdi-ctl-net')).toContainText('⇄');
  await page.locator('.bdi-ctl-target').fill('100');
  await page.waitForFunction(() => window.demo.view.panel?.result, null, { timeout: 60_000 });
  await expect(page.locator('.bdi-ctl-z')).toHaveText(/≈ \d+\.\d Ω/);
  await expect(page.locator('.bdi-ctl-sug')).toHaveText(/100 Ω at w 0\.\d+ mm|✓ w/);
  const draft = await page.evaluate(() => window.demo.view.draft);
  expect(draft.kind).toBe('differential');
  expect(draft.layer).toBe(p.main); // where most of the route is (the pair breaks out on F.Cu, runs on B.Cu)
  expect(Math.abs(draft.width - p.mainWidth)).toBeLessThan(1e-3); // the Gerber track is the KiCad track
  expect(draft.gap).toBeGreaterThan(0);
  expect(draft.target).toBe(100);
  expect(draft.tier1.z).toBeGreaterThan(60);
  expect(draft.anchors).toHaveLength(2);
  const control = page.evaluate(() => new Promise((r) => window.demo.view.on('control', r)));
  await page.locator('.bdi-ctl-ok').click();
  expect((await control).nets).toEqual(draft.nets);
  await shot(page, 'pair-100');
  // on another layer the net's copper there is drawn as such (fainter): the stop shows the net is on it
  await expect(page.locator('.bdi-lv-stop.has-net')).not.toHaveCount(0);
  expect(errors).toEqual([]);
});

test('click selects a segment; pair with… takes the next click; a long-press is a right click', async ({ page }) => {
  await open(page);
  const p = await kicadTrack(page, PAIR[0]);
  const n = await kicadTrack(page, PAIR[1]);
  await zoomTo(page, p);
  const [px, py] = await screenOf(page, p.x, p.y);
  await page.mouse.click(px, py);
  await expect(page.locator('.bdi-lv-seg')).toContainText(`w ${+p.width.toFixed(4)} mm`);
  await expect(page.locator('.bdi-ctl')).toBeHidden();
  // long-press (touch): the context gesture (the timer runs late while SwiftShader draws: let the stage settle)
  await page.evaluate(() => window.demo.view.stage.ready());
  await page.evaluate(([x, y]) => {
    const pane = window.demo.view.stage.panes[0].el;
    pane.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 7, pointerType: 'touch', clientX: x, clientY: y, button: 0, bubbles: true, isPrimary: true }));
  }, [px, py]);
  await expect(page.locator('.bdi-ctl')).toBeVisible({ timeout: 15_000 });
  await page.evaluate(([x, y]) => {
    window.demo.view.stage.panes[0].el.dispatchEvent(new PointerEvent('pointerup', { pointerId: 7, pointerType: 'touch', clientX: x, clientY: y, button: 0, bubbles: true }));
  }, [px, py]);
  const first = await page.evaluate(() => window.demo.view.selection.nets);
  await page.locator('.bdi-ctl-pairwith').click();
  const [qx, qy] = await screenOf(page, n.x, n.y);
  await page.mouse.click(qx, qy);
  const pair = await page.evaluate(() => window.demo.view.selection.nets);
  expect(pair).toHaveLength(2);
  expect(pair).toContain(first[0]);
});
