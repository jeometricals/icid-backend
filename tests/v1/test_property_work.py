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
