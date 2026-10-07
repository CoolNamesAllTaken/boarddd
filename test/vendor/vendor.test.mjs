// scripts/vendor.mjs: vendoring boarddd into a temp dir from a temp git checkout, with three.js and
// occt-import-js packed from node_modules (and once through a local fake registry).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { run, parseArgs, specifiers, stripComments, rewriteThree, threeTarget, untar, exportDirs, closeSubpaths } from '../../scripts/vendor.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'boarddd-vendor-'));
test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));

const sh = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] }).toString();
const git = (cwd, ...args) => sh('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false', ...args], cwd);

// A boarddd checkout of what this tree ships (package.json "files"), committed and tagged v9.9.9.
const PKG = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
const SRC = path.join(TMP, 'boarddd');
fs.mkdirSync(SRC);
for (const f of ['package.json', ...PKG.files]) fs.cpSync(path.join(ROOT, f), path.join(SRC, f), { recursive: true });
git(SRC, 'init', '-q', '-b', 'main');
git(SRC, 'add', '.');
git(SRC, 'commit', '-q', '-m', 'boarddd');
git(SRC, 'tag', 'v9.9.9');
const TAGGED = git(SRC, 'rev-parse', 'HEAD').trim();
const V = PKG.version.replace(/\./g, '\\.');

// npm-pack-like tarballs (package/...) of the installed devDependencies
function pack(name, entries) {
  const stage = path.join(TMP, `pack-${name}`), pkg = path.join(stage, 'package');
  for (const e of entries) fs.cpSync(path.join(ROOT, 'node_modules', name, e), path.join(pkg, e), { recursive: true });
  const tgz = path.join(TMP, `${name}.tgz`);
  sh('tar', ['-czf', tgz, '-C', stage, 'package']);
  return tgz;
}
const THREE_TGZ = pack('three', ['package.json', 'LICENSE', 'build/three.module.js', 'build/three.core.js',
  'examples/jsm/controls', 'examples/jsm/loaders/GLTFLoader.js', 'examples/jsm/environments', 'examples/jsm/utils', 'examples/jsm/exporters']);
const OCCT_TGZ = pack('occt-import-js', ['package.json', 'LICENSE.md', 'dist']);
const THREE_VERSION = JSON.parse(fs.readFileSync(path.join(ROOT, 'node_modules/three/package.json'), 'utf8')).version;

let n = 0;
const fresh = () => path.join(TMP, `out${++n}`);
async function vendor(...argv) {
  const lines = [];
  const code = await run(['--source', SRC, ...argv], (s) => lines.push(s));
  return { code, out: lines.join('\n') };
}
const files = (dir) => fs.readdirSync(dir, { recursive: true, withFileTypes: true })
  .filter((e) => e.isFile()).map((e) => path.relative(dir, path.join(e.parentPath ?? e.path, e.name))).sort();
const snapshot = (dir) => Object.fromEntries(files(dir).map((f) => [f, [fs.readFileSync(path.join(dir, f), 'hex'), fs.statSync(path.join(dir, f)).mtimeMs]]));

// every relative specifier in every vendored module points at a file that exists; none is bare
function assertSelfContained(dir) {
  let checked = 0;
  for (const f of files(dir).filter((f) => /\.m?js$/.test(f))) {
    for (const spec of specifiers(fs.readFileSync(path.join(dir, f), 'utf8'))) {
      assert.ok(spec.startsWith('./') || spec.startsWith('../'), `${f} imports bare ${spec}`);
      assert.ok(fs.existsSync(path.join(dir, path.dirname(f), spec)), `${f}: ${spec} does not exist`);
      checked++;
    }
  }
  return checked;
}

test('specifiers: import/export/dynamic import; not comments, strings, regexes or templates', () => {
  const code = [
    "// import x from 'three'",
    "import * as T from 'three'; /* from 'three/addons/a.js' */",
    "const r = /from 'three'/g, s = 'x//y', q = \"a from 'three'\";",
    'export { O } from "three/addons/controls/OrbitControls.js";',
    "const t = `from 'three' ${a} from \"x\"`;",
    "const m = await import( 'three' );",
    "import 'three/examples/jsm/utils/X.js';",
    "export * from './frames.js';",
  ].join('\n');
  assert.deepEqual(specifiers(code), ['three', 'three/addons/controls/OrbitControls.js', 'three', 'three/examples/jsm/utils/X.js', './frames.js']);
  assert.equal(stripComments(code).length, code.length);
  assert.equal(stripComments(code).split('\n').length, code.split('\n').length);
});

test('rewriteThree: only three specifiers, relative to the file', () => {
  const code = "import * as THREE from 'three';\n// from 'three'\nimport { A } from 'three/addons/controls/A.js';\nimport { b } from './b.js';\nconst m = import(\"three\");\n";
  assert.equal(rewriteThree(code, '/v/boarddd/src/scene', '/v/three'),
    "import * as THREE from '../../../three/three.module.js';\n// from 'three'\nimport { A } from '../../../three/addons/controls/A.js';\nimport { b } from './b.js';\nconst m = import(\"../../../three/three.module.js\");\n");
  assert.equal(rewriteThree("import 'three';", '/v/boarddd/src/x', '/v/boarddd/src/x/three'), "import './three/three.module.js';");
  assert.equal(threeTarget('three'), '');
  assert.equal(threeTarget('three/examples/jsm/a/B.js'), 'a/B.js');
  assert.equal(threeTarget('three-stdlib'), null);
});

test('untar reads git archive output (pax headers, long names)', () => {
  const long = 'src/' + 'd'.repeat(120) + '/f.js';
  const repo = path.join(TMP, 'tar');
  fs.mkdirSync(path.dirname(path.join(repo, long)), { recursive: true });
  fs.writeFileSync(path.join(repo, long), 'x');
  fs.writeFileSync(path.join(repo, 'bin.wasm'), Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]));
  git(repo, 'init', '-q');
  git(repo, 'add', '.');
  git(repo, 'commit', '-q', '-m', 'x');
  const t = untar(execFileSync('git', ['-C', repo, 'archive', '--format=tar', 'HEAD']));
  assert.equal(t.get(long).toString(), 'x');
  assert.deepEqual([...t.get('bin.wasm')], [0, 97, 115, 109, 1, 0, 0, 0]);
  assert.equal(t.comment, git(repo, 'rev-parse', 'HEAD').trim());
});

test('subpaths come from the exports map and pull in what they import', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  const dirs = exportDirs(pkg);
  assert.deepEqual([...dirs.keys()], ['geom', 'board', 'footprint', 'models', 'scene', 'gerber', 'view2d', 'model', 'impedance']);
  assert.equal(dirs.get('scene'), 'src/scene');
  // a future export with assets: "./gerber": "./src/gerber/index.js" -> the whole src/gerber dir
  assert.equal(exportDirs({ exports: { './gerber': { types: './src/gerber/index.d.ts', default: './src/gerber/index.js' } } }).get('gerber'), 'src/gerber');
  const shipped = new Map(files(SRC).filter((f) => !f.startsWith('.git/')).map((f) => [f, fs.readFileSync(path.join(SRC, f))]));
  // footprint builds on board, board on geom and gerber, gerber on the vendored upstream core (+ wasm)
  assert.deepEqual(closeSubpaths(shipped, dirs, ['footprint']),
    { subpaths: ['geom', 'board', 'footprint', 'gerber'], added: ['geom', 'board', 'gerber'], extra: ['third_party/wasm-gerber-renderer'] });
  assert.deepEqual(closeSubpaths(shipped, dirs, ['scene']), { subpaths: ['scene'], added: [], extra: [] });
  assert.deepEqual(closeSubpaths(shipped, dirs, ['geom']).subpaths, ['geom']);
  assert.deepEqual(closeSubpaths(shipped, dirs, ['model']), { subpaths: ['model'], added: [], extra: [] });
  assert.deepEqual(closeSubpaths(shipped, dirs, ['view2d']),
    { subpaths: ['gerber', 'view2d'], added: ['gerber'], extra: ['third_party/wasm-gerber-renderer'] });
});

test('arguments', () => {
  assert.throws(() => parseArgs([]), /--out is required/);
  assert.throws(() => parseArgs(['--out', 'x', '--three', '1.0.0', '--no-three']), /exclusive/);
  assert.throws(() => parseArgs(['--out', 'x', '--imports', 'cdn']), /relative or bare/);
  assert.throws(() => parseArgs(['--out', 'x', '--bogus']), /unknown argument/);
  assert.throws(() => parseArgs(['--out', 'x', '--addons', 'a.js']), /needs --three/);
  const o = parseArgs(['--out=v/boarddd', '--subpaths', 'geom, scene', '--three-dir', 'v/three']);
  assert.deepEqual(o.subpaths, ['geom', 'scene']);
  assert.equal(o.threeDir, path.resolve('v/three'));
  assert.equal(o.occtDir, path.resolve('v/boarddd/occt-import-js'));
});

test('kipr layout: boarddd, three and occt side by side; imports resolve in node with no importmap', async () => {
  const v = fresh();
  const args = ['--ref', 'v9.9.9', '--out', `${v}/boarddd`, '--three', THREE_TGZ, '--three-dir', `${v}/three`, '--occt', OCCT_TGZ, '--occt-dir', `${v}/occt-import-js`];
  const r = await vendor(...args);
  assert.equal(r.code, 0);
  assert.match(r.out, new RegExp(`boarddd ${V} v9\\.9\\.9 \\[geom board footprint models scene gerber view2d model impedance\\] -> .*: \\d+ added`));

  assert.deepEqual(files(`${v}/three`), ['LICENSE', 'VENDORED.json', 'addons/controls/OrbitControls.js', 'addons/controls/TrackballControls.js',
    'addons/environments/RoomEnvironment.js', 'addons/loaders/GLTFLoader.js', 'addons/utils/BufferGeometryUtils.js',
    'addons/utils/SkeletonUtils.js', 'three.core.js', 'three.module.js']);
  assert.deepEqual(files(`${v}/occt-import-js`), ['LICENSE.md', 'VENDORED.json', 'dist/occt-import-js.js', 'dist/occt-import-js.wasm']);
  assert.ok(fs.readFileSync(`${v}/occt-import-js/dist/occt-import-js.wasm`).equals(fs.readFileSync(path.join(ROOT, 'node_modules/occt-import-js/dist/occt-import-js.wasm'))));
  assert.ok(files(`${v}/boarddd`).includes('src/models/step_worker.js'));
  assert.ok(files(`${v}/boarddd`).includes('src/scene/index.d.ts'));
  assert.equal(fs.readFileSync(`${v}/boarddd/src/scene/viewer.js`, 'utf8').match(/^import .* from '(.*)';$/m)[1], '../../../three/three.module.js');
  // typings keep the bare name for @types/three
  assert.match(fs.readFileSync(`${v}/boarddd/src/models/index.d.ts`, 'utf8'), /from 'three'/);
  assert.ok(assertSelfContained(v) > 40);

  const commit = fs.readFileSync(`${v}/boarddd/COMMIT`, 'utf8').split('\n');
  assert.equal(commit[0], TAGGED);
  assert.equal(commit[1], `boarddd ${PKG.version} (tag v9.9.9)`);
  assert.equal(commit[2], 'subpaths: geom board footprint models scene gerber view2d model impedance');
  assert.equal(commit[3], 'with: third_party/wasm-gerber-renderer');
  assert.equal(commit[4], 'imports: three rewritten to ../three');
  assert.match(commit[5], new RegExp(`^three: ${THREE_VERSION.replace(/\./g, '\\.')} sha512-[A-Za-z0-9+/]+=* -> \\.\\./three$`));
  assert.match(commit[6], /^occt-import-js: \S+ sha512-\S+ -> \.\.\/occt-import-js$/);
  assert.match(commit[7], /--ref v9\.9\.9 .*--three-dir .*--three \S+ .*--occt-dir .*--occt \S+$/);
  const man = JSON.parse(fs.readFileSync(`${v}/three/VENDORED.json`, 'utf8'));
  assert.equal(man.integrity, 'sha512-' + crypto.createHash('sha512').update(fs.readFileSync(THREE_TGZ)).digest('base64'));

  // load every subpath in a fresh node process from the temp dir (no node_modules anywhere above it)
  const probe = path.join(v, 'probe.mjs');
  fs.writeFileSync(probe, `
    const three = await import('./three/three.module.js');
    const out = {};
    for (const sp of ['geom', 'board', 'footprint', 'models', 'scene', 'gerber', 'view2d', 'model', 'impedance']) out[sp] = Object.keys(await import('./boarddd/src/' + sp + '/index.js')).length;
    const { buildBoard } = await import('./boarddd/src/board/index.js');
    const board = buildBoard({ outline: { board: [[0, 0], [10, 0], [10, 5], [0, 5]] } });
    const obj = board instanceof three.Object3D ? board : board.group;
    out.sameThree = obj instanceof three.Group && obj.children.some((c) => c instanceof three.Mesh);
    out.revision = three.REVISION;
    console.log(JSON.stringify(out));`);
  const res = JSON.parse(sh(process.execPath, [probe], v));
  fs.rmSync(probe);
  for (const sp of ['geom', 'board', 'footprint', 'models', 'scene', 'gerber', 'view2d', 'model', 'impedance']) assert.ok(res[sp] > 0, sp);
  assert.equal(res.sameThree, true, 'boarddd builds objects of the vendored three');
  assert.equal(res.revision, THREE_VERSION.split('.')[1]);

  // idempotent: nothing rewritten
  const before = snapshot(v);
  const again = await vendor(...args);
  assert.equal(again.code, 0);
  assert.equal(again.out.match(/up to date/g).length, 3);
  assert.deepEqual(snapshot(v), before);

  // --check passes, then catches an edit, a stray file and a missing file
  assert.equal((await vendor('--check', ...args)).code, 0);
  fs.appendFileSync(`${v}/boarddd/src/geom/loops.js`, '// local fix\n');
  fs.writeFileSync(`${v}/boarddd/src/geom/extra.js`, '');
  fs.rmSync(`${v}/three/addons/utils/SkeletonUtils.js`);
  const bad = await vendor('--check', ...args);
  assert.equal(bad.code, 1);
  assert.match(bad.out, /src\/geom\/loops\.js: differs/);
  assert.match(bad.out, /src\/geom\/extra\.js: not part of the vendored tree/);
  assert.match(bad.out, /three\/addons\/utils\/SkeletonUtils\.js: missing/);
  // and a normal run refuses to discard the edit or the stray file
  await assert.rejects(vendor(...args), /refusing to overwrite or delete:\n {2}src\/geom\/loops\.js\n {2}src\/geom\/extra\.js/);
  assert.equal(fs.readFileSync(`${v}/boarddd/src/geom/loops.js`, 'utf8').endsWith('// local fix\n'), true, 'nothing written');
  assert.equal((await vendor('--force', ...args)).code, 0);
  assert.deepEqual(files(v), Object.keys(before).sort());
  assert.equal((await vendor('--check', ...args)).code, 0);
});

test('default three dir is <out>/three; a later run with fewer subpaths prunes the rest', async () => {
  const v = fresh();
  assert.equal((await vendor('--out', v, '--three', THREE_TGZ)).code, 0);
  assert.ok(fs.existsSync(`${v}/three/three.module.js`));
  assert.equal(fs.readFileSync(`${v}/src/scene/viewer.js`, 'utf8').match(/^import .* from '(.*)';$/m)[1], '../../three/three.module.js');
  assertSelfContained(v);

  // geom only: everything else goes, empty dirs too; the three dir (not managed this run) stays
  const r = await vendor('--out', v, '--subpaths', 'geom');
  assert.equal(r.code, 0);
  assert.deepEqual(fs.readdirSync(`${v}/src`), ['geom']);
  assert.ok(fs.existsSync(`${v}/three/three.module.js`));
  assert.match(fs.readFileSync(`${v}/COMMIT`, 'utf8'), /^subpaths: geom$/m);
  assert.equal((await vendor('--check', '--out', v, '--subpaths', 'geom')).code, 0);
});

test('--three adds extra addons; --check of a version is offline, against the manifest', async () => {
  const v = fresh();
  assert.equal((await vendor('--out', v, '--subpaths', 'models', '--three', THREE_TGZ, '--addons', 'exporters/GLTFExporter.js')).code, 0);
  assert.ok(fs.existsSync(`${v}/three/addons/exporters/GLTFExporter.js`));
  assert.ok(!fs.existsSync(`${v}/three/addons/controls`), 'models needs no controls');
  assertSelfContained(v);
  const ok = await vendor('--check', '--out', v, '--subpaths', 'models', '--three', THREE_VERSION, '--addons', 'exporters/GLTFExporter.js');
  assert.equal(ok.code, 0, ok.out);
  const old = await vendor('--check', '--out', v, '--subpaths', 'models', '--three', '0.160.0', '--addons', 'exporters/GLTFExporter.js');
  assert.equal(old.code, 1);
  assert.match(old.out, new RegExp(`three ${THREE_VERSION.replace(/\./g, '\\.')}, wanted 0\\.160\\.0`));
  const more = await vendor('--check', '--out', v, '--three', THREE_VERSION, '--addons', 'exporters/GLTFExporter.js');
  assert.match(more.out, /addons\/controls\/TrackballControls\.js: needed by boarddd but not vendored/);
});

test('gentoo layout: chosen subpaths, bare imports for its importmap, a note in the README', async () => {
  const v = fresh();
  const note = path.join(TMP, 'note.md');
  fs.writeFileSync(note, '## How the page reaches it\n\nThrough `_three_importmap.html`.\n');
  const args = ['--out', v, '--subpaths', 'geom,board,models,scene', '--imports', 'bare', '--no-three', '--note', note];
  assert.equal((await vendor(...args)).code, 0);
  assert.deepEqual(fs.readdirSync(`${v}/src`).sort(), ['board', 'geom', 'gerber', 'models', 'scene']);
  assert.ok(fs.existsSync(`${v}/third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor_bg.wasm`));
  assert.ok(fs.existsSync(`${v}/third_party/wasm-gerber-renderer/LICENSE`));
  assert.ok(!fs.existsSync(`${v}/third_party/wasm-gerber-renderer/crate`), 'the Rust crate is not shipped');
  for (const f of ['src/scene/viewer.js', 'src/models/step_worker.js', 'src/board/solid.js']) {
    assert.ok(fs.readFileSync(`${v}/${f}`).equals(fs.readFileSync(path.join(ROOT, f))), `${f} is byte-identical`);
  }
  const readme = fs.readFileSync(`${v}/README.md`, 'utf8');
  assert.match(readme, new RegExp(`^# boarddd ${V}, vendored at [0-9a-f]{7}$`, 'm'));
  assert.match(readme, /This directory is generated/);
  assert.match(readme, /left bare/);
  assert.ok(readme.endsWith('## How the page reaches it\n\nThrough `_three_importmap.html`.\n'));
  assert.equal((await vendor('--check', ...args)).code, 0);
});

test('relative imports need the three dir to have what boarddd imports', async () => {
  const v = fresh();
  await assert.rejects(vendor('--out', v), /three lacks three\.module\.js, addons\/controls\/OrbitControls\.js, /);
  assert.ok(!fs.existsSync(v), 'nothing written');
  assert.equal((await vendor('--out', v, '--no-three')).code, 0);
});

test('refuses to take over a directory it did not write, until --force', async () => {
  const v = fresh();
  fs.mkdirSync(`${v}/src/geom`, { recursive: true });
  fs.writeFileSync(`${v}/src/geom/loops.js`, 'hand-made');
  fs.writeFileSync(`${v}/notes.txt`, 'mine');
  await assert.rejects(vendor('--out', v, '--no-three'), /didn't write \(no VENDORED\.json\); refusing to overwrite or delete:\n {2}src\/geom\/loops\.js\n {2}notes\.txt\n/);
  assert.equal(fs.readFileSync(`${v}/notes.txt`, 'utf8'), 'mine');
  assert.equal((await vendor('--out', v, '--no-three', '--force')).code, 0);
  assert.ok(!fs.existsSync(`${v}/notes.txt`));
});

test('the working tree: refused when dirty, recorded as dirty with --allow-dirty; --ref takes the tag', async () => {
  fs.appendFileSync(path.join(SRC, 'src/geom/loops.js'), '\n// work in progress\n');
  try {
    const v = fresh();
    await assert.rejects(vendor('--out', v, '--no-three'), /uncommitted changes in what it ships[^]*src\/geom\/loops\.js/);
    assert.equal((await vendor('--out', v, '--no-three', '--allow-dirty')).code, 0);
    assert.equal(fs.readFileSync(`${v}/COMMIT`, 'utf8').split('\n')[0], `${TAGGED}-dirty`);
    assert.match(fs.readFileSync(`${v}/src/geom/loops.js`, 'utf8'), /work in progress/);
    const t = fresh();
    assert.equal((await vendor('--ref', 'v9.9.9', '--out', t, '--no-three')).code, 0);
    assert.doesNotMatch(fs.readFileSync(`${t}/src/geom/loops.js`, 'utf8'), /work in progress/);
    await assert.rejects(vendor('--ref', 'v0.0.0-nope', '--out', fresh(), '--no-three'), /not a commit/);
  } finally {
    git(SRC, 'checkout', '--', 'src');
  }
});

test('unknown subpaths and other bare imports are errors', async () => {
  await assert.rejects(vendor('--out', fresh(), '--subpaths', 'geom,gerbr', '--no-three'), new RegExp(`unknown subpath gerbr; boarddd ${V} exports geom, board, footprint, models, scene, gerber, view2d, model`));
  const other = path.join(TMP, 'other');
  fs.cpSync(SRC, other, { recursive: true });
  fs.appendFileSync(path.join(other, 'src/geom/loops.js'), "\nexport { x } from 'left-pad';\n");
  git(other, 'commit', '-qam', 'bare');
  await assert.rejects(run(['--source', other, '--out', fresh(), '--no-three'], () => {}), /bare imports other than three can't be made relative:\n {2}left-pad \(src\/geom\/loops\.js\)/);
  assert.equal(await run(['--source', other, '--out', fresh(), '--imports', 'bare', '-q'], () => {}), 0);
});

test('a registry download is checked against its sha512', async () => {
  const tgz = fs.readFileSync(THREE_TGZ);
  let integrity = 'sha512-' + crypto.createHash('sha512').update(tgz).digest('base64');
  const server = http.createServer((req, res) => {
    const base = `http://127.0.0.1:${server.address().port}`;
    if (req.url === `/three/${THREE_VERSION}`) res.end(JSON.stringify({ name: 'three', version: THREE_VERSION, dist: { tarball: `${base}/three/-/three.tgz`, integrity } }));
    else if (req.url === '/three/-/three.tgz') res.end(tgz);
    else res.writeHead(404).end();
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const registry = `http://127.0.0.1:${server.address().port}`;
  try {
    const v = fresh();
    assert.equal((await vendor('--out', v, '--three', THREE_VERSION, '--registry', registry, '-q')).code, 0);
    assert.equal(JSON.parse(fs.readFileSync(`${v}/three/VENDORED.json`, 'utf8')).integrity, integrity);
    assertSelfContained(v);
    await assert.rejects(vendor('--out', fresh(), '--three', '0.1.2', '--registry', registry), /answered 404/);
    await assert.rejects(vendor('--out', fresh(), '--three', 'latest', '--registry', registry), /exact version/);
    integrity = 'sha512-' + Buffer.alloc(64).toString('base64');
    const w = fresh();
    await assert.rejects(vendor('--out', w, '--three', THREE_VERSION, '--registry', registry), /tarball sha512 mismatch/);
    assert.ok(!fs.existsSync(w), 'nothing written');
  } finally {
    server.close();
  }
});

test('the CLI exits 2 with a message on errors, 1 when --check finds differences', () => {
  const cli = path.join(ROOT, 'scripts/vendor.mjs');
  const r = (args) => {
    try { return { code: 0, out: execFileSync(process.execPath, [cli, ...args], { stdio: 'pipe' }).toString() }; } catch (e) { return { code: e.status, out: e.stderr.toString() + e.stdout.toString() }; }
  };
  assert.match(r(['--help']).out, /usage: node scripts\/vendor\.mjs/);
  const e = r(['--out', fresh(), '--source', SRC, '--subpaths', 'nope']);
  assert.equal(e.code, 2);
  assert.match(e.out, /^vendor: unknown subpath nope/);
  const c = r(['--check', '--out', fresh(), '--source', SRC, '--no-three']);
  assert.equal(c.code, 1);
  assert.match(c.out, /COMMIT: missing/);
  assert.ok(pathToFileURL(cli));
});

test('model: the board-model validator (schema.js) vendors alone, needs no three', async () => {
  const v = fresh();
  assert.equal((await vendor('--ref', 'v9.9.9', '--out', v, '--subpaths', 'model')).code, 0);
  assert.deepEqual(files(`${v}/src`), ['model/board.d.ts', 'model/index.d.ts', 'model/index.js', 'model/schema.js']);
  assert.ok(!fs.existsSync(`${v}/three`));
  assertSelfContained(v);
  const probe = path.join(v, 'probe.mjs');
  fs.writeFileSync(probe, `
    const m = await import('./src/model/index.js');
    console.log(JSON.stringify({ id: m.SCHEMA_ID, schema: m.SCHEMA.$id || m.SCHEMA.title || null, bad: m.validateBoard({}).length > 0 }));`);
  const res = JSON.parse(sh(process.execPath, [probe], v));
  fs.rmSync(probe);
  assert.equal(res.id, 'boarddd/board@1');
  assert.ok(res.schema);
  assert.equal(res.bad, true);
  assert.equal((await vendor('--check', '--ref', 'v9.9.9', '--out', v, '--subpaths', 'model')).code, 0);
});
