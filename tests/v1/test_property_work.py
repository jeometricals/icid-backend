from decimal import Decimal
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

from tests.conftest import signed_in
from tests.v1.test_properties import INSPECTOR_ROW, OFF_PROJECT, PROJECTS, PROPERTY_ID, PROPERTY_ROW, PROPS

# ---------------------------------------------------------------------------
# /v1/properties/{property_id}/work-history (Slice V2): every import of a property into a report
# ---------------------------------------------------------------------------

SITES = "api.queries.idr_report_sites.run_query"
URL = f"/v1/properties/{PROPERTY_ID}/work-history"
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
ROW = {"id": UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c"), "idr_id": UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e"),
       "report_id": UUID("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"), "report_type": "SWCB", "work_type": "excavation",
       "work_type_other": None, "added_at": NOW, "idr_number": "123AB", "report_date": date(2026, 10, 19),
       "status": "approved", "deleted_at": None, "return_reason": None,
       "reporter_uuid": UUID("327d3ed2-a3d6-4235-9408-7fe721b12bed"), "reporter_name": "Genghis Khan"}


class TestWorkHistory:
    def test_every_import_is_listed_deleted_and_drafts_included(self, admin_client):
        deleted = {**ROW, "idr_number": None, "status": "deleted", "deleted_at": NOW, "work_type": "other",
                   "work_type_other": "Custom work spec"}
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(SITES, return_value=[ROW, deleted]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert [(e["work_type"], e["idr_number"], e["report_date"], e["reporter_name"]) for e in data] == [
            ("excavation", "123AB", "2026-10-19", "Genghis Khan"), ("other", None, "2026-10-19", "Genghis Khan")]
        assert data[1]["deleted_at"] == "2026-10-21T09:00:00Z" and data[1]["work_type_other"] == "Custom work spec"
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        assert "WHERE rs.property_id = %s ORDER BY i.report_date DESC, rs.added_at DESC" in one_line
        assert "deleted_at IS NULL" not in one_line  # deleted IDRs are listed too
        assert params == (PROPERTY_ID,)

    def test_a_user_off_the_project_is_403(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS, return_value=[PROPERTY_ROW]), \
             patch(PROJECTS, side_effect=OFF_PROJECT[1:]), patch(SITES) as run:
            response = inspector.get(URL)
        assert response.status_code == 403
        run.assert_not_called()

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]), patch(SITES) as run:
            assert admin_client.get(URL).status_code == 404
        run.assert_not_called()


# ---------------------------------------------------------------------------
# /v1/properties/{property_id}/pay-item-summary (Slice V5): the pay items IDRs report on a property
# ---------------------------------------------------------------------------

PAY = "api.queries.property_pay_items.run_query"
SUMMARY = f"/v1/properties/{PROPERTY_ID}/pay-item-summary"
SIDEWALK_ITEM = UUID("6b5c6d7e-8f9a-4b1c-9d2e-3f4a5b6c7d8e")
MTP_ITEM = UUID("7c6d7e8f-9a0b-4c1d-8e3f-4a5b6c7d8e9f")
APPROVED = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
DRAFT_A = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5f")
DRAFT_B = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d60")
FLAG_1, FLAG_2 = UUID("1f1f1f1f-1f1f-4f1f-8f1f-1f1f1f1f1f1f"), UUID("2f2f2f2f-2f2f-4f2f-8f2f-2f2f2f2f2f2f")


def line(idr_id: UUID, source: str, element: UUID, item: UUID, amount: str, **idr) -> dict:
    """
    Make a row as property_pay_item_lines returns it.
    Takes the IDR, source, element, contract item, amount and any IDR column.
    Returns the row dict.
    """
    ref = "4.13 AAS" if item == SIDEWALK_ITEM else "6.70 AB"
    return {"idr_id": idr_id, "source": source, "element_id": element, "contract_item_id": item, "item_no": ref,
            "budget_code": "HW-200(S)", "description": "Walk" if item == SIDEWALK_ITEM else "MPT",
            "unit": "SF" if item == SIDEWALK_ITEM else "LS", "amount": Decimal(amount),
            **{"idr_number": "IDR-001", "report_date": date(2026, 10, 19), "status": "approved",
               "reporter_name": "Genghis Khan", **idr}}


class TestPayItemSummary:
    def test_pay_items_are_totalled_with_each_idrs_part_newest_first(self, admin_client):
        rows = [
            line(DRAFT_A, "flags", FLAG_2, SIDEWALK_ITEM, "40", idr_number=None, report_date=date(2026, 10, 21),
                 status="draft", reporter_name="Jane Doe"),
            line(DRAFT_B, "flags", FLAG_2, SIDEWALK_ITEM, "40", idr_number="IDR-007", report_date=date(2026, 10, 20),
                 status="stage1_review", reporter_name="John Smith"),
            line(APPROVED, "flags", FLAG_1, SIDEWALK_ITEM, "100.005"),
            line(APPROVED, "sites", UUID(int=5), MTP_ITEM, "1"),
            line(APPROVED, "segments", UUID(int=6), SIDEWALK_ITEM, "0"),
        ]
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PAY, return_value=rows) as run:
            response = admin_client.get(SUMMARY)
        assert response.status_code == 200
        walk, mtp = response.json()["data"]
        # flag 2 is in two open IDRs: listed under each, counted once
        assert (walk["pay_item_ref"], walk["unit"], walk["total"]) == ("4.13 AAS", "SF", 140.01)
        assert [(e["idr_number"], e["report_date"], e["inspector"], e["quantity"]) for e in walk["idrs"]] == [
            (None, "2026-10-21", "Jane Doe", 40.0), ("IDR-007", "2026-10-20", "John Smith", 40.0),
            ("IDR-001", "2026-10-19", "Genghis Khan", 100.01)]
        assert (mtp["pay_item_ref"], mtp["total"], len(mtp["idrs"])) == ("6.70 AB", 1.0, 1)
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        assert one_line.count("UNION ALL") == 2 and "WHERE l.amount IS NOT NULL" in one_line
        assert "(i.status = 'approved' AND f.claimed_by_idr_id = i.idr_id)" in one_line
        assert "a.action IS DISTINCT FROM 'exclude' AND s.claimed_by_idr_id IS NULL" in one_line
        assert "WHERE rs.property_id = %s AND i.deleted_at IS NULL" in one_line
        assert params == (PROPERTY_ID,) * 3

    def test_nothing_reported_is_an_empty_list(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PAY, return_value=[]):
            assert admin_client.get(SUMMARY).json()["data"] == []

    def test_a_user_off_the_project_is_403(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS, return_value=[PROPERTY_ROW]), \
             patch(PROJECTS, side_effect=OFF_PROJECT[1:]), patch(PAY) as run:
            assert inspector.get(SUMMARY).status_code == 403
        run.assert_not_called()
