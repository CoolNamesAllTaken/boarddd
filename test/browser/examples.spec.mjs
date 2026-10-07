// The example page loads the KiCad demo board from its Gerbers without errors.
import { test, expect } from '@playwright/test';

test('examples/: the demo board builds, holes punched, no page errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/examples/');
  const demo = await page.waitForFunction(() => window.demo, null, { timeout: 120_000 }).then((h) => h.jsonValue());
  expect(demo.error).toBeUndefined();
  expect(demo.holes).toBeGreaterThan(50);
  expect(demo.matched).toBeGreaterThanOrEqual(10);   // the parts kicad-cli's GLB has models for
  await page.click('#diff');
  await page.waitForTimeout(500);
  expect(errors).toEqual([]);
});

test('examples/view2d.html: every board, view and mode draws; the URL keeps the view', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('/examples/view2d.html');
  await page.waitForFunction(() => window.demo?.stage);
  const settle = () => page.evaluate(() => window.demo.stage.ready());
  await settle();
  const shots = process.env.V2_SHOTS; // V2_SHOTS=dir: screenshots for the PR
  const shot = async (name) => { if (shots) await page.screenshot({ path: `${shots}/demo-${name}.png` }); };
  for (const mode of ['Side by side', 'Diff', 'Onion', 'Swipe', 'Base', 'Head']) {
    await page.click(`#modes button:text-is("${mode}")`);
    await settle();
    await shot(`pic-${mode.split(' ')[0].toLowerCase()}`);
    expect(await page.evaluate(() => window.demo.stage.stats().tiles.flat().every((t) => t.base))).toBe(true);
  }
  await page.click('#modes button:text-is("Diff")');
  await settle();
  await expect(page.locator('#note')).toHaveText(/changed area/);
  await page.click('#views button:text-is("Bottom")');
  await settle();
  await shot('pic-bottom-diff');
  await page.selectOption('#board', 'rb');
  await settle();
  await shot('rb-bottom');
  await page.click('#views button:text-is("Top")');
  await settle();
  await shot('rb-top');
  expect(await page.locator('.tick circle').count()).toBeGreaterThan(20); // placement ticks (pos.csv lists the top side)
  await page.click('#views button:text-is("Layers")');
  await settle();
  await shot('rb-layers');
  await page.selectOption('#board', 'sch');
  await settle();
  await page.click('#modes button:text-is("Diff")');
  await settle();
  await shot('sch-diff');
  // zoom, reload: same board, mode and region
  await page.mouse.move(500, 300);
  await page.mouse.wheel(0, -600);
  await settle();
  await page.waitForTimeout(400);
  const region = await page.evaluate(() => window.demo.stage.getRegion());
  await page.reload();
  await page.waitForFunction(() => window.demo?.stage);
  await settle();
  const back = await page.evaluate(() => ({ region: window.demo.stage.getRegion(), mode: window.demo.compare.mode }));
  expect(back.mode).toBe('diff');
  expect(Math.abs(back.region.w - region.w)).toBeLessThan(region.w * 0.01);
  expect(Math.abs(back.region.cx - region.cx)).toBeLessThan(region.w * 0.01);
  expect(errors).toEqual([]);
});
