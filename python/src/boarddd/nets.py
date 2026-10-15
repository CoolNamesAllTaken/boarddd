"""
Choosing nets by group: net-name patterns and tracks like a picked one (docs/nets.md).

The same rules as the JS ``boarddd/nets`` (``src/nets/``); ``fixtures/nets/cases.json`` keeps the two in step.

    USB_D*        wildcards: * any run, ? one character, [abc] / [!abc]; case-insensitive
    RF_{A,B,C}    alternatives
    *_{P,N}       two alternatives make pairs: the first is the + / P half
    *_P/*_N       the same pair, written as two patterns
    re:^CLK\\d+$   a regular expression (full match, case-insensitive)

A pattern without '/' also matches the last part of a hierarchical name: ``USB_D*`` matches ``/usb/USB_D+``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

__all__ = [
    "Pattern",
    "parse_pattern",
    "matches_pattern",
    "match_nets",
    "pairs_among",
    "similar_tracks",
    "pair_gap",
    "width_tolerance",
    "gap_tolerance",
]

_PAIR_RULE = re.compile(r"^(.*?)(\+|-|_P|_N|P|N)$")
_PARTNER = {"+": "-", "-": "+", "_P": "_N", "_N": "_P", "P": "N", "N": "P"}
_POSITIVE = re.compile(r"(\+|_P|P)$")
_SPECIAL = set(".*+?^${}()|[]\\/")


def _escape(text: str) -> str:
    return "".join("\\" + c if c in _SPECIAL else c for c in text)


def _glob_source(glob: str) -> str:
    """A glob as regex source, each wildcard a capture group (they key the pairs). Raises ValueError."""
    out = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            out.append("(.*?)")
        elif c == "?":
            out.append("(.)")
        elif c == "[":
            end = glob.find("]", i + 2)
            if end < 0:
                raise ValueError(f"unclosed [ in {glob}")
            body = glob[i + 1 : end]
            neg = body[:1] in ("!", "^")
            if neg:
                body = body[1:]
            body = re.sub(r"([\\\[\]^])", r"\\\1", body)
            out.append(f"([{'^' if neg else ''}{body}])")
            i = end
        elif c == "{":
            end = glob.find("}", i)
            if end < 0:
                raise ValueError(f"unclosed {{ in {glob}")
            out.append("(" + "|".join(_escape(a) for a in glob[i + 1 : end].split(",")) + ")")
            i = end
        else:
            out.append(_escape(c))
        i += 1
    return "".join(out)


def _pair_halves(text: str) -> tuple[str, str] | None:
    braces = list(re.finditer(r"\{([^{}]*)\}", text))
    if len(braces) == 1:
        alts = braces[0].group(1).split(",")
        if len(alts) == 2 and alts[0] != alts[1]:
            a, b = braces[0].span()
            return text[:a] + alts[0] + text[b:], text[:a] + alts[1] + text[b:]
    parts = text.split("/")
    if len(parts) == 2 and all(p and re.search(r"[*?]", p) for p in parts):
        return parts[0], parts[1]
    return None


def _compile(src: str, leaf: bool, case_sensitive: bool) -> re.Pattern:
    return re.compile(rf"^(?:(.*/))?(?:{src})$" if leaf else rf"^(?:{src})$", 0 if case_sensitive else re.I)


@dataclass
class Pattern:
    """A parsed pattern. ``pair`` is (positive half, negative half); ``error`` says why it matches nothing."""

    text: str
    regex: bool = False
    pair: tuple[str, str] | None = None
    error: str | None = None
    res: list[re.Pattern] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "regex": self.regex,
            "pair": list(self.pair) if self.pair else None,
            "error": self.error,
        }


def parse_pattern(text: str, *, case_sensitive: bool = False) -> Pattern:
    """A pattern, parsed (``case_sensitive`` as KiCad's own netclass patterns are)."""
    t = str(text if text is not None else "").strip()
    out = Pattern(text=t)
    try:
        if not t:
            raise ValueError("empty pattern")
        if t[:3].lower() == "re:":
            out.regex = True
            if not t[3:]:
                raise ValueError("empty pattern")
            try:
                out.res = [_compile(t[3:], False, case_sensitive)]
            except re.error as exc:
                raise ValueError(f"bad regular expression: {exc}") from None
            return out
        out.pair = _pair_halves(t)
        out.res = [_compile(_glob_source(g), "/" not in g, case_sensitive) for g in (out.pair or (t,))]
    except (ValueError, re.error) as exc:
        out.error = str(exc)
        out.res = []
    return out


def _key(rx: re.Pattern, name: str) -> str | None:
    m = rx.match(name)
    return None if m is None else "\0".join(g or "" for g in m.groups())


def matches_pattern(pattern: str | Pattern, name: str, *, case_sensitive: bool = False) -> bool:
    p = parse_pattern(pattern, case_sensitive=case_sensitive) if isinstance(pattern, str) else pattern
    return any(rx.match(name) for rx in p.res)


def _board_pairs(board) -> dict[str, str]:
    if board is None:
        return {}
    nets = board.get("nets") if isinstance(board, dict) else getattr(board, "nets", None)
    out = {}
    for n in nets or []:
        name, pair = (n.get("name"), n.get("pair")) if isinstance(n, dict) else (n.name, n.pair)
        if pair:
            out[name] = pair
    return out


def pairs_among(nets: list[str], *, board=None) -> list[tuple[str, str]]:
    """Differential pairs among ``nets``: board@1 Net.pair, else the naming rule (+/-, _P/_N, P/N)."""
    have = set(nets)
    named = _board_pairs(board)
    seen: set[str] = set()
    out = []
    for net in nets:
        if net in seen:
            continue
        partner = named.get(net)
        if not partner:
            m = _PAIR_RULE.match(net)
            partner = m.group(1) + _PARTNER[m.group(2)] if m and m.group(1) else None
        if not partner or partner not in have or partner in seen or partner == net:
            continue
        seen.update((net, partner))
        out.append((net, partner) if _POSITIVE.search(net) or not _POSITIVE.search(partner) else (partner, net))
    return out


def match_nets(names: list[str], patterns, *, board=None, case_sensitive: bool = False) -> dict:
    """
    Match patterns against net names: ``{nets, pairs, patterns: [{text, regex, pair, error, nets, pairs}]}``, as
    the JS ``matchNets``.
    """
    texts = [patterns] if isinstance(patterns, str) else list(patterns)
    parsed = [p for p in (parse_pattern(t, case_sensitive=case_sensitive) for t in texts) if p.text]
    matched: set[str] = set()
    pairs: list[tuple[str, str]] = []
    paired: set[str] = set()
    per = []
    for p in parsed:
        nets = [n for n in names if any(rx.match(n) for rx in p.res)]
        matched.update(nets)
        own = []
        if p.pair:
            negatives: dict[str, str] = {}
            for n in names:
                k = _key(p.res[1], n)
                if k is not None and k not in negatives:
                    negatives[k] = n
            for n in names:
                k = _key(p.res[0], n)
                partner = negatives.get(k) if k is not None else None
                if partner and partner != n:
                    own.append((n, partner))
        for pr in own:
            if pr[0] in paired or pr[1] in paired:
                continue
            paired.update(pr)
            pairs.append(pr)
        per.append({**p.to_dict(), "nets": nets, "pairs": [list(x) for x in own]})
    nets = [n for n in names if n in matched]
    pairs += pairs_among([n for n in nets if n not in paired], board=board)
    return {"nets": nets, "pairs": [list(x) for x in pairs], "patterns": per}


# ── tracks like a picked one ──────────────────────────────────────────────────────────────────────────────────


def width_tolerance(w: float) -> float:
    return max(0.001, 0.005 * w)


def gap_tolerance(g: float) -> float:
    return max(0.002, 0.02 * g)


def _round(v: float, n: int) -> float:
    """JS Math.round(v * 10**n) / 10**n (half up), so both languages give the same digits."""
    return math.floor(v * 10**n + 0.5) / 10**n


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _pieces(t) -> list:
    s, e, mid = _get(t, "start"), _get(t, "end"), _get(t, "mid")
    return [(s, mid), (mid, e)] if mid is not None else [(s, e)]


def _len(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _track_length(t) -> float:
    total = 0.0
    for a, b in _pieces(t):
        total += _len(a, b)
    return total


def _tracks_of(copper, layer: str, nets, width: float | None = None) -> list:
    want = set(nets)
    return [
        t
        for t in _get(copper, "tracks") or []
        if _get(t, "layer") == layer
        and _get(t, "net") in want
        and (width is None or abs(_get(t, "width") - width) <= width_tolerance(width))
    ]


def pair_gap(copper, a: str, b: str, *, layer: str, width: float | None = None) -> dict | None:
    """Edge-to-edge gap between two nets' parallel tracks on a layer: ``{gap, length}`` or None (see the JS)."""
    ta_list, tb_list = _tracks_of(copper, layer, [a], width), _tracks_of(copper, layer, [b], width)
    buckets: dict[int, list[float]] = {}
    for ta in ta_list:
        for p0, p1 in _pieces(ta):
            la = _len(p0, p1)
            if la < 1e-6:
                continue
            ux, uy = (p1[0] - p0[0]) / la, (p1[1] - p0[1]) / la
            for tb in tb_list:
                w = (_get(ta, "width") + _get(tb, "width")) / 2
                window = max(0.5, 4 * w)
                for q0, q1 in _pieces(tb):
                    lb = _len(q0, q1)
                    if lb < 1e-6:
                        continue
                    if abs(((q1[0] - q0[0]) * ux + (q1[1] - q0[1]) * uy) / lb) < 0.985:
                        continue
                    s0 = (q0[0] - p0[0]) * ux + (q0[1] - p0[1]) * uy
                    s1 = (q1[0] - p0[0]) * ux + (q1[1] - p0[1]) * uy
                    overlap = min(la, max(s0, s1)) - max(0, min(s0, s1))
                    if overlap <= 1e-6:
                        continue
                    d0 = (q0[1] - p0[1]) * ux - (q0[0] - p0[0]) * uy
                    d1 = (q1[1] - p0[1]) * ux - (q1[0] - p0[0]) * uy
                    d = abs((d0 + d1) / 2)
                    if d > window:
                        continue
                    gap = d - w
                    if gap <= 0:
                        continue
                    key = math.floor(gap * 1000 + 0.5)
                    was = buckets.setdefault(key, [0.0, 0.0])
                    was[0] += overlap
                    was[1] += gap * overlap
    if not buckets:
        return None
    best_key = None
    for k, v in buckets.items():
        best = buckets[best_key] if best_key is not None else None
        if best is None or v[0] > best[0] + 1e-9 or (abs(v[0] - best[0]) <= 1e-9 and k < best_key):
            best_key = k
    best = buckets[best_key]
    total = 0.0
    for v in buckets.values():
        total += v[0]
    return {"gap": _round(best[1] / best[0], 4), "length": _round(total, 3)}


def similar_tracks(copper, seed: dict, *, board=None) -> dict:
    """
    Tracks like the seed ``{layer, width?, nets}``: ``{kind, layer, width, gap, nets, pairs}``, as the JS
    ``similarTracks`` (one net: every net with tracks of that width on that layer; a pair: the pairs at that
    width with the seed's gap).
    """
    layer = seed["layer"]
    seed_nets = list(seed.get("nets") or [])
    width = seed.get("width")
    if width is None:
        by_width: dict[float, float] = {}
        for t in _tracks_of(copper, layer, seed_nets[:1]):
            by_width[_get(t, "width")] = by_width.get(_get(t, "width"), 0.0) + _track_length(t)
        ranked = sorted(by_width.items(), key=lambda kv: (-kv[1], kv[0]))
        width = ranked[0][0] if ranked else None
    out = {
        "kind": "differential" if len(seed_nets) == 2 else "single",
        "layer": layer,
        "width": width,
        "gap": None,
        "nets": [],
        "pairs": [],
    }
    if width is None:
        return out
    tol = width_tolerance(width)
    acc: dict[str, dict] = {}
    for t in _get(copper, "tracks") or []:
        if _get(t, "layer") != layer or not _get(t, "net") or abs(_get(t, "width") - width) > tol:
            continue
        was = acc.setdefault(_get(t, "net"), {"net": _get(t, "net"), "length": 0.0, "tracks": 0})
        was["length"] += _track_length(t)
        was["tracks"] += 1
    planes = {_get(p, "net") for p in _get(copper, "planes") or [] if _get(p, "solid")}
    names = list(acc)
    partner: dict[str, str] = {}
    for p, n in pairs_among(names, board=board):
        partner[p], partner[n] = n, p
    out["nets"] = sorted(
        (
            {
                "net": v["net"],
                "length": _round(v["length"], 3),
                "tracks": v["tracks"],
                "pair": partner.get(v["net"]),
                "plane": v["net"] in planes,
            }
            for v in acc.values()
        ),
        key=lambda v: (-v["length"], v["net"]),
    )
    if out["kind"] == "single":
        return out

    g0 = pair_gap(copper, seed_nets[0], seed_nets[1], layer=layer, width=width)
    if not g0:
        return out
    out["gap"] = g0["gap"]
    gtol = gap_tolerance(g0["gap"])
    candidates: list[dict] = []
    used: set[str] = set()
    seed_key = "\0".join(sorted(seed_nets))

    def consider(p: str, n: str) -> bool:
        g = pair_gap(copper, p, n, layer=layer, width=width)
        if not g or abs(g["gap"] - g0["gap"]) > gtol:
            return False
        candidates.append({"nets": [p, n], "gap": g["gap"], "length": g["length"]})
        used.update((p, n))
        return True

    for p, n in pairs_among(names, board=board):
        consider(p, n)
    lone = [
        v["net"] for v in out["nets"] if v["net"] not in used and v["net"] not in partner and v["net"] not in planes
    ]
    for i in range(len(lone)):
        if lone[i] in used:
            continue
        for j in range(i + 1, len(lone)):
            if lone[j] in used:
                continue
            if consider(lone[i], lone[j]):
                break
    candidates.sort(key=lambda c: ("\0".join(sorted(c["nets"])) != seed_key, -c["length"], c["nets"][0]))
    out["pairs"] = candidates
    return out
