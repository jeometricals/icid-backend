from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/projects/{project_id}/ped-ramp-inspections and its bulk-download (Pedramp Phase 3, S5). Mock rows are dicts,
# as run_query returns them; Storage is patched in the bulk-download service.
# ---------------------------------------------------------------------------

LISTING = "api.queries.ped_ramp_listing.run_query"
PROJECTS = "api.queries.projects.run_query"
BULK = "api.v1.ped_ramp_listing.bulk_download"

URL = "/v1/projects/HWS0023/ped-ramp-inspections"
INSPECTION_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
RE_ID = UUID("b0000000-0000-4000-8000-000000000003")
APPROVED_AT = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PROJECT = [{"project_id": "HWS0023", "project_name": "Sidewalks"}]
ROW = {"total": 31, "inspection_id": INSPECTION_ID, "corner_id": CORNER_ID, "corner_name": "3rd Ave & 38 Street",
       "north_south_street": "3rd Ave", "east_west_street": "38 Street", "corner_direction": "NE", "ramp_count": 2,
       "status": "approved", "inspector_uuid": ADMIN_USER_ROW["uuid"], "inspector_name": "Ada Admin",
       "re_uuid": RE_ID, "re_name": "Rhea Engineer", "approved_at": APPROVED_AT, "tif_count": 1,
       "has_dot_pdf": True, "dot_pdf_path": f"inspections/{INSPECTION_ID}/x.zip"}
EMPTY_PAGE = {"total": 0, **{key: None for key in ROW if key != "total"}}


class TestList:
    def test_a_page_of_rows_with_the_total(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING, return_value=[ROW]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["total"], data["page"], data["page_size"]) == (31, 1, 30)
        (row,) = data["rows"]
        assert row["corner_name"] == "3rd Ave & 38 Street" and row["has_dot_pdf"] is True
        assert "dot_pdf_path" not in row and row["re_name"] == "Rhea Engineer"
        sql, params = run.call_args.args
        assert "DISTINCT ON (i.corner_id)" in sql and "pc.deleted_at IS NULL" in sql
        assert "ORDER BY approved_at DESC NULLS LAST, north_south_street, east_west_street" in sql
        assert params == ("HWS0023", 30, 0)

    def test_every_filter_reaches_the_statement(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING, return_value=[EMPTY_PAGE]) as run:
            response = admin_client.get(URL, params={
                "status": "approved", "inspector_uuid": str(ADMIN_USER_ROW["uuid"]), "re_uuid": str(RE_ID),
                "from": "2026-10-01", "to": "2026-10-31", "has_tif": "no", "q": " 50%_off ", "page": 3, "page_size": 10,
            })
        assert response.status_code == 200 and response.json()["data"] == {"total": 0, "page": 3, "page_size": 10, "rows": []}
        sql, params = run.call_args.args
        assert "(l.re_signed_at AT TIME ZONE 'America/New_York')::date >= %s" in sql
        assert "NOT EXISTS (SELECT 1 FROM icid.ped_ramp_tifs t WHERE t.inspection_id = l.id)" in sql
        assert "a.ramp_id_label ILIKE %s" in sql
        assert params == ("HWS0023", "approved", ADMIN_USER_ROW["uuid"], RE_ID, date(2026, 10, 1), date(2026, 10, 31),
                          "%50\\%\\_off%", "%50\\%\\_off%", "%50\\%\\_off%", 10, 20)

    def test_has_tif_yes(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING, return_value=[EMPTY_PAGE]) as run:
            admin_client.get(URL, params={"has_tif": "yes"})
        assert " EXISTS (SELECT 1 FROM icid.ped_ramp_tifs t" in run.call_args.args[0]
        assert "NOT EXISTS (SELECT 1 FROM icid.ped_ramp_tifs" not in run.call_args.args[0]

    def test_bad_dates_and_pages_are_400(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING) as run:
            assert admin_client.get(URL, params={"from": "10/01/2026"}).json() == {"detail": "from must be a date, YYYY-MM-DD"}
            assert admin_client.get(URL, params={"from": "2026-10-31", "to": "2026-10-01"}).json() == {"detail": "from cannot be after to"}
            assert admin_client.get(URL, params={"page_size": 101}).json() == {"detail": "page_size must be between 1 and 100"}
            assert admin_client.get(URL, params={"page": 0}).status_code == 400
        run.assert_not_called()

    def test_someone_off_the_project_is_403(self):
        with signed_in(MEMBER_ROW) as stranger, patch(PROJECTS, side_effect=[PROJECT, []]), patch(LISTING) as run:
            assert stranger.get(URL).status_code == 403
        run.assert_not_called()


class TestBulkDownload:
    BULK_URL = f"{URL}/bulk-download"
    LINK = {"url": "https://storage.example/bulk.zip", "expires_at": APPROVED_AT, "corner_count": 1,
            "file_name": "HWS0023_pedramp_inspections_2026-10-21.zip"}

    def test_only_approved_ones_with_a_pdf_are_zipped(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING, return_value=[ROW | {"total": 1}]) as run, \
             patch(BULK, return_value=self.LINK) as bulk:
            response = admin_client.post(self.BULK_URL, json={"from": "2026-10-01", "has_tif": "yes"})
        assert response.status_code == 200 and response.json()["data"]["corner_count"] == 1
        sql, params = run.call_args.args
        assert "l.status = 'approved' AND l.dot_pdf_path IS NOT NULL" in sql
        assert params == ("HWS0023", date(2026, 10, 1), 101, 0)
        project_id, rows = bulk.call_args.args
        assert project_id == "HWS0023" and rows[0]["dot_pdf_path"] == ROW["dot_pdf_path"]

    def test_more_than_100_is_400(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING, return_value=[ROW | {"total": 101}]), \
             patch(BULK) as bulk:
            response = admin_client.post(self.BULK_URL, json={})
        assert response.status_code == 400 and "narrow the filters" in response.json()["detail"]
        bulk.assert_not_called()

    def test_nothing_to_download_is_400(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT), patch(LISTING, return_value=[EMPTY_PAGE]), patch(BULK) as bulk:
            response = admin_client.post(self.BULK_URL, json={"status": "draft"})
        assert response.json() == {"detail": "No approved inspection with a DOT PDF matches these filters"}
        bulk.assert_not_called()

    def test_an_unknown_filter_is_422(self, admin_client):
        with patch(PROJECTS, return_value=PROJECT):
            assert admin_client.post(self.BULK_URL, json={"colour": "red"}).status_code == 422
