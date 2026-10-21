from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from psycopg.errors import UniqueViolation

from api.queries.idr_flags import PROPERTY_FLAG_LINES, list_idr_flags
from api.queries.idr_report_site_pay_items import insert_site_quantities_cte
from api.queries.idr_segments import PROPERTY_SEGMENT_LINES, insert_segment_quantities_cte
from api.queries.property_pay_items import property_pay_item_lines
from api.queries.quantities import insert_flag_quantities_cte
from api.services.idr_flags import _rollup as flag_lines
from api.services.idr_segments import _rollup as segment_lines
from api.services.pay_item_sources import override_text, pay_item_sources, site_auto_amount, site_export_lines
from tests.v1.test_idr_report_site_pay_items import (
    CONTRACT_ITEM_ID,
    IDR_ITEM,
    ITEM_ID,
    PAY,
    PAY_ITEM,
    URL,
    pay_db,
    writes,
)
from tests.v1.test_idr_report_sites import (
    ADMIN_ID,
    DRAFT_IDR,
    IDRS,
    NOW,
    PROPERTY_ID,
    REPORT_ID,
    REPORTS,
    SIDEWALK_SITE,
    SITE_ID,
    SITES,
    report,
)
from tests.v1.test_reviews import APPROVED_IDR, approve, flat

# ---------------------------------------------------------------------------
# Slice X.1: a site's override of what its flags and curb segments come to for a pay item in an IDR (overrides_auto,
# migration 056). Billing itself (up, down, Auto Calculate, two IDRs on one site) was run against the database in a
# rolled-back transaction; these hold the statements' shape and the Python around them.
# ---------------------------------------------------------------------------

AUTO = "api.v1.idr_report_site_pay_items.site_auto_amount"
OVERRIDE = {**PAY_ITEM, "quantity": Decimal(185), "overrides_auto": True, "pay_item_ref": "4.13 AAS", "unit": "SF"}


def override_call(client, method: str, path: str = "", json: dict = None, rows: list = (), written: list = None,
                  auto=(Decimal("180.00"), "SF")):
    """
    Call a site pay-item route on a draft with its site's pay items and the automatic amount mocked.
    Takes the client, the method, the path after the pay-items URL, the body, the site's pay items, what the write
    returns and what the site's flags and segments come to (None for nothing).
    Returns (the response, the mocked run_query).
    """
    with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
         patch(SITES, return_value=[SIDEWALK_SITE]), patch(AUTO, return_value=auto), \
         patch(PAY, side_effect=pay_db(list(rows), written=written)) as run:
        response = getattr(client, method)(URL + path, **({"json": json} if json is not None else {}))
    return response, run


class TestSetting:
    def test_an_override_is_written_with_its_audit_words(self, admin_client):
        response, run = override_call(admin_client, "post", json={
            "contract_item_id": str(CONTRACT_ITEM_ID), "quantity": "185", "overrides_auto": True}, written=[OVERRIDE])
        assert response.status_code == 201 and response.json()["data"]["overrides_auto"] is True
        assert response.json()["message"] == "Override set"
        [(sql, params)] = writes(run)
        assert "overrides_auto)" in sql and "INSERT INTO icid.idr_audit" in sql and "WHERE sp.overrides_auto" in sql
        assert params == (Decimal(185), None, ADMIN_ID, True, REPORT_ID, SITE_ID, CONTRACT_ITEM_ID, ADMIN_ID,
                          "site_override_set", "overrode auto 180 SF → 185 SF", "overrode auto 180 SF → 185 SF")

    def test_an_override_may_be_lower_or_zero(self, admin_client):
        for quantity, words in (("160", "overrode auto 180 SF → 160 SF"), (0, "overrode auto 180 SF → 0 SF")):
            response, run = override_call(admin_client, "post", json={
                "contract_item_id": str(CONTRACT_ITEM_ID), "quantity": quantity, "overrides_auto": True},
                written=[OVERRIDE])
            assert response.status_code == 201 and writes(run)[0][1][-1] == words

    def test_zero_is_only_for_an_override(self, admin_client):
        response, run = override_call(admin_client, "post", json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 0})
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_negative_override_is_422(self, admin_client):
        response, run = override_call(admin_client, "post", json={
            "contract_item_id": str(CONTRACT_ITEM_ID), "quantity": -1, "overrides_auto": True})
        assert response.status_code == 422
        run.assert_not_called()

    def test_nothing_to_override_is_400(self, admin_client):
        response, run = override_call(admin_client, "post", json={
            "contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 5, "overrides_auto": True}, auto=None)
        assert response.status_code == 400 and "No flags or curb segments on this site fill that pay item" in \
            response.json()["detail"]
        assert writes(run) == []

    def test_a_second_override_of_a_pay_item_is_409(self, admin_client):
        def taken(sql, params):
            raise UniqueViolation("uq_idr_report_site_pay_items_one_override")
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(AUTO, return_value=(Decimal(180), "SF")), \
             patch(PAY, side_effect=taken):
            response = admin_client.post(URL, json={"contract_item_id": str(CONTRACT_ITEM_ID), "quantity": 185,
                                                    "overrides_auto": True})
        assert response.status_code == 409 and "already overrides" in response.json()["detail"]


class TestChangingAndResetting:
    def test_changing_an_override_logs_the_new_quantity(self, admin_client):
        response, run = override_call(admin_client, "patch", f"/{ITEM_ID}", {"quantity": 190}, rows=[OVERRIDE],
                                      written=[{**OVERRIDE, "quantity": Decimal(190)}])
        assert response.status_code == 200
        [(sql, params)] = writes(run)
        assert params[-4:] == (ADMIN_ID, "site_override_change", "overrode auto 180 SF → 190 SF",
                               "overrode auto 180 SF → 190 SF")

    def test_an_override_can_go_to_zero_an_ordinary_pay_item_cant(self, admin_client):
        response, _ = override_call(admin_client, "patch", f"/{ITEM_ID}", {"quantity": 0}, rows=[OVERRIDE],
                                    written=[{**OVERRIDE, "quantity": Decimal(0)}])
        assert response.status_code == 200
        response, run = override_call(admin_client, "patch", f"/{ITEM_ID}", {"quantity": 0}, rows=[PAY_ITEM])
        assert response.status_code == 400 and writes(run) == []

    def test_a_note_alone_on_an_override_logs_nothing(self, admin_client):
        _, run = override_call(admin_client, "patch", f"/{ITEM_ID}", {"note": "checked"}, rows=[OVERRIDE],
                               written=[OVERRIDE])
        assert writes(run)[0][1][-2:] == (None, None)

    def test_removing_an_override_resets_to_auto_and_logs_it(self, admin_client):
        response, run = override_call(admin_client, "delete", f"/{ITEM_ID}", rows=[OVERRIDE], written=[{"id": ITEM_ID}])
        assert response.status_code == 204
        [(sql, params)] = writes(run)
        assert "DELETE FROM icid.idr_report_site_pay_items" in sql
        assert params == (REPORT_ID, SITE_ID, ITEM_ID, ADMIN_ID, "site_override_reset", "reset to auto (180 SF)",
                          "reset to auto (180 SF)")

    def test_the_list_carries_the_latest_override_change_per_pay_item(self, admin_client):
        audit = [{"contract_item_id": CONTRACT_ITEM_ID, "action": "site_override_set",
                  "text": "overrode auto 180 SF → 185 SF", "at": NOW, "actor_name": "Jane Doe"}]
        with patch(IDRS, return_value=[DRAFT_IDR]), patch(REPORTS, return_value=[report()]), \
             patch(SITES, return_value=[SIDEWALK_SITE]), patch(PAY, side_effect=pay_db([OVERRIDE], audit)) as run:
            response = admin_client.get(URL)
        [entry] = response.json()["audit"]
        assert (entry["contract_item_id"], entry["action"], entry["text"], entry["actor_name"]) == \
            (str(CONTRACT_ITEM_ID), "site_override_set", "overrode auto 180 SF → 185 SF", "Jane Doe")
        audit_sql, audit_params = run.call_args_list[1].args
        assert "DISTINCT ON (n.contract_item_id)" in audit_sql and audit_params[2] == str(SITE_ID)


class TestWords:
    def test_set_change_and_reset(self):
        auto = (Decimal("180.00"), "SF")
        assert override_text(auto, Decimal("185")) == "overrode auto 180 SF → 185 SF"
        assert override_text((Decimal("20.50"), "LF"), Decimal("18.25")) == "overrode auto 20.5 LF → 18.25 LF"
        assert override_text(auto, None) == "reset to auto (180 SF)"

    def test_the_auto_amount_is_the_sites_counting_drawings_of_that_pay_item(self):
        other_item = UUID("c1000000-0000-4000-8000-000000000001")
        flag = {"property_id": PROPERTY_ID, "pay_item_id": CONTRACT_ITEM_ID, "state": "attributed", "amount": 100, "unit": "SF"}
        flags = [flag, {**flag, "amount": 80}, {**flag, "state": "excluded"}, {**flag, "state": "claimed_elsewhere"},
                 {**flag, "amount": None}, {**flag, "pay_item_id": other_item}, {**flag, "property_id": SITE_ID}]
        with patch("api.services.pay_item_sources.list_idr_flags", return_value=flags), \
             patch("api.services.pay_item_sources.list_idr_segments", return_value=[]):
            assert site_auto_amount(DRAFT_IDR, PROPERTY_ID, CONTRACT_ITEM_ID) == (Decimal("180.00"), "SF")
            assert site_auto_amount(DRAFT_IDR, PROPERTY_ID, UUID(int=1)) is None


class TestTheDrawingsAddNothing:
    def test_an_overridden_flag_or_segment_is_left_out_of_the_rollup(self):
        flag = {"pay_item_id": CONTRACT_ITEM_ID, "pay_item_ref": "4.13 AAS", "budget_code": "B", "pay_item_description": "d",
                "unit": "SF", "state": "attributed", "amount": 100, "overridden": False}
        assert [line["amount"] for line in flag_lines([flag, {**flag, "amount": 80, "overridden": True}])] == [Decimal(100)]
        segment = {**flag, "unit": "LF", "amount": 20}
        assert segment_lines([{**segment, "overridden": True}]) == []

    def test_the_listing_marks_them(self):
        with patch("api.queries.idr_flags.run_query", return_value=[]) as run:
            list_idr_flags(DRAFT_IDR["idr_id"], False)
        sql = flat(run.call_args.args[0])
        assert "osp.overrides_auto AND ors.idr_id = i.idr_id AND ors.property_id = p.id AND osp.contract_item_id = f.pay_item_id" in sql
        assert sql.count("AS overridden") == 1

    def test_approval_adds_nothing_for_them_and_bills_the_override_only_with_drawings_of_its_own(self):
        assert "ors.property_id = fpir.property_id AND osp.contract_item_id = f.pay_item_id" in flat(insert_flag_quantities_cte("moved"))
        assert "ors.property_id = pir.property_id AND osp.contract_item_id = s.pay_item_id" in flat(insert_segment_quantities_cte("moved"))
        sites = flat(insert_site_quantities_cte("moved"))
        assert "WHERE NOT sp.overrides_auto OR (EXISTS ( SELECT 1 FROM icid.flags df" in sites
        assert "df.id IN (SELECT id FROM claimed_flags)" in sites and "ds.segment_id IN (SELECT segment_id FROM claimed_segments)" in sites

    def test_pay_items_reported_leaves_them_out_and_a_dropped_override_too(self):
        assert "AND NOT EXISTS ( SELECT 1 FROM icid.idr_report_site_pay_items osp" in flat(PROPERTY_FLAG_LINES)
        assert "AND NOT EXISTS ( SELECT 1 FROM icid.idr_report_site_pay_items osp" in flat(PROPERTY_SEGMENT_LINES)
        with patch("api.queries.property_pay_items.run_query", return_value=[]) as run:
            property_pay_item_lines(PROPERTY_ID)
        assert "AND NOT (sp.overrides_auto AND i.status = 'approved' AND NOT" in flat(run.call_args.args[0])


class TestDroppedAtApproval:
    def test_approval_logs_dropped_overrides_and_says_so(self):
        response, moves = approve(moved=[{**APPROVED_IDR, "flags_claimed": 0, "overrides_dropped": 1}])
        assert response.status_code == 200
        one_line = flat(moves[0][0])
        assert "dropped_overrides AS ( INSERT INTO icid.idr_audit" in one_line
        assert "'override dropped at approval ('" in one_line and "' already claimed by '" in one_line
        assert "(SELECT count(*) FROM dropped_overrides) AS overrides_dropped" in one_line
        body = response.json()
        assert body["overrides_dropped"] == 1
        assert body["message"] == "IDR approved; 1 site override was dropped, its flags or curb segments already claimed"

    def test_nothing_dropped_says_nothing(self):
        response, _ = approve(moved=[{**APPROVED_IDR, "flags_claimed": 0, "overrides_dropped": 0}])
        assert response.json()["message"] == "IDR approved" and response.json()["overrides_dropped"] == 0

    def test_a_dropped_override_is_left_out_of_the_sources_and_the_export(self):
        dropped = {**IDR_ITEM, "overrides_auto": True, "override_dropped": True}
        with patch("api.services.pay_item_sources.flag_rollup", return_value=[]), \
             patch("api.services.pay_item_sources.segment_rollup", return_value=[]):
            assert pay_item_sources(DRAFT_IDR, [], [dropped])["pay_items"] == []
        assert site_export_lines([dropped], REPORT_ID) == []
        kept = {**dropped, "override_dropped": False}
        with patch("api.services.pay_item_sources.flag_rollup", return_value=[]), \
             patch("api.services.pay_item_sources.segment_rollup", return_value=[]):
            [item] = pay_item_sources(DRAFT_IDR, [], [kept])["pay_items"]
        assert item["sites"] == 12.5 and item["site_lines"][0]["overrides_auto"] is True
