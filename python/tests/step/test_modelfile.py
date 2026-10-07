"""
Whether an uploaded file is the 3D model its name claims.

Written against what the formats actually say about themselves rather than against a fixture:
these are four small headers, and a test that only knows one real file per format cannot tell
a check that reads the header from a check that reads the first sixteen bytes and hopes.

The case that started it is `test_a_text_file_named_glb_is_refused` -- a placement model used
to be accepted on its suffix alone, so a text file called `part.glb` was stored, attached to
the placement, and surfaced hours later as a console error in somebody else's browser.

Ported from magpie's tests/step/test_modelfile.py at 3a0374d3.
"""

from __future__ import annotations

import json
import struct

import pytest

from boarddd.step import modelfile


def glb(version: int = 2, *, length: int | None = None, payload: bytes = b"") -> bytes:
    """A binary glTF header, honest about its length unless told otherwise."""
    body = b"glTF" + struct.pack("<II", version, 0) + payload
    stated = len(body) if length is None else length
    return b"glTF" + struct.pack("<II", version, stated) + payload


def stl_binary(triangles: int, *, pad: int = 0) -> bytes:
    return b"\0" * 80 + struct.pack("<I", triangles) + b"\0" * (triangles * 50 + pad)


# ─── The fault this was written for ──────────────────────────────────────────


def test_a_text_file_named_glb_is_refused():
    """
    The suffix is the one part of a file the uploader chose, so it is the one part that cannot
    be trusted. This exact upload used to succeed.
    """
    complaint = modelfile.problem("nonsense.glb", b"this is definitely not a 3D model")

    assert complaint
    assert "glTF" in complaint


def test_a_real_binary_gltf_is_accepted():
    assert modelfile.problem("part.glb", glb()) is None


# ─── Each format knows itself ────────────────────────────────────────────────


def test_a_truncated_glb_is_told_apart_from_a_file_that_was_never_a_model():
    """
    Both are refused and they are not the same problem: one is a bad upload worth retrying,
    the other is the wrong file. The message has to say which.
    """
    cut = modelfile.problem("part.glb", glb(length=999_999))

    assert cut and "truncated" in cut
    assert "truncated" not in (modelfile.problem("part.glb", b"\x89PNG\r\n\x1a\n" + b"x" * 40) or "")


def test_the_other_gltf_version_is_named_rather_than_rejected_vaguely():
    complaint = modelfile.problem("part.glb", glb(version=1))

    assert complaint and "version 1" in complaint


@pytest.mark.parametrize(
    "content, ok",
    [
        (json.dumps({"asset": {"version": "2.0"}}).encode(), True),
        (json.dumps({"asset": {}}).encode(), False),
        (json.dumps({"meshes": []}).encode(), False),
        (b"<!doctype html><html>", False),
        (b"\xff\xfe\x00not utf-8 at all", False),
    ],
)
def test_a_json_gltf_is_read_as_json(content, ok):
    assert (modelfile.problem("part.gltf", content) is None) is ok


@pytest.mark.parametrize(
    "content, ok",
    [
        (b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", True),
        (b"  ISO-10303-21;\nHEADER;\nEND-ISO-10303-21;", True),
        (b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n", False),
        (b"solid not a step\nendsolid", False),
    ],
)
def test_a_step_announces_itself_at_both_ends(content, ok):
    """A STEP cut off partway through still starts correctly, so the end is checked too."""
    assert (modelfile.problem("part.step", content) is None) is ok
    assert (modelfile.problem("part.stp", content) is None) is ok


def test_a_binary_stl_is_decided_by_arithmetic_rather_than_by_sniffing():
    """
    An 80-byte STL header can say anything at all, `solid` included -- which is the classic way
    a binary STL is mistaken for an ASCII one. The triangle count and the length agree or it is
    not an STL.
    """
    assert modelfile.problem("part.stl", stl_binary(12)) is None
    assert modelfile.problem("part.stl", stl_binary(12, pad=3)) is not None

    lying = b"solid" + b"\0" * 75 + struct.pack("<I", 2) + b"\0" * 100
    assert modelfile.problem("part.stl", lying) is None, "a binary STL whose header says solid"


def test_an_ascii_stl_is_accepted_and_its_truncation_is_caught():
    body = b"solid part\nfacet normal 0 0 1\nouter loop\nendloop\nendfacet\n"
    assert modelfile.problem("part.stl", body + b"endsolid part\n") is None
    assert modelfile.problem("part.stl", body) is not None


# ─── The edges ───────────────────────────────────────────────────────────────


def test_an_empty_file_is_refused_before_any_format_is_consulted():
    for name in ("a.glb", "a.gltf", "a.step", "a.stl"):
        assert modelfile.problem(name, b"") == "That file is empty."


def test_a_suffix_nothing_reads_is_named_along_with_the_ones_that_are():
    complaint = modelfile.problem("drawing.dxf", b"anything")

    assert complaint and ".dxf" in complaint and ".glb" in complaint


def test_a_file_with_no_suffix_at_all_does_not_crash():
    assert modelfile.problem("README", b"x")
    assert modelfile.problem("", b"x")
