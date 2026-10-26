from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from api.queries.idr_report_site_pay_items import SITE_UNIT, insert_site_quantities_cte
from api.queries.idrs import admin_unlock_idr, approve_stage2, undelete_idr
from api.services.export import _with_site_lines
from api.services.pay_item_sources import site_export_lines
from tests.conftest import signed_in
from tests.v1.test_idr_report_sites import (
    ADMIN_ID,
    DRAFT_IDR,
    IDR_ID,
    IDRS,
    NOW,
    OTHER_REPORT_ID,
    PROPERTY_ID,
    REPORT_ID,
    REPORTER,
    REPORTS,
    SIDEWALK_SITE,
    SITE_ID,
    SITES,
    report,
)

# ---------------------------------------------------------------------------
# /v1/idrs/{idr_id}/reports/{report_id}/sites/{site_id}/pay-items (Slice V1): pay items entered on an imported site,
# and /v1/idrs/{idr_id}/pay-item-sources, an IDR's pay items by source.
# ---------------------------------------------------------------------------

PAY = "api.queries.idr_report_site_pay_items.run_query"
ITEM_ID = UUID("5a4b5c6d-7e8f-4a9b-8c1d-2e3f4a5b6c7d")
CONTRACT_ITEM_ID = UUID("6b5c6d7e-8f9a-4b1c-9d2e-3f4a5b6c7d8e")
URL = f"/v1/idrs/{IDR_ID}/reports/{REPORT_ID}/sites/{SITE_ID}/pay-items"
SOURCES = f"/v1/idrs/{IDR_ID}/pay-item-sources"
PAY_ITEM = {"id": ITEM_ID, "report_site_id": SITE_ID, "contract_item_id": CONTRACT_ITEM_ID, "quantity": Decimal("12.5"),
            "note": "MTP", "added_at": NOW, "added_by": ADMIN_ID, "pay_item_ref": "6.70 AB", "budget_code": "HW-200(S)",
            "description": "Maintenance and protection of traffic", "unit": "LS", "overrides_auto": False}
IDR_ITEM = {**PAY_ITEM, "report_id": REPORT_ID, "property_id": PROPERTY_ID, "kind": "sidewalk",
            "address": "138-22 227 Street", "intersection_street_1": None, "intersection_street_2": None,
            "quadrant": None, "facing_street": None}
IN_REVIEW = {**DRAFT_IDR, "status": "stage1_review", "stage1_reviewer_uuid": ADMIN_ID}


def pay_db(rows: list, audit: list = (), written: list = None):
    """
    Stand in for the site pay items' run_query, answering each statement by what it is.
    Takes the site's pay items (what a list reads), the override audit rows and what a write returns (rows when None).
    Returns the side_effect function.
    """
    def answer(sql: str, params: tuple) -> list:
        """Answer one statement: the audit read, the site's list, or a write."""
        if "FROM icid.idr_audit a" in sql:
            return list(audit)
        if sql.lstrip().startswith("SELECT"):
            return list(rows)
        return list(rows if written is None else written)
    return answer


def writes(run) -> list:
    """The (sql, params) of the statements a mocked run_query ran that weren't plain reads."""
    return [call.args for call in run.call_args_list if not call.args[0].lstrip().startswith("SELECT")]


class TestList:
    def test_a_site_lists_its_pay_items(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(PAY, side_effect=pay_db([PAY_ITEM])) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        item = response.json()["data"][0]
        assert item["pay_item_ref"] == "6.70 AB" and item["quantity"] == 12.5 and item["note"] == "MTP"
        assert item["overrides_auto"] is False and response.json()["audit"] == []
        sql, params = run.call_args_list[0].args
        assert "JOIN icid.idr_report_sites rs ON rs.id = sp.report_site_id" in sql and params == (REPORT_ID, SITE_ID)

    def test_a_site_that_isnt_the_reports_is_404(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[]), patch(PAY) as run:
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Site not found on this report"}
        run.assert_not_called()


class TestAdd:
    def test_its_inspector_enters_a_pay_item_on_a_draft(self):
        with signed_in(REPORTER) as inspector, patch(IDRS, return_value=[DRAFT_IDR]), \
             patch(REPORTS, return_value=[report()]), patch(SITES, return_value=[SIDEWALK_SITE]), \
             patch(PAY, return_value=[PAY_ITEM]) as run:
            response = inspector.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": "12.5",
                                                 "note": "  MTP  "})
        assert response.status_code == 201 and response.json()["data"]["id"] == str(ITEM_ID)
        sql, params = run.call_args.args
        assert "INSERT INTO icid.idr_report_site_pay_items" in sql
        assert "JOIN icid.contract_items ci ON ci.project_id = i.project_id" in sql
        # an ordinary pay item: not an override, nothing logged
        assert params == (Decimal("12.5"), "MTP", REPORTER["uuid"], False, REPORT_ID, SITE_ID, CONTRACT_ITEM_ID,
                          REPORTER["uuid"], "site_override_set", None, None)

    def test_a_contract_item_of_another_project_is_400(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(PAY, return_value=[]):
            response = admin_client.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 3})
        assert response.status_code == 400 and response.json() == {"detail": "That pay item isn't one of this IDR's project"}

    def test_a_quantity_not_above_zero_is_422(self, admin_client):
        for quantity in (0, -1, "abc"):
            with patch(IDRS, return_value=[DRAFT_IDR]), patch(PAY) as run:
                response = admin_client.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": quantity})
            assert response.status_code == 422
            run.assert_not_called()

    def test_someone_elses_draft_is_403(self):
        other = {**REPORTER, "uuid": UUID("c0000000-0000-4000-8000-000000000009")}
        with signed_in(other) as user, patch(IDRS, return_value=[DRAFT_IDR]), patch(PAY) as run:
            response = user.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 1})
        assert response.status_code == 403
        run.assert_not_called()

    def test_an_approved_idr_is_409(self, admin_client):
        with patch(IDRS, return_value=[{**DRAFT_IDR, "status": "approved"}]), patch(PAY) as run:
            response = admin_client.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 1})
        assert response.status_code == 409
        run.assert_not_called()

    def test_a_demo_user_is_refused(self, demo_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(PAY) as run:
            response = demo_client.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 1})
        assert response.status_code in (403, 404)
        run.assert_not_called()


class TestUpdate:
    def test_the_quantity_and_note_change(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), \
             patch(PAY, side_effect=pay_db([PAY_ITEM], written=[{**PAY_ITEM, "quantity": 4}])) as run:
            response = admin_client.patch(f"{URL}/{ITEM_ID}", json={"quantity": 4})
        assert response.status_code == 200 and response.json()["data"]["quantity"] == 4
        [(sql, params)] = writes(run)
        assert "SET quantity = COALESCE(%s, sp.quantity)" in sql
        # the note wasn't sent, so it is kept; not an override, so nothing is logged
        assert params == (Decimal(4), False, None, REPORT_ID, SITE_ID, ITEM_ID, ADMIN_ID, "site_override_change", None,
                          None)

    def test_a_null_note_clears_it(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), \
             patch(PAY, side_effect=pay_db([PAY_ITEM], written=[{**PAY_ITEM, "note": None}])) as run:
            admin_client.patch(f"{URL}/{ITEM_ID}", json={"note": None})
        assert writes(run)[0][1][:6] == (None, True, None, REPORT_ID, SITE_ID, ITEM_ID)

    def test_an_empty_body_or_a_null_quantity_is_422(self, admin_client):
        for body in ({}, {"quantity": None}):
            with patch(IDRS, return_value=[DRAFT_IDR]), patch(PAY) as run:
                assert admin_client.patch(f"{URL}/{ITEM_ID}", json=body).status_code == 422
            run.assert_not_called()

    def test_the_stages_reviewer_edits_in_review(self, admin_client):
        with patch(IDRS, return_value=[IN_REVIEW]), patch(REPORTS, return_value=[report()]), \
             patch("api.services.auth.get_user_roles_on_project", return_value=["oe"]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(PAY, return_value=[PAY_ITEM]):
            assert admin_client.patch(f"{URL}/{ITEM_ID}", json={"note": "x"}).status_code == 200

    def test_an_unknown_pay_item_is_404(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(PAY, return_value=[]):
            response = admin_client.patch(f"{URL}/{ITEM_ID}", json={"quantity": 2})
        assert response.status_code == 404 and response.json() == {"detail": "Pay item not found on this site"}


class TestRemove:
    def test_a_pay_item_is_removed(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), \
             patch(PAY, side_effect=pay_db([PAY_ITEM], written=[{"id": ITEM_ID}])) as run:
            assert admin_client.delete(f"{URL}/{ITEM_ID}").status_code == 204
        [(sql, params)] = writes(run)
        assert "DELETE FROM icid.idr_report_site_pay_items" in sql
        assert params == (REPORT_ID, SITE_ID, ITEM_ID, ADMIN_ID, "site_override_reset", None, None)

    def test_an_unknown_pay_item_is_404(self, admin_client):
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(PAY, return_value=[]):
            assert admin_client.delete(f"{URL}/{ITEM_ID}").status_code == 404


FLAG_LINE = {"pay_item_ref": "4.01 AAS", "budget_code": "HW-200(S)", "description": "4in sidewalk", "unit": "SF",
             "amount": Decimal("100.00")}
SEGMENT_LINE = {"pay_item_ref": "4.08 AA", "budget_code": "HW-200(S)", "description": "Concrete curb", "unit": "LF",
                "amount": Decimal("30.50")}


def sources(admin_client, reports: list, site_items: list, flags: list = (), segments: list = (), query: str = ""):
    """
    Ask for an IDR's pay-item sources with its reports, site pay items and flag and segment rollups mocked.
    Takes the reports, the site pay items, the two rollups and a query string.
    Returns the response.
    """
    with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, side_effect=[[report()], reports] if query else [reports]), \
         patch(PAY, return_value=site_items), \
         patch("api.services.pay_item_sources.flag_rollup", return_value=list(flags)), \
         patch("api.services.pay_item_sources.segment_rollup", return_value=list(segments)):
        return admin_client.get(SOURCES + query)


class TestPayItemSources:
    def test_all_four_sources_sum_per_pay_item(self, admin_client):
        swcb = report(pay_items=[{"itemNo": "4.01 AAS ", "budgetCode": "HW-200(S)", "payQuantity": "20", "unit": "S.F."}])
        site_flag_item = {**IDR_ITEM, "pay_item_ref": "4.01 AAS", "quantity": Decimal("5"), "unit": "SF",
                          "description": "4in sidewalk"}
        response = sources(admin_client, [swcb], [site_flag_item, IDR_ITEM], [FLAG_LINE], [SEGMENT_LINE])
        assert response.status_code == 200
        items = {item["pay_item_ref"]: item for item in response.json()["data"]["pay_items"]}
        assert list(items) == ["4.01 AAS", "4.08 AA", "6.70 AB"]
        sidewalk = items["4.01 AAS"]
        assert (sidewalk["flags"], sidewalk["segments"], sidewalk["sites"], sidewalk["manual"], sidewalk["total"]) == \
            (100.0, 0.0, 5.0, 20.0, 125.0)
        assert sidewalk["unit"] == "SF"
        assert sidewalk["site_lines"] == [{"amount": 5.0, "report_id": str(REPORT_ID), "site_id": str(SITE_ID),
                                           "property_id": str(PROPERTY_ID), "label": "138-22 227 Street",
                                           "overrides_auto": False}]
        assert sidewalk["manual_lines"] == [{"amount": 20.0, "report_id": str(REPORT_ID), "site_id": None,
                                             "property_id": None, "label": None, "overrides_auto": None}]
        assert items["4.08 AA"]["total"] == 30.5 and items["4.08 AA"]["segments"] == 30.5
        assert items["6.70 AB"]["total"] == 12.5 and items["6.70 AB"]["sites"] == 12.5

    def test_no_sources_is_no_pay_items(self, admin_client):
        assert sources(admin_client, [report()], []).json()["data"]["pay_items"] == []

    def test_an_auto_general_and_other_reports_hand_items_are_left_out_for_one_report(self, admin_client):
        auto = report("GEN", OTHER_REPORT_ID, [{"itemNo": "9.99", "budgetCode": "X", "payQuantity": "1"}],
                      is_auto_generated=True)
        other = report("AC", OTHER_REPORT_ID, [{"itemNo": "9.98", "budgetCode": "X", "payQuantity": "1"}])
        assert sources(admin_client, [auto], []).json()["data"]["pay_items"] == []
        data = sources(admin_client, [report(), other], [], query=f"?report_id={REPORT_ID}").json()["data"]
        assert data["pay_items"] == [] and data["report_id"] == str(REPORT_ID)


class TestHandEntryCheck:
    def test_saving_a_report_with_a_pay_item_a_site_holds_is_400_naming_the_sites(self, admin_client):
        body = {"payItems": [{"itemNo": "6.70 AB", "budgetCode": "HW-200(S)", "payQuantity": "1"}]}
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]) as reports, \
             patch(PAY, return_value=[IDR_ITEM]):
            response = admin_client.put(f"/v1/idrs/{IDR_ID}/reports/{REPORT_ID}", json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == ("Pay item 6.70 AB (HW-200(S)) is entered on this IDR's imported site "
                                             "138-22 227 Street; remove it from the report, or from those sites")
        assert all("UPDATE" not in call.args[0] for call in reports.call_args_list)


class TestBilling:
    def test_the_quantity_rows_are_the_sites_pay_items_per_report_and_item(self):
        cte = " ".join(insert_site_quantities_cte("moved").split())
        assert "site_quantities AS ( INSERT INTO icid.quantities" in cte and "'from_sites'" in cte
        assert "r.report_type, ci.item_no, ci.budget_code, ci.description, sum(sp.quantity)" in cte
        assert "GROUP BY i.project_id, i.idr_id, i.report_date, i.reporter_uuid, r.report_type, ci.contract_item_id" in cte
        assert "FROM moved i JOIN icid.idr_report_sites rs ON rs.idr_id = i.idr_id" in cte

    def test_a_seeded_units_long_form_is_shortened(self):
        unit = " ".join(SITE_UNIT.split())
        assert "regexp_replace(ci.unit, '[.[:space:]]', '', 'g')" in unit
        assert "WHEN 'EACH' THEN 'EA' WHEN 'TONS' THEN 'TN' WHEN 'TON' THEN 'TN'" in unit

    def test_approval_and_undelete_write_them_and_unlock_and_delete_remove_them(self):
        with patch("api.queries.idrs._move_idr", return_value=[]) as move:
            approve_stage2(IDR_ID, ADMIN_ID, "sig.png", False, [], [], [])
            assert "site_quantities AS (" in move.call_args.kwargs["also"]
            admin_unlock_idr(IDR_ID, ADMIN_ID)
            # every quantity row of the IDR goes, its from_sites ones among them
            assert "DELETE FROM icid.quantities WHERE idr_id IN (SELECT idr_id FROM moved) )" in \
                " ".join(move.call_args.kwargs["also"].split())
        with patch("api.queries.idrs.run_query", return_value=[]) as run:
            undelete_idr(IDR_ID, ADMIN_ID, [])
        assert "site_quantities AS (" in run.call_args.args[0] and "FROM approved i JOIN icid.idr_report_sites" in \
            " ".join(run.call_args.args[0].split())


class TestExportLines:
    def test_a_reports_site_pay_items_print_summed_per_pay_item(self):
        items = [IDR_ITEM, {**IDR_ITEM, "quantity": Decimal("7.5")},
                 {**IDR_ITEM, "pay_item_ref": "4.01 AAS", "quantity": Decimal("100"), "unit": "S.F.", "description": "Walk"},
                 {**IDR_ITEM, "report_id": OTHER_REPORT_ID, "pay_item_ref": "9.99"}]
        assert site_export_lines(items, REPORT_ID) == [
            {"itemNo": "4.01 AAS", "budgetCode": "HW-200(S)", "payQuantity": "100", "unit": "SF", "description": "Walk"},
            {"itemNo": "6.70 AB", "budgetCode": "HW-200(S)", "payQuantity": "20", "unit": "LS",
             "description": "Maintenance and protection of traffic"},
        ]
        assert site_export_lines(items[3:], REPORT_ID) == []

    def test_the_export_adds_them_to_each_swcb_after_its_own(self):
        reports = [report(pay_items=[{"itemNo": "1"}]), report("GEN", OTHER_REPORT_ID)]
        with patch(PAY, return_value=[IDR_ITEM]):
            out = _with_site_lines(reports, IDR_ID)
        assert [item.get("itemNo") for item in out[0]["report_data"]["payItems"]] == ["1", "6.70 AB"]
        assert out[1] is reports[1]
        with patch(PAY) as run:
            assert _with_site_lines([reports[1]], IDR_ID) == [reports[1]]
        run.assert_not_called()
