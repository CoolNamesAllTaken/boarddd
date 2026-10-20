// boarddd/impedance/ui: a type-ahead over a board's nets. fuzzyNets ranks names against what is typed ('rf' finds
// /1090_RF_IN: a whole name, then a name part's start, then anywhere, then the letters in order); netPicker is the
// box with its dropdown (↑ ↓ Enter, Esc; Enter adds and keeps the box open for the next one).

const SPLIT = /[/._\-\s:]+/;

/** How well `name` matches `q` (lower case): 0 best, null none. */
export function netScore(name, q) {
  if (!q) return 10;
  const n = name.toLowerCase();
  if (n === q) return 0;
  const parts = n.split(SPLIT).filter(Boolean);
  if (parts.includes(q)) return 1;
  if (n.startsWith(q) || parts.some((p) => p.startsWith(q))) return 2;
  if (n.includes(q)) return 3;
  // the letters in order: fewer gaps first
  let i = 0, gaps = 0, last = -1;
  for (const c of q) {
    const j = n.indexOf(c, i);
    if (j < 0) return null;
    if (last >= 0 && j > last + 1) gaps++;
    last = j; i = j + 1;
  }
  return 4 + gaps;
}

/**
 * Nets matching `query`, best first: entries are names or { name, ... }; `first` (names) go to the top when they
 * match (a likely pair partner); at most `limit`.
 */
export function fuzzyNets(entries, query, { limit = 12, first = [] } = {}) {
  const q = String(query ?? '').trim().toLowerCase();
  const out = [];
  for (const e of entries) {
    const name = typeof e === 'string' ? e : e.name;
    const s = netScore(name, q);
    if (s == null) continue;
    out.push({ e, s: first.includes(name) ? -1 - (first.length - first.indexOf(name)) : s, name });
  }
  out.sort((a, b) => a.s - b.s || a.name.length - b.name.length || a.name.localeCompare(b.name));
  return out.slice(0, limit).map((x) => x.e);
}

let seq = 0;

/**
 * A net box in `el`. Options: `source` (entries [{ name, pads?, layers?, auto? }] or names, or an async function of
 * the query resolving to them), `onPick(name)`, `first()` (names to suggest first), `placeholder`, `label`,
 * `exclude()` (names to leave out, e.g. the selection), `keepOpen` (default true: Enter adds and the box stays for
 * the next). Returns { root, input, focus(), set(text), destroy() }.
 */
export function netPicker(el, { source = [], onPick = () => {}, first = () => [], exclude = () => [], placeholder = 'Net…', label = 'Net', keepOpen = true } = {}) {
  const id = `bdiNp${++seq}`;
  const root = document.createElement('div');
  root.className = 'bdi-np';
  const input = document.createElement('input');
  input.className = 'bdi-pattern bdi-np-input';
  input.type = 'text';
  input.placeholder = placeholder;
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-label', label);
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-controls', `${id}-list`);
  const box = document.createElement('ul');
  box.className = 'bdi-np-list';
  box.id = `${id}-list`;
  box.setAttribute('role', 'listbox');
  box.hidden = true;
  root.append(input, box);
  el.append(root);
  let items = [];
  let active = -1;
  let run = 0;

  const close = () => { box.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
  function draw() {
    box.replaceChildren();
    items.forEach((e, i) => {
      const li = document.createElement('li');
      li.id = `${id}-o${i}`;
      li.className = `bdi-np-opt${i === active ? ' is-active' : ''}`;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', String(i === active));
      li.dataset.net = e.name;
      const n = document.createElement('span');
      n.className = 'bdi-np-name';
      n.textContent = e.name;
      li.append(n);
      const bits = [e.pads != null ? `${e.pads} pad${e.pads === 1 ? '' : 's'}` : null, e.layers?.length ? e.layers.join(' ') : null].filter(Boolean);
      if (bits.length || e.hint) {
        const m = document.createElement('span');
        m.className = 'bdi-np-meta';
        m.textContent = [e.hint, ...bits].filter(Boolean).join(' · ');
        li.append(m);
      }
      li.title = `${e.name}${e.auto ? ' · named here (no net name in the files)' : ''}`;
      // pointerdown, not click: the input keeps the focus (no blur closing the list first)
      li.addEventListener('pointerdown', (ev) => { ev.preventDefault(); pick(i); });
      box.append(li);
    });
    box.hidden = !items.length;
    input.setAttribute('aria-expanded', String(!box.hidden));
    if (active >= 0) input.setAttribute('aria-activedescendant', `${id}-o${active}`); else input.removeAttribute('aria-activedescendant');
  }
  async function update() {
    const mine = ++run;
    const q = input.value;
    const skip = new Set(exclude());
    let found;
    if (typeof source === 'function') {
      try { found = await source(q); } catch { found = []; }
      if (mine !== run) return;
      found = (found ?? []).map((e) => (typeof e === 'string' ? { name: e } : e));
      const lead = first();
      found = [...found.filter((e) => lead.includes(e.name)), ...found.filter((e) => !lead.includes(e.name))];
    } else {
      found = fuzzyNets(source.map((e) => (typeof e === 'string' ? { name: e } : e)), q, { first: first(), limit: 40 });
    }
    items = found.filter((e) => !skip.has(e.name)).slice(0, 12);
    active = items.length && q.trim() ? 0 : -1;
    draw();
  }
  function pick(i) {
    const e = items[i];
    if (!e) return;
    onPick(e.name);
    input.value = '';
    if (keepOpen) update(); else close();
  }
  input.addEventListener('input', update);
  input.addEventListener('focus', update);
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (box.hidden) { update(); return; }
      if (!items.length) return;
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      draw();
      box.children[active]?.scrollIntoView?.({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pick(active >= 0 ? active : 0);
    } else if (e.key === 'Escape') {
      if (!box.hidden) { e.stopPropagation(); close(); }
    }
  });
  return {
    root, input,
    focus() { input.focus(); },
    set(text) { input.value = text; update(); },
    refresh: update,
    destroy() { root.remove(); },
  };
}
