from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.features import _WRITABLE, list_features_for_pir
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/pir-pages/{pir_page_id}/features, /v1/pirs/{pir_id}/features and /v1/features/{feature_id}
# Mock rows are dicts, as run_query returns them: a feature's own columns, what counts as its quantity, and its
# page's PIR, property, project and deleted states.
# ---------------------------------------------------------------------------

FEATURES = "api.queries.features.run_query"
FLAGS = "api.queries.flags.run_query"
PAGES = "api.queries.pir_pages.run_query"
PIRS = "api.queries.pirs.run_query"
PROJECTS = "api.queries.projects.run_query"
CONTRACT = "api.queries.contract_items.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
MEMBER_ID = MEMBER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
FLAG_ID = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
OTHER_PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1f")
FEATURE_ID = UUID("1d2e3f4a-5b6c-4d7e-8f9a-0b1c2d3e4f5a")
PAY_ITEM_ID = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e")
PAGE_FEATURES_URL = f"/v1/pir-pages/{PAGE_ID}/features"
PIR_FEATURES_URL = f"/v1/pirs/{PIR_ID}/features"
URL = f"/v1/features/{FEATURE_ID}"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": date(2026, 9, 30),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}
PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "page_number": 1, "image_path": f"pirs/{PIR_ID}/u1/page-1.png", "image_mime": "image/png",
            "width_px": 1275, "height_px": 1650, "scale_px_per_ft": Decimal("15.0000"), "rotation_deg": 0,
            "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}
FLAG_ROW = {"id": FLAG_ID, "pir_id": PIR_ID, "pir_page_id": PAGE_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "display_order": 1, "added_in_field": False, "shape": "rectangle", "dimensions": {"L": 5.0, "W": 4.0},
            "sketch_x": None, "sketch_y": None, "rotation": 0.0, "surveyed_mark": "cross",
            "surveyed_is_assessable": True, "surveyed_non_assessable_reason": None, "surveyed_thickness": "4in",
            "is_constructed": False, "pay_item_id": None, "defect_codes": [], "computed_quantity": Decimal("20.00"),
            "reported_quantity": None, "non_assessable_areas_count": 0, "non_assessable_quantity": Decimal("0"),
            "subtracted_quantity": Decimal("0"), "net_quantity": Decimal("20.00"), "quantity": Decimal("20.00"),
            "is_overridden": False, "quantity_delta_reason": None, "created_at": CREATED, "updated_at": CREATED,
            "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None, "page_deleted_at": None,
            "pir_deleted_at": None, "property_deleted_at": None}
PAY_ITEM_ROW = {"pay_item_uuid": PAY_ITEM_ID, "pay_item_ref": "4.01", "description": "Sidewalk", "unit": "SF",
                "quantity": Decimal("100"), "unit_price": Decimal("12"), "budget_code": "SE01", "spec_section": None}
FEATURE_ROW = {"feature_id": FEATURE_ID, "pir_page_id": PAGE_ID, "flag_id": None, "feature_type": "hydrant",
               "area_impact": "marker_only", "sketch_x": 40.0, "sketch_y": 50.0, "rotation": 0.0, "shape": None,
               "dimensions": None, "label": None, "pay_item_id": None, "computed_quantity": None,
               "reported_quantity": None, "quantity": None, "created_at": CREATED, "updated_at": CREATED,
               "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None, "pir_id": PIR_ID,
               "property_id": PROPERTY_ID, "project_id": "HWS0023", "page_deleted_at": None, "pir_deleted_at": None,
               "property_deleted_at": None}
SIZED_ROW = {**FEATURE_ROW, "feature_type": "tree_pit", "area_impact": "subtract", "shape": "circle",
             "dimensions": {"r": 1.0}, "flag_id": FLAG_ID, "computed_quantity": Decimal("3.14"),
             "quantity": Decimal("3.14")}
DELETED_ROW = {**FEATURE_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID}
BODY = {"feature_type": "hydrant", "sketch_x": 40, "sketch_y": 50}
SUBTRACT = {"feature_type": "tree_pit", "area_impact": "subtract", "sketch_x": 40, "sketch_y": 50, "shape": "circle",
            "dimensions": {"r": 1}, "flag_id": str(FLAG_ID)}

ON_PROJECT = [{"?column?": 1}]
DEMO_READ_ONLY = {"detail": "Demo mode: PIRs are read-only"}

# Where a write's parameters sit: the writable columns, then the computed area, then (on create) the creator
AREA = len(_WRITABLE)
CREATOR = AREA + 1


def written(run, call: int = -1) -> dict:
    """
    Read the writable values a create or update statement was given.
    Takes the run_query mock and which of its calls (the last by default).
    Returns them keyed by column, with the computed area under computed_quantity.
    """
    params = run.call_args_list[call].args[1]
    values = dict(zip(_WRITABLE, params))
    values["computed_quantity"] = params[AREA]
    return values


def one_line(sql: str) -> str:
    """
    Collapse a statement's whitespace, to look for a clause in it.
    Takes the SQL.
    Returns it on one line.
    """
    return " ".join(sql.split())


class TestCreateFeature:
    def test_an_admin_adds_a_marker_with_no_area(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 201 and response.json()["message"] == "Feature created"
        data = response.json()["data"]
        assert data["computed_quantity"] is None and data["area_impact"] == "marker_only"
        values = written(run)
        assert values["computed_quantity"] is None and values["shape"] is None and values["area_impact"] == "marker_only"
        assert run.call_args.args[1][CREATOR] == ADMIN_ID and run.call_args.args[1][CREATOR + 1] == PAGE_ID

    def test_a_circle_that_subtracts_is_stored_with_pi_r_squared(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=[FLAG_ROW]), \
                patch(FEATURES, return_value=[SIZED_ROW]) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json=SUBTRACT)
        assert response.status_code == 201 and response.json()["data"]["computed_quantity"] == 3.14
        values = written(run)
        assert values["computed_quantity"] == Decimal("3.14") and values["flag_id"] == FLAG_ID

    @pytest.mark.parametrize("shape,dimensions,area", [
        ("rectangle", {"L": 2, "W": 3}, "6.00"), ("circle", {"r": 2}, "12.57"), ("triangle", {"a": 3, "b": 4}, "6.00"),
    ])
    def test_a_sized_feature_is_stored_with_its_area(self, admin_client, shape, dimensions, area):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json={**BODY, "area_impact": "sits_on_top", "shape": shape,
                                                                   "dimensions": dimensions})
        assert response.status_code == 201 and written(run)["computed_quantity"] == Decimal(area)

    def test_a_label_is_trimmed(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            admin_client.post(PAGE_FEATURES_URL, json={**BODY, "label": "  H-1  "})
        assert written(run)["label"] == "H-1"

    def test_a_member_adds_a_feature_as_its_creator(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FEATURES, return_value=[{**FEATURE_ROW, "created_by": MEMBER_ID}]) as run:
            response = member.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["created_by"] == str(MEMBER_ID)
        assert run.call_args.args[1][CREATOR] == MEMBER_ID

    def test_the_statement_checks_the_page_the_pay_item_and_the_flag_itself(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            admin_client.post(PAGE_FEATURES_URL, json=BODY)
        sql = one_line(run.call_args.args[0])
        assert "INSERT INTO icid.features" in sql
        assert "WHERE page.pir_page_id = %s AND page.deleted_at IS NULL" in sql
        assert "ci.contract_item_id = %s::uuid AND ci.project_id = p.project_id" in sql
        assert ("fl.id = %s::uuid AND fl.pir_page_id = page.pir_page_id AND fl.deleted_at IS NULL") in sql
        assert "COALESCE(ft.reported_quantity, ft.computed_quantity) AS quantity" in sql

    @pytest.mark.parametrize("changes,message", [
        ({"shape": "circle"}, "shape and dimensions come together"),
        ({"dimensions": {"r": 1}}, "shape and dimensions come together"),
        ({"area_impact": "sits_on_top"}, "needs a shape"),
        ({"area_impact": "subtract", "shape": "circle", "dimensions": {"r": 1}}, "names its flag"),
        ({"shape": "circle", "dimensions": {"L": 1}}, "Field required"),
        ({"feature_type": "bench"}, "Input should be"),
        ({"reported_quantity": 0}, "greater than 0"),
        ({"area_impact": "sits_on_top", "shape": "corner_quadrant", "dimensions": {"H": 5.66, "C": 18, "R1": 12.58, "R2": 12, "A": 4.83, "B": 3.83}}, "Input should be"),
    ], ids=["shape-alone", "dimensions-alone", "sits-without-shape", "subtract-without-flag", "bad-circle",
            "unknown-type", "zero-override", "corner-quadrant"])
    def test_a_feature_that_breaks_a_rule_is_422(self, admin_client, changes, message):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json={**BODY, **changes})
        assert response.status_code == 422
        assert message in " | ".join(error["msg"] for error in response.json()["detail"])
        run.assert_not_called()

    def test_a_pay_item_of_the_project_is_taken(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[PAY_ITEM_ROW]) as items, \
                patch(FEATURES, return_value=[{**FEATURE_ROW, "pay_item_id": PAY_ITEM_ID}]) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 201 and items.call_args.args[1] == ("HWS0023", PAY_ITEM_ID)
        assert written(run)["pay_item_id"] == PAY_ITEM_ID

    def test_a_pay_item_of_another_project_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(CONTRACT, return_value=[]), patch(FEATURES) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json={**BODY, "pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 400 and response.json() == {"detail": "Pay item belongs to a different project"}
        run.assert_not_called()

    @pytest.mark.parametrize("flag", [[], [{**FLAG_ROW, "deleted_at": DELETED}], [{**FLAG_ROW, "pir_page_id": OTHER_PAGE_ID}]],
                             ids=["unknown", "deleted", "other-page"])
    def test_a_flag_that_isnt_a_live_flag_on_the_page_is_400(self, admin_client, flag):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FLAGS, return_value=flag), patch(FEATURES) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json=SUBTRACT)
        assert response.status_code == 400 and response.json() == {"detail": "Flag is not a live flag on this page"}
        run.assert_not_called()

    def test_nobody_adds_to_a_replaced_page(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": DELETED}]), patch(FEATURES) as run:
            response = admin_client.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}
        run.assert_not_called()

    def test_a_page_replaced_between_the_check_and_the_statement_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES, return_value=[]):
            response = admin_client.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}

    @pytest.mark.parametrize("parent,detail", [("pir_deleted_at", "PIR is deleted"),
                                               ("property_deleted_at", "Property is deleted")])
    def test_a_member_cant_add_under_a_deleted_pir_or_property(self, parent, detail):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[{**PAGE_ROW, parent: DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(FEATURES) as run:
            response = member.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_an_admin_adds_under_a_deleted_pir(self, admin_client):
        with patch(PAGES, return_value=[{**PAGE_ROW, "pir_deleted_at": DELETED}]), \
                patch(FEATURES, return_value=[FEATURE_ROW]):
            assert admin_client.post(PAGE_FEATURES_URL, json=BODY).status_code == 201

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), \
                patch(FEATURES) as run:
            response = member.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_cant_add_on_their_own_project(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=ON_PROJECT), patch(FEATURES) as run:
            response = demo_client.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 403 and response.json() == DEMO_READ_ONLY
        run.assert_not_called()

    def test_a_demo_user_gets_404_on_another_project(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), patch(FEATURES) as run:
            response = demo_client.post(PAGE_FEATURES_URL, json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}
        run.assert_not_called()

    def test_an_unknown_page_is_404(self, admin_client):
        with patch(PAGES, return_value=[]), patch(FEATURES) as run:
            assert admin_client.post(PAGE_FEATURES_URL, json=BODY).status_code == 404
        run.assert_not_called()


class TestListAndReadFeatures:
    def test_a_member_lists_a_pages_live_features(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(FEATURES, return_value=[FEATURE_ROW, SIZED_ROW]) as run:
            response = member.get(PAGE_FEATURES_URL)
        assert response.status_code == 200 and len(response.json()["data"]) == 2
        sql = one_line(run.call_args.args[0])
        assert "WHERE ft.pir_page_id = %s AND ft.deleted_at IS NULL" in sql and run.call_args.args[1] == (PAGE_ID,)

    def test_a_member_lists_a_pirs_live_features_on_its_live_pages(self):
        with signed_in(MEMBER_ROW) as member, patch(PIRS, return_value=[PIR_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = member.get(PIR_FEATURES_URL)
        assert response.status_code == 200 and len(response.json()["data"]) == 1
        sql = one_line(run.call_args.args[0])
        assert "WHERE pg.pir_id = %s AND ft.deleted_at IS NULL AND pg.deleted_at IS NULL" in sql
        assert "ORDER BY pg.page_number, ft.created_at" in sql

    def test_a_user_who_isnt_on_the_project_cant_list(self):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), patch(PIRS, return_value=[PIR_ROW]), \
                patch(PROJECTS, return_value=[]), patch(FEATURES) as run:
            assert member.get(PAGE_FEATURES_URL).status_code == 403
            assert member.get(PIR_FEATURES_URL).status_code == 403
        run.assert_not_called()

    def test_a_demo_user_gets_404_for_another_projects_page(self, demo_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(PROJECTS, return_value=[]), patch(FEATURES) as run:
            response = demo_client.get(PAGE_FEATURES_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}
        run.assert_not_called()

    def test_a_list_that_cant_be_read_is_500(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(FEATURES, return_value=None):
            assert admin_client.get(PAGE_FEATURES_URL).status_code == 500

    def test_a_member_reads_one_feature_deleted_or_not(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FEATURES, return_value=[DELETED_ROW]):
            response = member.get(URL)
        assert response.status_code == 200 and response.json()["data"]["deleted_at"] is not None

    def test_a_user_who_isnt_on_the_project_cant_read_one(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), \
                patch(FEATURES, return_value=[FEATURE_ROW]):
            assert member.get(URL).status_code == 403

    def test_a_demo_user_gets_404_for_another_projects_feature(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(FEATURES, return_value=[FEATURE_ROW]):
            response = demo_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Feature not found"}

    def test_an_unknown_feature_is_404(self, admin_client):
        with patch(FEATURES, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Feature not found"}


class TestUpdateFeature:
    def test_giving_a_marker_a_shape_computes_its_area(self, admin_client):
        with patch(FEATURES, side_effect=[[FEATURE_ROW], [FEATURE_ROW]]) as run:
            response = admin_client.patch(URL, json={"area_impact": "sits_on_top", "shape": "rectangle",
                                                     "dimensions": {"L": 2, "W": 2}})
        assert response.status_code == 200 and response.json()["message"] == "Feature updated"
        values = written(run)
        assert values["computed_quantity"] == Decimal("4.00") and values["feature_type"] == "hydrant"

    def test_new_dimensions_alone_are_recomputed(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(FEATURES, side_effect=[[SIZED_ROW], [SIZED_ROW]]) as run:
            assert admin_client.patch(URL, json={"dimensions": {"r": 2}}).status_code == 200
        assert written(run)["computed_quantity"] == Decimal("12.57")

    def test_a_new_shape_without_dimensions_is_422(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(FEATURES, return_value=[SIZED_ROW]) as run:
            response = admin_client.patch(URL, json={"shape": "rectangle"})
        assert response.status_code == 422 and run.call_count == 1

    def test_nulls_clear_the_shape_and_the_area_back_to_a_marker(self, admin_client):
        sized_marker = {**SIZED_ROW, "area_impact": "sits_on_top", "flag_id": None}
        with patch(FEATURES, side_effect=[[sized_marker], [FEATURE_ROW]]) as run:
            response = admin_client.patch(URL, json={"area_impact": "marker_only", "shape": None, "dimensions": None,
                                                     "label": None})
        assert response.status_code == 200
        values = written(run)
        assert values["shape"] is None and values["dimensions"] is None and values["computed_quantity"] is None

    def test_a_null_clears_a_clearable_field_and_is_ignored_on_any_other(self, admin_client):
        stored = {**FEATURE_ROW, "label": "H1", "pay_item_id": PAY_ITEM_ID}
        with patch(CONTRACT, return_value=[PAY_ITEM_ROW]), patch(FEATURES, side_effect=[[stored], [FEATURE_ROW]]) as run:
            response = admin_client.patch(URL, json={"label": None, "pay_item_id": None, "feature_type": None,
                                                     "sketch_x": 7})
        assert response.status_code == 200
        values = written(run)
        assert values["label"] is None and values["pay_item_id"] is None
        assert values["feature_type"] == "hydrant" and values["sketch_x"] == 7

    def test_clearing_the_flag_of_a_subtracting_feature_is_422(self, admin_client):
        with patch(FEATURES, return_value=[SIZED_ROW]) as run:
            response = admin_client.patch(URL, json={"flag_id": None})
        assert response.status_code == 422 and run.call_count == 1

    @pytest.mark.parametrize("body", [{}, {"feature_type": None, "sketch_x": None}])
    def test_nothing_to_change_is_400(self, admin_client, body):
        with patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = admin_client.patch(URL, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1

    def test_a_pay_item_of_another_project_is_400(self, admin_client):
        with patch(CONTRACT, return_value=[]), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = admin_client.patch(URL, json={"pay_item_id": str(PAY_ITEM_ID)})
        assert response.status_code == 400 and run.call_count == 1

    def test_a_flag_on_another_page_is_400(self, admin_client):
        with patch(FLAGS, return_value=[{**FLAG_ROW, "pir_page_id": OTHER_PAGE_ID}]), \
                patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = admin_client.patch(URL, json={"flag_id": str(FLAG_ID)})
        assert response.status_code == 400 and response.json() == {"detail": "Flag is not a live flag on this page"}
        assert run.call_count == 1

    @pytest.mark.parametrize("feature,detail", [
        ({"deleted_at": DELETED}, "Feature is deleted"),
        ({"page_deleted_at": DELETED}, "Page is deleted"),
        ({"pir_deleted_at": DELETED}, "PIR is deleted"),
        ({"property_deleted_at": DELETED}, "Property is deleted"),
    ])
    def test_a_member_cant_edit_under_something_deleted(self, feature, detail):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FEATURES, return_value=[{**FEATURE_ROW, **feature}]) as run:
            response = member.patch(URL, json={"sketch_x": 1})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1

    def test_an_admin_edits_a_deleted_feature(self, admin_client):
        with patch(FEATURES, side_effect=[[DELETED_ROW], [DELETED_ROW]]):
            assert admin_client.patch(URL, json={"sketch_x": 1}).status_code == 200

    def test_a_member_edits_a_feature(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FEATURES, side_effect=[[FEATURE_ROW], [{**FEATURE_ROW, "sketch_x": 1.0}]]):
            response = member.patch(URL, json={"sketch_x": 1})
        assert response.status_code == 200 and response.json()["data"]["sketch_x"] == 1.0

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), \
                patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            assert member.patch(URL, json={"sketch_x": 1}).status_code == 403
        assert run.call_count == 1

    def test_a_demo_user_cant_edit_on_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = demo_client.patch(URL, json={"sketch_x": 1})
        assert response.status_code == 403 and response.json() == DEMO_READ_ONLY
        assert run.call_count == 1

    def test_an_unknown_feature_is_404(self, admin_client):
        with patch(FEATURES, return_value=[]):
            assert admin_client.patch(URL, json={"sketch_x": 1}).status_code == 404


class TestDeleteFeature:
    def test_a_member_soft_deletes_a_feature(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FEATURES, side_effect=[[FEATURE_ROW], [DELETED_ROW]]) as run:
            response = member.delete(URL)
        assert response.status_code == 204
        sql, params = run.call_args.args
        assert "SET deleted_at = now(), deleted_by = %s" in sql and "deleted_at IS NULL" in sql
        assert params == (MEMBER_ID, FEATURE_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(FEATURES, return_value=[DELETED_ROW]) as run:
            assert admin_client.delete(URL).status_code == 204
        assert run.call_count == 1

    @pytest.mark.parametrize("parent,detail", [
        ("page_deleted_at", "Page is deleted"), ("pir_deleted_at", "PIR is deleted"),
        ("property_deleted_at", "Property is deleted"),
    ])
    def test_a_member_cant_delete_under_something_deleted(self, parent, detail):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(FEATURES, return_value=[{**FEATURE_ROW, parent: DELETED}]) as run:
            response = member.delete(URL)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1

    def test_a_demo_user_cant_delete(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(FEATURES, return_value=[FEATURE_ROW]) as run:
            response = demo_client.delete(URL)
        assert response.status_code == 403 and response.json() == DEMO_READ_ONLY
        assert run.call_count == 1

    def test_a_user_who_isnt_on_the_project_cant_delete(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), \
                patch(FEATURES, return_value=[FEATURE_ROW]):
            assert member.delete(URL).status_code == 403

    def test_an_unknown_feature_is_404(self, admin_client):
        with patch(FEATURES, return_value=[]):
            assert admin_client.delete(URL).status_code == 404


def test_every_feature_statement_is_in_the_icid_schema():
    with patch(FEATURES, return_value=[]) as run:
        list_features_for_pir(PIR_ID)
    sql = run.call_args.args[0]
    assert "FROM icid.features ft" in sql and "JOIN icid.pir_pages pg" in sql and "JOIN icid.properties p" in sql
