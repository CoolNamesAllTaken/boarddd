import { test, expect } from '@playwright/test';

// SHOTS=dir writes the screenshots used in PR descriptions.
const shots = process.env.SHOTS;

test('a real kicad-cli GLB loads, matches by name and renders', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 600 });
  for (const [theme, view] of [['light', 'iso'], ['dark', 'top'], ['light', 'bottom']]) {
    await page.goto(`/test/browser/pages/models.html?theme=${theme}&view=${view}`);
    await page.waitForFunction(() => window.ready, null, { timeout: 60_000 });
    expect(await page.evaluate(() => window.errors)).toEqual([]);
    const report = await page.evaluate(() => window.report);
    expect(report.method).toBe('name');
    expect(report.matched).toBe(11);
    // Something other than background in the middle of the frame.
    const spread = await page.evaluate(() => {
      window.v.render();
      const c = document.createElement('canvas');
      c.width = 200; c.height = 120;
      const ctx = c.getContext('2d');
      ctx.drawImage(window.v.canvas, window.v.canvas.width / 2 - 100, window.v.canvas.height / 2 - 60, 200, 120, 0, 0, 200, 120);
      const d = ctx.getImageData(0, 0, 200, 120).data;
      const set = new Set();
      for (let i = 0; i < d.length; i += 4) set.add((d[i] >> 4) * 256 + (d[i + 1] >> 4) * 16 + (d[i + 2] >> 4));
      return set.size;
    });
    expect(spread).toBeGreaterThan(6);
    if (shots) await page.locator('#v').screenshot({ path: `${shots}/glb-${theme}-${view}.png` });
  }
});

test('STEP colour, for the record', async ({ page }) => {
  test.skip(!shots, 'screenshots only');
  await page.goto('/test/browser/pages/scene.html?model=step');
  await page.waitForFunction(() => window.ready, null, { timeout: 60_000 });
  for (const view of ['iso', 'top']) {
    await page.evaluate((v) => window.v.setView(v), view);
    await page.waitForTimeout(200);
    await page.locator('#v').screenshot({ path: `${shots}/step-blue-${view}.png` });
  }
});

test('a Draco board GLB loads coarse, upgrades to full, keeps names and boxes', async ({ page }) => {
  await page.goto('/test/browser/pages/boardglb.html');
  await page.waitForFunction(() => window.ready, null, { timeout: 60_000 });
  expect(await page.evaluate(() => window.errors)).toEqual([]);
  const r = await page.evaluate(() => window.report);
  for (const ref of ['R1', 'R5', 'U1', 'U3', 'C1', 'D1', 'Q1']) expect(r.names).toContain(ref);
  expect(r.names.some((n) => n.startsWith('=>['))).toBe(true);
  expect(r.meta.compression).toBe('draco');
  expect(r.swapped).toBeGreaterThan(10);
  expect(r.full).toBeGreaterThan(r.coarse);
  // mm, z up: R1 on top of the 1.6 mm board, R5 under it, a 0402 about a millimeter long.
  expect(r.R1[0][2]).toBeGreaterThan(1.0);
  expect(r.R5[1][2]).toBeLessThan(0.1);
  expect(r.R1[1][0] - r.R1[0][0]).toBeGreaterThan(0.9);
  expect(r.R1[1][0] - r.R1[0][0]).toBeLessThan(1.2);
  // A ray from above lands on the part under it.
  expect(r.hitR1).toBe('R1');
  expect(r.hitU1).toBe('U1');
  if (shots) await page.locator('#v').screenshot({ path: `${shots}/boardglb.png` });
});
