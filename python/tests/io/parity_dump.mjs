// Dump boarddd/gerber's JS readers as JSON, for python/tests/io/test_parity_js.py:
//   node parity_dump.mjs drills <file.drl>...              -> [[hole, ...], ...]  (parseExcellon)
//   node parity_dump.mjs outline <file.gbr> <width> <height> -> boardOutline(text, {width, height})
import { readFileSync } from "node:fs";
import { parseExcellon } from "../../../src/gerber/drills.js";
import { boardOutline } from "../../../src/gerber/outline.js";

const [mode, ...args] = process.argv.slice(2);
let out;
if (mode === "drills") {
  out = args.map((path) => parseExcellon(readFileSync(path, "utf8")));
} else if (mode === "outline") {
  const [path, width, height] = args;
  const options = width ? { width: Number(width), height: Number(height) } : {};
  out = boardOutline(readFileSync(path, "utf8"), options);
} else {
  throw new Error(`unknown mode ${mode}`);
}
process.stdout.write(JSON.stringify(out));
