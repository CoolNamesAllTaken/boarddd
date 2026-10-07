"""split.Model fills in Measurements.seat, so own_height is the part's own height."""

from __future__ import annotations

import pytest
from stepkit import require_occ

require_occ()

from stepkit import FIXTURES  # noqa: E402

from boarddd.step.split import split  # noqa: E402


def test_seat_and_own_height_on_a_board():
    assembly = split(FIXTURES / "tiny.step", pos=FIXTURES / "tiny-pos.csv")
    r1 = assembly.components["R1"].measurements
    assert r1.seat == pytest.approx(assembly.seat_z) == pytest.approx(0.085)
    assert r1.height == pytest.approx(0.435, abs=1e-3)  # above the substrate
    assert r1.own_height == pytest.approx(0.35, abs=1e-3)  # the 0402's own 0.35 mm
    assert r1.to_dict()["own_height"] == pytest.approx(0.35, abs=1e-3)
    for component in assembly.components.values():
        assert component.measurements.own_height == pytest.approx(component.model.model_height, abs=1e-6)


def test_a_model_file_has_no_seat():
    assembly = split(FIXTURES / "tiny.step", pos=FIXTURES / "tiny-pos.csv")
    (again,) = split(assembly.components["R1"].step_bytes()).components.values()
    assert again.measurements.seat == 0.0
    assert again.measurements.own_height == pytest.approx(0.35, abs=1e-3)
