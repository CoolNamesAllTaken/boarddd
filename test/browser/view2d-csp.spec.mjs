import { writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { STAGE_CSS } from '../../src/view2d/stage.js';

// Under style-src 'self' (kipr's viewers) the injected <style> is refused: injectCss: false and STAGE_CSS in
// the page's own stylesheet. Also the busy event and the pane class / data-side hooks.

test('strict CSP: no injected style, no violation; busy only while re-rendering; pane hooks', async ({ page }) => {
  // the page's own stylesheet: the host size plus STAGE_CSS, as an app would ship it
  writeFileSync(new URL('./pages/view2d-csp.css', import.meta.url), `#host{position:absolute;left:0;top:0;width:800px;height:500px}\n${STAGE_CSS}`);
  page.on('pageerror', (e) => { throw e; });
  await page.goto('/test/browser/pages/view2d-csp.html');
  await page.waitForFunction(() => window.ready);
  const r = await page.evaluate(() => window.run(false));
  expect(r.violations).toEqual([]);
  expect(r.styles).toBe(0);
  expect(r.position).toBe('relative'); // from the stylesheet
  expect(r.cls).toBe('bd2-pane mine');
  expect(r.side).toBe('head');
  expect(r.css).toBe(true);
  expect(r.busy[0]).toBe(true); // the first render
  expect(r.busy.slice(0, r.before).at(-1)).toBe(false); // idle after the first render
  expect(r.panBusy, JSON.stringify(r)).toBe(0); // a pan needs no re-render: never busy
  expect(r.busy.slice(r.before), JSON.stringify(r)).toEqual([true, false]); // the zoom

  // with the default (injectCss), the browser refuses the <style> and reports it
  await page.goto('/test/browser/pages/view2d-csp.html');
  await page.waitForFunction(() => window.ready);
  const d = await page.evaluate(() => window.run(true));
  expect(d.violations.some((v) => v.startsWith('style-src'))).toBe(true);
});
