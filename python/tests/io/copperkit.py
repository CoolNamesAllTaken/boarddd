"""Compare two boarddd/copper@1 documents of one board read from different sources (KiCad vs its Gerber X2 export).

What may differ, by design (docs/copper.md "Gerber vs KiCad"):

* Gerber writers recompute arc ends from the centre and angle (KiCad: up to 0.1 um off) and write arcs shorter than
  a few microns as straight draws;
* Gerber has one pad per layer (KiCad: one per pad, on its layers) and no pad rotation or type; KiCad plots a
  custom pad as several flashes (anchor and primitives), compared as one;
* Gerber regions are every filled copper (pours, teardrops, drawings with a net): zones are compared as the total
  area per (layer, net).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from boarddd import copper as cu

POS = 2e-3  # mm: matching tolerance for points
SHORT_ARC = 0.05  # mm: arcs with a shorter chord may come back as straight draws
AREA_REL = 5e-3  # zone and pad areas: relative tolerance
AREA_ABS = 0.01  # mm^2


@dataclass
class Report:
    tracks: int = 0
    arcs: int = 0
    arcs_as_lines: int = 0
    vias: int = 0
    pads: int = 0
    custom_parts: int = 0
    zone_keys: int = 0
    zone_area: float = 0.0
    zone_area_other: float = 0.0
    worst_zone_rel: float = 0.0
    problems: list[str] = field(default_factory=list)

    def row(self, name: str) -> dict:
        return {
            "board": name,
            "tracks": self.tracks,
            "arcs": self.arcs,
            "arcs_as_lines": self.arcs_as_lines,
            "vias": self.vias,
            "pads": self.pads,
            "custom_parts": self.custom_parts,
            "zone_keys": self.zone_keys,
            "zone_area": round(self.zone_area, 3),
            "zone_area_other": round(self.zone_area_other, 3),
            "worst_zone_rel": self.worst_zone_rel,
            "problems": len(self.problems),
        }


def _close(a, b, tol=POS) -> bool:
    return math.dist(a, b) <= tol


def compare(k: cu.Copper, g: cu.Copper) -> Report:
    """``k``: the reference (KiCad), ``g``: the Gerber reading of the same board."""
    rep = Report()
    p = rep.problems
    if k.layers != g.layers:
        p.append(f"layers {k.layers} != {g.layers}")
    if set(k.nets) - {""} != set(g.nets) - {""}:
        p.append(
            f"nets: only KiCad {sorted(set(k.nets) - set(g.nets))[:5]}, only Gerber {sorted(set(g.nets) - set(k.nets))[:5]}"
        )

    # tracks: same layer, net, width, ends within POS; arcs keep their mid
    bucket: dict[tuple, list[cu.Track]] = defaultdict(list)
    for t in g.tracks:
        bucket[(t.layer, t.net, t.width)].append(t)
    for t in k.tracks:
        cands = bucket.get((t.layer, t.net, t.width), [])
        for i, o in enumerate(cands):
            if (_close(t.start, o.start) and _close(t.end, o.end)) or (
                _close(t.start, o.end) and _close(t.end, o.start)
            ):
                cands.pop(i)
                break
        else:
            p.append(f"track only in KiCad: {t.layer} {t.net!r} w={t.width} {t.start}->{t.end}")
            continue
        rep.tracks += 1
        if t.mid is not None:
            rep.arcs += 1
            if o.mid is None:
                if math.dist(t.start, t.end) < SHORT_ARC:
                    rep.arcs_as_lines += 1
                else:
                    p.append(f"arc written as a line: {t.layer} {t.start}->{t.end}")
            elif not _close(t.mid, o.mid):
                p.append(f"arc mid {t.mid} != {o.mid}")
        elif o.mid is not None:
            p.append(f"line read as an arc: {t.layer} {t.start}->{t.end}")
    left = sum(len(v) for v in bucket.values())
    if left:
        p.append(f"{left} tracks only in Gerber, e.g. {next(t for v in bucket.values() for t in v)}")

    # vias: same place, net, ring and span
    gv = {(round(v.at[0], 2), round(v.at[1], 2)): v for v in g.vias}
    for v in k.vias:
        o = gv.pop((round(v.at[0], 2), round(v.at[1], 2)), None)
        if o is None or not _close(v.at, o.at):
            p.append(f"via only in KiCad: {v.at} {v.net!r}")
            continue
        if (v.net, v.span) != (o.net, o.span) or abs(v.diameter - o.diameter) > 1e-6:
            p.append(f"via {v.at}: {v.net!r} {v.span} {v.diameter} != {o.net!r} {o.span} {o.diameter}")
            continue
        rep.vias += 1
    if gv:
        p.append(f"{len(gv)} vias only in Gerber, e.g. {next(iter(gv.values())).at}")

    # pads, per layer: same place (copper centre), net, ref and number; same copper area
    gp: dict[tuple, list[cu.CopperPad]] = defaultdict(list)
    for o in g.pads:
        gp[(o.layers[0], round(o.at[0], 2), round(o.at[1], 2))].append(o)
    for pad in k.pads:
        area = cu.fill_area([cu.FillPolygon(outline=pl) for pl in pad.polygons[:1]])
        for lid in pad.layers:
            cands = gp.get((lid, round(pad.at[0], 2), round(pad.at[1], 2)), [])
            o = next((c for c in cands if _close(c.at, pad.at) and (c.ref, c.number) == (pad.ref, pad.number)), None)
            if o is None:
                p.append(f"pad only in KiCad: {pad.ref}.{pad.number} on {lid} at {pad.at}")
                continue
            cands.remove(o)
            if o.net != pad.net:
                p.append(f"pad {pad.ref}.{pad.number} on {lid}: net {pad.net!r} != {o.net!r}")
            if len(pad.polygons) == 1 and len(o.polygons) == 1:
                ga = cu.fill_area([cu.FillPolygon(outline=o.polygons[0])])
                if abs(area - ga) > max(0.02 * area, AREA_ABS):
                    p.append(
                        f"pad {pad.ref}.{pad.number} on {lid} ({pad.shape}/{o.shape}): area {area:.4f} != {ga:.4f}"
                    )
            rep.pads += 1
    custom = {(pad.ref, pad.number, lid) for pad in k.pads if pad.shape == "custom" for lid in pad.layers}
    rest = [o for v in gp.values() for o in v if (o.ref, o.number, o.layers[0]) not in custom]
    rep.custom_parts = sum(len(v) for v in gp.values()) - len(rest)  # a custom pad plotted as several flashes
    if rest:
        p.append(f"{len(rest)} pads only in Gerber, e.g. {rest[0]}")

    # filled copper: total area per (layer, net)
    ka: dict[tuple, float] = defaultdict(float)
    ga_: dict[tuple, float] = defaultdict(float)
    for z in k.zones:
        ka[(z.layer, z.net)] += z.area
    for z in g.zones:
        ga_[(z.layer, z.net)] += z.area
    for key in sorted(set(ka) | set(ga_)):
        a, b = ka.get(key, 0.0), ga_.get(key, 0.0)
        rep.zone_keys += 1
        rep.zone_area += a
        rep.zone_area_other += b
        rel = abs(a - b) / max(a, b) if max(a, b) else 0.0
        rep.worst_zone_rel = max(rep.worst_zone_rel, round(rel, 6))
        if abs(a - b) > max(AREA_REL * max(a, b), AREA_ABS):
            p.append(f"filled area on {key}: KiCad {a:.4f} mm^2, Gerber {b:.4f} mm^2")
    return rep
