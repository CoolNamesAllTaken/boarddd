"""
ODB++ (v7/v8) -> components with their footprints, the same `Board` as `boarddd.io.ipc2581`.

An ODB++ job is a directory tree, usually shipped as .tgz or .zip. What this reads, for one
step (`steps/<step>/`):

* `matrix/matrix` -- every layer's ROW, TYPE (SIGNAL, SOLDER_MASK, SOLDER_PASTE, DRILL,
  COMPONENT ...) and NAME. A layer's side comes from its row relative to the signal layers.
* `eda/data` -- `UNITS`, the `LYR` list, the packages (`PKG`, with their outline and their
  `PIN` records: name, type, center in the package frame) and the net section, whose
  `SNT TOP <T|B> <cmp> <pin>` / `FID <C|H> <layer> <feature>` records tie each component pin
  to the features drawn for it on each layer.
* `layers/comp_+_top|comp_+_bot/components` -- `CMP` (package, x, y, rotation, mirror,
  reference, part name), `PRP` properties (MPN, Manufacturer, Value ...) and `TOP` toeprints.
* `layers/<layer>/features` -- the pads (`P` records with symbol, rotation and mirror). The
  standard symbols (`r`, `s`, `rect`, `rect…xr…`, `rect…xc…`, `oval`, `di`, `oct`, `el`,
  `donut_r`) are read, and user symbols from `symbols/<name>/features` (their surfaces become
  polygon pads).

Units: `UNITS=MM|INCH` (or `U MM|INCH`) per file. Symbol sizes are in microns (MM) or mils
(INCH). Members compressed as `.Z` (Unix compress) or `.gz` are read transparently.

Conventions settled against KiCad 10's exporter and synthetic boards with known ground truth
(magpie's corpus; in boarddd, python/tests/io/test_odbpp.py cross-checks KiCad's demo boards):

* rotations are clockwise: a top component's model rotation (CCW) is `-rotation`;
* on the bottom (`M`, or listed in comp_+_bot) the format's convention is a top-view package
  with model rotation `180 + rotation`; KiCad 10 instead writes a separate, already mirrored
  package with rotation `-rotation`. Each bottom component is read the way that puts its
  pins on the file's toeprints (`_placement`);
* a pad's angle in the footprint frame is `-(feature angle) - component` on top,
  `component + feature angle` for a top-view package on the bottom, and
  `component - feature angle` for KiCad's mirrored packages (all in the model's CCW sense).

KiCad writes ODB++ with 2 decimals by default (`--precision`), so its coordinates are only
good to 0.005 mm.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import math
import re
import tarfile
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from boarddd import model as m
from boarddd.io import eda
from boarddd.io.eda import Aperture, Board, Component, Drill, Footprint, FootprintSource, Graphic, Pad, Placement
from boarddd.io.layers import layer_id as eda_layer_id
from boarddd.io.layers import layer_order, sort_key

__all__ = ["Board", "Component", "read_components", "read_odbpp", "Job", "symbol_shape", "uncompress_z"]

Point = tuple[float, float]
_UNIT_MM = {"MM": 1.0, "INCH": 25.4}
_SYMBOL_UNIT_MM = {"MM": 0.001, "INCH": 0.0254}  # microns or mils


# ─── The job's files ─────────────────────────────────────────────────────────


class Job:
    """An ODB++ job's members by lowercase path, whatever it was shipped as."""

    def __init__(self, source):
        self.files: dict[str, bytes] = {}
        if isinstance(source, (bytes, bytearray)):
            self._archive(bytes(source))
        else:
            path = Path(source)
            if path.is_dir():
                for p in path.rglob("*"):
                    if p.is_file():
                        self._add(str(p.relative_to(path)), p.read_bytes())
            else:
                self._archive(path.read_bytes())
        self._strip_root()

    def _archive(self, data: bytes) -> None:
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        self._add(info.filename, z.read(info))
            return
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
                for m in t.getmembers():
                    if m.isfile():
                        self._add(m.name, t.extractfile(m).read())
            return
        except tarfile.TarError:
            pass
        raise ValueError("not an ODB++ archive (zip, tar, tgz) or directory")

    def _add(self, name: str, data: bytes) -> None:
        name = name.replace("\\", "/").lstrip("./")
        low = name.lower()
        # Decompress by content, not by name: a layer can be named `…z`, and DipTrace writes
        # its `.Z` members as single-file zip archives.
        if low.endswith((".z", ".gz")):
            stem = low[:-2] if low.endswith(".z") else low[:-3]
            if data[:2] == b"\x1f\x9d":
                data, low = uncompress_z(data), stem
            elif data[:2] == b"\x1f\x8b":
                data, low = gzip.decompress(data), stem
            elif data[:4] == b"PK\x03\x04":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    members = [i for i in z.infolist() if not i.is_dir()]
                    if len(members) == 1:
                        data, low = z.read(members[0]), stem
        self.files[low] = data

    def _strip_root(self) -> None:
        """Archives usually hold one top directory (the job name): drop it."""
        if any(k.startswith("matrix/") for k in self.files):
            return
        roots = {k.split("/", 1)[0] for k in self.files if "/" in k}
        for root in roots:
            if f"{root}/matrix/matrix" in self.files:
                self.files = {k[len(root) + 1 :]: v for k, v in self.files.items() if k.startswith(root + "/")}
                return

    def text(self, name: str) -> str | None:
        data = self.files.get(name.lower())
        return data.decode("utf-8", "replace") if data is not None else None

    def steps(self) -> list[str]:
        return sorted({k.split("/")[1] for k in self.files if k.startswith("steps/") and k.count("/") >= 2})


def uncompress_z(data: bytes) -> bytes:
    """Unix `compress` (.Z, LZW) data, as old ODB++ jobs store their members."""
    if data[:2] != b"\x1f\x9d":
        raise ValueError("not .Z data")
    flags = data[2]
    max_bits = flags & 0x1F
    block_mode = bool(flags & 0x80)
    out = bytearray()
    table: list[bytes] = [bytes([i]) for i in range(256)]
    if block_mode:
        table.append(b"")  # 256 = CLEAR
    bits, prev = 9, None
    buf, nbits, pos = 0, 0, 3
    group_start = 3  # codes come in groups of `bits` bytes; CLEAR skips to the end
    n = len(data)
    while True:
        while nbits < bits and pos < n:
            buf |= data[pos] << nbits
            nbits += 8
            pos += 1
        if nbits < bits:
            break
        code = buf & ((1 << bits) - 1)
        buf >>= bits
        nbits -= bits
        if block_mode and code == 256:
            # Skip to the end of the current group of `bits`-byte chunks.
            consumed = pos - group_start
            pad = (bits - consumed % bits) % bits
            pos += pad
            buf, nbits = 0, 0
            group_start = pos
            table = table[:257]
            bits, prev = 9, None
            continue
        if code < len(table):
            entry = table[code]
            if prev is not None:
                table.append(prev + entry[:1])
        elif code == len(table) and prev is not None:
            entry = prev + prev[:1]
            table.append(entry)
        else:
            raise ValueError("corrupt .Z data")
        out += entry
        prev = entry
        if len(table) >= (1 << bits) and bits < max_bits:
            consumed = pos - group_start
            pad = (bits - consumed % bits) % bits
            pos += pad
            buf, nbits = 0, 0
            group_start = pos
            bits += 1
    return bytes(out)


def _r2(size) -> tuple[float, float]:
    """A size rounded to 6 decimals (1 nm), as the model serializes it."""
    return (round(size[0], 6), round(size[1], 6))


# ─── Small parsers ───────────────────────────────────────────────────────────


def _units(text: str, default: str = "INCH") -> str:
    m = re.search(r"^\s*(?:UNITS\s*=\s*|U\s+)(MM|INCH)\b", text, re.M | re.I)
    return m.group(1).upper() if m else default


def _blocks(text: str, name: str) -> list[dict[str, str]]:
    out = []
    for body in re.findall(name + r"\s*\{(.*?)\}", text, re.S):
        out.append(
            {k.strip().upper(): v.strip() for k, v in (line.split("=", 1) for line in body.splitlines() if "=" in line)}
        )
    return out


@dataclass
class _Layer:
    name: str
    type: str
    row: int
    side: str = ""  # top | bottom | inner | ''


def _matrix(job: Job) -> dict[str, _Layer]:
    text = job.text("matrix/matrix") or ""
    layers = {}
    for b in _blocks(text, "LAYER"):
        try:
            row = int(b.get("ROW", "0"))
        except ValueError:
            row = 0
        name = b.get("NAME", "").lower()
        layers[name] = _Layer(name, b.get("TYPE", "").upper(), row)
    signal = sorted(la.row for la in layers.values() if la.type in ("SIGNAL", "POWER_GROUND", "MIXED"))
    for la in layers.values():
        if not signal:
            continue
        if la.row <= signal[0]:
            la.side = "top"
        elif la.row >= signal[-1]:
            la.side = "bottom"
        else:
            la.side = "inner"
    return layers


@dataclass(frozen=True)
class Shape:
    shape: str
    size: Point
    roundrect_ratio: float = 0.0
    chamfer_ratio: float = 0.0
    chamfered: tuple[str, ...] = ()
    polygon: tuple[Point, ...] = ()


_NUM = r"(\d+(?:\.\d+)?)"
_CORNER_NAMES = {"1": "top_right", "2": "top_left", "3": "bottom_left", "4": "bottom_right"}


def symbol_shape(name: str, unit_mm: float) -> Shape | None:
    """A standard ODB++ symbol name -> Shape in mm (`unit_mm`: mm per symbol unit)."""
    s = name.strip().lower()
    if m := re.fullmatch(r"r" + _NUM, s):
        d = float(m.group(1)) * unit_mm
        return Shape("circle", (d, d))
    if m := re.fullmatch(r"s" + _NUM, s):
        a = float(m.group(1)) * unit_mm
        return Shape("rect", (a, a))
    if m := re.fullmatch(r"rect" + _NUM + "x" + _NUM + r"(?:x(r|c)" + _NUM + r"(?:x(\d+))?)?", s):
        w, h = float(m.group(1)) * unit_mm, float(m.group(2)) * unit_mm
        if not m.group(3):
            return Shape("rect", (w, h))
        r = float(m.group(4)) * unit_mm
        corners = tuple(_CORNER_NAMES[c] for c in (m.group(5) or "") if c in _CORNER_NAMES)
        ratio = round(r / min(w, h), 6) if min(w, h) else 0.0
        if m.group(3) == "r":
            return Shape("roundrect", (w, h), roundrect_ratio=ratio)
        return Shape("chamfered", (w, h), chamfer_ratio=ratio, chamfered=corners or tuple(_CORNER_NAMES.values()))
    if m := re.fullmatch(r"(?:oval|el)" + _NUM + "x" + _NUM, s):
        return Shape("oval", (float(m.group(1)) * unit_mm, float(m.group(2)) * unit_mm))
    if m := re.fullmatch(r"donut_r" + _NUM + "x" + _NUM, s):
        d = float(m.group(1)) * unit_mm
        return Shape("circle", (d, d))
    if m := re.fullmatch(r"di" + _NUM + "x" + _NUM, s):
        w, h = float(m.group(1)) * unit_mm, float(m.group(2)) * unit_mm
        return Shape("polygon", (w, h), polygon=((w / 2, 0), (0, h / 2), (-w / 2, 0), (0, -h / 2)))
    if m := re.fullmatch(r"oct" + _NUM + "x" + _NUM + "x" + _NUM, s):
        w, h, c = (float(m.group(i)) * unit_mm for i in (1, 2, 3))
        x, y = w / 2, h / 2
        outline = (
            (x - c, y),
            (-x + c, y),
            (-x, y - c),
            (-x, -y + c),
            (-x + c, -y),
            (x - c, -y),
            (x, -y + c),
            (x, y - c),
        )
        return Shape("polygon", (w, h), polygon=outline)
    return None


def _surface_points(lines: list[str], scale: float) -> list[list[Point]]:
    """OB/OS/OC/OE contours of a surface or an outline (arcs as their end points)."""
    contours, cur = [], []
    for line in lines:
        t = line.split()
        if not t:
            continue
        if t[0] == "OB":
            cur = [(float(t[1]) * scale, float(t[2]) * scale)]
        elif t[0] in ("OS", "OC") and cur is not None:
            cur.append((float(t[1]) * scale, float(t[2]) * scale))
        elif t[0] == "OE":
            if len(cur) > 1 and math.dist(cur[0], cur[-1]) < 1e-9:
                cur.pop()
            if len(cur) >= 3:
                contours.append(cur)
            cur = []
    return contours


def _polygon_shape(points: list[Point]) -> tuple[Shape, Point]:
    """An outline as a polygon shape around its extent center; an axis-aligned rectangle
    comes back as a rect."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    corners = {(round(x, 6), round(y, 6)) for x, y in points}
    axis_aligned = len({x for x, _ in corners}) == 2 and len({y for _, y in corners}) == 2
    if len(corners) == 4 and axis_aligned:
        return Shape("rect", (round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6))), (cx, cy)
    return (
        Shape(
            "polygon",
            (max(xs) - min(xs), max(ys) - min(ys)),
            polygon=tuple((round(x - cx, 6), round(y - cy, 6)) for x, y in points),
        ),
        (cx, cy),
    )


@dataclass
class _Feature:
    x: float
    y: float
    symbol: str
    rotation: float  # clockwise, degrees
    mirror: bool
    attrs: str = ""
    #: A surface (custom pad, segmented paste): its outline in board coordinates (file units).
    contour: tuple[Point, ...] = ()


@dataclass
class _Features:
    units: str
    symbols: dict[int, str]
    features: list[_Feature | None]  # by feature number; None for non-pad features


_LEGACY_ORIENT = {
    0: (0, False),
    1: (90, False),
    2: (180, False),
    3: (270, False),
    4: (0, True),
    5: (90, True),
    6: (180, True),
    7: (270, True),
}


def _features(text: str) -> _Features:
    units = _units(text)
    symbols: dict[int, str] = {}
    feats: list[_Feature | None] = []
    surface: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("$"):
            num, _, rest = line[1:].partition(" ")
            symbols[int(num)] = rest.split()[0] if rest.split() else ""
            continue
        head = line.split()[0]
        if surface is not None:
            if head == "SE":
                contours = _surface_points(surface, 1.0)
                if contours:
                    outer = max(contours, key=len)
                    xs, ys = [p[0] for p in outer], [p[1] for p in outer]
                    feats[-1] = _Feature(
                        (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, "", 0.0, False, contour=tuple(outer)
                    )
                surface = None
            else:
                surface.append(line)
            continue
        if head == "S":
            feats.append(None)
            surface = []
        elif head == "P":
            body, _, attrs = line.partition(";")
            t = body.split()
            # P x y apt_def polarity dcode orient_def [rotation]
            x, y, sym = float(t[1]), float(t[2]), int(t[3])
            orient = int(t[6]) if len(t) > 6 else 0
            if orient in (8, 9):
                rotation, mirror = float(t[7]) if len(t) > 7 else 0.0, orient == 9
            else:
                rotation, mirror = _LEGACY_ORIENT.get(orient, (0, False))
            feats.append(_Feature(x, y, symbols.get(sym, ""), rotation, mirror, attrs))
        elif head in ("L", "A", "T", "B"):
            feats.append(None)
    return _Features(units, symbols, feats)


# ─── eda/data ────────────────────────────────────────────────────────────────


@dataclass
class _Pin:
    name: str
    type: str  # T thru, B blind, S surface
    x: float
    y: float
    fhs: float  # finished hole size
    outline: list[list[Point]] = field(default_factory=list)
    shape_lines: list[str] = field(default_factory=list)


@dataclass
class _Package:
    name: str
    pins: list[_Pin] = field(default_factory=list)
    outline: list[list[Point]] = field(default_factory=list)


@dataclass
class _Eda:
    units: str
    layers: list[str]
    packages: list[_Package]
    #: (side 'T'|'B', component number, pin number) -> [(fid type, layer name, feature number)]
    fids: dict[tuple[str, int, int], list[tuple[str, str, int]]]


def _eda(text: str) -> _Eda:
    units = _units(text)
    scale = _UNIT_MM[units]
    layers: list[str] = []
    packages: list[_Package] = []
    fids: dict[tuple[str, int, int], list[tuple[str, str, int]]] = {}
    current_snt = None
    pkg: _Package | None = None
    pin: _Pin | None = None
    shape_lines: list[str] = []

    def flush():
        nonlocal shape_lines
        if pkg is None:
            shape_lines = []
            return
        if pin is not None:
            pin.shape_lines = shape_lines
            pin.outline = _surface_points(shape_lines, scale)
        else:
            pkg.outline = _surface_points(shape_lines, scale)
        shape_lines = []

    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip() if raw.lstrip().startswith("PKG") else raw.strip()
        if not line or line.startswith("#"):
            continue
        t = line.split()
        head = t[0]
        if head == "LYR":
            layers = [x.lower() for x in t[1:]]
        elif head == "SNT":
            # SNT TOP T|B cmp pin  (also SNT TRC / VIA, which carry no component)
            if len(t) >= 5 and t[1] == "TOP":
                current_snt = (t[2], int(t[3]), int(t[4]))
            else:
                current_snt = None
        elif head == "FID" and current_snt is not None and len(t) >= 4:
            lyr = int(t[2])
            name = layers[lyr] if 0 <= lyr < len(layers) else ""
            fids.setdefault(current_snt, []).append((t[1], name, int(t[3])))
        elif head == "PKG":
            flush()
            pkg, pin = _Package(t[1]), None
            packages.append(pkg)
        elif head == "PIN" and pkg is not None:
            flush()
            pin = _Pin(t[1], t[2], float(t[3]) * scale, float(t[4]) * scale, float(t[5]) * scale if len(t) > 5 else 0.0)
            pkg.pins.append(pin)
        elif head in ("CT", "OB", "OS", "OC", "OE", "CE", "RC", "CR", "SQ", "CT"):
            shape_lines.append(line)
        elif head in ("NET", "SNT", "FGR", "PRP"):
            pass
    flush()
    return _Eda(units, layers, packages, fids)


def _pin_outline_shape(pin: _Pin, scale: float) -> tuple[Shape, Point] | None:
    """A pin's own shape records: RC (rect), CR (circle), SQ (square), CT contour."""
    for line in pin.shape_lines:
        t = line.split()
        if t[0] == "RC":  # RC llx lly width height
            x, y, w, h = (float(v) * scale for v in t[1:5])
            return Shape("rect", (w, h)), (x + w / 2, y + h / 2)
        if t[0] == "CR":  # CR xc yc radius
            x, y, r = (float(v) * scale for v in t[1:4])
            return Shape("circle", (2 * r, 2 * r)), (x, y)
        if t[0] == "SQ":  # SQ xc yc half-side
            x, y, hs = (float(v) * scale for v in t[1:4])
            return Shape("rect", (2 * hs, 2 * hs)), (x, y)
    if pin.outline:
        return _polygon_shape(max(pin.outline, key=len))
    return None


# ─── Components ──────────────────────────────────────────────────────────────


@dataclass
class _Cmp:
    pkg: int
    x: float
    y: float
    rotation: float
    mirror: bool
    ref: str
    part: str
    attrs: str
    props: dict = field(default_factory=dict)
    toeprints: list[tuple[int, float, float, str]] = field(default_factory=list)


def _components(text: str) -> tuple[str, list[_Cmp], list[str]]:
    units = _units(text)
    scale = _UNIT_MM[units]
    attr_names: list[str] = []
    out: list[_Cmp] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("@"):
            attr_names.append(line.split(None, 1)[1] if " " in line else "")
            continue
        if not line or line.startswith("#"):
            continue
        t = line.split()
        if t[0] == "CMP":
            body, _, attrs = line.partition(";")
            b = body.split()
            out.append(
                _Cmp(
                    int(b[1]),
                    float(b[2]) * scale,
                    float(b[3]) * scale,
                    float(b[4]),
                    b[5].upper() == "M",
                    b[6],
                    b[7] if len(b) > 7 else "",
                    attrs.strip(),
                )
            )
        elif t[0] == "PRP" and out:
            m = re.match(r"PRP\s+(\S+)\s+'(.*)'", line)
            if m:
                out[-1].props[m.group(1)] = m.group(2)
        elif t[0] == "TOP" and out:
            out[-1].toeprints.append((int(t[1]), float(t[2]) * scale, float(t[3]) * scale, t[8] if len(t) > 8 else ""))
    return units, out, attr_names


_MOUNT_OPTIONS = ("", "smd", "tht", "tht")  # .comp_mount_type: other, smt, thmt, pressfit


def _attributes(cmp: _Cmp, attr_names: list[str]) -> dict[str, str]:
    """A record's attributes by name: `;0=1,1` is {names[0]: '1', names[1]: ''}."""
    out = {}
    for part in cmp.attrs.split(","):
        part = part.strip()
        if not part:
            continue
        num, _, value = part.partition("=")
        try:
            out[attr_names[int(num)]] = value
        except (ValueError, IndexError):
            continue
    return out


def _mount(cmp: _Cmp, attr_names: list[str]) -> str:
    value = _attributes(cmp, attr_names).get(".comp_mount_type")
    if value is None:
        return ""
    try:
        return _MOUNT_OPTIONS[int(value)]
    except (ValueError, IndexError):
        return ""


def _height(cmp: _Cmp, attr_names: list[str], scale: float) -> float | None:
    """`.comp_height` (the standard system attribute), in the file's units."""
    value = _attributes(cmp, attr_names).get(".comp_height")
    try:
        return round(float(value) * scale, 6) if value not in (None, "") else None
    except ValueError:
        return None


_DECIMALS = re.compile(r"(?<![\w.])-?\d+\.(\d+)")


def _resolution(texts: list[str], units: str) -> tuple[float, str]:
    """
    The coordinate step the job is written in: the most decimals of any coordinate on the
    package pins (eda/data PIN), component and toeprint records and pad features. ODB++ has no
    precision field; KiCad's `--precision` (2 by default) shows in the numbers.
    """
    decimals = 0
    for text in texts:
        for line in text.splitlines():
            head = line[:4]
            if head.startswith(("PIN ", "P ", "CMP ", "TOP ", "PKG ")):
                body = line.split(";", 1)[0]
                for m in _DECIMALS.finditer(body):
                    decimals = max(decimals, len(m.group(1)))
    decimals = min(decimals, 9)
    step = round(10.0**-decimals * _UNIT_MM[units], 12)
    return step, f"ODB++ coordinates: {decimals} decimals, {units}"


# ─── Reading ─────────────────────────────────────────────────────────────────


def read_components(source, *, step: str = "") -> Board:
    """An ODB++ job (directory, .zip, .tgz/.tar, or bytes of one) -> Board."""
    job = Job(source)
    steps = job.steps()
    if not steps:
        raise ValueError("ODB++ job has no steps")
    warnings: list[str] = []
    name = step.lower() if step else steps[0]
    if not step and len(steps) > 1:
        # Prefer the board-like step (pcb, board) over panels.
        preferred = [s for s in steps if s in ("pcb", "board")] or steps
        name = preferred[0]
        warnings.append(f"{len(steps)} steps {steps}; read {name!r}")
    base = f"steps/{name}"
    matrix = _matrix(job)
    eda_text = job.text(f"{base}/eda/data")
    if eda_text is None:
        raise ValueError(f"step {name!r} has no eda/data")
    eda = _eda(eda_text)
    eda_scale = _UNIT_MM[eda.units]

    feature_cache: dict[str, _Features | None] = {}

    def features(layer: str) -> _Features | None:
        if layer not in feature_cache:
            text = job.text(f"{base}/layers/{layer}/features")
            feature_cache[layer] = _features(text) if text is not None else None
        return feature_cache[layer]

    user_symbols: dict[str, tuple[Shape, Point] | None] = {}
    drills = _drill_hits(matrix, features)

    def symbol(name_: str, units: str) -> tuple[Shape, Point] | None:
        shape = symbol_shape(name_, _SYMBOL_UNIT_MM[units])
        if shape is not None:
            return shape, (0.0, 0.0)
        if name_ not in user_symbols:
            text = job.text(f"symbols/{name_}/features")
            user_symbols[name_] = None
            if text is not None:
                scale = _UNIT_MM[_units(text)]
                contours = _surface_points(text.splitlines(), scale)
                if contours:
                    user_symbols[name_] = _polygon_shape(max(contours, key=len))
            if user_symbols[name_] is None:
                warnings.append(f"symbol {name_!r} not understood")
        return user_symbols[name_]

    kicad = "kicad" in (job.text("misc/info") or "").lower()
    component_texts = [
        t for side in ("comp_+_top", "comp_+_bot") if (t := job.text(f"{base}/layers/{side}/components")) is not None
    ]
    copper_texts = [
        t
        for layer_name, layer in matrix.items()
        if layer.type == "SIGNAL" and (t := job.text(f"{base}/layers/{layer_name}/features")) is not None
    ]
    resolution, basis = _resolution([eda_text] + component_texts + copper_texts, eda.units)
    components: list[Component] = []
    for side_name, side_code in (("comp_+_top", "T"), ("comp_+_bot", "B")):
        text = job.text(f"{base}/layers/{side_name}/components")
        if text is None:
            continue
        _, cmps, attr_names = _components(text)
        for index, cmp in enumerate(cmps):
            if not 0 <= cmp.pkg < len(eda.packages):
                warnings.append(f"{cmp.ref}: package {cmp.pkg} not in eda/data")
                continue
            pkg = eda.packages[cmp.pkg]
            bottom = cmp.mirror or side_code == "B"
            placement, flip = _placement(cmp, pkg, bottom, kicad)
            pads = []
            for pin_index, pin in enumerate(pkg.pins):
                pads.append(
                    _pad(
                        pin,
                        pin_index,
                        eda.fids.get((side_code, index, pin_index), []),
                        matrix,
                        features,
                        symbol,
                        placement,
                        eda_scale,
                        warnings,
                        cmp.ref,
                        flip,
                        drills,
                    )
                )
            pads = _merge_stacked([p for p in pads if p is not None])
            if kicad:
                pads = [_unnumbered(p) for p in pads]
            _check(cmp, placement, pads, pkg, warnings, flip)
            mount = _mount(cmp, attr_names)
            mpns = tuple(
                v
                for k, v in cmp.props.items()
                if k.lower() in ("mpn", "mfr_pn", "manufacturer_part_number", "part_number") and v
            )
            footprint = Footprint(
                name=pkg.name,
                pads=tuple(pads),
                courtyard=tuple(Graphic("courtyard", "polygon", tuple(c)) for c in pkg.outline),
                fields=dict(cmp.props),
                source=FootprintSource(
                    kind="odbpp",
                    board=name,
                    reference=cmp.ref,
                    side=placement.side,
                    resolution=resolution,
                    resolution_basis=basis,
                ),
                mount=mount,
            )
            attributes = _attributes(cmp, attr_names)
            components.append(
                Component(
                    reference=cmp.ref,
                    part_number=cmp.part,
                    package_name=pkg.name,
                    placement=placement,
                    footprint=footprint,
                    mpns=mpns,
                    properties=dict(cmp.props),
                    mount=mount,
                    populate=".no_pop" not in attributes,
                    height=_height(cmp, attr_names, _UNIT_MM[_units(text)]),
                )
            )
    return Board(
        name=name, components=components, units=eda.units, source="odbpp", revision=_version(job), warnings=warnings
    )


#: KiCad's exporters name a pad that has no number 'PAD<index>' ('NPTH<index>' for a plain
#: hole).
_INVENTED = re.compile(r"^(PAD|NPTH)\d+$")


def _unnumbered(pad: Pad) -> Pad:
    """A KiCad pin name invented for an unnumbered pad: number None, the name kept."""
    if pad.number and _INVENTED.match(pad.number):
        return replace(pad, number=None, name=pad.number)
    return pad


def _version(job: Job) -> str:
    info = job.text("misc/info") or ""
    major = re.search(r"ODB_VERSION_MAJOR\s*=\s*(\d+)", info)
    minor = re.search(r"ODB_VERSION_MINOR\s*=\s*(\d+)", info)
    return f"{major.group(1)}.{minor.group(1) if minor else 0}" if major else ""


_KIND_LAYER = {"SIGNAL": "Cu", "POWER_GROUND": "Cu", "MIXED": "Cu", "SOLDER_PASTE": "Paste", "SOLDER_MASK": "Mask"}


def _placement(cmp: _Cmp, pkg: _Package, bottom: bool, kicad: bool) -> tuple[Placement, bool]:
    """
    The component's placement in the model's convention, and whether its package is stored
    mirrored.

    ODB++ rotations are clockwise. On the bottom, exporters differ, and the toeprints decide:

    * the format's own convention (Xpedition, Valor and other ODB++ exporters): the package is
      a top view, mirrored about the y axis, and its rotation is clockwise as seen from the
      bottom, i.e. model rotation 180 + r (checked on Siemens' designodb_rigidflex sample,
      where 180 - r misplaces an LQFP-64 by 11 mm);
    * KiCad 10: a separate, already mirrored package and rotation -r.

    When both fit (symmetric parts) the exporter named in misc/info breaks the tie.
    """

    def make(rotation: float) -> Placement:
        rotation = rotation % 360.0
        rotation = 0.0 if abs(rotation - 360.0) < 1e-9 else round(rotation, 6)
        return Placement(
            reference=cmp.ref,
            x=round(cmp.x, 6),
            y=round(cmp.y, 6),
            rotation=rotation,
            side="bottom" if bottom else "top",
            package=pkg.name,
        )

    if not bottom:
        return make(-cmp.rotation), False
    standard, mirrored = make(180.0 + cmp.rotation), make(-cmp.rotation)
    e_standard = _toeprint_error(cmp, standard, pkg, False)
    e_mirrored = _toeprint_error(cmp, mirrored, pkg, True)
    if abs(e_standard - e_mirrored) < 1e-4:
        return (mirrored, True) if kicad else (standard, False)
    return (standard, False) if e_standard < e_mirrored else (mirrored, True)


def _toeprint_error(cmp: _Cmp, placement: Placement, pkg: _Package, flip: bool) -> float:
    worst = 0.0
    for pin_num, x, y, _name in cmp.toeprints:
        if 0 <= pin_num < len(pkg.pins):
            pin = pkg.pins[pin_num]
            bx, by = placement.to_board((pin.x, -pin.y if flip else pin.y))
            worst = max(worst, math.dist((bx, by), (x, y)))
    return worst


def _drill_hits(matrix, features) -> list[tuple[Point, float, bool]]:
    """Every round hole on the drill layers: (board position mm, diameter mm, plated)."""
    out = []
    for name, layer in matrix.items():
        if layer.type != "DRILL":
            continue
        feats = features(name)
        if feats is None:
            continue
        scale = _UNIT_MM[feats.units]
        plated = not re.search(r"non[-_]?plated|npth|unplated", name)
        for f in feats.features:
            if f is None or f.contour:
                continue
            shape = symbol_shape(f.symbol, _SYMBOL_UNIT_MM[feats.units])
            if shape is not None:
                out.append(((f.x * scale, f.y * scale), shape.size[0], plated))
    return out


def _merge_stacked(pads: list[Pad]) -> list[Pad]:
    """Pads with the same number, center, shape and size drawn once per side become one
    pad with both sides' layers (how KiCad exports a pad soldered on both faces)."""
    out: list[Pad] = []
    for p in pads:
        twin = next(
            (
                i
                for i, q in enumerate(out)
                if q.number == p.number
                and math.dist(q.center, p.center) < 1e-4
                and q.shape == p.shape
                and q.size == p.size
            ),
            None,
        )
        if twin is None:
            out.append(p)
            continue
        q = out[twin]
        kinds: dict[str, set[str]] = {}
        for layer in q.layers + p.layers:
            side, kind = layer.split(".", 1)
            kinds.setdefault(kind, set()).add(side)
        layers = tuple(("*" if len(v) > 1 or "*" in v else next(iter(v))) + "." + k for k, v in kinds.items())
        out[twin] = replace(q, layers=layers, paste=q.paste or p.paste, mask=q.mask or p.mask)
    return out


def _pad(
    pin: _Pin,
    pin_index,
    fids,
    matrix,
    features,
    symbol,
    placement: Placement,
    eda_scale,
    warnings,
    ref,
    flip: bool = False,
    drills=(),
) -> Pad | None:
    bottom = placement.side == "bottom"
    center = (round(pin.x, 6), round(-pin.y if flip else pin.y, 6))
    copper = None
    drill = None
    sides: dict[str, set[str]] = {}
    paste: list[Aperture] = []
    mask = None
    has_paste_layer = any(la.type == "SOLDER_PASTE" for la in matrix.values())
    back_copper = None
    for ftype, layer_name, fnum in fids:
        layer = matrix.get(layer_name)
        feats = features(layer_name)
        if layer is None or feats is None or not 0 <= fnum < len(feats.features):
            continue
        feat = feats.features[fnum]
        if feat is None:
            continue
        scale = _UNIT_MM[feats.units]
        if feat.contour:
            pts = [placement.to_footprint((x * scale, y * scale)) for x, y in feat.contour]
            shape, at = _polygon_shape(pts)
            sym, rot = (shape, (0.0, 0.0)), 0.0
        else:
            sym = symbol(feat.symbol, feats.units)
            # Angle of this feature in the footprint frame (model CCW sense).
            if not bottom:
                rot = (-feat.rotation - placement.rotation) % 360.0
            elif flip:  # KiCad's mirrored package
                rot = (placement.rotation - feat.rotation) % 360.0
            else:  # top-view package, mirrored on placement
                rot = (placement.rotation + feat.rotation) % 360.0
            rot = 0.0 if abs(rot - 360.0) < 1e-6 else round(rot, 4)
            at = placement.to_footprint((feat.x * scale, feat.y * scale))
        if ftype == "H" or layer.type == "DRILL":
            if sym is not None:
                plated = not re.search(r"non[-_]?plated|npth|unplated", layer_name)
                w, h = sym[0].size
                if sym[0].shape == "oval" and abs(w - h) > 1e-9:
                    # A slot: its own angle in the footprint frame for now; made relative to
                    # the pad below, once the pad's angle is known.
                    drill = Drill(size=(w, h), shape="oblong", plated=plated, rotation=rot)
                else:
                    drill = Drill(size=(w, w), plated=plated)
            continue
        kind = _KIND_LAYER.get(layer.type)
        if kind is None or sym is None:
            continue
        front = (layer.side != "bottom") != bottom if layer.side in ("top", "bottom") else True
        sides.setdefault(kind, set()).add("F" if front else "B")
        shape, offset = sym
        if kind == "Cu" and not front:
            back_copper = back_copper or (shape, offset, rot, at)
            continue
        if not front:
            continue
        if kind == "Cu" and (copper is None or math.dist(at, center) < math.dist(copper[3], center)):
            # boarddd: the copper feature nearest the pin, not the first listed (a thermal via pin of an
            # exposed pad lists the pad's copper too)
            copper = (shape, offset, rot, at)
        elif kind == "Paste":
            paste.append(
                Aperture(
                    shape.shape,
                    _r2(shape.size),
                    (round(at[0], 6), round(at[1], 6)),
                    rot,
                    shape.roundrect_ratio,
                    shape.polygon,
                )
            )
        elif kind == "Mask" and mask is None:
            mask = Aperture(
                shape.shape,
                _r2(shape.size),
                (round(at[0], 6), round(at[1], 6)),
                rot,
                shape.roundrect_ratio,
                shape.polygon,
            )
    if copper is None and back_copper is not None:
        copper = back_copper  # a pad only on the far side (edge-mount connectors)
    if copper is not None and copper[0].shape == "polygon":
        center = (round(copper[3][0], 6), round(copper[3][1], 6))
    if drill is None and pin.type == "T":
        board = placement.to_board(center)
        hit = min(drills, key=lambda h: math.dist(h[0], board), default=None)
        if hit is not None and math.dist(hit[0], board) < 0.01:
            drill = Drill(size=(hit[1], hit[1]), plated=hit[2])
    if copper is None and drill is not None and not drill.plated:
        # A bare non-plated hole: its outline is the hole.
        copper = (Shape("circle", drill.size), (0.0, 0.0), 0.0, center)
    if copper is None and paste:
        # A paste-only pad (KiCad's segmented paste under an exposed pad): its paste opening
        # is the only thing drawn for it.
        ap = paste[0]
        copper = (Shape(ap.shape, ap.size, ap.roundrect_ratio, polygon=ap.polygon), (0.0, 0.0), ap.rotation, ap.center)
        center = ap.center
    if copper is None:
        found = _pin_outline_shape(pin, eda_scale)
        if found is None:
            warnings.append(f"{ref} pin {pin.name}: no pad shape")
            return None
        shape, at = found
        if flip:
            shape = Shape(
                shape.shape,
                shape.size,
                shape.roundrect_ratio,
                shape.chamfer_ratio,
                shape.chamfered,
                tuple((x, -y) for x, y in shape.polygon),
            )
            at = (at[0], -at[1])
        copper = (shape, (0.0, 0.0), 0.0, at)
        center = (round(at[0], 6), round(at[1], 6))
        if fids:
            warnings.append(f"{ref} pin {pin.name}: shape from the package outline")
    shape, offset, rotation, copper_at = copper
    if drill is not None and shape.shape != "polygon" and fids and math.dist(copper_at, center) > 1e-3:
        # boarddd fix: copper drawn away from its hole (KiCad's (drill (offset)), e.g. a pin header's
        # offset rect pads). The pin is the hole; keep the copper where the feature is and record the hole
        # as the drill's offset from it, in the pad frame (magpie put the copper on the hole).
        dx, dy = center[0] - copper_at[0], center[1] - copper_at[1]
        c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
        drill = replace(drill, offset=(round(dx * c + dy * s, 6), round(-dx * s + dy * c, 6)))
        center = (round(copper_at[0], 6), round(copper_at[1], 6))
    if drill is not None and drill.shape == "oblong":
        drill = replace(drill, rotation=round((drill.rotation - rotation) % 360.0, 6) % 360.0)
    if drill is None and pin.type == "T" and pin.fhs > 0:
        drill = Drill(size=(pin.fhs, pin.fhs))
    kind = "smd"
    if pin.type == "T" or drill is not None:
        kind = "tht" if drill is None or drill.plated else "npth"
    polygon = shape.polygon
    if shape.shape == "polygon":
        c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
        polygon = tuple((round(x * c - y * s, 6), round(x * s + y * c, 6)) for x, y in polygon)
        rotation = 0.0
    layers = tuple(
        ("*" if len(v) > 1 else next(iter(v))) + "." + k for k in ("Cu", "Paste", "Mask") if (v := sides.get(k))
    )
    return Pad(
        number=pin.name or None,
        kind=kind,
        shape=shape.shape,
        size=(round(shape.size[0], 6), round(shape.size[1], 6)),
        center=(round(center[0] + offset[0], 6), round(center[1] + offset[1], 6)),
        rotation=rotation,
        roundrect_ratio=shape.roundrect_ratio,
        chamfer_ratio=shape.chamfer_ratio,
        chamfered=shape.chamfered,
        polygon=polygon,
        drill=drill,
        paste=(tuple(paste) if has_paste_layer else None),
        mask=mask,
        layers=layers,
    )


def _check(cmp: _Cmp, placement: Placement, pads: list[Pad], pkg: _Package, warnings, flip: bool = False) -> None:
    """Toeprint board positions against placement.to_board(pin center)."""
    worst = 0.0
    for pin_num, x, y, _name in cmp.toeprints:
        if 0 <= pin_num < len(pkg.pins):
            pin = pkg.pins[pin_num]
            bx, by = placement.to_board((pin.x, -pin.y if flip else pin.y))
            worst = max(worst, math.dist((bx, by), (x, y)))
    if worst > 0.02:
        warnings.append(
            f"{cmp.ref}: pins land {worst:.3f} mm from the file's toeprints; the "
            "rotation convention may differ from KiCad's"
        )


# ─── The whole board as boarddd.model (boarddd's own) ───────────────────────────────

#: Matrix TYPE -> Layer.role. DOCUMENT layers are told apart by name (KiCad's names survive).
_LAYER_ROLE = {
    "SIGNAL": "copper",
    "POWER_GROUND": "copper",
    "MIXED": "copper",
    "SOLDER_MASK": "mask",
    "SOLDER_PASTE": "paste",
    "SILK_SCREEN": "silk",
    "DRILL": "drill",
    "ROUT": "drill",
}
_DOCUMENT_ROLE = (
    (re.compile(r"edge[._]cuts|outline|profile"), "outline"),
    (re.compile(r"\.?fab$|assembly"), "fab"),
    (re.compile(r"courtyard|crtyd"), "courtyard"),
    (re.compile(r"adhes|glue"), "adhesive"),
)
#: `.drill` attribute values (ODB++ enumerations are written as their index).
_DRILL_ATTR = {
    "0": "plated",
    "1": "non_plated",
    "2": "via",
    "plated": "plated",
    "non_plated": "non_plated",
    "via": "via",
}


def read_odbpp(source, *, step: str = "", name: str | None = None) -> m.Board:
    """
    An ODB++ job (directory, .zip, .tgz/.tar, or bytes of one) -> `boarddd.model.Board`: components
    and their packages as footprints (`read_components`), the step profile as the outline, holes and
    slots of the drill layers, the layer matrix, per-layer stackup values (thickness, Er, Df,
    material) and the net names of eda/data.
    """
    if isinstance(source, (bytes, bytearray)):
        data, path = bytes(source), None
    else:
        path = Path(source)
        data = None if path.is_dir() else path.read_bytes()
    job = Job(data if data is not None else path)
    board = read_components(data if data is not None else path, step=step)
    footprints, components, warnings = eda.to_model(board)
    steps = job.steps()
    chosen = step.lower() if step else ([s for s in steps if s in ("pcb", "board")] or steps)[0]
    base = f"steps/{chosen}"
    matrix = _matrix(job)
    layers, copper_ids, ids = _model_layers(matrix)
    drills = _model_drills(job, base, matrix, ids, warnings)
    for layer in layers:
        plating = {d.plated for d in drills if d.layer == layer.id}
        if layer.role == "drill" and len(plating) == 1:
            layer.plated = plating.pop()
    info = job.text("misc/info") or ""
    app = re.search(r"^SAVE_APP=(.*)$", info, re.M) or re.search(r"^ODB_SOURCE=(.*)$", info, re.M)
    created = re.search(r"^CREATION_DATE=(\d{8})\.?(\d{6})?", info, re.M)
    job_name = re.search(r"^JOB_NAME=(.*)$", info, re.M)
    if path is not None and path.is_dir():
        files, digest = [], None
    else:
        digest = hashlib.sha256(data).hexdigest()
        files = [m.SourceFile(path=path.name if path else "job.zip", role="odb", sha256=digest)]
    stem = re.sub(r"[-_]odb(pp)?$", "", path.name.split(".")[0], flags=re.I) if path is not None else ""
    out = m.Board(
        name=name or (stem if stem else (job_name.group(1).strip() if job_name else chosen)),
        source=m.Source(
            kind="odbpp",
            files=files,
            generator=app.group(1).strip() if app else None,
            reader="boarddd.io.odbpp",
            created=_iso(created) if created else None,
        ),
        outline=_model_outline(job.text(f"{base}/profile"), warnings),
        stackup=_model_stackup(job, base, matrix, copper_ids, ids),
        layers=layers,
        drills=drills,
        footprints=footprints,
        components=components,
        nets=[m.Net(name=n) for n in _net_names(job.text(f"{base}/eda/data") or "")],
        warnings=board.warnings + warnings,
    )
    return out


def _iso(match) -> str:
    d, t = match.group(1), match.group(2)
    date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return f"{date}T{t[:2]}:{t[2:4]}:{t[4:6]}" if t else date


def _role(layer: _Layer) -> str | None:
    role = _LAYER_ROLE.get(layer.type)
    if role is None and layer.type == "DOCUMENT":
        role = next((r for rx, r in _DOCUMENT_ROLE if rx.search(layer.name)), "user")
    return role


def _named_side(layer: _Layer, role: str) -> str:
    if role == "copper":
        return layer.side
    if re.match(r"^(f\.|top|.*_top$)", layer.name):
        return "top"
    if re.match(r"^(b\.|bot|.*_bot(tom)?$)", layer.name):
        return "bottom"
    return layer.side if role in ("mask", "paste", "silk") else "none"


def _model_layers(matrix: dict[str, _Layer]) -> tuple[list[m.Layer], list[str], dict[str, str]]:
    """(layers, copper ids top to bottom, Layer.id by matrix name)."""
    ids: dict[str, str] = {}
    copper = sorted((la for la in matrix.values() if _role(la) == "copper"), key=lambda la: la.row)
    count = len(copper)
    out, seen = [], set()
    for layer in sorted(matrix.values(), key=lambda la: la.row):
        role = _role(layer)
        if role is None:
            continue
        side = _named_side(layer, role)
        index = copper.index(layer) + 1 if role == "copper" else None
        plated = False if role == "drill" and re.search(r"non[-_]?plated|npth|unplated", layer.name) else None
        if role in ("drill", "user") or (role != "copper" and side not in ("top", "bottom") and role != "outline"):
            lid = layer.name
        else:
            lid = eda_layer_id(role, side, index, count, plated=plated)
        base, k = lid, 2
        while lid in seen:
            lid, k = f"{base}-{k}", k + 1
        seen.add(lid)
        out.append(
            m.Layer(
                id=lid,
                role=role,
                side=side if side in ("top", "bottom", "inner") else "none",
                order=layer_order(role, side, index, count, plated=plated),
                files=[],
                format="odb",
                function=layer.type,
                plated=plated,
            )
        )
        ids[layer.name] = lid
    out.sort(key=sort_key)
    return out, [la.id for la in out if la.role == "copper"], ids


def _model_outline(text: str | None, warnings: list[str]) -> m.Outline | None:
    if not text:
        return None
    scale = _UNIT_MM[_units(text, "INCH")]
    islands, holes = [], []
    cur: list[Point] | None = None
    hole = False
    for raw in text.splitlines():
        t = raw.split()
        if not t:
            continue
        if t[0] == "OB":
            cur, hole = [(float(t[1]) * scale, float(t[2]) * scale)], len(t) > 3 and t[3].upper() == "H"
        elif t[0] == "OS" and cur is not None:
            cur.append((float(t[1]) * scale, float(t[2]) * scale))
        elif t[0] == "OC" and cur is not None:
            end = (float(t[1]) * scale, float(t[2]) * scale)
            center = (float(t[3]) * scale, float(t[4]) * scale)
            cur += _arc_points(cur[-1], end, center, len(t) > 5 and t[5].upper() == "Y")
        elif t[0] == "OE" and cur is not None:
            if len(cur) > 1 and math.dist(cur[0], cur[-1]) < 1e-9:
                cur.pop()
            if len(cur) >= 3:
                (holes if hole else islands).append(cur)
            cur = None
    if not islands:
        warnings.append("the step profile has no closed outline")
        return None
    board = max(islands, key=lambda pts: abs(_signed(pts)))
    if len(islands) > 1:
        warnings.append(f"the profile has {len(islands)} islands; the largest is the board")
    return m.Outline(board=_wind(board, True), cutouts=[_wind(h, False) for h in holes])


def _arc_points(start: Point, end: Point, center: Point, clockwise: bool) -> list[Point]:
    """An arc, flattened at 48 points per turn (as io.outline); the end point included."""
    cx, cy = center
    radius = math.hypot(start[0] - cx, start[1] - cy)
    a0, a1 = math.atan2(start[1] - cy, start[0] - cx), math.atan2(end[1] - cy, end[0] - cx)
    sweep = a1 - a0
    if clockwise:
        while sweep >= 0:
            sweep -= 2 * math.pi
    else:
        while sweep <= 0:
            sweep += 2 * math.pi
    steps = max(2, int(abs(sweep) / (2 * math.pi) * 48) + 2)
    pts = [
        (cx + radius * math.cos(a0 + sweep * i / steps), cy + radius * math.sin(a0 + sweep * i / steps))
        for i in range(1, steps)
    ]
    return pts + [end]


def _signed(pts: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True)) / 2


def _wind(pts: list[Point], ccw: bool) -> list[tuple[float, float]]:
    out = [(eda.r(x), eda.r(y)) for x, y in pts]
    return out if (_signed(out) > 0) == ccw else out[::-1]


def _attr_values(text: str, names: list[str], attrs: str) -> dict[str, str]:
    """`;0=2,1=0` against the file's `@<n> <name>` and `&<n> <text>` tables."""
    strings = {int(n): s for n, s in re.findall(r"^&(\d+)\s+(.*)$", text, re.M)}
    out = {}
    for part in attrs.split(","):
        key, _, value = part.strip().partition("=")
        if key.isdigit() and int(key) < len(names):
            out[names[int(key)]] = strings.get(int(value), value) if names[int(key)] == ".geometry" else value
    return out


def _model_drills(
    job: Job, base: str, matrix: dict[str, _Layer], ids: dict[str, str], warnings: list[str]
) -> list[m.Drill]:
    out: list[m.Drill] = []
    for layer in sorted(matrix.values(), key=lambda la: la.row):
        if layer.type not in ("DRILL", "ROUT"):
            continue
        text = job.text(f"{base}/layers/{layer.name}/features")
        if text is None:
            continue
        feats = _features(text)
        scale = _UNIT_MM[feats.units]
        names = [n for _, n in sorted((int(i), n) for i, n in re.findall(r"^@(\d+)\s+(\S+)", text, re.M))]
        default_plated = not re.search(r"non[-_]?plated|npth|unplated", layer.name)
        lid = ids.get(layer.name, layer.name)
        for f in feats.features:
            if f is None or f.contour:
                continue
            shape = symbol_shape(f.symbol, _SYMBOL_UNIT_MM[feats.units])
            if shape is None:
                warnings.append(f"{layer.name}: drill symbol {f.symbol!r} not understood")
                continue
            kind = _DRILL_ATTR.get(_attr_values(text, names, f.attrs).get(".drill", ""), "")
            plated = default_plated if not kind else kind != "non_plated"
            function = "via" if kind == "via" else ("component" if plated else None)
            x, y = f.x * scale, f.y * scale
            w, h = shape.size
            if shape.shape == "oval" and abs(w - h) > 1e-9:
                along = -f.rotation + (0.0 if w > h else 90.0)  # ODB++ rotations are clockwise
                half = (max(w, h) - min(w, h)) / 2
                dx, dy = half * math.cos(math.radians(along)), half * math.sin(math.radians(along))
                out.append(
                    m.Drill(
                        x=eda.r(x - dx),
                        y=eda.r(y - dy),
                        x2=eda.r(x + dx),
                        y2=eda.r(y + dy),
                        diameter=eda.r(min(w, h)),
                        plated=plated,
                        function=function,
                        layer=lid,
                    )
                )
            else:
                out.append(
                    m.Drill(x=eda.r(x), y=eda.r(y), diameter=eda.r(w), plated=plated, function=function, layer=lid)
                )
    return out


def _attrlist(job: Job, base: str, layer: str) -> dict[str, str]:
    text = job.text(f"{base}/layers/{layer}/attrlist") or ""
    return {k.strip().lower(): v.strip() for k, _, v in (ln.partition("=") for ln in text.splitlines() if "=" in ln)}


def _model_stackup(
    job: Job, base: str, matrix: dict[str, _Layer], copper_ids: list[str], ids: dict[str, str]
) -> m.Stackup:
    """Per-layer values from each layer's attrlist (KiCad writes them to 0.01 mm). The job states no
    overall thickness, finish or colours, so those stay unknown."""
    kinds = {
        "SIGNAL": "copper",
        "POWER_GROUND": "copper",
        "MIXED": "copper",
        "DIELECTRIC": "dielectric",
        "SOLDER_MASK": "mask",
        "SOLDER_PASTE": "paste",
        "SILK_SCREEN": "silk",
    }
    rows = sorted((la for la in matrix.values() if la.type in kinds), key=lambda la: la.row)
    out = []
    copper_index = 0

    def num(value: str | None) -> float | None:
        try:
            v = float(value) if value not in (None, "") else None
        except ValueError:
            return None
        return eda.r(v) if v is not None and v > 0 else None

    for la in rows:
        kind = kinds[la.type]
        attrs = _attrlist(job, base, la.name)
        side = la.side or "inner"
        layer = None
        if kind == "copper":
            layer = copper_ids[copper_index] if copper_index < len(copper_ids) else None
            copper_index += 1
        elif kind in ("mask", "paste", "silk"):
            side = _named_side(la, kind)
            layer = ids.get(la.name)
        material = attrs.get(".material")
        out.append(
            m.StackupLayer(
                name=la.name,
                kind=kind,
                side=side if side in ("top", "bottom", "inner") else "inner",
                thickness=num(attrs.get(".layer_dielectric")),
                material=material.upper()
                if material and material != "not_specified" and kind == "dielectric"
                else None,
                epsilon_r=num(attrs.get(".dielectric_constant")),
                loss_tangent=num(attrs.get(".loss_tangent")),
                layer=layer,
            )
        )
    return m.Stackup(copper_layers=len(copper_ids) or None, layers=out)


def _net_names(text: str) -> list[str]:
    names = {ln.split(None, 1)[1].strip() for ln in text.splitlines() if ln.startswith("NET ") and len(ln.split()) > 1}
    return sorted(n for n in names if n and n != "$NONE$")
