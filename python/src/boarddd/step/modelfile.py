"""
Whether an uploaded file is actually the 3D model its name claims.

A placement can be given a model of its own, for the part nobody gave a body in CAD. What
arrives is whatever was on somebody's disk, and until this existed the only check was the
suffix -- so a text file called `part.glb` was stored, attached to the placement, and became a
console error in the browser hours later, with nothing on the page to say which of the four
things that could go wrong had.

Checked by content, because the suffix is the one part of a file the uploader chose. Enough of
each format is read to know it is that format and not to validate it: a glTF with a broken
accessor is a real glTF and belongs to whoever exported it, but a JPEG named `.glb` is not a
model and there is no point storing it.

Pure: bytes in, a complaint or None out. No filesystem, no settings.

Source: `magpie/step/modelfile.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

__all__ = ["SUFFIXES", "problem"]

#: What may be uploaded. `.step`/`.stp` and `.stl` are accepted although the browser cannot
#: draw them -- they are what a mechanical engineer actually has, and tessellation happens
#: elsewhere. The viewer says so per row rather than refusing the file.
SUFFIXES = frozenset({".glb", ".gltf", ".step", ".stp", ".stl"})

#: `glTF` little-endian, the first four bytes of every binary glTF.
_GLB_MAGIC = b"glTF"
#: Header of a binary glTF: magic, version, total length. Nothing shorter can be one.
_GLB_HEADER = 12
#: A binary STL is an 80-byte header, a uint32 triangle count, then 50 bytes per triangle.
_STL_HEADER = 84
_STL_TRIANGLE = 50


def problem(name: str, data: bytes) -> str | None:
    """
    What is wrong with this file as a 3D model, or None if nothing is.

    The message is shown to whoever uploaded it, so it says what was expected rather than what
    the parser did: "not a binary glTF" is something a person can act on, and
    "struct.error: unpack requires a buffer of 4 bytes" is not.
    """
    suffix = Path(name or "").suffix.lower()
    if suffix not in SUFFIXES:
        listed = ", ".join(sorted(SUFFIXES))
        return f"{suffix or 'That'} is not a model this can read ({listed})."
    if not data:
        return "That file is empty."

    checks = {".glb": _glb, ".gltf": _gltf, ".step": _step, ".stp": _step, ".stl": _stl}
    return checks[suffix](data)


def _glb(data: bytes) -> str | None:
    """A binary glTF: `glTF`, a version, and a length that agrees with the file."""
    if len(data) < _GLB_HEADER or data[:4] != _GLB_MAGIC:
        return "That is not a binary glTF — it does not start with the glTF header."
    version, total = struct.unpack("<II", data[4:12])
    if version != 2:
        return f"That is glTF version {version}; this reads version 2."
    # The header states the length of the whole file. A truncated upload is the common way for
    # this to be wrong, and it is worth telling apart from a file that was never a model.
    if total != len(data):
        return f"That glTF says it is {total} bytes and {len(data)} arrived — it looks truncated."
    return None


def _gltf(data: bytes) -> str | None:
    """The JSON form: parseable, and carrying the asset version every glTF has."""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "That is not a glTF — it is not readable as JSON."
    if not isinstance(document, dict) or "asset" not in document:
        return "That JSON is not a glTF: it has no `asset` block."
    if "version" not in (document.get("asset") or {}):
        return "That glTF's `asset` block states no version."
    return None


def _step(data: bytes) -> str | None:
    """
    ISO 10303-21, which announces itself on the first line and signs off on the last.

    Both ends are checked. A STEP cut off partway through still starts correctly, and a
    half-model is the kind of thing that looks fine until somebody tries to tessellate it.
    """
    head = data[:256].lstrip()
    if not head.upper().startswith(b"ISO-10303-21"):
        return "That is not a STEP file — it does not begin with ISO-10303-21."
    if b"END-ISO-10303-21" not in data[-256:].rstrip().upper():
        return "That STEP file has no END-ISO-10303-21 — it looks truncated."
    return None


def _stl(data: bytes) -> str | None:
    """
    Either flavour of STL: ASCII by its keywords, binary by its arithmetic.

    Binary is decided by size rather than by sniffing the header, because an 80-byte header
    can say anything at all -- including `solid`, which is how a binary STL gets mistaken for
    an ASCII one. The triangle count and the file length agree or it is not an STL.
    """
    if data.lstrip()[:5].lower() == b"solid" and b"facet" in data[:2048].lower():
        if b"endsolid" not in data[-2048:].lower():
            return "That ASCII STL has no `endsolid` — it looks truncated."
        return None

    if len(data) < _STL_HEADER:
        return "That is not an STL — it is too short to be one."
    (triangles,) = struct.unpack("<I", data[80:84])
    if len(data) != _STL_HEADER + triangles * _STL_TRIANGLE:
        return (
            "That is not an STL — its triangle count does not match its size "
            f"({triangles} triangles needs {_STL_HEADER + triangles * _STL_TRIANGLE} "
            f"bytes, {len(data)} arrived)."
        )
    return None
