from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.sidewalk_strip import FLIP_REASON
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# PUT /v1/pir-pages/{pir_page_id}/sidewalk-strip (Slice Y1.5)
# Mock rows are dicts, as run_query returns them: a page as api.queries.pir_pages reads it, the page's flags as
# api.queries.flags lists them, and the strip statement's row.
# ---------------------------------------------------------------------------

PAGES = "api.queries.pir_pages.run_query"
FLAGS = "api.queries.flags.run_query"
STRIPS = "api.queries.sidewalk_strip.run_query"
PROJECTS = "api.queries.projects.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PAGE_ID = UUID("e2000000-0000-4000-8000-000000000001")
PIR_ID = UUID("e1000000-0000-4000-8000-000000000001")
IDR_ID = UUID("e4000000-0000-4000-8000-000000000001")
URL = f"/v1/pir-pages/{PAGE_ID}/sidewalk-strip"
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)

PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": UUID("e0000000-0000-4000-8000-000000000001"),
            "project_id": "HWS0023", "page_number": 1, "image_path": "pirs/x/page-1.png", "image_mime": "image/png",
            "width_px": 1650, "height_px": 1275, "scale_px_per_ft": Decimal("16"), "rotation_deg": 0,
            "created_at": NOW, "updated_at": NOW, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}
# The property line (300, 250) -> (460, 250) is 160 px = 10.00 ft at 16 px/ft
STRIP = [{"x": 300, "y": 250}, {"x": 460, "y": 250}, {"x": 460, "y": 450}, {"x": 300, "y": 450}]


def flag_row(n: int, x: float, y: float, assessable: bool = True, claimed: UUID | None = None) -> dict:
    """A 5 x 5 ft flag on the page, as the page's flag list returns it."""
    return {"id": UUID(f"e3000000-0000-4000-8000-00000000000{n}"), "display_order": n, "shape": "rectangle",
            "dimensions": {"L": 5, "W": 5}, "sketch_x": x, "sketch_y": y, "rotation": 0,
            "surveyed_is_assessable": assessable, "claimed_by_idr_id": claimed}


INSIDE, CROSSING, OUTSIDE = flag_row(1, 400, 300), flag_row(4, 450, 450), flag_row(3, 700, 300)
NON_ASSESSABLE_OUTSIDE = flag_row(2, 900, 600, assessable=False)
CLAIMED_OUTSIDE = flag_row(5, 900, 300, claimed=IDR_ID)
PAGE_FLAGS = [INSIDE, NON_ASSESSABLE_OUTSIDE, OUTSIDE, CROSSING, CLAIMED_OUTSIDE]


def strip_row(points=STRIP, flipped=None) -> dict:
    """The strip statement's row."""
    return {"pir_page_id": PAGE_ID, "sidewalk_strip_points": points, "sidewalk_strip_source": "manual" if points else None,
            "sidewalk_strip_set_at": NOW if points else None, "sidewalk_strip_set_by": ADMIN_ID if points else None,
            "flipped": [{"id": str(OUTSIDE["id"]), "display_order": 3}] if flipped is None else flipped}


def roles(*held: str):
    """Answer the project statements: on the project, holding the roles given."""
    def answer(sql, params=None):
        return [{"role": role} for role in held] if "SELECT role" in sql else [{"?column?": 1}]
    return answer


class TestDraw:
    def test_an_admin_draws_it_and_the_wholly_outside_flags_flip(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=PAGE_FLAGS), \
                patch(STRIPS, return_value=[strip_row()]) as run:
            response = admin_client.put(URL, json={"points": STRIP})
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Sidewalk strip saved; 1 flag marked non-assessable; 1 claimed flag outside it left as it was"
        data = body["data"]
        assert (data["points"], data["source"], data["frontage_ft"]) == (STRIP, "manual", 10.0)
        assert [f["display_order"] for f in data["flipped"]] == [3]
        assert [(f["display_order"], f["claimed_by_idr_id"]) for f in data["outside_claimed"]] == [(5, str(IDR_ID))]
        sql, params = run.call_args.args
        assert params[0].obj == STRIP and params[1:4] == (ADMIN_ID, PAGE_ID, [OUTSIDE["id"]])
        assert params[4:] == (FLIP_REASON, None)  # the system's edits: no editor

    def test_the_one_statement_writes_the_strip_flips_and_their_edits(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=PAGE_FLAGS), \
                patch(STRIPS, return_value=[strip_row()]) as run:
            admin_client.put(URL, json={"points": STRIP})
        one_line = " ".join(run.call_args.args[0].split())
        assert "INSERT INTO icid.pir_annotations AS a" in one_line and "'manual', now(), %s::uuid" in one_line
        assert "WHERE pg.pir_page_id = %s AND pg.deleted_at IS NULL" in one_line
        assert ("SET surveyed_is_assessable = false, surveyed_non_assessable_reason = 'outside_property_line'"
                in one_line)
        # re-checked in the statement: still live, on the page, unclaimed and assessable
        assert ("f.deleted_at IS NULL AND f.claimed_by_idr_id IS NULL AND f.surveyed_is_assessable" in one_line)
        assert "INSERT INTO icid.flag_edits (flag_id, field_path, old_value, new_value, reason, editor_uuid)" in one_line
        assert "WHERE v.old_value IS DISTINCT FROM v.new_value" in one_line

    def test_nothing_outside_flips_nothing(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[INSIDE, CROSSING]), \
                patch(STRIPS, return_value=[strip_row(flipped=[])]) as run:
            response = admin_client.put(URL, json={"points": STRIP})
        assert response.json()["message"] == "Sidewalk strip saved; 0 flags marked non-assessable"
        assert run.call_args.args[1][3] == []

    def test_an_oe_draws_it(self):
        with signed_in(MEMBER_ROW) as oe, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, side_effect=roles("oe")), \
                patch(FLAGS, return_value=[]), patch(STRIPS, return_value=[strip_row(flipped=[])]):
            assert oe.put(URL, json={"points": STRIP}).status_code == 200

    def test_an_re_draws_it(self):
        with signed_in(MEMBER_ROW) as re, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, side_effect=roles("re")), \
                patch(FLAGS, return_value=[]), patch(STRIPS, return_value=[strip_row(flipped=[])]):
            assert re.put(URL, json={"points": STRIP}).status_code == 200

    def test_an_inspector_is_refused(self):
        with signed_in(MEMBER_ROW) as inspector, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, side_effect=roles("inspector")), patch(STRIPS) as run:
            response = inspector.put(URL, json={"points": STRIP})
        assert (response.status_code, response.json()["detail"]) == (403, "Role required: oe/re")
        run.assert_not_called()

    def test_a_demo_user_is_refused(self, demo_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "project_id": "DEMO01"}]), patch(PROJECTS, side_effect=roles()), \
                patch(STRIPS) as run:
            assert demo_client.put(URL, json={"points": STRIP}).status_code == 403
        run.assert_not_called()

    def test_a_page_without_a_scale_is_refused(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "scale_px_per_ft": None}]), patch(STRIPS) as run:
            response = admin_client.put(URL, json={"points": STRIP})
        assert (response.status_code, response.json()["detail"]) == (400, "Set the scale of PIR page 1 first")
        run.assert_not_called()

    @pytest.mark.parametrize("points,detail", [
        (STRIP[:2], "A sidewalk strip has 3 to 200 points"),
        ([{"x": 0, "y": 0}, {"x": 100, "y": 100}, {"x": 100, "y": 0}, {"x": 0, "y": 50}],
         "A sidewalk strip's edges must not cross"),
    ], ids=["two-points", "crossing"])
    def test_a_strip_that_isnt_a_simple_polygon_is_400(self, admin_client, points, detail):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(STRIPS) as run:
            response = admin_client.put(URL, json={"points": points})
        assert (response.status_code, response.json()["detail"]) == (400, detail)
        run.assert_not_called()

    def test_a_replaced_page_is_refused(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": NOW}]), patch(STRIPS) as run:
            response = admin_client.put(URL, json={"points": STRIP})
        assert (response.status_code, response.json()["detail"]) == (400, "Page is deleted")
        run.assert_not_called()

    def test_a_page_replaced_meanwhile_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[]), patch(STRIPS, return_value=[]):
            assert admin_client.put(URL, json={"points": STRIP}).status_code == 400

    def test_a_member_on_a_deleted_pirs_page_is_refused(self):
        with signed_in(MEMBER_ROW) as oe, patch(PAGES, return_value=[{**PAGE_ROW, "pir_deleted_at": NOW}]), \
                patch(PROJECTS, side_effect=roles("oe")), patch(STRIPS) as run:
            assert oe.put(URL, json={"points": STRIP}).status_code == 400
        run.assert_not_called()

    def test_an_unknown_page_is_404(self, admin_client):
        with patch(PAGES, return_value=[]):
            assert admin_client.put(URL, json={"points": STRIP}).status_code == 404

    def test_an_extra_key_is_422(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]):
            assert admin_client.put(URL, json={"points": STRIP, "source": "r1k"}).status_code == 422


class TestClear:
    def test_null_clears_it_and_flips_nothing_back(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS) as flags, \
                patch(STRIPS, return_value=[strip_row(points=None, flipped=[])]) as run:
            response = admin_client.put(URL, json={"points": None})
        assert response.status_code == 200
        assert response.json()["message"] == "Sidewalk strip cleared; flags stay as they are"
        assert response.json()["data"]["points"] is None and response.json()["data"]["frontage_ft"] is None
        sql, params = run.call_args.args
        assert "SET sidewalk_strip_points = NULL, sidewalk_strip_source = NULL" in " ".join(sql.split())
        assert "icid.flags" not in sql and params == (PAGE_ID,)
        flags.assert_not_called()

    def test_clearing_a_page_without_a_title_block_is_200(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(STRIPS, return_value=[]):
            response = admin_client.put(URL, json={"points": None})
        assert response.status_code == 200 and response.json()["data"]["points"] is None
