// Pixel geometry in headless Chromium (WebGL2 via SwiftShader): looking straight down (orthographic),
// every slotted hole must show the background through a STADIUM -- including the corners of its
// bounding box' inner flanks that an ellipse of the same size would cover -- and copper/board just
// outside it. Run for the footprint builder (src/footprint) and the Gerber board (src/board), which
// takes its holes from KiCad's own drill file (G85 slots).
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { parseKicadFootprint } from '../../src/footprint/kicad_mod.js';
import { padDrillSlot, padToKicad, kicadToBoard, PLATING_MM } from '../../src/geom/index.js';

const FP = 'test/fixtures/footprints/MountingHole_Slotted_boarddd.kicad_mod';
const fp = parseKicadFootprint(readFileSync(FP, 'utf8'));
const MAGENTA = ([r, g, b]) => r > 200 && g < 60 && b > 200;

/** Sample points around each drilled pad: [{pad, point, want: 'open' | 'solid'}]. */
function probes(at = [0, 0], bore = (pad) => 0) {
  const out = [];
  for (const pad0 of fp.pads) {
    const pad = { ...pad0, at: [pad0.at[0] + at[0], pad0.at[1] + at[1], pad0.at[2]] };
    const slot = padDrillSlot(pad);
    if (!slot) continue;
    const [e1, e2] = slot.ends.map((e) => kicadToBoard(...e));
    const dir = [e2[0] - e1[0], e2[1] - e1[1]];
    const len = Math.hypot(...dir) || 1;
    const u = [dir[0] / len, dir[1] / len], n = [-u[1], u[0]];
    const r = slot.radius - bore(pad);
    // along the flanks (including right at the cap tangent points: an ellipse misses these) and around the caps
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      for (const s of [-1, 1]) {
        const base = [e1[0] + dir[0] * t, e1[1] + dir[1] * t];
        out.push({ pad: pad.number, want: 'open', point: [base[0] + n[0] * s * (r - 0.08), base[1] + n[1] * s * (r - 0.08)] });
        out.push({ pad: pad.number, want: 'solid', point: [base[0] + n[0] * s * (slot.radius + 0.12), base[1] + n[1] * s * (slot.radius + 0.12)] });
      }
    }
    for (let k = 0; k < 8; k++) {
      const a = (k * Math.PI) / 8 - Math.PI / 2;
      for (const [e, sgn] of [[e2, 1], [e1, -1]]) {
        const d = [sgn * (u[0] * Math.cos(a) - u[1] * Math.sin(a)), sgn * (u[1] * Math.cos(a) + u[0] * Math.sin(a))];
        out.push({ pad: pad.number, want: 'open', point: [e[0] + d[0] * (r - 0.08), e[1] + d[1] * (r - 0.08)] });
        out.push({ pad: pad.number, want: 'solid', point: [e[0] + d[0] * (slot.radius + 0.12), e[1] + d[1] * (slot.radius + 0.12)] });
      }
    }
  }
  return out;
}

async function check(page, url, list) {
  await page.goto(url);
  const h = await page.waitForFunction(() => window.harness, null, { timeout: 120_000 }).then((x) => x.jsonValue());
  expect(h.error).toBeUndefined();
  expect(h.info.pxPerMm).toBeGreaterThan(40);
  const colors = await page.evaluate((pts) => window.sample(pts.map((p) => [...p, 0.8])), list.map((p) => p.point));
  const wrong = list.filter((p, i) => (p.want === 'open') !== MAGENTA(colors[i])).map((p, i) => ({ ...p, color: colors[list.indexOf(p)] }));
  expect(wrong, JSON.stringify(wrong.slice(0, 5))).toEqual([]);
}

for (const view of ['top', 'bottom']) {
  test(`footprint: slotted holes are stadiums (${view})`, async ({ page }) => {
    await check(page, `/test/browser/pages/view.html?fp=/${FP}&view=${view}&w=1000&h=1000`, probes());
  });

  test(`gerber board: KiCad's G85 slots are stadiums, bores inside the plating (${view})`, async ({ page }) => {
    // H1 sits at KiCad (122, 97) on test/fixtures/slots-board; the region is its courtyard
    const list = probes([122, 97], (pad) => (pad.type === 'thru_hole' ? PLATING_MM : 0));
    await check(page, `/test/browser/pages/view.html?gerber=/test/fixtures/slots-board/&view=${view}&w=1000&h=1000&region=119,-106,132,-93`, list);
  });
}
