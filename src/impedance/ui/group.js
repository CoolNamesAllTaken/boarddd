// boarddd/impedance/ui: netGroup, nets chosen as a group. Two ways in, one list out:
//   ≋        tracks like the picked one (boarddd/nets similarTracks: same width on the layer; a pair: same gap too)
//   ⌕ text   net-name patterns (matchNets: USB_D*, *_{P,N}, re:...), matched as they are typed
// The matches are chips, each one ticked; untick to drop it. ✓ hands the group over ('apply'), ✕ clears it.
//   [≋] [⌕ USB_D*        ]  13 ⇄  w 0.13 s 0.25 F.Cu  [✓] [✕]
//   [☑ ⇄ ETH_PI.TRD0] [☑ ⇄ DPHY0_P.D0] [☐ — HDMI_PI.SDA] …
import { matchNets, similarTracks } from '../../nets/index.js';

const el = (tag, cls, parent, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};
const mm = (v) => (v == null ? '–' : String(+v.toFixed(4)));
/** A net's short name: the last part of a hierarchical name; a pair's without its +/-, _P/_N, P/N. */
export function shortName(nets) {
  const leaf = (n) => n.slice(n.lastIndexOf('/') + 1) || n;
  if (nets.length !== 2) return leaf(nets[0]);
  const a = leaf(nets[0]), b = leaf(nets[1]);
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  return a.slice(0, i).replace(/[_.\-]$/, '') || a;
}

/**
 * The group box in `el`. `copper` (copper@1) and `board` (board@1, for Net.pair) give the nets. Events (`on`):
 * 'change' { members, nets } as it is edited (the host highlights `nets`), 'apply' the group, 'clear'.
 * A group: { source: 'similar' | 'pattern', kind, layer, width, gap, patterns, members: [{ nets, on, length? }],
 * nets (the ticked members' nets), pairs (the ticked pairs) }.
 */
export function netGroup(el0, { copper, board = null, names = null, placeholder = 'USB_D*  *_{P,N}  RF_*' } = {}) {
  const all = names ?? copper?.nets?.filter((n) => n) ?? [];
  const listeners = new Map();
  const emit = (n, v) => { for (const fn of listeners.get(n) ?? []) fn(v); };
  const root = el('div', 'bdi-group', el0);
  root.dataset.state = 'empty';
  const head = el('div', 'bdi-group-head', root);
  const simBtn = el('button', 'bdi-gbtn bdi-similar', head, '≋');
  simBtn.type = 'button';
  simBtn.title = 'select similar: every track of this width on this layer (a pair: the same width and gap)';
  simBtn.setAttribute('aria-label', 'Select similar tracks');
  simBtn.disabled = true;
  const input = el('input', 'bdi-pattern', head);
  input.type = 'text';
  input.placeholder = placeholder;
  input.spellcheck = false;
  input.title = 'net names: * any, ? one, [abc], {a,b}; *_{P,N} or *_P/*_N pairs; re:… a regular expression; several with spaces';
  input.setAttribute('aria-label', 'Net name patterns');
  const count = el('span', 'bdi-gcount', head);
  const geom = el('span', 'bdi-ggeom', head);
  const applyBtn = el('button', 'bdi-gbtn bdi-apply', head, '✓');
  applyBtn.type = 'button';
  applyBtn.title = 'use these nets';
  applyBtn.setAttribute('aria-label', 'Use these nets');
  const clearBtn = el('button', 'bdi-gbtn bdi-gclear', head, '✕');
  clearBtn.type = 'button';
  clearBtn.title = 'clear';
  clearBtn.setAttribute('aria-label', 'Clear the group');
  const list = el('ul', 'bdi-members', root);

  let group = null;
  let seed = null;

  function nets() { return group ? group.members.filter((m) => m.on).flatMap((m) => m.nets) : []; }
  function value() {
    if (!group) return null;
    const on = group.members.filter((m) => m.on);
    return { ...group, nets: on.flatMap((m) => m.nets), pairs: on.filter((m) => m.nets.length === 2).map((m) => m.nets) };
  }

  function render() {
    list.replaceChildren();
    root.dataset.state = group ? (group.members.length ? 'ready' : 'none') : 'empty';
    const on = group ? group.members.filter((m) => m.on) : [];
    const pairs = on.filter((m) => m.nets.length === 2).length, singles = on.length - pairs;
    count.textContent = group ? [pairs ? `${pairs} ⇄` : '', singles ? `${singles} —` : '', !on.length ? '0' : ''].filter(Boolean).join(' ') : '';
    count.title = group ? `${on.length} of ${group.members.length} chosen: ${pairs} pair${pairs === 1 ? '' : 's'}, ${singles} single net${singles === 1 ? '' : 's'}` : '';
    geom.textContent = group?.source === 'similar' ? `w ${mm(group.width)}${group.gap != null ? ` s ${mm(group.gap)}` : ''} ${group.layer}` : '';
    geom.title = group?.source === 'similar' ? `track width${group.gap != null ? ' and pair gap (edge to edge)' : ''}, mm, on ${group.layer}` : '';
    applyBtn.disabled = !on.length;
    if (!group) return;
    group.members.forEach((m, i) => {
      const li = el('li', `bdi-member${m.on ? ' is-on' : ''}`, list);
      const label = el('label', null, li);
      const box = el('input', null, label);
      box.type = 'checkbox';
      box.checked = m.on;
      box.dataset.i = String(i);
      el('span', 'bdi-msym', label, m.nets.length === 2 ? '⇄' : '—');
      el('span', 'bdi-mname', label, shortName(m.nets));
      li.title = `${m.nets.join(' ')}${m.length != null ? ` · ${m.length.toFixed(1)} mm` : ''}${m.note ? ` · ${m.note}` : ''}`;
      box.addEventListener('change', () => { m.on = box.checked; li.classList.toggle('is-on', m.on); render(); emit('change', value()); });
    });
  }

  function set(g) {
    group = g;
    render();
    emit('change', value());
  }

  /** Tracks like the seed ({ layer, width, nets }). */
  function similar(s = seed) {
    if (!s || !copper) return null;
    input.value = '';
    const r = similarTracks(copper, s, { board });
    const members = r.kind === 'differential'
      ? r.pairs.map((p) => ({ nets: p.nets, on: true, length: p.length, note: `gap ${mm(p.gap)} mm` }))
      : r.nets.map((n) => ({ nets: [n.net], on: !n.plane, length: n.length, note: n.plane ? 'a plane net: left out' : n.pair ? `half of a pair with ${n.pair}` : '' }));
    set({ source: 'similar', kind: r.kind, layer: r.layer, width: r.width, gap: r.gap, patterns: [], members });
    return value();
  }

  /** Nets matching patterns (a string, split at spaces, or a list). */
  function patterns(texts) {
    const list0 = Array.isArray(texts) ? texts : String(texts ?? '').split(/\s+(?![^{[]*[}\]])/).filter(Boolean);
    if (Array.isArray(texts)) input.value = list0.join(' ');
    if (!list0.length) { set(null); return null; }
    const r = matchNets(all, list0, { board });
    const paired = new Set(r.pairs.flat());
    const members = [
      ...r.pairs.map((p) => ({ nets: p, on: true })),
      ...r.nets.filter((n) => !paired.has(n)).map((n) => ({ nets: [n], on: true })),
    ];
    const bad = r.patterns.filter((p) => p.error);
    input.classList.toggle('is-bad', bad.length > 0);
    input.title = bad.length ? bad.map((p) => `${p.text}: ${p.error}`).join('\n') : input.title.split('\n')[0];
    const kind = members.length && members.every((m) => m.nets.length === 2) ? 'differential' : 'single';
    set({ source: 'pattern', kind, layer: null, width: null, gap: null, patterns: list0, members });
    return value();
  }

  simBtn.addEventListener('click', () => similar());
  let typing = null;
  input.addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => patterns(input.value), 120); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(typing); patterns(input.value); if (nets().length) applyBtn.click(); } });
  applyBtn.addEventListener('click', () => { const v = value(); if (v && v.nets.length) emit('apply', v); });
  clearBtn.addEventListener('click', () => api.clear());

  const api = {
    root,
    get value() { return value(); },
    /** The pick a ≋ starts from ({ layer, width, nets }), or null (≋ off). */
    setSeed(s) { seed = s; simBtn.disabled = !s; },
    similar,
    patterns,
    /** Tick or untick members by net name. */
    toggle(net, on) {
      if (!group) return;
      for (const m of group.members) if (m.nets.includes(net)) m.on = on ?? !m.on;
      render();
      emit('change', value());
    },
    clear() { input.value = ''; input.classList.remove('is-bad'); group = null; render(); emit('clear', null); emit('change', null); },
    on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); return () => listeners.get(name)?.delete(fn); },
    destroy() { clearTimeout(typing); root.remove(); },
  };
  return api;
}
