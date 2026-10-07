import { expect, test } from "@playwright/test";

// boarddd/copper's copperFromGerbers in a real browser: the Gerber-only upload path. royalblue54L_feather's
// eight copper layers, drills and outline, given as bytes (as a file input would), become a valid
// boarddd/copper@1 document with the same counts the node and Python tests see; the copper is then drawn by net
// as SVG over the page (COPPER_SHOTS=dir saves the screenshot).

const FAB = "/fixtures/royalblue54L_feather/fab/RoyalBlue54L-Feather-";
const FILES = ["F_Cu", "In1_Cu", "In2_Cu", "In3_Cu", "In4_Cu", "In5_Cu", "In6_Cu", "B_Cu", "Edge_Cuts"]
  .map((n) => `${n}.gbr`)
  .concat(["PTH.drl", "NPTH.drl"]);

test("copperFromGerbers reads a Gerber X2 upload in the browser", async ({ page }, info) => {
  page.on("pageerror", (error) => {
    throw error;
  });
  await page.goto("/test/browser/pages/blank.html");
  const result = await page.evaluate(
    async ({ fab, files }) => {
      const { copperFromGerbers, validateCopper } = await import("/src/copper/index.js");
      const upload = await Promise.all(
        files.map(async (name) => ({ name: `RoyalBlue54L-Feather-${name}`, data: new Uint8Array(await (await fetch(fab + name)).arrayBuffer()) })),
      );
      const t0 = performance.now();
      const doc = copperFromGerbers(upload, { name: "RoyalBlue54L-Feather" });
      const ms = performance.now() - t0;
      // draw F.Cu by net: fills, pads, tracks (board frame y up -> SVG y down)
      const layer = "F.Cu";
      const color = (net) => (net === "GND" ? "#3a7bd5" : net ? `hsl(${[...net].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)} 70% 55%)` : "#888");
      const pts = (ring) => ring.map(([x, y]) => `${x},${-y}`).join(" ");
      const parts = [];
      for (const z of doc.zones.filter((z) => z.layer === layer))
        for (const p of z.fill) parts.push(`<path fill-rule="evenodd" fill="${color(z.net)}" opacity="0.55" d="${[p.outline, ...p.holes].map((r) => `M${pts(r)}Z`).join("")}"/>`);
      for (const p of doc.pads.filter((p) => p.layers.includes(layer)))
        for (const ring of p.polygons) parts.push(`<polygon fill="${color(p.net)}" points="${pts(ring)}"/>`);
      for (const t of doc.tracks.filter((t) => t.layer === layer))
        parts.push(`<polyline fill="none" stroke="${color(t.net)}" stroke-linecap="round" stroke-width="${t.width}" points="${pts([t.start, ...(t.mid ? [t.mid] : []), t.end])}"/>`);
      for (const v of doc.vias) parts.push(`<circle cx="${v.at[0]}" cy="${-v.at[1]}" r="${v.diameter / 2}" fill="#e8c547"/>`);
      const xs = doc.vias.map((v) => v.at[0]), ys = doc.vias.map((v) => -v.at[1]);
      const [x0, y0, x1, y1] = [Math.min(...xs) - 6, Math.min(...ys) - 6, Math.max(...xs) + 6, Math.max(...ys) + 6];
      document.body.style.margin = "0";
      document.body.style.background = "#111";
      document.body.innerHTML = `<svg id="cu" xmlns="http://www.w3.org/2000/svg" width="1000" height="${Math.round((1000 * (y1 - y0)) / (x1 - x0))}" viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}">${parts.join("")}</svg>`;
      return {
        errors: validateCopper(doc),
        layers: doc.layers,
        tracks: doc.tracks.length,
        vias: doc.vias.length,
        pads: doc.pads.length,
        nets: doc.nets.length,
        solid: doc.planes.filter((p) => p.solid).length,
        warnings: doc.warnings,
        ms,
      };
    },
    { fab: FAB, files: FILES },
  );
  expect(result.errors).toEqual([]);
  expect(result.layers).toEqual(["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu"]);
  expect([result.tracks, result.vias, result.pads, result.nets, result.solid]).toEqual([943, 183, 991, 96, 7]);
  expect(result.warnings).toEqual([]);
  info.annotations.push({ type: "copperFromGerbers", description: `${result.ms.toFixed(0)} ms` });
  const svg = page.locator("#cu");
  await expect(svg).toBeVisible();
  if (process.env.COPPER_SHOTS) await svg.screenshot({ path: `${process.env.COPPER_SHOTS}/browser-royalblue-F_Cu.png` });
});
