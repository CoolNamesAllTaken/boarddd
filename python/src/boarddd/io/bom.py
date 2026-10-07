"""
Reading a BOM export, from any tool. Pure functions -- no network, no database.

KiCad's layout is what the auto-detection recognizes out of the box -- but detection is only
ever a default for the mapping the user confirms.

A KiCad BOM export configured with the usual fields emits, in this order:

    Reference, Value, Footprint, Quantity, Populate, Standard Cost, Manufacturer, MPN,
    LCSC PN, Note

Every other tool emits the same facts under other names, and some of them not at all:

    Altium    Designator, Comment, Footprint, Quantity, Manufacturer Part Number -- as .xlsx
              by default, and often one designator per row
    EasyEDA   Comment, Designator, Footprint, LCSC Part #
    Eagle     Part, Value, Device, Package, Description -- no MPN anywhere in it
    Allegro   Part Reference, PCB Footprint, Part Number
    Excel     whatever somebody typed, separated by ';' in half the world

Only the designators are required. A quantity that is not stated is the number of
designators; an MPN that is not there means nothing matches by itself, and every line is
resolved by hand or by distributor search -- which is still an import, and the honest one.
Everything else is carried through for the human reviewing it, and Populate is the one field
with behavior attached.

A file with a bad row is rejected whole, naming the rows. A half-parsed import is worse than a
bounced upload: the user would have to work out which rows made it and which did not, and the
answer would be buried in a table of forty.

Source: `magpie/pcb/bom.py` at magpie commit 3a0374d3 (copied for boarddd phase F1; magpie's later
changes are re-diffed before magpie switches to this module).
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass

__all__ = [
    "ParsedRow",
    "ParseError",
    "Rows",
    "parse_csv",
    "parse_filename",
    "read_headers",
    "detect_columns",
    "find_header_row",
    "records",
    "decode",
    "spreadsheet_to_csv",
    "looks_like_bom",
    "FIELDS",
    "REQUIRED_HEADERS",
    "SPREADSHEET_EXTENSIONS",
]

REQUIRED_HEADERS = ("reference",)

# Anything in this set means "fitted on the board? no".
DNP_VALUES = {
    "dnp",
    "no",
    "false",
    "0",
    "n",
    "dnf",
    "dni",
    "not fitted",
    "not_fitted",
    "notfitted",
    "nofit",
    "no fit",
    "unfitted",
    "not populated",
    "not_populated",
    "do not populate",
    "do not fit",
    "do_not_populate",
    "do_not_fit",
    "omit",
    "exclude",
    "excluded",
    "none",
    "x",
    "nc",
}

# <ipn>-<rev>-<name>-bom.csv: 000123456-D-my_board-bom.csv  ->  ('000123456', 'D', 'my_board')
# Also tolerates the underscore form: 000123457-G_other_board-bom.csv
_FILENAME = re.compile(r"^(?P<ipn>\d{6,12})-(?P<rev>[A-Z0-9]{1,3})[-_](?P<name>.+?)[-_]bom\.\w+$", re.I)

_MAX_QUANTITY = 1_000_000
_DELIMITERS = (",", ";", "\t")
SPREADSHEET_EXTENSIONS = (".xlsx", ".xlsm", ".xls")


class ParseError(ValueError):
    """The file cannot be read. Carries every problem, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class ParsedRow:
    row_number: int
    reference: str = ""
    quantity: int = 1
    value: str = ""
    footprint: str = ""
    manufacturer: str = ""
    mpn: str = ""
    lcsc_pn: str = ""
    note: str = ""
    standard_cost: str = ""
    populate: bool = True

    @property
    def designators(self) -> list[str]:
        return split_designators(self.reference)


def split_designators(reference: str) -> list[str]:
    """`C1,C18`, `C1, C18`, `C1; C18` and `C1 C18` are all two designators."""
    return [d for d in re.split(r"[,;\s]+", reference or "") if d]


#: The fields an import can populate, in the order the mapping UI shows them. `label` is what
#: the reviewer sees; `required` fields must be mapped to something or the file cannot be read.
FIELDS = [
    ("reference", "Designators", True),
    ("quantity", "Quantity", False),
    ("mpn", "MPN", False),
    ("value", "Value", False),
    ("footprint", "Footprint", False),
    ("manufacturer", "Manufacturer", False),
    ("lcsc_pn", "Distributor PN", False),
    ("populate", "Populate", False),
    ("standard_cost", "Cost", False),
    ("note", "Note", False),
]


def _normalize(header: str) -> str:
    """'LCSC PN' and 'lcsc_pn' and ' MPN ' should all land on the same key."""
    return re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")


# Normalized header -> ParsedRow field. Many spellings map to one field because every tool
# names these columns differently, exporters rename them over the years, and
# old files still need to import. A header is normalized before lookup -- lowercased, runs of
# punctuation and spaces become one underscore -- so `Mfr. Part #` is `mfr_part`.
COLUMNS = {
    # designators
    "reference": "reference",
    "references": "reference",
    "designator": "reference",
    "designators": "reference",
    "refdes": "reference",
    "ref": "reference",
    "refs": "reference",
    "ref_des": "reference",
    "reference_designator": "reference",
    "reference_designators": "reference",
    "part_reference": "reference",
    "part_references": "reference",
    "parts": "reference",
    "part": "reference",
    "components": "reference",
    "component": "reference",
    "ref_designators": "reference",
    "reference_s": "reference",
    "designator_s": "reference",
    # quantity
    "quantity": "quantity",
    "qty": "quantity",
    "qnty": "quantity",
    "count": "quantity",
    "quantities": "quantity",
    "q": "quantity",
    "quantity_per_board": "quantity",
    "qty_per_board": "quantity",
    "total_qty": "quantity",
    "total_quantity": "quantity",
    "amount": "quantity",
    # value
    "value": "value",
    "val": "value",
    "values": "value",
    "comment": "value",
    "part_value": "value",
    "comp_value": "value",
    "component_value": "value",
    # footprint
    "footprint": "footprint",
    "package": "footprint",
    "pcb_footprint": "footprint",
    "pattern": "footprint",
    "footprints": "footprint",
    "case": "footprint",
    "case_package": "footprint",
    "package_case": "footprint",
    "footprint_name": "footprint",
    "sym_name": "footprint",
    "land_pattern": "footprint",
    # fitted or not
    "populate": "populate",
    "populated": "populate",
    "fitted": "populate",
    "fit": "populate",
    "place": "populate",
    "placed": "populate",
    "install": "populate",
    "installed": "populate",
    "assemble": "populate",
    "mount": "populate",
    "mounted": "populate",
    "include": "populate",
    "included": "populate",
    "in_bom": "populate",
    "dnp": "_dnp",
    "dnf": "_dnp",
    "dni": "_dnp",
    "do_not_populate": "_dnp",
    "do_not_fit": "_dnp",
    "not_fitted": "_dnp",
    "no_pop": "_dnp",
    "exclude": "_dnp",
    "exclude_from_bom": "_dnp",
    "excluded": "_dnp",
    "omit": "_dnp",
    "not_populated": "_dnp",
    # cost
    "standard_cost": "standard_cost",
    "cost": "standard_cost",
    "unit_cost": "standard_cost",
    "unit_price": "standard_cost",
    "price": "standard_cost",
    "cost_each": "standard_cost",
    "unit_price_usd": "standard_cost",
    "price_each": "standard_cost",
    # manufacturer
    "manufacturer": "manufacturer",
    "mfr": "manufacturer",
    "mfg": "manufacturer",
    "manufacturer_name": "manufacturer",
    "mfr_name": "manufacturer",
    "mfg_name": "manufacturer",
    "manufacturer_1": "manufacturer",
    "mfr_1": "manufacturer",
    "mfg_1": "manufacturer",
    "brand": "manufacturer",
    "maker": "manufacturer",
    "vendor": "manufacturer",
    "manufacturer_s": "manufacturer",
    "mf": "manufacturer",  # Eagle / Fusion 360 attribute
    # manufacturer part number
    "mpn": "mpn",
    "manufacturer_part_number": "mpn",
    "mfr_pn": "mpn",
    "mfg_pn": "mpn",
    "mfr_part_number": "mpn",
    "mfg_part_number": "mpn",
    "mfr_part": "mpn",
    "mfg_part": "mpn",
    "mfr_p_n": "mpn",
    "mfg_p_n": "mpn",
    "manufacturer_p_n": "mpn",
    "part_number": "mpn",
    "partnumber": "mpn",
    "part_no": "mpn",
    "part_num": "mpn",
    "manufacturer_pn": "mpn",
    "manufacturer_part": "mpn",
    "manufacturer_part_no": "mpn",
    "manufacturer_part_num": "mpn",
    "mfr_part_no": "mpn",
    "mfr_part_num": "mpn",
    "mfg_part_no": "mpn",
    "mfg_part_num": "mpn",
    "mpn_1": "mpn",
    "manufacturer_part_number_1": "mpn",
    "mfr_part_number_1": "mpn",
    "pn": "mpn",
    "p_n": "mpn",
    "mfr_no": "mpn",
    "mfg_no": "mpn",
    "manufacturer_number": "mpn",
    "manufacturer_part_1": "mpn",
    "orderable_mpn": "mpn",
    "mfrpn": "mpn",
    "mfgpn": "mpn",
    "comp_part_number": "mpn",
    "part_id": "mpn",
    # a distributor's own number
    "lcsc_pn": "lcsc_pn",
    "lcsc": "lcsc_pn",
    "lcsc_part": "lcsc_pn",
    "lcsc_part_number": "lcsc_pn",
    "jlcpcb_part": "lcsc_pn",
    "lcsc_part_1": "lcsc_pn",
    "jlcpcb_part_1": "lcsc_pn",
    "jlc_part": "lcsc_pn",
    "jlcpcb_part_number": "lcsc_pn",
    "supplier_part": "lcsc_pn",
    "supplier_part_number": "lcsc_pn",
    "supplier_part_number_1": "lcsc_pn",
    "supplier_pn": "lcsc_pn",
    "supplier_part_1": "lcsc_pn",
    "distributor_pn": "lcsc_pn",
    "distributor_part_number": "lcsc_pn",
    "distributor_part": "lcsc_pn",
    "digikey_part_number": "lcsc_pn",
    "digi_key_part_number": "lcsc_pn",
    "digikey_pn": "lcsc_pn",
    "digikey": "lcsc_pn",
    "digi_key": "lcsc_pn",
    "mouser_part_number": "lcsc_pn",
    "mouser_no": "lcsc_pn",
    "mouser_pn": "lcsc_pn",
    "mouser": "lcsc_pn",
    "sku": "lcsc_pn",
    "order_number": "lcsc_pn",
    "order_no": "lcsc_pn",
    "supplier_sku": "lcsc_pn",
    "vendor_part_number": "lcsc_pn",
    "vendor_pn": "lcsc_pn",
    # free text
    "note": "note",
    "notes": "note",
    "description": "note",
    "desc": "note",
    "part_description": "note",
    "component_description": "note",
    "remark": "note",
    "remarks": "note",
}


#: Text fields whose blank in the detected column is filled from another column of the same
#: meaning (see `parse_csv`).
FALLBACK_FIELDS = ("manufacturer", "mpn", "value", "footprint", "lcsc_pn")

# Headers too generic to trust when the file has a better one for the same field: EasyEDA's
# BOM calls the value `Name`, and Altium templates use `Name` for the component's name.
WEAK_COLUMNS = {"name": "value", "device": "footprint"}


def _strip_bom(text: str) -> str:
    # KiCad and Excel both emit a BOM, and a stray ﻿ on the first header makes 'Reference'
    # silently unrecognized -- which then reads as "missing required column".
    return text[1:] if text.startswith("﻿") else text


def decode(data: bytes) -> str:
    """
    The file as text. UTF-8 first, with the Excel/KiCad byte-order mark dropped; failing
    that, Windows-1252, which is what Altium and Excel-on-Windows actually write when a
    value has a µ or an Ω in it. Never `errors='replace'` on the first try: a file that is
    genuinely cp1252 decodes cleanly as cp1252 and would come through UTF-8 with every
    accented character replaced.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def spreadsheet_to_csv(data: bytes, name: str = "") -> str:
    """
    A .xlsx or .xls BOM as CSV text, so the rest of this module reads it like any other.

    The sheet with the most recognizable column names wins -- Altium's template puts a cover
    sheet first -- and formulas come back as their computed values. Raises ParseError when
    the spreadsheet cannot be opened or the library to open it is not installed.
    """
    sheets = _sheets(data, name)
    if not sheets:
        raise ParseError(["The spreadsheet has no rows in it."])
    best = max(sheets, key=lambda rows: _header_score(rows)[1])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in best:
        writer.writerow(["" if cell is None else _cell_text(cell) for cell in row])
    return out.getvalue()


def _cell_text(cell) -> str:
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def _sheets(data: bytes, name: str) -> list[list[list]]:
    lowered = (name or "").lower()
    if lowered.endswith(".xls"):
        try:
            import xlrd
        except ImportError:
            raise ParseError([".xls spreadsheets need the xlrd library; save it as .xlsx or CSV."]) from None
        try:
            book = xlrd.open_workbook(file_contents=data)
        except Exception as exc:  # xlrd raises its own hierarchy
            raise ParseError([f"The spreadsheet could not be opened: {exc}"]) from exc
        return [[sheet.row_values(r) for r in range(sheet.nrows)] for sheet in book.sheets()]
    try:
        import openpyxl
    except ImportError:
        raise ParseError(["Spreadsheets need the openpyxl library; save it as CSV instead."]) from None
    _check_unpacked_size(data)
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError([f"The spreadsheet could not be opened: {exc}"]) from exc
    try:
        budget = [MAX_SPREADSHEET_CELLS]
        return [_sheet_rows(sheet, budget) for sheet in book.worksheets]
    finally:
        book.close()


#: The most of a spreadsheet a BOM is read from. A real BOM is a few hundred lines by a couple
#: of dozen columns; these are two orders of magnitude past that, and there only to bound what
#: a hostile file can make the worker hold.
MAX_SPREADSHEET_ROWS = 10_000
MAX_SPREADSHEET_COLUMNS = 256
MAX_SPREADSHEET_CELLS = 1_000_000
#: What an .xlsx may unpack to, all parts together. A BOM of MAX_SPREADSHEET_ROWS rows is a
#: few MB of sheet XML.
MAX_SPREADSHEET_UNPACKED = 50 * 1024 * 1024
_TOO_BIG = " Delete the unused rows and columns, or save the BOM sheet as CSV."


def _check_unpacked_size(data: bytes) -> None:
    """
    Refuse an .xlsx whose parts unpack to more than MAX_SPREADSHEET_UNPACKED.

    The sizes are the zip's own; zipfile never hands openpyxl more than a member says it holds,
    so the stated size bounds what can be unpacked. A file that is not a zip is left for
    openpyxl to say so.
    """
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unpacked = sum(member.file_size for member in archive.infolist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return
    if unpacked > MAX_SPREADSHEET_UNPACKED:
        raise ParseError(
            [f"The spreadsheet unpacks to {unpacked // (1024 * 1024)} MB, more than a BOM can be." + _TOO_BIG]
        )


def _sheet_rows(sheet, budget: list[int]) -> list[list]:
    """
    One sheet's rows, read no further than the caps above.

    openpyxl pads every row out to the sheet's declared `<dimension>` and yields a row for
    every row number up to it, present or not. That declaration is the file's word, and one
    four-KB file saying `A1:XFD1048576` with a single cell at the far corner took gigabytes
    to read -- in a real upload. So a sheet that declares more than
    the caps is refused with a message saying so; the declaration is then thrown away
    (`reset_dimensions`) and the rows are read with explicit limits, so a file that declares
    less than it holds cannot get past them either. The rows are cut to the sheet's widest
    real row, the width the declaration would have given an honest file.
    """
    title = sheet.title
    declared_rows, declared_columns = sheet.max_row or 0, sheet.max_column or 0
    if declared_rows > MAX_SPREADSHEET_ROWS or declared_columns > MAX_SPREADSHEET_COLUMNS:
        raise ParseError(
            [
                f'The sheet "{title}" is {declared_rows:,} rows by '
                f"{declared_columns:,} columns; a BOM is read from at most "
                f"{MAX_SPREADSHEET_ROWS:,} rows and {MAX_SPREADSHEET_COLUMNS} "
                f"columns." + _TOO_BIG
            ]
        )
    sheet.reset_dimensions()
    rows: list[list] = []
    width = 0
    for number, row in enumerate(
        sheet.iter_rows(max_row=MAX_SPREADSHEET_ROWS + 1, max_col=MAX_SPREADSHEET_COLUMNS, values_only=True), start=1
    ):
        # An empty row -- and up to the cap, openpyxl makes one for every missing row number --
        # is recognized at C speed; only rows with something in them are trimmed one by one.
        cells = [] if row.count(None) == len(row) else list(row)
        while cells and cells[-1] is None:
            cells.pop()
        if number > MAX_SPREADSHEET_ROWS:
            if cells:
                raise ParseError([f'The sheet "{title}" has more than {MAX_SPREADSHEET_ROWS:,} rows.' + _TOO_BIG])
            break
        budget[0] -= len(cells) + 1
        if budget[0] < 0:
            raise ParseError([f"The spreadsheet has more than {MAX_SPREADSHEET_CELLS:,} cells." + _TOO_BIG])
        width = max(width, len(cells))
        rows.append(cells)
    while rows and not rows[-1]:
        rows.pop()
    # Padding every row out to the width is cells too.
    budget[0] -= sum(width - len(cells) for cells in rows)
    if budget[0] < 0:
        raise ParseError([f"The spreadsheet has more than {MAX_SPREADSHEET_CELLS:,} cells." + _TOO_BIG])
    return [cells + [None] * (width - len(cells)) for cells in rows]


def _header_score(rows: list[list]) -> tuple[int, int]:
    """(row index, how many known column names it carries) of the best header row."""
    best, best_score = 0, 0
    for index, row in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        score = sum(1 for cell in row if cell is not None and _normalize(str(cell)) in COLUMNS)
        if score > best_score:
            best, best_score = index, score
    return best, best_score


def _delimiter(text: str) -> str:
    """
    Whatever separates the columns: a comma, or the semicolon Excel uses in locales where
    the comma is the decimal point, or a tab.

    Counted over the first few lines rather than sniffed with csv.Sniffer, which guesses
    from quoting and gets a quoted KiCad export wrong.
    """
    sample = "\n".join(text.splitlines()[:_HEADER_SEARCH_ROWS])
    counts = {d: sample.count(d) for d in _DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def records(text: str) -> list[list[str]]:
    """Every row of the file as a list of cells, whatever the delimiter."""
    text = _strip_bom(text)
    return list(csv.reader(io.StringIO(text), delimiter=_delimiter(text)))


#: How far into a file to look for the header. Anything with a title block longer than this is
#: a document with a table in it rather than a BOM, and guessing further would start finding
#: rows of data that happen to mention "quantity".
_HEADER_SEARCH_ROWS = 25


def find_header_row(text: str) -> int:
    """
    Which row the column names are on, counting from zero.

    Usually the first, and often not: an exporter that puts a title block above the table --
    project name, revision, date, a blank line -- is entirely ordinary, and read as a header
    that block gives one column called something like "Bill of Materials" and nothing else.
    That is not a mapping problem, because there is nothing on the row to map, which is why
    this is a separate question from which column is which.

    Scored rather than pattern-matched: the row that names the most fields we recognize wins.
    A title block names none, and a data row names none either -- `C1` and `100nF` are not
    column names -- so the header stands out from both without having to describe either.
    """
    return _header_score(records(text))[0]


def read_headers(text: str, header_row: int | None = None) -> list[str]:
    """
    The file's own column names, exactly as written.

    `header_row` counts from zero. Left out, the row is found by looking for the one that names
    the most fields we know.
    """
    rows = records(text)
    at = _header_score(rows)[0] if header_row is None else max(0, int(header_row))
    if at < len(rows):
        return [h.strip() for h in rows[at]]
    return []


def detect_columns(text: str, header_row: int | None = None) -> dict[str, str]:
    """
    Guess {field: header} from the header row.

    This recognizes the layouts we see most (KiCad's among them) as a starting point -- every
    guess is shown in the mapping UI and can be overridden, because "we support one CSV layout"
    is not a property anyone can rely on.
    """
    mapping: dict[str, str] = {}
    headers = read_headers(text, header_row)
    for header in headers:
        field_name = COLUMNS.get(_normalize(header))
        if field_name == "_dnp":
            mapping.setdefault("_dnp", header)
        elif field_name and field_name not in mapping:
            mapping[field_name] = header
    for header in headers:
        field_name = WEAK_COLUMNS.get(_normalize(header))
        if field_name and field_name not in mapping:
            mapping[field_name] = header
    return mapping


def looks_like_bom(text: str) -> bool:
    """
    Whether a header row in here names designators and at least one thing about the part.

    Designators alone are not enough: a placement file has them too. What makes a BOM is a
    designator column beside a value, a part number, a manufacturer or a quantity.
    """
    found = detect_columns(text)
    return "reference" in found and any(f in found for f in ("mpn", "value", "manufacturer", "quantity", "lcsc_pn"))


class Rows(list):
    """
    The parts read out of a file, and where reading stopped.

    A list, because everything that has ever taken these treats them as one and the extra is
    genuinely incidental -- but reading stops somewhere, and the two facts belong together.
    Returning a pair instead would rewrite every caller and every test to carry a value most
    of them do not want.

    `stopped_at` is the file's own row number, or None when the file simply ran out.
    `consolidated` is how many one-designator rows were folded into the line for their part.
    """

    stopped_at: int | None = None
    stop_reason: str = ""
    consolidated: int = 0

    def stop(self, row_number: int, reason: str) -> None:
        self.stopped_at = row_number
        self.stop_reason = reason


def parse_csv(text: str, column_map: dict[str, str] | None = None, header_row: int | None = None) -> list[ParsedRow]:
    """
    Parse a whole export, or raise ParseError listing everything wrong with it.

    ``column_map`` is {field: header}. Omit it to auto-detect the common layouts;
    pass one to read a layout we have never seen.

    ``header_row`` counts from zero and says where the column names are. Omit it and the row
    is found by looking. Everything above it is dropped -- a title block is not data, and the
    row numbers reported afterwards are the file's own, so a problem on row 14 is on row 14 of
    the file somebody is looking at rather than of the table inside it.
    """
    text = _strip_bom(text)

    # Read as records rather than through DictReader, which skips empty rows outright. That
    # skipping is the whole difficulty here twice over: a blank row is how a table usually ends
    # and would never arrive, and every row after one would be reported a line early -- the
    # numbers here are the file's own, so that a complaint about row 14 points at row 14 of the
    # file somebody has open.
    all_records = records(text)
    at = find_header_row(text) if header_row is None else max(0, int(header_row))
    if at >= len(all_records):
        raise ParseError(["The file is empty."])

    fieldnames = [(h or "").strip() for h in all_records[at]]
    if not any(fieldnames):
        raise ParseError(["The file is empty."])

    chosen = column_map if column_map is not None else detect_columns(text, at)
    # Invert to {header: field}, ignoring blanks (the mapping UI sends "" for "not mapped") and
    # anything naming a column the file does not actually have.
    available = set(fieldnames)
    mapping = {header: field for field, header in chosen.items() if header and header in available}
    # Where each named column sits. A file with the same header twice keeps the later one,
    # which is what DictReader did and is as good an answer as any.
    column_at = {header: index for index, header in enumerate(fieldnames)}
    # Auto-detected only: the other columns that mean the same text field. A schematic export
    # often carries a manufacturer field under two names (`MANUFACTURER` empty on most parts,
    # `Manufacturer` filled on a few), and a row's blank in the first is filled from the next.
    fallbacks: dict[str, list[str]] = {}
    if column_map is None:
        for header in fieldnames:
            field_name = COLUMNS.get(_normalize(header))
            if field_name in FALLBACK_FIELDS and header not in mapping and chosen.get(field_name) not in (None, header):
                fallbacks.setdefault(field_name, []).append(header)

    present = set(mapping.values())
    if "_dnp" in present:
        present.add("populate")
    missing = [h for h in REQUIRED_HEADERS if h not in present]
    if missing:
        raise ParseError(
            [f"Nothing is mapped to: {', '.join(missing)}. The file's columns are: {', '.join(fieldnames)}"]
        )
    counted = "quantity" in present

    rows = Rows()
    problems: list[str] = []

    # Row numbers are the file's own, counting the title block that was dropped: a complaint
    # about row 14 should point at row 14 of the file somebody has open.
    for offset, record in enumerate(all_records[at + 1 :], start=at + 2):
        cells = [(cell or "").strip() for cell in record]
        if not any(cells):
            rows.stop(offset, "a blank row")
            break

        row = ParsedRow(row_number=offset)
        dnp_flag = False
        blanks: list[str] = []
        for column, field_name in mapping.items():
            index = column_at[column]
            value = cells[index] if index < len(cells) else ""
            if field_name == "quantity":
                if not value:
                    blanks.append("quantity")
                else:
                    parsed = _quantity(value, offset, problems)
                    if parsed is not None:
                        row.quantity = parsed
            elif field_name == "populate":
                if value:
                    row.populate = value.strip().lower() not in DNP_VALUES
            elif field_name == "_dnp":
                # A dedicated DNP column means the opposite of Populate.
                dnp_flag = value.strip().lower() not in ("", "no", "false", "0", "n")
            else:
                setattr(row, field_name, value[:500])
        if dnp_flag:
            row.populate = False
        for field_name, headers in fallbacks.items():
            if not getattr(row, field_name):
                for header in headers:
                    index = column_at[header]
                    if index < len(cells) and cells[index]:
                        setattr(row, field_name, cells[index][:500])
                        break

        # Designators as one comma-separated list, however the file separated them. The
        # reference is written to InvenTree as-is, and `C1; C2` is not what anybody wants there.
        designators = split_designators(row.reference)
        row.reference = ",".join(designators)
        if not designators:
            blanks.insert(0, "reference")
        elif not counted:
            # No quantity column: a line is as many as it names. That is what the column
            # says in every export that has one.
            row.quantity = len(designators)

        # A row that does not carry the fields a part must have is not a part. Nearly always
        # it is the bottom of the table -- a total, a signature line, a note about the
        # revision -- and the table is over. Rows already read are kept and the rest is
        # ignored, which is the difference between an import somebody can use and an error
        # message about row 214 of a file whose parts all read perfectly.
        if blanks:
            rows.stop(offset, "no " + " or ".join(blanks))
            break

        rows.append(row)

    if problems:
        raise ParseError(problems)
    if not rows:
        raise ParseError(["The file has a header but no rows."])
    return _consolidate(rows)


def _identity(row: ParsedRow) -> tuple:
    """What makes two one-designator rows the same part."""
    if row.mpn.strip():
        return ("mpn", row.mpn.strip().lower(), row.populate)
    if row.lcsc_pn.strip():
        return ("lcsc", row.lcsc_pn.strip().lower(), row.populate)
    return (
        "value",
        row.value.strip().lower(),
        row.footprint.strip().lower(),
        row.manufacturer.strip().lower(),
        row.populate,
    )


def _consolidate(rows: Rows) -> Rows:
    """
    Fold a one-row-per-designator export into one line per part.

    Altium's flat BOM and Allegro's both list `C1 … 100nF`, `C2 … 100nF`, `C3 … 100nF` as three
    rows. Imported as three lines they would go onto the assembly as three BOM lines for one
    component -- which the apply step then flags as a conflict on every later import, forever.
    Grouped by MPN (or, failing that, by value, footprint and manufacturer) they become the
    one line KiCad would have written.

    Only when the file is entirely that shape: every row names exactly one designator and
    counts one of it. A grouped export never has both properties, so it is left exactly as
    written, and the row numbers on the folded lines are those of their first row.
    """
    if len(rows) < 2:
        return rows
    if any(len(row.designators) != 1 or row.quantity != 1 for row in rows):
        return rows

    grouped: dict[tuple, ParsedRow] = {}
    for row in rows:
        key = _identity(row)
        first = grouped.get(key)
        if first is None:
            grouped[key] = row
            continue
        first.reference = ",".join(first.designators + row.designators)
        first.quantity += 1
        for field_name in ("note", "standard_cost", "value", "footprint", "manufacturer"):
            if not getattr(first, field_name) and getattr(row, field_name):
                setattr(first, field_name, getattr(row, field_name))
    if len(grouped) == len(rows):
        return rows

    folded = Rows(grouped.values())
    folded.stopped_at, folded.stop_reason = rows.stopped_at, rows.stop_reason
    folded.consolidated = len(rows) - len(folded)
    return folded


def _quantity(value: str, row_number: int, problems: list[str]) -> int | None:
    if not value:
        problems.append(f"row {row_number}: quantity is blank")
        return None
    try:
        # Some exporters write '2.0'; accept it as long as it is a whole number.
        number = float(value.replace(",", ""))
    except ValueError:
        problems.append(f"row {row_number}: quantity {value!r} is not a number")
        return None
    # float() also reads 'inf', 'nan' and '1e400' (which overflows to inf), and int() of
    # any of them raises OverflowError or ValueError -- outside the try above, and every
    # caller catches only ParseError, so one such cell was a 500 on ingest and on every
    # re-read of the package. They are not quantities; say so like any other bad cell.
    if not math.isfinite(number):
        problems.append(f"row {row_number}: quantity {value!r} is not a number")
        return None
    if number != int(number) or number < 1:
        problems.append(f"row {row_number}: quantity {value!r} must be a whole number of 1 or more")
        return None
    if number > _MAX_QUANTITY:
        problems.append(f"row {row_number}: quantity {value!r} is implausibly large")
        return None
    return int(number)


def parse_filename(filename: str) -> tuple[str, str, str]:
    """
    (ipn, rev, name) from an `<ipn>-<rev>-<name>-bom.csv` naming convention:
    `000123456-D-my_board-bom.csv` -> ('000123456', 'D', 'my board').

    Blanks when the name does not conform. Only ever a suggestion -- one IPN can name several
    revisions -- so a filename can suggest but must never select.
    """
    match = _FILENAME.match((filename or "").strip())
    if not match:
        return "", "", ""
    return (match.group("ipn"), match.group("rev").upper(), match.group("name").replace("_", " ").strip())
