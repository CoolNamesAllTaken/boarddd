#!/usr/bin/env node
// Vendor boarddd into another project: copy the chosen subpaths (sources, .d.ts typings and any
// committed assets such as wasm), rewrite the bare `three` / `three/addons/...` imports to relative
// paths so pages need no importmap, optionally fetch three.js and occt-import-js from the npm
// registry (sha512-checked) for offline use, and record what was taken in COMMIT.
//
//   node scripts/vendor.mjs --out <dir> [options]
//
// The output is generated: every directory this script writes carries a VENDORED.json manifest
// (file -> sha256). A re-run with the same inputs changes nothing; a run that would overwrite or
// delete a file the manifest does not list (or one edited since) stops, unless --force.
// --check compares instead of writing and exits 1 on any difference. See README "Using boarddd in
// your project". Node >= 18, no dependencies (git is needed for --ref and for the commit id).
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MANIFEST = 'VENDORED.json';
const TOOL = 'boarddd/scripts/vendor.mjs';

const USAGE = `usage: node scripts/vendor.mjs --out <dir> [options]

  --out <dir>              where boarddd goes (src/<subpath>/..., LICENSE, README.md, COMMIT)
  --subpaths a,b,...       boarddd exports to take (default: all); subpaths they import are added
  --ref <tag|sha>          export this commit of the source checkout (git archive); default: the
                           working tree, which must be clean (or --allow-dirty)
  --source <dir>           boarddd checkout to vendor from (default: the one holding this script)
  --imports relative|bare  relative (default): rewrite 'three' and 'three/addons/...' to paths into
                           the three dir; bare: leave them for the page's importmap or a bundler
  --three <version|.tgz>   also vendor three.js (build + the addons boarddd needs) into --three-dir
  --no-three               don't fetch three and don't check that the three dir has what is needed
  --three-dir <dir>        where three.js lives (default <out>/three)
  --addons a,b             extra addons to vendor with --three (e.g. exporters/GLTFExporter.js)
  --occt <version|.tgz>    also vendor occt-import-js (dist/*.js + .wasm, unmodified) into --occt-dir
  --no-occt                don't (default)
  --occt-dir <dir>         where occt-import-js goes (default <out>/occt-import-js)
  --note <file>            Markdown appended to the generated README.md (how your pages load it)
  --registry <url>         npm registry (default $npm_config_registry or https://registry.npmjs.org)
  --check                  write nothing; exit 1 if the vendored dirs differ from what would be written
  --force                  overwrite files this script didn't write (adopting a hand-made vendor dir)
  --allow-dirty            vendor an unclean working tree (COMMIT records it as dirty)
  -q, --quiet              only errors`;

class VendorError extends Error {}
const fail = (msg) => { throw new VendorError(msg); };

// ---------------------------------------------------------------------------------------------------
// arguments

export function parseArgs(argv) {
  const o = { imports: 'relative', addons: [], subpaths: null, three: null, occt: null };
  const flags = { '--check': 'check', '--force': 'force', '--allow-dirty': 'allowDirty', '--no-three': 'noThree',
    '--no-occt': 'noOcct', '-q': 'quiet', '--quiet': 'quiet', '-h': 'help', '--help': 'help' };
  const values = { '--out': 'out', '--ref': 'ref', '--source': 'source', '--imports': 'imports', '--three': 'three',
    '--three-dir': 'threeDir', '--occt': 'occt', '--occt-dir': 'occtDir', '--registry': 'registry',
    '--subpaths': 'subpaths', '--addons': 'addons', '--note': 'note' };
  for (let i = 0; i < argv.length; i++) {
    let a = argv[i], v;
    const eq = a.indexOf('=');
    if (a.startsWith('--') && eq > 0) { v = a.slice(eq + 1); a = a.slice(0, eq); }
    if (flags[a] && v === undefined) { o[flags[a]] = true; continue; }
    if (!values[a]) fail(`unknown argument ${argv[i]}\n\n${USAGE}`);
    if (v === undefined) {
      if (i + 1 >= argv.length) fail(`${a} needs a value`);
      v = argv[++i];
    }
    const k = values[a];
    o[k] = (k === 'subpaths' || k === 'addons') ? v.split(',').map((s) => s.trim()).filter(Boolean) : v;
  }
  if (o.help) return o;
  if (!o.out) fail(`--out is required\n\n${USAGE}`);
  if (!['relative', 'bare'].includes(o.imports)) fail(`--imports must be relative or bare, not ${o.imports}`);
  if (o.three && o.noThree) fail('--three and --no-three are exclusive');
  if (o.occt && o.noOcct) fail('--occt and --no-occt are exclusive');
  if (o.addons.length && !o.three) fail('--addons needs --three');
  for (const a of o.addons) if (!/^[\w-]+\/[\w./-]+\.js$/.test(a) || a.includes('..')) fail(`bad addon ${a} (want e.g. exporters/GLTFExporter.js)`);
  o.out = path.resolve(o.out);
  o.threeDir = path.resolve(o.threeDir || path.join(o.out, 'three'));
  o.occtDir = path.resolve(o.occtDir || path.join(o.out, 'occt-import-js'));
  if (o.note) o.note = path.resolve(o.note);
  o.source = path.resolve(o.source || path.join(HERE, '..'));
  o.registry = (o.registry || process.env.npm_config_registry || 'https://registry.npmjs.org').replace(/\/+$/, '');
  return o;
}

// ---------------------------------------------------------------------------------------------------
// tar (git archive output and npm tarballs): regular files only, ustar prefix + pax/GNU long names

export function untar(buf) {
  const files = new Map();
  let pax = {}, longName = null;
  const str = (b, s, n) => { const x = b.subarray(s, s + n); const z = x.indexOf(0); return x.subarray(0, z < 0 ? n : z).toString('utf8'); };
  for (let off = 0; off + 512 <= buf.length;) {
    const h = buf.subarray(off, off + 512);
    if (h.every((b) => b === 0)) break;
    const size = parseInt(str(h, 124, 12).trim() || '0', 8);
    const type = String.fromCharCode(h[156] || 48);
    const data = buf.subarray(off + 512, off + 512 + size);
    off += 512 + Math.ceil(size / 512) * 512;
    if (type === 'x' || type === 'g') {
      const kv = {};
      for (let s = data.toString('utf8'), i = 0; i < s.length;) {
        const sp = s.indexOf(' ', i), len = parseInt(s.slice(i, sp), 10);
        if (!(len > 0)) break;
        const rec = s.slice(sp + 1, i + len - 1), e = rec.indexOf('=');
        kv[rec.slice(0, e)] = rec.slice(e + 1);
        i += len;
      }
      if (type === 'x') pax = kv; else if (kv.comment) files.comment = kv.comment;   // git archive: commit id
      continue;
    }
    if (type === 'L') { longName = str(data, 0, data.length); continue; }
    let name = pax.path || longName || ((str(h, 257, 6) === 'ustar' && str(h, 345, 155) ? str(h, 345, 155) + '/' : '') + str(h, 0, 100));
    pax = {}; longName = null;
    if (type === '0' || type === '\0' || type === '7') files.set(name.replace(/^\.\//, ''), Buffer.from(data));
  }
  return files;
}

// ---------------------------------------------------------------------------------------------------
// import rewriting

// `from 'x'`, `import 'x'`, `import('x')`, `export ... from 'x'`: the specifier strings of a module.
const SPEC = /(\bfrom\s*|\bimport\s*\(?\s*)(['"])([^'"\n]+)\2/g;

/**
 * code with comments, regex literals, template literals and strings that can't be module specifiers
 * blanked to spaces (same length, newlines kept), so SPEC sees only import/export specifiers.
 */
export function stripComments(code) {
  const out = code.split('');
  const blank = (i, j) => { for (let k = i; k < j; k++) if (out[k] !== '\n') out[k] = ' '; };
  let prev = '';   // last significant character: decides whether '/' starts a regex
  for (let i = 0; i < code.length;) {
    const c = code[i], n = code[i + 1];
    if (c === '/' && n === '/') { const j = code.indexOf('\n', i); blank(i, j < 0 ? code.length : j); i = j < 0 ? code.length : j; continue; }
    if (c === '/' && n === '*') { const j = code.indexOf('*/', i + 2); const e = j < 0 ? code.length : j + 2; blank(i, e); i = e; continue; }
    if (c === "'" || c === '"' || c === '`') {   // strings: kept only where a specifier can be
      let j = i + 1;
      while (j < code.length && code[j] !== c) j += code[j] === '\\' ? 2 : 1;
      if (c === '`' || !/(\bfrom|\bimport\s*\(?)\s*$/.test(code.slice(Math.max(0, i - 40), i))) blank(i + 1, j);
      i = j + 1; prev = c; continue;
    }
    if (c === '/' && (prev === '' || '(,=:[!&|?{};+-*%<>~^'.includes(prev))) {   // a regex literal
      let j = i + 1, cls = false;
      while (j < code.length && code[j] !== '\n' && (cls || code[j] !== '/')) {
        if (code[j] === '\\') j++; else if (code[j] === '[') cls = true; else if (code[j] === ']') cls = false;
        j++;
      }
      blank(i, j + 1); i = j + 1; prev = '/'; continue;
    }
    if (!/\s/.test(c)) prev = /[\w$]/.test(c) ? 'a' : c;
    i++;
  }
  return out.join('');
}

export function specifiers(code) {
  return [...stripComments(code).matchAll(SPEC)].map((m) => m[3]);
}

/** The addon path ('controls/OrbitControls.js') a three specifier names, '' for 'three', null otherwise. */
export function threeTarget(spec) {
  if (spec === 'three') return '';
  const m = /^three\/(?:addons|examples\/jsm)\/(.+)$/.exec(spec);
  return m ? m[1] : null;
}

const posix = (p) => p.split(path.sep).join('/');
const dotted = (p) => (p.startsWith('.') ? p : './' + p);

/**
 * Rewrites a module's three imports to paths relative to it. fileDir and threeDir are absolute (or
 * both relative to the same root); the result imports threeDir/three.module.js and threeDir/addons/...
 */
export function rewriteThree(code, fileDir, threeDir) {
  let out = '', last = 0;
  for (const m of stripComments(code).matchAll(SPEC)) {
    const t = threeTarget(m[3]);
    if (t === null) continue;
    const target = path.join(threeDir, t === '' ? 'three.module.js' : path.join('addons', t));
    const at = m.index + m[1].length + 1;
    out += code.slice(last, at) + dotted(posix(path.relative(fileDir, target)));
    last = at + m[3].length;
  }
  return out + code.slice(last);
}

const isCode = (f) => /\.m?js$/.test(f);
const isTypes = (f) => /\.d\.ts$/.test(f);

// ---------------------------------------------------------------------------------------------------
// git / the boarddd source

function git(cwd, args, opts = {}) {
  return execFileSync('git', ['-C', cwd, ...args], { maxBuffer: 1 << 30, stdio: ['ignore', 'pipe', 'pipe'], ...opts });
}

function isGit(dir) {
  try { return git(dir, ['rev-parse', '--show-toplevel']).toString().trim() === fs.realpathSync(dir); } catch { return false; }
}

function walk(dir, rel = '', out = []) {
  for (const e of fs.readdirSync(path.join(dir, rel), { withFileTypes: true })) {
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) walk(dir, r, out); else if (e.isFile()) out.push(r);
  }
  return out;
}

/** {files: Map(path -> Buffer) of src/, LICENSE, package.json; commit, tag, dirty} */
export function readSource(o) {
  const want = (f) => f.startsWith('src/') || f === 'LICENSE' || f === 'package.json';
  const hasGit = isGit(o.source);
  if (o.ref) {
    if (!hasGit) fail(`--ref needs a git checkout; ${o.source} is not one`);
    let commit;
    try { commit = git(o.source, ['rev-parse', '--verify', `${o.ref}^{commit}`]).toString().trim(); }
    catch { fail(`${o.ref} is not a commit in ${o.source} (git fetch --tags first?)`); }
    const files = new Map([...untar(git(o.source, ['archive', '--format=tar', commit, 'src', 'LICENSE', 'package.json']))].filter(([f]) => want(f)));
    return { files, commit, tag: tagOf(o.source, commit), dirty: false };
  }
  const files = new Map();
  for (const top of ['src', 'LICENSE', 'package.json']) {
    const p = path.join(o.source, top);
    if (!fs.existsSync(p)) continue;
    if (fs.statSync(p).isDirectory()) for (const f of walk(o.source, top)) files.set(f, fs.readFileSync(path.join(o.source, f)));
    else files.set(top, fs.readFileSync(p));
  }
  if (!hasGit) return { files, commit: null, tag: null, dirty: false };
  const commit = git(o.source, ['rev-parse', 'HEAD']).toString().trim();
  const status = git(o.source, ['status', '--porcelain', '--untracked-files=all', '--', 'src', 'LICENSE', 'package.json']).toString().trim();
  if (status && !o.allowDirty) fail(`${o.source} has uncommitted changes under src/ (pass --ref <tag>, commit them, or --allow-dirty):\n${status}`);
  return { files, commit, tag: tagOf(o.source, commit), dirty: !!status };
}

function tagOf(dir, commit) {
  try { return git(dir, ['describe', '--tags', '--exact-match', commit]).toString().trim(); } catch { return null; }
}

/** subpath name -> directory under src/, from package.json "exports" */
export function exportDirs(pkg) {
  const dirs = new Map();
  for (const [key, val] of Object.entries(pkg.exports || {})) {
    const target = typeof val === 'string' ? val : val && (val.default || val.import);
    const m = /^\.\/(.+)$/.exec(key);
    if (!m || typeof target !== 'string' || !target.startsWith('./src/')) continue;
    dirs.set(m[1], path.posix.dirname(target.slice(2)));
  }
  return dirs;
}

/** The subpaths asked for plus every subpath their modules import (relative imports across src/). */
export function closeSubpaths(files, dirs, asked) {
  const byDir = new Map([...dirs].map(([name, dir]) => [dir, name]));
  const take = new Set(asked), queue = [...asked];
  while (queue.length) {
    const dir = dirs.get(queue.shift());
    for (const [f, buf] of files) {
      if (!f.startsWith(dir + '/') || !(isCode(f) || isTypes(f))) continue;
      for (const spec of specifiers(buf.toString('utf8'))) {
        if (!spec.startsWith('.')) continue;
        const target = path.posix.join(path.posix.dirname(f), spec);
        for (const [d, name] of byDir) {
          if (target.startsWith(d + '/') && !take.has(name)) { take.add(name); queue.push(name); }
        }
      }
    }
  }
  const subpaths = [...dirs.keys()].filter((n) => take.has(n));   // in exports order
  return { subpaths, added: subpaths.filter((n) => !asked.includes(n)) };
}

// ---------------------------------------------------------------------------------------------------
// npm packages

const sha512 = (buf) => 'sha512-' + crypto.createHash('sha512').update(buf).digest('base64');
const sha256 = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

/** Reads `name@version` from the registry (or a local .tgz), verifying the registry's sha512. */
export async function fetchPackage(name, spec, registry) {
  let tgz, integrity, from;
  if (/\.tgz$/.test(spec) || fs.existsSync(spec)) {
    tgz = fs.readFileSync(spec);
    from = path.basename(spec);
  } else {
    if (!/^\d+\.\d+\.\d+([-+][\w.]+)?$/.test(spec)) fail(`${name}: want an exact version (e.g. 0.185.1) or a .tgz, not ${spec}`);
    const metaUrl = `${registry}/${name}/${spec}`;
    const res = await fetch(metaUrl);
    if (!res.ok) fail(`${name}@${spec}: ${metaUrl} answered ${res.status}`);
    const meta = await res.json();
    integrity = meta.dist?.integrity;
    if (!integrity?.startsWith('sha512-')) fail(`${name}@${spec}: the registry gives no sha512 integrity`);
    const t = await fetch(meta.dist.tarball);
    if (!t.ok) fail(`${name}@${spec}: ${meta.dist.tarball} answered ${t.status}`);
    tgz = Buffer.from(await t.arrayBuffer());
    from = meta.dist.tarball;
  }
  const got = sha512(tgz);
  if (integrity && got !== integrity) fail(`${name}@${spec}: tarball sha512 mismatch\n  registry ${integrity}\n  download ${got}`);
  const files = new Map();
  for (const [f, buf] of untar(zlib.gunzipSync(tgz))) files.set(f.replace(/^[^/]+\//, ''), buf);   // drop package/
  const pkg = JSON.parse(files.get('package.json')?.toString('utf8') || '{}');
  if (pkg.name !== name) fail(`${spec} is ${pkg.name || 'not an npm package'}, not ${name}`);
  if (!files.has('package.json')) fail(`${spec}: no package.json`);
  return { name, version: pkg.version, integrity: got, from, files };
}

const licenseFiles = (files) => [...files.keys()].filter((f) => /^(licen[cs]e|copying)(\.\w+)?$/i.test(f));

/** three.js: build/three.module.js (+ three.core.js), LICENSE, addons/<the ones needed and what they import> */
export function threeFiles(pkg, addons, imports, threeDir) {
  const out = new Map();
  for (const f of ['build/three.module.js', 'build/three.core.js']) {
    if (pkg.files.has(f)) out.set(path.posix.basename(f), pkg.files.get(f));
  }
  if (!out.has('three.module.js')) fail(`three ${pkg.version}: no build/three.module.js`);
  for (const f of licenseFiles(pkg.files)) out.set(f, pkg.files.get(f));
  const queue = [...addons], seen = new Set();
  while (queue.length) {
    const a = queue.shift();
    if (seen.has(a)) continue;
    seen.add(a);
    const src = pkg.files.get(`examples/jsm/${a}`);
    if (!src) fail(`three ${pkg.version} has no addon ${a}`);
    let code = src.toString('utf8');
    for (const spec of specifiers(code)) {
      if (spec.startsWith('.')) queue.push(path.posix.join(path.posix.dirname(a), spec));
      else if (threeTarget(spec) === null) fail(`three addon ${a} imports ${spec}, which this script can't vendor`);
      else if (threeTarget(spec) !== '') queue.push(threeTarget(spec));
    }
    if (imports === 'relative') code = rewriteThree(code, path.join(threeDir, 'addons', path.dirname(a)), threeDir);
    out.set(`addons/${a}`, Buffer.from(code));
  }
  return out;
}

function occtFiles(pkg) {
  const out = new Map();
  for (const f of ['dist/occt-import-js.js', 'dist/occt-import-js.wasm']) {
    if (!pkg.files.has(f)) fail(`occt-import-js ${pkg.version}: no ${f}`);
    out.set(f, pkg.files.get(f));
  }
  for (const f of licenseFiles(pkg.files)) out.set(f, pkg.files.get(f));
  return out;
}

// ---------------------------------------------------------------------------------------------------
// the boarddd tree

export function boardddFiles(o, source) {
  const pkg = JSON.parse(source.files.get('package.json')?.toString('utf8') || 'null');
  if (!pkg || pkg.name !== 'boarddd') fail(`${o.source} is not a boarddd checkout (no package.json named boarddd)`);
  const dirs = exportDirs(pkg);
  const asked = o.subpaths || [...dirs.keys()];
  const unknown = asked.filter((s) => !dirs.has(s));
  if (unknown.length) fail(`unknown subpath${unknown.length > 1 ? 's' : ''} ${unknown.join(', ')}; boarddd ${pkg.version} exports ${[...dirs.keys()].join(', ')}`);
  const { subpaths, added } = closeSubpaths(source.files, dirs, asked);

  const out = new Map(), addons = new Set(), bare = new Set();
  for (const sp of subpaths) {
    const dir = dirs.get(sp);
    for (const [f, buf] of source.files) {
      if (!f.startsWith(dir + '/')) continue;
      let data = buf;
      if (isCode(f)) {
        const code = buf.toString('utf8');
        for (const spec of specifiers(code)) {
          if (spec.startsWith('.') || /^[a-z]+:/.test(spec)) continue;
          const t = threeTarget(spec);
          if (t === null) bare.add(`${spec} (${f})`); else if (t) addons.add(t);
        }
        if (o.imports === 'relative') data = Buffer.from(rewriteThree(code, path.join(o.out, path.dirname(f)), o.threeDir));
      }
      out.set(f, data);
    }
  }
  if (bare.size && o.imports === 'relative') fail(`bare imports other than three can't be made relative:\n  ${[...bare].join('\n  ')}\n(use --imports bare and map them yourself)`);
  if (source.files.has('LICENSE')) out.set('LICENSE', source.files.get('LICENSE'));
  return { files: out, pkg, subpaths, added, addons: [...addons].sort() };
}

function describeCommit(source) {
  if (!source.commit) return 'unknown (not a git checkout)';
  return source.commit + (source.dirty ? '-dirty' : '');
}

/** The root the recorded command's paths are relative to: the git repository --out is in, else cwd. */
function consumerRoot(out) {
  let p = out;
  while (!fs.existsSync(p)) p = path.dirname(p);
  try { return git(p, ['rev-parse', '--show-toplevel']).toString().trim(); } catch { return process.cwd(); }
}

function rerunCommand(o, info) {
  const root = consumerRoot(o.out);
  const rel = (p) => posix(path.relative(root, p)) || '.';
  const a = ['node', '<boarddd>/scripts/vendor.mjs', '--out', rel(o.out)];
  if (o.ref) a.push('--ref', info.tag || o.ref);
  if (o.subpaths) a.push('--subpaths', o.subpaths.join(','));
  if (o.imports !== 'relative') a.push('--imports', o.imports);
  if (o.threeDir !== path.join(o.out, 'three')) a.push('--three-dir', rel(o.threeDir));
  if (info.three) a.push('--three', info.three.version);
  if (o.noThree) a.push('--no-three');
  if (o.addons.length) a.push('--addons', o.addons.join(','));
  if (o.note) a.push('--note', rel(o.note));
  if (info.occt) {
    if (o.occtDir !== path.join(o.out, 'occt-import-js')) a.push('--occt-dir', rel(o.occtDir));
    a.push('--occt', info.occt.version);
  }
  return a.join(' ');
}

function commitFile(o, source, b, info) {
  const lines = [
    describeCommit(source),
    `boarddd ${b.pkg.version}${source.tag ? ` (tag ${source.tag})` : ''}`,
    `subpaths: ${b.subpaths.join(' ')}`,
    `imports: ${o.imports === 'relative' ? `three rewritten to ${dotted(posix(path.relative(o.out, o.threeDir)))}` : 'bare (three via importmap or bundler)'}`,
  ];
  if (info.three) lines.push(`three: ${info.three.version} ${info.three.integrity} -> ${dotted(posix(path.relative(o.out, o.threeDir)))}`);
  if (info.occt) lines.push(`occt-import-js: ${info.occt.version} ${info.occt.integrity} -> ${dotted(posix(path.relative(o.out, o.occtDir)))}`);
  lines.push(`generated by ${TOOL}: ${rerunCommand(o, info)}`);
  return lines.join('\n') + '\n';
}

function readmeFile(o, source, b, info) {
  const short = source.commit ? source.commit.slice(0, 7) + (source.dirty ? '-dirty' : '') : 'an unknown commit';
  const three = o.imports === 'relative'
    ? `Its \`three\` and \`three/addons/...\` imports are rewritten to relative paths into
\`${dotted(posix(path.relative(o.out, o.threeDir)))}\` (\`three.module.js\`, \`addons/<dir>/<file>.js\`), so pages need
no importmap.${info.three ? ` three.js ${info.three.version} (MIT) is vendored there by the same script.` : ''}`
    : `Its \`three\` and \`three/addons/...\` imports are left bare: map them with an importmap or a bundler.`;
  return `# boarddd ${b.pkg.version}, vendored at ${short}

MIT (see LICENSE). https://github.com/CoolNamesAllTaken/boarddd${source.tag ? `, tag \`${source.tag}\`` : ''}, commit \`${describeCommit(source)}\`.

**This directory is generated. Do not edit it.** Change boarddd upstream, tag it, and re-run
(from the root of this repository, \`<boarddd>\` being a boarddd checkout):

    ${rerunCommand(o, info)}

Taken: ${b.subpaths.map((s) => `\`src/${s}\``).join(', ')} (sources, \`.d.ts\` typings and assets), and LICENSE.
Import \`src/<subpath>/index.js\`. ${three}
The \`.d.ts\` files keep \`from 'three'\` for the type checker (@types/three).
${info.occt ? `\nocct-import-js ${info.occt.version} (LGPL-2.1) is in \`${dotted(posix(path.relative(o.out, o.occtDir)))}\`, unmodified; pass its
\`dist/occt-import-js.js\` and \`.wasm\` URLs to boarddd's STEP loader.\n` : ''}${o.note ? '\n' + fs.readFileSync(o.note, 'utf8').replace(/\n*$/, '\n') : ''}`;
}

// ---------------------------------------------------------------------------------------------------
// writing and checking

function readManifest(dir) {
  try { return JSON.parse(fs.readFileSync(path.join(dir, MANIFEST), 'utf8')); } catch { return null; }
}

function manifestFor(files, meta) {
  const sorted = [...files.keys()].sort();
  return Buffer.from(JSON.stringify({ generator: TOOL, ...meta, files: Object.fromEntries(sorted.map((f) => [f, sha256(files.get(f))])) }, null, 2) + '\n');
}

/** Files on disk under dir, skipping the other target dirs nested in it. */
function diskFiles(dir, skip) {
  if (!fs.existsSync(dir)) return [];
  return walk(dir).filter((f) => !skip.some((s) => (path.join(dir, f) + path.sep).startsWith(s + path.sep)));
}

/** What is different between the wanted files and dir: {add, change, remove} lists of relative paths. */
function diff(dir, files, skip) {
  const on = new Set(diskFiles(dir, skip));
  const add = [], change = [];
  for (const [f, buf] of files) {
    if (!on.has(f)) add.push(f);
    else if (!fs.readFileSync(path.join(dir, f)).equals(buf)) change.push(f);
    on.delete(f);
  }
  return { add, change, remove: [...on] };
}

/** Throws unless every file that would be touched was written by this script and is unedited. */
function guard(t, d, force) {
  if (force) return;
  const man = readManifest(t.dir);
  const touched = [...d.change, ...d.remove];
  if (!touched.length) return;
  const name = path.relative(process.cwd(), t.dir) || '.';
  if (!man || man.generator !== TOOL) {
    fail(`${name} has files ${TOOL} didn't write (no ${MANIFEST}); refusing to overwrite or delete:\n  ${touched.slice(0, 10).join('\n  ')}${touched.length > 10 ? `\n  ... ${touched.length - 10} more` : ''}\nIf it is an older vendored copy, re-run with --force once to adopt it.`);
  }
  const bad = touched.filter((f) => f !== MANIFEST && (!man.files?.[f] || man.files[f] !== sha256(fs.readFileSync(path.join(t.dir, f)))));
  if (bad.length) fail(`${name}: files not written by ${TOOL} or edited since; refusing to overwrite or delete:\n  ${bad.join('\n  ')}\n(--force to discard them)`);
}

function apply(t, d) {
  for (const f of [...d.add, ...d.change]) {
    fs.mkdirSync(path.dirname(path.join(t.dir, f)), { recursive: true });
    fs.writeFileSync(path.join(t.dir, f), t.files.get(f));
  }
  for (const f of d.remove) {
    fs.rmSync(path.join(t.dir, f));
    for (let p = path.dirname(path.join(t.dir, f)); p.startsWith(t.dir + path.sep); p = path.dirname(p)) {
      if (fs.readdirSync(p).length) break;
      fs.rmdirSync(p);
    }
  }
}

/** Checks an npm package dir offline: same version, files as its manifest says, required addons present. */
function checkPackageDir(dir, name, version, required) {
  const problems = [];
  const man = readManifest(dir);
  const rel = path.relative(process.cwd(), dir) || '.';
  if (!man || man.package !== name) return [`${rel}: no ${name} vendored by ${TOOL}`];
  if (version && man.version !== version) problems.push(`${rel}: ${name} ${man.version}, wanted ${version}`);
  for (const [f, h] of Object.entries(man.files)) {
    const p = path.join(dir, f);
    if (!fs.existsSync(p)) problems.push(`${rel}/${f}: missing`);
    else if (sha256(fs.readFileSync(p)) !== h) problems.push(`${rel}/${f}: edited`);
  }
  for (const f of required) if (!man.files[f]) problems.push(`${rel}/${f}: needed by boarddd but not vendored`);
  return problems;
}

export async function run(argv, log = console.log) {
  const o = parseArgs(argv);
  if (o.help) { log(USAGE); return 0; }
  const say = o.quiet ? () => {} : log;
  const source = readSource(o);
  const b = boardddFiles(o, source);
  if (b.added.length) say(`boarddd: also taking ${b.added.join(', ')} (imported by the subpaths asked for)`);

  const info = {};
  const neededThree = ['three.module.js', ...b.addons.map((a) => `addons/${a}`)];
  const targets = [];
  // --check reads nothing from the network: an npm dir is checked against its own manifest
  const pkgFor = async (name, spec) => {
    if (!o.check || /\.tgz$/.test(spec)) return fetchPackage(name, spec, o.registry);
    const man = readManifest(name === 'three' ? o.threeDir : o.occtDir);
    return { name, version: spec, integrity: man?.version === spec ? man.integrity : 'unknown', files: null };
  };
  if (o.three) {
    const pkg = info.three = await pkgFor('three', o.three);
    const t = { dir: o.threeDir, what: `three ${pkg.version}`, pkg: 'three' };
    if (pkg.files) {
      t.files = threeFiles(pkg, [...b.addons, ...o.addons], o.imports, o.threeDir);
      t.files.set(MANIFEST, manifestFor(t.files, { package: 'three', version: pkg.version, integrity: pkg.integrity, addons: [...b.addons, ...o.addons].sort() }));
    }
    targets.push(t);
  }
  if (o.occt) {
    const pkg = info.occt = await pkgFor('occt-import-js', o.occt);
    const t = { dir: o.occtDir, what: `occt-import-js ${pkg.version}`, pkg: 'occt-import-js' };
    if (pkg.files) {
      t.files = occtFiles(pkg);
      t.files.set(MANIFEST, manifestFor(t.files, { package: 'occt-import-js', version: pkg.version, integrity: pkg.integrity }));
    }
    targets.push(t);
  }
  const files = b.files;
  files.set('COMMIT', Buffer.from(commitFile(o, source, b, info)));
  files.set('README.md', Buffer.from(readmeFile(o, source, b, info)));
  files.set(MANIFEST, manifestFor(files, { package: 'boarddd', version: b.pkg.version, commit: describeCommit(source), tag: source.tag, subpaths: b.subpaths }));
  targets.unshift({ dir: o.out, files, what: `boarddd ${b.pkg.version} ${source.tag || describeCommit(source).slice(0, 7)} [${b.subpaths.join(' ')}]` });

  // a target's own scan skips the other targets nested in it (the default three/occt dirs live
  // inside --out even when this run doesn't manage them)
  const managed = [...new Set([...targets.map((t) => t.dir), o.threeDir, o.occtDir])];
  for (const t of targets) t.skip = managed.filter((d) => d !== t.dir && (d + path.sep).startsWith(t.dir + path.sep));

  const problems = [];
  if (o.imports === 'relative' && !o.three && !o.noThree) {
    const missing = neededThree.filter((f) => !fs.existsSync(path.join(o.threeDir, f)));
    if (missing.length) problems.push(`${path.relative(process.cwd(), o.threeDir) || '.'} lacks ${missing.join(', ')}: pass --three <version> to vendor it, --three-dir to point at it, or --no-three`);
  }

  if (o.check) {
    for (const t of targets) {
      if (t.pkg) {
        const required = t.pkg === 'three' ? [...neededThree, ...o.addons.map((a) => `addons/${a}`)] : [];
        problems.push(...checkPackageDir(t.dir, t.pkg, (t.pkg === 'three' ? info.three : info.occt).version, required));
        if (!t.files) continue;
      }
      const d = diff(t.dir, t.files, t.skip);
      const rel = path.relative(process.cwd(), t.dir) || '.';
      for (const f of d.add) problems.push(`${rel}/${f}: missing`);
      for (const f of d.change) problems.push(`${rel}/${f}: differs`);
      for (const f of d.remove) problems.push(`${rel}/${f}: not part of the vendored tree`);
    }
    if (problems.length) {
      log(`vendor --check: ${problems.length} problem${problems.length > 1 ? 's' : ''}\n  ${problems.join('\n  ')}`);
      return 1;
    }
    say(`vendor --check: ${targets.map((t) => t.what).join('; ')}: up to date`);
    return 0;
  }

  if (problems.length) fail(problems.join('\n'));
  const plans = targets.map((t) => ({ t, d: diff(t.dir, t.files, t.skip) }));
  for (const { t, d } of plans) guard(t, d, o.force);   // all checks before any write
  for (const { t, d } of plans) {
    const n = d.add.length + d.change.length + d.remove.length;
    const rel = path.relative(process.cwd(), t.dir) || '.';
    if (!n) { say(`${t.what} -> ${rel}: up to date`); continue; }
    apply(t, d);
    say(`${t.what} -> ${rel}: ${d.add.length} added, ${d.change.length} changed, ${d.remove.length} removed`);
  }
  return 0;
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  run(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (err) => {
    if (!(err instanceof VendorError)) throw err;
    console.error(`vendor: ${err.message}`);
    process.exitCode = 2;
  });
}
