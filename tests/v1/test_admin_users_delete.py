import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import bcrypt
import pytest
from fastapi import HTTPException
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from api.queries.users import (
    count_user_references, get_all_users, get_user_by_email, list_users_for_admin, permanent_delete_user,
    soft_delete_user, undelete_user,
)
from api.services import auth
from api.services.auth import auth_provider, current_user
from api.schemas.auth import UserOut
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# POST /v1/admin/users/{user_id}/delete and /undelete, DELETE /v1/admin/users/{user_id}/permanent,
# and what a deleted user can no longer do.
# Mock rows are dicts, as run_query returns them: the columns sign-in reads about a user, deleted_at and
# deleted_by among them (migration 028).
# ---------------------------------------------------------------------------

URL = "/v1/admin/users"
QUERY = "api.queries.users.run_query"
USER_ID = "b0000000-0000-4000-8000-000000000002"
OTHER_ID = "e0000000-0000-4000-8000-000000000009"
ADMIN_ID = str(ADMIN_USER_ROW["uuid"])
DELETE = f"{URL}/{USER_ID}/delete"
UNDELETE = f"{URL}/{USER_ID}/undelete"
PERMANENT = f"{URL}/{USER_ID}/permanent"
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
CRON_SECRET = "cron-secret-for-tests-only"
CRON = {"Authorization": f"Bearer {CRON_SECRET}"}
ROOT = Path(__file__).resolve().parents[2]

USER_ROW = {"uuid": UUID(USER_ID), "email": "KhanG@magnoleng.pc", "first_name": "Genghis", "last_name": "Khan",
            "client_id": "C00001", "role": None, "is_demo": False, "signature_path": None, "signature_type": None,
            "signature_set_at": None, "deleted_at": None, "deleted_by": None}
DELETED_ROW = {**USER_ROW, "deleted_at": NOW, "deleted_by": ADMIN_USER_ROW["uuid"]}
DELETED_JSON = {"uuid": USER_ID, "email": "KhanG@magnoleng.pc", "first_name": "Genghis", "last_name": "Khan",
                "role": None, "is_demo": False, "signature_set_at": None, "has_signature": False,
                "deleted_at": "2026-10-13T15:00:00Z", "deleted_by": ADMIN_ID}
OTHER_ROW = {**USER_ROW, "uuid": UUID(OTHER_ID), "email": "khang@magnoleng.pc", "first_name": "New"}
NO_REFERENCES = {"idrs": 0, "project_users": 0, "audit": 0, "field_edits": 0, "attachments": 0, "properties": 0,
                 "task_orders": 0, "task_order_properties": 0, "pirs": 0,
                 "flags": 0, "pir_pages": 0, "non_assessable_areas": 0, "features": 0, "segments": 0,
                 "idr_flag_attributions": 0, "idr_segment_attributions": 0, "match_lines": 0,
                 "ped_ramp_corners": 0, "ped_ramp_inspections": 0, "ped_ramp_tifs": 0, "ped_ramp_photos": 0,
                 "idr_report_sites": 0, "idr_report_site_pay_items": 0, "sidewalk_strips": 0, "flag_edits": 0}
INSPECTOR_ROW = {**ADMIN_USER_ROW, "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})


def flat(sql: str) -> str:
    """
    Put a statement on one line.
    Takes the SQL.
    Returns it with every run of whitespace as one space.
    """
    return " ".join(sql.split())


@pytest.fixture
def cron_secret(monkeypatch):
    """
    Give the app a CRON_SECRET for one test.
    """
    monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)


class TestSoftDeleteUser:
    def test_an_admin_soft_deletes_a_user(self, admin_client):
        with patch(QUERY, return_value=[DELETED_ROW]) as run:
            response = admin_client.post(DELETE)
        assert response.status_code == 200 and response.json() == {"data": DELETED_JSON}
        assert run.call_count == 1
        sql, params = run.call_args.args
        assert "SET deleted_at = now(), deleted_by = %s, updated_at = now()" in flat(sql)
        assert "WHERE u.uuid = %s AND u.deleted_at IS NULL" in sql and params == (ADMIN_USER_ROW["uuid"], UUID(USER_ID))

    def test_nothing_else_of_theirs_is_touched(self, admin_client):
        with patch(QUERY, return_value=[DELETED_ROW]) as run:
            admin_client.post(DELETE)
        sql = run.call_args.args[0]
        assert "UPDATE icid.users u" in sql and "DELETE" not in sql and sql.count(";") == 1
        for table in ("icid.idrs", "icid.project_users", "icid.idr_audit"):
            assert table not in sql
        for column in ("password_hash", "signature_path =", "role =", "email ="):
            assert column not in sql.split("RETURNING")[0]

    def test_an_admin_cant_delete_their_own_account(self, admin_client):
        with patch(QUERY) as run:
            response = admin_client.post(f"{URL}/{ADMIN_ID}/delete")
        assert response.status_code == 400
        assert response.json() == {"detail": "You cannot delete your own account."}
        run.assert_not_called()

    def test_an_unknown_user_is_404(self, admin_client):
        with patch(QUERY, return_value=[]) as run:
            response = admin_client.post(DELETE)
        assert response.status_code == 404 and response.json() == {"detail": "User not found"}
        assert run.call_count == 2

    def test_a_user_already_deleted_is_404_and_keeps_their_first_deletion(self, admin_client):
        with patch(QUERY, side_effect=[[], [DELETED_ROW]]) as run:
            response = admin_client.post(DELETE)
        assert response.status_code == 404 and response.json() == {"detail": "User is already deleted"}
        assert [call.args[0].lstrip().split()[0] for call in run.call_args_list] == ["UPDATE", "SELECT"]

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.post(DELETE)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(QUERY) as run:
            assert client.post(DELETE, headers=CRON).status_code == 401
        run.assert_not_called()

    def test_a_user_id_that_isnt_a_uuid_is_422(self, admin_client):
        with patch(QUERY) as run:
            assert admin_client.post(f"{URL}/not-a-uuid/delete").status_code == 422
        run.assert_not_called()


class TestADeletedUserIsLockedOut:
    def test_sign_in_looks_only_among_users_who_arent_deleted(self):
        with patch(QUERY, return_value=[]) as run:
            assert auth_provider.verify_credentials("KhanG@magnoleng.pc", "test1234") is None
        sql, params = run.call_args.args
        assert "WHERE lower(u.email) = %s AND u.deleted_at IS NULL" in sql and params == ("khang@magnoleng.pc",)

    def test_a_deleted_user_gets_the_same_401_as_a_wrong_password(self, client):
        with patch(QUERY, return_value=[]):  # the lookup finds no user who isn't deleted
            deleted = client.post("/v1/auth/login", json={"email": "KhanG@magnoleng.pc", "password": "test1234"})
        stored = {**USER_ROW, "password_hash": bcrypt.hashpw(b"test1234", bcrypt.gensalt(rounds=4)).decode()}
        with patch(QUERY, return_value=[stored]):
            wrong = client.post("/v1/auth/login", json={"email": "KhanG@magnoleng.pc", "password": "not-it-1"})
            right = client.post("/v1/auth/login", json={"email": "KhanG@magnoleng.pc", "password": "test1234"})
        assert (deleted.status_code, wrong.status_code, right.status_code) == (401, 401, 200)
        assert deleted.json() == wrong.json() == {"detail": "Invalid email or password"}
        assert deleted.headers.get("www-authenticate") == wrong.headers.get("www-authenticate")

    def test_a_token_they_already_hold_stops_working(self, client):
        token = auth_provider.issue_token(UserOut.model_validate(USER_ROW))
        headers = {"Authorization": f"Bearer {token}"}
        with patch("api.services.auth.get_user_by_uuid", return_value=USER_ROW):
            before = client.get("/v1/auth/me", headers=headers)
        with patch("api.services.auth.get_user_by_uuid", return_value=DELETED_ROW), patch(QUERY) as run:
            after = client.get("/v1/auth/me", headers=headers)
            listing = client.get("/v1/users/", headers=headers)
        assert before.status_code == 200
        assert after.status_code == 401 and after.json() == {"detail": "Invalid token"}
        assert listing.status_code == 401
        run.assert_not_called()

    def test_the_dependency_refuses_a_deleted_user_like_one_who_is_gone(self):
        token = auth_provider.issue_token(UserOut.model_validate(USER_ROW))
        for row in (DELETED_ROW, None):
            with patch("api.services.auth.get_user_by_uuid", return_value=row), pytest.raises(HTTPException) as error:
                current_user(f"Bearer {token}")
            assert (error.value.status_code, error.value.detail) == (401, "Invalid token")

    def test_a_restored_user_signs_in_again_with_the_password_they_had(self, admin_client, client):
        password_hash = bcrypt.hashpw(b"test1234", bcrypt.gensalt(rounds=4)).decode()
        with patch(QUERY, side_effect=[[DELETED_ROW], [USER_ROW]]) as run:
            assert admin_client.post(UNDELETE).status_code == 200
        assert "password_hash" not in run.call_args.args[0].split("RETURNING")[0]  # the restore leaves it alone
        with patch(QUERY, return_value=[{**USER_ROW, "password_hash": password_hash}]):
            response = client.post("/v1/auth/login", json={"email": "khang@magnoleng.pc", "password": "test1234"})
        assert response.status_code == 200 and response.json()["user"]["uuid"] == USER_ID

    def test_a_deleted_user_cant_be_given_a_project_role_but_can_lose_one(self, admin_client):
        body = {"user_uuid": USER_ID, "role": "oe"}
        project = [{"project_id": "HWS0023", "project_name": "S/W", "project_description": None,
                    "registration_code": None, "borough": None, "status": None}]

        def projects_query(sql, params=None):
            """Stand in for run_query in api.queries.projects: the project exists and nobody holds a role on it."""
            return [] if "project_users" in sql else project

        with patch("api.queries.projects.run_query", side_effect=projects_query) as projects, \
             patch(QUERY, return_value=[DELETED_ROW]):
            granted = admin_client.post("/v1/projects/HWS0023/roles", json={**body, "action": "grant"})
            assert projects.call_count == 1  # the project lookup; no role was written
            revoked = admin_client.post("/v1/projects/HWS0023/roles", json={**body, "action": "revoke"})
        assert granted.status_code == 400
        assert granted.json() == {"detail": "Deleted users can't be given project roles"}
        assert revoked.status_code == 200
        assert any("DELETE FROM icid.project_users" in call.args[0] for call in projects.call_args_list)


class TestUndeleteUser:
    def test_a_deleted_user_is_restored(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [USER_ROW]]) as run:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 200
        assert response.json() == {"data": {**DELETED_JSON, "deleted_at": None, "deleted_by": None}}
        sql, params = run.call_args.args
        one_line = flat(sql)
        assert "SET deleted_at = NULL, deleted_by = NULL, updated_at = now()" in one_line
        assert "WHERE u.uuid = %s AND u.deleted_at IS NOT NULL" in one_line and params == (UUID(USER_ID),)
        assert ("AND NOT EXISTS ( SELECT 1 FROM icid.users other WHERE lower(other.email) = lower(u.email) "
                "AND other.uuid <> u.uuid AND other.deleted_at IS NULL )") in one_line

    def test_a_user_who_isnt_deleted_is_404(self, admin_client):
        with patch(QUERY, side_effect=[[USER_ROW]]) as run:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 404 and response.json() == {"detail": "User is not deleted"}
        assert run.call_count == 1

    def test_an_unknown_user_is_404(self, admin_client):
        with patch(QUERY, return_value=[]):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 404 and response.json() == {"detail": "User not found"}

    def test_an_email_another_user_now_has_is_409_naming_them(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [], [OTHER_ROW]]) as run:
            response = admin_client.post(UNDELETE)
        assert response.status_code == 409
        assert response.json() == {"detail": "Email is now in use by another user", "existing_user_uuid": OTHER_ID}
        lookup_sql, lookup_params = run.call_args.args
        assert "lower(u.email) = %s AND u.deleted_at IS NULL" in lookup_sql
        assert lookup_params == ("khang@magnoleng.pc",)

    def test_a_user_restored_by_someone_else_meanwhile_is_404(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [], [USER_ROW]]):
            response = admin_client.post(UNDELETE)
        assert response.status_code == 404 and response.json() == {"detail": "User is not deleted"}

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.post(UNDELETE)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(QUERY) as run:
            assert client.post(UNDELETE, headers=CRON).status_code == 401
        run.assert_not_called()


class TestPermanentDeleteUser:
    def test_a_deleted_user_nothing_names_is_deleted_for_good(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [NO_REFERENCES], [{"uuid": UUID(USER_ID)}]]) as run, \
             patch("api.services.attachments.remove_files") as remove:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 204 and response.content == b""
        sql, params = run.call_args.args
        assert flat(sql) == "DELETE FROM icid.users u WHERE u.uuid = %s AND u.deleted_at IS NOT NULL RETURNING u.uuid;"
        assert params == (UUID(USER_ID),)
        # Their own signature file goes, from the signatures bucket; no IDR's copy is touched
        remove.assert_called_once_with([f"users/{USER_ID}/signature.png"], "signatures")

    def test_who_deleted_them_is_logged(self, admin_client, caplog):
        with patch(QUERY, side_effect=[[DELETED_ROW], [NO_REFERENCES], [{"uuid": UUID(USER_ID)}]]), \
             patch("api.services.attachments.remove_files"), caplog.at_level(logging.INFO, logger="api.v1.admin_users"):
            admin_client.delete(PERMANENT)
        line = next(r.getMessage() for r in caplog.records if "User permanently deleted" in r.getMessage())
        assert f"user_uuid={USER_ID}" in line and "email=KhanG@magnoleng.pc" in line
        assert f"admin={ADMIN_ID}" in line and "at=" in line

    @pytest.mark.parametrize("references", [
        {**NO_REFERENCES, "idrs": 3}, {**NO_REFERENCES, "project_users": 2}, {**NO_REFERENCES, "audit": 14},
        {**NO_REFERENCES, "field_edits": 1}, {**NO_REFERENCES, "attachments": 5}, {**NO_REFERENCES, "properties": 2},
        {**NO_REFERENCES, "task_orders": 1}, {**NO_REFERENCES, "task_order_properties": 3},
        {**NO_REFERENCES, "pirs": 4}, {**NO_REFERENCES, "flags": 6}, {**NO_REFERENCES, "pir_pages": 2},
        {**NO_REFERENCES, "non_assessable_areas": 2}, {**NO_REFERENCES, "features": 3}, {**NO_REFERENCES, "segments": 1},
        {**NO_REFERENCES, "idr_flag_attributions": 2}, {**NO_REFERENCES, "idr_segment_attributions": 1},
        {**NO_REFERENCES, "match_lines": 2}, {**NO_REFERENCES, "ped_ramp_corners": 1},
        {**NO_REFERENCES, "ped_ramp_inspections": 1}, {**NO_REFERENCES, "ped_ramp_tifs": 2},
        {**NO_REFERENCES, "ped_ramp_photos": 3}, {**NO_REFERENCES, "idr_report_sites": 2}, {**NO_REFERENCES, "idr_report_site_pay_items": 3},
        {**NO_REFERENCES, "sidewalk_strips": 1}, {**NO_REFERENCES, "flag_edits": 2},
        {"idrs": 4, "project_users": 2, "audit": 9, "field_edits": 3, "attachments": 1, "properties": 1,
         "task_orders": 2, "task_order_properties": 5, "pirs": 1, "flags": 3, "pir_pages": 1,
         "non_assessable_areas": 1, "features": 2, "segments": 1, "idr_flag_attributions": 1,
         "idr_segment_attributions": 1, "match_lines": 1, "ped_ramp_corners": 2, "ped_ramp_inspections": 1,
         "ped_ramp_tifs": 1, "ped_ramp_photos": 4, "idr_report_sites": 1, "idr_report_site_pay_items": 2,
         "sidewalk_strips": 1, "flag_edits": 3},
    ])
    def test_a_user_with_history_is_409_with_what_names_them(self, admin_client, references):
        with patch(QUERY, side_effect=[[DELETED_ROW], [references]]) as run, \
             patch("api.services.attachments.remove_files") as remove:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 409
        assert response.json() == {"detail": "User has history and can't be permanently deleted",
                                   "references": references}
        assert run.call_count == 2 and "DELETE" not in run.call_args.args[0]
        remove.assert_not_called()

    def test_the_count_covers_every_foreign_key_to_users(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [{**NO_REFERENCES, "idrs": 1}]]) as run:
            admin_client.delete(PERMANENT)
        sql, params = run.call_args.args
        one_line = flat(sql)
        assert "%s IN (i.reporter_uuid, i.stage1_reviewer_uuid, i.re_reviewer_uuid, i.deleted_by, i.last_unlocked_by)" in one_line
        for reference in ("icid.project_users pu WHERE pu.user_uuid = %s", "icid.idr_audit a WHERE a.actor_uuid = %s",
                          "icid.idr_field_edits e WHERE e.editor_uuid = %s",
                          "icid.report_attachments ra WHERE ra.uploaded_by = %s",
                          "icid.properties p WHERE %s IN (p.created_by, p.deleted_by, p.tax_map_pdf_uploaded_by)",
                          "icid.task_orders t WHERE %s IN (t.created_by, t.deleted_by)",
                          "icid.task_order_properties tp WHERE tp.added_by = %s",
                          "icid.pirs pir WHERE %s IN (pir.digitized_by, pir.as_built_signed_by, pir.deleted_by)",
                          "icid.flags f WHERE %s IN (f.created_by, f.deleted_by)",
                          "icid.pir_pages pg WHERE pg.deleted_by = %s",
                          "icid.non_assessable_areas na WHERE %s IN (na.created_by, na.deleted_by)",
                          "icid.features ft WHERE %s IN (ft.created_by, ft.deleted_by)",
                          "icid.segments s WHERE %s IN (s.created_by, s.deleted_by)",
                          "icid.idr_flag_attributions ifa WHERE ifa.set_by = %s",
                          "icid.idr_segment_attributions isa WHERE isa.set_by = %s",
                          "icid.pir_match_lines ml WHERE %s IN (ml.created_by, ml.deleted_by)",
                          "icid.ped_ramp_corners rc WHERE %s IN (rc.created_by, rc.deleted_by)",
                          "icid.ped_ramp_inspections ri WHERE %s IN (ri.inspector_uuid, ri.re_uuid, ri.returned_by, ri.created_by, ri.last_unlocked_by)",
                          "icid.ped_ramp_tifs rt WHERE %s IN (rt.signed_by_uuid, rt.created_by)",
                          "icid.ped_ramp_photos rp WHERE rp.uploaded_by = %s",
                          "icid.idr_report_sites irs WHERE %s IN (irs.added_by, irs.updated_by)",
                          "icid.idr_report_site_pay_items sp WHERE sp.added_by = %s",
                          "icid.pir_annotations an WHERE an.sidewalk_strip_set_by = %s",
                          "icid.flag_edits fe WHERE fe.editor_uuid = %s"):
            assert reference in one_line
        assert params == (UUID(USER_ID),) * 25
        # schema.sql has no foreign key to users that the count leaves out
        schema = ROOT.joinpath("schema.sql").read_text(encoding="utf-8")
        # the forty-three counted (each table's last_unlocked_by, the report sites' added_by and updated_by and the site pay
        # items' added_by too) (the properties', task orders', bundles', PIRs', flags', PIR pages', non-assessable
        # areas', features', segments', IDR flag and segment attributions', match lines', ped ramp corners',
        # inspections', TIFs' and photos' among them; Slice Y1.5's tax-map PDF uploader, strip setter and flag editor),
        # and users.deleted_by
        assert schema.count("REFERENCES icid.users(uuid)") == 47

    def test_a_user_who_isnt_soft_deleted_is_404(self, admin_client):
        with patch(QUERY, side_effect=[[USER_ROW]]) as run, patch("api.services.attachments.remove_files") as remove:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 404 and response.json() == {"detail": "User must be soft-deleted first"}
        assert run.call_count == 1
        remove.assert_not_called()

    def test_an_unknown_user_is_404(self, admin_client):
        with patch(QUERY, return_value=[]):
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 404 and response.json() == {"detail": "User not found"}

    def test_a_row_that_names_them_at_the_last_moment_is_409(self, admin_client):
        late = {**NO_REFERENCES, "project_users": 1}
        with patch(QUERY, side_effect=[[DELETED_ROW], [NO_REFERENCES], ForeignKeyViolation("fk"), [late]]), \
             patch("api.services.attachments.remove_files") as remove:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 409 and response.json()["references"] == late
        remove.assert_not_called()

    def test_a_user_restored_at_the_last_moment_is_404_and_keeps_their_signature(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [NO_REFERENCES], [], [NO_REFERENCES]]), \
             patch("api.services.attachments.remove_files") as remove:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 404 and response.json() == {"detail": "User must be soft-deleted first"}
        remove.assert_not_called()

    def test_a_storage_failure_doesnt_fail_the_delete(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], [NO_REFERENCES], [{"uuid": UUID(USER_ID)}]]), \
             patch("api.services.attachments.remove_files", side_effect=RuntimeError("storage down")):
            assert admin_client.delete(PERMANENT).status_code == 204

    def test_a_count_that_fails_is_500_and_nothing_is_deleted(self, admin_client):
        with patch(QUERY, side_effect=[[DELETED_ROW], None]) as run:
            response = admin_client.delete(PERMANENT)
        assert response.status_code == 500 and run.call_count == 2

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.delete(PERMANENT)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(QUERY) as run:
            assert client.delete(PERMANENT, headers=CRON).status_code == 401
        run.assert_not_called()


class TestListsLeaveDeletedUsersOut:
    def test_the_admin_list_leaves_them_out_by_default(self, admin_client):
        with patch(QUERY, return_value=[USER_ROW]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200 and len(response.json()["data"]) == 1
        assert "WHERE u.deleted_at IS NULL" in run.call_args.args[0]

    @pytest.mark.parametrize("value", ["true", "1"])
    def test_the_admin_list_includes_them_when_asked(self, admin_client, value):
        with patch(QUERY, return_value=[DELETED_ROW, USER_ROW]) as run:
            response = admin_client.get(URL, params={"include_deleted": value})
        data = response.json()["data"]
        assert response.status_code == 200 and "WHERE" not in run.call_args.args[0]
        assert data[0] == DELETED_JSON
        assert (data[1]["deleted_at"], data[1]["deleted_by"]) == (None, None)

    def test_include_deleted_false_is_the_default(self, admin_client):
        with patch(QUERY, return_value=[]) as run:
            admin_client.get(URL, params={"include_deleted": "false"})
        assert "WHERE u.deleted_at IS NULL" in run.call_args.args[0]

    def test_the_users_list_leaves_them_out_and_takes_no_such_parameter(self, admin_client):
        with patch(QUERY, return_value=[]) as run:
            response = admin_client.get("/v1/users/", params={"include_deleted": "true"})
        assert response.status_code == 200
        assert "WHERE u.deleted_at IS NULL" in run.call_args.args[0] and run.call_args.args[1:] == ()

    def test_an_email_is_looked_up_among_users_who_arent_deleted(self):
        with patch(QUERY, return_value=[]) as run:
            assert get_user_by_email("KhanG@magnoleng.pc") is None
        assert "AND u.deleted_at IS NULL" in run.call_args.args[0]


class TestUserDeleteQueries:
    def test_the_list_queries_filter_in_sql(self):
        with patch(QUERY, return_value=[]) as run:
            get_all_users()
            list_users_for_admin()
            list_users_for_admin(include_deleted=True)
        users, default, everyone = (call.args[0] for call in run.call_args_list)
        assert "WHERE u.deleted_at IS NULL" in users and "WHERE u.deleted_at IS NULL" in default
        assert "WHERE" not in everyone and "u.deleted_at" in everyone  # the column is still returned

    def test_soft_delete_and_undelete_return_the_row_or_none(self):
        with patch(QUERY, return_value=[DELETED_ROW]):
            assert soft_delete_user(UUID(USER_ID), ADMIN_USER_ROW["uuid"]) == DELETED_ROW
            assert undelete_user(UUID(USER_ID)) == DELETED_ROW
        with patch(QUERY, return_value=[]):
            assert soft_delete_user(UUID(USER_ID), ADMIN_USER_ROW["uuid"]) is None
            assert undelete_user(UUID(USER_ID)) is None

    def test_undelete_treats_an_email_taken_at_the_last_moment_as_no_row(self):
        with patch(QUERY, side_effect=UniqueViolation("idx_users_email_lower")):
            assert undelete_user(UUID(USER_ID)) is None

    def test_the_counts_are_the_row_or_none(self):
        with patch(QUERY, return_value=[NO_REFERENCES]):
            assert count_user_references(UUID(USER_ID)) == NO_REFERENCES
        with patch(QUERY, return_value=None):
            assert count_user_references(UUID(USER_ID)) is None

    def test_permanent_delete_says_whether_a_row_went(self):
        with patch(QUERY, return_value=[{"uuid": UUID(USER_ID)}]):
            assert permanent_delete_user(UUID(USER_ID)) is True
        with patch(QUERY, return_value=[]):
            assert permanent_delete_user(UUID(USER_ID)) is False
        with patch(QUERY, side_effect=ForeignKeyViolation("fk")):
            assert permanent_delete_user(UUID(USER_ID)) is False


class TestMigration028:
    def test_it_adds_the_two_columns_and_makes_the_email_index_partial(self):
        text = ROOT.joinpath("migrations", "028_users_soft_delete.sql").read_text(encoding="utf-8")
        sql = text.split("-- MIGRATION", 1)[1].split("-- VERIFICATION", 1)[0]
        assert "ADD COLUMN deleted_at TIMESTAMPTZ," in sql
        assert "ADD COLUMN deleted_by UUID REFERENCES icid.users(uuid) ON DELETE SET NULL;" in sql
        assert sql.index("DROP INDEX icid.idx_users_email_lower;") < sql.index("CREATE UNIQUE INDEX")
        assert "CREATE UNIQUE INDEX idx_users_email_lower ON icid.users (lower(email)) WHERE deleted_at IS NULL;" in sql
        assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1 and "UPDATE" not in sql

    def test_schema_sql_matches_it(self):
        schema = ROOT.joinpath("schema.sql").read_text(encoding="utf-8")
        assert "deleted_at       TIMESTAMPTZ," in schema
        assert "deleted_by       UUID REFERENCES icid.users(uuid) ON DELETE SET NULL," in schema
        assert "ON icid.users (lower(email)) WHERE deleted_at IS NULL;" in schema
