"""
Lining the 3D model up with the board we already know about.

A STEP export puts components in the board's own coordinate frame. The pick-and-place file puts
them in the page frame KiCad plotted with. The two differ by a rigid transform, and because
both files name the same components, that transform can be solved for rather than dragged into
place by hand: match the designators, then find the rotation and shift that best carries one
set of points onto the other.

On a real 66-part KiCad board the answer is exact -- all sixty-six shared components agree on a
translation of (60, -54) mm to within the exporter's own micron of rounding. That is the normal
case for a KiCad export and the whole reason this is automatic. The candidate search and the
confidence reporting exist for the other cases: a model from somewhere else, a board flipped
in export, a file that shares only a handful of designators, or nothing recognisable at all,
where the honest answer is "I cannot place this, move it yourself".

The mirror candidate is a rotation about X, not a reflection. A reflection would be cheaper
arithmetic and wrong: it turns solids inside out, so every component mesh would arrive with its
faces reversed. Flipping a board is turning it over.

Three ways of finding it, tried in order, each only when the one before could not answer:

1. **By name, fitted.** Least squares through every shared designator, with the few parts a
   footprint models away from its anchor trimmed out. Exact on a normal export.
2. **By name, voted.** When too many parts are modeled elsewhere for trimming -- it stops at a
   quarter, and its yardstick is the median, which is no yardstick once the odd ones are not
   few -- each pair votes for the shift that would put it right, and the shift most pairs
   agree on wins. A vote cannot be dragged: a part 40 mm away votes for something nobody
   else does.
3. **By position.** When the names are no help at all (a model from a tool that calls its
   solids `R0402`, a placement file with its own numbering), every solid is tried against
   every placement and the same vote is taken. A constellation of a few dozen points fits
   itself in one way only, so this works more often than it sounds; but it is never reported
   as confident, because a regular array of identical parts fits itself in several.

The answers to 2 and 3 are offered, not asserted: a person confirms them, in the view, where
the hand alignment is.

Pure: no settings, no filesystem.

Source: `magpie/pcb/registration.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

__all__ = ["Registration", "register", "apply", "GOOD_MM", "DOUBTFUL_MM", "OUTLIER_RATIO", "TRIM_SHARE", "VOTE_MIN"]

#: A residual at or below this is the transform, full stop. KiCad exports land near zero; the
#: allowance is for a model somebody re-exported through another tool.
GOOD_MM = 0.5
#: Above this, a transform is not worth offering: something differs that a rigid motion cannot
#: explain, and quietly placing the model would put every height against the wrong board.
DOUBTFUL_MM = 2.0

#: Fewer shared components than this and the fit is not overdetermined enough to trust on its
#: own -- two points can always be joined by some rotation.
ENOUGH_POINTS = 3

#: A component this many times further off than the typical one, and further than GOOD_MM,
#: is not evidence about where the board sits: it is a part the model places somewhere the
#: placement file does not say. KiCad puts each solid at the footprint's anchor plus the
#: model offset set in that footprint, and the pick-and-place file uses the anchor alone --
#: so an audio jack drawn about its rear face lands 7.8 mm from its placement while every
#: passive on the board agrees to a micron. Fitted through, five such parts on one
#: real board dragged the other two hundred by a tenth of a millimetre and called the
#: whole board doubtful.
OUTLIER_RATIO = 5.0
#: But never more than this share of the shared components. When a quarter of the board
#: disagrees, the disagreement is the finding, not a handful of odd footprints.
TRIM_SHARE = 0.25


#: The vote needs this many pairs behind it. One more than a fit needs: three points that
#: agree are a fit, and four are a fit with a witness.
VOTE_MIN = 4
#: By name: a winning shift needs more than this share of the shared components behind it
#: to stand on its own, and at least VOTE_FLOOR of them to be offered at all.
VOTE_MAJORITY = 0.5
VOTE_FLOOR = 0.3
#: ...and no rival shift with more than this fraction of the winner's support. Ten parts
#: agreeing on one shift and ten on another is two boards' worth of opinion, not an answer.
VOTE_RIVAL = 0.6
#: By position: this share of the smaller set has to land on something.
POSITION_SHARE = 0.5
#: Every solid against every placement, eight ways: past this many pairs it is not tried.
POSITION_PAIRS_MAX = 250_000

_SQUARE = [(rot, flip) for flip in (False, True) for rot in (0.0, 90.0, 180.0, 270.0)]


@dataclass(frozen=True)
class Registration:
    """Where the model goes, how well it fits, and what did not line up."""

    ok: bool
    reason: str
    #: `{'rot_deg', 'flip', 'dx', 'dy'}` -- applied in that order: flip, then rotate, then move.
    transform: dict
    matched: list[str] = field(default_factory=list)
    unmatched_step: list[str] = field(default_factory=list)
    unmatched_placements: list[str] = field(default_factory=list)
    residual_mm: float = 0.0
    #: The worst few designators, as (ref, mm), for a person deciding whether to trust it.
    worst: list[tuple[str, float]] = field(default_factory=list)
    #: Components the model places somewhere the placement file does not, as (ref, mm) at
    #: the final transform. Left out of the fit and named, so nobody reads them as a misfit.
    elsewhere: list[tuple[str, float]] = field(default_factory=list)
    #: How much of the matched set agrees about which face it is on, once the flip is taken
    #: into account. Low agreement means the flip is probably wrong even if the points fit.
    side_agreement: float = 1.0
    low_confidence: bool = False
    #: How it was found: 'designators' (fitted by name), 'consensus' (voted by name) or
    #: 'position' (voted with the names ignored). '' when nothing was.
    method: str = ""
    #: By position only: which solid each placement landed on, {placement ref: model ref}.
    pairs: dict = field(default_factory=dict)

    @property
    def confident(self) -> bool:
        return self.ok and not self.low_confidence and self.residual_mm <= GOOD_MM


def apply(transform: dict, x: float, y: float) -> tuple[float, float]:
    """A point carried from the model's frame into the board's."""
    if transform.get("flip"):
        y = -y
    angle = math.radians(float(transform.get("rot_deg") or 0.0))
    cos, sin = math.cos(angle), math.sin(angle)
    return (
        x * cos - y * sin + float(transform.get("dx") or 0.0),
        x * sin + y * cos + float(transform.get("dy") or 0.0),
    )


def _fit(pairs, rot_deg: float, flip: bool) -> tuple[dict, float, list[tuple[str, float]]]:
    """
    The best shift for a given rotation, and how badly it still misses.

    With the rotation fixed the shift is not a search: the transform that minimizes the summed
    square error is the one carrying the centroid of one set onto the centroid of the other.
    """
    moved = [
        (ref, *apply({"rot_deg": rot_deg, "flip": flip, "dx": 0, "dy": 0}, sx, sy), tx, ty)
        for ref, sx, sy, tx, ty in pairs
    ]
    count = len(moved)
    dx = sum(tx - mx for _ref, mx, _my, tx, _ty in moved) / count
    dy = sum(ty - my for _ref, _mx, my, _tx, ty in moved) / count

    errors = [(ref, math.hypot(mx + dx - tx, my + dy - ty)) for ref, mx, my, tx, ty in moved]
    residual = math.sqrt(sum(error**2 for _ref, error in errors) / count)
    worst = sorted(errors, key=lambda pair: -pair[1])[:5]
    return ({"rot_deg": rot_deg, "flip": flip, "dx": dx, "dy": dy}, residual, worst)


def _free_angle(pairs, flip: bool) -> float | None:
    """
    The best rotation of any angle, not just the four right ones.

    KiCad exports are axis-aligned, so this is only consulted to catch the case where they are
    not -- a model re-exported through a tool that rotated it. Closed form: the angle is the
    argument of the summed cross and dot products about the two centroids.
    """
    moved = [
        (ref, *apply({"rot_deg": 0, "flip": flip, "dx": 0, "dy": 0}, sx, sy), tx, ty) for ref, sx, sy, tx, ty in pairs
    ]
    count = len(moved)
    mean_mx = sum(mx for _r, mx, _my, _tx, _ty in moved) / count
    mean_my = sum(my for _r, _mx, my, _tx, _ty in moved) / count
    mean_tx = sum(tx for _r, _mx, _my, tx, _ty in moved) / count
    mean_ty = sum(ty for _r, _mx, _my, _tx, ty in moved) / count

    cross = sum((mx - mean_mx) * (ty - mean_ty) - (my - mean_my) * (tx - mean_tx) for _r, mx, my, tx, ty in moved)
    dot = sum((mx - mean_mx) * (tx - mean_tx) + (my - mean_my) * (ty - mean_ty) for _r, mx, my, tx, ty in moved)
    if abs(cross) < 1e-12 and abs(dot) < 1e-12:
        return None  # every point sits on its own centroid
    return math.degrees(math.atan2(cross, dot))


def _errors(pairs, transform: dict) -> list[tuple[str, float]]:
    """How far each component lands from its placement under a transform, as (ref, mm)."""
    return [
        (ref, math.hypot(*(a - b for a, b in zip(apply(transform, sx, sy), (tx, ty), strict=False))))
        for ref, sx, sy, tx, ty in pairs
    ]


def _best(pairs) -> tuple[dict, float, list[tuple[str, float]]]:
    """The best of the eight square candidates and, when it is a real improvement, the free angle."""
    # The eight ways a board can be laid down: four quarter turns, each either face up.
    candidates = [_fit(pairs, rot, flip) for flip in (False, True) for rot in (0.0, 90.0, 180.0, 270.0)]

    # ...and, for a model that is not axis-aligned, the best angle of any size. Kept only if it
    # is a real improvement, so an export that IS square stays reported as square.
    for flip in (False, True):
        angle = _free_angle(pairs, flip)
        if (
            angle is not None
            and min(abs((angle - square) % 360.0) for square in (0.0, 90.0, 180.0, 270.0, 360.0)) > 1.0
        ):
            candidates.append(_fit(pairs, angle, flip))

    return min(candidates, key=lambda found: found[1])


def _trimmed(pairs):
    """
    The best fit with the components it should not be fitted through set aside.

    Fit, find the components sitting OUTLIER_RATIO times further off than the typical one
    (and further than GOOD_MM), refit without them, and look again -- a big outlier hides a
    smaller one behind the shift it causes. Stops when nothing stands out, when TRIM_SHARE of
    the components would be gone, or when fewer than ENOUGH_POINTS would remain.

    Returns (transform, residual, worst, elsewhere), with the residual and the worst over
    the components fitted through, and `elsewhere` measured against the final transform.
    """
    kept = list(pairs)
    aside: list[str] = []
    allowance = int(len(pairs) * TRIM_SHARE)
    transform, residual, worst = _best(kept)
    for _round in range(4):
        errors = _errors(kept, transform)
        ordered = sorted(error for _ref, error in errors)
        median = ordered[len(ordered) // 2]
        limit = max(GOOD_MM, OUTLIER_RATIO * median)
        out = sorted(((ref, error) for ref, error in errors if error > limit), key=lambda pair: -pair[1])
        room = min(allowance - len(aside), len(kept) - ENOUGH_POINTS)
        out = out[: max(room, 0)]
        if not out:
            break
        aside.extend(ref for ref, _error in out)
        gone = {ref for ref, _error in out}
        kept = [pair for pair in kept if pair[0] not in gone]
        transform, residual, worst = _best(kept)

    elsewhere = sorted(
        ((ref, error) for ref, error in _errors(pairs, transform) if ref in aside), key=lambda pair: -pair[1]
    )
    return transform, residual, worst, elsewhere


def _planar(kept) -> bool:
    """
    Whether these candidates' model points spread over an area rather than along a line.

    Points in a line say nothing about a flip -- turned over about that line they land on
    themselves -- so one row of a board's capacitors "agrees" with a wrong answer as readily
    as with the right one. Such a cluster is neither a winner nor a rival to one.
    """
    points = [(candidate[2], candidate[3]) for candidate in kept]
    if len(points) < ENOUGH_POINTS:
        return False
    ax, ay = points[0]
    bx, by = max(points, key=lambda point: math.hypot(point[0] - ax, point[1] - ay))
    length = math.hypot(bx - ax, by - ay)
    if length < 1.0:
        return False
    return max(abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / length for px, py in points) > 1.0


def _vote(candidates, tolerance: float = GOOD_MM):
    """
    The square orientation and shift that the most candidate pairs agree on.

    `candidates` are `(model key, placement key, sx, sy, tx, ty)`. For each of the eight ways
    a board can lie, every candidate names the shift that would put it right; shifts are
    binned, the fullest bin is tightened to the pairs within `tolerance` of its center, and
    each key is used once (by position, one solid could otherwise answer for two placements).

    Returns `(winner, rival)`: the winner as `(rot, flip, inliers)` with inliers in the
    candidates' own form, and the support of the best answer that is not the winner -- a
    second shift at the same orientation, or the best at another. `(None, 0)` for no votes.
    """
    cell = 2.0 * tolerance
    found = []
    for rot, flip in _SQUARE:
        turn = {"rot_deg": rot, "flip": flip, "dx": 0.0, "dy": 0.0}
        shifts = []
        for candidate in candidates:
            mx, my = apply(turn, candidate[2], candidate[3])
            shifts.append((candidate[4] - mx, candidate[5] - my, candidate))
        bins = Counter((round(dx / cell), round(dy / cell)) for dx, dy, _c in shifts)
        clusters = []
        for (bx, by), _count in bins.most_common(4):
            near = [shift for shift in shifts if abs(shift[0] / cell - bx) <= 1.0 and abs(shift[1] / cell - by) <= 1.0]
            home = [shift for shift in near if round(shift[0] / cell) == bx and round(shift[1] / cell) == by]
            cx = sum(shift[0] for shift in home) / len(home)
            cy = sum(shift[1] for shift in home) / len(home)
            for _again in range(2):
                inside = [shift for shift in near if math.hypot(shift[0] - cx, shift[1] - cy) <= tolerance]
                if not inside:
                    break
                cx = sum(shift[0] for shift in inside) / len(inside)
                cy = sum(shift[1] for shift in inside) / len(inside)
            inside.sort(key=lambda shift: math.hypot(shift[0] - cx, shift[1] - cy))
            used_model, used_placed, kept = set(), set(), []
            for _dx, _dy, candidate in inside:
                if candidate[0] in used_model or candidate[1] in used_placed:
                    continue
                used_model.add(candidate[0])
                used_placed.add(candidate[1])
                kept.append(candidate)
            clusters.append(((cx, cy), kept))
        clusters = [cluster for cluster in clusters if _planar(cluster[1])]
        clusters.sort(key=lambda cluster: -len(cluster[1]))
        if not clusters:
            continue
        (center, kept) = clusters[0]
        # A second cluster only rivals the first if it is a different shift, not the same
        # one seen from the neighbouring bin.
        second = max(
            (
                len(other)
                for where, other in clusters[1:]
                if math.hypot(where[0] - center[0], where[1] - center[1]) > 2 * tolerance
            ),
            default=0,
        )
        found.append((rot, flip, kept, second))
    if not found:
        return None, 0
    found.sort(key=lambda entry: -len(entry[2]))
    rot, flip, kept, second = found[0]
    rival = max([second] + [len(entry[2]) for entry in found[1:]])
    return (rot, flip, kept), rival


def _by_position(step_keyed: dict, placement_keyed: dict):
    """Every solid against every placement whose face it could be on, for `_vote`."""
    if len(step_keyed) * len(placement_keyed) > POSITION_PAIRS_MAX:
        return None, 0
    candidates = [
        (model_ref, placed_ref, sx, sy, tx, ty)
        for model_ref, (sx, sy, _s_side) in step_keyed.items()
        for placed_ref, (tx, ty, _t_side) in placement_keyed.items()
    ]
    winner, rival = _vote(candidates)
    if winner is None:
        return None, 0
    _rot, flip, kept = winner
    # The faces, where both files state them, have to agree with the flip -- checked after
    # the vote rather than used to thin it, because the flip is what is being voted on.
    stated = [
        (step_keyed[a][2], placement_keyed[b][2]) for a, b, *_rest in kept if step_keyed[a][2] and placement_keyed[b][2]
    ]
    if stated and sum(1 for a, b in stated if (a != b) == flip) < 0.5 * len(stated):
        return None, 0
    return winner, rival


def _not_a_board(placement_keyed: dict) -> str:
    """Why these placements cannot be a board's, or '' when they could be."""
    if len(placement_keyed) < ENOUGH_POINTS:
        return ""
    xs = [value[0] for value in placement_keyed.values()]
    ys = [value[1] for value in placement_keyed.values()]
    if max(xs) - min(xs) < 0.01 or max(ys) - min(ys) < 0.01:
        axis = "X" if max(xs) - min(xs) < 0.01 else "Y"
        return (
            f"The placement file gives all {len(placement_keyed)} components the same {axis}, "
            f"which is a line and not a board. It is probably not a placement file, or its "
            f"X and Y columns were not found. Upload the placement file again."
        )
    return ""


def _spread(pairs) -> float:
    """How far the matched points spread out. Near zero means they are effectively one point."""
    xs = [sx for _ref, sx, _sy, _tx, _ty in pairs]
    ys = [sy for _ref, _sx, sy, _tx, _ty in pairs]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def register(step_points: dict, placement_points: dict) -> Registration:
    """
    Solve for the transform carrying the model's frame onto the board's.

    Both arguments are `{reference: (x, y, side)}`; side may be `''` where the STEP does not
    say. Designators are matched case-insensitively, since one file may shout and the other
    may not.
    """
    step_keyed = {ref.strip().upper(): value for ref, value in step_points.items() if ref.strip()}
    placement_keyed = {ref.strip().upper(): value for ref, value in placement_points.items() if ref.strip()}
    shared = sorted(set(step_keyed) & set(placement_keyed))

    unmatched_step = sorted(set(step_keyed) - set(placement_keyed))
    unmatched_placements = sorted(set(placement_keyed) - set(step_keyed))

    common = {"unmatched_step": unmatched_step, "unmatched_placements": unmatched_placements}
    identity = {"rot_deg": 0.0, "flip": False, "dx": 0.0, "dy": 0.0}

    pairs = [
        (ref, step_keyed[ref][0], step_keyed[ref][1], placement_keyed[ref][0], placement_keyed[ref][1])
        for ref in shared
    ]

    def sides_agree(flip: bool, refs) -> float:
        # Only components whose side both files state can vote.
        stated = [
            (step_keyed[a][2], placement_keyed[b][2]) for a, b in refs if step_keyed[a][2] and placement_keyed[b][2]
        ]
        if not stated:
            return 1.0
        return sum(1 for a, b in stated if (a != b) == bool(flip)) / len(stated)

    fitted = None
    if shared:
        transform, residual, worst, elsewhere = _trimmed(pairs)
        fitted = (transform, residual, worst, elsewhere)
        if residual <= GOOD_MM:
            return _report(
                transform,
                residual,
                worst,
                elsewhere,
                shared,
                pairs,
                sides_agree(transform["flip"], [(ref, ref) for ref in shared]),
                "designators",
                common,
            )

    # Asked before anything cleverer, because every answer below would be about the wrong
    # question: no way of turning a model lines it up with placements that do not describe a
    # board. Not asked of a fit that already works -- three capacitors in a row are a line too.
    broken = _not_a_board(placement_keyed)
    if broken:
        return Registration(ok=False, reason=broken, transform=identity, matched=shared, **common)

    if shared:
        # 2. By name, voted.
        winner, rival = _vote([(ref, ref, sx, sy, tx, ty) for ref, sx, sy, tx, ty in pairs])
        if winner is not None:
            rot, flip, kept = winner
            share = len(kept) / len(pairs)
            if len(kept) >= VOTE_MIN and share >= VOTE_FLOOR and rival <= VOTE_RIVAL * len(kept):
                agreeing = [candidate[1:] for candidate in kept]
                transform, residual, worst = _fit(agreeing, rot, flip)
                inside = {candidate[0] for candidate in kept}
                elsewhere = sorted(
                    ((ref, error) for ref, error in _errors(pairs, transform) if ref not in inside),
                    key=lambda pair: -pair[1],
                )
                note = (
                    f"{len(kept)} of {len(pairs)} components agree on where the board is; "
                    f"the other {len(pairs) - len(kept)} are modeled away from their "
                    f"footprints. Check it against the picture"
                )
                return Registration(
                    ok=True,
                    reason=note,
                    transform=transform,
                    matched=shared,
                    residual_mm=residual,
                    worst=worst,
                    elsewhere=elsewhere,
                    side_agreement=sides_agree(flip, [(ref, ref) for ref in inside]),
                    # Never silently: most of a board agreeing is an answer, but one a
                    # person should see before heights are measured against it.
                    low_confidence=True,
                    method="consensus",
                    **common,
                )

    # 3. By position, the names set aside -- only once they have had their chance. A fit by
    # name that is merely doubtful is still about the right components.
    winner, rival = (
        _by_position(step_keyed, placement_keyed) if fitted is None or fitted[1] > DOUBTFUL_MM else (None, 0)
    )
    if winner is not None:
        rot, flip, kept = winner
        smaller = min(len(step_keyed), len(placement_keyed))
        if len(kept) >= VOTE_MIN and len(kept) >= POSITION_SHARE * smaller and rival <= VOTE_RIVAL * len(kept):
            transform, residual, worst = _fit(
                [(placed, sx, sy, tx, ty) for _model, placed, sx, sy, tx, ty in kept], rot, flip
            )
            named = {placed: model for model, placed, *_rest in kept}
            return Registration(
                ok=True,
                reason=(
                    f"Lined up by where the components stand, because their names do not "
                    f"match: {len(kept)} of the model's {len(step_keyed)} solids sit on a "
                    f"placement. Check it against the picture"
                ),
                transform=transform,
                matched=sorted(named),
                residual_mm=residual,
                worst=worst,
                side_agreement=sides_agree(flip, [(model, placed) for placed, model in named.items()]),
                low_confidence=True,
                method="position",
                pairs=named,
                unmatched_step=sorted(set(step_keyed) - set(named.values())),
                unmatched_placements=sorted(set(placement_keyed) - set(named)),
            )

    if fitted is None:
        return Registration(
            ok=False,
            reason="Nothing in the 3D model shares a designator with the placements, so there "
            "is nothing to line it up by. Position it by hand.",
            transform=identity,
            **common,
        )
    transform, residual, worst, elsewhere = fitted
    return _report(
        transform,
        residual,
        worst,
        elsewhere,
        shared,
        pairs,
        sides_agree(transform["flip"], [(ref, ref) for ref in shared]),
        "designators",
        common,
    )


def _report(transform, residual, worst, elsewhere, shared, pairs, side_agreement, method, common) -> Registration:
    """What a fit by name amounts to: refused, offered with its doubts, or simply right."""
    low_confidence = len(shared) < ENOUGH_POINTS or _spread(pairs) < 1e-6

    if residual > DOUBTFUL_MM:
        return Registration(
            ok=False,
            reason=f"The model and the placements disagree by {residual:.2f} mm even at the "
            f"best fit, which no amount of turning the board explains. Position it by "
            f"hand.",
            transform=transform,
            matched=shared,
            residual_mm=residual,
            worst=worst,
            elsewhere=elsewhere,
            side_agreement=side_agreement,
            low_confidence=low_confidence,
            **common,
        )

    reasons = []
    if residual > GOOD_MM:
        reasons.append(f"components sit {residual:.2f} mm from where the placements put them")
    if low_confidence:
        reasons.append(
            f"only {len(shared)} component{'s' if len(shared) != 1 else ''} to go on"
            if len(shared) < ENOUGH_POINTS
            else "the matched components are in a line"
        )
    if side_agreement < 0.9:
        reasons.append(
            f"{round((1 - side_agreement) * 100)}% of them are on the other face from where the placements say"
        )

    return Registration(
        ok=True,
        reason="; ".join(reasons).capitalize() if reasons else "",
        transform=transform,
        matched=shared,
        residual_mm=residual,
        worst=worst,
        elsewhere=elsewhere,
        side_agreement=side_agreement,
        low_confidence=low_confidence,
        method=method,
        **common,
    )
