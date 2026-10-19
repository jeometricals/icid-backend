from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/pir-pages/{pir_page_id}/match-lines, /v1/pirs/{pir_id}/match-lines and /v1/match-lines/{match_line_id} (R1h.1a)
# Mock rows are dicts, as run_query returns them: a line's own columns, its page's number, PIR, property and project,
# and its page's, PIR's and property's deleted states.
# ---------------------------------------------------------------------------

LINES = "api.queries.match_lines.run_query"
PAGES = "api.queries.pir_pages.run_query"
PIRS = "api.queries.pirs.run_query"
PROJECTS = "api.queries.projects.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PAGE_2 = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d2e")
PAGE_3 = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d3e")
LINE_ID = UUID("2c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f")
PAGE_URL = f"/v1/pir-pages/{PAGE_ID}/match-lines"
PIR_URL = f"/v1/pirs/{PIR_ID}/match-lines"
URL = f"/v1/match-lines/{LINE_ID}"
CREATED = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)

PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "page_number": 1, "image_path": f"pirs/{PIR_ID}/u1/page-1.png", "image_mime": "image/png",
            "width_px": 1275, "height_px": 1650, "scale_px_per_ft": Decimal("15.0000"), "rotation_deg": 0,
            "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": CREATED.date(),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}


def line_row(label: str = "A", page_id: UUID = PAGE_ID, page_number: int = 1, line_id: UUID = LINE_ID, **overrides):
    """
    Build a row as the match-line statements return one: a vertical line on a live page.
    Takes its label, its page and that page's number, its id and any column overrides.
    Returns the row.
    """
    return {"match_line_id": line_id, "pir_page_id": page_id, "page_number": page_number, "label": label,
            "x1": 1562.0, "y1": 640.0, "x2": 1562.0, "y2": 1010.0, "created_at": CREATED, "updated_at": CREATED,
            "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None, "pir_id": PIR_ID,
            "property_id": PROPERTY_ID, "project_id": "HWS0023", "page_deleted_at": None, "pir_deleted_at": None,
            "property_deleted_at": None, **overrides}


BODY = {"label": " a ", "points": [{"x": 1562, "y": 640}, {"x": 1562, "y": 1010}]}


def lines_answer(existing: list, written: list):
    """
    Answer the match-line statements: the PIR's lines for the label checks, and the write's result.
    Takes the PIR's live lines and what the write returns.
    Returns the side effect.
    """
    def answer(sql, params=None):
        if "INSERT INTO" in sql or "UPDATE icid.pir_match_lines" in sql:
            return written
        return existing
    return answer


class TestReading:
    def test_a_pirs_lines_are_listed_by_page_and_label(self, admin_client):
        rows = [line_row(), line_row("A", PAGE_2, 2, UUID(int=2))]
        with patch(PIRS, return_value=[PIR_ROW]), patch(LINES, return_value=rows) as run:
            response = admin_client.get(PIR_URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert [(line["label"], line["page_number"]) for line in data] == [("A", 1), ("A", 2)]
        assert data[0]["points"] == [{"x": 1562.0, "y": 640.0}, {"x": 1562.0, "y": 1010.0}]
        assert "ORDER BY pg.page_number, ml.label" in " ".join(run.call_args.args[0].split())

    def test_a_pages_lines(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(LINES, return_value=[line_row()]) as run:
            assert admin_client.get(PAGE_URL).status_code == 200
        assert run.call_args.args[1] == (PAGE_ID,)

    def test_one_line(self, admin_client):
        with patch(LINES, return_value=[line_row()]):
            assert admin_client.get(URL).json()["data"]["label"] == "A"

    def test_an_unknown_line_is_404(self, admin_client):
        with patch(LINES, return_value=[]):
            assert admin_client.get(URL).status_code == 404

    def test_a_user_off_the_project_is_403(self):
        with signed_in(MEMBER_ROW) as stranger, patch(LINES, return_value=[line_row()]), \
                patch(PROJECTS, return_value=[]):
            assert stranger.get(URL).status_code == 403


class TestCreate:
    def test_a_member_draws_a_line_its_label_in_capitals(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(LINES, side_effect=lines_answer([], [line_row()])) as run:
            response = member.post(PAGE_URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["label"] == "A"
        sql, params = run.call_args.args
        assert "INSERT INTO icid.pir_match_lines" in sql and "count(DISTINCT other.pir_page_id)" in sql
        assert params[:7] == ("A", 1562.0, 640.0, 1562.0, 1010.0, MEMBER_ROW["uuid"], PAGE_ID)

    def test_a_label_the_page_has_is_409(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(LINES, side_effect=lines_answer([line_row()], [])):
            response = admin_client.post(PAGE_URL, json=BODY)
        assert response.status_code == 409 and response.json() == {"detail": 'Match line "A" is already on this page'}

    def test_a_label_on_two_other_pages_is_409(self, admin_client):
        taken = [line_row("A", PAGE_2, 2, UUID(int=2)), line_row("A", PAGE_3, 3, UUID(int=3))]
        with patch(PAGES, return_value=[PAGE_ROW]), patch(LINES, side_effect=lines_answer(taken, [])):
            response = admin_client.post(PAGE_URL, json=BODY)
        assert response.status_code == 409 and response.json() == {"detail": 'Match line "A" is already on pages 2 and 3'}

    def test_a_label_on_one_other_page_pairs_with_it(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), \
                patch(LINES, side_effect=lines_answer([line_row("A", PAGE_2, 2, UUID(int=2))], [line_row()])):
            assert admin_client.post(PAGE_URL, json=BODY).status_code == 201

    @pytest.mark.parametrize("body", [
        {"label": "A1", "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
        {"label": "ABC", "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
        {"label": "A", "points": [{"x": 0, "y": 0}]},
        {"label": "A", "points": [{"x": 5, "y": 5}, {"x": 5, "y": 5}]},
    ])
    def test_a_line_that_breaks_a_rule_is_422(self, admin_client, body):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(LINES, return_value=[]) as run:
            assert admin_client.post(PAGE_URL, json=body).status_code == 422
        run.assert_not_called()

    def test_a_replaced_page_takes_nothing(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": CREATED}]), patch(LINES, return_value=[]):
            assert admin_client.post(PAGE_URL, json=BODY).status_code == 400

    def test_a_demo_user_only_reads(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(LINES, return_value=[]):
            assert demo_client.post(PAGE_URL, json=BODY).status_code == 403


class TestChangeAndDelete:
    def test_the_label_is_changed_and_checked_without_the_line_itself(self, admin_client):
        with patch(LINES, side_effect=[[line_row()], [line_row()], [line_row("B")]]) as run:
            response = admin_client.patch(URL, json={"label": "b"})
        assert response.status_code == 200 and response.json()["data"]["label"] == "B"
        sql, params = run.call_args.args
        assert "UPDATE icid.pir_match_lines line" in sql
        assert params[:6] == ("B", 1562.0, 640.0, 1562.0, 1010.0, LINE_ID)

    def test_nothing_to_change_is_400(self, admin_client):
        with patch(LINES, return_value=[line_row()]):
            assert admin_client.patch(URL, json={}).status_code == 400

    def test_points_that_coincide_are_422(self, admin_client):
        with patch(LINES, return_value=[line_row()]):
            response = admin_client.patch(URL, json={"points": [{"x": 1, "y": 1}, {"x": 1, "y": 1}]})
        assert response.status_code == 422

    def test_delete_is_a_soft_delete(self, admin_client):
        with patch(LINES, side_effect=[[line_row()], [line_row(deleted_at=CREATED)]]) as run:
            assert admin_client.delete(URL).status_code == 204
        assert "SET deleted_at = now(), deleted_by = %s" in run.call_args.args[0]

    def test_a_member_cant_change_a_deleted_line(self):
        with signed_in(MEMBER_ROW) as member, patch(LINES, return_value=[line_row(deleted_at=CREATED)]), \
                patch(PROJECTS, return_value=[{"?column?": 1}]):
            assert member.patch(URL, json={"label": "B"}).status_code == 400
