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
