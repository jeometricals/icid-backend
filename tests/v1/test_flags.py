import math
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.flags import _WRITABLE, get_flag_placement, list_flags, soft_delete_flag, undelete_flag
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/pir-pages/{pir_page_id}/flags, /v1/pirs/{pir_id}/flags and /v1/flags/{flag_id}
# Mock rows are dicts, as run_query returns them: a flag's own columns, its PIR's property and project, its quantity
# as the statement works it out, and its page's, PIR's and property's deleted states.
# ---------------------------------------------------------------------------

FLAGS = "api.queries.flags.run_query"
PAGES = "api.queries.pir_pages.run_query"
PIRS = "api.queries.pirs.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
CONTRACT = "api.queries.contract_items.run_query"
MEASUREMENTS = "api.queries.flag_measurements.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
MEMBER_ID = MEMBER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
FLAG_ID = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PAY_ITEM_ID = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e")
FLAGS_URL = f"/v1/pir-pages/{PAGE_ID}/flags"
PIR_FLAGS_URL = f"/v1/pirs/{PIR_ID}/flags"
URL = f"/v1/flags/{FLAG_ID}"
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": date(2026, 9, 30),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}
PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "page_number": 1, "image_path": f"pirs/{PIR_ID}/u1/page-1.png", "image_mime": "image/png",
            "width_px": 1275, "height_px": 1650, "scale_px_per_ft": Decimal("15.0000"), "rotation_deg": 0,
            "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}
PAY_ITEM_ROW = {"pay_item_uuid": PAY_ITEM_ID, "pay_item_ref": "4.01", "description": "Sidewalk", "unit": "SF",
                "quantity": Decimal("100"), "unit_price": Decimal("12"), "budget_code": "SE01", "spec_section": None}
FLAG_ROW = {"id": FLAG_ID, "pir_id": PIR_ID, "pir_page_id": PAGE_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "display_order": 1,
            "added_in_field": False, "shape": "rectangle", "dimensions": {"L": 5.0, "W": 4.0}, "sketch_x": None,
            "sketch_y": None, "rotation": 0.0, "surveyed_mark": "cross", "surveyed_is_assessable": True,
            "surveyed_non_assessable_reason": None, "surveyed_thickness": "4in", "is_constructed": False,
            "pay_item_id": None, "defect_codes": [], "computed_quantity": Decimal("20.00"), "reported_quantity": None,
            "non_assessable_areas_count": 0, "non_assessable_quantity": Decimal("0"), "subtracted_quantity": Decimal("0"),
            "net_quantity": Decimal("20.00"),
            "quantity": Decimal("20.00"), "is_overridden": False, "quantity_delta_reason": None,
            "created_at": CREATED, "updated_at": CREATED, "created_by": ADMIN_ID, "deleted_at": None,
            "deleted_by": None, "page_deleted_at": None, "pir_deleted_at": None, "property_deleted_at": None}
DELETED_ROW = {**FLAG_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID}
BODY = {"display_order": 1, "shape": "rectangle", "dimensions": {"L": 5, "W": 4}, "surveyed_mark": "cross",
        "surveyed_is_assessable": True, "surveyed_thickness": "4in"}

FORBIDDEN = (403, {"detail": "Admin access required"})
ON_PROJECT = [{"?column?": 1}]

# Where a write's parameters sit: the writable columns, then the computed area, then (on create) the creator
AREA = len(_WRITABLE)
CREATOR = AREA + 1
# Where an update's writable values start: after the remesh's intent (10 values) and its editor (Slice Y3b-1)
UPDATE_OFFSET = 11


def written(run, call: int = -1) -> dict:
    """
    Read the writable values a create or update statement was given.
    Takes the run_query mock and which of its calls (the last by default).
    Returns them keyed by column, with the computed area under computed_quantity.
    """
    sql, params = run.call_args_list[call].args
    # An update's values follow the remesh's intent and its editor (Slice Y3b-1)
    offset = UPDATE_OFFSET if "WITH intent AS" in sql else 0
    values = dict(zip(_WRITABLE, params[offset:]))
    values["computed_quantity"] = params[offset + AREA]
    return values


def error_messages(response) -> str:
    """
    Join a 422's messages, to look for one in.
    Takes the response.
    Returns the messages, one string.
    """
    return " | ".join(error["msg"] for error in response.json()["detail"])


class TestCreateFlag:
    def test_an_admin_creates_a_rectangle_with_its_area(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 201 and response.json()["message"] == "Flag created"
        data = response.json()["data"]
        assert data["computed_quantity"] == 20.0 and data["quantity"] == 20.0 and data["is_overridden"] is False
        assert data["property_id"] == str(PROPERTY_ID) and data["project_id"] == "HWS0023"
        values = written(run)
        assert values["computed_quantity"] == Decimal("20.00") and values["reported_quantity"] is None
        assert values["rotation"] == 0 and values["added_in_field"] is False
        assert run.call_args.args[1][CREATOR] == ADMIN_ID

    def test_a_member_creates_a_flag_as_its_creator(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(FLAGS, return_value=[{**FLAG_ROW, "created_by": MEMBER_ID}]) as run:
            response = member.post(FLAGS_URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["created_by"] == str(MEMBER_ID)
        assert run.call_args.args[1][CREATOR] == MEMBER_ID

    @pytest.mark.parametrize("shape,dimensions,area", [
        ("rectangle", {"L": 5, "W": 4}, "20.00"),
        ("trapezoid", {"a": 3, "b": 5, "w": 4}, "16.00"),
        ("triangle", {"a": 3, "b": 4}, "6.00"),
        ("polygon", {"vertices": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}]}, "12.00"),
        ("arc", {"r": 2, "theta": math.pi}, "6.28"),
        ("corner_quadrant", {"H": 5.66, "C": 18, "R1": 12.58, "R2": 12, "A": 4.83, "B": 3.83}, "129.87"),
    ])
    def test_each_shape_is_stored_with_its_area(self, admin_client, shape, dimensions, area):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "shape": shape, "dimensions": dimensions})
        assert response.status_code == 201
        assert written(run)["computed_quantity"] == Decimal(area)
        assert written(run)["shape"] == shape

    def test_the_statement_checks_the_page_and_the_pay_items_project_itself(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            admin_client.post(FLAGS_URL, json=BODY)
        one_line = " ".join(run.call_args.args[0].split())
        assert "INSERT INTO icid.flags" in one_line
        assert ("FROM icid.pir_pages page JOIN icid.pirs pir ON pir.id = page.pir_id JOIN icid.properties p"
                in one_line)
        assert "WHERE page.pir_page_id = %s AND page.deleted_at IS NULL" in one_line
        assert run.call_args.args[1][CREATOR + 1] == PAGE_ID
        assert "ci.contract_item_id = %s::uuid AND ci.project_id = p.project_id" in one_line
        assert "COALESCE(f.reported_quantity, GREATEST(round((fm.outer_area - ded.quantity) * f.area_share_fraction, 2) - CASE WHEN fm.surveyed_is_assessable IS NULL THEN naa.quantity + sub.quantity ELSE 0 END, 0)) AS quantity" in one_line
        assert "%s::real" in one_line and "%s::jsonb" in one_line  # a NULL parameter is typed, not text

    def test_the_flag_comes_with_its_solo_measurement_in_the_same_statement(self, admin_client):
        """Slice Y3a: the INSERT gives the flag a new measurement id, and the same statement adds that measurement of
        the flag's own shape, dimensions and area; the flag's quantity is read through it."""
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 201
        one_line = " ".join(run.call_args.args[0].split())
        assert "created_by, measurement_id) SELECT" in one_line and "%s::uuid, gen_random_uuid() FROM" in one_line
        assert ("m AS ( INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, "
                "outer_area, created_by) SELECT measurement_id, pir_id, shape, dimensions, computed_quantity, created_by "
                "FROM f RETURNING * )") in one_line
        # the new flag's quantity is read from the measurement just written (a statement can't read its own writes)
        assert "JOIN m fm ON fm.measurement_id = f.measurement_id" in one_line

    @pytest.mark.parametrize("dimensions,shape", [
        ({"L": -1, "W": 4}, "rectangle"),
        ({"L": 0, "W": 4}, "rectangle"),
        ({"L": 5}, "rectangle"),
        ({"L": 5, "W": 4, "H": 2}, "rectangle"),
        ({"a": 3, "b": 4}, "rectangle"),
        ({"a": 3, "b": -5, "w": 4}, "trapezoid"),
        ({"a": 3, "b": 0}, "triangle"),
        ({"vertices": [{"x": 0, "y": 0}, {"x": 4, "y": 0}]}, "polygon"),
        ({"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}]}, "polygon"),
        ({"r": 2, "theta": 2 * math.pi + 0.01}, "arc"),
        ({"r": 2, "theta": 0}, "arc"),
        ({"r": -2, "theta": 1}, "arc"),
    ])
    def test_dimensions_that_dont_fit_the_shape_are_422(self, admin_client, dimensions, shape):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "shape": shape, "dimensions": dimensions})
        assert response.status_code == 422
        run.assert_not_called()

    @pytest.mark.parametrize("changes,message", [
        ({"H": 10}, "H/C must come to between 0.01 and 0.50"),
        ({"H": 0.01, "C": 100}, "H/C must come to between 0.01 and 0.50"),
        ({"A": 100, "B": 100}, "Correction rectangle exceeds arc + triangle"),
        ({"R1": 12.585}, "at most two decimal places"),
        ({"A": -1}, "greater than or equal to 0"),
    ], ids=["ratio-above", "ratio-below", "correction-too-large", "three-places", "negative-a"])
    def test_a_corner_quadrant_that_breaks_a_rule_is_422_saying_why(self, admin_client, changes, message):
        dimensions = {**{"H": 5.66, "C": 18, "R1": 12.58, "R2": 12, "A": 4.83, "B": 3.83}, **changes}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "shape": "corner_quadrant", "dimensions": dimensions})
        assert response.status_code == 422 and message in error_messages(response)
        run.assert_not_called()

    def test_a_corner_quadrant_needs_all_six(self, admin_client):
        dimensions = {key: value for key, value in {"H": 5.66, "C": 18, "R1": 12.58, "R2": 12, "A": 4.83, "B": 3.83}.items() if key != "B"}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "shape": "corner_quadrant", "dimensions": dimensions})
        assert response.status_code == 422 and "Field required" in error_messages(response)
        run.assert_not_called()

    def test_a_degenerate_polygon_says_why(self, admin_client):
        collinear = {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}]}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS):
            response = admin_client.post(FLAGS_URL, json={**BODY, "shape": "polygon", "dimensions": collinear})
        assert "must enclose an area" in error_messages(response)

    @pytest.mark.parametrize("changes,message", [
        ({"surveyed_is_assessable": False}, "needs a surveyed_non_assessable_reason"),
        ({"surveyed_non_assessable_reason": "hydrant"}, "an assessable flag has no surveyed_non_assessable_reason"),
        ({"surveyed_mark": "none"}, "'none' is only for a flag added in the field"),
        ({"added_in_field": True}, "a flag added in the field has surveyed_mark 'none'"),
        ({"reported_quantity": -1}, "greater than 0"),
        ({"reported_quantity": 0}, "greater than 0"),
        ({"display_order": -1}, "greater than or equal to 0"),
        ({"surveyed_thickness": "6in"}, "Input should be"),
        ({"surveyed_non_assessable_reason": "aliens", "surveyed_is_assessable": False}, "Input should be"),
    ])
    def test_a_flag_that_breaks_a_rule_is_422(self, admin_client, changes, message):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, **changes})
        assert response.status_code == 422 and message in error_messages(response)
        run.assert_not_called()

    def test_the_rules_let_a_field_added_flag_and_a_non_assessable_one_through(self, admin_client):
        body = {**BODY, "added_in_field": True, "surveyed_mark": "none", "surveyed_is_assessable": False,
                "surveyed_non_assessable_reason": "hydrant", "reported_quantity": 18.5,
                "quantity_delta_reason": "measured on site"}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            assert admin_client.post(FLAGS_URL, json=body).status_code == 201
        values = written(run)
        assert values["reported_quantity"] == Decimal("18.5") and values["computed_quantity"] == Decimal("20.00")

    def test_a_pay_item_of_the_project_is_taken(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[PAY_ITEM_ROW]) as items, \
                patch(FLAGS, return_value=[{**FLAG_ROW, "pay_item_id": PAY_ITEM_ID}]) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 201
        assert items.call_args.args[1] == ("HWS0023", PAY_ITEM_ID)
        assert written(run)["pay_item_id"] == PAY_ITEM_ID

    def test_a_pay_item_of_another_project_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 400 and response.json() == {"detail": "Pay item belongs to a different project"}
        run.assert_not_called()

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), \
                patch(FLAGS) as run:
            response = member.post(FLAGS_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_cant_add_on_their_own_project(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS) as run:
            response = demo_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: flags are read-only"}
        run.assert_not_called()

    def test_a_demo_user_gets_404_on_another_project(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), patch(FLAGS) as run:
            response = demo_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}
        run.assert_not_called()

    @pytest.mark.parametrize("parent,detail", [("pir_deleted_at", "PIR is deleted"),
                                               ("property_deleted_at", "Property is deleted")])
    def test_a_member_cant_add_under_a_deleted_pir_or_property(self, parent, detail):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[{**PAGE_ROW, parent: DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS) as run:
            response = member.post(FLAGS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_an_admin_adds_under_a_deleted_pir(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "pir_deleted_at": DELETED}]), \
                patch(FLAGS, return_value=[FLAG_ROW]):
            assert admin_client.post(FLAGS_URL, json=BODY).status_code == 201

    def test_nobody_adds_to_a_replaced_page(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": DELETED}]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}
        run.assert_not_called()

    def test_a_page_replaced_between_the_check_and_the_statement_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[]):
            response = admin_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}

    def test_an_unknown_page_is_404(self, admin_client):
        with patch(PAGES, return_value=[]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}
        run.assert_not_called()


class TestListAndReadFlags:
    def test_a_member_lists_the_live_flags_in_display_order(self):
        with signed_in(MEMBER_ROW) as member, patch(PIRS, return_value=[PIR_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = member.get(PIR_FLAGS_URL, params={"include_deleted": "true"})
        assert response.status_code == 200 and len(response.json()["data"]) == 1
        sql, params = run.call_args.args
        assert "(%s OR (f.deleted_at IS NULL AND pg.deleted_at IS NULL))" in sql
        assert "LEFT JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id" in sql
        assert "ORDER BY f.display_order, f.created_at;" in sql
        assert params == (PIR_ID, False)  # include_deleted from a member is ignored

    def test_an_admin_can_ask_for_the_deleted_ones_too(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(FLAGS, return_value=[FLAG_ROW, DELETED_ROW]) as run:
            response = admin_client.get(PIR_FLAGS_URL, params={"include_deleted": "true"})
        assert len(response.json()["data"]) == 2 and run.call_args.args[1] == (PIR_ID, True)

    def test_a_user_who_isnt_on_the_project_cant_list(self):
        with signed_in(MEMBER_ROW) as member, patch(PIRS, return_value=[PIR_ROW]), \
                patch(PROJECTS, return_value=[]), patch(FLAGS) as run:
            assert member.get(PIR_FLAGS_URL).status_code == 403
        run.assert_not_called()

    def test_a_member_reads_one_flag(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FLAGS, return_value=[DELETED_ROW]):
            response = member.get(URL)
        assert response.status_code == 200 and response.json()["data"]["deleted_at"] is not None

    def test_a_user_who_isnt_on_the_project_cant_read_one(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), patch(FLAGS, return_value=[FLAG_ROW]):
            response = member.get(URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}

    def test_an_unknown_flag_is_404(self, admin_client):
        with patch(FLAGS, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Flag not found"}

    def test_an_override_reads_as_the_quantity(self, admin_client):
        row = {**FLAG_ROW, "reported_quantity": Decimal("18.5"), "quantity": Decimal("18.5"), "is_overridden": True}
        with patch(FLAGS, return_value=[row]):
            data = admin_client.get(URL).json()["data"]
        assert (data["computed_quantity"], data["reported_quantity"], data["quantity"], data["is_overridden"]) == \
            (20.0, 18.5, 18.5, True)


class TestUpdateFlag:
    def test_a_new_shape_with_its_dimensions_is_recomputed(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]) as run:
            response = admin_client.patch(URL, json={"shape": "triangle", "dimensions": {"a": 3, "b": 4}})
        assert response.status_code == 200 and response.json()["message"] == "Flag updated"
        values = written(run)
        assert values["shape"] == "triangle" and values["dimensions"].obj == {"a": 3.0, "b": 4.0}
        assert values["computed_quantity"] == Decimal("6.00")
        assert values["surveyed_mark"] == "cross"  # the rest of the stored flag is kept
        assert "UPDATE icid.flags flag" in run.call_args.args[0]

    def test_new_dimensions_alone_are_recomputed(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]) as run:
            admin_client.patch(URL, json={"dimensions": {"L": 10, "W": 2.5}})
        assert written(run)["computed_quantity"] == Decimal("25.00")

    def test_a_flag_becomes_a_corner_quadrant_with_the_formulas_area(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]) as run:
            response = admin_client.patch(URL, json={"shape": "corner_quadrant", "dimensions": {"H": 5.66, "C": 18, "R1": 12.58, "R2": 12, "A": 4.83, "B": 3.83}})
        assert response.status_code == 200
        assert written(run)["shape"] == "corner_quadrant" and written(run)["computed_quantity"] == Decimal("129.87")

    def test_a_bundled_flag_cant_become_a_corner_quadrant(self, admin_client):
        with patch(FLAGS, return_value=[{**FLAG_ROW, "bundled": True}]) as run:
            response = admin_client.patch(URL, json={"shape": "corner_quadrant", "dimensions": {"H": 5.66, "C": 18, "R1": 12.58, "R2": 12, "A": 4.83, "B": 3.83}})
        assert response.status_code == 422
        assert response.json()["detail"] == "corner_quadrant cannot be bundled: break up its bundle first"
        assert run.call_count == 1  # the flag read, nothing written

    def test_a_new_shape_without_dimensions_is_422(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.patch(URL, json={"shape": "arc"})
        assert response.status_code == 422 and "required when the shape changes" in error_messages(response)
        assert run.call_count == 1  # the flag read, nothing written

    def test_dimensions_that_dont_fit_the_stored_shape_are_422(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run:
            assert admin_client.patch(URL, json={"dimensions": {"r": 2, "theta": 1}}).status_code == 422
        assert run.call_count == 1

    def test_an_override_is_written(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]) as run:
            admin_client.patch(URL, json={"reported_quantity": 22.25, "quantity_delta_reason": "re-measured"})
        values = written(run)
        assert values["reported_quantity"] == Decimal("22.25") and values["quantity_delta_reason"] == "re-measured"

    def test_a_null_override_reverts_to_the_computed_quantity_and_clears_its_reason(self, admin_client):
        overridden = {**FLAG_ROW, "reported_quantity": Decimal("22.25"), "quantity_delta_reason": "re-measured"}
        with patch(FLAGS, side_effect=[[overridden], [FLAG_ROW]]) as run:
            response = admin_client.patch(URL, json={"reported_quantity": None})
        assert response.status_code == 200
        values = written(run)
        assert values["reported_quantity"] is None and values["quantity_delta_reason"] is None
        assert values["computed_quantity"] == Decimal("20.00")

    def test_a_null_clears_a_clearable_field_and_is_ignored_on_any_other(self, admin_client):
        mapped = {**FLAG_ROW, "pay_item_id": PAY_ITEM_ID, "sketch_x": 3.0}
        with patch(FLAGS, side_effect=[[mapped], [FLAG_ROW]]) as run:
            admin_client.patch(URL, json={"pay_item_id": None, "sketch_x": None, "shape": None, "rotation": None})
        values = written(run)
        assert values["pay_item_id"] is None and values["sketch_x"] is None
        assert values["shape"] == "rectangle" and values["rotation"] == 0

    def test_nothing_to_change_is_400(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.patch(URL, json={"shape": None, "is_constructed": True})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1  # is_constructed isn't the API's to write

    def test_the_merged_flag_is_checked_against_the_rules(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.patch(URL, json={"surveyed_is_assessable": False})
        assert response.status_code == 422 and "needs a surveyed_non_assessable_reason" in error_messages(response)
        assert run.call_count == 1

    def test_switching_to_assessable_takes_the_reason_away_in_one_patch(self, admin_client):
        not_assessable = {**FLAG_ROW, "surveyed_is_assessable": False, "surveyed_non_assessable_reason": "hydrant"}
        with patch(FLAGS, side_effect=[[not_assessable], [FLAG_ROW]]) as run:
            response = admin_client.patch(URL, json={"surveyed_is_assessable": True,
                                                     "surveyed_non_assessable_reason": None})
        assert response.status_code == 200 and written(run)["surveyed_non_assessable_reason"] is None

    def test_a_patch_writes_flag_edits_for_assessability_by_the_editor(self, admin_client):
        """Slice Y1.5: changes of surveyed_is_assessable or its reason are kept in flag_edits, in the same statement."""
        not_assessable = {**FLAG_ROW, "surveyed_is_assessable": False, "surveyed_non_assessable_reason": "hydrant"}
        with patch(FLAGS, side_effect=[[not_assessable], [FLAG_ROW]]) as run:
            admin_client.patch(URL, json={"surveyed_is_assessable": True, "surveyed_non_assessable_reason": None})
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        assert "JOIN icid.flags old ON true" in one_line and "old.id = flag.id" in one_line
        assert ("RETURNING flag.*, old.surveyed_is_assessable AS old_assessable, "
                "old.surveyed_non_assessable_reason AS old_reason") in one_line
        assert "INSERT INTO icid.flag_edits (flag_id, field_path, old_value, new_value, reason, editor_uuid)" in one_line
        assert "WHERE v.old_value IS DISTINCT FROM v.new_value" in one_line  # only the fields that changed
        assert params[-2:] == (None, ADMIN_USER_ROW["uuid"])  # no reason words; the editor

    def test_a_patch_moves_the_solo_measurement_with_the_flag(self, admin_client):
        """Slice Y3a: the same statement gives the flag's measurement its shape, dimensions and area as written,
        stamped by the editor only when one of them changed, and the flag's quantity is read through it."""
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]) as run:
            response = admin_client.patch(URL, json={"dimensions": {"L": 6, "W": 4}})
        assert response.status_code == 200
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        changed = ("(fm.outer_shape, fm.outer_dimensions, fm.outer_area) IS DISTINCT FROM "
                   "(s.new_shape, s.new_dims, s.new_computed)")
        # Slice Y3b-1: the remesh carries it, a solo measurement staying with its flag as in Y3a
        assert ("rm_solo AS ( UPDATE icid.flag_measurements fm SET outer_shape = CASE WHEN s.action = 'stay' "
                "THEN s.new_shape ELSE fm.outer_shape END") in one_line
        assert (f"updated_by = CASE WHEN s.action = 'join' OR {changed} THEN (SELECT uuid FROM rm_editor) "
                "ELSE fm.updated_by END") in one_line
        assert "JOIN rm_m fm ON fm.measurement_id = f.measurement_id" in one_line
        assert "measurement_id = rf.measurement_id, area_share_fraction = rf.share" in one_line
        # the intent: no bundle to join, the new area, shape and dimensions; then the editor
        assert params[0] is None and params[4] == Decimal("24.00") and params[5] == "rectangle"
        assert params[6].obj == {"L": 6.0, "W": 4.0} and params[10] == ADMIN_USER_ROW["uuid"]
        assert written(run)["computed_quantity"] == Decimal("24.00")

    def test_a_pay_item_of_another_project_is_400(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run, patch(CONTRACT, return_value=[]):
            response = admin_client.patch(URL, json={"pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 400 and response.json() == {"detail": "Pay item belongs to a different project"}
        assert run.call_count == 1

    def test_a_member_edits_a_flag(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]):
            assert member.patch(URL, json={"display_order": 3}).status_code == 200

    @pytest.mark.parametrize("flag,detail", [
        (DELETED_ROW, "Flag is deleted"),
        ({**FLAG_ROW, "page_deleted_at": DELETED}, "Page is deleted"),
        ({**FLAG_ROW, "pir_deleted_at": DELETED}, "PIR is deleted"),
        ({**FLAG_ROW, "property_deleted_at": DELETED}, "Property is deleted"),
    ])
    def test_a_member_cant_edit_under_something_deleted(self, flag, detail):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FLAGS, return_value=[flag]) as run:
            response = member.patch(URL, json={"display_order": 3})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1  # read, nothing written

    def test_an_admin_edits_a_deleted_flag(self, admin_client):
        with patch(FLAGS, side_effect=[[DELETED_ROW], [DELETED_ROW]]), patch(PIRS) as pirs:
            assert admin_client.patch(URL, json={"display_order": 3}).status_code == 200
        pirs.assert_not_called()

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), \
                patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = member.patch(URL, json={"display_order": 3})
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert run.call_count == 1

    def test_a_demo_user_cant_edit_on_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = demo_client.patch(URL, json={"display_order": 3})
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: flags are read-only"}
        assert run.call_count == 1

    def test_a_demo_user_gets_404_on_another_project(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(FLAGS, return_value=[FLAG_ROW]):
            response = demo_client.patch(URL, json={"display_order": 3})
        assert response.status_code == 404 and response.json() == {"detail": "Flag not found"}


class TestSoftDeleteAndUndeleteFlag:
    def test_an_admin_soft_deletes_and_restores_a_flag(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [DELETED_ROW]]):
            deleted = admin_client.post(f"{URL}/admin/soft-delete")
        assert deleted.status_code == 200 and deleted.json()["message"] == "Flag deleted"
        assert deleted.json()["data"]["deleted_by"] == str(ADMIN_ID)
        with patch(FLAGS, side_effect=[[DELETED_ROW], [FLAG_ROW], [FLAG_ROW]]):
            restored = admin_client.post(f"{URL}/admin/undelete")
        assert restored.status_code == 200 and restored.json()["message"] == "Flag restored"
        assert restored.json()["data"]["deleted_at"] is None

    def test_the_statements_touch_only_the_delete_columns(self):
        with patch(FLAGS, return_value=[]) as run:
            soft_delete_flag(FLAG_ID, ADMIN_ID)
            undelete_flag(FLAG_ID)
        delete_sql, undelete_sql = (call.args[0] for call in run.call_args_list)
        assert "SET deleted_at = now(), deleted_by = %s" in delete_sql and "deleted_at IS NULL" in delete_sql
        assert "SET deleted_at = NULL, deleted_by = NULL" in undelete_sql
        assert run.call_args_list[0].args[1] == (FLAG_ID, ADMIN_ID, ADMIN_ID, FLAG_ID, ADMIN_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(FLAGS, return_value=[DELETED_ROW]) as run:
            response = admin_client.post(f"{URL}/admin/soft-delete")
        assert response.status_code == 200 and response.json()["message"] == "Flag was already deleted"
        assert run.call_count == 1

    def test_undeleting_one_that_isnt_deleted_is_404(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]):
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 404 and response.json() == {"detail": "Flag is not deleted"}

    def test_an_unknown_flag_is_404(self, admin_client):
        with patch(FLAGS, return_value=[]):
            assert admin_client.post(f"{URL}/admin/soft-delete").status_code == 404
            assert admin_client.post(f"{URL}/admin/undelete").status_code == 404

    @pytest.mark.parametrize("action", ["soft-delete", "undelete"])
    def test_a_member_who_isnt_an_admin_is_forbidden(self, action):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS) as run:
            response = member.post(f"{URL}/admin/{action}")
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()


def test_every_flag_statement_is_in_the_icid_schema():
    with patch(FLAGS, return_value=[]) as run:
        list_flags(PIR_ID)
    sql = run.call_args.args[0]
    assert "FROM icid.flags f" in sql and "JOIN icid.pirs pir" in sql and "JOIN icid.properties p" in sql


class TestPageFlags:
    def test_a_member_lists_a_pages_live_flags(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = member.get(FLAGS_URL)
        assert response.status_code == 200 and response.json()["data"][0]["pir_page_id"] == str(PAGE_ID)
        sql, params = run.call_args.args
        assert "WHERE f.pir_page_id = %s AND f.deleted_at IS NULL" in sql and params == (PAGE_ID,)

    def test_a_user_who_isnt_on_the_project_cant_list_them(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=[]), patch(FLAGS) as run:
            response = member.get(FLAGS_URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_gets_404_for_another_projects_page(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), patch(FLAGS) as run:
            response = demo_client.get(FLAGS_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}
        run.assert_not_called()


class TestDeprecatedCreateOnPir:
    def test_it_puts_the_flag_on_page_1(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, return_value=[PAGE_ROW]) as pages, \
                patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(PIR_FLAGS_URL, json=BODY)
        assert response.status_code == 201
        assert pages.call_args.args[1] == (PIR_ID,) and run.call_args.args[1][CREATOR + 1] == PAGE_ID

    def test_a_pir_without_pages_is_400(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, return_value=[]), patch(FLAGS) as run:
            response = admin_client.post(PIR_FLAGS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Upload the PIR image first"}
        run.assert_not_called()

    def test_a_member_cant_add_to_a_deleted_pir(self):
        with signed_in(MEMBER_ROW) as member, patch(PIRS, return_value=[{**PIR_ROW, "deleted_at": DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PAGES) as pages, patch(FLAGS) as run:
            response = member.post(PIR_FLAGS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "PIR is deleted"}
        pages.assert_not_called()
        run.assert_not_called()


class TestMemberDelete:
    def test_a_member_soft_deletes_a_flag(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FLAGS, side_effect=[[FLAG_ROW], [{**DELETED_ROW, "deleted_by": MEMBER_ID}]]) as run:
            response = member.delete(URL)
        assert response.status_code == 204 and response.content == b""
        sql, params = run.call_args.args
        assert "SET deleted_at = now(), deleted_by = %s" in sql and "DELETE" not in sql
        assert params == (FLAG_ID, MEMBER_ID, MEMBER_ID, FLAG_ID, MEMBER_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(FLAGS, return_value=[DELETED_ROW]) as run:
            assert admin_client.delete(URL).status_code == 204
        assert run.call_count == 1

    @pytest.mark.parametrize("parent,detail", [("page_deleted_at", "Page is deleted"), ("pir_deleted_at", "PIR is deleted"),
                                               ("property_deleted_at", "Property is deleted")])
    def test_a_member_cant_delete_under_something_deleted(self, parent, detail):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FLAGS, return_value=[{**FLAG_ROW, parent: DELETED}]) as run:
            response = member.delete(URL)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1

    def test_a_demo_user_cant_delete(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = demo_client.delete(URL)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: flags are read-only"}
        assert run.call_count == 1

    def test_a_user_who_isnt_on_the_project_cant_delete(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), patch(FLAGS, return_value=[FLAG_ROW]):
            assert member.delete(URL).status_code == 403


class TestUndeleteNeedsALivePage:
    def test_a_flag_from_before_pages_cant_come_back(self, admin_client):
        with patch(FLAGS, return_value=[{**DELETED_ROW, "pir_page_id": None, "page_deleted_at": None}]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 400 and response.json() == {"detail": "This flag has no page; draw it again"}
        assert run.call_count == 1

    def test_a_flag_whose_page_was_replaced_cant_come_back(self, admin_client):
        with patch(FLAGS, return_value=[{**DELETED_ROW, "page_deleted_at": DELETED}]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 400 and response.json() == {"detail": "Its page was replaced"}
        assert run.call_count == 1

    def test_the_statement_brings_it_back_only_onto_a_live_page(self, admin_client):
        with patch(FLAGS, side_effect=[[DELETED_ROW], [FLAG_ROW], [FLAG_ROW]]) as run:
            assert admin_client.post(f"{URL}/admin/undelete").status_code == 200
        assert "page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL" in " ".join(run.call_args_list[1].args[0].split())


class TestDefectCodesAndReasons:
    def test_defect_codes_are_stored_in_order_each_once(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "defect_codes": ["6C", "1", "6C", "3"]})
        assert response.status_code == 201
        assert written(run)["defect_codes"] == ["1", "3", "6C"]
        assert "%s::text[]" in run.call_args.args[0]

    def test_a_flag_without_defect_codes_has_none(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            admin_client.post(FLAGS_URL, json=BODY)
        assert written(run)["defect_codes"] == []

    @pytest.mark.parametrize("codes", [["7"], ["6a"], ["6D"], "1"])
    def test_a_code_outside_the_dots_is_422(self, admin_client, codes):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "defect_codes": codes})
        assert response.status_code == 422
        run.assert_not_called()

    @pytest.mark.parametrize("reason", ["tree_damage", "settlement", "tree", "driveway", "existing_sidewalk",
                                        "scaffolding", "other_perm_obstruction", "other_temp_obstruction"])
    def test_the_old_and_new_non_assessable_reasons_are_taken(self, admin_client, reason):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "surveyed_is_assessable": False,
                                                          "surveyed_non_assessable_reason": reason})
        assert response.status_code == 201 and written(run)["surveyed_non_assessable_reason"] == reason

    def test_a_patch_replaces_the_codes_and_a_null_keeps_them(self, admin_client):
        stored = {**FLAG_ROW, "defect_codes": ["2"]}
        with patch(FLAGS, side_effect=[[stored], [stored]]) as run:
            assert admin_client.patch(URL, json={"defect_codes": ["5", "4"]}).status_code == 200
        assert written(run)["defect_codes"] == ["4", "5"]
        with patch(FLAGS, side_effect=[[stored], [stored]]) as run:
            assert admin_client.patch(URL, json={"defect_codes": None, "display_order": 2}).status_code == 200
        assert written(run)["defect_codes"] == ["2"]


class TestRemesh:
    """Slice Y3b-1: a PATCH that changes a flag's classification reads its page's flags for the one bundle it joins,
    and hands the pick to the statement, which checks it again."""

    PLACED = {**FLAG_ROW, "sketch_x": 400.0, "sketch_y": 300.0}
    ME = {**PLACED, "measurement_id": UUID("a1000000-0000-4000-8000-000000000001"), "scale_px_per_ft": Decimal("16"),
          "bundled": False, "frozen": False}
    BUNDLE_ID = UUID("b1000000-0000-4000-8000-000000000001")
    # 7in, 80 px (5 ft) to the right: it shares the flag's right edge
    NEIGHBOUR = {**ME, "id": UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0e"), "sketch_x": 480.0, "surveyed_thickness": "7in",
                 "measurement_id": BUNDLE_ID, "bundled": True}

    def test_a_new_classification_joins_the_one_bundle_open_to_it(self, admin_client):
        with patch(FLAGS, side_effect=[[self.PLACED], [FLAG_ROW]]) as run, \
                patch(MEASUREMENTS, return_value=[self.ME, self.NEIGHBOUR]) as read:
            response = admin_client.patch(URL, json={"surveyed_thickness": "7in"})
        assert response.status_code == 200
        assert read.call_args.args[1] == (PAGE_ID,)
        params = run.call_args.args[1]
        assert params[0] == self.BUNDLE_ID and params[1] == "7in"  # the pick, and the new classification

    def test_none_open_and_it_goes_solo(self, admin_client):
        with patch(FLAGS, side_effect=[[self.PLACED], [FLAG_ROW]]) as run, \
                patch(MEASUREMENTS, return_value=[self.ME, {**self.NEIGHBOUR, "frozen": True}]):
            admin_client.patch(URL, json={"surveyed_thickness": "7in"})
        assert run.call_args.args[1][0] is None

    def test_the_same_classification_reads_nothing(self, admin_client):
        with patch(FLAGS, side_effect=[[self.PLACED], [FLAG_ROW]]) as run, patch(MEASUREMENTS) as read:
            admin_client.patch(URL, json={"dimensions": {"L": 6, "W": 4}, "surveyed_non_assessable_reason": None})
        read.assert_not_called()
        assert run.call_args.args[1][0] is None

    def test_a_failed_read_is_500_and_writes_nothing(self, admin_client):
        with patch(FLAGS, side_effect=[[self.PLACED]]) as run, patch(MEASUREMENTS, return_value=None):
            response = admin_client.patch(URL, json={"pay_item_id": None, "surveyed_is_assessable": False,
                                                     "surveyed_non_assessable_reason": "tree"})
        assert (response.status_code, response.json()["detail"]) == (500, "Failed to read the page's flags")
        assert run.call_count == 1


class TestNetQuantity:
    def test_a_flag_carries_its_areas_and_subtracted_features(self, admin_client):
        row = {**FLAG_ROW, "non_assessable_areas_count": 2, "non_assessable_quantity": Decimal("5.00"),
               "subtracted_quantity": Decimal("3.14"), "net_quantity": Decimal("11.86"), "quantity": Decimal("11.86")}
        with patch(FLAGS, return_value=[row]):
            data = admin_client.get(URL).json()["data"]
        assert data["computed_quantity"] == 20.0 and data["net_quantity"] == 11.86 and data["quantity"] == 11.86
        assert data["non_assessable_areas_count"] == 2 and data["subtracted_quantity"] == 3.14

    def test_the_read_works_the_net_out_in_sql(self):
        with patch(FLAGS, return_value=[]) as run:
            list_flags(PIR_ID)
        one_line = " ".join(run.call_args.args[0].split())
        assert "GREATEST(round((fm.outer_area - ded.quantity) * f.area_share_fraction, 2) - CASE WHEN fm.surveyed_is_assessable IS NULL THEN naa.quantity + sub.quantity ELSE 0 END, 0) AS net_quantity" in one_line
        # Slice Y3a: through the flag's measurement, which every flag has
        assert "JOIN icid.flag_measurements fm ON fm.measurement_id = f.measurement_id" in one_line
        # an area counts while live, and once its flag is deleted, if it went with the flag
        assert "na.flag_id = f.id AND (na.deleted_at IS NULL OR na.deleted_at = f.deleted_at)" in one_line
        assert "ft.flag_id = f.id AND ft.area_impact = 'subtract' AND ft.deleted_at IS NULL" in one_line
        assert "COALESCE(ft.reported_quantity, ft.computed_quantity)" in one_line


class TestAreasGoWithTheirFlag:
    def test_deleting_a_flag_deletes_its_live_areas_in_the_same_statement(self):
        with patch(FLAGS, return_value=[]) as run:
            soft_delete_flag(FLAG_ID, ADMIN_ID)
        one_line = " ".join(run.call_args.args[0].split())
        assert ("UPDATE icid.non_assessable_areas SET deleted_at = now(), deleted_by = %s, updated_at = now() "
                "WHERE flag_id IN (SELECT id FROM f) AND deleted_at IS NULL") in one_line
        assert "CROSS JOIN (SELECT count(*) FROM areas) AS areas_retired" in one_line

    def test_undeleting_brings_back_only_the_areas_deleted_with_it(self):
        with patch(FLAGS, return_value=[]) as run:
            undelete_flag(FLAG_ID)
        one_line = " ".join(run.call_args.args[0].split())
        assert "WITH before AS ( SELECT id, deleted_at FROM icid.flags WHERE id = %s )" in one_line
        assert "na.flag_id = before.id AND na.deleted_at = before.deleted_at" in one_line
        assert "WHERE before.id IN (SELECT id FROM f)" in one_line
        assert run.call_args.args[1] == (FLAG_ID, FLAG_ID, FLAG_ID)

    def test_the_restored_flag_is_read_again_for_its_areas(self, admin_client):
        restored = {**FLAG_ROW, "non_assessable_areas_count": 1}
        with patch(FLAGS, side_effect=[[DELETED_ROW], [FLAG_ROW], [restored]]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.json()["data"]["non_assessable_areas_count"] == 1 and run.call_count == 3


# ---------------------------------------------------------------------------
# R1f: a flag's pay item must be in a unit an IDR's rollup can measure it in, and a flag shows who claimed it
# ---------------------------------------------------------------------------

LF_REFUSED = "A flag can't be measured in LF: give it a pay item in SF, SY, CY, EA"


class TestFlagPayItemUnits:
    def test_a_pay_item_in_lf_is_422_on_create_and_nothing_is_written(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[{**PAY_ITEM_ROW, "unit": "LF"}]), \
                patch(FLAGS) as run:
            response = admin_client.post(FLAGS_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 422 and response.json() == {"detail": LF_REFUSED}
        run.assert_not_called()

    @pytest.mark.parametrize("unit", ["SF", "S.F.", "SY", "CY", "C.Y.", "EA", "Each"])
    def test_a_unit_a_flag_measures_is_taken(self, admin_client, unit):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[{**PAY_ITEM_ROW, "unit": unit}]), \
                patch(FLAGS, return_value=[{**FLAG_ROW, "pay_item_id": PAY_ITEM_ID}]):
            response = admin_client.post(FLAGS_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 201

    def test_a_new_pay_item_in_lf_is_422_on_patch(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run, patch(CONTRACT, return_value=[{**PAY_ITEM_ROW, "unit": "LF"}]):
            response = admin_client.patch(URL, json={"pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 422 and response.json() == {"detail": LF_REFUSED}
        assert run.call_count == 1  # the read, nothing written

    def test_a_flag_keeping_its_pay_item_isnt_refused_for_its_unit(self, admin_client):
        mapped = {**FLAG_ROW, "pay_item_id": PAY_ITEM_ID}
        with patch(FLAGS, side_effect=[[mapped], [mapped]]), \
                patch(CONTRACT, return_value=[{**PAY_ITEM_ROW, "unit": "LF"}]):
            response = admin_client.patch(URL, json={"display_order": 2})
        assert response.status_code == 200

    def test_a_flag_shows_the_idr_that_claimed_it(self, admin_client):
        idr_id = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
        claimed = {**FLAG_ROW, "is_constructed": True, "claimed_by_idr_id": idr_id, "claimed_by_idr_number": "042",
                   "constructed_on": date(2026, 10, 1)}
        with patch(FLAGS, return_value=[claimed]):
            data = admin_client.get(URL).json()["data"]
        assert (data["is_constructed"], data["claimed_by_idr_id"], data["claimed_by_idr_number"],
                data["constructed_on"]) == (True, str(idr_id), "042", "2026-10-01")

    def test_an_unclaimed_flag_shows_none(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as run:
            data = admin_client.get(URL).json()["data"]
        assert (data["claimed_by_idr_id"], data["claimed_by_idr_number"], data["constructed_on"]) == (None, None, None)
        sql = " ".join(run.call_args.args[0].split())
        assert "f.claimed_by_idr_id, f.constructed_on" in sql and "AS claimed_by_idr_number" in sql


NOT_ASSESSABLE_BODY = {**BODY, "surveyed_is_assessable": False, "surveyed_non_assessable_reason": "tree"}
NOT_ASSESSABLE = {"surveyed_is_assessable": False, "surveyed_non_assessable_reason": "tree"}
AAS_ROW = {**PAY_ITEM_ROW, "pay_item_ref": "4.13 AAS", "budget_code": "HW-200(S)"}


def by_number_calls(contract) -> list:
    """
    Pick out the default pay item's lookups among the contract-items mock's calls.
    Takes the mock.
    Returns each lookup's parameters (project id, item number).
    """
    return [call.args[1] for call in contract.call_args_list if "ORDER BY ci.budget_code" in call.args[0]]


class TestDefaultPayItem:
    """Pilot sweep #2: a non-assessable flag without a pay item takes its thickness's, when the project has exactly one."""

    @pytest.mark.parametrize("thickness,item_no", [("4in", "4.13 AAS"), ("7in", "4.13 BAS"), ("7in_as_4in", "4.13 AAS")])
    def test_a_new_non_assessable_flag_takes_its_thicknesss_item(self, admin_client, thickness, item_no):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[AAS_ROW]) as contract, \
                patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json={**NOT_ASSESSABLE_BODY, "surveyed_thickness": thickness})
        assert response.status_code == 201
        assert by_number_calls(contract) == [("HWS0023", item_no)]
        assert written(run)["pay_item_id"] == PAY_ITEM_ID

    @pytest.mark.parametrize("rows", [
        [],  # the project has no such item
        [AAS_ROW, {**AAS_ROW, "pay_item_uuid": UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4f"), "budget_code": "SE02"}],
        [{**AAS_ROW, "unit": "LF"}],  # one a flag can't be measured in
        None,  # it can't be read
    ])
    def test_none_or_several_leave_it_without_one(self, admin_client, rows):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=rows), \
                patch(FLAGS, return_value=[FLAG_ROW]) as run:
            response = admin_client.post(FLAGS_URL, json=NOT_ASSESSABLE_BODY)
        assert response.status_code == 201 and written(run)["pay_item_id"] is None

    def test_a_null_sent_stays_null_and_an_assessable_flag_takes_none(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[AAS_ROW]) as contract, \
                patch(FLAGS, return_value=[FLAG_ROW]) as run:
            admin_client.post(FLAGS_URL, json={**NOT_ASSESSABLE_BODY, "pay_item_id": None})
            assert written(run)["pay_item_id"] is None
            admin_client.post(FLAGS_URL, json=BODY)
            assert written(run)["pay_item_id"] is None
        contract.assert_not_called()

    def test_marking_a_flag_non_assessable_gives_it_one(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW]]) as run, \
                patch(MEASUREMENTS, return_value=[]), patch(CONTRACT, return_value=[AAS_ROW]) as contract:
            response = admin_client.patch(URL, json=NOT_ASSESSABLE)
        assert response.status_code == 200
        assert by_number_calls(contract) == [("HWS0023", "4.13 AAS")]
        assert written(run)["pay_item_id"] == PAY_ITEM_ID

    def test_marking_it_non_assessable_keeps_the_one_it_has(self, admin_client):
        mapped = {**FLAG_ROW, "pay_item_id": UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4f")}
        with patch(FLAGS, side_effect=[[mapped], [mapped]]) as run, patch(MEASUREMENTS, return_value=[]), \
                patch(CONTRACT, return_value=[AAS_ROW]) as contract:
            admin_client.patch(URL, json=NOT_ASSESSABLE)
        assert by_number_calls(contract) == []
        assert written(run)["pay_item_id"] == mapped["pay_item_id"]

    def test_a_null_sent_with_it_or_a_flag_already_non_assessable_takes_none(self, admin_client):
        not_assessable = {**FLAG_ROW, **NOT_ASSESSABLE}
        with patch(FLAGS, side_effect=[[FLAG_ROW], [FLAG_ROW], [not_assessable], [not_assessable]]) as run, \
                patch(MEASUREMENTS, return_value=[]), patch(CONTRACT, return_value=[AAS_ROW]) as contract:
            admin_client.patch(URL, json={**NOT_ASSESSABLE, "pay_item_id": None})
            assert written(run)["pay_item_id"] is None
            admin_client.patch(URL, json={"display_order": 2})
            assert written(run)["pay_item_id"] is None
        assert by_number_calls(contract) == []


class TestFlagPlacement:
    def test_it_reads_the_flags_shape_and_place_and_its_pages_scale(self):
        row = {"shape": "rectangle", "dimensions": {"L": 5, "W": 4}, "sketch_x": 10.0, "sketch_y": 12.0,
               "rotation": 0.0, "scale_px_per_ft": Decimal("24")}
        with patch(FLAGS, return_value=[row]) as run:
            assert get_flag_placement(FLAG_ID) == [row]
        sql = " ".join(run.call_args.args[0].split())
        assert "pg.scale_px_per_ft FROM icid.flags f LEFT JOIN icid.pir_pages pg" in sql
        assert run.call_args.args[1] == (FLAG_ID,)


class TestMeasurementFields:
    """Slice Y4a: a flag carries its measurement and whether that is a bundle, for the workspace to group by."""

    def test_the_read_carries_them(self):
        with patch(FLAGS, return_value=[]) as run:
            list_flags(PIR_ID)
        one_line = " ".join(run.call_args.args[0].split())
        assert "f.measurement_id, fm.surveyed_is_assessable IS NOT NULL AS bundled" in one_line

    def test_the_response_carries_them(self, admin_client):
        measurement = UUID("b1000000-0000-4000-8000-000000000001")
        with patch(FLAGS, return_value=[{**FLAG_ROW, "measurement_id": measurement, "bundled": True}]):
            data = admin_client.get(URL).json()["data"]
        assert (data["measurement_id"], data["bundled"]) == (str(measurement), True)
