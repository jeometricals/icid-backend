from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/idrs/{idr_id}/reports/{report_id}/sites (Slice U2): the sites a report imports
# Mock rows are dicts, as run_query returns them: the IDR's, its reports' and the sites'.
# ---------------------------------------------------------------------------

IDRS = "api.queries.idrs.run_query"
REPORTS = "api.queries.idr_reports.run_query"
SITES = "api.queries.idr_report_sites.run_query"
PROJECTS = "api.queries.projects.run_query"

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
REPORT_ID = UUID("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
OTHER_REPORT_ID = UUID("2a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
PROPERTY_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
SITE_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
ADMIN_ID = ADMIN_USER_ROW["uuid"]
REPORTER = {**ADMIN_USER_ROW, "uuid": UUID("c0000000-0000-4000-8000-000000000003"), "role": None}
URL = f"/v1/idrs/{IDR_ID}/reports/{REPORT_ID}/sites"
DRAFT_IDR = {
    "idr_id": IDR_ID, "project_id": "HWS0023", "reporter_uuid": REPORTER["uuid"], "report_date": date(2026, 10, 21),
    "work_start_time": None, "work_end_time": None, "inspector_start_time": None, "inspector_end_time": None,
    "temp_low": None, "temp_high": None, "weather_am": None, "weather_pm": None, "total_pages": None,
    "has_dismissed_auto_general": False, "status": "draft", "submitted_at": None, "created_at": NOW,
    "updated_at": NOW, "inspector_signature_path": None, "inspector_signed_at": None, "idr_number": None,
    "stage1_reviewer_uuid": None, "stage1_accepted_at": None, "stage1_reviewed_at": None, "re_reviewer_uuid": None,
    "stage2_accepted_at": None, "re_signature_path": None, "re_signed_at": None, "return_reason": None,
    "returned_from": None, "deleted_at": None, "deleted_by": None, "task_order_id": None,
}


def report(report_type: str = "SWCB", report_id: UUID = REPORT_ID, pay_items: list | None = None, **extra) -> dict:
    """
    Make a report row as the report queries return it.
    Takes its type, id, pay items and any other column.
    Returns the report dict.
    """
    return {"report_id": report_id, "idr_id": IDR_ID, "report_type": report_type, "is_addendum": False,
            "parent_report_id": None, "page_number": 2, "report_data": {"payItems": pay_items or []},
            "is_auto_generated": False, "created_at": NOW, "updated_at": NOW, **extra}


SIDEWALK_SITE = {"id": SITE_ID, "report_id": REPORT_ID, "idr_id": IDR_ID, "property_id": PROPERTY_ID,
                 "work_type": "concrete", "work_type_other": None, "added_at": NOW, "added_by": ADMIN_ID,
                 "kind": "sidewalk", "boro": "Brooklyn", "address": "138-22 227 Street", "block": "123", "lot": "45",
                 "intersection_street_1": None, "intersection_street_2": None, "quadrant": None, "facing_street": None,
                 "ramp_type": None, "community_board": "BK07", "property_deleted_at": None}
RAMP_SITE = {**SIDEWALK_SITE, "kind": "ped_ramp", "address": None, "block": None, "lot": None,
             "intersection_street_1": "38 St", "intersection_street_2": "31 Ave", "quadrant": "SW",
             "facing_street": "street_2", "ramp_type": "PERPENDICULAR"}
BODY = {"property_id": str(PROPERTY_ID), "work_type": "concrete"}


class TestList:
    def test_a_report_lists_its_sites_labelled(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE, RAMP_SITE]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        labels = [site["display_address"] for site in response.json()["data"]]
        assert labels == ["138-22 227 Street", "38 St & 31 Ave — SW Quadrant (Facing 31 Ave)"]
        sql, params = run.call_args.args
        assert "FROM icid.idr_report_sites rs" in sql and "ORDER BY rs.added_at" in sql and params == (REPORT_ID,)

    def test_a_report_of_another_idr_is_404(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[]), patch(SITES) as run:
            assert admin_client.get(URL).json() == {"detail": "Report not found in this IDR"}
        run.assert_not_called()


class TestImport:
    def test_its_inspector_imports_a_site_into_a_draft(self):
        with signed_in(REPORTER) as inspector, patch(IDRS, return_value=[DRAFT_IDR]), \
             patch(REPORTS, side_effect=[[report()], [report()]]), \
             patch(SITES, side_effect=[[], [], [SIDEWALK_SITE]]) as run:
            response = inspector.post(URL, json={**BODY, "work_type_other": "ignored"})
        assert response.status_code == 201 and response.json()["data"]["display_address"] == "138-22 227 Street"
        sql, params = run.call_args.args
        assert "INSERT INTO icid.idr_report_sites" in sql and "JOIN icid.properties p ON p.project_id = i.project_id" in sql
        assert "ON CONFLICT (idr_id, property_id) DO NOTHING" in sql
        # the other-text is dropped for a work type that isn't 'other'
        assert params == (REPORT_ID, "concrete", None, REPORTER["uuid"], IDR_ID, PROPERTY_ID)

    def test_a_site_already_in_the_idr_is_409_naming_where(self, admin_client):
        where = {"id": SITE_ID, "report_id": OTHER_REPORT_ID, "report_type": "GEN", "page_number": 1,
                 "is_auto_generated": False}
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[where]):
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": "Already in General (page 1) on this IDR",
                                   "existing_report_id": str(OTHER_REPORT_ID)}

    def test_a_hand_entered_pay_item_the_site_fills_is_409(self, admin_client):
        hand = report("AC", OTHER_REPORT_ID, [{"itemNo": " 4.13 AAS ", "budgetCode": "HW-200(S)", "payQuantity": "40"}],
                      page_number=3)
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, side_effect=[[report()], [report(), hand]]), \
             patch(SITES, side_effect=[[], [{"item_no": "4.13 AAS", "budget_code": "HW-200(S)"}]]) as run:
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 409
        assert response.json()["detail"].startswith("Pay item 4.13 AAS (HW-200(S)) is entered by hand on Asphaltic Concrete (page 3)")
        assert all("INSERT" not in call.args[0] for call in run.call_args_list)
        assert "f.claimed_by_idr_id IS NULL" in run.call_args.args[0] and "COALESCE(s.reported_length_ft, s.length_ft) IS NOT NULL" in run.call_args.args[0]

    def test_an_auto_general_holding_the_item_is_no_clash(self, admin_client):
        auto = report("GEN", OTHER_REPORT_ID, [{"itemNo": "4.13 AAS", "budgetCode": "HW-200(S)"}], is_auto_generated=True)
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, side_effect=[[report()], [report(), auto]]), \
             patch(SITES, side_effect=[[], [{"item_no": "4.13 AAS", "budget_code": "HW-200(S)"}], [SIDEWALK_SITE]]):
            assert admin_client.post(URL, json=BODY).status_code == 201

    def test_a_site_off_the_project_is_400(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, side_effect=[[report()], [report()]]), \
             patch(SITES, side_effect=[[], [], [], []]):
            response = admin_client.post(URL, json=BODY)
        assert (response.status_code, response.json()) == (400, {"detail": "That site isn't a live site of this IDR's project"})

    def test_a_dsp_takes_no_sites_and_other_work_needs_its_text(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report("DSP")]), patch(SITES) as run:
            assert admin_client.post(URL, json=BODY).status_code == 400
            assert admin_client.post(URL, json={**BODY, "work_type": "other", "work_type_other": "  "}).status_code == 422
            assert admin_client.post(URL, json={**BODY, "work_type": "paving"}).status_code == 422
        run.assert_not_called()

    def test_an_approved_idr_or_someone_elses_draft_is_refused(self, admin_client):
        with patch(IDRS, return_value=[{**DRAFT_IDR, "status": "approved"}]), patch(SITES) as run:
            assert admin_client.post(URL, json=BODY).status_code == 409
        other = {**REPORTER, "uuid": UUID("c0000000-0000-4000-8000-000000000009")}
        with signed_in(other) as stranger, patch(IDRS, return_value=[DRAFT_IDR]), patch(SITES):
            assert stranger.post(URL, json=BODY).status_code == 403
        run.assert_not_called()

    def test_a_demo_user_cant(self, demo_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(SITES) as run:
            assert demo_client.post(URL, json=BODY).status_code in (403, 404)
        run.assert_not_called()


class TestChangeAndRemove:
    def test_the_work_changes(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[{**SIDEWALK_SITE, "work_type": "other", "work_type_other": "Tree pit"}]) as run:
            response = admin_client.patch(f"{URL}/{SITE_ID}", json={"work_type": "other", "work_type_other": " Tree pit "})
        assert response.status_code == 200 and response.json()["data"]["work_type_other"] == "Tree pit"
        sql, params = run.call_args.args
        assert params == ("other", "Tree pit", REPORT_ID, SITE_ID, "other", "Tree pit", ADMIN_ID)
        one_line = " ".join(sql.split())
        # who and when only when the work changes
        assert "(s.work_type, s.work_type_other) IS DISTINCT FROM (%s::text, %s::text) AS is_changed" in one_line
        assert "updated_at = CASE WHEN c.is_changed THEN now() ELSE s.updated_at END" in one_line
        assert "updated_by = CASE WHEN c.is_changed THEN %s::uuid ELSE s.updated_by END" in one_line
        assert "LEFT JOIN icid.users uu ON uu.uuid = rs.updated_by" in one_line

    def test_a_site_is_removed_and_an_unknown_one_is_404(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[{"id": SITE_ID}]) as run:
            assert admin_client.delete(f"{URL}/{SITE_ID}").status_code == 204
        assert run.call_args.args == ("DELETE FROM icid.idr_report_sites WHERE report_id = %s AND id = %s RETURNING id;",
                                      (REPORT_ID, SITE_ID))
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), patch(SITES, return_value=[]):
            assert admin_client.delete(f"{URL}/{SITE_ID}").status_code == 404
            assert admin_client.patch(f"{URL}/{SITE_ID}", json={"work_type": "concrete"}).status_code == 404
