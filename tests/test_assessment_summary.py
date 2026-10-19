import io
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import openpyxl
import pytest

from api.services.assessment_summary import (
    EMPTY_PROJECT,
    EMPTY_RANGE,
    MonthError,
    build_workbook,
    parse_month,
    summary_filename,
    summary_months,
)

# ---------------------------------------------------------------------------
# summary_months and build_workbook: rows as list_assessment_flags returns them
# ---------------------------------------------------------------------------

RATES = {"cost_per_sf_4in": Decimal("11.54"), "cost_per_sf_7in": Decimal("12.64")}
MAIN = UUID("20000000-0000-4000-8000-000000000001")
ELM = UUID("20000000-0000-4000-8000-000000000002")
OCT_15 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
NOV_2 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
PROJECT = {"project_id": "SE384", "project_name": "Sidewalks Queens", "registration_code": "2024123456",
           "borough": "Queens", **RATES}


def flag(property_id: UUID, thickness: str, gross: str, quantity: str, assessable: bool = True,
         constructed: date = None, approved: datetime = OCT_15, address: str = None) -> dict:
    """
    Build a row as list_assessment_flags returns one.
    Takes its property, thickness, gross area, quantity, whether surveyed assessable, the property's Date Constructed, when its IDR was approved and the address.
    Returns the row.
    """
    return {"idr_id": UUID("1d000000-0000-4000-8000-000000000001"), "re_signed_at": approved,
            "property_id": property_id, "property_kind": "sidewalk",
            "address": address or ("138-22 227 STREET" if property_id == MAIN else "350 Main St, Brooklyn"),
            "intersection_street_1": None, "intersection_street_2": None, "quadrant": None, "facing_street": None,
            "date_constructed": constructed, "surveyed_thickness": thickness, "surveyed_is_assessable": assessable,
            "computed_quantity": Decimal(gross), "quantity": Decimal(quantity)}


# SE384 as it stands: 138-22 227 Street (a non-assessable 4", an assessable 4", 7") and 350 Main St (4", 7")
SE384 = [flag(MAIN, "4in", "90.25", "90.25", assessable=False), flag(MAIN, "4in", "160.69", "113.19"),
         flag(MAIN, "7in", "171.81", "94.14"), flag(ELM, "4in", "180", "180"), flag(ELM, "7in", "45", "45")]


def tab(content: bytes, name: str):
    """
    Open a workbook and find a tab.
    Takes the file's bytes and the tab's name.
    Returns the worksheet.
    """
    return openpyxl.load_workbook(io.BytesIO(content))[name]


def values(sheet, row: int) -> list:
    """
    Read one row's twelve cells.
    Takes the sheet and the row number.
    Returns the values.
    """
    return [sheet.cell(row, column).value for column in range(1, 13)]


class TestGrouping:
    def test_se384_is_one_month_with_its_two_properties(self):
        months = summary_months(SE384, RATES)
        assert list(months) == [date(2026, 10, 1)]
        main, elm = months[date(2026, 10, 1)]
        assert (main.four_assessable, main.four_non_assessable, main.four_total) == (
            Decimal("113.19"), Decimal("137.75"), Decimal("250.94"))
        assert main.four_amount == Decimal("113.19") * Decimal("11.54")
        assert round(main.grand_total, 2) == Decimal("2496.14")
        assert round(elm.grand_total, 2) == Decimal("2646.00") and elm.four_non_assessable == 0
        assert main.remarks == [] and elm.remarks == []

    def test_rows_are_ordered_by_address_any_case(self):
        flags = [flag(ELM, "4in", "1", "1", address="b street"), flag(MAIN, "4in", "1", "1", address="A Street")]
        assert [row.address for row in summary_months(flags, RATES)[date(2026, 10, 1)]] == ["A Street", "b street"]

    def test_two_months_are_two_lists_with_nothing_shared(self):
        flags = [flag(MAIN, "4in", "10", "10"), flag(MAIN, "4in", "20", "20", approved=NOV_2)]
        months = summary_months(flags, RATES)
        assert list(months) == [date(2026, 10, 1), date(2026, 11, 1)]
        assert months[date(2026, 10, 1)][0].four_assessable == 10
        assert months[date(2026, 11, 1)][0].four_assessable == 20

    def test_the_date_constructed_decides_the_month_over_the_approval(self):
        months = summary_months([flag(MAIN, "4in", "10", "10", constructed=date(2026, 9, 28))], RATES)
        assert list(months) == [date(2026, 9, 1)]
        assert months[date(2026, 9, 1)][0].used_approval_date is False

    def test_without_one_the_approval_day_in_new_york_decides(self):
        # 02:00 UTC on Nov 1 is Oct 31 in New York
        months = summary_months([flag(MAIN, "4in", "10", "10", approved=datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc))],
                                RATES)
        assert list(months) == [date(2026, 10, 1)]
        assert months[date(2026, 10, 1)][0].used_approval_date is True

    def test_7in_assessed_as_4in_counts_in_the_4in_columns_with_a_remark(self):
        row = summary_months([flag(MAIN, "7in_as_4in", "50", "50")], RATES)[date(2026, 10, 1)][0]
        assert (row.four_assessable, row.four_total, row.seven_total) == (Decimal(50), Decimal(50), 0)
        assert row.four_amount == Decimal(50) * Decimal("11.54")
        assert row.remarks == ['50.00 SF built at 7" billed at 4"']

    def test_a_non_assessable_area_is_in_non_assessable_and_not_in_the_amount(self):
        row = summary_months([flag(MAIN, "4in", "25", "20")], RATES)[date(2026, 10, 1)][0]
        assert (row.four_assessable, row.four_non_assessable) == (Decimal(20), Decimal(5))
        assert row.four_amount == Decimal(20) * Decimal("11.54")

    def test_a_missing_rate_leaves_its_amount_blank_with_a_remark(self):
        row = summary_months(SE384[:3], {**RATES, "cost_per_sf_4in": None})[date(2026, 10, 1)][0]
        assert row.four_amount is None and row.seven_amount is not None
        assert row.grand_total == row.seven_amount
        assert row.remarks == ['4" rate not set']

    def test_the_range_keeps_its_months_only(self):
        flags = [flag(MAIN, "4in", "10", "10"), flag(MAIN, "4in", "20", "20", approved=NOV_2)]
        assert list(summary_months(flags, RATES, first=date(2026, 11, 1))) == [date(2026, 11, 1)]
        assert list(summary_months(flags, RATES, last=date(2026, 10, 1))) == [date(2026, 10, 1)]
        assert summary_months(flags, RATES, last=date(2026, 9, 1)) == {}


class TestParseMonth:
    def test_a_month_is_its_first_day(self):
        assert parse_month("2026-10", "from") == date(2026, 10, 1)
        assert parse_month(None, "from") is None and parse_month("  ", "to") is None

    @pytest.mark.parametrize("value", ["10-2026", "2026-13", "2026-1", "2026/10", "Oct 2026"])
    def test_anything_else_is_refused(self, value):
        with pytest.raises(MonthError, match="from must be YYYY-MM"):
            parse_month(value, "from")


class TestWorkbook:
    def test_se384_has_one_tab_with_the_header_rows_and_totals(self):
        content = build_workbook(PROJECT, summary_months(SE384, RATES), filtered=False)
        book = openpyxl.load_workbook(io.BytesIO(content))
        assert book.sheetnames == ["2026-10"]
        sheet = book["2026-10"]
        assert sheet["A1"].value == "Sidewalks Queens · Project ID: SE384 · Reg: 2024123456 · Borough: Queens"
        assert (sheet["A2"].value, sheet["C2"].value, sheet["G2"].value) == (
            "October 2026", '4" rate: $11.54', '7" rate: $12.64')
        assert values(sheet, 3)[:3] == ["Property Address", "CB", '4" Assessable (SF)']
        assert values(sheet, 3)[11] == "Remarks"
        assert sheet.freeze_panes == "A4"
        main = values(sheet, 4)
        assert main[0] == "138-22 227 STREET" and main[1] is None  # no CB set
        assert [round(Decimal(str(v)), 2) for v in main[2:11]] == [
            Decimal("113.19"), Decimal("137.75"), Decimal("250.94"), Decimal("1306.21"), Decimal("94.14"),
            Decimal("77.67"), Decimal("171.81"), Decimal("1189.93"), Decimal("2496.14")]
        elm = values(sheet, 5)
        assert elm[0] == "350 Main St, Brooklyn" and elm[3] == "—"  # no non-assessable square feet
        totals = values(sheet, 6)
        assert totals[0] == "Totals" and totals[1] is None
        assert sheet["A6"].font.bold and sheet["A6"].fill.fgColor.rgb.endswith("D9D9D9")
        assert round(Decimal(str(totals[2])), 2) == Decimal("293.19")
        assert round(Decimal(str(totals[3])), 2) == Decimal("137.75")
        assert round(Decimal(str(totals[6])), 2) == Decimal("139.14")
        assert round(Decimal(str(totals[10])), 2) == Decimal("5142.14")
        assert sheet["C4"].number_format == "#,##0.00" and sheet["F4"].number_format == "$#,##0.00"
        assert sheet["J4"].number_format == "$#,##0.00" and sheet["K4"].number_format == "$#,##0.00"
        assert sheet["A8"].value == "2 rows used IDR approval date (no page-level Date Constructed)."
        assert len(content) < 30_000

    def test_cells_keep_full_precision(self):
        content = build_workbook(PROJECT, summary_months([flag(MAIN, "4in", "1.005", "1.005")], RATES), False)
        assert tab(content, "2026-10")["C4"].value == pytest.approx(1.005)

    def test_each_row_carries_its_propertys_community_board(self):
        flags = [{**flag(MAIN, "4in", "10", "10"), "community_board": "QN13"}]
        sheet = tab(build_workbook(PROJECT, summary_months(flags, RATES), False), "2026-10")
        assert sheet["B3"].value == "CB" and sheet["B4"].value == "QN13"

    def test_with_dates_typed_there_is_no_footer_note(self):
        flags = [flag(MAIN, "4in", "10", "10", constructed=date(2026, 10, 3))]
        sheet = tab(build_workbook(PROJECT, summary_months(flags, RATES), False), "2026-10")
        assert sheet["A7"].value is None

    def test_a_missing_rate_reads_as_a_dash_in_the_header(self):
        project = {**PROJECT, "cost_per_sf_7in": None}
        sheet = tab(build_workbook(project, summary_months(SE384, {**RATES, "cost_per_sf_7in": None}), False), "2026-10")
        assert sheet["G2"].value == '7" rate: —'
        assert sheet["J4"].value is None and sheet["L4"].value == '7" rate not set'

    def test_two_months_are_two_tabs(self):
        flags = [flag(MAIN, "4in", "10", "10"), flag(MAIN, "4in", "20", "20", approved=NOV_2)]
        book = openpyxl.load_workbook(io.BytesIO(build_workbook(PROJECT, summary_months(flags, RATES), False)))
        assert book.sheetnames == ["2026-10", "2026-11"]
        assert book["2026-11"]["A2"].value == "November 2026"

    @pytest.mark.parametrize("filtered, message", [(False, EMPTY_PROJECT), (True, EMPTY_RANGE)])
    def test_nothing_is_one_no_activity_tab(self, filtered, message):
        book = openpyxl.load_workbook(io.BytesIO(build_workbook(PROJECT, {}, filtered)))
        assert book.sheetnames == ["No activity"]
        assert book["No activity"]["A4"].value == message and book["No activity"]["A4"].font.italic
        assert book["No activity"]["A1"].value.startswith("Sidewalks Queens")


def test_the_file_is_named_for_the_project_and_the_day_in_new_york():
    # 03:00 UTC on Oct 17 is still Oct 16 in New York
    assert summary_filename("SE384", datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)) == \
        "SE384_assessment_summary_2026-10-16.xlsx"
