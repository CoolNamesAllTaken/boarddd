"""
Reading a zip somebody uploaded (`boarddd.io.archive`).

Ported from magpie `tests/pcb/test_extract.py` at 3a0374d3.

This is the application's whole hostile-archive surface, so the tests are about what an
archive must not be allowed to do rather than about the happy path.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from boarddd.io import archive as extract


def _zip(entries: dict) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


# ─── Where a member may be written ───────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "../escape.gbr",
        "../../etc/passwd",
        "fab/../../escape.gbr",
        "/absolute.gbr",
        "C:/windows/system32/evil.gbr",
        r"..\..\escape.gbr",
        r"fab\..\..\escape.gbr",
        "",
    ],
)
def test_a_path_that_escapes_is_refused(name):
    """
    Refused, not sanitised. Rewriting '../../etc/passwd' into 'etc/passwd' stores a file the
    archive never asked for under a name nobody chose; declining to store it is the only
    answer that cannot surprise anyone.
    """
    assert extract.safe_relpath(name) == ""


@pytest.mark.parametrize(
    "name,expected",
    [
        ("fab/gerber/board-F_Cu.gbr", "fab/gerber/board-F_Cu.gbr"),
        ("./fab/./board.gbr", "fab/board.gbr"),
        (r"fab\gerber\board.gbr", "fab/gerber/board.gbr"),
        ("board.gbr", "board.gbr"),
    ],
)
def test_an_ordinary_path_survives(name, expected):
    assert extract.safe_relpath(name) == expected


@pytest.mark.parametrize(
    "name", [".kept/shot-assembly.png", ".derived/board.glb", "a/.hidden/b.gbr", ".git/config", r"fab\.kept\x.png"]
)
def test_a_dot_segment_is_refused(name):
    """
    An application storing a package keeps its own files in dot-directories (`.derived`,
    `.kept`) and relies on no uploaded relpath reaching them -- a member named
    `.kept/shot-assembly.png` would be served as the board's own picture. Refusing only '..'
    is not enough.
    """
    assert extract.safe_relpath(name) == ""


@pytest.mark.parametrize(
    "name",
    ["fab/" + "x" * 256 + ".gbr", "fab/" + "\u00e9" * 128, "/".join(["abcdefghij"] * 37) + ".gbr"],
    ids=["long-segment", "long-segment-in-bytes", "long-path"],
)
def test_a_path_too_long_to_store_is_refused(name):
    """Past NAME_MAX, or the 400-character relpath limit: an OSError or a database error at the write."""
    assert extract.safe_relpath(name) == ""


def test_a_path_just_short_enough_survives():
    name = "/".join(["a" * 255] + ["b" * 143])
    assert len(name) == 399 and extract.safe_relpath(name) == name


def test_a_dot_in_a_name_is_not_a_dot_segment():
    """Only a segment that STARTS with '.' is a dot-directory; every gerber has a dot in it."""
    assert extract.safe_relpath("a/b.gbr") == "a/b.gbr"
    assert extract.safe_relpath("fab/board-F.Cu.gbr") == "fab/board-F.Cu.gbr"


def test_an_escaping_member_is_skipped_not_fatal():
    """One hostile entry must not cost the other twenty-six files."""
    members = list(extract.members(_zip({"../evil.gbr": b"x", "good.gbr": b"G04*"})))

    assert [m.relpath for m in members] == ["good.gbr"]


# ─── Size and count limits ───────────────────────────────────────────────────


def test_too_many_members_is_refused():
    archive = _zip({f"f{i}.gbr": b"x" for i in range(extract.MAX_MEMBERS + 1)})
    with pytest.raises(extract.ArchiveError, match="the limit is"):
        list(extract.members(archive))


def test_a_member_bigger_than_the_limit_is_refused(monkeypatch):
    """
    Measured while decompressing, never from the header: a zip states each member's
    uncompressed size in its own directory, and a hostile zip states whatever it likes.
    """
    monkeypatch.setattr(extract, "MAX_MEMBER_BYTES", 1024)
    archive = _zip({"bomb.gbr": b"0" * 8192})

    with pytest.raises(extract.ArchiveError, match="larger than"):
        list(extract.members(archive))


def test_the_total_is_capped_even_when_each_member_is_small(monkeypatch):
    monkeypatch.setattr(extract, "MAX_TOTAL_BYTES", 4096)
    archive = _zip({f"f{i}.gbr": b"0" * 1024 for i in range(8)})

    with pytest.raises(extract.ArchiveError, match="expands to more than"):
        list(extract.members(archive))


def test_one_budget_spans_every_archive_it_is_handed(monkeypatch):
    """
    MAX_TOTAL_BYTES was per call, so per archive: a request of many zips each under it had
    no ceiling at all. A Budget handed to each call is the request's ceiling.
    """
    monkeypatch.setattr(extract, "MAX_TOTAL_BYTES", 4096)
    budget = extract.Budget()

    def small():
        return _zip({"f.gbr": b"0" * 1500})  # three of these are 4500

    assert len(list(extract.members(small(), budget=budget))) == 1
    assert len(list(extract.members(small(), budget=budget))) == 1
    with pytest.raises(extract.ArchiveError, match="expands to more than"):
        list(extract.members(small(), budget=budget))
    # Without one, each archive is judged on its own, as before.
    assert len(list(extract.members(small()))) == 1


def test_the_header_size_is_not_trusted():
    """A lying directory entry must not get past the limit; only the bytes read count."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("honest.gbr", b"G04 real*\n")
    raw = bytearray(buffer.getvalue())
    archive = zipfile.ZipFile(io.BytesIO(bytes(raw)))
    # Claim the member is enormous; extraction should still succeed on its real ten bytes.
    archive.infolist()[0].file_size = 10**12

    members = list(extract.members(archive))
    assert [m.data for m in members] == [b"G04 real*\n"]


# ─── What is skipped ─────────────────────────────────────────────────────────


def test_directories_and_empty_members_are_skipped():
    """
    A KiCad manufacturing zip contains a zero-byte entry named after the zip itself. Storing
    it would put an empty file called 'something-manufacturing.zip' in every package.
    """
    members = list(extract.members(_zip({"fab/": b"", "empty.zip": b"", "real.gbr": b"G04*"})))

    assert [m.relpath for m in members] == ["real.gbr"]


def test_nested_archives_are_skipped():
    """Not squeamishness: recursing is how a bomb gets past a per-member limit."""
    members = list(extract.members(_zip({"inner.zip": b"PK\x03\x04 pretend", "real.gbr": b"G04*"})))

    assert [m.relpath for m in members] == ["real.gbr"]


def test_a_symlink_member_is_skipped():
    """
    A symlink's content is a path. Writing it hands the archive a pointer to any file on the
    box, and the next read of "that gerber" reads whatever it aimed at.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link.gbr")
        info.external_attr = 0o120777 << 16  # S_IFLNK
        archive.writestr(info, "/etc/passwd")
        archive.writestr("real.gbr", b"G04*")

    members = list(extract.members(zipfile.ZipFile(io.BytesIO(buffer.getvalue()))))
    assert [m.relpath for m in members] == ["real.gbr"]


# ─── What an archive cannot be unpacked with ─────────────────────────────────


def _raw_zip(name: str, data: bytes, *, flag_bits: int = 0, method: int = zipfile.ZIP_STORED) -> bytes:
    """A one-member zip whose header says what the caller wants, not what zipfile would write."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
    raw = bytearray(buffer.getvalue())
    for offset, signature in ((0, b"PK\x03\x04"), (raw.rindex(b"PK\x01\x02"), b"PK\x01\x02")):
        assert raw[offset : offset + 4] == signature
        # The flag bits and the method sit two bytes further into a central-directory entry
        # than into a local header, which starts with no "version made by".
        at = offset + (8 if signature == b"PK\x01\x02" else 6)
        raw[at : at + 2] = flag_bits.to_bytes(2, "little")
        raw[at + 2 : at + 4] = method.to_bytes(2, "little")
    return bytes(raw)


def test_a_password_protected_zip_is_refused_with_a_sentence():
    """zipfile's RuntimeError was a server error for an ordinary mistake."""
    archive = zipfile.ZipFile(io.BytesIO(_raw_zip("board.gbr", b"G04*\n", flag_bits=0x1)))

    with pytest.raises(extract.ArchiveError, match="password"):
        list(extract.members(archive))


@pytest.mark.parametrize("method", [99, zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA])
def test_a_member_that_cannot_be_unpacked_is_refused(method):
    """An unknown method (99 is WinZip's AES) or a stream that is not what the header says."""
    archive = zipfile.ZipFile(io.BytesIO(_raw_zip("board.gbr", b"G04 not packed at all*\n", method=method)))

    with pytest.raises(extract.ArchiveError, match="could not be unpacked"):
        list(extract.members(archive))
