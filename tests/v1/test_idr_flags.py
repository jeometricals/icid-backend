import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.idr_flags import (
    claim_flags_cte,
    claimed_flag_ids,
    clear_attribution,
    in_default_scope,
    list_idr_flags,
    reclaim_ids_sql,
    release_flags_cte,
    released_claims,
    released_note,
    set_attribution,
)
from api.queries.quantities import list_flag_quantities
from api.services.idr_flags import (
    EXPORT_HEADING,
    attributed_flag_ids,
    export_lines,
    flag_pay_item_keys,
    idr_flags,
    manual_entry_conflicts,
)
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/idrs/{idr_id}/attributed-flags (R1f): which flags an IDR covers, and the overrides that change them.
# Mock rows are dicts, as run_query returns them.
# ---------------------------------------------------------------------------

IDRS = "api.queries.idrs.run_query"
IDR_FLAGS = "api.queries.idr_flags.run_query"
FLAGS = "api.queries.flags.run_query"
PROJECTS = "api.queries.projects.run_query"
QUANTITIES = "api.queries.quantities.run_query"

IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
OTHER_IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5f")
TASK_ORDER_ID = UUID("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b")
FLAG_ID = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d")
FLAG_2 = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0e")
FLAG_3 = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0f")
FLAG_4 = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c10")
PIR_ID = UUID("4a5b6c7d-8e9f-4a0b-8c1d-2e3f4a5b6c7d")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
SF_ITEM = UUID("6d7e8f90-a1b2-4c3d-8e4f-5a6b7c8d9e0f")
CY_ITEM = UUID("6d7e8f90-a1b2-4c3d-8e4f-5a6b7c8d9e10")
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)

def real_flag_quantities():
    """
    Undo the session's stub of an approved IDR's frozen rollup (tests/conftest.py), so the read goes to the quantities statement a test patches.
    Takes nothing.
    Returns the patch, as a context manager.
    """
    return patch("api.services.idr_flags.list_flag_quantities", list_flag_quantities)

URL = f"/v1/idrs/{IDR_ID}/attributed-flags"

REPORTER = {**ADMIN_USER_ROW, "uuid": UUID("c0000000-0000-4000-8000-000000000003"), "email": "ian@icid.local",
            "first_name": "Ian", "role": None}
STRANGER = {**REPORTER, "uuid": UUID("c0000000-0000-4000-8000-000000000009"), "email": "sam@icid.local"}
OLIVE = {**REPORTER, "uuid": UUID("f0000000-0000-4000-8000-000000000006"), "email": "olive@icid.local"}
REX = {**REPORTER, "uuid": UUID("f0000000-0000-4000-8000-000000000007"), "email": "rex@icid.local"}

DRAFT_IDR = {
    "idr_id": IDR_ID, "project_id": "SE384", "reporter_uuid": REPORTER["uuid"], "report_date": date(2026, 10, 5),
    "work_start_time": None, "work_end_time": None, "inspector_start_time": None, "inspector_end_time": None,
    "temp_low": None, "temp_high": None, "weather_am": None, "weather_pm": None, "total_pages": None,
    "has_dismissed_auto_general": False, "status": "draft", "submitted_at": None, "created_at": NOW,
    "updated_at": NOW, "inspector_signature_path": None, "inspector_signed_at": None, "idr_number": None,
    "stage1_reviewer_uuid": None, "stage1_accepted_at": None, "stage1_reviewed_at": None, "re_reviewer_uuid": None,
    "stage2_accepted_at": None, "re_signature_path": None, "re_signed_at": None, "return_reason": None,
    "returned_from": None, "deleted_at": None, "deleted_by": None, "task_order_id": TASK_ORDER_ID,
}
STAGE1_IDR = {**DRAFT_IDR, "status": "stage1_review", "stage1_reviewer_uuid": OLIVE["uuid"], "idr_number": "005"}
APPROVED_IDR = {**DRAFT_IDR, "status": "approved", "idr_number": "005"}


def flag_row(flag_id: UUID = FLAG_ID, **overrides: Any) -> dict[str, Any]:
    """
    Build a row as list_idr_flags returns one: a sidewalk's 4-inch flag mapped to an SF pay item, attributed.
    Takes the flag's id and any column overrides.
    Returns the row.
    """
    return {
        "flag_id": flag_id, "pir_id": PIR_ID, "pir_page_id": PAGE_ID, "page_number": 1, "display_order": 1,
        "shape": "rectangle", "surveyed_thickness": "4in", "property_id": PROPERTY_ID, "property_kind": "sidewalk",
        "boro": "Brooklyn", "block": "1234", "lot": "52", "address": "350 Main St", "intersection_street_1": None,
        "intersection_street_2": None, "quadrant": None, "facing_street": None, "pay_item_id": SF_ITEM,
        "pay_item_ref": "4.13 AAS", "budget_code": "HW0001Q-CONS", "pay_item_description": "4\" SIDEWALK",
        "unit": "SF", "quantity_sf": Decimal("25"), "amount": Decimal("25"), "state": "attributed",
        "override": None, "in_task_order": True, "claimed_by_idr_id": None, "claimed_by_idr_number": None,
        "is_constructed": False, "constructed_on": None, "deleted_at": None, **overrides,
    }


def cy_row(flag_id: UUID, square_feet: str) -> dict[str, Any]:
    """
    Build a 4-inch flag's row mapped to a CY pay item: its amount the square feet x 4 in / 324.
    Takes the flag's id and its square feet as text.
    Returns the row.
    """
    sf = Decimal(square_feet)
    return flag_row(flag_id, pay_item_id=CY_ITEM, pay_item_ref="UTL-6.06", budget_code="EP0007-CONS",
                    pay_item_description="EXCAVATION", unit="CY", quantity_sf=sf, amount=sf * 4 / 324)


def get_flag_row(**overrides: Any) -> dict[str, Any]:
    """
    Build a row as get_flag returns one, with the columns the routes read (its project, claim and deleted states).
    Takes any column overrides.
    Returns the row.
    """
    return {"id": FLAG_ID, "project_id": "SE384", "claimed_by_idr_id": None, "claimed_by_idr_number": None,
            "deleted_at": None, "page_deleted_at": None, "pir_deleted_at": None, "property_deleted_at": None,
            **overrides}


def idr_flags_backend(flags: list[dict[str, Any]], in_scope: bool = True):
    """
    Stand in for api.queries.idr_flags: the list, the scope check and the two override writes.
    Takes the flag rows the list returns and whether the flag is in the IDR's default scope.
    Returns a side_effect for run_query.
    """
    def run(sql: str, params: Any = None) -> Optional[list[dict[str, Any]]]:
        """
        Answer one statement of api.queries.idr_flags by what it is.
        Takes the statement and its parameters.
        Returns its rows.
        """
        if "AS in_scope" in sql:
            return [{"in_scope": in_scope}]
        if "INSERT INTO icid.idr_flag_attributions" in sql:
            return [{"idr_id": params[0], "flag_id": params[1], "action": params[2], "set_by": params[3],
                     "set_at": NOW}]
        if "DELETE FROM icid.idr_flag_attributions" in sql:
            return [{"dropped": 1}]
        return flags
    return run


@contextmanager
def backend(idr: dict[str, Any] = DRAFT_IDR, flags: Optional[list[dict[str, Any]]] = None,
            flag: Optional[dict[str, Any]] = None, in_scope: bool = True, roles: tuple = ()):
    """
    Patch every statement an IDR-flags route runs.
    Takes the IDR row (None for no IDR), the flag rows its list returns, the row get_flag returns, whether the flag
    is in the IDR's default scope, and the project roles of the signed-in user.
    Yields the idr_flags run_query mock.
    """
    with patch(IDRS, return_value=[idr] if idr else []), \
            patch(IDR_FLAGS, side_effect=idr_flags_backend(flags if flags is not None else [flag_row()], in_scope)) as run, \
            patch(FLAGS, return_value=[flag or get_flag_row()]), \
            patch(PROJECTS, return_value=[{"role": role} for role in roles]):
        yield run


def writes(run) -> list[tuple[str, tuple]]:
    """
    Pick the override writes out of the idr_flags statements a route ran.
    Takes the run_query mock.
    Returns (statement, parameters) for each INSERT or DELETE on icid.idr_flag_attributions.
    """
    return [(call.args[0], call.args[1]) for call in run.call_args_list
            if "icid.idr_flag_attributions" in call.args[0] and ("INSERT" in call.args[0] or "DELETE" in call.args[0])]


class TestTheView:
    def test_a_live_idr_lists_its_flags_with_counts_and_a_rollup(self, admin_client):
        flags = [flag_row(), flag_row(FLAG_2, quantity_sf=Decimal("20"), amount=Decimal("20")),
                 flag_row(FLAG_3, state="excluded", override="exclude"),
                 flag_row(FLAG_4, state="claimed_elsewhere", claimed_by_idr_id=OTHER_IDR_ID,
                          claimed_by_idr_number="003")]
        with backend(flags=flags) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["frozen"] is False and data["task_order_id"] == str(TASK_ORDER_ID)
        assert data["counts"] == {"attributed": 2, "excluded": 1, "claimed_elsewhere": 1, "total": 4}
        assert data["pay_items"] == [{"pay_item_id": str(SF_ITEM), "pay_item_ref": "4.13 AAS",
                                      "budget_code": "HW0001Q-CONS", "description": "4\" SIDEWALK", "unit": "SF",
                                      "amount": 45.0, "flag_count": 2}]
        assert data["unmapped"] == 0
        assert data["flags"][0]["display_address"] == "350 Main St"
        assert run.call_args.args[1] == (IDR_ID,)

    def test_the_rollup_sums_each_pay_item_and_rounds_halves_up_once(self, admin_client):
        flags = [cy_row(FLAG_ID, "25"), cy_row(FLAG_2, "25"), cy_row(FLAG_3, "20"), cy_row(FLAG_4, "20"),
                 flag_row(UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c11"), quantity_sf=Decimal("12.345"),
                          amount=Decimal("12.345"))]
        with backend(flags=flags):
            data = admin_client.get(URL).json()["data"]
        assert [(line["pay_item_ref"], line["unit"], line["amount"], line["flag_count"]) for line in data["pay_items"]] \
            == [("4.13 AAS", "SF", 12.35, 1), ("UTL-6.06", "CY", 1.11, 4)]

    def test_flags_without_a_measurable_pay_item_are_unmapped(self, admin_client):
        flags = [flag_row(), flag_row(FLAG_2, pay_item_id=None, pay_item_ref=None, budget_code=None, unit=None,
                                      amount=None),
                 flag_row(FLAG_3, unit="LF", amount=None)]
        with backend(flags=flags):
            data = admin_client.get(URL).json()["data"]
        assert data["unmapped"] == 2 and [line["flag_count"] for line in data["pay_items"]] == [1]

    def test_an_approved_idr_is_frozen_its_rollup_the_rows_it_wrote(self, admin_client):
        frozen = [{"pay_item_ref": "4.13 AAS", "budget_code": "HW0001Q-CONS", "description": "4\" SIDEWALK",
                   "unit": "SF", "amount": Decimal("90.00")}]
        claimed = flag_row(state="claimed", claimed_by_idr_id=IDR_ID, claimed_by_idr_number="005",
                           is_constructed=True, constructed_on=date(2026, 10, 5))
        with backend(idr=APPROVED_IDR, flags=[claimed]) as run, real_flag_quantities(),                 patch(QUANTITIES, return_value=frozen) as quantities:
            data = admin_client.get(URL).json()["data"]
        assert data["frozen"] is True and data["counts"]["attributed"] == 1
        assert data["pay_items"] == [{"pay_item_id": None, "pay_item_ref": "4.13 AAS", "budget_code": "HW0001Q-CONS",
                                      "description": "4\" SIDEWALK", "unit": "SF", "amount": 90.0,
                                      "flag_count": None}]
        assert data["flags"][0]["constructed_on"] == "2026-10-05"
        assert "f.claimed_by_idr_id = i.idr_id" in run.call_args.args[0]
        assert "source = %s" in quantities.call_args.args[0] and quantities.call_args.args[1][1] == "from_flags"

    def test_an_unknown_idr_is_404(self, admin_client):
        with backend(idr=None):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}

    def test_a_failed_read_is_500(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(IDR_FLAGS, return_value=None):
            response = admin_client.get(URL)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to load the IDR's flags"}

    def test_any_signed_in_user_reads_it(self):
        with signed_in(STRANGER) as client, backend():
            assert client.get(URL).status_code == 200


class TestExclude:
    url = f"{URL}/{FLAG_ID}/exclude"

    def test_a_flag_on_its_task_order_is_excluded_by_an_override(self, admin_client):
        with backend(flags=[flag_row(state="excluded", override="exclude")], in_scope=True) as run:
            response = admin_client.post(self.url)
        assert response.status_code == 200 and response.json()["message"] == "Flag excluded"
        assert response.json()["data"]["counts"]["excluded"] == 1
        (sql, params), = writes(run)
        assert "INSERT INTO icid.idr_flag_attributions" in sql and "ON CONFLICT (idr_id, flag_id) DO UPDATE" in sql
        assert params == (IDR_ID, FLAG_ID, "exclude", ADMIN_USER_ROW["uuid"])

    def test_a_flag_it_had_included_is_dropped_back_out(self, admin_client):
        with backend(in_scope=False) as run:
            assert admin_client.post(self.url).status_code == 200
        (sql, params), = writes(run)
        assert "DELETE FROM icid.idr_flag_attributions" in sql and params == (IDR_ID, FLAG_ID)

    def test_a_flag_of_another_project_is_404(self, admin_client):
        with backend(flag=get_flag_row(project_id="HWS0023")) as run:
            response = admin_client.post(self.url)
        assert response.status_code == 404 and response.json() == {"detail": "Flag not found in this IDR's project"}
        assert writes(run) == []

    def test_a_flag_claimed_by_another_idr_is_409_naming_it(self, admin_client):
        with backend(flag=get_flag_row(claimed_by_idr_id=OTHER_IDR_ID, claimed_by_idr_number="003")) as run:
            response = admin_client.post(self.url)
        assert response.status_code == 409 and response.json() == {"detail": "Flag is claimed by IDR 003"}
        assert writes(run) == []


class TestInclude:
    url = f"{URL}/{FLAG_ID}/include"

    def test_a_flag_on_its_task_order_loses_its_exclusion(self, admin_client):
        with backend(in_scope=True) as run:
            response = admin_client.post(self.url)
        assert response.status_code == 200 and response.json()["message"] == "Flag included"
        (sql, params), = writes(run)
        assert "DELETE FROM icid.idr_flag_attributions" in sql and params == (IDR_ID, FLAG_ID)

    def test_a_flag_from_elsewhere_in_the_project_is_included_by_an_override(self, admin_client):
        with backend(in_scope=False) as run:
            assert admin_client.post(self.url).status_code == 200
        (sql, params), = writes(run)
        assert "INSERT INTO icid.idr_flag_attributions" in sql
        assert params == (IDR_ID, FLAG_ID, "include", ADMIN_USER_ROW["uuid"])

    @pytest.mark.parametrize("deleted", ["deleted_at", "page_deleted_at", "pir_deleted_at", "property_deleted_at"])
    def test_a_deleted_flag_is_400(self, admin_client, deleted):
        with backend(flag=get_flag_row(**{deleted: NOW})) as run:
            response = admin_client.post(self.url)
        assert response.status_code == 400 and response.json() == {"detail": "Flag is deleted"}
        assert writes(run) == []

    def test_a_flag_of_another_project_is_404(self, admin_client):
        with backend(flag=get_flag_row(project_id="HWS0023")):
            assert admin_client.post(self.url).status_code == 404

    def test_a_flag_claimed_by_another_idr_without_a_number_is_409(self, admin_client):
        with backend(flag=get_flag_row(claimed_by_idr_id=OTHER_IDR_ID)):
            response = admin_client.post(self.url)
        assert response.status_code == 409 and response.json() == {"detail": "Flag is claimed by IDR without a number"}

    def test_a_flag_this_idr_claimed_is_not_another_idrs(self, admin_client):
        with backend(flag=get_flag_row(claimed_by_idr_id=IDR_ID, claimed_by_idr_number="005"), in_scope=False):
            assert admin_client.post(self.url).status_code == 200


class TestWhoChangesTheFlags:
    url = f"{URL}/{FLAG_ID}/exclude"

    def test_a_demo_user_only_reads(self, demo_client):
        own = {**DRAFT_IDR, "reporter_uuid": DEMO_USER_ROW["uuid"]}
        with backend(idr=own) as run:
            response = demo_client.post(self.url)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: IDR flags are read-only"}
        assert writes(run) == []

    @pytest.mark.parametrize("idr,detail", [
        (APPROVED_IDR, "IDR is approved: its flags are claimed; unlock it to change them"),
        ({**DRAFT_IDR, "deleted_at": NOW, "status": "deleted"}, "IDR is deleted"),
        ({**DRAFT_IDR, "status": "submitted"}, "A submitted IDR's flags change once a reviewer accepts it"),
    ])
    def test_an_approved_deleted_or_submitted_idr_is_409(self, admin_client, idr, detail):
        with backend(idr=idr) as run:
            response = admin_client.post(self.url)
        assert response.status_code == 409 and response.json() == {"detail": detail}
        assert writes(run) == []

    def test_its_inspector_changes_their_draft(self):
        with signed_in(REPORTER) as client, backend() as run:
            assert client.post(self.url).status_code == 200
        assert writes(run)[0][1][3] == REPORTER["uuid"]

    def test_anyone_else_is_403_on_a_draft(self):
        with signed_in(STRANGER) as client, backend() as run:
            response = client.post(self.url)
        assert response.status_code == 403
        assert response.json() == {"detail": "Only the IDR's inspector can change its flags while it is a draft"}
        assert writes(run) == []

    def test_an_admin_changes_a_draft(self, admin_client):
        with backend():
            assert admin_client.post(self.url).status_code == 200

    def test_the_stage_reviewer_changes_it_in_review(self):
        with signed_in(OLIVE) as client, backend(idr=STAGE1_IDR, roles=("oe",)) as run:
            assert client.post(self.url).status_code == 200
        assert writes(run)[0][1][3] == OLIVE["uuid"]

    def test_another_reviewer_is_403_in_review(self):
        with signed_in(REX) as client, backend(idr=STAGE1_IDR, roles=("oe",)) as run:
            response = client.post(self.url)
        assert response.status_code == 403 and writes(run) == []

    def test_an_unknown_idr_is_404(self, admin_client):
        with backend(idr=None):
            assert admin_client.post(self.url).status_code == 404


class TestService:
    def test_attributed_flag_ids_are_only_the_attributed(self):
        flags = [flag_row(), flag_row(FLAG_2, state="excluded"), flag_row(FLAG_3, state="claimed_elsewhere")]
        with patch(IDR_FLAGS, return_value=flags):
            assert attributed_flag_ids(DRAFT_IDR) == [FLAG_ID]

    def test_flag_pay_item_keys_are_the_pay_items_of_the_flags_that_count(self):
        flags = [flag_row(), cy_row(FLAG_2, "10"), flag_row(FLAG_3, state="excluded", pay_item_ref="X", budget_code="Y"),
                 flag_row(FLAG_4, pay_item_id=None, pay_item_ref=None, budget_code=None)]
        with patch(IDR_FLAGS, return_value=flags):
            assert flag_pay_item_keys(DRAFT_IDR) == {("4.13 AAS", "HW0001Q-CONS"), ("UTL-6.06", "EP0007-CONS")}

    def test_manual_entry_conflicts_match_item_and_budget_code_trimmed(self):
        with patch(IDR_FLAGS, return_value=[flag_row()]):
            conflicts = manual_entry_conflicts(DRAFT_IDR, [
                {"itemNo": " 4.13 AAS ", "budgetCode": "HW0001Q-CONS "},
                {"itemNo": "4.13 AAS", "budgetCode": "OTHER"},
                "not an item",
            ])
        assert conflicts == ["Pay item 4.13 AAS (HW0001Q-CONS) comes from this IDR's PIR flags; "
                             "remove it from the report, or exclude its flags from the IDR"]

    def test_no_pay_items_reads_no_flags(self):
        with patch(IDR_FLAGS) as run:
            assert manual_entry_conflicts(DRAFT_IDR, []) == [] and manual_entry_conflicts(DRAFT_IDR, None) == []
        run.assert_not_called()

    def test_export_lines_head_the_rollup_with_pir_flags(self):
        with patch(IDR_FLAGS, return_value=[flag_row(), cy_row(FLAG_2, "25"), cy_row(FLAG_3, "20")]):
            lines = export_lines(DRAFT_IDR)
        assert lines == [
            {"description": EXPORT_HEADING},
            {"itemNo": "4.13 AAS", "budgetCode": "HW0001Q-CONS", "payQuantity": "25.00", "unit": "SF",
             "description": "4\" SIDEWALK"},
            {"itemNo": "UTL-6.06", "budgetCode": "EP0007-CONS", "payQuantity": "0.56", "unit": "CY",
             "description": "EXCAVATION"},
        ]
        assert EXPORT_HEADING == "PIR Flags"

    def test_export_lines_of_an_approved_idr_are_its_frozen_rows(self):
        frozen = [{"pay_item_ref": "4.13 AAS", "budget_code": "B", "description": "D", "unit": "SF",
                   "amount": Decimal("90.00")}]
        with patch(IDR_FLAGS) as run, real_flag_quantities(), patch(QUANTITIES, return_value=frozen):
            lines = export_lines(APPROVED_IDR)
        assert lines[1]["payQuantity"] == "90.00"
        run.assert_not_called()

    def test_no_flags_no_export_lines(self):
        with patch(IDR_FLAGS, return_value=[]):
            assert export_lines(DRAFT_IDR) == []

    def test_the_view_counts_claimed_flags_once_approved(self):
        with patch(IDR_FLAGS, return_value=[flag_row(state="claimed")]), patch(QUANTITIES, return_value=[]):
            view = idr_flags(APPROVED_IDR)
        assert view["counts"]["attributed"] == 1 and view["frozen"] is True


class TestQueries:
    def test_the_live_list_reads_the_task_order_and_the_overrides(self):
        with patch(IDR_FLAGS, return_value=[]) as run:
            list_idr_flags(IDR_ID, claimed_only=False)
        sql = " ".join(run.call_args.args[0].split())
        assert "(tp.property_id IS NOT NULL OR a.action = 'include')" in sql
        assert "f.deleted_at IS NULL AND pg.deleted_at IS NULL AND pir.deleted_at IS NULL AND p.deleted_at IS NULL" in sql
        assert "LEFT JOIN icid.task_orders t ON t.id = i.task_order_id AND t.deleted_at IS NULL" in sql
        assert "WHEN a.action = 'exclude' THEN 'excluded'" in sql
        assert run.call_args.args[1] == (IDR_ID,)

    def test_the_claimed_list_reads_only_what_it_claimed(self):
        with patch(IDR_FLAGS, return_value=[]) as run:
            list_idr_flags(IDR_ID, claimed_only=True)
        sql = " ".join(run.call_args.args[0].split())
        assert "WHERE i.idr_id = %s AND f.claimed_by_idr_id = i.idr_id ORDER BY" in sql

    def test_in_default_scope(self):
        with patch(IDR_FLAGS, return_value=[{"in_scope": True}]) as run:
            assert in_default_scope(IDR_ID, FLAG_ID) is True
        assert run.call_args.args[1] == (IDR_ID, FLAG_ID)
        with patch(IDR_FLAGS, return_value=None):
            assert in_default_scope(IDR_ID, FLAG_ID) is None

    def test_set_and_clear_attribution(self):
        row = {"idr_id": IDR_ID, "flag_id": FLAG_ID, "action": "exclude", "set_by": None, "set_at": NOW}
        with patch(IDR_FLAGS, return_value=[row]) as run:
            assert set_attribution(IDR_ID, FLAG_ID, "exclude", ADMIN_USER_ROW["uuid"]) == row
        assert "SET action = EXCLUDED.action, set_by = EXCLUDED.set_by, set_at = now()" in run.call_args.args[0]
        with patch(IDR_FLAGS, return_value=[{"dropped": 0}]):
            assert clear_attribution(IDR_ID, FLAG_ID) == 0

    def test_claimed_flag_ids(self):
        with patch(IDR_FLAGS, return_value=[{"id": FLAG_ID}, {"id": FLAG_2}]) as run:
            assert claimed_flag_ids(IDR_ID) == [FLAG_ID, FLAG_2]
        assert "WHERE claimed_by_idr_id = %s" in run.call_args.args[0]
        with patch(IDR_FLAGS, return_value=None):
            assert claimed_flag_ids(IDR_ID) is None

    def test_released_claims_reads_the_latest_admin_delete_note(self):
        record = {"status_before": "approved", "released": [FLAG_ID], "taken": []}
        with patch(IDR_FLAGS, return_value=[record]) as run:
            assert released_claims(IDR_ID) == record
        sql = " ".join(run.call_args.args[0].split())
        assert "a.action = 'admin_delete'" in sql and "-> 'released_flag_ids'" in sql
        assert "WHERE f.claimed_by_idr_id <> %s" in sql and run.call_args.args[1] == (IDR_ID, IDR_ID)

    def test_released_note_is_json_of_the_uuids(self):
        assert json.loads(released_note([FLAG_ID, FLAG_2])) == {"released_flag_ids": [str(FLAG_ID), str(FLAG_2)]}
        assert json.loads(released_note([])) == {"released_flag_ids": []}

    def test_the_claim_cte_takes_only_unclaimed_flags(self):
        sql = " ".join(claim_flags_cte("moved").split())
        assert sql.startswith("claimed_flags AS ( UPDATE icid.flags f")
        assert "SET claimed_by_idr_id = i.idr_id, is_constructed = true, constructed_on = i.report_date" in sql
        assert "WHERE f.id IN (SELECT unnest(%s::uuid[])) AND f.claimed_by_idr_id IS NULL" in sql
        reclaim = " ".join(claim_flags_cte("approved", reclaim_ids_sql("target")).split())
        assert "%s" not in reclaim and "(SELECT note_before FROM target)" in reclaim

    def test_the_release_cte_frees_every_claim_of_the_idr(self):
        sql = " ".join(release_flags_cte("moved").split())
        assert "SET claimed_by_idr_id = NULL, is_constructed = false, constructed_on = NULL" in sql
        assert "WHERE f.claimed_by_idr_id = i.idr_id RETURNING f.id" in sql and "%s" not in sql

    def test_every_table_is_in_the_icid_schema(self):
        with patch(IDR_FLAGS, return_value=[]) as run:
            list_idr_flags(IDR_ID, claimed_only=False)
        for table in ("flags", "pirs", "properties", "pir_pages", "idr_flag_attributions", "task_orders",
                      "task_order_properties", "contract_items", "idrs"):
            assert f"icid.{table} " in run.call_args.args[0]
