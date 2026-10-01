import io
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
from api.services.export import DESCRIPTION_LINE_CHARS, DESCRIPTION_ROWS, description_lines, generate_idr_export

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
# generate_idr_export
# ---------------------------------------------------------------------------

class TestGenerateIdrExport:
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

    def test_stamps_the_general_front_header(self):
        sheet = exported_workbook()["Gen Fr"]
        assert sheet["AI4"].value == datetime(2026, 9, 30)
        assert sheet["AH8"].value == 1 and sheet["AM8"].value == 3
        assert sheet["AG10"].value == "( Start 07:00 End 15:30 )"
        assert sheet["AG12"].value == "( Start 06:45 End ________ )"
        assert sheet["AD13"].value == "Low  45"
        assert sheet["AK13"].value == "High  62.5"
        assert sheet["AD17"].value == "Cloudy" and sheet["AK17"].value == "Rain"
        assert sheet["H17"].value == "Genghis Khan"
        assert sheet["AH6"].value is None  # no I.R. No. in the data model yet

    def test_highlights_only_the_day_of_week(self, full_export):
        sheet = full_export[1]["Gen Fr"]
        highlighted = [c for c in ("AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5") if sheet[c].fill.fill_type == "solid"]
        assert highlighted == ["AL5"]
        assert sheet["AL5"].value == "W"
        assert sheet["AL5"].border.left.style == "medium"

    def test_writes_the_description_on_the_ruled_lines(self):
        sheet = exported_workbook()["Gen Fr"]
        assert sheet["B22"].value == "Poured curb along Main St."
        assert sheet["B23"].value == "Inspected forms before the pour."
        assert sheet["B24"].value is None

    def test_composes_a_general_when_the_idr_has_none(self):
        children = [
            {"report_type": "SWCB", "report_data": {"description": "Formed sidewalk."}},
            {"report_type": "CONC_MIX", "report_data": {"description": "Addendum-type, left out."}},
        ]
        sheet = exported_workbook(general=None, main_reports=children)["Gen Fr"]
        assert sheet["B22"].value == "Sidewalk, Curb, Concrete Base: Formed sidewalk."
        assert sheet["B23"].value.startswith("See the individual reports")
        # A composed General isn't one of the IDR's numbered pages
        assert sheet["AH8"].value is None and sheet["AM8"].value is None

    def test_missing_optional_fields_leave_the_template_blanks(self):
        idr = {**SUBMITTED_IDR, "work_start_time": None, "work_end_time": None, "temp_low": None, "weather_am": None}
        sheet = exported_workbook(idr=idr, contractor=None, user=None)["Gen Fr"]
        assert sheet["AG10"].value == "( Start ________ End ________ )"
        assert sheet["AD13"].value == "Low"
        assert sheet["AD17"].value is None
        assert sheet["H17"].value is None

    def test_sets_letter_portrait_one_page_print_setup_on_gen_fr(self, full_export):
        sheet = full_export[1]["Gen Fr"]
        assert sheet.page_setup.paperSize == 1  # US Letter
        assert sheet.page_setup.orientation == "portrait"
        assert sheet.sheet_properties.pageSetUpPr.fitToPage is True
        assert (sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight) == (1, 1)

    def test_shows_only_gen_fr_and_keeps_every_drawing(self, full_export):
        content, workbook = full_export
        assert [ws.title for ws in workbook.worksheets if ws.sheet_state == "visible"] == ["Gen Fr"]
        assert workbook.active.title == "Gen Fr"
        assert drawing_parts(content) == drawing_parts(TEMPLATE.read_bytes())

    def test_unknown_idr_raises_not_found(self):
        with patched_export(idr=None), pytest.raises(export.IdrNotFoundError):
            generate_idr_export(IDR_ID)

    def test_draft_idr_raises_not_submitted(self):
        with patched_export(idr={**SUBMITTED_IDR, "status": "draft"}), pytest.raises(export.IdrNotSubmittedError):
            generate_idr_export(IDR_ID)


class TestDescriptionLines:
    def test_wraps_long_paragraphs_to_the_line_width(self):
        lines = description_lines("word " * 40)
        assert len(lines) > 1
        assert all(len(line) <= DESCRIPTION_LINE_CHARS for line in lines)

    def test_cuts_overflow_with_a_continued_note(self):
        lines = description_lines("\n".join(f"Line {n}" for n in range(30)))
        assert len(lines) == len(DESCRIPTION_ROWS)
        assert lines[-1].endswith("(continued in ICID)")

    def test_non_text_description_is_empty(self):
        assert description_lines(None) == [] and description_lines({"a": 1}) == []

    def test_control_characters_do_not_break_the_file(self):
        general = {**GENERAL, "report_data": {"description": "Bad \x01char & <tag>"}}
        assert exported_workbook(general=general)["Gen Fr"]["B22"].value == "Bad char & <tag>"


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
