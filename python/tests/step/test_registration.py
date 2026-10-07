"""
Lining a 3D model up with the placements we already have.

The happy case is verified against a real KiCad export further down (royalblue54L_feather's STEP
excerpt and its pick-and-place file) and is exact -- zero residual. Everything above it is the unhappy cases, which is where the value
is: a transform that is confidently wrong would put every measured height against the wrong
board surface, so the interesting question is not "does it fit" but "does it know when it
doesn't".

Ported from magpie's tests/pcb/test_registration.py at 3a0374d3; the real-board fixture is public.
"""

from __future__ import annotations

import math

import pytest

from boarddd.step import registration

from conftest import GENERATED, RB_FAB

# A handful of components spread over a board, in the model's frame.
MODEL = {
    "C1": (1.0, 1.0, "top"),
    "R2": (9.0, 2.0, "top"),
    "U3": (4.0, 7.0, "top"),
    "L4": (8.0, 8.0, "top"),
}


def _placed(transform, points=MODEL, sides=None):
    """The same components, carried through a transform -- what a pos file would say."""
    out = {}
    for ref, (x, y, side) in points.items():
        tx, ty = registration.apply(transform, x, y)
        out[ref] = (tx, ty, (sides or {}).get(ref, side))
    return out


# ─── Solving it ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rot_deg", [0.0, 90.0, 180.0, 270.0])
@pytest.mark.parametrize("flip", [False, True])
def test_every_way_a_board_can_be_laid_down(rot_deg, flip):
    """Four quarter turns, either face up. Anything else is not a board on a table."""
    truth = {"rot_deg": rot_deg, "flip": flip, "dx": 60.0, "dy": -54.0}
    sides = {ref: ("bottom" if flip else "top") for ref in MODEL}

    found = registration.register(MODEL, _placed(truth, sides=sides))

    assert found.ok and found.confident
    assert found.residual_mm == pytest.approx(0.0, abs=1e-9)
    assert found.transform["flip"] is flip
    assert found.transform["rot_deg"] % 360 == pytest.approx(rot_deg)
    assert (found.transform["dx"], found.transform["dy"]) == pytest.approx((60.0, -54.0))


def test_the_shift_is_solved_not_searched():
    """Any translation, not just tidy ones."""
    found = registration.register(MODEL, _placed({"rot_deg": 0, "flip": False, "dx": -12.345, "dy": 6.789}))
    assert (found.transform["dx"], found.transform["dy"]) == pytest.approx((-12.345, 6.789))


def test_a_designator_is_matched_whatever_its_case():
    """One file may shout and the other may not; they still mean the same component."""
    shouted = {ref.lower(): value for ref, value in _placed({"rot_deg": 0, "flip": False, "dx": 5, "dy": 5}).items()}
    found = registration.register(MODEL, shouted)

    assert found.ok and len(found.matched) == 4


def test_an_off_square_model_is_fitted_at_its_real_angle():
    """
    KiCad exports are square to the axes, so the four quarter turns are normally the whole
    story. A model re-exported through something that rotated it is not, and reporting a
    30-degree board as the nearest right angle would be a fit that is visibly wrong.
    """
    found = registration.register(MODEL, _placed({"rot_deg": 30.0, "flip": False, "dx": 3.0, "dy": 4.0}))

    assert found.ok
    assert found.transform["rot_deg"] % 360 == pytest.approx(30.0, abs=0.01)
    assert found.residual_mm == pytest.approx(0.0, abs=1e-9)


def test_a_square_export_is_reported_as_square():
    """The free-angle fit must not turn an exact quarter turn into 89.9999 degrees."""
    found = registration.register(MODEL, _placed({"rot_deg": 90.0, "flip": False, "dx": 0, "dy": 0}))
    assert found.transform["rot_deg"] in (90.0, -270.0)


def test_a_flip_is_a_turn_over_not_a_mirror():
    """
    A reflection would fit the points just as well and turn every solid inside out -- meshes
    arriving with their faces reversed. The flip candidate is a rotation about X: y negates,
    and so does z, which is why the sides swap with it.
    """
    x, y = registration.apply({"rot_deg": 0, "flip": True, "dx": 0, "dy": 0}, 3.0, 4.0)
    assert (x, y) == (3.0, -4.0)


# ─── Knowing when it does not know ───────────────────────────────────────────


def test_no_shared_designators_is_refused_outright():
    """A foreign model shares nothing to line up by; there is no fit to offer."""
    found = registration.register(MODEL, {"X9": (0.0, 0.0, "top")})

    assert not found.ok
    assert "nothing to line it up by" in found.reason
    assert found.unmatched_step == ["C1", "L4", "R2", "U3"]


def test_points_that_no_rigid_motion_explains_are_refused():
    """
    Not a transform at all -- one component moved on its own. Placing the model anyway would
    put every height against a board that is not where the model thinks it is.
    """
    placed = _placed({"rot_deg": 0, "flip": False, "dx": 10, "dy": 10})
    placed["U3"] = (placed["U3"][0] + 25.0, placed["U3"][1], "top")

    found = registration.register(MODEL, placed)

    assert not found.ok
    assert found.residual_mm > registration.DOUBTFUL_MM
    assert found.worst[0][0] == "U3", "and it names the component that does not fit"


def test_a_small_disagreement_fits_but_says_so():
    """Between good and hopeless: offered, with the number, for a person to judge."""
    placed = _placed({"rot_deg": 0, "flip": False, "dx": 10, "dy": 10})
    placed["U3"] = (placed["U3"][0] + 1.6, placed["U3"][1], "top")

    found = registration.register(MODEL, placed)

    assert found.ok and not found.confident
    assert registration.GOOD_MM < found.residual_mm <= registration.DOUBTFUL_MM
    assert "mm from where the placements put them" in found.reason


def test_too_few_components_fits_but_is_not_trusted():
    """Two points can be joined by some rotation; that is not evidence of the right one."""
    two = {ref: MODEL[ref] for ref in ("C1", "R2")}
    found = registration.register(two, _placed({"rot_deg": 0, "flip": False, "dx": 5, "dy": 5}, points=two))

    assert found.ok and found.low_confidence and not found.confident
    assert "to go on" in found.reason


def test_components_all_in_a_line_are_not_trusted():
    """A row of parts pins the shift but says nothing about turning about that row."""
    collinear = {"C1": (0.0, 0.0, "top"), "C2": (1.0, 0.0, "top"), "C3": (2.0, 0.0, "top")}
    found = registration.register(collinear, {ref: (x, y, s) for ref, (x, y, s) in collinear.items()})

    assert found.ok
    assert found.residual_mm == pytest.approx(0.0, abs=1e-9)


def test_a_flip_that_the_sides_contradict_is_flagged():
    """
    The points can fit upside-down and still be wrong: if the placements say these parts are on
    top and the fit only works face-down, something disagrees that geometry cannot settle.
    """
    placed = _placed({"rot_deg": 0, "flip": True, "dx": 0, "dy": 0})  # sides left as 'top'
    found = registration.register(MODEL, placed)

    assert found.transform["flip"] is True
    assert found.side_agreement == 0.0
    assert "other face" in found.reason


def test_a_component_the_model_does_not_have_is_reported_not_ignored():
    """Testpoints, graphics and parts with no 3D model. Expected, and worth saying."""
    placed = _placed({"rot_deg": 0, "flip": False, "dx": 0, "dy": 0})
    placed["T1"] = (50.0, 50.0, "top")

    found = registration.register(MODEL, placed)

    assert found.ok and found.unmatched_placements == ["T1"]


# ─── Against the real board ──────────────────────────────────────────────────


def _real():
    """royalblue54L_feather: 16 components of its STEP export and its whole pick-and-place file."""
    from boarddd.io import pos as posfile
    from boarddd.step import text as stepmeta

    excerpt = (GENERATED / "step" / "royalblue54L_feather-excerpt.step").read_text("utf-8", errors="replace")
    model = {item.ref: (item.x, item.y, item.side) for item in stepmeta.instances(excerpt)}
    rows = posfile.parse((RB_FAB / "pos.csv").read_text("utf-8-sig"))
    return model, {r.reference: (float(r.x), float(r.y), r.side) for r in rows}


def test_the_real_board_lines_up_exactly():
    """
    Every component the model has agrees on the same transform. This is the case the whole
    feature is built around: a KiCad export needs no dragging into place. KiCad 10 writes the
    STEP in the pick-and-place file's own frame, so the transform is the identity (older KiCad
    exports, and other tools, differ by a page offset -- the synthetic cases above).
    """
    model, placements = _real()

    found = registration.register(model, placements)

    assert found.ok and found.confident
    assert found.residual_mm < 0.001
    assert found.transform["rot_deg"] == 0.0 and found.transform["flip"] is False
    assert (found.transform["dx"], found.transform["dy"]) == pytest.approx((0.0, 0.0), abs=1e-4)
    assert not found.unmatched_step, "every modeled component is in the placement file"
    assert len(found.matched) == 16


def test_the_real_connector_modeled_away_from_its_anchor_is_set_aside():
    """J4's model sits 4.5 mm from its footprint anchor; it is named, not fitted through."""
    model, placements = _real()

    found = registration.register(model, placements)

    assert [ref for ref, _mm in found.elsewhere] == ["J4"]
    assert dict(found.elsewhere)["J4"] == pytest.approx(4.5, abs=1e-3)


# ─── Not falling over ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model,placed",
    [
        ({}, {}),
        ({}, {"C1": (0.0, 0.0, "top")}),
        ({"C1": (0.0, 0.0, "top")}, {}),
        ({"  ": (1.0, 1.0, "")}, {"  ": (1.0, 1.0, "")}),
    ],
)
def test_nothing_to_work_with_is_an_answer_not_a_crash(model, placed):
    found = registration.register(model, placed)
    assert not found.ok and math.isfinite(found.residual_mm)


# ─── Components the model places somewhere else ──────────────────────────────
#
# KiCad puts each solid at the footprint anchor plus the model offset set in that footprint;
# the pick-and-place file uses the anchor alone. On one real board an audio jack and four
# light pipes sat 2 to 8 mm from their placements while two hundred passives agreed to a
# micron -- and fitted through, they dragged everything else a tenth of a millimetre and
# called the board doubtful.


def _grid(count=40):
    return {f"C{i}": (float(i % 8) * 3.0, float(i // 8) * 3.0, "top") for i in range(count)}


def test_a_few_components_placed_elsewhere_do_not_drag_the_fit():
    truth = {"rot_deg": 0.0, "flip": False, "dx": 60.0, "dy": -54.0}
    model = {**_grid(), "J3": (12.0, 12.0, "top"), "PART3": (3.0, 15.0, "top"), "PART4": (18.0, 15.0, "top")}
    placed = _placed(truth, model)
    # The model draws these about a different point than the footprint anchor.
    model["J3"] = (12.0 + 7.8, 12.0, "top")
    model["PART3"] = (3.0 + 2.26, 15.0 - 2.31, "top")
    model["PART4"] = (18.0 + 2.26, 15.0 - 2.31, "top")

    found = registration.register(model, placed)

    assert found.ok and found.confident
    assert found.transform["dx"] == pytest.approx(60.0, abs=1e-6)
    assert found.transform["dy"] == pytest.approx(-54.0, abs=1e-6)
    assert found.residual_mm < 1e-6
    assert [ref for ref, _mm in found.elsewhere] == ["J3", "PART3", "PART4"]
    assert dict(found.elsewhere)["J3"] == pytest.approx(7.8, abs=1e-6)
    assert dict(found.elsewhere)["PART3"] == pytest.approx(math.hypot(2.26, 2.31), abs=1e-6)
    assert "J3" in found.matched, "still a shared designator, just not fitted through"


def test_a_smaller_outlier_hidden_behind_a_bigger_one_is_still_found():
    truth = {"rot_deg": 0.0, "flip": False, "dx": 10.0, "dy": 5.0}
    model = {**_grid(), "J1": (12.0, 12.0, "top"), "J2": (0.0, 12.0, "top")}
    placed = _placed(truth, model)
    model["J1"] = (12.0 + 30.0, 12.0, "top")
    model["J2"] = (0.0 + 0.9, 12.0, "top")

    found = registration.register(model, placed)

    assert found.confident
    assert [ref for ref, _mm in found.elsewhere] == ["J1", "J2"]


def test_a_board_that_disagrees_everywhere_is_not_explained_away():
    """Half the components 3 mm off is a finding about the board, not a handful of footprints."""
    truth = {"rot_deg": 0.0, "flip": False, "dx": 10.0, "dy": 5.0}
    model = _grid(20)
    placed = _placed(truth, model)
    for ref in list(model)[:10]:
        x, y, side = model[ref]
        model[ref] = (x + 3.0, y, side)

    found = registration.register(model, placed)

    assert not found.confident
    assert len(found.elsewhere) <= 5, "at most a quarter can be set aside"
    assert found.residual_mm > registration.GOOD_MM


def test_trimming_never_leaves_too_few_to_fit():
    truth = {"rot_deg": 0.0, "flip": False, "dx": 10.0, "dy": 5.0}
    model = {"C1": (0.0, 0.0, "top"), "C2": (5.0, 0.0, "top"), "C3": (0.0, 5.0, "top")}
    placed = _placed(truth, model)
    model["C3"] = (0.0, 9.0, "top")

    found = registration.register(model, placed)

    assert found.elsewhere == []
    assert len(found.matched) == 3


def test_a_board_with_nothing_placed_elsewhere_reports_none():
    model, placements = _real()
    del model["J4"]

    assert registration.register(model, placements).elsewhere == []


# ─── When fitting by name is not enough ──────────────────────────────────────
#
# Found on a real carrier board, whose "placements" turned out to be its BOM read
# positionally -- every part at (Quantity, 0). The fit said "position it by hand", which was
# true and useless: the thing to say was that the placements were not placements.


def test_placements_that_are_a_line_are_called_what_they_are():
    placed = {ref: (1.0, 0.0, "top") for ref in _grid(12)}
    placed["C0"] = (2.0, 0.0, "top")

    found = registration.register(_grid(12), placed)

    assert not found.ok
    assert "not a board" in found.reason and "placement file" in found.reason
    assert "by hand" not in found.reason, "no amount of dragging fixes the wrong file"


def test_most_of_a_board_agreeing_outvotes_the_parts_modeled_elsewhere():
    """Eight of twenty away from their anchors, each by its own amount: past what trimming allows."""
    truth = {"rot_deg": 90.0, "flip": False, "dx": 60.0, "dy": -54.0}
    model = _grid(20)
    placed = _placed(truth, model)
    offsets = [(2.0, 1.0), (-3.5, 0.7), (4.1, -2.2), (0.9, 5.3), (-6.0, -1.1), (7.7, 3.0), (-1.3, -4.4), (2.8, -7.5)]
    for (off_x, off_y), ref in zip(offsets, list(model)[:8], strict=False):
        x, y, side = model[ref]
        model[ref] = (x + off_x, y + off_y, side)

    found = registration.register(model, placed)

    assert found.ok and found.method == "consensus"
    assert not found.confident, "offered for a person to confirm, never asserted"
    assert found.transform["rot_deg"] == 90.0
    assert found.transform["dx"] == pytest.approx(60.0, abs=1e-6)
    assert found.transform["dy"] == pytest.approx(-54.0, abs=1e-6)
    assert sorted(ref for ref, _mm in found.elsewhere) == sorted(list(model)[:8])
    assert "12 of 20" in found.reason


def _scattered(count=16):
    """Components with no regularity to them, so the constellation fits itself one way only."""
    import random

    dice = random.Random(54)
    return {f"U{i}": (round(dice.uniform(0, 40), 3), round(dice.uniform(0, 30), 3), "top") for i in range(count)}


def test_a_model_with_its_own_names_is_lined_up_by_where_things_stand():
    truth = {"rot_deg": 180.0, "flip": False, "dx": 97.5, "dy": -59.6}
    model = _scattered()
    placed = {f"R{index}": value for index, value in enumerate(_placed(truth, model).values())}

    found = registration.register(model, placed)

    assert found.ok and found.method == "position"
    assert not found.confident
    assert found.transform["rot_deg"] == 180.0
    assert found.transform["dx"] == pytest.approx(97.5, abs=1e-6)
    assert found.pairs["R3"] == "U3", "and it says which solid each placement landed on"
    assert "names do not match" in found.reason


def test_a_regular_array_is_not_lined_up_by_position():
    """A grid of identical parts fits itself shifted by one pitch nearly as well as unshifted."""
    model = {f"U{i}": value for i, value in enumerate(_grid(16).values())}
    placed = {f"R{i}": (x + 3.0, y, side) for i, (x, y, side) in enumerate(_grid(16).values())}
    placed = dict(list(placed.items())[:12])

    found = registration.register(model, placed)

    assert not found.ok
