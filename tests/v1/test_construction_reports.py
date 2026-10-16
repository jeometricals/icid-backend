import io
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pypdfium2 as pdfium
import pytest
from PIL import Image

from api.services import signatures
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
    "lot": "49", "address": "138-22 227 STREET", "intersection_street_1": None, "intersection_street_2": None,
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
        for expected in ("CONSTRUCTION REPORT", "CITY PROPERTY", "Sheet 1 of 1", "HWS2014Q", "61188", "Queens",
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
        assert "422.75" in text and "$2,496.14" in text

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
