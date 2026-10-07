"""
OpenCascade access: loading OCP, reading a STEP into an XCAF document, and walking it.

OCP is the `cadquery-ocp` wheel (the `[step]` extra). It links libGL even though nothing here
draws, so importing it on a machine without `libGL.so.1` fails. Two ways round that:

* a Docker image or CI runner: `apt-get install -y --no-install-recommends libgl1`;
* no root: `python -m boarddd.step.occ fetch-gl` downloads Debian's three libglvnd packages
  (about 170 KB) and unpacks them into `<venv>/lib/boarddd-gl`. `load()` preloads them from
  there (or from `$BOARDDD_GL_LIBDIR`) before importing OCP, so nothing has to be put on
  `LD_LIBRARY_PATH`. No GL context is ever created, so no Mesa is needed.

Everything else in `boarddd.step` gets OCP through `load()`, so the workaround lives here only.

Source: magpie `step/occ.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4); settings renamed
`BOARDDD_*` (magpie's `MAGPIE_*` names still read).
"""

from __future__ import annotations

import ctypes
import io
import os
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ._extra import require, setting

require(__name__, ("OCP",))

__all__ = [
    "load",
    "gl_libdir",
    "fetch_gl",
    "Doc",
    "read",
    "new_doc",
    "label_name",
    "label_entry",
    "components",
    "referred",
    "location_matrix",
    "shape_of",
]

GL_LIBDIR_ENV = "BOARDDD_GL_LIBDIR"
#: Preloaded in dependency order; the dynamic loader then satisfies OCP's `libGL.so.1`
#: from the copy already in the process.
GL_LIBS = ("libGLdispatch.so.0", "libGLX.so.0", "libGL.so.1")
#: Debian bookworm builds, which need glibc 2.36 at most. KiCad's rootfs has libGL too, but
#: built against glibc 2.41, which this box's 2.36 cannot load.
GL_DEBS = tuple(
    f"http://deb.debian.org/debian/pool/main/libg/libglvnd/{name}_1.6.0-1_amd64.deb"
    for name in ("libgl1", "libglvnd0", "libglx0")
)

_ocp_loaded = False


def gl_libdir() -> Path:
    """Where `fetch_gl` puts the libraries and `load` looks for them."""
    configured = setting("GL_LIBDIR")
    return Path(configured) if configured else Path(sys.prefix) / "lib" / "boarddd-gl"


def _preload_gl() -> bool:
    folder = gl_libdir()
    if not all((folder / name).exists() for name in GL_LIBS):
        folder = Path(sys.prefix) / "lib" / "magpie-gl"  # where magpie's fetch-gl put them
        if not all((folder / name).exists() for name in GL_LIBS):
            return False
    for name in GL_LIBS:
        ctypes.CDLL(str(folder / name), mode=ctypes.RTLD_GLOBAL)
    return True


def load():
    """Import OCP, preloading a private libGL when the system has none. Returns the module."""
    global _ocp_loaded
    if not _ocp_loaded:
        try:
            import OCP  # noqa: F401
        except ImportError as error:
            if "libGL" not in str(error) or not _preload_gl():
                raise ImportError(
                    f"{error}. Install libgl1 (apt-get install libgl1) or run "
                    f"`python -m boarddd.step.occ fetch-gl` to get a private copy."
                ) from error
            sys.modules.pop("OCP", None)
            sys.modules.pop("OCP.OCP", None)
            import OCP  # noqa: F401
        _quiet()
        _ocp_loaded = True
    import OCP

    return OCP


def _quiet() -> None:
    """Stop OCCT printing reader chatter to stdout; problems are reported through results."""
    from OCP.Message import Message

    printers = Message.DefaultMessenger_s().Printers()
    for _ in range(printers.Size()):
        Message.DefaultMessenger_s().RemovePrinter(printers.First())


def _ar_members(data: bytes):
    """The members of a Unix `ar` archive (a .deb), as (name, bytes)."""
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive")
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset : offset + 60]
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode().strip())
        offset += 60
        yield name, data[offset : offset + size]
        offset += size + (size & 1)


def fetch_gl(folder: Path | None = None) -> Path:
    """Download libglvnd's three packages and unpack only the shared libraries into `folder`."""
    folder = folder or gl_libdir()
    folder.mkdir(parents=True, exist_ok=True)
    for url in GL_DEBS:
        with urllib.request.urlopen(url, timeout=60) as response:
            package = response.read()
        for name, member in _ar_members(package):
            if not name.startswith("data.tar"):
                continue
            with tarfile.open(fileobj=io.BytesIO(member)) as archive:
                for entry in archive.getmembers():
                    base = os.path.basename(entry.name)
                    if not base.startswith("lib") or ".so" not in base:
                        continue
                    if entry.issym():
                        target = folder / base
                        target.unlink(missing_ok=True)
                        target.symlink_to(os.path.basename(entry.linkname))
                    elif entry.isfile():
                        (folder / base).write_bytes(archive.extractfile(entry).read())
    return folder


# ─── Documents ───────────────────────────────────────────────────────────────


@dataclass
class Doc:
    """An XCAF document with its shape and color tools, as read from one STEP file."""

    doc: object
    shapes: object
    colors: object
    #: What the reader complained about (unresolved references and the like), for reports.
    warnings: list[str]


def new_doc() -> Doc:
    load()
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    return Doc(
        document,
        XCAFDoc_DocumentTool.ShapeTool_s(document.Main()),
        XCAFDoc_DocumentTool.ColorTool_s(document.Main()),
        [],
    )


def read(source: str | os.PathLike | bytes) -> Doc:
    """Read a STEP file (a path or its bytes) with names and colors into a new document."""
    load()
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader

    if isinstance(source, (bytes, bytearray, memoryview)):
        # OCCT's stream reader is not exposed usefully; a temporary file costs one write.
        with tempfile.NamedTemporaryFile(suffix=".step") as handle:
            handle.write(bytes(source))
            handle.flush()
            return read(handle.name)

    result = new_doc()
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(False)
    reader.SetPropsMode(False)
    status = reader.ReadFile(str(source))
    if status != IFSelect_RetDone:
        raise ValueError(f"not a readable STEP file ({status})")
    if not reader.Transfer(result.doc):
        raise ValueError("STEP file read but nothing could be transferred")
    return result


# ─── Labels ──────────────────────────────────────────────────────────────────


def label_name(label) -> str:
    from OCP.TDataStd import TDataStd_Name

    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString()
    return ""


def label_entry(label) -> str:
    """The label's tag path, `0:1:1:3`: a stable key for a product within one document."""
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def components(label) -> list:
    """The instance labels directly under an assembly label (empty for a simple shape)."""
    from OCP.collections import Sequence_TDF_Label
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    found = Sequence_TDF_Label()
    if not XCAFDoc_ShapeTool.IsAssembly_s(label):
        return []
    XCAFDoc_ShapeTool.GetComponents_s(label, found, False)
    return [found.Value(i) for i in range(1, found.Length() + 1)]


def referred(label):
    """The product label an instance label refers to (the label itself when it is not one)."""
    from OCP.TDF import TDF_Label
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    target = TDF_Label()
    if XCAFDoc_ShapeTool.IsReference_s(label) and XCAFDoc_ShapeTool.GetReferredShape_s(label, target):
        return target
    return label


def colors(label, _depth: int = 0) -> set[tuple[float, float, float]]:
    """Every distinct color under a product label (its sub-shapes and its components' products)."""
    from OCP.Quantity import Quantity_Color
    from OCP.TDF import TDF_ChildIterator
    from OCP.XCAFDoc import XCAFDoc_ColorTool, XCAFDoc_ColorType

    found: set[tuple[float, float, float]] = set()
    labels = [label]
    children = TDF_ChildIterator(label, True)
    while children.More():
        labels.append(children.Value())
        children.Next()
    for each in labels:
        for kind in (XCAFDoc_ColorType.XCAFDoc_ColorGen, XCAFDoc_ColorType.XCAFDoc_ColorSurf):
            color = Quantity_Color()
            if XCAFDoc_ColorTool.GetColor_s(each, kind, color):
                found.add((round(color.Red(), 3), round(color.Green(), 3), round(color.Blue(), 3)))
    if _depth < 8:
        for instance in components(label):
            found |= colors(referred(instance), _depth + 1)
    return found


def free_shapes(doc: Doc) -> list:
    from OCP.collections import Sequence_TDF_Label

    found = Sequence_TDF_Label()
    doc.shapes.GetFreeShapes(found)
    return [found.Value(i) for i in range(1, found.Length() + 1)]


def shape_of(label):
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    return XCAFDoc_ShapeTool.GetShape_s(label)


def location_matrix(label):
    """An instance label's placement as a 4x4 numpy matrix (identity for a non-instance)."""
    import numpy as np
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    return (
        trsf_matrix(XCAFDoc_ShapeTool.GetLocation_s(label).Transformation())
        if XCAFDoc_ShapeTool.IsReference_s(label)
        else np.eye(4)
    )


def trsf_matrix(trsf):
    import numpy as np

    matrix = np.eye(4)
    for row in range(3):
        for column in range(4):
            matrix[row, column] = trsf.Value(row + 1, column + 1)
    return matrix


def matrix_trsf(matrix):
    """A rigid 4x4 matrix as a gp_Trsf."""
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetValues(*(float(matrix[row, column]) for row in range(3) for column in range(4)))
    return trsf


def _main(argv: list[str]) -> int:
    if argv[:1] == ["fetch-gl"]:
        folder = fetch_gl(Path(argv[1]) if len(argv) > 1 else None)
        print(f"libGL unpacked into {folder}")
        return 0
    if argv[:1] == ["check"]:
        load()
        from OCP.STEPCAFControl import STEPCAFControl_Reader  # noqa: F401  (what reading needs)

        print("OCP imports")
        return 0
    print("usage: python -m boarddd.step.occ fetch-gl [DIR] | check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
