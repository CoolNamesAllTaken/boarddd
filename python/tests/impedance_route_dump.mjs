// analyzeNet as JSON, for python/tests/test_impedance_route.py (Python = JS):
//   node impedance_route_dump.mjs <case.json|case.json.gz> '<net or [p, n] as JSON>' ['<options as JSON>']
// The case file is {"board": board@1, "copper": copper@1}.
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { analyzeNet } from "../../src/impedance/index.js";

const [file, nets, options] = process.argv.slice(2);
const raw = readFileSync(file);
const { board, copper } = JSON.parse((file.endsWith(".gz") ? gunzipSync(raw) : raw).toString("utf8"));
process.stdout.write(JSON.stringify(analyzeNet(board, copper, JSON.parse(nets), JSON.parse(options || "{}"))));
