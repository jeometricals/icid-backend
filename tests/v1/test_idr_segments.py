from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.idr_flags import released_note
from api.queries.idr_segments import (
    SEGMENT_AMOUNT,
    claim_segments_cte,
    insert_segment_quantities_cte,
    list_idr_segments,
    reclaim_segment_ids_sql,
    release_segments_cte,
)
from api.queries.quantities import list_source_quantities
from api.services.idr_segments import export_segment_lines, manual_segment_conflicts
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/idrs/{idr_id}/attributed-segments (curb billing): which curb segments an IDR covers, and the overrides that
# change them. Mock rows are dicts, as run_query returns them.
# ---------------------------------------------------------------------------

IDRS = "api.queries.idrs.run_query"
IDR_SEGMENTS = "api.queries.idr_segments.run_query"

IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
OTHER_IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5f")
TASK_ORDER_ID = UUID("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b")
SEGMENT_ID = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c20")
SEGMENT_2 = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c21")
PIR_ID = UUID("4a5b6c7d-8e9f-4a0b-8c1d-2e3f4a5b6c7d")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
CURB_ITEM = UUID("6d7e8f90-a1b2-4c3d-8e4f-5a6b7c8d9e30")
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
URL = f"/v1/idrs/{IDR_ID}/attributed-segments"

REPORTER = {**ADMIN_USER_ROW, "uuid": UUID("c0000000-0000-4000-8000-000000000003"), "email": "ian@icid.local",
            "first_name": "Ian", "role": None}
STRANGER = {**REPORTER, "uuid": UUID("c0000000-0000-4000-8000-000000000009"), "email": "sam@icid.local"}

DRAFT_IDR = {
    "idr_id": IDR_ID, "project_id": "HWS0023", "reporter_uuid": REPORTER["uuid"], "report_date": date(2026, 10, 5),
    "work_start_time": None, "work_end_time": None, "inspector_start_time": None, "inspector_end_time": None,
    "temp_low": None, "temp_high": None, "weather_am": None, "weather_pm": None, "total_pages": None,
    "has_dismissed_auto_general": False, "status": "draft", "submitted_at": None, "created_at": NOW,
    "updated_at": NOW, "inspector_signature_path": None, "inspector_signed_at": None, "idr_number": None,
    "stage1_reviewer_uuid": None, "stage1_accepted_at": None, "stage1_reviewed_at": None, "re_reviewer_uuid": None,
    "stage2_accepted_at": None, "re_signature_path": None, "re_signed_at": None, "return_reason": None,
    "returned_from": None, "deleted_at": None, "deleted_by": None, "task_order_id": TASK_ORDER_ID,
}
APPROVED_IDR = {**DRAFT_IDR, "status": "approved", "idr_number": "IDR-001"}


def segment_row(segment_id: UUID = SEGMENT_ID, **overrides: Any) -> dict[str, Any]:
    """
    Build a row as list_idr_segments returns one: a sidewalk's concrete curb, 20 ft, mapped to an LF pay item, attributed.
    Takes the segment's id and any column overrides.
    Returns the row.
    """
    return {
        "segment_id": segment_id, "pir_id": PIR_ID, "pir_page_id": PAGE_ID, "page_number": 1,
        "segment_type": "curb_concrete", "length_ft": Decimal("20.00"), "note": None, "property_id": PROPERTY_ID,
        "property_kind": "sidewalk", "boro": "Brooklyn", "block": "645", "lot": "26", "address": "156 22nd ST",
        "intersection_street_1": None, "intersection_street_2": None, "quadrant": None, "facing_street": None,
        "pay_item_id": CURB_ITEM, "pay_item_ref": "4.08 AA", "budget_code": "HW-200(S)",
        "pay_item_description": "Concrete Curb (18\" Deep)", "unit": "LF", "amount": Decimal("20.00"),
        "state": "attributed", "override": None, "in_task_order": True, "claimed_by_idr_id": None,
        "claimed_by_idr_number": None, "constructed_on": None, "deleted_at": None, **overrides,
    }


def project_segment(**overrides: Any) -> dict[str, Any]:
    """
    Build a row as get_project_segment returns one: a live HWS0023 segment nobody claimed.
    Takes any column overrides.
    Returns the row.
    """
    return {"segment_id": SEGMENT_ID, "project_id": "HWS0023", "claimed_by_idr_id": None,
            "claimed_by_idr_number": None, "deleted": False, **overrides}


def segment_statements(listed: list[dict[str, Any]], found: dict[str, Any] = None, in_scope: bool = True):
    """
    Answer the IDR-segment statements: the list, the segment an override names, its default scope, and the writes.
    Takes the rows the list returns, the segment get_project_segment finds (None: none) and whether it is in the IDR's default scope.
    Returns the side effect.
    """
    def answer(sql, params=None):
        if "AS in_scope" in sql:
            return [{"in_scope": in_scope}]
        if "AS deleted" in sql:
            return [found] if found else []
        if "INSERT INTO icid.idr_segment_attributions" in sql:
            return [{"idr_id": IDR_ID, "segment_id": SEGMENT_ID, "action": params[2], "set_by": params[3], "set_at": NOW}]
        if "DELETE FROM icid.idr_segment_attributions" in sql:
            return [{"dropped": 1}]
        return listed
    return answer


class TestTheView:
    def test_a_draft_lists_its_segments_and_rolls_the_attributed_ones_up(self, admin_client):
        rows = [segment_row(), segment_row(SEGMENT_2, length_ft=Decimal("5.50"), amount=Decimal("5.50")),
                segment_row(UUID(int=3), state="excluded", override="exclude")]
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(IDR_SEGMENTS, side_effect=segment_statements(rows)) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["frozen"] is False and data["counts"] == {"attributed": 2, "excluded": 1, "claimed_elsewhere": 0,
                                                                "total": 3}
        assert data["pay_items"] == [{"pay_item_id": str(CURB_ITEM), "pay_item_ref": "4.08 AA",
                                      "budget_code": "HW-200(S)", "description": "Concrete Curb (18\" Deep)",
                                      "unit": "LF", "amount": 25.5, "segment_count": 2}]
        assert data["segments"][0]["display_address"] == "156 22nd ST"
        assert "tp.property_id IS NOT NULL OR a.action = 'include'" in run.call_args.args[0]

    def test_a_segment_without_a_pay_item_in_lf_is_unmapped(self, admin_client):
        rows = [segment_row(pay_item_id=None, pay_item_ref=None, budget_code=None, unit=None, amount=None)]
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(IDR_SEGMENTS, side_effect=segment_statements(rows)):
            data = admin_client.get(URL).json()["data"]
        assert data["unmapped"] == 1 and data["pay_items"] == []

    def test_an_approved_idr_lists_what_it_claimed_and_the_rows_it_wrote(self, admin_client):
        frozen = [{"pay_item_ref": "4.08 AA", "budget_code": "HW-200(S)", "description": "Concrete Curb",
                   "unit": "LF", "amount": Decimal("333.43")}]
        with patch(IDRS, return_value=[APPROVED_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([segment_row(state="claimed")])) as run, \
                patch("api.services.idr_segments.list_source_quantities", return_value=frozen) as quantities:
            data = admin_client.get(URL).json()["data"]
        assert data["frozen"] is True and data["pay_items"][0]["amount"] == 333.43
        assert "s.claimed_by_idr_id = i.idr_id" in run.call_args.args[0]
        assert quantities.call_args.args == (IDR_ID, "from_segments")

    def test_an_unknown_idr_is_404(self, admin_client):
        with patch(IDRS, return_value=[]):
            assert admin_client.get(URL).status_code == 404


class TestOverrides:
    def test_excluding_a_task_order_segment_writes_an_exclude(self):
        with signed_in(REPORTER) as reporter, patch(IDRS, return_value=[DRAFT_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([], project_segment())) as run:
            response = reporter.post(f"{URL}/{SEGMENT_ID}/exclude")
        assert response.status_code == 200 and response.json()["message"] == "Segment excluded"
        write = next(call for call in run.call_args_list if "INSERT INTO" in call.args[0])
        assert write.args[1] == (IDR_ID, SEGMENT_ID, "exclude", REPORTER["uuid"])

    def test_excluding_an_included_segment_drops_the_override(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([], project_segment(), in_scope=False)) as run:
            assert admin_client.post(f"{URL}/{SEGMENT_ID}/exclude").status_code == 200
        assert any("DELETE FROM icid.idr_segment_attributions" in call.args[0] for call in run.call_args_list)

    def test_including_one_from_elsewhere_writes_an_include(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([], project_segment(), in_scope=False)) as run:
            assert admin_client.post(f"{URL}/{SEGMENT_ID}/include").status_code == 200
        write = next(call for call in run.call_args_list if "INSERT INTO" in call.args[0])
        assert write.args[1][2] == "include"

    def test_including_a_deleted_segment_is_400(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([], project_segment(deleted=True))):
            response = admin_client.post(f"{URL}/{SEGMENT_ID}/include")
        assert response.status_code == 400 and response.json() == {"detail": "Segment is deleted"}

    def test_a_segment_another_idr_claimed_is_409(self, admin_client):
        found = project_segment(claimed_by_idr_id=OTHER_IDR_ID, claimed_by_idr_number="IDR-002")
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(IDR_SEGMENTS, side_effect=segment_statements([], found)):
            response = admin_client.post(f"{URL}/{SEGMENT_ID}/exclude")
        assert response.status_code == 409 and response.json() == {"detail": "Segment is claimed by IDR IDR-002"}

    def test_a_segment_of_another_project_is_404(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([], project_segment(project_id="SE384"))):
            assert admin_client.post(f"{URL}/{SEGMENT_ID}/exclude").status_code == 404

    def test_someone_elses_draft_is_403(self):
        with signed_in(STRANGER) as stranger, patch(IDRS, return_value=[DRAFT_IDR]), \
                patch(IDR_SEGMENTS, side_effect=segment_statements([], project_segment())):
            assert stranger.post(f"{URL}/{SEGMENT_ID}/exclude").status_code == 403

    def test_an_approved_idr_is_409(self, admin_client):
        with patch(IDRS, return_value=[APPROVED_IDR]), patch(IDR_SEGMENTS, side_effect=segment_statements([])):
            assert admin_client.post(f"{URL}/{SEGMENT_ID}/include").status_code == 409


class TestStatements:
    def test_a_segment_is_measured_by_its_length_in_lf_only(self):
        assert "= 'LF' THEN s.length_ft END" in SEGMENT_AMOUNT

    def test_approval_claims_unclaimed_segments_on_the_report_date(self):
        cte = " ".join(claim_segments_cte("moved").split())
        assert "SET claimed_by_idr_id = i.idr_id, constructed_on = i.report_date" in cte
        assert "s.segment_id IN (SELECT unnest(%s::uuid[])) AND s.claimed_by_idr_id IS NULL" in cte

    def test_unlock_and_delete_release_every_claim(self):
        cte = " ".join(release_segments_cte("moved").split())
        assert "SET claimed_by_idr_id = NULL, constructed_on = NULL" in cte and "s.claimed_by_idr_id = i.idr_id" in cte

    def test_the_quantity_rows_are_the_claimed_lengths_per_pay_item(self):
        cte = " ".join(insert_segment_quantities_cte("moved").split())
        assert "'PIR_SEGMENTS'" in cte and "'from_segments'" in cte and "round(sum(" in cte
        assert "GROUP BY i.project_id, i.idr_id, i.report_date, i.reporter_uuid, ci.contract_item_id" in cte

    def test_an_undelete_claims_back_the_segments_its_delete_recorded(self):
        assert "'released_segment_ids'" in reclaim_segment_ids_sql("target")
        note = released_note([], [SEGMENT_ID])
        assert note == f'{{"released_flag_ids": [], "released_segment_ids": ["{SEGMENT_ID}"]}}'
        assert released_note([]) == '{"released_flag_ids": []}'

    def test_the_list_is_one_statement(self):
        with patch(IDR_SEGMENTS, return_value=[]) as run:
            assert list_idr_segments(IDR_ID, False) == []
        assert run.call_args.args[1] == (IDR_ID,)

    def test_the_frozen_rows_are_read_by_source(self):
        with patch("api.queries.quantities.run_query", return_value=[]) as run:
            list_source_quantities(IDR_ID, "from_segments")
        assert run.call_args.args[1] == (IDR_ID, "from_segments")


class TestManualEntryAndExport:
    def test_a_report_cant_hold_a_pay_item_its_segments_fill(self):
        with patch(IDR_SEGMENTS, side_effect=segment_statements([segment_row()])):
            conflicts = manual_segment_conflicts(DRAFT_IDR, [{"itemNo": " 4.08 AA ", "budgetCode": "HW-200(S)"},
                                                             {"itemNo": "4.13 AAS", "budgetCode": "HW-200(S)"}])
        assert conflicts == ["Pay item 4.08 AA (HW-200(S)) comes from this IDR's curb segments; remove it from the "
                             "report, or exclude its segments from the IDR"]

    def test_an_excluded_segment_doesnt_fence_its_pay_item(self):
        with patch(IDR_SEGMENTS, side_effect=segment_statements([segment_row(state="excluded")])):
            assert manual_segment_conflicts(DRAFT_IDR, [{"itemNo": "4.08 AA", "budgetCode": "HW-200(S)"}]) == []

    def test_the_export_prints_a_line_per_pay_item(self):
        with patch(IDR_SEGMENTS, side_effect=segment_statements([segment_row(), segment_row(SEGMENT_2)])):
            assert export_segment_lines(DRAFT_IDR) == [{"itemNo": "4.08 AA", "budgetCode": "HW-200(S)",
                                                        "payQuantity": "40.00", "unit": "LF",
                                                        "description": "Concrete Curb (18\" Deep)"}]


@pytest.mark.parametrize("key", ["segments_claimed", "segments_released"])
def test_idr_responses_say_how_many_segments_moved(key):
    from api.schemas.idr import IdrResponse
    assert key in IdrResponse.model_fields
