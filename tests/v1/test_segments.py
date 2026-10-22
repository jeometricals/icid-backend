from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/pir-pages/{pir_page_id}/segments, /v1/pirs/{pir_id}/segments and /v1/segments/{segment_id}
# Mock rows are dicts, as run_query returns them: a segment's own columns, its page's PIR, property and project, and
# its page's, PIR's and property's deleted states.
# ---------------------------------------------------------------------------

SEGMENTS = "api.queries.segments.run_query"
PAGES = "api.queries.pir_pages.run_query"
PIRS = "api.queries.pirs.run_query"
PROJECTS = "api.queries.projects.run_query"
CONTRACT = "api.queries.contract_items.run_query"
SIGN = "api.services.pir_image.create_signed_url"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
MEMBER_ID = MEMBER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
SEGMENT_ID = UUID("1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f")
PAY_ITEM_ID = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e")
SEGMENTS_URL = f"/v1/pir-pages/{PAGE_ID}/segments"
PIR_SEGMENTS_URL = f"/v1/pirs/{PIR_ID}/segments"
URL = f"/v1/segments/{SEGMENT_ID}"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": CREATED.date(),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}
PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "page_number": 1, "image_path": f"pirs/{PIR_ID}/u1/page-1.png", "image_mime": "image/png",
            "width_px": 1275, "height_px": 1650, "scale_px_per_ft": Decimal("15.0000"), "rotation_deg": 0,
            "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}
POINTS = [{"x": 0.0, "y": 0.0}, {"x": 30.0, "y": 40.0}]
SEGMENT_ROW = {"segment_id": SEGMENT_ID, "pir_page_id": PAGE_ID, "segment_type": "curb_concrete", "points": POINTS,
               "length_ft": Decimal("3.33"), "reported_length_ft": None, "length_delta_reason": None,
               "effective_length_ft": Decimal("3.33"), "is_overridden": False,
               "pay_item_id": None, "note": "north side", "created_at": CREATED,
               "updated_at": CREATED, "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None,
               "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "page_deleted_at": None,
               "pir_deleted_at": None, "property_deleted_at": None}
DELETED_ROW = {**SEGMENT_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID}
PAY_ITEM_ROW = {"pay_item_uuid": PAY_ITEM_ID, "pay_item_ref": "4.01", "description": "Curb", "unit": "LF",
                "quantity": Decimal("100"), "unit_price": Decimal("12"), "budget_code": "SE01", "spec_section": None}
BODY = {"segment_type": "curb_concrete", "points": [{"x": 0, "y": 0}, {"x": 30, "y": 40}]}

ON_PROJECT = [{"?column?": 1}]


def one_line(sql: str) -> str:
    """
    Collapse a statement's whitespace, to look for a clause in it.
    Takes the SQL.
    Returns it on one line.
    """
    return " ".join(sql.split())


def member():
    """
    A client signed in as a project member who isn't an admin.
    Takes nothing.
    Returns the signed_in context manager.
    """
    return signed_in(MEMBER_ROW)


class TestListSegments:
    def test_a_pages_live_segments_are_listed_in_drawing_order(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            response = admin_client.get(SEGMENTS_URL)
        assert response.status_code == 200 and response.json()["message"] == "Segments retrieved"
        data = response.json()["data"]
        assert data[0]["segment_id"] == str(SEGMENT_ID) and data[0]["length_ft"] == 3.33
        assert data[0]["points"] == POINTS and data[0]["project_id"] == "HWS0023"
        sql = one_line(run.call_args.args[0])
        assert "WHERE s.pir_page_id = %s AND s.deleted_at IS NULL ORDER BY s.created_at" in sql
        assert run.call_args.args[1] == (PAGE_ID,)

    def test_a_pirs_live_segments_on_its_live_pages_are_listed_by_page(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            response = admin_client.get(PIR_SEGMENTS_URL)
        assert response.status_code == 200 and len(response.json()["data"]) == 1
        sql = one_line(run.call_args.args[0])
        assert "WHERE pg.pir_id = %s AND s.deleted_at IS NULL AND pg.deleted_at IS NULL" in sql
        assert "ORDER BY pg.page_number, s.created_at" in sql

    def test_a_list_that_cant_be_read_is_500(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS, return_value=None):
            assert admin_client.get(SEGMENTS_URL).status_code == 500

    def test_a_member_reads_their_projects_segments(self):
        with member() as client, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=ON_PROJECT), \
                patch(SEGMENTS, return_value=[SEGMENT_ROW]):
            assert client.get(SEGMENTS_URL).status_code == 200

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with member() as client, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), \
                patch(SEGMENTS) as run:
            response = client.get(SEGMENTS_URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_an_unknown_page_is_404(self, admin_client):
        with patch(PAGES, return_value=[]):
            response = admin_client.get(SEGMENTS_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}


class TestCreateSegment:
    def test_an_admin_creates_a_segment_its_length_from_the_pages_scale(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "note": "north side"})
        assert response.status_code == 201 and response.json()["message"] == "Segment created"
        assert response.json()["data"]["length_ft"] == 3.33
        sql = one_line(run.call_args.args[0])
        assert ("INSERT INTO icid.segments (segment_type, points, pay_item_id, note, reported_length_ft, "
                "length_delta_reason, length_ft, pir_page_id, created_by)") in sql
        assert ("FROM jsonb_array_elements(x.points) WITH ORDINALITY AS a(pt, n) "
                "JOIN jsonb_array_elements(x.points) WITH ORDINALITY AS b(pt, n) ON b.n = a.n + 1") in sql
        assert "sqrt(power((b.pt->>'x')::numeric - (a.pt->>'x')::numeric, 2)" in sql
        assert "/ page.scale_px_per_ft, 2)" in sql and "SELECT round(sum(" in sql
        assert "JOIN icid.pir_pages page ON page.pir_page_id = %s AND page.deleted_at IS NULL" in sql
        assert "ci.contract_item_id = %s::uuid AND ci.project_id = p.project_id" in sql
        params = run.call_args.args[1]
        assert params[0] == ADMIN_ID
        assert params[1] == "curb_concrete" and params[2].obj == POINTS and params[3] is None
        assert params[4] == "north side" and params[5] is None and params[6] is None
        assert params[7] == PAGE_ID and params[8:] == (None, None)

    def test_a_member_creates_a_segment_as_its_creator(self):
        with member() as client, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=ON_PROJECT), \
                patch(SEGMENTS, return_value=[{**SEGMENT_ROW, "created_by": MEMBER_ID}]) as run:
            response = client.post(SEGMENTS_URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["created_by"] == str(MEMBER_ID)
        assert run.call_args.args[1][0] == MEMBER_ID

    def test_a_pay_item_of_the_project_is_passed_twice_for_the_check(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[PAY_ITEM_ROW]), \
                patch(SEGMENTS, return_value=[{**SEGMENT_ROW, "pay_item_id": PAY_ITEM_ID}]) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 201
        assert run.call_args.args[1][3] == PAY_ITEM_ID and run.call_args.args[1][8:] == (PAY_ITEM_ID, PAY_ITEM_ID)

    def test_a_pay_item_of_another_project_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[]), patch(SEGMENTS) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 400
        assert response.json() == {"detail": "Pay item belongs to a different project"}
        run.assert_not_called()

    @pytest.mark.parametrize("points", [[], [{"x": 0, "y": 0}]], ids=["none", "one"])
    def test_fewer_than_two_points_is_422(self, admin_client, points):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "points": points})
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_point_with_another_key_is_422(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1, "z": 2}]})
        assert response.status_code == 422
        run.assert_not_called()

    def test_an_unknown_segment_type_is_422(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "segment_type": "curb_gold"})
        assert response.status_code == 422
        run.assert_not_called()

    def test_nobody_adds_to_a_replaced_page_not_even_an_admin(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": DELETED}]), patch(SEGMENTS) as run:
            response = admin_client.post(SEGMENTS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}
        run.assert_not_called()

    def test_a_page_replaced_between_the_check_and_the_statement_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS, return_value=[]):
            response = admin_client.post(SEGMENTS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}

    @pytest.mark.parametrize("parent,detail", [
        ("pir_deleted_at", "PIR is deleted"), ("property_deleted_at", "Property is deleted"),
    ])
    def test_a_member_cant_add_under_a_deleted_pir_or_property(self, parent, detail):
        with member() as client, patch(PAGES, return_value=[{**PAGE_ROW, parent: DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(SEGMENTS) as run:
            response = client.post(SEGMENTS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    @pytest.mark.parametrize("parent", ["pir_deleted_at", "property_deleted_at"])
    def test_an_admin_can_add_under_a_deleted_pir_or_property(self, admin_client, parent):
        with patch(PAGES, return_value=[{**PAGE_ROW, parent: DELETED}]), patch(SEGMENTS, return_value=[SEGMENT_ROW]):
            assert admin_client.post(SEGMENTS_URL, json=BODY).status_code == 201

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with member() as client, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), \
                patch(SEGMENTS) as run:
            assert client.post(SEGMENTS_URL, json=BODY).status_code == 403
        run.assert_not_called()

    def test_a_demo_user_only_reads(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=ON_PROJECT), patch(SEGMENTS) as run:
            response = demo_client.post(SEGMENTS_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        run.assert_not_called()


class TestGetSegment:
    def test_one_segment_is_read_deleted_or_not(self, admin_client):
        with patch(SEGMENTS, return_value=[DELETED_ROW]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200 and response.json()["message"] == "Segment retrieved"
        assert response.json()["data"]["deleted_by"] == str(ADMIN_ID)
        assert "WHERE s.segment_id = %s" in one_line(run.call_args.args[0])

    def test_an_unknown_segment_is_404(self, admin_client):
        with patch(SEGMENTS, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Segment not found"}

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with member() as client, patch(SEGMENTS, return_value=[SEGMENT_ROW]), patch(PROJECTS, return_value=[]):
            response = client.get(URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}

    def test_a_demo_user_gets_404_for_another_projects_segment(self, demo_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]), patch(PROJECTS, return_value=[]):
            response = demo_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Segment not found"}


class TestUpdateSegment:
    def test_the_body_is_merged_over_the_stored_segment_and_its_length_worked_out_again(self, admin_client):
        new_points = [{"x": 0.0, "y": 0.0}, {"x": 150.0, "y": 0.0}]
        with patch(SEGMENTS, side_effect=[[SEGMENT_ROW], [{**SEGMENT_ROW, "points": new_points}]]) as run:
            response = admin_client.patch(URL, json={"points": new_points})
        assert response.status_code == 200 and response.json()["message"] == "Segment updated"
        sql = one_line(run.call_args.args[0])
        assert "UPDATE icid.segments seg SET segment_type = %s::text, points = %s::jsonb" in sql
        assert "FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY AS a(pt, n)" in sql
        assert "/ page.scale_px_per_ft, 2)" in sql
        assert "WHERE seg.segment_id = %s AND page.pir_page_id = seg.pir_page_id" in sql
        params = run.call_args.args[1]
        assert params[0] == "curb_concrete" and params[1].obj == new_points and params[3] == "north side"
        assert params[4] is None and params[5] is None  # no reported length
        assert params[6].obj == new_points and params[7].obj == new_points  # the new points, for the length
        assert params[8] == SEGMENT_ID and params[9:] == (None, None)

    def test_nothing_sent_is_400(self, admin_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            response = admin_client.patch(URL, json={})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1

    def test_null_points_counts_as_not_sent(self, admin_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            response = admin_client.patch(URL, json={"points": None})
        assert response.status_code == 400 and run.call_count == 1

    def test_null_clears_the_note_and_the_pay_item(self, admin_client):
        stored = {**SEGMENT_ROW, "pay_item_id": PAY_ITEM_ID}
        with patch(SEGMENTS, side_effect=[[stored], [SEGMENT_ROW]]) as run:
            response = admin_client.patch(URL, json={"note": None, "pay_item_id": None})
        assert response.status_code == 200
        params = run.call_args.args[1]
        assert params[1].obj == POINTS and params[2] is None and params[3] is None

    def test_a_new_pay_item_of_another_project_is_400(self, admin_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run, patch(CONTRACT, return_value=[]):
            response = admin_client.patch(URL, json={"pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 400 and run.call_count == 1

    def test_points_that_break_a_rule_are_422_in_fastapis_shape(self, admin_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            response = admin_client.patch(URL, json={"points": [{"x": 0, "y": 0}]})
        assert response.status_code == 422 and response.json()["detail"][0]["loc"][0] == "body"
        assert run.call_count == 1

    def test_a_member_cant_change_a_deleted_segment_but_an_admin_can(self, admin_client):
        with member() as client, patch(SEGMENTS, return_value=[DELETED_ROW]), patch(PROJECTS, return_value=ON_PROJECT):
            response = client.patch(URL, json={"note": "x"})
        assert response.status_code == 400 and response.json() == {"detail": "Segment is deleted"}
        with patch(SEGMENTS, side_effect=[[DELETED_ROW], [DELETED_ROW]]):
            assert admin_client.patch(URL, json={"note": "x"}).status_code == 200

    @pytest.mark.parametrize("parent,detail", [
        ("page_deleted_at", "Page is deleted"), ("pir_deleted_at", "PIR is deleted"),
        ("property_deleted_at", "Property is deleted"),
    ])
    def test_a_member_cant_change_a_segment_under_something_deleted(self, parent, detail):
        with member() as client, patch(SEGMENTS, return_value=[{**SEGMENT_ROW, parent: DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT):
            response = client.patch(URL, json={"note": "x"})
        assert response.status_code == 400 and response.json() == {"detail": detail}

    def test_a_demo_user_is_refused(self, demo_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run, patch(PROJECTS, return_value=ON_PROJECT):
            response = demo_client.patch(URL, json={"note": "x"})
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        assert run.call_count == 1


class TestDeleteSegment:
    def test_a_member_soft_deletes_a_segment(self):
        with member() as client, patch(SEGMENTS, side_effect=[[SEGMENT_ROW], [DELETED_ROW]]) as run, \
                patch(PROJECTS, return_value=ON_PROJECT):
            response = client.delete(URL)
        assert response.status_code == 204
        sql, params = run.call_args.args
        assert "SET deleted_at = now(), deleted_by = %s" in sql and "deleted_at IS NULL" in sql
        assert params == (MEMBER_ID, SEGMENT_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(SEGMENTS, return_value=[DELETED_ROW]) as run:
            assert admin_client.delete(URL).status_code == 204
        assert run.call_count == 1

    def test_a_member_cant_delete_under_a_deleted_page(self):
        with member() as client, patch(SEGMENTS, return_value=[{**SEGMENT_ROW, "page_deleted_at": DELETED}]) as run, \
                patch(PROJECTS, return_value=ON_PROJECT):
            response = client.delete(URL)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}
        assert run.call_count == 1

    def test_a_user_who_isnt_on_the_project_cant_delete(self):
        with member() as client, patch(SEGMENTS, return_value=[SEGMENT_ROW]), patch(PROJECTS, return_value=[]):
            assert client.delete(URL).status_code == 403

    def test_a_demo_user_cant_delete(self, demo_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run, patch(PROJECTS, return_value=ON_PROJECT):
            response = demo_client.delete(URL)
        assert response.status_code == 403 and run.call_count == 1

    def test_an_unknown_segment_is_404(self, admin_client):
        with patch(SEGMENTS, return_value=[]):
            assert admin_client.delete(URL).status_code == 404


class TestPageScaleRescalesSegments:
    def test_setting_a_pages_scale_works_out_its_segments_lengths_in_the_same_statement(self, admin_client):
        with patch(PAGES, side_effect=[[PAGE_ROW], [{**PAGE_ROW, "scale_px_per_ft": Decimal("30")}]]) as run, \
                patch(SIGN, return_value="https://storage.example/signed?token=s"):
            response = admin_client.patch(f"/v1/pir-pages/{PAGE_ID}/scale", json={"scale_px_per_ft": 30})
        assert response.status_code == 200
        sql, params = run.call_args.args
        sql = one_line(sql)
        assert "UPDATE icid.pir_pages SET scale_px_per_ft = %s::numeric" in sql
        assert "UPDATE icid.segments s SET length_ft =" in sql
        assert "FROM jsonb_array_elements(s.points) WITH ORDINALITY AS a(pt, n)" in sql
        assert "/ pg.scale_px_per_ft, 2)" in sql
        assert "FROM pg WHERE s.pir_page_id = pg.pir_page_id AND s.deleted_at IS NULL" in sql
        assert "CROSS JOIN (SELECT count(*) FROM lengths) AS lengths_set" in sql
        assert params == (Decimal("30"), PAGE_ID)
