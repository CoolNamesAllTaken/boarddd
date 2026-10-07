"""Fingerprint comparison and rounding (no OpenCascade geometry needed, but the module is the extra's)."""

from __future__ import annotations

from dataclasses import replace

from stepkit import require_occ

require_occ()

from boarddd.step.fingerprint import Fingerprint, _grid, _sig, same  # noqa: E402

BASE = Fingerprint(
    shape_key="a",
    frame_key="b",
    brep_key="c",
    volume=0.175,
    area=2.05,
    principal_moments=(0.005, 0.017, 0.019),
    face_types={"plane": 18, "cylinder": 8},
    bbox_min=(-0.5, -0.25, 0.0),
    bbox_max=(0.5, 0.25, 0.35),
)


def test_same_tolerates_re_export_noise():
    noisy = replace(BASE, volume=0.175 * (1 + 1e-6), area=2.05 * (1 - 1e-6), bbox_max=(0.5 + 1e-7, 0.25, 0.35))
    assert same(BASE, noisy)


def test_same_rejects_real_differences():
    assert not same(BASE, replace(BASE, face_types={"plane": 19, "cylinder": 8}))
    assert not same(BASE, replace(BASE, volume=0.18))
    assert not same(BASE, replace(BASE, principal_moments=(0.005, 0.017, 0.021)))
    moved = replace(BASE, bbox_min=(0.0, -0.25, 0.0), bbox_max=(1.0, 0.25, 0.35))
    assert not same(BASE, moved) and same(BASE, moved, frame=False)


def test_grid_keeps_cad_numbers_off_cell_edges():
    # 1.2525 is a multiple of 0.0005 and sat exactly on an unshifted 0.001 cell edge.
    assert _grid(1.2525 + 1e-12, 0.001) == _grid(1.2525 - 1e-12, 0.001)
    assert _grid(-0.6025 + 1e-12, 0.001) == _grid(-0.6025 - 1e-12, 0.001)


def test_significant_figures():
    assert _sig(0.0123456) == 0.01235 and _sig(12345.6) == 12350.0 and _sig(0.0) == 0.0
