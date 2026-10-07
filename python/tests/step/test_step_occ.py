"""The OCP loader and the libGL workaround."""

from __future__ import annotations

import io
import tarfile

import pytest
from stepkit import require_occ

require_occ()

from boarddd.step import occ  # noqa: E402


def _ar(members: dict[str, bytes]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, data in members.items():
        header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n"
        out += header.encode() + data + (b"\n" if len(data) % 2 else b"")
    return bytes(out)


def test_ar_members_reads_a_deb_layout():
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:xz") as archive:
        data = b"\x7fELF fake"
        info = tarfile.TarInfo("./usr/lib/x86_64-linux-gnu/libGL.so.1.7.0")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    deb = _ar({"debian-binary": b"2.0\n", "control.tar.xz": b"x", "data.tar.xz": payload.getvalue()})
    names = [name for name, _data in occ._ar_members(deb)]
    assert names == ["debian-binary", "control.tar.xz", "data.tar.xz"]


def test_gl_libdir_follows_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(occ.GL_LIBDIR_ENV, str(tmp_path))
    assert occ.gl_libdir() == tmp_path


def test_load_imports_ocp():
    try:
        module = occ.load()
    except ImportError as error:
        pytest.skip(str(error))
    from OCP.STEPCAFControl import STEPCAFControl_Reader  # noqa: F401

    assert module.__name__ == "OCP"


def test_read_rejects_garbage():
    try:
        occ.load()
    except ImportError as error:
        pytest.skip(str(error))
    with pytest.raises(ValueError):
        occ.read(b"this is not a STEP file")
