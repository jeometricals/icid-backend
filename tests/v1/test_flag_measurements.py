from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# POST /v1/pir-pages/{pir_page_id}/measurements/bundle, GET and DELETE /v1/measurements/{measurement_id} (Slice Y3b-2)
# Mock rows are dicts, as run_query returns them: a page as api.queries.pir_pages reads it, a page's flags as
# list_remesh_neighbours reads them, and a measurement as get_measurement reads it.
# ---------------------------------------------------------------------------

PAGES = "api.queries.pir_pages.run_query"
FLAGS = "api.queries.flags.run_query"
MEASUREMENTS = "api.queries.flag_measurements.run_query"
PROJECTS = "api.queries.projects.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PAGE_ID = UUID("e2000000-0000-4000-8000-000000000001")
PIR_ID = UUID("e1000000-0000-4000-8000-000000000001")
BUNDLE_ID = UUID("b1000000-0000-4000-8000-000000000001")
IDR_ID = UUID("e4000000-0000-4000-8000-000000000001")
URL = f"/v1/pir-pages/{PAGE_ID}/measurements/bundle"
MEASUREMENT_URL = f"/v1/measurements/{BUNDLE_ID}"
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
OUTLINE = {"rectangles": [{"L": 10, "W": 5}]}

PAGE_ROW = {"pir_page_id": PAGE_ID, "pir_id": PIR_ID, "property_id": UUID("e0000000-0000-4000-8000-000000000001"),
            "project_id": "HWS0023", "page_number": 1, "image_path": "pirs/x/page-1.png", "image_mime": "image/png",
            "width_px": 1650, "height_px": 1275, "scale_px_per_ft": Decimal("16"), "rotation_deg": 0,
            "created_at": NOW, "updated_at": NOW, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}


def flag_id(n: int) -> UUID:
    """The nth test flag's id."""
    return UUID(f"f3000000-0000-4000-8000-00000000000{n}")


def neighbour(n: int, x: float, **changes) -> dict:
    """A live 5 x 5 ft 4in assessable flag on the page centred at (x, 300), as list_remesh_neighbours returns it."""
    return {"id": flag_id(n), "display_order": n, "measurement_id": UUID(f"a1000000-0000-4000-8000-00000000000{n}"),
            "shape": "rectangle", "dimensions": {"L": 5, "W": 5}, "sketch_x": x, "sketch_y": 300, "rotation": 0,
            "surveyed_thickness": "4in", "surveyed_is_assessable": True, "pay_item_id": None, "claimed_by_idr_id": None,
            "claimed_by_idr_number": None, "scale_px_per_ft": Decimal("16"), "bundled": False, "frozen": False,
            **changes}


A, B, C = neighbour(1, 400), neighbour(2, 480), neighbour(3, 560)


def measurement_row(**changes) -> dict:
    """A bundle of flags 1 and 2 as get_measurement returns it."""
    return {"measurement_id": BUNDLE_ID, "pir_id": PIR_ID, "property_id": PAGE_ROW["property_id"],
            "project_id": "HWS0023", "outer_shape": "component_rectangles", "outer_dimensions": OUTLINE,
            "outer_area": Decimal("50.00"), "deductions": [], "needs_remeasure": False, "pay_item_id": None,
            "surveyed_thickness": "4in", "surveyed_is_assessable": True, "bundled": True, "frozen": False,
            "claimed_by_idr_number": None, "retired_at": None, "created_at": NOW, "created_by": ADMIN_ID,
            "updated_at": NOW, "updated_by": None,
            "members": [{"flag_id": str(flag_id(n)), "display_order": n, "pir_page_id": str(PAGE_ID),
                         "area_share_fraction": 0.5, "computed_quantity": 25.0, "quantity": 25.0,
                         "claimed_by_idr_id": None, "deleted_at": None} for n in (1, 2)],
            **changes}


def measurements(page=(A, B, C), formed=None, found=None, broken=None):
    """
    Answer the measurement statements: the page's flags, the bundle formed, the measurement read (one dict, or a list
    answered in turn), the break-up's counts.
    """
    reads = list(found) if isinstance(found, list) else None

    def answer(sql, params=None):
        if "bd_new AS" in sql:
            return [{"measurement_id": BUNDLE_ID}] if formed is None else formed
        if "ub_retired AS" in sql:
            return [{"retired": 1, "members": 2, "fresh": 2, "edits": 5}] if broken is None else broken
        if "FROM icid.flag_measurements me" in sql:
            row = reads.pop(0) if reads is not None else (measurement_row() if found is None else found)
            return [row] if row else []
        return list(page)
    return answer


def roles(*held: str):
    """Answer the project statements: on the project, holding the roles given."""
    def answer(sql, params=None):
        return [{"role": role} for role in held] if "SELECT role" in sql else [{"?column?": 1}]
    return answer


class TestBundle:
    def test_an_admin_bundles_abutting_flags_of_one_classification(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements()) as run:
            response = admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["message"] == "Bundle formed: 2 flags"
        assert body["data"]["measurement_id"] == str(BUNDLE_ID) and body["data"]["bundled"] is True
        assert [m["display_order"] for m in body["data"]["members"]] == [1, 2]
        sql, params = next(call.args for call in run.call_args_list if "bd_new AS" in call.args[0])
        ids = [A["id"], B["id"]]
        assert params[:7] == (ADMIN_ID, ids, PAGE_ID, "4in", True, None, ids)
        assert params[7].obj == {"rectangles": [{"L": 10.0, "W": 5.0}]} and params[8] == Decimal("50.00")
        assert params[9:] == (None, "4in", True)

    def test_the_outer_area_is_the_rectangles_measured(self, admin_client):
        outline = {"rectangles": [{"L": 10, "W": 5}, {"L": 2.5, "W": 3.3}, {"L": 0.333, "W": 1}]}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements()) as run:
            admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": outline})
        params = next(call.args[1] for call in run.call_args_list if "bd_new AS" in call.args[0])
        assert params[8] == Decimal("58.58")  # 50.00 + 8.25 + 0.33, each to the cent

    def test_three_in_a_chain_bundle_though_the_ends_dont_touch(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements()):
            response = admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(C["id"]), str(B["id"])],
                                                    "outer_dimensions": OUTLINE})
        assert response.status_code == 201

    @pytest.mark.parametrize("ids, page, detail", [
        ([1], (A, B), "A bundle takes at least two flags"),
        ([1, 1], (A, B), f"Flag {flag_id(1)} is listed twice"),
        ([1, 2], (A, {**B, "claimed_by_idr_id": IDR_ID, "claimed_by_idr_number": "Y15"}), "Flag 2 is claimed by IDR Y15"),
        ([1, 2], (A, {**B, "bundled": True}), "Flag 2 is already in a bundle"),
        ([1, 2], (A, {**B, "sketch_x": None}), "Flag 2 has no place on the page"),
        ([1, 2], (A, {**B, "surveyed_thickness": "7in"}),
         "The flags differ in thickness, assessment or pay item: Flag 2 differ from Flag 1"),
        ([1, 2, 3], (A, B, {**C, "surveyed_is_assessable": False}),
         "The flags differ in thickness, assessment or pay item: Flag 3 differ from Flag 1"),
        ([1, 3], (A, B, C), "Flag 3 doesn't abut the rest"),
    ], ids=["one", "twice", "claimed", "bundled", "unplaced", "thickness", "assessment", "apart"])
    def test_flags_that_cant_be_bundled_are_400(self, admin_client, ids, page, detail):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements(page)) as run:
            response = admin_client.post(URL, json={"flag_ids": [str(flag_id(n)) for n in ids],
                                                    "outer_dimensions": OUTLINE})
        assert (response.status_code, response.json()["detail"]) == (400, detail)
        assert not any("bd_new AS" in call.args[0] for call in run.call_args_list)

    def test_a_flag_not_on_the_page_is_400(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements((A,))), \
                patch(FLAGS, return_value=[]):
            response = admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE})
        assert (response.status_code, response.json()["detail"]) == (400, f"Flag {B['id']} is not on this page")

    def test_a_deleted_flag_of_the_page_is_400(self, admin_client):
        deleted = {"id": B["id"], "display_order": 2, "pir_page_id": PAGE_ID, "deleted_at": NOW}
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements((A,))), \
                patch("api.v1.flag_measurements.get_flag", return_value=deleted):
            response = admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE})
        assert (response.status_code, response.json()["detail"]) == (400, "Flag 2 is deleted")

    def test_a_page_without_a_scale_or_replaced_is_400(self, admin_client):
        body = {"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE}
        with patch(PAGES, return_value=[{**PAGE_ROW, "scale_px_per_ft": None}]), patch(MEASUREMENTS) as run:
            response = admin_client.post(URL, json=body)
        assert (response.status_code, response.json()["detail"]) == (400, "Set the scale of PIR page 1 first")
        run.assert_not_called()
        with patch(PAGES, return_value=[{**PAGE_ROW, "deleted_at": NOW}]):
            assert admin_client.post(URL, json=body).json()["detail"] == "Page is deleted"

    def test_flags_changed_meanwhile_is_409(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS, side_effect=measurements(formed=[])):
            response = admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE})
        assert (response.status_code, response.json()["detail"]) == (409, "The flags changed meanwhile; read them again")

    @pytest.mark.parametrize("outline", [
        {"rectangles": []}, {"rectangles": [{"L": 1, "W": 1}] * 4}, {"rectangles": [{"L": 0, "W": 1}]},
        {"rectangles": [{"L": 1}]}, {"rectangles": [{"L": 1, "W": 1}], "extra": 1}, {},
    ], ids=["none", "four", "zero", "no-width", "extra-key", "missing"])
    def test_an_outline_that_isnt_1_to_3_rectangles_is_422(self, admin_client, outline):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(MEASUREMENTS) as run:
            response = admin_client.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": outline})
        assert response.status_code == 422
        run.assert_not_called()

    @pytest.mark.parametrize("role", ["oe", "re"])
    def test_an_oe_or_re_bundles(self, role):
        with signed_in(MEMBER_ROW) as member, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, side_effect=roles(role)), patch(MEASUREMENTS, side_effect=measurements()):
            response = member.post(URL, json={"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE})
        assert response.status_code == 201

    def test_an_inspector_and_a_demo_user_are_refused(self, demo_client):
        body = {"flag_ids": [str(A["id"]), str(B["id"])], "outer_dimensions": OUTLINE}
        with signed_in(MEMBER_ROW) as inspector, patch(PAGES, return_value=[PAGE_ROW]), \
                patch(PROJECTS, side_effect=roles("inspector")), patch(MEASUREMENTS) as run:
            response = inspector.post(URL, json=body)
        assert (response.status_code, response.json()["detail"]) == (403, "Role required: oe/re")
        with patch(PAGES, return_value=[{**PAGE_ROW, "project_id": "DEMO01"}]), patch(PROJECTS, side_effect=roles()):
            assert demo_client.post(URL, json=body).status_code == 403
        run.assert_not_called()


class TestRead:
    def test_it_returns_the_measurement_and_its_members(self, admin_client):
        with patch(MEASUREMENTS, side_effect=measurements()) as run:
            response = admin_client.get(MEASUREMENT_URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["outer_area"], data["frozen"], len(data["members"])) == (50.0, False, 2)
        assert run.call_args.args[1] == (BUNDLE_ID,)

    def test_an_unknown_one_is_404_and_an_inspector_403(self, admin_client):
        with patch(MEASUREMENTS, side_effect=measurements(found=[None])):
            assert admin_client.get(MEASUREMENT_URL).status_code == 404
        with signed_in(MEMBER_ROW) as inspector, patch(PROJECTS, side_effect=roles("inspector")), \
                patch(MEASUREMENTS, side_effect=measurements()):
            assert inspector.get(MEASUREMENT_URL).status_code == 403


class TestUnbundle:
    def test_it_breaks_up_an_open_bundle(self, admin_client):
        with patch(MEASUREMENTS, side_effect=measurements()) as run:
            response = admin_client.delete(MEASUREMENT_URL)
        assert response.status_code == 204
        sql, params = next(call.args for call in run.call_args_list if "ub_retired AS" in call.args[0])
        assert params == (ADMIN_ID, BUNDLE_ID)

    @pytest.mark.parametrize("found, status, detail", [
        (measurement_row(bundled=False, surveyed_is_assessable=None, surveyed_thickness=None), 400, "Not a bundle"),
        (measurement_row(retired_at=NOW), 404, "Bundle is already retired"),
        (measurement_row(frozen=True, claimed_by_idr_number="Y15"), 409,
         "A member is claimed by IDR Y15: the bundle is frozen"),
    ], ids=["solo", "retired", "frozen"])
    def test_what_isnt_an_open_bundle_is_refused(self, admin_client, found, status, detail):
        with patch(MEASUREMENTS, side_effect=measurements(found=found)) as run:
            response = admin_client.delete(MEASUREMENT_URL)
        assert (response.status_code, response.json()["detail"]) == (status, detail)
        assert not any("ub_retired AS" in call.args[0] for call in run.call_args_list)

    def test_claimed_or_retired_meanwhile_is_answered_as_it_now_is(self, admin_client):
        frozen = measurement_row(frozen=True, claimed_by_idr_number="Y15")
        with patch(MEASUREMENTS, side_effect=measurements(found=[measurement_row(), frozen],
                                                          broken=[{"retired": 0, "members": 0, "fresh": 0, "edits": 0}])):
            response = admin_client.delete(MEASUREMENT_URL)
        assert (response.status_code, response.json()["detail"]) == (409, "A member is claimed by IDR Y15: the bundle is frozen")
        with patch(MEASUREMENTS, side_effect=measurements(found=[measurement_row(), measurement_row(retired_at=NOW)],
                                                          broken=[{"retired": 0, "members": 0, "fresh": 0, "edits": 0}])):
            assert admin_client.delete(MEASUREMENT_URL).status_code == 404

    def test_a_demo_user_is_refused(self, demo_client):
        with patch(MEASUREMENTS, side_effect=measurements(found=measurement_row(project_id="DEMO01"))) as run, \
                patch(PROJECTS, side_effect=roles()):
            assert demo_client.delete(MEASUREMENT_URL).status_code == 403
        assert not any("ub_retired AS" in call.args[0] for call in run.call_args_list)
