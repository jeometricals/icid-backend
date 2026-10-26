from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.pir_annotations import ANNOTATION_TYPES
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET and PUT /v1/pir-pages/{pir_page_id}/annotations: a page's title block
# Mock rows are dicts, as run_query returns them: a page as api.queries.pir_pages reads it, and a row of
# icid.pir_annotations.
# ---------------------------------------------------------------------------

ANNOTATIONS = "api.queries.pir_annotations.run_query"
PAGES = "api.queries.pir_pages.run_query"
PROJECTS = "api.queries.projects.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
URL = f"/v1/pir-pages/{PAGE_ID}/annotations"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "page_number": 1, "image_path": f"pirs/{PIR_ID}/u1/page-1.png", "image_mime": "image/png",
            "width_px": 1275, "height_px": 1650, "scale_px_per_ft": Decimal("15.0000"), "rotation_deg": 0,
            "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}
EMPTY_FIELDS = {field: None for field in ANNOTATION_TYPES} | {"violation_not_removed_due_to": []}
ANNOTATION_ROW = {"pir_page_id": PAGE_ID, **EMPTY_FIELDS, "contract_no": "SE-384", "boro": "Queens",
                  "tax_map_width_ft": Decimal("25.50"), "violation_not_removed_due_to": ["tree"],
                  "created_at": CREATED, "updated_at": CREATED}

ON_PROJECT = [{"?column?": 1}]


def written(run) -> dict:
    """
    Read the fields the upsert was given.
    Takes the run_query mock.
    Returns them keyed by field, with the page's id under pir_page_id.
    """
    params = run.call_args.args[1]
    values = dict(zip(ANNOTATION_TYPES, params))
    values["pir_page_id"] = params[-1]
    return values


class TestGetAnnotations:
    def test_a_page_without_a_title_block_gives_none(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        assert response.json() == {"status": "success", "message": "No annotations for this page", "data": None}
        assert run.call_args.args[1] == (PAGE_ID,)
        assert "FROM icid.pir_annotations WHERE pir_page_id = %s" in run.call_args.args[0]

    def test_a_pages_title_block_is_read(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]):
            response = admin_client.get(URL)
        assert response.status_code == 200 and response.json()["message"] == "Annotations retrieved"
        data = response.json()["data"]
        assert data["contract_no"] == "SE-384" and data["boro"] == "Queens" and data["tax_map_width_ft"] == 25.5
        assert data["violation_not_removed_due_to"] == ["tree"] and data["pir_page_id"] == str(PAGE_ID)

    def test_a_member_reads_it(self):
        with signed_in(MEMBER_ROW) as client, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]):
            assert client.get(URL).status_code == 200

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as client, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), \
                patch(ANNOTATIONS) as run:
            response = client.get(URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_an_unknown_page_is_404(self, admin_client):
        with patch(PAGES, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}


class TestPutAnnotations:
    def test_text_is_trimmed_and_blank_is_none(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]) as run:
            response = admin_client.put(URL, json={
                "contract_no": "  SE-384 ", "lot": "   ", "boro": "Queens", "tax_map_width_ft": 25.5,
                "violation_not_removed_due_to": [" tree ", "  ", "scaffold"]})
        assert response.status_code == 200 and response.json()["message"] == "Annotations saved"
        values = written(run)
        assert values["contract_no"] == "SE-384" and values["lot"] is None and values["boro"] == "Queens"
        assert values["tax_map_width_ft"] == Decimal("25.5")
        assert values["violation_not_removed_due_to"] == ["tree", "scaffold"]
        assert values["pir_page_id"] == PAGE_ID

    def test_a_field_left_out_keeps_its_value(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]) as run:
            assert admin_client.put(URL, json={"boro": "Bronx"}).status_code == 200
        sql = " ".join(run.call_args.args[0].split())
        assert "DO UPDATE SET boro = EXCLUDED.boro, updated_at = now()" in sql
        assert "contract_no = EXCLUDED" not in sql and "date_constructed = EXCLUDED" not in sql
        values = written(run)
        assert len(run.call_args.args[1]) == len(ANNOTATION_TYPES) + 3
        # a page without a title block gets one, the fields not sent empty
        assert values["boro"] == "Bronx" and values["violation_not_removed_due_to"] == []
        assert all(values[field] is None for field in ANNOTATION_TYPES
                   if field not in ("boro", "violation_not_removed_due_to"))

    def test_a_null_clears_a_field(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]) as run:
            assert admin_client.put(URL, json={"contract_no": None, "violation_not_removed_due_to": None,
                                               "date_excavated": None}).status_code == 200
        sql = " ".join(run.call_args.args[0].split())
        assert ("DO UPDATE SET contract_no = EXCLUDED.contract_no, violation_not_removed_due_to = "
                "EXCLUDED.violation_not_removed_due_to, date_excavated = EXCLUDED.date_excavated, updated_at = now()") in sql
        values = written(run)
        assert values["contract_no"] is None and values["violation_not_removed_due_to"] == []
        assert values["date_excavated"] is None

    def test_the_three_dates_are_written(self, admin_client):
        saved = {**ANNOTATION_ROW, "date_notice_issued": date(2026, 9, 1), "date_excavated": date(2026, 10, 1),
                 "date_constructed": date(2026, 10, 3)}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[saved]) as run:
            response = admin_client.put(URL, json={"date_notice_issued": "2026-09-01", "date_excavated": "2026-10-01",
                                                   "date_constructed": "2026-10-03"})
        assert response.status_code == 200
        assert response.json()["data"]["date_constructed"] == "2026-10-03"
        values = written(run)
        assert values["date_notice_issued"] == date(2026, 9, 1) and values["date_constructed"] == date(2026, 10, 3)
        assert "%s::date" in run.call_args.args[0]

    def test_a_malformed_date_is_422(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS) as run:
            assert admin_client.put(URL, json={"date_excavated": "10/01/2026"}).status_code == 422
        run.assert_not_called()

    def test_the_upsert_matches_only_a_live_page(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]) as run:
            admin_client.put(URL, json={})
        sql = " ".join(run.call_args.args[0].split())
        assert "INSERT INTO icid.pir_annotations (pir_page_id, contract_no," in sql
        assert "FROM icid.pir_pages page WHERE page.pir_page_id = %s AND page.deleted_at IS NULL" in sql
        assert "ON CONFLICT (pir_page_id) DO UPDATE SET updated_at = now()" in sql
        assert "%s::text[]" in sql and "%s::numeric" in sql

    @pytest.mark.parametrize("body", [
        {"boro": "Mars"},
        {"tax_map_width_ft": -1},
        {"cc_curb_to_curb_ft": -0.5},
        {"property_status": "done"},
        {"corner_property_status": "ramp"},
        {"violation_not_removed_due_to": ["x" * 201]},
        {"contract_no": "x" * 201},
    ], ids=["boro", "tax-map-width", "curb-to-curb", "property-status", "corner-status", "long-reason", "long-text"])
    def test_a_value_that_doesnt_fit_is_422(self, admin_client, body):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS) as run:
            response = admin_client.put(URL, json=body)
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_replaced_page_is_400_even_for_an_admin(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": DELETED}]), patch(ANNOTATIONS, return_value=[]):
            response = admin_client.put(URL, json={"boro": "Bronx"})
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}

    @pytest.mark.parametrize("parent,detail", [
        ("deleted_at", "Page is deleted"), ("pir_deleted_at", "PIR is deleted"),
        ("property_deleted_at", "Property is deleted"),
    ])
    def test_a_member_cant_change_a_page_under_something_deleted(self, parent, detail):
        with signed_in(MEMBER_ROW) as client, patch(PAGES, return_value=[{**PAGE_ROW, parent: DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(ANNOTATIONS) as run:
            response = client.put(URL, json={"boro": "Bronx"})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_an_admin_writes_under_a_deleted_pir(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "pir_deleted_at": DELETED}]), \
                patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]):
            assert admin_client.put(URL, json={"boro": "Bronx"}).status_code == 200

    def test_a_member_writes_it(self):
        with signed_in(MEMBER_ROW) as client, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]):
            assert client.put(URL, json={"boro": "Bronx"}).status_code == 200

    def test_a_demo_user_only_reads(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=ON_PROJECT), patch(ANNOTATIONS) as run:
            response = demo_client.put(URL, json={"boro": "Bronx"})
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        run.assert_not_called()

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as client, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), \
                patch(ANNOTATIONS) as run:
            assert client.put(URL, json={"boro": "Bronx"}).status_code == 403
        run.assert_not_called()


class TestTaxMapSource:
    """Slice Y1: a width written here was typed by hand, so it is stamped manual when it changes."""

    def test_a_width_sent_is_stamped_manual_when_it_changes(self, admin_client):
        stamped = {**ANNOTATION_ROW, "tax_map_source": "manual", "tax_map_fetched_at": CREATED}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[stamped]) as run:
            response = admin_client.put(URL, json={"tax_map_width_ft": 25.5})
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["tax_map_source"], data["tax_map_width_ft"]) == ("manual", 25.5)
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        assert ("CASE WHEN %s::numeric IS NOT NULL THEN 'manual' END, "
                "CASE WHEN %s::numeric IS NOT NULL THEN now() END") in one_line
        assert ("tax_map_source = CASE WHEN pir_annotations.tax_map_width_ft IS DISTINCT FROM "
                "EXCLUDED.tax_map_width_ft THEN 'manual' ELSE pir_annotations.tax_map_source END") in one_line
        assert params[len(ANNOTATION_TYPES):] == (Decimal("25.5"), Decimal("25.5"), PAGE_ID)

    def test_a_save_without_the_width_leaves_its_source(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(ANNOTATIONS, return_value=[ANNOTATION_ROW]) as run:
            assert admin_client.put(URL, json={"contract_no": "SE-385"}).status_code == 200
        sql = " ".join(run.call_args.args[0].split())
        assert "tax_map_source = CASE" not in sql
        assert run.call_args.args[1][len(ANNOTATION_TYPES):] == (None, None, PAGE_ID)
