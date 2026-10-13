import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
from psycopg.errors import UniqueViolation

from api.queries.idrs import IdrSlotTakenError, permanent_delete_idr, undelete_idr
from api.queries.report_attachments import list_storage_paths_for_idr
from api.services import auth
from api.services.idr_purge import purge_idr
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/admin/undelete and DELETE /v1/idrs/{idr_id}/admin/permanent
# Mock rows are dicts, as run_query returns them. Storage is patched where api.services.idr_purge reaches it.
# ---------------------------------------------------------------------------

IDR_ID = "9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e"
OTHER_IDR_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
UNDELETE = f"/v1/idrs/{IDR_ID}/admin/undelete"
PERMANENT = f"/v1/idrs/{IDR_ID}/admin/permanent"
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
CRON_SECRET = "cron-secret-for-tests-only"
CRON = {"Authorization": f"Bearer {CRON_SECRET}"}
INSPECTOR = UUID("c0000000-0000-4000-8000-000000000003")
REVIEWER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("f0000000-0000-4000-8000-000000000006"), "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})

LIVE_IDR = {
    "idr_id": UUID(IDR_ID), "project_id": "HWS0023", "reporter_uuid": INSPECTOR, "report_date": date(2026, 10, 5),
    "status": "approved", "idr_number": "005", "total_pages": 2, "created_at": NOW, "updated_at": NOW,
    "submitted_at": NOW, "inspector_signature_path": f"idrs/{IDR_ID}/inspector_aa.png", "inspector_signed_at": NOW,
    "re_signature_path": f"idrs/{IDR_ID}/re_bb.png", "re_signed_at": NOW, "re_reviewer_uuid": REVIEWER_ROW["uuid"],
    "deleted_at": None, "deleted_by": None,
}
DELETED_IDR = {**LIVE_IDR, "status": "deleted", "deleted_at": NOW, "deleted_by": ADMIN_USER_ROW["uuid"]}
RESTORED = {**LIVE_IDR, "status_on_record": True}
SWCB = UUID("e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091")
PAY_REPORT = {"report_id": SWCB, "idr_id": UUID(IDR_ID), "report_type": "SWCB", "is_auto_generated": False,
              "report_data": {"payItems": [{"id": "a", "itemNo": "4.13 AAS", "budgetCode": "12345",
                                            "payQuantity": "60.00", "unit": "S.F.", "description": "Sidewalk"}]}}


def flat(sql: str) -> str:
    """
    Put a statement on one line.
    Takes the SQL.
    Returns it with every run of whitespace as one space.
    """
    return " ".join(sql.split())


@contextmanager
def backend(idr=DELETED_IDR, restored="same", day_holder=None, number_holder=None, reports=(), gone=True,
            attachments=(), signatures=(), exports=()):
    """
    Patch what the two routes reach: the IDR queries, the IDR's reports and attachment rows, and Storage.
    Takes the IDR row the read returns (None: no such IDR), what the un-delete statement returns ("same": the restored IDR; or [] / None / an exception to raise), the uuid of an IDR now holding the deleted one's day or its number, the IDR's report rows, whether the permanent delete finds a row, its attachment paths, and the files in its signature and export folders.
    Yields a dict: "statements" is every (sql, params) run in api.queries.idrs, "removed" the (bucket, path) of each file removed from Storage, "listed" the (bucket, folder) of each listing.
    """
    seen = {"statements": [], "removed": [], "listed": []}
    folders = {("signatures", f"idrs/{IDR_ID}"): list(signatures), ("idr-exports", IDR_ID): list(exports)}

    def idrs_query(sql, params=None):
        """Stand in for run_query in api.queries.idrs."""
        seen["statements"].append((sql, params))
        if "UPDATE icid.idrs" in sql:
            if isinstance(restored, Exception):
                raise restored
            return [RESTORED] if restored == "same" else restored
        if "DELETE FROM icid.idrs" in sql:
            return [{"idr_id": UUID(IDR_ID)}] if gone else []
        if "reporter_uuid = %s AND report_date = %s" in sql:
            return [{"idr_id": UUID(day_holder)}] if day_holder else []
        if "idr_number = %s" in sql:
            return [{"idr_id": UUID(number_holder)}] if number_holder else []
        return [idr] if idr else []

    def remove(paths, bucket="report-attachments"):
        """Stand in for the Storage client's remove_files."""
        seen["removed"] += [(bucket, path) for path in paths]
        folders.update({key: [p for p in files if p not in paths] for key, files in folders.items()})

    def listing(bucket, folder):
        """Stand in for the Storage client's list_file_paths."""
        seen["listed"].append((bucket, folder))
        return list(folders.get((bucket, folder), []))

    with patch("api.queries.idrs.run_query", side_effect=idrs_query), \
         patch("api.queries.idr_reports.run_query", return_value=list(reports)), \
         patch("api.queries.report_attachments.run_query",
               return_value=[{"storage_path": path} for path in attachments]), \
         patch("api.services.attachments.remove_files", side_effect=remove), \
         patch("api.services.idr_purge.list_file_paths", side_effect=listing):
        yield seen


def writes(seen: dict) -> list[tuple]:
    """
    Pick the statements that change icid.idrs out of what a test saw.
    Takes the dict backend() yielded.
    Returns their (sql, params).
    """
    return [(sql, params) for sql, params in seen["statements"] if "UPDATE icid.idrs" in sql or "DELETE FROM" in sql]


@pytest.fixture
def cron_secret(monkeypatch):
    """
    Give the app a CRON_SECRET for one test.
    """
    monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)


class TestUndelete:
    def test_a_deleted_idr_comes_back_with_the_status_it_had(self, admin_client):
        with backend() as seen:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["message"]) == ("success", "IDR restored")
        assert (body["data"]["status"], body["data"]["deleted_at"], body["data"]["deleted_by"]) == (
            "approved", None, None)
        assert body["data"]["idr_number"] == "005" and "status_on_record" not in body["data"]
        assert len(writes(seen)) == 1

    def test_the_status_is_read_off_the_deletes_own_audit_row_in_one_statement(self, admin_client):
        with backend() as seen:
            admin_client.post(UNDELETE)
        sql, params = writes(seen)[0]
        one_line = flat(sql)
        assert sql.count(";") == 1
        assert "FROM icid.idr_audit a WHERE a.idr_id = i.idr_id AND a.action = 'admin_delete'" in one_line
        assert "ORDER BY a.created_at DESC LIMIT 1" in one_line
        assert "WHERE i.idr_id = %s AND i.deleted_at IS NOT NULL FOR UPDATE OF i" in one_line  # only a deleted IDR
        assert ("SET status = COALESCE(t.status_before, 'draft'), deleted_at = NULL, deleted_by = NULL, "
                "updated_at = now()") in one_line
        assert "INSERT INTO icid.idr_audit" in sql and "t.from_status, m.status" in sql
        assert params[:4] == (UUID(IDR_ID), ADMIN_USER_ROW["uuid"], "admin_undelete", None)

    @pytest.mark.parametrize("status", ["draft", "submitted", "stage1_review", "stage2_review", "approved"])
    def test_whatever_status_the_statement_restores_is_returned(self, admin_client, status):
        with backend(restored=[{**RESTORED, "status": status}]):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 200 and response.json()["data"]["status"] == status

    def test_an_idr_with_no_record_of_its_status_comes_back_as_a_draft_and_says_so(self, admin_client):
        with backend(restored=[{**RESTORED, "status": "draft", "status_on_record": False}]):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 200 and response.json()["data"]["status"] == "draft"
        assert response.json()["message"] == (
            "IDR restored as a draft: the status it had when it was deleted is not on record")

    def test_an_idr_that_comes_back_approved_gets_its_quantities_in_the_same_statement(self, admin_client):
        with backend(reports=[PAY_REPORT]) as seen:
            admin_client.post(UNDELETE)
        sql, params = writes(seen)[0]
        one_line = flat(sql)
        assert "approved AS ( SELECT idr_id FROM moved WHERE status = 'approved' )" in one_line
        assert "DELETE FROM icid.quantities WHERE idr_id IN (SELECT idr_id FROM approved)" in one_line
        assert "INSERT INTO icid.quantities (" in sql and "CROSS JOIN approved i" in sql
        rows = params[4].obj
        assert len(rows) == 1
        assert (rows[0]["pay_item_ref"], rows[0]["budget_code"], rows[0]["unit"], rows[0]["report_type"]) == (
            "4.13 AAS", "12345", "SF", "SWCB")
        assert float(rows[0]["amount"]) == 60.0

    def test_an_idr_that_isnt_deleted_is_404(self, admin_client):
        with backend(idr=LIVE_IDR) as seen:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 404 and response.json() == {"detail": "IDR is not deleted"}
        assert writes(seen) == []

    def test_an_idr_that_doesnt_exist_or_was_permanently_deleted_is_404(self, admin_client):
        with backend(idr=None) as seen:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}
        assert writes(seen) == []

    def test_an_idr_restored_by_someone_else_meanwhile_is_404(self, admin_client):
        with backend(restored=[]):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 404 and response.json() == {"detail": "IDR is not deleted"}

    def test_another_idr_on_its_day_is_409_naming_it(self, admin_client):
        with backend(day_holder=OTHER_IDR_ID) as seen:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 409
        assert response.json() == {"detail": "Another IDR now exists for this inspector, project and date",
                                   "existing_idr_id": OTHER_IDR_ID}
        assert writes(seen) == []
        lookup = next(params for sql, params in seen["statements"] if "report_date = %s" in sql)
        assert lookup == ("HWS0023", INSPECTOR, date(2026, 10, 5))

    def test_another_idr_with_its_number_is_409_naming_it(self, admin_client):
        with backend(number_holder=OTHER_IDR_ID) as seen:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 409
        assert response.json() == {"detail": "This IDR's number is now in use on this project",
                                   "existing_idr_id": OTHER_IDR_ID}
        assert writes(seen) == []

    def test_an_idr_without_a_number_isnt_looked_up_by_number(self, admin_client):
        with backend(idr={**DELETED_IDR, "idr_number": None}) as seen:
            assert admin_client.post(UNDELETE).status_code == 200
        assert not any("idr_number = %s" in sql for sql, _ in seen["statements"])

    def test_losing_its_day_or_number_between_the_check_and_the_statement_is_409(self, admin_client):
        with backend(restored=UniqueViolation("uq_idrs_project_number")):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 409
        assert "meanwhile" in response.json()["detail"]

    def test_a_failed_statement_is_500(self, admin_client):
        with backend(restored=None):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to restore IDR"}

    @pytest.mark.parametrize("row", [REVIEWER_ROW, {**ADMIN_USER_ROW, "uuid": INSPECTOR, "role": None}])
    def test_a_user_who_isnt_an_admin_is_forbidden(self, row):
        with signed_in(row) as client, backend() as seen:
            response = client.post(UNDELETE)
        assert (response.status_code, response.json()) == FORBIDDEN
        assert writes(seen) == []

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with backend() as seen:
            assert client.post(UNDELETE, headers=CRON).status_code == 401
        assert seen["statements"] == []


class TestPermanentDelete:
    def test_a_soft_deleted_idr_is_deleted_for_good(self, admin_client):
        with backend() as seen:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 204 and response.content == b""
        (sql, params), = writes(seen)
        assert flat(sql) == "DELETE FROM icid.idrs WHERE idr_id = %s AND deleted_at IS NOT NULL RETURNING idr_id;"
        assert params == (UUID(IDR_ID),)

    def test_its_files_go_from_all_three_buckets_after_the_row(self, admin_client):
        attachments = [f"{SWCB}/a1/photo.jpg", f"{SWCB}/a2/plan.pdf"]
        signatures = [f"idrs/{IDR_ID}/inspector_aa.png", f"idrs/{IDR_ID}/re_bb.png", f"idrs/{IDR_ID}/re_old.png"]
        exports = [f"{IDR_ID}/20261006_140000_IDR.xlsx"]
        with backend(attachments=attachments, signatures=signatures, exports=exports) as seen:
            assert admin_client.delete(PERMANENT).status_code == 204
        assert seen["removed"] == (
            [("report-attachments", path) for path in attachments]
            + [("signatures", path) for path in signatures] + [("idr-exports", path) for path in exports])
        # Only the IDR's own folders are listed: never another IDR's, never a user's signature
        assert set(seen["listed"]) == {("signatures", f"idrs/{IDR_ID}"), ("idr-exports", IDR_ID)}

    def test_who_deleted_it_is_logged(self, admin_client, caplog):
        with backend(), caplog.at_level(logging.INFO, logger="api.v1.reviews"):
            admin_client.delete(PERMANENT)
        line = next(record.getMessage() for record in caplog.records if "permanently deleted" in record.getMessage())
        assert f"idr_id={IDR_ID}" in line and "idr_number=005" in line and "project_id=HWS0023" in line
        assert f"admin_user_uuid={ADMIN_USER_ROW['uuid']}" in line and "at=" in line

    def test_an_idr_that_isnt_soft_deleted_is_404_and_nothing_is_touched(self, admin_client):
        with backend(idr=LIVE_IDR, attachments=["x/y/z.jpg"]) as seen:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 404 and response.json() == {"detail": "IDR must be soft-deleted first"}
        assert writes(seen) == [] and seen["removed"] == [] and seen["listed"] == []

    def test_an_idr_restored_between_the_read_and_the_delete_keeps_its_files(self, admin_client):
        with backend(gone=False, attachments=["x/y/z.jpg"], signatures=[f"idrs/{IDR_ID}/re_bb.png"]) as seen:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 404 and response.json() == {"detail": "IDR must be soft-deleted first"}
        assert seen["removed"] == [] and seen["listed"] == []

    def test_an_idr_that_doesnt_exist_is_404(self, admin_client):
        with backend(idr=None) as seen:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}
        assert writes(seen) == []

    def test_a_storage_failure_doesnt_fail_the_delete(self, admin_client):
        with backend(attachments=["x/y/z.jpg"]), \
             patch("api.services.attachments.remove_files", side_effect=RuntimeError("storage down")), \
             patch("api.services.idr_purge.list_file_paths", side_effect=RuntimeError("storage down")):
            assert admin_client.delete(PERMANENT).status_code == 204

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(REVIEWER_ROW) as client, backend() as seen:
            response = client.delete(PERMANENT)
        assert (response.status_code, response.json()) == FORBIDDEN
        assert writes(seen) == [] and seen["removed"] == []

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with backend() as seen:
            assert client.delete(PERMANENT, headers=CRON).status_code == 401
        assert seen["statements"] == [] and seen["removed"] == []


class TestDeleteQueriesAndPurge:
    def test_undelete_returns_the_rows_and_turns_a_unique_violation_into_a_slot_error(self):
        with patch("api.queries.idrs.run_query", return_value=[RESTORED]):
            assert undelete_idr(UUID(IDR_ID), ADMIN_USER_ROW["uuid"], []) == [RESTORED]
        with patch("api.queries.idrs.run_query", side_effect=UniqueViolation("taken")):
            with pytest.raises(IdrSlotTakenError):
                undelete_idr(UUID(IDR_ID), ADMIN_USER_ROW["uuid"], [])

    def test_permanent_delete_says_whether_a_row_went(self):
        with patch("api.queries.idrs.run_query", return_value=[{"idr_id": UUID(IDR_ID)}]):
            assert permanent_delete_idr(UUID(IDR_ID)) is True
        with patch("api.queries.idrs.run_query", return_value=[]):
            assert permanent_delete_idr(UUID(IDR_ID)) is False

    def test_the_attachment_paths_are_those_of_the_idrs_reports(self):
        with patch("api.queries.report_attachments.run_query", return_value=[]) as run:
            assert list_storage_paths_for_idr(UUID(IDR_ID)) == []
        sql, params = run.call_args.args
        assert "FROM icid.report_attachments a" in sql and "JOIN icid.idr_reports r ON r.report_id = a.report_id" in sql
        assert "WHERE r.idr_id = %s" in sql and params == (UUID(IDR_ID),)

    def test_purge_empties_a_folder_that_takes_more_than_one_listing(self):
        first, second = [f"{IDR_ID}/a.xlsx", f"{IDR_ID}/b.xlsx"], [f"{IDR_ID}/c.xlsx"]
        listings = iter([[], first, second, []])  # the signatures folder, then the exports folder three times
        with backend() as seen, patch("api.services.idr_purge.list_file_paths", side_effect=lambda *_: next(listings)):
            assert purge_idr(UUID(IDR_ID)) is True
        assert seen["removed"] == [("idr-exports", path) for path in first + second]

    def test_purge_stops_when_a_folders_files_wont_go(self):
        stuck = [f"{IDR_ID}/a.xlsx"]
        with backend(), patch("api.services.idr_purge.list_file_paths", return_value=stuck) as listing, \
             patch("api.services.attachments.remove_files", side_effect=RuntimeError("refused")):
            assert purge_idr(UUID(IDR_ID)) is True
        assert listing.call_count == 4  # each folder: listed, tried, listed again, given up
