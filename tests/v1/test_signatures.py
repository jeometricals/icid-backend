from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from storage3.exceptions import StorageApiError

from api.queries.users import get_user_by_uuid, get_user_for_auth, set_user_signature
from api.schemas.auth import UserOut
from api.services.signatures import (
    SignatureStorageError,
    idr_signature_path,
    request_user_signature_upload,
    snapshot_signature_for_idr,
    user_signature_path,
)
from api.storage import client as storage
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, UNSIGNED_USER_ROW

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the user SELECTs in api/queries/users.py.
# ---------------------------------------------------------------------------

USER_UUID = ADMIN_USER_ROW["uuid"]
SIGNATURE_PATH = f"users/{USER_UUID}/signature.png"
UPLOAD_URL = "https://storage.example/object/upload/sign/signatures/users/x/signature.png?token=up"
SET_AT = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
SIGNED_ROW = {**UNSIGNED_USER_ROW, "signature_path": SIGNATURE_PATH, "signature_type": "uploaded",
              "signature_set_at": SET_AT}
IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
QUERY = "api.queries.users.run_query"


@pytest.fixture
def bucket():
    """
    Replace the Supabase client, so nothing reaches real Storage.
    Yields (the mock client's storage API, the mock bucket API every bucket lookup returns).
    """
    client = MagicMock()
    api = client.storage.from_.return_value
    api.create_signed_upload_url.return_value = {"signed_url": UPLOAD_URL, "signedUrl": UPLOAD_URL, "token": "up",
                                                 "path": SIGNATURE_PATH}
    api.exists.return_value = True
    with patch("api.storage.client.get_client", return_value=client):
        yield client.storage, api


# ---------------------------------------------------------------------------
# POST /v1/signatures/upload-request
# ---------------------------------------------------------------------------

class TestUploadRequest:
    url = "/v1/signatures/upload-request"
    body = {"content_type": "image/png"}

    def test_returns_a_signed_url_for_the_users_signature_path(self, unsigned_client, bucket):
        response = unsigned_client.post(self.url, json=self.body)
        assert response.status_code == 200
        assert response.json() == {"upload_url": UPLOAD_URL, "storage_path": SIGNATURE_PATH, "expires_in": 7200}

    def test_signs_in_the_signatures_bucket_allowing_a_replacement(self, unsigned_client, bucket):
        storage_api, api = bucket
        unsigned_client.post(self.url, json=self.body)
        storage_api.from_.assert_called_once_with("signatures")
        (path, options), _ = api.create_signed_upload_url.call_args
        assert path == SIGNATURE_PATH and options.upsert == "true"  # a second signature replaces the first

    def test_the_path_is_the_signed_in_users_own_whatever_they_send(self, admin_client, bucket):
        _, api = bucket
        admin_client.post(self.url, json={**self.body, "user_uuid": str(DEMO_USER_ROW["uuid"]),
                                          "storage_path": "users/someone-else/signature.png"})
        assert api.create_signed_upload_url.call_args.args[0] == SIGNATURE_PATH

    def test_it_touches_no_user_row(self, unsigned_client, bucket):
        with patch(QUERY) as run:
            unsigned_client.post(self.url, json=self.body)
        run.assert_not_called()  # nothing is recorded until confirm

    @pytest.mark.parametrize("body", [{"content_type": "image/jpeg"}, {"content_type": "application/pdf"}, {}])
    def test_only_png_is_taken(self, unsigned_client, bucket, body):
        _, api = bucket
        assert unsigned_client.post(self.url, json=body).status_code == 422
        api.create_signed_upload_url.assert_not_called()

    def test_storage_failing_is_502(self, unsigned_client, bucket):
        _, api = bucket
        api.create_signed_upload_url.side_effect = RuntimeError("storage down")
        response = unsigned_client.post(self.url, json=self.body)
        assert response.status_code == 502 and response.json() == {"detail": "Could not create an upload link"}

    def test_a_demo_user_is_refused(self, demo_client, bucket):
        _, api = bucket
        response = demo_client.post(self.url, json=self.body)
        assert response.status_code == 403
        assert response.json() == {"detail": "Demo mode: signatures are not available"}
        api.create_signed_upload_url.assert_not_called()

    def test_needs_sign_in(self, client, bucket):
        assert client.post(self.url, json=self.body).status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/signatures/confirm
# ---------------------------------------------------------------------------

class TestConfirm:
    url = "/v1/signatures/confirm"

    @pytest.mark.parametrize("signature_type", ["drawn", "uploaded"])
    def test_records_the_signature_and_returns_the_updated_user(self, unsigned_client, bucket, signature_type):
        row = {**SIGNED_ROW, "signature_type": signature_type}
        with patch(QUERY, return_value=[row]) as run:
            response = unsigned_client.post(self.url, json={"signature_type": signature_type})
        assert response.status_code == 200
        user = response.json()
        assert user["uuid"] == str(USER_UUID) and user["has_signature"] is True
        assert user["signature_set_at"] == "2026-10-05T14:00:00Z"
        assert "signature_path" not in user and SIGNATURE_PATH not in response.text  # the path stays internal
        sql, params = run.call_args.args
        assert "UPDATE icid.users u" in sql and "signature_set_at = now()" in sql
        assert params == (SIGNATURE_PATH, signature_type, USER_UUID)

    def test_checks_the_file_is_in_the_signatures_bucket_first(self, unsigned_client, bucket):
        storage_api, api = bucket
        with patch(QUERY, return_value=[SIGNED_ROW]):
            unsigned_client.post(self.url, json={"signature_type": "drawn"})
        storage_api.from_.assert_called_once_with("signatures")
        api.exists.assert_called_once_with(SIGNATURE_PATH)

    def test_nothing_uploaded_is_400_and_the_user_row_is_untouched(self, unsigned_client, bucket):
        _, api = bucket
        api.exists.return_value = False
        with patch(QUERY) as run:
            response = unsigned_client.post(self.url, json={"signature_type": "drawn"})
        assert response.status_code == 400
        assert response.json() == {"detail": "Upload the signature before confirming"}
        run.assert_not_called()

    @pytest.mark.parametrize("status", [400, 404, "404"])
    def test_storages_not_found_error_counts_as_nothing_uploaded(self, unsigned_client, bucket, status):
        _, api = bucket
        api.exists.side_effect = StorageApiError("Object not found", "not_found", status)
        with patch(QUERY) as run:
            response = unsigned_client.post(self.url, json={"signature_type": "drawn"})
        assert response.status_code == 400
        run.assert_not_called()

    def test_storage_failing_is_502_and_the_user_row_is_untouched(self, unsigned_client, bucket):
        _, api = bucket
        api.exists.side_effect = StorageApiError("Internal error", "internal", 500)
        with patch(QUERY) as run:
            response = unsigned_client.post(self.url, json={"signature_type": "drawn"})
        assert response.status_code == 502
        assert response.json() == {"detail": "Could not check the uploaded signature"}
        run.assert_not_called()

    def test_a_row_that_wasnt_updated_is_500(self, unsigned_client, bucket):
        with patch(QUERY, return_value=[]):
            response = unsigned_client.post(self.url, json={"signature_type": "drawn"})
        assert response.status_code == 500 and response.json() == {"detail": "Failed to save signature"}

    @pytest.mark.parametrize("body", [{"signature_type": "typed"}, {"signature_type": ""}, {}])
    def test_the_type_must_be_drawn_or_uploaded(self, unsigned_client, bucket, body):
        _, api = bucket
        assert unsigned_client.post(self.url, json=body).status_code == 422
        api.exists.assert_not_called()

    def test_replacing_a_signature_works_the_same_way(self, admin_client, bucket):
        with patch(QUERY, return_value=[SIGNED_ROW]) as run:
            response = admin_client.post(self.url, json={"signature_type": "uploaded"})
        assert response.status_code == 200
        assert run.call_args.args[1] == (SIGNATURE_PATH, "uploaded", USER_UUID)

    def test_a_demo_user_is_refused(self, demo_client, bucket):
        _, api = bucket
        with patch(QUERY) as run:
            response = demo_client.post(self.url, json={"signature_type": "drawn"})
        assert response.status_code == 403
        assert response.json() == {"detail": "Demo mode: signatures are not available"}
        api.exists.assert_not_called()
        run.assert_not_called()

    def test_needs_sign_in(self, client, bucket):
        assert client.post(self.url, json={"signature_type": "drawn"}).status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/auth/me and login: has_signature and signature_set_at
# ---------------------------------------------------------------------------

class TestSignatureOnTheUser:
    def test_me_says_a_signature_is_set_and_when(self, admin_client):
        signed = admin_client.get("/v1/auth/me").json()
        assert (signed["has_signature"], signed["signature_set_at"]) == (True, "2026-10-01T09:00:00Z")

    def test_me_says_when_none_is_set(self, unsigned_client):
        unsigned = unsigned_client.get("/v1/auth/me").json()
        assert (unsigned["has_signature"], unsigned["signature_set_at"]) == (False, None)

    def test_me_never_returns_the_path_or_the_type(self, admin_client):
        response = admin_client.get("/v1/auth/me")
        assert set(response.json()) == {"uuid", "email", "first_name", "last_name", "role", "is_demo", "has_signature",
                                        "signature_set_at"}
        assert "signature.png" not in response.text

    def test_login_returns_them_too(self, client):
        import bcrypt
        row = {**SIGNED_ROW, "password_hash": bcrypt.hashpw(b"correct horse", bcrypt.gensalt(4)).decode()}
        with patch(QUERY, return_value=[row]):
            body = client.post("/v1/auth/login", json={"email": "admin@icid.local", "password": "correct horse"}).json()
        assert body["user"]["has_signature"] is True and body["user"]["signature_set_at"] == "2026-10-05T14:00:00Z"
        assert "signature_path" not in body["user"]

    def test_a_demo_user_has_none(self, demo_client):
        user = demo_client.get("/v1/auth/me").json()
        assert (user["has_signature"], user["signature_set_at"]) == (False, None)

    def test_the_user_model_keeps_the_path_internally(self):
        user = UserOut.model_validate(ADMIN_USER_ROW)
        assert user.signature_path == SIGNATURE_PATH and user.has_signature is True
        assert "signature_path" not in user.model_dump() and "signature_path" not in user.model_dump_json()
        assert UserOut.model_validate(DEMO_USER_ROW).has_signature is False  # a row without the columns


# ---------------------------------------------------------------------------
# Queries, paths and the Storage helpers
# ---------------------------------------------------------------------------

class TestSignatureQueries:
    def test_the_user_lookups_select_the_signature_columns(self):
        with patch(QUERY, return_value=[SIGNED_ROW]) as run:
            get_user_by_uuid(USER_UUID)
            get_user_for_auth("admin@icid.local")
        for call in run.call_args_list:
            for column in ("u.signature_path", "u.signature_type", "u.signature_set_at"):
                assert column in call.args[0]
        assert "u.password_hash" not in run.call_args_list[0].args[0]
        assert "u.password_hash" in run.call_args_list[1].args[0]

    def test_set_user_signature_is_one_update_never_on_a_demo_user(self):
        with patch(QUERY, return_value=[SIGNED_ROW]) as run:
            assert set_user_signature(USER_UUID, SIGNATURE_PATH, "drawn") == SIGNED_ROW
        assert run.call_count == 1
        sql, params = run.call_args.args
        assert "WHERE u.uuid = %s AND u.is_demo = false" in sql
        assert "RETURNING" in sql and "u.signature_set_at" in sql.split("RETURNING")[1]
        assert "password_hash" not in sql
        assert params == (SIGNATURE_PATH, "drawn", USER_UUID)

    def test_set_user_signature_is_none_when_no_row_matched(self):
        with patch(QUERY, return_value=[]):
            assert set_user_signature(USER_UUID, SIGNATURE_PATH, "drawn") is None


class TestSignaturePaths:
    def test_a_users_signature_has_one_fixed_path(self):
        assert user_signature_path(USER_UUID) == f"users/{USER_UUID}/signature.png"

    def test_an_idrs_copy_gets_a_fresh_path_under_the_idr_every_time(self):
        first, second = idr_signature_path(IDR_ID), idr_signature_path(IDR_ID)
        assert first != second  # a copy is never written over another
        for path in (first, second):
            assert path.startswith(f"idrs/{IDR_ID}/inspector_") and path.endswith(".png")


class TestSignatureStorage:
    def test_the_snapshot_copies_within_the_signatures_bucket_and_returns_the_copys_path(self, bucket):
        storage_api, api = bucket
        copy_path = snapshot_signature_for_idr(SIGNATURE_PATH, IDR_ID)
        storage_api.from_.assert_called_once_with("signatures")
        api.copy.assert_called_once_with(SIGNATURE_PATH, copy_path)
        assert copy_path.startswith(f"idrs/{IDR_ID}/inspector_")
        api.remove.assert_not_called()  # nothing is ever removed to make room
        api.upload.assert_not_called()  # a server-side copy, not a download and re-upload

    def test_a_failed_copy_raises_the_storage_error(self, bucket):
        _, api = bucket
        api.copy.side_effect = StorageApiError("Object not found", "not_found", 404)
        with pytest.raises(SignatureStorageError, match="Could not copy the signature"):
            snapshot_signature_for_idr(SIGNATURE_PATH, IDR_ID)

    def test_the_upload_request_reports_supabases_fixed_upload_lifetime(self, bucket):
        assert request_user_signature_upload(USER_UUID)["expires_in"] == 7200

    def test_object_exists_is_true_false_or_raises(self, bucket):
        _, api = bucket
        assert storage.object_exists("signatures", SIGNATURE_PATH) is True
        api.exists.return_value = False
        assert storage.object_exists("signatures", SIGNATURE_PATH) is False
        api.exists.side_effect = StorageApiError("Object not found", "not_found", 404)
        assert storage.object_exists("signatures", SIGNATURE_PATH) is False
        api.exists.side_effect = StorageApiError("Internal error", "internal", 500)
        with pytest.raises(StorageApiError):
            storage.object_exists("signatures", SIGNATURE_PATH)

    def test_attachment_uploads_are_still_signed_without_replacement(self, bucket):
        storage_api, api = bucket
        storage.create_signed_upload_url("report/attachment.jpg")
        storage_api.from_.assert_called_once_with("report-attachments")
        api.create_signed_upload_url.assert_called_once_with("report/attachment.jpg")
