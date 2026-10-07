"""
Reading a BOM export.

Exercised against a KiCad-style grouped BOM of the royalblue54L_feather demo board
(fixtures/generated/bom, written from the golden board.json by make_bom.py), because a CSV
written inside a test agrees with whatever the parser assumes. The other
tools' layouts below are hand-written from their documented column names, which is the best
that can be done without a licence for each.
"""

from __future__ import annotations

import io
import re

import pytest

from boarddd.io.bom import (
    ParseError,
    decode,
    detect_columns,
    find_header_row,
    looks_like_bom,
    parse_csv,
    parse_filename,
    read_headers,
    spreadsheet_to_csv,
)

from conftest import GENERATED

REAL_CSV = GENERATED / "bom" / "royalblue54L_feather-bom.csv"


def _real() -> str:
    return REAL_CSV.read_text(encoding="utf-8-sig")


# ─── The KiCad-style file ───────────────────────────────────────────────────────────


def test_parses_the_real_export():
    rows = parse_csv(_real())
    assert len(rows) == 31, "the royalblue BOM has 31 grouped rows"
    assert all(r.reference for r in rows)
    assert all(r.quantity >= 1 for r in rows)


def test_the_export_has_two_dnp_rows():
    """DNP rows are parsed and carried, never silently dropped -- the reviewer should see them."""
    rows = parse_csv(_real())
    dnp = [r for r in rows if not r.populate]
    assert len(dnp) == 2
    assert all(r.mpn or r.value for r in dnp), "a DNP row is still a real row"


def test_row_numbers_line_up_with_the_file():
    """Row 2 is the first data row, because row 1 is the header. Error messages depend on it."""
    rows = parse_csv(_real())
    assert rows[0].row_number == 2
    assert rows[-1].row_number == 32


def test_designators_are_split():
    rows = parse_csv(_real())
    multi = next(r for r in rows if "," in r.reference)
    assert len(multi.designators) > 1


# ─── Header tolerance ────────────────────────────────────────────────────────


def test_headers_are_matched_case_and_punctuation_insensitively():
    text = "reference,QTY,mfr_pn\nC1,2,ABC-123\n"
    rows = parse_csv(text)
    assert rows[0].reference == "C1" and rows[0].quantity == 2 and rows[0].mpn == "ABC-123"


def test_a_utf8_bom_does_not_hide_the_first_column():
    """Excel and KiCad both emit one; without stripping it 'Reference' reads as missing."""
    rows = parse_csv('﻿"Reference","Quantity","MPN"\n"C1","1","X"\n')
    assert rows[0].reference == "C1"


def test_unknown_columns_are_ignored():
    rows = parse_csv("Reference,Quantity,MPN,Sheet,Fitted\nC1,1,X,/root,yes\n")
    assert rows[0].mpn == "X"


def test_a_file_with_no_designator_column_is_rejected():
    """The one column nothing can stand in for: a BOM line is its designators."""
    with pytest.raises(ParseError) as exc:
        parse_csv("Quantity,MPN\n1,b\n")
    assert "Nothing is mapped to: reference" in str(exc.value)


def test_the_error_names_what_was_actually_found():
    """ "Invalid file" is useless; the user needs to see which header we did not recognize."""
    with pytest.raises(ParseError) as exc:
        parse_csv("Alpha,Beta,Gamma\nC1,1,X\n")
    assert "Alpha" in str(exc.value)
    assert "Beta" in str(exc.value)


def test_without_a_quantity_column_a_line_counts_its_designators():
    """
    Eagle's and Allegro's exports have no quantity column; what they have is the list of
    designators, which is what the quantity column says in every export that has one.
    """
    rows = parse_csv('Reference,MPN\n"C1,C2,C3",A\nR1,B\n')
    assert [(r.reference, r.quantity) for r in rows] == [("C1,C2,C3", 3), ("R1", 1)]


def test_without_an_mpn_column_the_file_still_imports():
    """
    An Eagle BOM has no part number anywhere in it. It still imports -- every line then has
    to be resolved by hand or by distributor search, which is an import, and the honest one.
    """
    rows = parse_csv("Part,Value,Device,Package\nC1,100n,C-EUC0402,C0402\n")
    assert rows[0].reference == "C1" and rows[0].mpn == ""
    assert rows[0].value == "100n" and rows[0].footprint == "C0402"


# ─── Bad rows reject the whole file ──────────────────────────────────────────


def test_a_bad_quantity_rejects_the_file_and_names_the_row():
    with pytest.raises(ParseError) as exc:
        parse_csv("Reference,Quantity,MPN\nC1,1,A\nC2,banana,B\nC3,2,C\n")
    assert "row 3" in str(exc.value)


def test_every_bad_row_is_reported_not_just_the_first():
    """Fixing one row at a time through four uploads is a miserable way to import a BOM."""
    with pytest.raises(ParseError) as exc:
        parse_csv("Reference,Quantity,MPN\nC1,x,A\nC2,y,B\nC3,z,C\n")
    assert len(exc.value.problems) == 3


@pytest.mark.parametrize("quantity", ["0", "-1", "1.5", ""])
def test_quantities_must_be_whole_and_positive(quantity):
    with pytest.raises(ParseError):
        parse_csv(f"Reference,Quantity,MPN\nC1,{quantity},A\n")


@pytest.mark.parametrize("quantity", ["inf", "-inf", "nan", "Infinity", "1e400"])
def test_a_quantity_that_is_not_a_finite_number_is_a_parse_error(quantity):
    """float() reads all of these, and int() of the result used to raise OverflowError or
    ValueError past the try -- a 500 on ingest instead of a row the page can name. Every
    caller catches ParseError and nothing else, so that is what they must get."""
    with pytest.raises(ParseError) as exc:
        parse_csv(f"Reference,Quantity,MPN\nC1,{quantity},A\n")
    assert "row 2" in str(exc.value)


def test_a_whole_number_written_as_a_float_is_accepted():
    """Some exporters write 2.0. Rejecting that would be pedantry, not safety."""
    assert parse_csv("Reference,Quantity,MPN\nC1,2.0,A\n")[0].quantity == 2


def test_an_empty_file_is_rejected():
    with pytest.raises(ParseError):
        parse_csv("")


def test_a_header_with_no_rows_is_rejected():
    with pytest.raises(ParseError, match="no rows"):
        parse_csv("Reference,Quantity,MPN\n")


def test_trailing_blank_lines_are_ignored():
    rows = parse_csv("Reference,Quantity,MPN\nC1,1,A\n,,\n\n")
    assert len(rows) == 1


# ─── Populate / DNP ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,populate",
    [
        ("DNP", False),
        ("dnp", False),
        ("No", False),
        ("FALSE", False),
        ("0", False),
        ("", True),
        ("yes", True),
        ("Populate", True),
    ],
)
def test_populate_column(value, populate):
    assert parse_csv(f"Reference,Quantity,MPN,Populate\nC1,1,A,{value}\n")[0].populate is populate


def test_a_dedicated_dnp_column_means_the_opposite():
    assert parse_csv("Reference,Quantity,MPN,DNP\nC1,1,A,yes\n")[0].populate is False
    assert parse_csv("Reference,Quantity,MPN,DNP\nC1,1,A,\n")[0].populate is True


# ─── Filenames ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("000123456-D-my_board-bom.csv", ("000123456", "D", "my board")),
        ("000123457-G_other_board-bom.csv", ("000123457", "G", "other board")),
        ("000123458-A-board_2-bom.csv", ("000123458", "A", "board 2")),
    ],
)
def test_filename_parsing(filename, expected):
    assert parse_filename(filename) == expected


@pytest.mark.parametrize("filename", ["bom.csv", "my export.csv", "", "PROJ-A_thing-bom.csv"])
def test_a_nonconforming_filename_yields_blanks(filename):
    """Blank prefill, never a guess -- the filename must not select a target."""
    assert parse_filename(filename) == ("", "", "")


# ─── Arbitrary column mapping ────────────────────────────────────────────────


def test_an_explicit_mapping_reads_a_layout_we_have_never_seen():
    """
    The whole point of the mapping: a file whose headers mean nothing to the auto-detector.
    Auto-detection is a default, not a requirement.
    """
    text = 'Part Ident,Board Refs,How Many,Fitted\nABC-123,"C1,C2",2,yes\n'
    with pytest.raises(ParseError):
        parse_csv(text)  # nothing recognisable

    rows = parse_csv(
        text, {"mpn": "Part Ident", "reference": "Board Refs", "quantity": "How Many", "populate": "Fitted"}
    )
    assert rows[0].mpn == "ABC-123"
    assert rows[0].designators == ["C1", "C2"]
    assert rows[0].quantity == 2 and rows[0].populate is True


def test_a_mapping_can_override_a_correct_guess():
    """Two plausible columns; the human decides which one is really the MPN."""
    text = "Reference,Quantity,MPN,Internal PN\nC1,1,VENDOR-1,IPN-9\n"
    assert parse_csv(text)[0].mpn == "VENDOR-1"
    assert parse_csv(text, {"reference": "Reference", "quantity": "Quantity", "mpn": "Internal PN"})[0].mpn == "IPN-9"


def test_unmapped_optional_fields_are_simply_blank():
    rows = parse_csv(
        "Reference,Quantity,MPN,Value\nC1,1,A,10k\n", {"reference": "Reference", "quantity": "Quantity", "mpn": "MPN"}
    )
    assert rows[0].value == "", "an unmapped column is ignored, not guessed at"


def test_a_blank_mapping_entry_means_not_mapped():
    """The mapping form posts '' for 'do not use this field'."""
    rows = parse_csv(
        "Reference,Quantity,MPN,Note\nC1,1,A,hi\n",
        {"reference": "Reference", "quantity": "Quantity", "mpn": "MPN", "note": ""},
    )
    assert rows[0].note == ""


def test_a_mapping_naming_a_column_that_does_not_exist_is_ignored():
    """A stale mapping must not silently drop a required field into a confusing error."""
    with pytest.raises(ParseError, match="Nothing is mapped to: reference"):
        parse_csv("Reference,Quantity,MPN\nC1,1,A\n", {"reference": "Nope", "quantity": "Quantity", "mpn": "MPN"})
    # An optional field pointed at a column that is not there is simply not mapped.
    rows = parse_csv("Reference,Quantity,MPN\nC1,1,A\n", {"reference": "Reference", "quantity": "Nope", "mpn": "MPN"})
    assert rows[0].quantity == 1


def test_detect_columns_finds_the_kicad_layout():
    detected = detect_columns(_real())
    assert detected["reference"] == "Reference"
    assert detected["quantity"] == "Quantity"
    assert detected["mpn"] == "MPN"
    assert detected["lcsc_pn"] == "LCSC PN"


def test_read_headers_returns_the_file_s_own_names():
    assert read_headers(_real())[:4] == ["Reference", "Value", "Footprint", "Quantity"]


def test_read_headers_survives_an_empty_file():
    assert read_headers("") == []


# ─── Where the column names are ──────────────────────────────────────────────

TITLED = (
    "Bill of Materials,,\n"
    "Project,my_board,\n"
    "Revision,D,\n"
    ",,\n"
    "Reference,Qty,MPN\n"
    "C1,2,GRM155R61A106ME11D\n"
    "R1,1,RC0402FR-0710KL\n"
)


def test_the_header_row_is_usually_the_first_one():
    assert find_header_row("Reference,Qty,MPN\nC1,2,X\n") == 0


def test_a_title_block_above_the_table_is_seen_past():
    """
    Read from row one, this file has one column called "Bill of Materials". That is not a
    mapping problem -- there is nothing on the row to map to -- which is why it is asked as
    its own question.
    """
    assert find_header_row(TITLED) == 4
    assert read_headers(TITLED) == ["Reference", "Qty", "MPN"]


def test_the_row_is_found_by_what_it_names_rather_than_by_shape():
    """
    A title block names no fields we know, and nor does a data row -- `C1` and `100nF` are not
    column names. So the header stands out from both without having to describe either.
    """
    rows = parse_csv(TITLED)

    assert [row.reference for row in rows] == ["C1", "R1"]


def test_row_numbers_are_the_files_own():
    """A complaint about row 6 should point at row 6 of the file somebody has open."""
    rows = parse_csv(TITLED)

    assert [row.row_number for row in rows] == [6, 7]


def test_a_row_somebody_names_beats_the_guess():
    """They are looking at the file; the reader is counting matches in it."""
    headers = read_headers(TITLED, header_row=0)

    assert headers == ["Bill of Materials", "", ""]


def test_the_earliest_of_equally_good_rows_wins():
    """A file that repeats its headers partway down is still read from the top."""
    doubled = "Reference,Qty,MPN\nC1,2,X\nReference,Qty,MPN\nR1,1,Y\n"

    assert find_header_row(doubled) == 0


def test_a_quoted_newline_does_not_cut_a_row_in_half():
    """The block is dropped by re-splitting parsed records, not by counting line breaks."""
    text = 'Title,,\nReference,Qty,MPN\n"C1\nC2",2,"GRM155"\n'

    rows = parse_csv(text)

    assert len(rows) == 1 and rows[0].quantity == 2


# ─── Where the table ends ────────────────────────────────────────────────────


def test_a_footer_ends_the_table_rather_than_the_import():
    """
    "Row 214 has no quantity" is a true thing to say about a totals line and a useless one:
    every part in the file read perfectly, and the import failed. A row that does not carry
    the fields a part must have is not a part, and by then the table is over.
    """
    rows = parse_csv("Reference,Qty,MPN\nC1,2,GRM155\nR1,1,RC0402\nTotal,,\n")

    assert [row.reference for row in rows] == ["C1", "R1"]
    assert rows.stopped_at == 4 and rows.stop_reason == "no quantity"


def test_a_blank_row_ends_it_too():
    """Which is how most exporters separate a table from whatever follows it."""
    rows = parse_csv("Reference,Qty,MPN\nC1,2,X\n\nGenerated by KiCad,,\n")

    assert len(rows) == 1
    assert rows.stopped_at == 3 and rows.stop_reason == "a blank row"


def test_a_row_with_no_designator_ends_it():
    rows = parse_csv("Reference,Qty,MPN\nC1,2,X\n,1,Y\n")

    assert len(rows) == 1 and rows.stop_reason == "no reference"


def test_a_file_that_simply_runs_out_stopped_nowhere():
    """There is a difference between "the table ended" and "the file ended"."""
    rows = parse_csv("Reference,Qty,MPN\nC1,2,X\n")

    assert rows.stopped_at is None and rows.stop_reason == ""


def test_a_blank_row_does_not_shift_the_numbering_after_it():
    """
    csv.DictReader drops empty rows outright, which broke this twice: a blank row never
    arrived to end the table, and every row after one was reported a line early.
    """
    rows = parse_csv("Reference,Qty,MPN\nC1,2,X\n\nR1,1,Y\n")

    assert [row.row_number for row in rows] == [2]
    assert rows.stopped_at == 3


def test_a_quantity_that_is_present_but_wrong_is_still_an_error():
    """
    Missing and malformed are different. Nothing there is the end of the table; "two" in the
    quantity column of a row that is plainly a part is a mistake somebody wants told about.
    """
    with pytest.raises(ParseError) as raised:
        parse_csv("Reference,Qty,MPN\nC1,two,X\n")

    assert "row 2" in raised.value.problems[0]


def test_the_real_export_reads_to_the_end():
    """The KiCad-style BOM has no footer, so nothing here should stop it early."""
    rows = parse_csv(_real())

    assert rows.stopped_at is None, "nothing in it looks like a footer"
    # Every line of the file below the header, which is the point: stopping early on a real
    # export would be the failure mode this whole mechanism risks introducing.
    assert len(rows) == len(_real().strip().splitlines()) - 1


# ─── Other tools' exports ────────────────────────────────────────────────────

ALTIUM_FLAT = (
    "Designator,Comment,Footprint,Quantity,Manufacturer,Manufacturer Part Number,"
    "Description\n"
    "C1,100nF,0402,1,Murata,GRM155R61A104KA01D,Cap 100nF\n"
    "C2,100nF,0402,1,Murata,GRM155R61A104KA01D,Cap 100nF\n"
    "R1,10k,0402,1,Yageo,RC0402FR-0710KL,Res 10k\n"
)


def test_altium_column_names_are_recognized():
    detected = detect_columns(ALTIUM_FLAT)
    assert detected["reference"] == "Designator"
    assert detected["value"] == "Comment", "Altium's Comment is the value"
    assert detected["mpn"] == "Manufacturer Part Number"
    assert detected["note"] == "Description"


def test_a_one_designator_per_row_export_is_folded_into_one_line_per_part():
    """
    Altium's flat BOM lists C1, C2 and C3 of the same capacitor as three rows. Imported as
    three lines they would go onto the assembly as three BOM lines for one component, which
    the apply step flags as a conflict on every later import, forever.
    """
    rows = parse_csv(ALTIUM_FLAT)

    assert [(r.reference, r.quantity, r.mpn) for r in rows] == [
        ("C1,C2", 2, "GRM155R61A104KA01D"),
        ("R1", 1, "RC0402FR-0710KL"),
    ]
    assert rows.consolidated == 1
    assert rows[0].row_number == 2, "a folded line keeps its first row's number"


def test_a_grouped_export_is_never_folded():
    """A KiCad file already groups; folding it would change nothing and must not."""
    rows = parse_csv(_real())
    assert rows.consolidated == 0 and len(rows) == 31


def test_flat_rows_without_a_part_number_fold_on_value_and_footprint():
    rows = parse_csv("Part,Value,Package\nC1,100n,C0402\nC2,100n,C0402\nC3,100n,C0603\n")
    assert [(r.reference, r.quantity) for r in rows] == [("C1,C2", 2), ("C3", 1)]


def test_rows_that_already_carry_a_quantity_above_one_are_not_folded():
    """Two rows naming one designator each but counting two is a file saying something else."""
    rows = parse_csv("Reference,Quantity,MPN\nC1,2,A\nC2,2,A\n")
    assert len(rows) == 2


def test_easyeda_column_names_are_recognized():
    rows = parse_csv('Comment,Designator,Footprint,LCSC Part #\n100nF,"C1,C2",C0402,C1525\n10k,R1,R0402,C25744\n')
    assert (rows[0].reference, rows[0].quantity, rows[0].value, rows[0].lcsc_pn) == ("C1,C2", 2, "100nF", "C1525")


def test_a_semicolon_separated_file_reads_like_a_comma_separated_one():
    """Excel writes ';' wherever the comma is the decimal point, and Eagle's ULP does too."""
    rows = parse_csv("Part;Value;Device;Package\nC1;100n;C-EUC0402;C0402\nR1;10k;R;R0402\n")
    assert [(r.reference, r.value) for r in rows] == [("C1", "100n"), ("R1", "10k")]
    assert read_headers("Part;Value;Device\nC1;1;x\n") == ["Part", "Value", "Device"]


def test_a_tab_separated_file_reads_too():
    rows = parse_csv("Reference\tQuantity\tMPN\nC1\t1\tA\n")
    assert rows[0].mpn == "A"


@pytest.mark.parametrize("separator", [", ", "; ", " "])
def test_designators_are_normalized_to_one_comma_separated_list(separator):
    """Altium writes `C1, C2`; what an application stores is `C1,C2` whatever the file wrote."""
    rows = parse_csv(f'Reference,MPN\n"C1{separator}C2",A\n')
    assert rows[0].reference == "C1,C2" and rows[0].quantity == 2


@pytest.mark.parametrize(
    "value,populate",
    [
        ("Fitted", True),
        ("Not Fitted", False),
        ("DNF", False),
        ("yes", True),
        ("x", False),
    ],
)
def test_other_tools_words_for_fitted_are_understood(value, populate):
    assert parse_csv(f"Reference,MPN,Fitted\nC1,A,{value}\n")[0].populate is populate


def test_a_windows_1252_file_decodes_without_losing_its_symbols():
    """Altium and Excel-on-Windows write cp1252, and a BOM is full of µ."""
    assert decode("Reference,Value\nC1,10 µF\n".encode("cp1252")) == "Reference,Value\nC1,10 µF\n"
    assert decode("\ufeffReference\n".encode()) == "Reference\n", "the BOM is dropped"


def test_looks_like_bom_needs_designators_and_something_about_the_part():
    assert looks_like_bom("Reference,Value\nC1,1\n")
    assert looks_like_bom("Designator,Manufacturer Part Number\nC1,x\n")
    assert not looks_like_bom("Reference\nC1\n"), "designators alone could be a placement file"
    assert not looks_like_bom("Ref,PosX,PosY\nC1,1,2\n")


def test_a_spreadsheet_is_read_from_the_sheet_that_looks_most_like_a_bom():
    """
    Altium's default export is .xlsx, and its template puts a cover sheet first. The sheet
    with the most recognizable column names is the table.
    """
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    cover = book.active
    cover.title = "Cover"
    cover.append(["Bill of Materials", "Rev A"])
    table = book.create_sheet("BOM")
    table.append(["Designator", "Quantity", "Manufacturer Part Number"])
    table.append(["C1, C2", 2, "GRM155"])
    table.append(["R1", 1.0, "RC0402"])
    buffer = io.BytesIO()
    book.save(buffer)

    text = spreadsheet_to_csv(buffer.getvalue(), "bom.xlsx")
    rows = parse_csv(text)

    assert text.splitlines()[0] == "Designator,Quantity,Manufacturer Part Number"
    assert [(r.reference, r.quantity, r.mpn) for r in rows] == [("C1,C2", 2, "GRM155"), ("R1", 1, "RC0402")]


def test_a_spreadsheet_that_is_not_one_is_a_parse_error_not_a_crash():
    pytest.importorskip("openpyxl")
    with pytest.raises(ParseError):
        spreadsheet_to_csv(b"not a spreadsheet at all", "bom.xlsx")


# ─── Spreadsheets built to take the worker's memory ──────────────────────────


def _crafted_xlsx(dimension: str, far_cells: tuple[str, ...] = (), padding: int = 0) -> bytes:
    """
    A small real BOM whose sheet XML is then edited: the `<dimension>` it declares replaced,
    a lone cell added at each of `far_cells`, and optionally a zip member of `padding` zero
    bytes, which deflates to almost nothing.
    """
    import zipfile

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.append(["Reference", "Value", "MPN"])
    book.active.append(["C1", "100nF", "GRM155"])
    book.active.append(["R1", "10k"])
    plain = io.BytesIO()
    book.save(plain)

    source = zipfile.ZipFile(io.BytesIO(plain.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as crafted:
        for member in source.infolist():
            data = source.read(member)
            if member.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(rb'<dimension ref="[^"]*"\s*/>', f'<dimension ref="{dimension}"/>'.encode(), data)
                extra = "".join(
                    f'<row r="{re.sub("[A-Z]", "", cell)}"><c r="{cell}" t="n"><v>1</v></c></row>' for cell in far_cells
                )
                data = data.replace(b"</sheetData>", extra.encode() + b"</sheetData>")
            crafted.writestr(member, data)
        if padding:
            crafted.writestr("xl/padding.bin", bytes(padding))
    return out.getvalue()


@pytest.mark.parametrize(
    "dimension,far_cells,said",
    [
        ("A1:XFD1048576", ("A1048576",), "1,048,576 rows by 16,384 columns"),
        ("A1:C10001", ("A10001",), "10,001 rows by 3 columns"),
        ("A1:IW300", ("IW300",), "300 rows by 257 columns"),
    ],
)
def test_a_sheet_that_declares_itself_huge_is_refused_before_it_is_read(dimension, far_cells, said):
    """
    openpyxl pads every row to the declared `<dimension>` and yields every row up to it: a
    5 KB file declaring 8000 x 16384 took 1.27 GB in a real BOM import, and the full
    sheet is an out-of-memory kill. The declaration is checked first, and the answer says what
    was wrong rather than being a dead worker.
    """
    import tracemalloc

    data = _crafted_xlsx(dimension, far_cells)
    assert len(data) < 10_000

    tracemalloc.start()
    try:
        with pytest.raises(ParseError) as refused:
            spreadsheet_to_csv(data, "bom.xlsx")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert said in refused.value.problems[0]
    assert "save the BOM sheet as CSV" in refused.value.problems[0]
    assert peak < 20 * 1024 * 1024


def test_a_sheet_that_declares_less_than_it_holds_is_still_read_within_bounds():
    """
    The declaration is thrown away once checked, so a file claiming one row is read for what
    it has -- and still no further than the caps: a row past them with something in it is
    refused, and a lone cell out at the last row and column Excel allows is never reached.
    """
    with pytest.raises(ParseError, match="more than 10,000 rows"):
        spreadsheet_to_csv(_crafted_xlsx("A1", ("A10001",)), "bom.xlsx")

    text = spreadsheet_to_csv(_crafted_xlsx("A1", ("IV5", "IX6", "XFD1048576")), "bom.xlsx")
    assert text.splitlines() == [
        "Reference,Value,MPN" + "," * 253,
        "C1,100nF,GRM155" + "," * 253,
        "R1,10k" + "," * 254,
        "," * 255,
        "," * 255 + "1",
    ], "all rows padded to the widest real one (IV is column 256); IX6 is past it, unread"


def test_an_honest_spreadsheet_reads_as_it_always_did():
    text = spreadsheet_to_csv(_crafted_xlsx("A1:C3"), "bom.xlsx")
    assert text == "Reference,Value,MPN\nC1,100nF,GRM155\nR1,10k,\n"


def test_a_spreadsheet_that_unpacks_to_more_than_a_bom_can_be_is_refused():
    data = _crafted_xlsx("A1:C3", padding=60 * 1024 * 1024)
    assert len(data) < 200_000

    with pytest.raises(ParseError, match="unpacks to 60 MB"):
        spreadsheet_to_csv(data, "bom.xlsx")


def test_fusion_and_easyeda_column_names():
    """Fusion 360's MF attribute is the manufacturer; EasyEDA's Name is the value."""
    fusion = (
        '"Qty";"Value";"Device";"Package";"Parts";"Description";"MF";"MPN"\n'
        '"2";"10k";"R0603";"R0603";"R1 R2";"";"Yageo";"RC0603FR-0710KL"\n'
    )
    rows = parse_csv(fusion)
    assert (rows[0].manufacturer, rows[0].mpn, rows[0].value, rows[0].footprint) == (
        "Yageo",
        "RC0603FR-0710KL",
        "10k",
        "R0603",
    )
    assert rows[0].designators == ["R1", "R2"]
    easyeda = (
        "ID\tName\tDesignator\tFootprint\tQuantity\tManufacturer Part\tManufacturer\t"
        "Supplier\tSupplier Part\tPrice\n"
        "1\t100nF\tC1,C2\tC0402\t2\tCL05B104KO5NNNC\tSamsung\tLCSC\tC1525\t\n"
    )
    rows = parse_csv(easyeda)
    assert (rows[0].value, rows[0].mpn, rows[0].manufacturer, rows[0].lcsc_pn) == (
        "100nF",
        "CL05B104KO5NNNC",
        "Samsung",
        "C1525",
    )


def test_a_weak_name_column_does_not_displace_the_value():
    text = "Designator,Name,Comment,Manufacturer Part Number 1\nR1,RES-0603,10k,RC0603FR-0710KL\n"
    assert detect_columns(text)["value"] == "Comment"
    assert parse_csv(text)[0].value == "10k"


def test_a_blank_is_filled_from_another_column_of_the_same_meaning():
    """tinytapeout: MANUFACTURER is empty on every symbol, Manufacturer filled on a few."""
    text = (
        "Reference,Value,MANUFACTURER,Manufacturer,MPN\n"
        "R1,10k,,Stackpole Electronics Inc,RMCF0603FT10K0\n"
        "R2,1k,Yageo,,RC0603FR-071KL\n"
        "R3,2k,,,RC0603FR-072KL\n"
    )
    rows = parse_csv(text)
    assert [r.manufacturer for r in rows] == ["Stackpole Electronics Inc", "Yageo", ""]
    # An explicit mapping (the import screen's) is taken as given.
    mapped = parse_csv(text, {"reference": "Reference", "mpn": "MPN", "manufacturer": "MANUFACTURER"})
    assert [r.manufacturer for r in mapped] == ["", "Yageo", ""]
