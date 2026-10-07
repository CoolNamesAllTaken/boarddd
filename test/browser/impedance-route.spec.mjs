import { expect, test } from "@playwright/test";

// analyzeNet in a real browser: royalblue54L_feather's USB pair (fixtures/impedance/route/royalblue-usb.json.gz,
// unpacked with the platform DecompressionStream), timed cold and again with a shared cache; the route is then
// drawn as SVG coloured by impedance over the copper (ROUTE_SHOTS=dir saves the screenshot).

test("analyzeNet runs in the browser and draws the route by impedance", async ({ page }, info) => {
  page.on("pageerror", (error) => {
    throw error;
  });
  await page.goto("/test/browser/pages/blank.html");
  const r = await page.evaluate(async () => {
    const { analyzeNet, validateImpedance } = await import("/src/impedance/index.js");
    const res = await fetch("/fixtures/impedance/route/royalblue-usb.json.gz");
    const text = await new Response(res.body.pipeThrough(new DecompressionStream("gzip"))).text();
    const { board, copper } = JSON.parse(text);
    const nets = ["/Debugger/D+", "/Debugger/D-"];
    const cache = new Map();
    const t0 = performance.now();
    const doc = analyzeNet(board, copper, nets, { cache });
    const cold = performance.now() - t0;
    const t1 = performance.now();
    const again = analyzeNet(board, copper, nets, { cache });
    const warm = performance.now() - t1;
    // draw: F.Cu/B.Cu copper dim, the route's sections coloured by Zdiff (90 Ω nominal for USB)
    const pts = doc.sections.flatMap((s) => [s.start, s.end]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => -p[1]);
    const [x0, y0, x1, y1] = [Math.min(...xs) - 1.5, Math.min(...ys) - 1.5, Math.max(...xs) + 1.5, Math.max(...ys) + 1.5];
    const ring = (r) => r.map(([x, y]) => `${x},${-y}`).join(" ");
    const parts = [];
    for (const zn of copper.zones.filter((z) => z.layer === "F.Cu"))
      for (const f of zn.fill) parts.push(`<path fill-rule="evenodd" fill="#2b4f86" opacity="0.5" d="${[f.outline, ...f.holes].map((x) => `M${ring(x)}Z`).join("")}"/>`);
    for (const t of copper.tracks.filter((t) => t.layer === "F.Cu" && !nets.includes(t.net)))
      parts.push(`<line x1="${t.start[0]}" y1="${-t.start[1]}" x2="${t.end[0]}" y2="${-t.end[1]}" stroke="#667" stroke-width="${t.width}" stroke-linecap="round"/>`);
    const colour = (zv) => (zv == null ? "#999" : `hsl(${Math.max(0, Math.min(240, 120 - ((zv - 90) / 90) * 400))} 85% 55%)`);
    for (const s of doc.sections) {
      const zv = s.z ? s.z.Zdiff ?? s.z.Z0 : null;
      parts.push(`<line x1="${s.start[0]}" y1="${-s.start[1]}" x2="${s.end[0]}" y2="${-s.end[1]}" stroke="${colour(zv)}" stroke-width="${s.geometry.width * 1.4}" stroke-linecap="round" ${s.layer === "B.Cu" ? 'stroke-dasharray="0.15 0.1"' : ""}/>`);
    }
    for (const d of doc.discontinuities.filter((d) => d.type === "via" || d.type === "plane_gap"))
      parts.push(`<circle cx="${d.at[0]}" cy="${-d.at[1]}" r="0.18" fill="none" stroke="#fff" stroke-width="0.04"/>`);
    document.body.style.margin = "0";
    document.body.style.background = "#111";
    document.body.innerHTML = `<svg id="route" xmlns="http://www.w3.org/2000/svg" width="1000" height="${Math.round((1000 * (y1 - y0)) / (x1 - x0))}" viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}">${parts.join("")}</svg>`;
    return { errors: validateImpedance(doc), cold, warm, solves: doc.timing.solves, againSolves: again.timing.solves, sections: doc.sections.length, length: doc.summary.length };
  });
  expect(r.errors).toEqual([]);
  expect(r.againSolves).toBe(0);
  expect(Math.abs(r.length - 22.55)).toBeLessThan(0.01);
  info.annotations.push({ type: "analyzeNet", description: `cold ${r.cold.toFixed(0)} ms (${r.solves} solves), with the cache ${r.warm.toFixed(0)} ms` });
  console.log(`analyzeNet in Chromium: cold ${r.cold.toFixed(0)} ms (${r.solves} solves), cached ${r.warm.toFixed(0)} ms, ${r.sections} sections`);
  expect(r.cold).toBeLessThan(10000);
  await expect(page.locator("#route")).toBeVisible();
  if (process.env.ROUTE_SHOTS) await page.locator("#route").screenshot({ path: `${process.env.ROUTE_SHOTS}/browser-royalblue-usb-route.png` });
});
