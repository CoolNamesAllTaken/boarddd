"""
Reading a zip somebody uploaded.

This is the only place in the readers that opens an archive, so the whole hostile-archive
surface is this file: zip slip, decompression bombs, symlink escapes, member floods.
It is written to be read by somebody checking exactly that, and it is pure -- no
settings, no filesystem writes. It yields names and streams; the caller decides where bytes go.

The sizes are checked while decompressing, never from the header. A zip states each member's
uncompressed size in its own directory, and a hostile zip states whatever it likes.

Source: `magpie/pcb/extract.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import lzma
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

__all__ = ["ArchiveError", "Budget", "MAX_MEMBERS", "MAX_MEMBER_BYTES", "MAX_TOTAL_BYTES", "safe_relpath", "members"]

#: What zipfile raises while unpacking a member it cannot unpack: a bad CRC or header
#: (BadZipFile), a compression method it has no codec for (NotImplementedError, e.g. method 99,
#: WinZip's AES), a stream that is damaged (zlib.error, lzma.LZMAError, and OSError from bz2) or
#: cut short (EOFError). Encryption is RuntimeError, but is told apart before it is met.
_UNPACK_ERRORS = (zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error, lzma.LZMAError, OSError, EOFError)

#: A KiCad export is ~27 files. Room for a panel or a few revisions, not for a zip bomb.
MAX_MEMBERS = 256
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
CHUNK = 64 * 1024
#: The longest name one path segment may have on disk, and the longest relpath a FabFile holds.
MAX_SEGMENT_BYTES = 255
MAX_RELPATH_CHARS = 400


class ArchiveError(ValueError):
    """The archive is malformed, hostile, or bigger than we accept."""


class Budget:
    """
    How many decompressed bytes one upload may still turn into, across everything in it.

    `members` used to count per archive, which made MAX_TOTAL_BYTES a limit on one zip and
    not on a request: sixty-four zips each just under it were sixteen gigabytes held in
    memory before a byte was written. The caller reading a whole request makes one of these
    and hands it to every archive and every loose file it reads, so the ceiling is the
    request's. `limit` is read when the budget is made, not at import, so a test may move
    MAX_TOTAL_BYTES first.
    """

    def __init__(self, limit: int | None = None):
        self.limit = MAX_TOTAL_BYTES if limit is None else limit
        self.spent = 0

    def spend(self, count: int) -> None:
        self.spent += count
        if self.spent > self.limit:
            raise ArchiveError(f"The upload expands to more than {self.limit // (1024 * 1024)} MB.")


@dataclass(frozen=True)
class Member:
    relpath: str
    data: bytes


def safe_relpath(name: str) -> str:
    """
    The path a member may be written to, or '' if it may not be written at all.

    Rejects rather than sanitises where sanitising would invent a path nobody asked for: an
    absolute path, a drive letter or a '..' segment means the archive is trying to write
    outside the destination, and the honest response is to refuse the member.

    A segment starting with '.' is refused too. An application storing a package next to
    its own derived files keeps those in dot-directories (`.derived`, `.kept`) and relies on
    no uploaded relpath being able to land there; a member named `.kept/shot-assembly.png`
    would otherwise overwrite one. No real export puts its files in a dot-directory.
    """
    if not name or name.endswith("/"):
        return ""
    # Windows writers use backslashes; treat them as separators before judging the segments,
    # or 'a\\..\\..\\etc' passes a check that only understands '/'.
    candidate = name.replace("\\", "/").strip()
    if candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
        return ""

    segments = []
    for segment in candidate.split("/"):
        if segment in ("", "."):
            continue
        if segment == ".." or segment.startswith("."):
            return ""
        # A name the filesystem cannot hold (NAME_MAX is 255 bytes) was an OSError at the
        # write, and so a server error.
        if len(segment.encode("utf-8", errors="surrogateescape")) > MAX_SEGMENT_BYTES:
            return ""
        segments.append(segment)
    if not segments:
        return ""
    relpath = "/".join(segments)
    # The longest relpath a caller is expected to store; longer is refused here, not at the write.
    return relpath if len(relpath) <= MAX_RELPATH_CHARS else ""


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # The unix mode lives in the top 16 bits of external_attr; 0o120000 is S_IFLNK. A symlink
    # member's "content" is a path, and writing it would hand the archive a way to point at
    # anything on the filesystem.
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def members(archive: zipfile.ZipFile, *, budget: Budget | None = None) -> Iterator[Member]:
    """
    Every member worth storing, decompressed and size-checked as it is read.

    Skipped without complaint: directories, symlinks, empty files, and nested archives. That
    last one is not squeamishness -- a KiCad manufacturing zip contains a zero-byte entry
    named after the zip itself, and recursing into archives is how a bomb gets past a
    per-member limit.

    `budget` is the request's (see Budget); without one, this archive gets MAX_TOTAL_BYTES to
    itself, which is right for a caller reading exactly one archive.
    """
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise ArchiveError(f"The archive has {len(infos)} files; the limit is {MAX_MEMBERS}.")

    if budget is None:
        budget = Budget()
    for info in infos:
        if _is_symlink(info):
            continue
        relpath = safe_relpath(info.filename)
        if not relpath or relpath.lower().endswith((".zip", ".tar", ".gz", ".tgz", ".7z")):
            continue

        # A password is an ordinary mistake -- somebody zipped the export the way their mail
        # system insists on -- and deserves a sentence that says so. zipfile's answer was a
        # RuntimeError, and so a server error, as was an unpacking it has no codec for.
        if info.flag_bits & 0x1:
            raise ArchiveError(f"{relpath} is password-protected. Zip the files again without a password.")

        chunks, size = [], 0
        try:
            with archive.open(info) as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_MEMBER_BYTES:
                        raise ArchiveError(f"{relpath} is larger than {MAX_MEMBER_BYTES // (1024 * 1024)} MB.")
                    budget.spend(len(chunk))
                    chunks.append(chunk)
        except _UNPACK_ERRORS as exc:
            raise ArchiveError(
                f"{relpath} could not be unpacked ({exc or type(exc).__name__}). "
                f"Zip the files again with ordinary compression."
            ) from exc

        if size == 0:
            continue
        yield Member(relpath=relpath, data=b"".join(chunks))
