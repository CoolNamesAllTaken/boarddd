// boarddd/nets: net-name patterns. The same rules as python's boarddd.nets (fixtures/nets/cases.json keeps them
// in step); see docs/nets.md.
//
//   USB_D*        wildcards: * any run, ? one character, [abc] / [!abc] one of / none of; case-insensitive
//   RF_{A,B,C}    alternatives
//   *_{P,N}       two alternatives make pairs: the first is the + / P half
//   *_P/*_N       the same pair, written as two patterns
//   re:^CLK\d+$   a regular expression (full match, case-insensitive)
//
// A pattern without '/' also matches the last part of a hierarchical name: USB_D* matches /usb/USB_D+.

const PAIR_RULE = /^(.*?)(\+|-|_P|_N|P|N)$/;
const PARTNER = { '+': '-', '-': '+', _P: '_N', _N: '_P', P: 'N', N: 'P' };
const POSITIVE = /(\+|_P|P)$/;

const escape = (c) => c.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

/** A glob as regex source, each wildcard a capture group (they key the pairs). Throws on a bad pattern. */
function globSource(glob) {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') out += '(.*?)';
    else if (c === '?') out += '(.)';
    else if (c === '[') {
      const end = glob.indexOf(']', i + 2);
      if (end < 0) throw new Error(`unclosed [ in ${glob}`);
      let body = glob.slice(i + 1, end);
      const not = body[0] === '!' || body[0] === '^';
      if (not) body = body.slice(1);
      out += `([${not ? '^' : ''}${body.replace(/[\\[\]^]/g, '\\$&')}])`;
      i = end;
    } else if (c === '{') {
      const end = glob.indexOf('}', i);
      if (end < 0) throw new Error(`unclosed { in ${glob}`);
      out += `(${glob.slice(i + 1, end).split(',').map(escape).join('|')})`;
      i = end;
    } else out += escape(c);
  }
  return out;
}

/** The two halves of a pair pattern ('*_{P,N}' or '*_P/*_N'), or null. */
function pairHalves(text) {
  const braces = [...text.matchAll(/\{([^{}]*)\}/g)];
  if (braces.length === 1) {
    const alts = braces[0][1].split(',');
    if (alts.length === 2 && alts[0] !== alts[1]) {
      const [a, b] = [braces[0].index, braces[0].index + braces[0][0].length];
      return [text.slice(0, a) + alts[0] + text.slice(b), text.slice(0, a) + alts[1] + text.slice(b)];
    }
  }
  const parts = text.split('/');
  if (parts.length === 2 && parts.every((p) => p && /[*?]/.test(p))) return parts;
  return null;
}

function compile(src, leaf, caseSensitive) {
  return new RegExp(leaf ? `^(?:(.*/))?(?:${src})$` : `^(?:${src})$`, caseSensitive ? 'u' : 'iu');
}

/**
 * A pattern, parsed: `{ text, regex, pair, error }`. `pair` is [positive half, negative half] for a pair pattern.
 * `error` is a message when it can't be used (it then matches nothing). `caseSensitive` as KiCad's own netclass
 * patterns are.
 */
export function parsePattern(text, { caseSensitive = false } = {}) {
  const t = String(text ?? '').trim();
  const out = { text: t, regex: false, pair: null, error: null };
  try {
    if (!t) throw new Error('empty pattern');
    if (/^re:/i.test(t)) {
      out.regex = true;
      const src = t.slice(3);
      if (!src) throw new Error('empty pattern');
      out._res = [compile(src, false, caseSensitive)];
      return out;
    }
    out.pair = pairHalves(t);
    out._res = (out.pair ?? [t]).map((g) => compile(globSource(g), !g.includes('/'), caseSensitive));
  } catch (err) {
    out.error = String(err?.message ?? err);
    out._res = [];
  }
  return out;
}

/** The key of a name under a half's regex (the prefix path and what the wildcards caught), or null. */
function keyOf(re, name) {
  const m = re.exec(name);
  return m ? m.slice(1).map((g) => g ?? '').join('\u0000') : null;
}

/** Whether a name matches a pattern (text or parsed). */
export function matchesPattern(pattern, name, options = {}) {
  const p = typeof pattern === 'string' ? parsePattern(pattern, options) : pattern;
  return p._res.some((re) => re.test(name));
}

/**
 * Differential pairs among `nets`: board@1 `Net.pair` when the board names one, else KiCad's rule (the same name
 * with '+'/'-', '_P'/'_N' or 'P'/'N' swapped). [[positive, negative], ...] in the order of `nets`.
 */
export function pairsAmong(nets, { board = null } = {}) {
  const have = new Set(nets);
  const named = new Map((board?.nets ?? []).filter((n) => n.pair).map((n) => [n.name, n.pair]));
  const seen = new Set();
  const out = [];
  for (const net of nets) {
    if (seen.has(net)) continue;
    let partner = named.get(net) ?? null;
    if (!partner) {
      const m = PAIR_RULE.exec(net);
      partner = m && m[1] ? m[1] + PARTNER[m[2]] : null;
    }
    if (!partner || !have.has(partner) || seen.has(partner) || partner === net) continue;
    seen.add(net).add(partner);
    out.push(POSITIVE.test(net) || !POSITIVE.test(partner) ? [net, partner] : [partner, net]);
  }
  return out;
}

/**
 * Match `patterns` (texts) against net `names`. Returns `{ nets, pairs, patterns: [{ text, regex, pair, error, nets,
 * pairs }] }`: `nets` every match in the order of `names`; `pairs` from pair patterns (halves whose wildcards caught
 * the same text), then pairsAmong the other matches. `caseSensitive: true` matches as KiCad does.
 */
export function matchNets(names, patterns, { board = null, caseSensitive = false } = {}) {
  const list = (Array.isArray(patterns) ? patterns : [patterns]).map((p) => parsePattern(p, { caseSensitive })).filter((p) => p.text);
  const matched = new Set();
  const pairs = [];
  const paired = new Set();
  const per = list.map((p) => {
    const nets = names.filter((n) => p._res.some((re) => re.test(n)));
    for (const n of nets) matched.add(n);
    const own = [];
    if (p.pair) {
      const negatives = new Map();
      for (const n of names) {
        const k = keyOf(p._res[1], n);
        if (k != null && !negatives.has(k)) negatives.set(k, n);
      }
      for (const n of names) {
        const k = keyOf(p._res[0], n);
        const partner = k != null ? negatives.get(k) : undefined;
        if (partner && partner !== n) own.push([n, partner]);
      }
    }
    for (const pr of own) {
      if (paired.has(pr[0]) || paired.has(pr[1])) continue;
      paired.add(pr[0]).add(pr[1]);
      pairs.push(pr);
    }
    return { text: p.text, regex: p.regex, pair: p.pair, error: p.error, nets, pairs: own };
  });
  const nets = names.filter((n) => matched.has(n));
  for (const pr of pairsAmong(nets.filter((n) => !paired.has(n)), { board })) pairs.push(pr);
  return { nets, pairs, patterns: per };
}
