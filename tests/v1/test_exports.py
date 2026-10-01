import io
import re
import textwrap
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import openpyxl
import pytest

from api.queries.projects import get_project_contractor_name
from api.services import export
from api.services.export import generate_idr_export
from api.services.export_general import (
    REPORT_CONT_LINE_CHARS, fill_lines, fit_pay_description, paragraphs, pay_item_rows, truncate_to_lines,
)
from api.services.xlsx_template import WorkbookTemplate

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "report_forms.xlsx"
SOURCE_TEMPLATE = ROOT / "templates" / "report_forms_source.xltx"

# The sample project the DDC template shipped with; none of it may survive the cleanup
SAMPLE_VALUES = ["SER200220", "20151410922", "Jewett", "LaPeruta", "Jay Patel", "Staten Island", "STATEN ISLAND"]

IDR_ID = UUID("8b2f887b-1c2d-4e3f-9a0b-1c2d3e4f5a6b")
REPORTER = UUID("7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90")

SUBMITTED_IDR = {
    "idr_id": IDR_ID,
    "project_id": "HWS0023",
    "reporter_uuid": REPORTER,
    "report_date": date(2026, 9, 30),  # a Wednesday
    "work_start_time": time(7, 0),
    "work_end_time": time(15, 30),
    "inspector_start_time": time(6, 45),
    "inspector_end_time": None,
    "temp_low": Decimal("45.0"),
    "temp_high": Decimal("62.5"),
    "weather_am": "Cloudy",
    "weather_pm": "Rain",
    "total_pages": 3,
    "has_dismissed_auto_general": False,
    "status": "submitted",
    "submitted_at": datetime(2026, 9, 30, 17, 0),
    "created_at": datetime(2026, 9, 30, 7, 0),
    "updated_at": datetime(2026, 9, 30, 17, 0),
}

PROJECT = {
    "project_id": "HWS0023",
    "project_name": "S/W Queens 2025",
    "project_description": "Installation of Curb, Sidewalk & Ped-Ramp <Queens>",
    "registration_code": "2024123457",
    "borough": "Queens",
    "status": "Construction; Active",
}

USER = {"user_id": REPORTER, "email": "KhanG@magnoleng.pc", "first_name": "Genghis", "last_name": "Khan"}

GENERAL = {
    "report_type": "GEN",
    "page_number": 1,
    "report_data": {"description": "Poured curb along Main St.\n\nInspected forms before the pour."},
}


@contextmanager
def patched_export(idr=SUBMITTED_IDR, project=PROJECT, contractor="Benny Bowers Contracting Co.", user=USER,
                   general=GENERAL, main_reports=None):
    """
    Patch the queries generate_idr_export reads, so it runs without a database.
    Takes the IDR, project, contractor name, user, General report (or None) and non-General main reports.
    Yields a dict of the mocks.
    """
    with patch.object(export, "get_idr_by_id", return_value=idr) as gi, \
         patch.object(export, "get_project_by_id", return_value=project) as gp, \
         patch.object(export, "get_project_contractor_name", return_value=contractor) as gc, \
         patch.object(export, "get_user_by_id", return_value=user) as gu, \
         patch.object(export, "get_general_report", return_value=general) as gg, \
         patch.object(export, "list_non_general_main_reports", return_value=main_reports or []) as lm:
        yield {"idr": gi, "project": gp, "contractor": gc, "user": gu, "general": gg, "main": lm}


def exported_workbook(**overrides) -> openpyxl.Workbook:
    """
    Run generate_idr_export on stubbed data and open the result read-only, for checking cell values.
    Takes patched_export's keyword overrides.
    Returns the workbook (read-only mode parses only the sheets a test touches; a full load takes seconds).
    """
    with patched_export(**overrides):
        result = generate_idr_export(IDR_ID)
    return openpyxl.load_workbook(io.BytesIO(result.content), read_only=True)


@pytest.fixture(scope="module")
def full_export() -> tuple[bytes, openpyxl.Workbook]:
    """
    The default export, fully loaded once for the checks read-only mode can't make (styles, print setup, visibility).
    Takes nothing.
    Returns (the export's bytes, the loaded workbook).
    """
    with patched_export():
        content = generate_idr_export(IDR_ID).content
    return content, openpyxl.load_workbook(io.BytesIO(content))


def drawing_parts(package_bytes: bytes) -> list[str]:
    """
    List an .xlsx package's drawing parts.
    Takes the package's bytes.
    Returns the drawing part names, sorted.
    """
    return sorted(n for n in zipfile.ZipFile(io.BytesIO(package_bytes)).namelist() if n.startswith("xl/drawings/drawing"))


# ---------------------------------------------------------------------------
# The cleaned template (templates/report_forms.xlsx)
# ---------------------------------------------------------------------------

class TestTemplate:
    def test_contract_info_values_are_blank(self):
        sheet = openpyxl.load_workbook(TEMPLATE, read_only=True)["Contract Info"]
        assert [sheet[f"C{row}"].value for row in range(2, 8)] == [None] * 6
        assert sheet["B2"].value == "Contract No.:"  # the labels stay

    def test_no_sample_project_data_left_anywhere_in_the_package(self):
        package = zipfile.ZipFile(TEMPLATE)
        xml = "".join(package.read(n).decode("utf-8", "ignore") for n in package.namelist() if n.endswith(".xml"))
        assert [value for value in SAMPLE_VALUES if value in xml] == []

    def test_header_formulas_still_read_contract_info(self):
        sheet = openpyxl.load_workbook(TEMPLATE, read_only=True)["Gen Fr"]
        assert sheet["G8"].value == "='Contract Info'!C2"
        assert sheet["F12"].value == "='Contract Info'!C5"

    def test_cleanup_kept_every_logo_and_shape(self):
        assert drawing_parts(TEMPLATE.read_bytes()) == drawing_parts(SOURCE_TEMPLATE.read_bytes())
        source, cleaned = zipfile.ZipFile(SOURCE_TEMPLATE), zipfile.ZipFile(TEMPLATE)
        for name in source.namelist():
            if name.startswith(("xl/drawings/", "xl/media/")):
                assert cleaned.read(name) == source.read(name), name


# ---------------------------------------------------------------------------
# generate_idr_export: Gen Fr header
# ---------------------------------------------------------------------------

def general_with(**report_data) -> dict:
    """
    Build the General report row the export reads, with the given report_data.
    Takes report_data fields as keyword arguments.
    Returns the row (page 1).
    """
    return {"report_type": "GEN", "page_number": 1, "report_data": report_data}


def visible_sheets(content: bytes) -> list[str]:
    """
    List an export's visible sheets in tab order, read straight from workbook.xml (fast, no full load).
    Takes the .xlsx bytes.
    Returns the sheet names without state="hidden".
    """
    workbook = zipfile.ZipFile(io.BytesIO(content)).read("xl/workbook.xml").decode()
    return [re.search(r'name="([^"]+)"', tag).group(1) for tag in re.findall(r"<sheet [^>]*/>", workbook)
            if 'state="hidden"' not in tag]


def export_bytes(**overrides) -> bytes:
    """
    Run generate_idr_export on stubbed data.
    Takes patched_export's keyword overrides.
    Returns the .xlsx bytes.
    """
    with patched_export(**overrides):
        return generate_idr_export(IDR_ID).content


# Long enough to fill Gen Fr, Gen Bk's comment lines and Report Cont, and still be cut
LONG_DESCRIPTION = " ".join(f"word{n}" for n in range(400))
CASCADE = general_with(description=LONG_DESCRIPTION, comments="Visitor from DEP at 10am.")


@pytest.fixture(scope="module")
def full_cascade() -> tuple[bytes, openpyxl.Workbook]:
    """
    An export whose description runs onto Gen Bk and Report Cont, fully loaded once for style and order checks.
    Takes nothing.
    Returns (the export's bytes, the loaded workbook).
    """
    content = export_bytes(general=CASCADE)
    return content, openpyxl.load_workbook(io.BytesIO(content))


class TestGeneralFrontHeader:
    def test_returns_a_valid_workbook_named_for_the_idr(self):
        with patched_export():
            result = generate_idr_export(IDR_ID)
        assert result.filename == f"IDR_{IDR_ID}_2026-09-30.xlsx"
        assert openpyxl.load_workbook(io.BytesIO(result.content), read_only=True).sheetnames[0] == "Contract Info"

    def test_stamps_contract_info(self):
        sheet = exported_workbook()["Contract Info"]
        assert [sheet[f"C{row}"].value for row in range(2, 8)] == [
            "HWS0023", "2024123457", "Installation of Curb, Sidewalk & Ped-Ramp <Queens>", "Queens",
            "Benny Bowers Contracting Co.", None,  # no Resident Engineer in the data model yet
        ]

    def test_writes_project_values_over_the_contract_info_formulas(self):
        sheet = exported_workbook()["Gen Fr"]
        # A formula would read back as "='Contract Info'!C2"; the export leaves plain values for previewers
        assert [sheet[c].value for c in ("G8", "P8", "I10", "F12", "F14")] == [
            "HWS0023", "2024123457", "Installation of Curb, Sidewalk & Ped-Ramp <Queens>", "Queens",
            "Benny Bowers Contracting Co.",
        ]

    def test_stamps_the_header_fields(self):
        sheet = exported_workbook()["Gen Fr"]
        assert sheet["AI4"].value == datetime(2026, 9, 30)
        assert sheet["AH8"].value == 1 and sheet["AM8"].value == 3
        assert sheet["AG10"].value == "( Start 07:00 End 15:30 )"
        assert sheet["AG12"].value == "( Start 06:45 End ________ )"  # one of two times known keeps the other blank
        assert sheet["AD13"].value == "Low  45" and sheet["AK13"].value == "High  62.5"
        assert sheet["AD17"].value == "Cloudy" and sheet["AK17"].value == "Rain"
        assert sheet["H17"].value == "Genghis Khan"
        assert sheet["AH6"].value is None  # no I.R. No. in the data model yet

    def test_empty_fields_clear_the_template_placeholders(self):
        idr = {**SUBMITTED_IDR, "work_start_time": None, "work_end_time": None, "inspector_start_time": None,
               "temp_low": None, "temp_high": None, "weather_am": None, "weather_pm": "  "}
        sheet = exported_workbook(idr=idr, contractor=None, user=None)["Gen Fr"]
        assert sheet["AG10"].value is None and sheet["AG12"].value is None
        assert sheet["AD17"].value is None and sheet["AK17"].value is None
        assert sheet["F14"].value is None and sheet["H17"].value is None
        # Low / High are the boxes' labels, not placeholders, so they stay
        assert sheet["AD13"].value == "Low" and sheet["AK13"].value == "High"

    def test_highlights_only_the_day_of_week(self, full_export):
        sheet = full_export[1]["Gen Fr"]
        highlighted = [c for c in ("AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5") if sheet[c].fill.fill_type == "solid"]
        assert highlighted == ["AL5"]
        assert sheet["AL5"].value == "W" and sheet["AL5"].border.left.style == "medium"

    def test_sets_letter_portrait_one_page_print_setup(self, full_export):
        sheet = full_export[1]["Gen Fr"]
        assert sheet.page_setup.paperSize == 1  # US Letter
        assert sheet.page_setup.orientation == "portrait"
        assert sheet.sheet_properties.pageSetUpPr.fitToPage is True
        assert (sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight) == (1, 1)

    def test_composes_a_general_when_the_idr_has_none(self):
        children = [
            {"report_type": "SWCB", "report_data": {"description": "Formed sidewalk."}},
            {"report_type": "CONC_MIX", "report_data": {"description": "Addendum-type, left out."}},
        ]
        sheet = exported_workbook(general=None, main_reports=children)["Gen Fr"]
        assert sheet["B22"].value == "Sidewalk, Curb, Concrete Base: Formed sidewalk."
        assert sheet["B23"].value.startswith("See the individual reports")
        assert sheet["AH8"].value is None and sheet["AM8"].value is None  # not one of the IDR's numbered pages

    def test_unknown_idr_raises_not_found(self):
        with patched_export(idr=None), pytest.raises(export.IdrNotFoundError):
            generate_idr_export(IDR_ID)

    def test_draft_idr_raises_not_submitted(self):
        with patched_export(idr={**SUBMITTED_IDR, "status": "draft"}), pytest.raises(export.IdrNotSubmittedError):
            generate_idr_export(IDR_ID)


# ---------------------------------------------------------------------------
# Pay items (Gen Fr)
# ---------------------------------------------------------------------------

def pay_item(n: int, **overrides) -> dict:
    """
    Build a saved pay item.
    Takes a number to make it distinct and field overrides.
    Returns the pay item dict.
    """
    return {"itemNo": f"4.{n:02d} AAS", "budgetCode": "12345", "payQuantity": "312.50", "unit": "S.F.",
            "description": f"Sidewalk {n}", **overrides}


class TestPayItems:
    def test_stamps_each_column_with_the_unit_in_pay_quantity_and_chk_blank(self):
        general = general_with(payItems=[pay_item(1, quantityChk="RM")])
        sheet = exported_workbook(general=general)["Gen Fr"]
        assert [sheet[f"{c}39"].value for c in "BGNSX"] == ["4.01 AAS", "12345", "312.50 S.F.", None, "Sidewalk 1"]
        assert sheet["B40"].value is None

    def test_pay_quantity_without_a_unit_is_just_the_number(self):
        general = general_with(payItems=[pay_item(1, unit=""), pay_item(2, payQuantity="", unit="S.F.")])
        sheet = exported_workbook(general=general)["Gen Fr"]
        assert sheet["N39"].value == "312.50"
        assert sheet["N40"].value == "S.F."

    def test_more_items_than_rows_end_with_a_count(self):
        general = general_with(payItems=[pay_item(n) for n in range(14)])
        sheet = exported_workbook(general=general)["Gen Fr"]
        assert sheet["B49"].value == "4.10 AAS"  # 11 items fit, then the note on the 12th row
        assert [sheet[f"{c}50"].value for c in "BGNS"] == [None] * 4
        assert sheet["X50"].value == "… 3 more items in ICID"

    def test_non_list_pay_items_are_none(self):
        assert pay_item_rows(None) == [] and pay_item_rows({"a": 1}) == [] and pay_item_rows(["x"]) == []

    def test_a_long_description_wraps_on_a_taller_row(self):
        long = 'Thermoplastic Reflectorized Pavement Markings (4" Wide)'  # 55 characters: two lines
        content = export_bytes(general=general_with(payItems=[pay_item(1, description=long)]))
        cell = openpyxl.load_workbook(io.BytesIO(content), read_only=True)["Gen Fr"]["X39"]
        assert cell.value == long
        assert cell.alignment.wrap_text is True
        assert cell.alignment.horizontal == "center"  # the template's centring is kept
        assert gen_front_row_height(content, 39) == 2 * 12.75

    def test_a_short_description_wraps_but_keeps_the_row_height(self):
        content = export_bytes(general=general_with(payItems=[pay_item(1, description="Plastic Barrels")]))
        assert openpyxl.load_workbook(io.BytesIO(content), read_only=True)["Gen Fr"]["X39"].alignment.wrap_text is True
        assert gen_front_row_height(content, 39) == 18.0

    def test_descriptions_shrink_to_8pt_before_being_cut(self):
        level_1 = "Plastic Barrels"
        level_2 = 'Thermoplastic Reflectorized Pavement Markings (4" Wide)'
        level_3 = "Reinforced Concrete Pavement (Full Width Pavement) with dowels and tie bars at all joints"
        level_4 = ('Thermoplastic Reflectorized Pavement Markings (4" Wide) including surface preparation, primer, '
                   "layout and removal of existing markings where shown")
        items = [pay_item(n, description=d) for n, d in enumerate((level_1, level_2, level_3, level_4))]
        content = export_bytes(general=general_with(payItems=items))
        sheet = openpyxl.load_workbook(io.BytesIO(content), read_only=True)["Gen Fr"]
        cells = [sheet[f"X{row}"] for row in (39, 40, 41, 42)]
        assert [c.font.sz for c in cells] == [10, 10, 8, 8]
        assert [gen_front_row_height(content, row) for row in (39, 40, 41, 42)] == [18.0, 25.5, 22.5, 22.5]
        assert [c.value for c in cells[:3]] == [level_1, level_2, level_3]  # not cut
        assert cells[3].value.endswith("preparation...") and len(textwrap.wrap(cells[3].value, 48)) == 2
        assert all(c.alignment.wrap_text for c in cells)

    def test_fit_pay_description_levels(self):
        assert fit_pay_description("Plastic Barrels") == ("Plastic Barrels", 1, 10)
        assert fit_pay_description("word " * 15)[1:] == (2, 10)
        assert fit_pay_description("word " * 18)[1:] == (2, 8)
        text, lines, size = fit_pay_description("word " * 40)
        assert (lines, size) == (2, 8) and text.endswith("word...")

    def test_truncation_ends_on_a_word_and_handles_one_long_word(self):
        cut = truncate_to_lines("alpha beta gamma delta epsilon zeta", width=12, max_lines=2, suffix="...")
        assert cut == "alpha beta gamma..."
        long_word = truncate_to_lines("X" * 130, width=48, max_lines=2, suffix="...")
        assert long_word == "X" * 93 + "..." and len(textwrap.wrap(long_word, 48)) == 2

    def test_empty_pay_item_rows_keep_the_template_style(self):
        content = export_bytes(general=general_with(payItems=[pay_item(1)]))
        template_style = WorkbookTemplate(TEMPLATE).cell_style("Gen Fr", "X40")
        assert gen_front_cell_style(content, "X40") == template_style
        assert gen_front_row_height(content, 40) == 18.0


def gen_front_xml(content: bytes) -> str:
    """
    Read Gen Fr's worksheet XML from an export.
    Takes the .xlsx bytes.
    Returns the XML (Gen Fr is sheet4.xml in the template package).
    """
    return zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet4.xml").decode()


def gen_front_row_height(content: bytes, row: int) -> float:
    """
    Read one Gen Fr row's height from an export (read-only openpyxl doesn't expose row heights).
    Takes the .xlsx bytes and the row number.
    Returns the height in points.
    """
    return float(re.search(rf'<row r="{row}"[^>]*?\sht="([\d.]+)"', gen_front_xml(content)).group(1))


def gen_front_cell_style(content: bytes, coordinate: str) -> str:
    """
    Read one Gen Fr cell's style index from an export.
    Takes the .xlsx bytes and the cell reference.
    Returns the style index.
    """
    return re.search(rf'<c r="{coordinate}"[^>]*?\ss="(\d+)"', gen_front_xml(content)).group(1)


# ---------------------------------------------------------------------------
# Gen Bk: work force, equipment, safety, comments
# ---------------------------------------------------------------------------

class TestGenBack:
    def test_gen_bk_stays_hidden_with_nothing_on_it(self):
        assert visible_sheets(export_bytes()) == ["Gen Fr"]

    def test_workforce_counts_including_legacy_keys(self):
        general = general_with(workforce={"superintendent": "1", "foreman": "2", "laborers": "6", "flaggers": "x2"})
        content = export_bytes(general=general)
        sheet = openpyxl.load_workbook(io.BytesIO(content), read_only=True)["Gen Bk"]
        assert [sheet[f"G{row}"].value for row in range(43, 48)] == [1, 2, None, 6, "x2"]
        assert visible_sheets(content) == ["Gen Fr", "Gen Bk"]

    def test_added_trades_use_printed_rows_then_blank_rows_then_a_count(self):
        trades = [{"label": "Masons", "count": "3"}, {"label": "Chauffeurs", "count": "1"},
                  {"label": "Surveyors", "count": "2"}, {"label": "Welders", "count": "1"}]
        sheet = exported_workbook(general=general_with(additionalWorkforce=trades))["Gen Bk"]
        assert sheet["G50"].value == 3  # Masons' own row
        assert (sheet["B52"].value, sheet["G52"].value) == ("Chauffeurs", 1)
        assert (sheet["B53"].value, sheet["G53"].value) == ("+2 more (see ICID)", None)

    def test_equipment_model_and_number(self):
        general = general_with(equipment={"backhoe": {"model": "CAT 420", "number": "1"}, "excavator": {"model": "", "number": ""}})
        sheet = exported_workbook(general=general)["Gen Bk"]
        assert (sheet["N44"].value, sheet["V44"].value) == ("CAT 420", 1)
        assert (sheet["N49"].value, sheet["V49"].value) == (None, None)

    def test_added_equipment_shares_printed_rows_and_uses_the_blank_row(self):
        extras = [{"label": "Crane", "model": "Grove", "number": "1"},
                  {"label": "Roller – Static", "model": "BW120", "number": "1"},
                  {"label": "Roller – Dynamic", "model": "CB24", "number": "2"},
                  {"label": "Sweepers", "model": "Elgin", "number": "1"}]
        sheet = exported_workbook(general=general_with(additionalEquipment=extras))["Gen Bk"]
        assert (sheet["N45"].value, sheet["V45"].value) == ("Grove", 1)
        # Both rollers on the Roller row, in its two Model / No. pairs, each keeping its variant name
        assert (sheet["N47"].value, sheet["V47"].value) == ("Roller – Static BW120", 1)
        assert (sheet["Y47"].value, sheet["AG47"].value) == ("Roller – Dynamic CB24", 2)
        assert (sheet["I53"].value, sheet["N53"].value, sheet["V53"].value) == ("Sweepers", "Elgin", 1)

    def test_safety_checklist_marks_y_or_n_and_writes_remarks(self):
        general = general_with(
            safetyChecks={"plasticBarrels": "Y", "fencing": "N", "plates": "NA", "arrowBoard": True, "timberCurbs": None},
            safetyRemarks={"fencing": "Gap at gate", "plates": "None on site", "generalSafety": "Good"},
        )
        sheet = exported_workbook(general=general)["Gen Bk"]
        row = lambda r: (sheet[f"N{r}"].value, sheet[f"P{r}"].value, sheet[f"R{r}"].value)
        assert row(30) == ("X", None, None)                      # plasticBarrels: Y
        assert row(36) == (None, "X", "Gap at gate")             # fencing: N
        assert row(37) == (None, None, "N/A — None on site")      # plates: N/A has no column on the form
        assert row(38) == ("X", None, None)                      # arrowBoard: older reports' True
        assert row(32) == (None, None, None)                     # timberCurbs: unanswered
        assert row(34) == (None, None, "Good")                   # a remark without an answer

    def test_comments_go_on_the_back_without_continuing(self):
        content = export_bytes(general=general_with(description="Short.", comments="Visitor from DEP at 10am."))
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        assert workbook["Gen Bk"]["B3"].value == "Visitor from DEP at 10am."
        assert workbook["Gen Bk"]["B4"].value is None
        assert workbook["Gen Bk"]["Z27"].value is None
        assert workbook["Gen Fr"]["AC36"].value is None  # the description fit on the front
        assert visible_sheets(content) == ["Gen Fr", "Gen Bk"]


# ---------------------------------------------------------------------------
# Description of Work overflow: Gen Fr → Gen Bk → Report Cont
# ---------------------------------------------------------------------------

def words(count: int, start: int = 0) -> str:
    """
    Make filler text of distinct words.
    Takes how many words and the first word's number.
    Returns the text.
    """
    return " ".join(f"word{n}" for n in range(start, start + count))


class TestDescriptionCascade:
    def test_level_1_fits_on_the_front(self, full_export):
        content, workbook = full_export
        sheet = workbook["Gen Fr"]
        assert sheet["B22"].value == "Poured curb along Main St."
        assert sheet["AC36"].value is None
        assert visible_sheets(content) == ["Gen Fr"]
        # The template centres the first line; every description line is left-aligned
        assert [sheet[f"B{row}"].alignment.horizontal for row in (22, 23, 34)] == ["left"] * 3

    def test_level_2_continues_on_the_back_and_ticks_reverse_page_used(self):
        content = export_bytes(general=general_with(description=words(110), comments="Visitor."))
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        assert workbook["Gen Fr"]["AC36"].value == "X"
        back = [workbook["Gen Bk"][f"B{row}"].value for row in range(3, 7)]
        assert back[0] == "Description of work (continued):"
        assert back[-2:] == ["Comments:", "Visitor."]
        assert workbook["Gen Bk"]["Z27"].value is None
        assert visible_sheets(content) == ["Gen Fr", "Gen Bk"]

    def test_level_3_continues_on_report_cont_and_ticks_continued(self, full_cascade):
        content, workbook = full_cascade
        assert workbook["Gen Fr"]["AC36"].value == "X"
        assert workbook["Gen Bk"]["Z27"].value == "X"
        assert workbook["Report Cont"]["B21"].value.startswith("word")
        assert visible_sheets(content) == ["Gen Fr", "Gen Bk", "Report Cont"]

    def test_text_past_report_cont_is_cut_with_a_note(self, full_cascade):
        sheet = full_cascade[1]["Report Cont"]
        assert sheet["B45"].value.endswith("… (continued in ICID)")
        assert len(sheet["B45"].value) <= REPORT_CONT_LINE_CHARS

    def test_report_cont_header_is_stamped_and_placeholders_cleared(self, full_cascade):
        sheet = full_cascade[1]["Report Cont"]
        assert sheet["I10"].value == "9/30/26"
        assert sheet["L11"].fill.fill_type == "solid"  # Wednesday
        assert sheet["U10"].value is None and sheet["AA10"].value == "Sheet No.:"
        assert [sheet[c].value for c in ("G14", "P14", "F17", "H19")] == ["HWS0023", "2024123457", "Queens", "Genghis Khan"]

    def test_continuation_lines_are_left_aligned(self, full_cascade):
        workbook = full_cascade[1]
        assert workbook["Gen Bk"]["B3"].alignment.horizontal == "left"  # the template centres these
        assert workbook["Report Cont"]["B21"].alignment.horizontal == "left"

    def test_report_cont_prints_after_the_general_pages(self, full_cascade):
        workbook = full_cascade[1]
        names = workbook.sheetnames
        assert names.index("Gen Fr") < names.index("Gen Bk") < names.index("Report Cont")
        assert workbook.active.title == "Gen Fr"
        # The one sheet-scoped name (AC Fr's print area) still points at AC Fr after the move
        xml = zipfile.ZipFile(io.BytesIO(full_cascade[0])).read("xl/workbook.xml").decode()
        assert re.search(r'localSheetId="(\d+)"', xml).group(1) == str(names.index("AC Fr"))

    def test_control_characters_do_not_break_the_file(self):
        general = general_with(description="Bad \x01char & <tag>")
        assert exported_workbook(general=general)["Gen Fr"]["B22"].value == "Bad char & <tag>"


class TestTextLayout:
    def test_fill_lines_wraps_and_leaves_the_rest_queued(self):
        queue = [words(30), "Second paragraph."]
        lines = fill_lines(queue, capacity=2, width=40)
        assert len(lines) == 2 and all(len(line) <= 40 for line in lines)
        assert queue[0].startswith("word") and queue[1] == "Second paragraph."

    def test_each_paragraph_starts_a_new_line(self):
        assert fill_lines(["One.", "Two."], capacity=5, width=40) == ["One.", "Two."]

    def test_paragraphs_ignores_blank_lines_and_non_text(self):
        assert paragraphs("A\n\n  B  \n") == ["A", "B"]
        assert paragraphs(None) == [] and paragraphs({"a": 1}) == []


# ---------------------------------------------------------------------------
# The XML writer
# ---------------------------------------------------------------------------

def written(workbook: WorkbookTemplate) -> openpyxl.Workbook:
    """
    Serialize a WorkbookTemplate and open it read-only.
    Takes the workbook.
    Returns the loaded workbook.
    """
    return openpyxl.load_workbook(io.BytesIO(workbook.to_bytes()), read_only=True)


class TestWorkbookTemplate:
    def test_a_value_replaces_a_formula(self):
        workbook = WorkbookTemplate(TEMPLATE)
        assert workbook.cell_style("Gen Fr", "G8") is not None
        workbook.set_cell("Gen Fr", "G8", "HWS0023")
        sheet = written(workbook)["Gen Fr"]
        assert sheet["G8"].value == "HWS0023"
        assert sheet["P8"].value == "='Contract Info'!C3"  # untouched neighbours keep their formulas

    def test_left_alignment_keeps_the_other_alignment_settings(self):
        workbook = WorkbookTemplate(TEMPLATE)
        workbook.align_left("Gen Fr", "AD17")  # a centred, wrapped cell
        cell = written(workbook)["Gen Fr"]["AD17"]
        assert cell.alignment.horizontal == "left"
        assert cell.alignment.wrap_text is True

    def test_derived_styles_are_shared(self):
        workbook = WorkbookTemplate(TEMPLATE)
        style = workbook.cell_style("Gen Fr", "B23")
        assert workbook.left_aligned_style(style) == workbook.left_aligned_style(style)

    def test_wrap_text_style_on_the_default_style(self):
        workbook = WorkbookTemplate(TEMPLATE)
        style = workbook.wrap_text_style(None)
        styles = zipfile.ZipFile(io.BytesIO(workbook.to_bytes())).read("xl/styles.xml").decode()
        cell_xfs = re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", styles, re.DOTALL)
        assert re.search(r'count="(\d+)"', cell_xfs.group(0)).group(1) == str(int(style) + 1)
        new_xf = re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", cell_xfs.group(1), re.DOTALL)[int(style)]
        assert 'applyAlignment="1"' in new_xf and '<alignment wrapText="1"/>' in new_xf

    def test_wrap_style_is_shared_and_keeps_left_alignment(self):
        workbook = WorkbookTemplate(TEMPLATE)
        left = workbook.left_aligned_style(workbook.cell_style("Gen Fr", "B23"))
        assert workbook.wrap_text_style(left) == workbook.wrap_text_style(left)
        workbook.set_style("Gen Fr", "B23", workbook.wrap_text_style(left))
        cell = written(workbook)["Gen Fr"]["B23"]
        assert (cell.alignment.horizontal, cell.alignment.wrap_text) == ("left", True)

    def test_font_size_style_points_at_a_new_8pt_font(self):
        workbook = WorkbookTemplate(TEMPLATE)
        style = workbook.font_size_style(None, 8)
        assert workbook.font_size_style(None, 8) == style  # shared, not appended twice
        styles = zipfile.ZipFile(io.BytesIO(workbook.to_bytes())).read("xl/styles.xml").decode()
        fonts_block = re.search(r"<fonts\b[^>]*>(.*?)</fonts>", styles, re.DOTALL)
        fonts = re.findall(r"<font\b[^>]*?(?:/>|>.*?</font>)", fonts_block.group(1), re.DOTALL)
        assert re.search(r'count="(\d+)"', fonts_block.group(0)).group(1) == str(len(fonts))
        xf = re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", styles, re.DOTALL).group(1), re.DOTALL)[int(style)]
        new_font = fonts[int(re.search(r'fontId="(\d+)"', xf).group(1))]
        assert '<sz val="8"/>' in new_font and '<name val="Arial"/>' in new_font
        assert 'applyFont="1"' in xf
        assert '<sz val="10"/>' in fonts[0]  # the shared default font is left alone

    def test_set_font_size_keeps_the_cell_wrapping(self):
        workbook = WorkbookTemplate(TEMPLATE)
        workbook.wrap_cell("Gen Fr", "X39")
        workbook.set_font_size("Gen Fr", "X39", 8)
        cell = written(workbook)["Gen Fr"]["X39"]
        assert (cell.font.sz, cell.alignment.wrap_text, cell.alignment.horizontal) == (8, True, "center")

    def test_set_row_height_fixes_the_height(self):
        workbook = WorkbookTemplate(TEMPLATE)
        workbook.set_row_height("Gen Fr", 39, 25.5)
        assert gen_front_row_height(workbook.to_bytes(), 39) == 25.5

    def test_the_calculation_chain_is_dropped(self):
        package = zipfile.ZipFile(io.BytesIO(WorkbookTemplate(TEMPLATE).to_bytes()))
        assert "xl/calcChain.xml" not in package.namelist()
        assert "calcChain" not in package.read("xl/_rels/workbook.xml.rels").decode()
        assert "calcChain" not in package.read("[Content_Types].xml").decode()


# ---------------------------------------------------------------------------
# get_project_contractor_name (query)
# ---------------------------------------------------------------------------

class TestProjectContractorQuery:
    def test_returns_the_contractor_client_name(self):
        with patch("api.queries.projects.run_query", return_value=[{"client_name": "Benny Bowers Contracting Co."}]) as mock:
            assert get_project_contractor_name("HWS0023") == "Benny Bowers Contracting Co."
        sql, params = mock.call_args.args
        assert "FROM icid.project_clients pc" in sql and "JOIN icid.clients c" in sql
        assert "pc.client_role = 'Contractor'" in sql
        assert params == ("HWS0023",)

    def test_returns_none_without_a_contractor(self):
        with patch("api.queries.projects.run_query", return_value=[]):
            assert get_project_contractor_name("HWS0023") is None


# ---------------------------------------------------------------------------
# GET /v1/idrs/{idr_id}/export
# ---------------------------------------------------------------------------

class TestExportEndpoint:
    url = f"/v1/idrs/{IDR_ID}/export"

    def test_submitted_idr_downloads_as_xlsx(self, client):
        with patched_export():
            response = client.get(self.url)
        assert response.status_code == 200
        assert response.headers["content-type"] == export_media_type()
        assert response.headers["content-disposition"] == f'attachment; filename="IDR_{IDR_ID}_2026-09-30.xlsx"'
        workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True)
        assert workbook["Contract Info"]["C2"].value == "HWS0023"

    def test_draft_idr_is_409(self, client):
        with patched_export(idr={**SUBMITTED_IDR, "status": "draft"}):
            response = client.get(self.url)
        assert response.status_code == 409
        assert response.json()["detail"] == "Only submitted IDRs can be exported"

    def test_unknown_idr_is_404(self, client):
        with patched_export(idr=None):
            assert client.get(self.url).status_code == 404

    def test_missing_project_is_500(self, client):
        with patched_export(project=None):
            assert client.get(self.url).status_code == 500


def export_media_type() -> str:
    """
    The .xlsx media type the export endpoint sends.
    Takes nothing.
    Returns the MIME type string.
    """
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
