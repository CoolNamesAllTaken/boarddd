"""
The model cache: what a model costs to work out, kept by what the model is, across boards.

A service that takes boards from many customers sees the same stock models on board after board. Each distinct model is
kept under its key (the product's text identity, `steptext.identity`, and where it sits in its
footprint, `export_local`): its measurements and fingerprint in the seat frame, its exported
STEP and GLB, its contacts for the footprint check, its footprint checks, and the cut-out STEP
of the product, from which anything else can be worked out again later. A model seen on any
earlier board then costs its instance transform and a few file reads.

Content-addressed and write-once: an entry's files only ever hold the one value for that key,
and each is written to a temporary name and moved into place, so two processes filling the
same entry at once both succeed and readers never see half a file.

Where it lives: `BOARDDD_STEP_CACHE` (or magpie's `MAGPIE_STEP_CACHE`), or the `cache=` argument
of `split`; unset, nothing is cached. Bump `VERSION` when anything cached changes meaning.

Source: magpie `step/cache.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4). The entry layout is magpie's; the version is boarddd's own since 0.7.1, when boarddd's measurements stopped matching magpie's.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from ._extra import setting as _setting

__all__ = ["Store", "open_store", "VERSION", "CACHE_ENV"]

#: Part of every key: entries from an older layout or older measuring code are never read.
#: boarddd's own since 0.7.1 (was "magpie-step-cache/1"): its measurements differ from magpie's.
VERSION = "boarddd-step-cache/1"
CACHE_ENV = "BOARDDD_STEP_CACHE"


class Store:
    """A directory of entries, `root/ab/abcdef.../name`."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha256(repr((VERSION,) + parts).encode()).hexdigest()[:40]

    def entry(self, key: str) -> Path:
        return self.root / key[:2] / key

    def has(self, key: str, name: str) -> bool:
        return (self.entry(key) / name).exists()

    def read_bytes(self, key: str, name: str) -> bytes | None:
        try:
            return (self.entry(key) / name).read_bytes()
        except FileNotFoundError:
            return None

    def write_bytes(self, key: str, name: str, data: bytes) -> None:
        folder = self.entry(key)
        folder.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{name}.")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temporary, folder / name)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def read_json(self, key: str, name: str):
        data = self.read_bytes(key, name)
        return None if data is None else json.loads(data)

    def write_json(self, key: str, name: str, value) -> None:
        self.write_bytes(key, name, json.dumps(value, sort_keys=True).encode())


def open_store(setting=None) -> Store | None:
    """`setting`: a Store, a path, False for none, or None for `$BOARDDD_STEP_CACHE`."""
    if setting is False:
        return None
    if isinstance(setting, Store):
        return setting
    if setting is None:
        setting = _setting("STEP_CACHE")
        if setting is None:
            return None
    return Store(setting)
