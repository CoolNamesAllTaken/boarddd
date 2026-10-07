import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { gzipSync } from "node:zlib";

import { loadOdbJob } from "../../src/gerber/odb.js";
import { createZipBytesJobTree } from "../../src/gerber/odb-zip.js";

// KiCad 10's ODB++ export of the pic_programmer demo (fixtures/make_exchange.sh).
const ZIP = new Uint8Array(readFileSync(new URL("../../fixtures/pic_programmer/exchange/pic_programmer-odb.zip", import.meta.url)));
const LAYERS = [
  ["profile.gko", "gerber"],
  ["f.silkscreen.gto", "gerber"],
  ["f.mask.gts", "gerber"],
  ["top_layer.gtl", "gerber"],
  ["bottom_layer.gbl", "gerber"],
  ["b.mask.gbs", "gerber"],
  ["b.silkscreen.gbo", "gerber"],
  ["drill_non-plated_top_layer-bottom_layer.drl", "drill"],
  ["drill_plated_top_layer-bottom_layer.drl", "drill"],
];

// The zip's members, read with this module's own reader (so the tar and folder cases get the same files).
async function members() {
  const tree = createZipBytesJobTree(ZIP, { archiveName: "pic.zip" });
  return Promise.all(tree.paths().map(async (path) => [path, await tree.readBytes(path)]));
}

// A minimal ustar writer: enough for the loader's TAR reader.
function tar(entries) {
  const blocks = [];
  const field = (buf, offset, length, text) => buf.set(new TextEncoder().encode(text).subarray(0, length), offset);
  for (const [path, bytes] of entries) {
    const header = new Uint8Array(512);
    field(header, 0, 100, path);
    field(header, 100, 8, "0000644\0");
    field(header, 108, 8, "0000000\0");
    field(header, 116, 8, "0000000\0");
    field(header, 124, 12, bytes.length.toString(8).padStart(11, "0") + "\0");
    field(header, 136, 12, "00000000000\0");
    field(header, 156, 1, "0");
    field(header, 257, 6, "ustar\0");
    field(header, 263, 2, "00");
    header.fill(0x20, 148, 156);
    const sum = header.reduce((a, b) => a + b, 0);
    field(header, 148, 8, sum.toString(8).padStart(6, "0") + "\0 ");
    blocks.push(header, bytes, new Uint8Array((512 - (bytes.length % 512)) % 512));
  }
  blocks.push(new Uint8Array(1024));
  const out = new Uint8Array(blocks.reduce((n, b) => n + b.length, 0));
  let offset = 0;
  for (const b of blocks) {
    out.set(b, offset);
    offset += b.length;
  }
  return out;
}

test("a KiCad ODB++ zip becomes renderer layers with Gerber-style names", async () => {
  const warnings = [];
  const layers = await loadOdbJob(ZIP, { name: "pic.zip", onWarning: (label, message) => warnings.push(`${label}: ${message}`) });
  assert.deepEqual(layers.map((l) => [l.name, l.kind]), LAYERS);
  assert.ok(layers.every((l) => l.source.startsWith("%ODB++LAYER%")));
  assert.deepEqual(warnings, [
    "f.paste.gtp: Layer has no features; skipped",
    "b.paste.gbp: Layer has no features; skipped",
    "job: Skipped 15 non-board layers: COMPONENT x2, DIELECTRIC x1, MISC/DOCUMENT x12",
  ]);
});

test("a File, a .tgz and a dropped folder read the same as the zip bytes", async () => {
  const fromZip = await loadOdbJob(ZIP);
  const fromFile = await loadOdbJob(new File([ZIP], "pic_programmer-odb.zip"));
  const entries = (await members()).map(([path, bytes]) => [`pic/${path}`, bytes]);
  const fromTgz = await loadOdbJob(gzipSync(tar(entries)), { name: "pic.tgz" });
  const fromTar = await loadOdbJob(tar(entries), { name: "pic.tar" });
  const fromFolder = await loadOdbJob(entries.map(([path, bytes]) => ({ file: new File([bytes], path.split("/").pop()), relativePath: path })));
  for (const other of [fromFile, fromTgz, fromTar, fromFolder]) assert.deepEqual(other, fromZip);
});

test("a zip that is not an ODB++ job, a damaged entry and a zip slip are refused", async () => {
  const gerbers = gzipSync(tar([["board-F_Cu.gbr", new TextEncoder().encode("%FSLAX46Y46*%\nM02*\n")]]));
  await assert.rejects(loadOdbJob(gerbers, { name: "gerbers.tgz" }), /gerbers\.tgz is not an ODB\+\+ job \(matrix\/matrix not found\)/);

  // Central-directory records: [offset, name].
  const records = (bytes) => {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let eocd = bytes.length - 22;
    while (view.getUint32(eocd, true) !== 0x06054b50) eocd -= 1;
    const out = [];
    let offset = view.getUint32(eocd + 16, true);
    for (let n = view.getUint16(eocd + 10, true); n > 0; n -= 1) {
      const nameLength = view.getUint16(offset + 28, true);
      out.push([offset, new TextDecoder().decode(bytes.subarray(offset + 46, offset + 46 + nameLength))]);
      offset += 46 + nameLength + view.getUint16(offset + 30, true) + view.getUint16(offset + 32, true);
    }
    return out;
  };
  const [infoAt] = records(ZIP).find(([, name]) => name.endsWith("misc/info"));

  // A wrong CRC in the central directory: the entry reads back as damaged.
  const damaged = ZIP.slice();
  damaged[infoAt + 16] ^= 0xff;
  await assert.rejects(
    createZipBytesJobTree(damaged, { archiveName: "bad.zip" }).readBytes("misc/info"),
    /bad\.zip ZIP entry CRC does not match its central directory/,
  );

  // A file name rewritten to start with '../'.
  const slip = ZIP.slice();
  slip.set(new TextEncoder().encode("../"), infoAt + 46);
  assert.throws(() => createZipBytesJobTree(slip, { archiveName: "slip.zip" }), /slip\.zip ZIP entry \d+ has an unsafe path/);
});

test("ODB++ envelopes: hasGeometry, layerRole and groupBoardLayers know them", async () => {
  const { hasGeometry, layerRole, groupBoardLayers } = await import("../../src/gerber/layers.js");
  const layers = (await loadOdbJob(ZIP)).map((l) => ({ name: l.name, content: l.source }));
  assert.ok(layers.every((l) => hasGeometry(l.content)));
  assert.equal(hasGeometry("%ODB++LAYER%\nkind=signal\nname=X\n%ODB++FILE features%\nUNITS=MM\nF 0\n%ODB++END%\n"), false);
  // the envelope decides, not the Gerber-style name (which reads as copper for `..._top_layer-bottom_layer.drl`)
  const roles = Object.fromEntries(layers.map((l) => [l.name, layerRole(l.name, l.content)]));
  assert.deepEqual(roles["drill_plated_top_layer-bottom_layer.drl"], { role: "drill", side: null, plated: true });
  assert.deepEqual(roles["drill_non-plated_top_layer-bottom_layer.drl"], { role: "drill", side: null, plated: false });
  assert.deepEqual(roles["profile.gko"], { role: "outline", side: null });
  const board = groupBoardLayers(layers);
  assert.equal(board.outline.name, "profile.gko");
  assert.deepEqual([board.top.copper.name, board.bottom.copper.name], ["top_layer.gtl", "bottom_layer.gbl"]);
  assert.deepEqual(board.drills.map((d) => [d.name, d.plated]), [
    ["drill_non-plated_top_layer-bottom_layer.drl", false],
    ["drill_plated_top_layer-bottom_layer.drl", true],
  ]);
  assert.deepEqual(board.other, []);
});
