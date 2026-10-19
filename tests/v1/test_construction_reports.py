import io
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import pypdfium2 as pdfium
import pytest
from PIL import Image

from api.services import construction_report, signatures
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET /v1/properties/{property_id}/construction-report and GET /v1/idrs/{idr_id}/construction-reports
# The report statements are answered by SQL shape (report_query); the PDFs are read back as text with pypdfium2.
# ---------------------------------------------------------------------------

REPORT = "api.queries.construction_report.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
IDRS = "api.queries.idrs.run_query"

PROPERTY_ID = UUID("404e6cd0-75f8-4cbd-b5bf-efe8a023d3d7")
OTHER_PROPERTY_ID = UUID("967b3653-7cce-44d3-941a-e86c8aa76e54")
IDR_ID = UUID("1d000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("50000000-0000-4000-8000-000000000001")
URL = f"/v1/properties/{PROPERTY_ID}/construction-report"
ZIP_URL = f"/v1/idrs/{IDR_ID}/construction-reports"
SIGNED = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}

PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "SE384", "kind": "sidewalk", "boro": "Queens", "block": "13140",
                "lot": "49", "address": "138-22 227 STREET", "deleted_at": None}
HEADER = {
    "property_id": PROPERTY_ID, "project_id": "SE384", "property_kind": "sidewalk", "boro": "Queens", "block": "13140",
    "lot": "49", "property_type": "1_3_family", "address": "138-22 227 STREET", "intersection_street_1": None, "intersection_street_2": None,
    "quadrant": None, "facing_street": None, "project_name": "Sidewalks Queens",
    "cost_per_sf_4in": Decimal("11.5400"), "cost_per_sf_7in": Decimal("12.6400"),
    "pir_id": UUID("6f000000-0000-4000-8000-000000000001"), "inspector_initials": "RC",
    "inspector_sign_date": date(2026, 10, 15), "digitized_at": SIGNED, "north_bearing": "N",
    "digitizer_first_name": "Genghis", "digitizer_last_name": "Khan",
    "idr_id": IDR_ID, "idr_number": "001", "report_date": date(2026, 10, 15),
    "inspector_signature_path": f"idrs/{IDR_ID}/inspector_abc.png", "re_signed_at": SIGNED,
    "reporter_first_name": "Reza", "reporter_last_name": "Mousavian", "re_first_name": "Ada", "re_last_name": "Admin",
    "tb_contract_no": "HWS2014Q", "tb_violation_no": "61188", "tb_cross_street_left": "139 AVENUE",
    "tb_cross_street_right": "138 AVENUE", "tb_frontage_street": "227 STREET", "tb_tax_map_width_ft": Decimal("40"),
    "tb_cc_curb_to_curb_ft": Decimal("34.25"), "tb_property_status": "all_defects_corrected",
    "tb_corner_property_status": None, "tb_violation_not_removed_due_to": ["tree", "something else"],
    "tb_date_notice_issued": date(2026, 9, 1), "tb_date_excavated": None, "tb_date_constructed": date(2026, 10, 3),
}


def flag_row(n: int, thickness: str, gross: str, quantity: str, assessable: bool = True, x: float = 0) -> dict:
    """
    Build a row as list_report_flags returns one: a 5 x 5 ft rectangle at 24 px/ft.
    Takes its drawing order, thickness, gross area, quantity, whether surveyed assessable and its x offset in feet.
    Returns the row.
    """
    return {"id": UUID(f"f0000000-0000-4000-8000-00000000000{n}"), "pir_page_id": PAGE_ID, "page_number": 1,
            "scale_px_per_ft": Decimal("24"), "rotation_deg": 0, "display_order": n, "shape": "rectangle",
            "dimensions": {"L": 5, "W": 5}, "sketch_x": 200 + x * 24, "sketch_y": 200.0, "rotation": 0.0,
            "surveyed_thickness": thickness, "surveyed_is_assessable": assessable,
            "computed_quantity": Decimal(gross), "quantity": Decimal(quantity), "areas": []}


# 138-22 227 Street's claimed flags: a non-assessable 4", an assessable 4" and three 7"
FLAGS = [flag_row(1, "4in", "90.25", "90.25", assessable=False), flag_row(2, "4in", "160.69", "113.19", x=6),
         flag_row(3, "7in", "60", "40", x=12), flag_row(4, "7in", "60", "30", x=18), flag_row(5, "7in", "51.81", "24.14", x=24)]
DRAWINGS = {"features": [{"feature_type": "hydrant", "area_impact": "marker_only", "sketch_x": 300, "sketch_y": 250,
                          "rotation": 0, "shape": None, "dimensions": None, "label": None}],
            "segments": [{"segment_type": "curb_concrete", "points": [{"x": 100, "y": 300}, {"x": 900, "y": 300}]}]}
IDR_ROW = {"idr_id": IDR_ID, "idr_number": "001", "project_id": "SE384", "status": "approved", "deleted_at": None,
           "reporter_uuid": INSPECTOR_ROW["uuid"]}


def report_query(found=({"idr_id": IDR_ID},), header=HEADER, flags=FLAGS, drawings=DRAWINGS, properties=None):
    """
    Answer the report statements by their shape.
    Takes what each returns: find_report_idr, get_report_header, list_report_flags, list_page_drawings and list_idr_report_properties.
    Returns the side effect for the patched run_query.
    """
    def answer(sql: str, params=None):
        if "SELECT i.idr_id" in sql:
            return list(found)
        if "pr.project_name" in sql:
            return [header] if header else []
        if "AS areas" in sql:
            return flags
        if "AS features" in sql:
            return [drawings]
        if "WHERE p.id IN" in sql:
            return properties or []
        raise AssertionError(f"unexpected statement: {sql[:80]}")
    return answer


def png() -> bytes:
    """
    Make a small signature PNG.
    Takes nothing.
    Returns its bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (120, 40), (0, 0, 120, 255)).save(buffer, "PNG")
    return buffer.getvalue()


def pdf_text(content: bytes) -> str:
    """
    Read a PDF's text back.
    Takes the PDF's bytes.
    Returns its first page's text, its lines joined by spaces.
    """
    page = pdfium.PdfDocument(content)[0]
    return " ".join(page.get_textpage().get_text_range().split())


def image_count(content: bytes) -> int:
    """
    Count the images on a PDF's first page.
    Takes the PDF's bytes.
    Returns how many there are.
    """
    page = pdfium.PdfDocument(content)[0]
    return sum(1 for obj in page.get_objects() if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE)


@pytest.fixture
def storage():
    """Answer the signature fetch with a PNG."""
    with patch.object(signatures, "download_file", return_value=png()) as fetch:
        yield fetch


# ---------------------------------------------------------------------------
# GET /v1/properties/{property_id}/construction-report
# ---------------------------------------------------------------------------

class TestPropertyReport:
    def test_it_is_a_pdf_named_for_the_property(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            response = admin_client.get(URL)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == 'attachment; filename="138-22_227_STREET_construction_report.pdf"'
        assert response.content.startswith(b"%PDF") and len(response.content) > 1000

    def test_it_prints_the_property_and_the_title_block(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            text = pdf_text(admin_client.get(URL).content)
        for expected in ("CONSTRUCTION REPORT", "1-3 FAMILY", "Sheet 1 of 1", "HWS2014Q", "61188", "Queens",
                         "138-22 227 STREET", "13140", "49", "2026-09-01", "2026-10-03", "RC 2026-10-15",
                         "Genghis Khan 2026-10-15", "Ada Admin 2026-10-15", "139 AVENUE", "227 STREET"):
            assert expected in text, expected

    def test_it_prints_the_construction_measure(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            text = pdf_text(admin_client.get(URL).content)
        # 4": 250.94 gross, 137.75 not assessable (the non-assessable flag and 47.50 of the other), 113.19 at $11.54
        assert "250.94 137.75 113.19 $11.54 $1,306.21" in text
        # 7" assessed as 4": nothing; 7": 171.81 gross, 94.14 assessable at $12.64
        assert "— — — $11.54 —" in text
        assert "171.81 77.67 94.14 $12.64 $1,189.93" in text
        assert "Estimated Sq.Ft 207.33" in text and "$2,496.14" in text  # the assessable total

    def test_it_is_certified_by_the_idrs_inspector_on_the_day_it_was_approved(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            content = admin_client.get(URL).content
        text = pdf_text(content)
        assert "Inspected By Reza Mousavian" in text and "IR # 001" in text and "RM 2026-10-15" in text
        storage.assert_called_once_with(f"idrs/{IDR_ID}/inspector_abc.png", "signatures")
        assert image_count(content) == 1

    def test_a_signature_that_cant_be_fetched_leaves_the_line_blank(self, admin_client):
        with patch.object(signatures, "download_file", side_effect=RuntimeError("gone")), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            response = admin_client.get(URL)
        assert response.status_code == 200 and image_count(response.content) == 0

    def test_the_status_box_and_the_known_reasons_are_ticked(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            text = pdf_text(admin_client.get(URL).content)
        assert "All Defects Corrected" in text and "Trees" in text
        assert "something else" not in text  # a reason outside the nine is dropped

    def test_a_property_without_a_title_block_prints_blanks(self, admin_client, storage):
        bare = {key: (None if key.startswith("tb_") else value) for key, value in HEADER.items()}
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(header=bare)):
            response = admin_client.get(URL)
        assert response.status_code == 200
        assert "HWS2014Q" not in pdf_text(response.content)

    def test_the_idr_id_picks_the_report(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()) as run:
            assert admin_client.get(URL, params={"idr_id": str(IDR_ID)}).status_code == 200
        find = next(call for call in run.call_args_list if "SELECT i.idr_id" in call.args[0])
        assert find.args[1] == (IDR_ID, IDR_ID, PROPERTY_ID)
        assert "i.status = 'approved' AND i.deleted_at IS NULL" in find.args[0]

    def test_by_default_it_is_the_most_recently_approved_idr(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()) as run:
            admin_client.get(URL)
        find = next(call for call in run.call_args_list if "SELECT i.idr_id" in call.args[0])
        assert find.args[1] == (None, None, PROPERTY_ID)
        assert "ORDER BY i.re_signed_at DESC" in find.args[0]

    def test_no_approved_idr_covering_its_flags_is_404(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(found=())):
            response = admin_client.get(URL)
        assert response.status_code == 404
        assert response.json() == {"detail": "No approved IDR covers this property's flags"}

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]), patch(REPORT) as run:
            assert admin_client.get(URL).status_code == 404
        run.assert_not_called()

    def test_flags_on_two_pages_are_422_for_multi_sheet(self, admin_client, storage):
        flags = [FLAGS[0], {**FLAGS[1], "pir_page_id": UUID("50000000-0000-4000-8000-000000000002")}]
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(flags=flags)):
            response = admin_client.get(URL)
        assert response.status_code == 422
        assert response.json()["detail"] == ("This property's sketch is too large to render on a single sheet. "
                                             "Multi-sheet support ships in R1h.1.")

    def test_a_sketch_too_large_for_one_sheet_is_422(self, admin_client, storage):
        flags = [FLAGS[0], flag_row(2, "4in", "25", "25", x=400)]
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(flags=flags)):
            response = admin_client.get(URL)
        assert response.status_code == 422 and "R1h.1" in response.json()["detail"]

    @pytest.mark.parametrize("bearing, expected", [("N", 0), ("NE", 45)])
    def test_the_north_arrow_is_not_turned_by_the_pages_rotation(self, admin_client, storage, bearing, expected):
        rotated = [{**flag, "rotation_deg": 90} for flag in FLAGS]
        with patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(REPORT, side_effect=report_query(header={**HEADER, "north_bearing": bearing}, flags=rotated)), \
                patch("api.services.construction_report.draw_report", wraps=construction_report.draw_report) as draw:
            assert admin_client.get(URL).status_code == 200
        assert draw.call_args.args[5] == expected

    def test_the_calculations_panel_lists_each_piece_and_agrees_with_box_2(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            text = pdf_text(admin_client.get(URL).content)
        assert "Calculations" in text and 'Total 4" Sidewalk Sq.Ft Non-Assessable' in text
        # Flag 1 is the non-assessable 4" one (A1, whole); flag 2 the assessable 4"
        assert "1 5.00 x 5.00 90.25" in text and "A1 5.00 x 5.00 90.25" in text
        # Box 2's 4" figures as the subtraction: 250.94 - 137.75 = 113.19 (an adjustment covers the override)
        assert "250.94 - 137.75 113.19" in text and "Adjustment to box 2 +47.50" in text
        assert "171.81 - 77.67 94.14" in text

    def test_the_perimeter_prints_the_property_line_the_tax_map_and_the_concrete_curb(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            text = pdf_text(admin_client.get(URL).content)
        # CC from the page's concrete curb segment: 800 px at 24 px/ft
        assert text.count("PL") >= 2 and "TAX MAP 40.00'" in text and "CC= 33.33'" in text
        assert "139 AVENUE" in text and "138 AVENUE" in text and "227 STREET" in text

    def test_without_the_widths_only_the_property_line_prints(self, admin_client, storage):
        header = {**HEADER, "tb_tax_map_width_ft": None, "tb_cc_curb_to_curb_ft": None}
        no_curb = {"features": [], "segments": []}
        with patch(PROPS, return_value=[PROPERTY_ROW]),                 patch(REPORT, side_effect=report_query(header=header, drawings=no_curb)):
            text = pdf_text(admin_client.get(URL).content)
        assert "PL" in text and "TAX MAP" not in text and "CC=" not in text

    def test_each_material_is_filled_its_own_way(self, admin_client, storage):
        fills = {"4in": MagicMock(), "7in": MagicMock(), "non_assessable": MagicMock()}
        with patch.dict(construction_report._FILLS, fills), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(REPORT, side_effect=report_query()):
            assert admin_client.get(URL).status_code == 200
        # One non-assessable 4", one assessable 4", three 7"
        assert (fills["non_assessable"].call_count, fills["4in"].call_count, fills["7in"].call_count) == (1, 1, 3)

    def test_large_pieces_are_labelled_by_material_and_reason(self, admin_client, storage):
        big = [{**flag_row(1, "4in", "500", "500"), "dimensions": {"L": 25, "W": 20}},
               {**flag_row(2, "7in", "500", "500", x=30), "dimensions": {"L": 25, "W": 20},
                "areas": [{"id": "a0000000-0000-4000-8000-000000000001", "display_order": 1, "reason": "grass",
                           "shape": "rectangle", "dimensions": {"L": 10, "W": 8}, "sketch_x": 200 + 30 * 24,
                           "sketch_y": 200.0, "rotation": 0, "computed_quantity": 80}]}]
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(flags=big)):
            text = pdf_text(admin_client.get(URL).content)
        assert '4" Conc.' in text and "Grass" in text and "A1" in text

    def test_too_many_pieces_for_the_calculations_panel_is_422(self, admin_client, storage):
        flags = [{**flag_row(1, "4in", "25", "25", x=(n % 8) * 0.2), "id": UUID(int=n + 1)} for n in range(45)]
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(flags=flags)):
            response = admin_client.get(URL)
        assert response.status_code == 422 and "calculations panel" in response.json()["detail"]

    def test_abutting_flags_are_one_slab_dimensioned_once(self, admin_client, storage):
        # Two 5 x 5 4" flags side by side (at 24 px/ft, 120 px apart): one 10.00' by 5.00' slab, labelled once
        side_by_side = [flag_row(1, "4in", "25", "25"), flag_row(2, "4in", "25", "25", x=5)]
        real, found = construction_report.slab_groups, []

        def recorded(items):
            found.extend(real(items))
            return found

        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(flags=side_by_side)), \
                patch("api.services.construction_report.slab_groups", side_effect=recorded):
            text = pdf_text(admin_client.get(URL).content)
        assert [len(group) for group in found] == [2]
        assert "10.00'" in text and "5.00'" in text
        assert text.count('4" Conc.') == 1

    def test_a_driveway_area_is_striped_and_labelled_DW(self, admin_client, storage):
        big = [{**flag_row(1, "4in", "500", "420"), "dimensions": {"L": 25, "W": 20},
                "areas": [{"id": "a0000000-0000-4000-8000-000000000001", "display_order": 1, "reason": "driveway",
                           "shape": "rectangle", "dimensions": {"L": 10, "W": 8}, "sketch_x": 200.0,
                           "sketch_y": 200.0, "rotation": 0, "computed_quantity": 80}]}]
        fills = {"4in": MagicMock(), "7in": MagicMock(), "non_assessable": MagicMock(), "driveway": MagicMock()}
        with patch.dict(construction_report._FILLS, fills), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(REPORT, side_effect=report_query(flags=big)):
            text = pdf_text(admin_client.get(URL).content)
        assert fills["driveway"].call_count == 1 and fills["non_assessable"].call_count == 0
        assert "10.00' DW" in text

    def test_cc_is_every_curb_segments_length_over_the_title_blocks_figure(self, admin_client, storage):
        # Two concrete curb segments of 10 and 15.5 ft at 24 px/ft and a 5 ft depressed one between them, which CC
        # counts too (DOT totals every kind of curb); the title block's 34.25 is only a fallback
        drawings = {"features": [], "segments": [
            {"segment_type": "curb_concrete", "points": [{"x": 100, "y": 330}, {"x": 100 + 10 * 24, "y": 330}]},
            {"segment_type": "curb_concrete_depressed", "points": [{"x": 340, "y": 330}, {"x": 460, "y": 330}]},
            {"segment_type": "curb_concrete", "points": [{"x": 460, "y": 330}, {"x": 460 + 15.5 * 24, "y": 330}]}]}
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(drawings=drawings)):
            text = pdf_text(admin_client.get(URL).content)
        assert "CC= 30.50'" in text and "CC= 34.25'" not in text

    def test_a_granite_curb_counts_in_cc(self, admin_client, storage):
        drawings = {"features": [], "segments": [
            {"segment_type": "curb_granite", "points": [{"x": 100, "y": 330}, {"x": 340, "y": 330}]}]}
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(drawings=drawings)):
            text = pdf_text(admin_client.get(URL).content)
        assert "CC= 10.00'" in text and "CC= 34.25'" not in text

    def test_without_a_curb_segment_cc_is_the_title_blocks_figure(self, admin_client, storage):
        drawings = {"features": [], "segments": [
            {"segment_type": "other", "points": [{"x": 100, "y": 330}, {"x": 340, "y": 330}]}]}
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(drawings=drawings)):
            text = pdf_text(admin_client.get(URL).content)
        assert "CC= 34.25'" in text

    def test_a_depressed_curb_is_ticked_and_measured(self, admin_client, storage):
        # 17.16 ft of depressed curb at 24 px/ft
        drawings = {"features": [], "segments": [
            {"segment_type": "curb_concrete_depressed", "points": [{"x": 100, "y": 330}, {"x": 100 + 17.16 * 24, "y": 330}]}]}
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(drawings=drawings)):
            text = pdf_text(admin_client.get(URL).content)
        assert "17.16'" in text

    def test_the_4_and_7_inch_tables_print_side_by_side(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            content = admin_client.get(URL).content
        page = pdfium.PdfDocument(content)[0]
        text = page.get_textpage()
        four = text.search('Total 4" Sidewalk Sq.Ft').get_next()
        seven = text.search('Total 7" Sidewalk Sq.Ft').get_next()
        assert four and seven
        # The two titles sit on one line, the 7" to the right
        (four_left, four_bottom, _, _), (seven_left, seven_bottom, _, _) = (text.get_charbox(four[0]),
                                                                            text.get_charbox(seven[0]))
        assert abs(four_bottom - seven_bottom) < 1 and seven_left > four_left + 100

    def test_the_signature_is_fitted_inside_its_cell(self, admin_client, storage):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query()):
            content = admin_client.get(URL).content
        page = pdfium.PdfDocument(content)[0]
        image = next(obj for obj in page.get_objects() if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE)
        left, bottom, right, top = image.get_pos()
        # The cell: the strip's right 210 points, above its line; the 120 x 40 image keeps its proportions
        assert left >= 546 + 5 and right <= 756 - 5 and bottom >= 14 + 10 and top <= 14 + 48
        assert (right - left) / (top - bottom) == pytest.approx(3, abs=0.05)

    def test_a_page_without_a_scale_is_422(self, admin_client, storage):
        flags = [{**flag, "scale_px_per_ft": None} for flag in FLAGS]
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=report_query(flags=flags)):
            response = admin_client.get(URL)
        assert response.status_code == 422 and response.json()["detail"] == "Set the PIR page's scale first"

    def test_a_failed_read_is_500(self, admin_client, storage):
        def failing(sql, params=None):
            return None
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(REPORT, side_effect=failing):
            assert admin_client.get(URL).status_code == 500

    def test_a_user_on_the_project_reads_it(self, storage):
        with signed_in(INSPECTOR_ROW) as client, patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PROJECTS, return_value=[{"?column?": 1}]), patch(REPORT, side_effect=report_query()):
            assert client.get(URL).status_code == 200

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as client, patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PROJECTS, return_value=[]), patch(REPORT) as run:
            response = client.get(URL)
        assert response.status_code == 403
        run.assert_not_called()

    def test_a_demo_user_reads_their_own_projects(self, demo_client, storage):
        demo_property = {**PROPERTY_ROW, "project_id": "DEMO01"}
        with patch(PROPS, return_value=[demo_property]), patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(REPORT, side_effect=report_query()):
            assert demo_client.get(URL).status_code == 200

    def test_a_demo_user_gets_404_for_another_projects(self, demo_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PROJECTS, return_value=[]), patch(REPORT) as run:
            assert demo_client.get(URL).status_code == 404
        run.assert_not_called()


# ---------------------------------------------------------------------------
# GET /v1/idrs/{idr_id}/construction-reports
# ---------------------------------------------------------------------------

TWO_PROPERTIES = [
    {"id": PROPERTY_ID, "kind": "sidewalk", "address": "138-22 227 STREET", "intersection_street_1": None,
     "intersection_street_2": None, "quadrant": None, "facing_street": None},
    {"id": OTHER_PROPERTY_ID, "kind": "sidewalk", "address": "350 Main St, Brooklyn", "intersection_street_1": None,
     "intersection_street_2": None, "quadrant": None, "facing_street": None},
]


class TestIdrReports:
    def test_a_zip_holds_one_pdf_per_property_the_idr_claimed(self, admin_client, storage):
        with patch(IDRS, return_value=[IDR_ROW]), patch(REPORT, side_effect=report_query(properties=TWO_PROPERTIES)):
            response = admin_client.get(ZIP_URL)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["content-disposition"] == 'attachment; filename="IDR_001_construction_reports.zip"'
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert len(names) == 2 and all(archive.read(name).startswith(b"%PDF") for name in names)
        # both reports are of this IDR
        assert names[0] == "138-22_227_STREET_construction_report.pdf"

    def test_two_properties_with_the_same_name_get_different_files(self, admin_client, storage):
        twins = [TWO_PROPERTIES[0], {**TWO_PROPERTIES[0], "id": OTHER_PROPERTY_ID}]
        with patch(IDRS, return_value=[IDR_ROW]), patch(REPORT, side_effect=report_query(properties=twins)):
            response = admin_client.get(ZIP_URL)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist() == ["138-22_227_STREET_construction_report.pdf",
                                          "138-22_227_STREET_construction_report_2.pdf"]

    def test_an_idr_that_isnt_approved_is_404(self, admin_client):
        with patch(IDRS, return_value=[{**IDR_ROW, "status": "stage2_review"}]), patch(REPORT) as run:
            response = admin_client.get(ZIP_URL)
        assert response.status_code == 404 and response.json() == {"detail": "IDR is not approved"}
        run.assert_not_called()

    def test_an_idr_that_claimed_no_flags_is_404(self, admin_client):
        with patch(IDRS, return_value=[IDR_ROW]), patch(REPORT, side_effect=report_query(properties=[])):
            response = admin_client.get(ZIP_URL)
        assert response.status_code == 404 and response.json() == {"detail": "This IDR claimed no flags"}

    def test_an_unknown_idr_is_404(self, admin_client):
        with patch(IDRS, return_value=[]):
            assert admin_client.get(ZIP_URL).status_code == 404

    def test_a_property_too_large_for_one_sheet_is_422_naming_it(self, admin_client, storage):
        flags = [FLAGS[0], flag_row(2, "4in", "25", "25", x=400)]
        with patch(IDRS, return_value=[IDR_ROW]), \
                patch(REPORT, side_effect=report_query(flags=flags, properties=TWO_PROPERTIES[:1])):
            response = admin_client.get(ZIP_URL)
        assert response.status_code == 422 and response.json()["detail"].startswith("138-22 227 STREET: ")

    def test_a_user_with_a_role_on_the_project_reads_it(self, storage):
        with signed_in(INSPECTOR_ROW) as client, patch(IDRS, return_value=[IDR_ROW]), \
                patch(PROJECTS, return_value=[{"role": "inspector"}]), \
                patch(REPORT, side_effect=report_query(properties=TWO_PROPERTIES[:1])):
            assert client.get(ZIP_URL).status_code == 200

    def test_a_user_without_a_role_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as client, patch(IDRS, return_value=[IDR_ROW]), \
                patch(PROJECTS, return_value=[]), patch(REPORT) as run:
            assert client.get(ZIP_URL).status_code == 403
        run.assert_not_called()

    def test_a_demo_user_gets_404_for_an_idr_they_didnt_report(self, demo_client):
        with patch(IDRS, return_value=[IDR_ROW]), patch(REPORT) as run:
            assert demo_client.get(ZIP_URL).status_code == 404
        run.assert_not_called()
        assert IDR_ROW["reporter_uuid"] != DEMO_USER_ROW["uuid"]
