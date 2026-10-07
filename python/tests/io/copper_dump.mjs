// Dump boarddd/copper's copperFromGerbers for python/tests/io/test_gerber_copper.py (the Python/JS parity check):
//   node copper_dump.mjs <name> <file>...   -> the boarddd/copper@1 document as JSON
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { copperFromGerbers } from "../../../src/copper/index.js";

const [name, ...paths] = process.argv.slice(2);
const files = paths.map((path) => ({ name: basename(path), text: readFileSync(path, "utf8") }));
process.stdout.write(JSON.stringify(copperFromGerbers(files, { name })));
