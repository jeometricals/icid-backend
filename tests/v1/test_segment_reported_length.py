from decimal import Decimal
from unittest.mock import patch

from api.queries.idr_report_sites import site_pay_item_keys
from api.queries.idr_segments import PROPERTY_SEGMENT_LINES, SEGMENT_AMOUNT, insert_segment_quantities_cte
from api.queries.progress import list_flag_progress
from api.queries.segments import EFFECTIVE_LENGTH
from api.services.idr_segments import _rollup as segment_lines
from api.services.pay_item_sources import site_auto_amount
from tests.v1.test_idr_report_sites import DRAFT_IDR, PROPERTY_ID
from tests.v1.test_reviews import flat
from tests.v1.test_segments import (
    BODY,
    PAGE_ROW,
    PAGES,
    SEGMENT_ROW,
    SEGMENTS,
    SEGMENTS_URL,
    URL,
)
from tests.v1.test_site_overrides import CONTRACT_ITEM_ID

# ---------------------------------------------------------------------------
# Slice X.2: a curb segment's reported length (reported_length_ft, migration 057), as a flag's reported quantity.
# Billing itself (one of two segments overridden, cleared, and with a site override on top) was run against the
# database in a rolled-back transaction; these hold the routes, the statements' shape and the Python around them.
# ---------------------------------------------------------------------------

OVERRIDDEN_ROW = {**SEGMENT_ROW, "reported_length_ft": Decimal("25"), "length_delta_reason": "field measure",
                  "effective_length_ft": Decimal("25"), "is_overridden": True}


def patched(client, body: dict, stored: dict = SEGMENT_ROW, written: dict = OVERRIDDEN_ROW):
    """
    PATCH a segment with its read and its write mocked.
    Takes the client, the body, the stored segment and what the write returns.
    Returns (the response, the mocked run_query).
    """
    with patch(SEGMENTS, side_effect=[[stored], [written]]) as run:
        return client.patch(URL, json=body), run


class TestSettingIt:
    def test_a_reported_length_and_its_reason_are_written_and_returned(self, admin_client):
        response, run = patched(admin_client, {"reported_length_ft": 25, "length_delta_reason": "field measure"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["reported_length_ft"] == 25 and data["effective_length_ft"] == 25 and data["is_overridden"] is True
        assert data["length_ft"] == 3.33  # the drawn length stands beside it
        params = run.call_args.args[1]
        assert params[4] == Decimal(25) and params[5] == "field measure"
        assert "reported_length_ft = %s::numeric, length_delta_reason = %s::text" in " ".join(run.call_args.args[0].split())

    def test_zero_is_allowed(self, admin_client):
        response, run = patched(admin_client, {"reported_length_ft": 0})
        assert response.status_code == 200 and run.call_args.args[1][4] == Decimal(0)

    def test_a_negative_length_is_422(self, admin_client):
        response, run = patched(admin_client, {"reported_length_ft": -1})
        assert response.status_code == 422 and run.call_count == 1

    def test_null_clears_the_override_and_its_reason(self, admin_client):
        response, run = patched(admin_client, {"reported_length_ft": None}, stored=OVERRIDDEN_ROW, written=SEGMENT_ROW)
        assert response.status_code == 200 and response.json()["data"]["is_overridden"] is False
        assert run.call_args.args[1][4] is None and run.call_args.args[1][5] is None

    def test_null_reason_alone_clears_only_the_reason(self, admin_client):
        response, run = patched(admin_client, {"length_delta_reason": None}, stored=OVERRIDDEN_ROW)
        assert response.status_code == 200
        assert run.call_args.args[1][4] == Decimal(25) and run.call_args.args[1][5] is None

    def test_a_new_segment_can_carry_one(self, admin_client):
        with patch(PAGES, return_value=[PAGE_ROW]), patch(SEGMENTS, return_value=[OVERRIDDEN_ROW]) as run:
            response = admin_client.post(SEGMENTS_URL, json={**BODY, "reported_length_ft": "25"})
        assert response.status_code == 201 and response.json()["data"]["is_overridden"] is True
        assert run.call_args.args[1][5] == Decimal(25)

    def test_a_segment_read_back_says_what_counts(self, admin_client):
        with patch(SEGMENTS, return_value=[SEGMENT_ROW]) as run:
            data = admin_client.get(URL).json()["data"]
        assert data["effective_length_ft"] == 3.33 and data["is_overridden"] is False
        sql = " ".join(run.call_args.args[0].split())
        assert "COALESCE(s.reported_length_ft, s.length_ft) AS effective_length_ft" in sql
        assert "(s.reported_length_ft IS NOT NULL) AS is_overridden" in sql


class TestWhatItBills:
    def test_one_expression_measures_a_segment_everywhere(self):
        assert EFFECTIVE_LENGTH == "COALESCE(s.reported_length_ft, s.length_ft)"
        assert EFFECTIVE_LENGTH in SEGMENT_AMOUNT
        # Approval's from_segments rows, Pay Items Reported and Progress all read SEGMENT_AMOUNT
        assert EFFECTIVE_LENGTH in flat(insert_segment_quantities_cte("moved"))
        assert EFFECTIVE_LENGTH in flat(PROPERTY_SEGMENT_LINES)
        with patch("api.queries.progress.run_query", return_value=[]) as run:
            list_flag_progress("HWS0023", None)
        assert EFFECTIVE_LENGTH in run.call_args.args[0]

    def test_a_segment_with_only_a_reported_length_fills_its_pay_item_on_import(self):
        with patch("api.queries.idr_report_sites.run_query", return_value=[]) as run:
            site_pay_item_keys(PROPERTY_ID)
        assert "COALESCE(s.reported_length_ft, s.length_ft) IS NOT NULL" in run.call_args.args[0]

    def test_one_of_two_segments_overridden_adds_its_reported_length_to_the_others_drawn(self):
        # amount is SEGMENT_AMOUNT as the listing reads it: 25 reported on the first, 12 drawn on the second
        segment = {"pay_item_id": CONTRACT_ITEM_ID, "pay_item_ref": "4.08 AA", "budget_code": "B",
                   "pay_item_description": "Curb", "unit": "LF", "state": "attributed", "overridden": False,
                   "property_id": PROPERTY_ID}
        rows = [{**segment, "amount": Decimal(25)}, {**segment, "amount": Decimal(12)}]
        assert [line["amount"] for line in segment_lines(rows)] == [Decimal(37)]
        with patch("api.services.pay_item_sources.list_idr_flags", return_value=[]), \
             patch("api.services.pay_item_sources.list_idr_segments", return_value=rows):
            assert site_auto_amount(DRAFT_IDR, PROPERTY_ID, CONTRACT_ITEM_ID) == (Decimal("37.00"), "LF")

    def test_a_site_override_still_wins_over_a_segments_reported_length(self):
        # A site's override of the pay item marks its segments overridden, whatever length they report: they add
        # nothing to the rollup or to approval's from_segments rows, and the override bills instead
        segment = {"pay_item_id": CONTRACT_ITEM_ID, "pay_item_ref": "4.08 AA", "budget_code": "B",
                   "pay_item_description": "Curb", "unit": "LF", "state": "attributed", "overridden": True,
                   "amount": Decimal(25)}
        assert segment_lines([segment]) == []
        cte = flat(insert_segment_quantities_cte("moved"))
        assert f"WHERE {flat(SEGMENT_AMOUNT)} IS NOT NULL AND NOT" in cte and "osp.overrides_auto" in cte
