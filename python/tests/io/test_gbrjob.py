"""
The board's size and project name from the .gbrjob (`boarddd.io.gbrjob`).

Ported from magpie `tests/pcb/test_formats.py` (the job file) at 3a0374d3.
"""

from __future__ import annotations

import pytest

from boarddd.io import gbrjob as jobfile

from conftest import RB_FAB

RB_JOB = RB_FAB / "RoyalBlue54L-Feather-job.gbrjob"


# ─── The job file ────────────────────────────────────────────────────────────


def test_the_board_size_comes_from_the_job_file():
    """
    The only trustworthy statement of how big the board is. Measuring the gerbers gives the
    drawing sheet on an export that plots a border on every layer; the job file gives the
    board -- 58.42 x 22.86 mm here, stated with the 0.1 mm edge stroke included.
    """
    assert jobfile.board_size(RB_JOB.read_text()) == (58.52, 22.96)


def test_the_project_name_comes_from_the_job_file():
    assert jobfile.project_name(RB_JOB.read_text()) == "RoyalBlue54L-Feather"
    assert not jobfile.looks_like_panel("RoyalBlue54L-Feather")


@pytest.mark.parametrize("name", ["board-panel", "Board_Panel"])
def test_a_kikit_panel_is_told_by_its_name(name):
    assert jobfile.looks_like_panel(name)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "{}",
        '{"GeneralSpecs": {}}',
        '{"GeneralSpecs": {"Size": {"X": 0, "Y": 5}}}',
        "[1, 2, 3]",
        '{"GeneralSpecs": "x"}',
        '{"GeneralSpecs": {"Size": "x"}}',
        '{"GeneralSpecs": {"Size": {"X": "NaN", "Y": 5}}}',
        '{"GeneralSpecs": {"Size": {"X": "Infinity", "Y": 5}}}',
        '{"GeneralSpecs": {"Size": {"X": 1e400, "Y": 5}}}',
        '{"GeneralSpecs": {"Size": {"X": 1' + "0" * 400 + ', "Y": 5}}}',
        '{"GeneralSpecs": {"Size": {"X": 1e7, "Y": 5}}}',
    ],
    ids=lambda text: text[:60],
)
def test_a_job_file_that_does_not_say_answers_none(text):
    assert jobfile.board_size(text) is None


@pytest.mark.parametrize(
    "text", ['{"GeneralSpecs": "x"}', '{"GeneralSpecs": [1]}', '{"GeneralSpecs": {"ProjectId": "x"}}']
)
def test_a_job_file_of_the_wrong_shape_names_no_project(text):
    """Read for every listing of a board's files: an AttributeError here was a server error there."""
    assert jobfile.project_name(text) == ""
