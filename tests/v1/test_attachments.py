import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch
from uuid import UUID

import pytest
from psycopg.errors import ForeignKeyViolation

from api.core.config import STORAGE_URL_EXPIRY_SECONDS
from tests.conftest import ADMIN_USER_ROW
from api.services.attachments import MAX_FILE_SIZE_BYTES, UPLOADED_BY_FK, sanitize_file_name

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the column names in api/queries/{idrs,idr_reports,users,report_attachments}.py.
# ---------------------------------------------------------------------------

IDR_ID = "9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e"
REPORT_ID = "e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091"
ATTACHMENT_ID = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"
UPLOADER_UUID = "7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90"
NOW = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)
STORAGE_PATH = f"{REPORT_ID}/{ATTACHMENT_ID}_site_photo.jpg"
SIGNED_URL = "https://example.supabase.co/storage/v1/object/sign/report-attachments/x?token=abc&download=site%20photo.jpg"
UPLOAD_URL = "https://example.supabase.co/storage/v1/object/upload/sign/report-attachments/x?token=up"

BASE = f"/v1/idrs/{IDR_ID}/reports/{REPORT_ID}/attachments"
ONE = f"{BASE}/{ATTACHMENT_ID}"
UPLOAD_REQUEST = f"{BASE}/upload-request"
UPLOAD_COMPLETE = f"{BASE}/upload-complete"

MOCK_IDR_ROW = {
    "idr_id": UUID(IDR_ID),
    "project_id": "HWS0023",
    "reporter_uuid": UUID(UPLOADER_UUID),
    "report_date": NOW.date(),
    "work_start_time": None,
    "work_end_time": None,
    "inspector_start_time": None,
    "inspector_end_time": None,
    "temp_low": None,
    "temp_high": None,
    "weather_am": None,
    "weather_pm": None,
    "total_pages": None,
    "has_dismissed_auto_general": False,
    "status": "draft",
    "submitted_at": None,
    "created_at": NOW,
    "updated_at": NOW,
}
MOCK_SUBMITTED_IDR_ROW = {**MOCK_IDR_ROW, "status": "submitted", "submitted_at": NOW, "total_pages": 1}

MOCK_REPORT_ROW = {
    "report_id": UUID(REPORT_ID),
    "idr_id": UUID(IDR_ID),
    "report_type": "SWR",
    "is_addendum": False,
    "parent_report_id": None,
    "page_number": None,
    "report_data": {},
    "is_auto_generated": False,
    "created_at": NOW,
    "updated_at": NOW,
}
MOCK_AUTO_GENERAL_ROW = {**MOCK_REPORT_ROW, "report_type": "GEN", "is_auto_generated": True}

MOCK_ASSIGNMENT_ROW = {"?column?": 1}  # is_user_on_project does SELECT 1

MOCK_ATTACHMENT_ROW = {
    "attachment_id": UUID(ATTACHMENT_ID),
    "report_id": UUID(REPORT_ID),
    "file_name": "site photo.jpg",
    "file_type": "image/jpeg",
    "file_size_bytes": 11,
    "storage_path": STORAGE_PATH,
    "uploaded_by": UUID(UPLOADER_UUID),
    "uploaded_at": NOW,
    "attachment_name": "North wall",
    "attachment_description": "Crack along the north wall footing.",
    "is_uploaded": True,
}
MOCK_PENDING_ROW = {**MOCK_ATTACHMENT_ROW, "is_uploaded": False}

PUBLIC_FIELDS = {
    "attachment_id",
    "file_name",
    "file_type",
    "file_size_bytes",
    "uploaded_by",
    "uploaded_at",
    "attachment_name",
    "attachment_description",
}
UPLOAD_REQUEST_FIELDS = PUBLIC_FIELDS | {"storage_path", "upload_url", "upload_url_expires_at", "upload_headers"}

# The uploader is the signed-in user (ADMIN_USER_ROW), not part of the body
REQUEST_BODY = {
    "file_name": "site photo.jpg",
    "file_type": "image/jpeg",
    "file_size_bytes": 11,
    "attachment_name": "North wall",
    "attachment_description": "Crack along the north wall footing.",
}
METADATA_BODY = {"attachment_name": "North wall", "attachment_description": "Crack along the north wall footing."}


class UploaderFkViolation(ForeignKeyViolation):
    """A ForeignKeyViolation naming the uploaded_by constraint, as psycopg raises it for an unknown user."""

    @property
    def diag(self) -> SimpleNamespace:
        """
        Stand in for psycopg's error diagnostics.
        Takes nothing.
        Returns an object whose constraint_name is the uploaded_by foreign key.
        """
        return SimpleNamespace(constraint_name=UPLOADED_BY_FK)


@pytest.fixture(autouse=True)
def bucket():
    """
    Replace the Supabase client for every test in this module, so nothing reaches real Storage.
    Takes nothing.
    Yields the mock bucket API (client.storage.from_(...)) the Storage module calls.
    """
    client = MagicMock()
    storage_bucket = client.storage.from_.return_value
    storage_bucket.create_signed_url.return_value = {"signedURL": SIGNED_URL, "signedUrl": SIGNED_URL}
    storage_bucket.create_signed_upload_url.return_value = {
        "signed_url": UPLOAD_URL,
        "signedUrl": UPLOAD_URL,
        "token": "up",
        "path": STORAGE_PATH,
    }
    with patch("api.storage.client.get_client", return_value=client):
        yield storage_bucket


@contextmanager
def patched(idrs=None, idr_reports=None, users=None, projects=None, attachments=None):
    """
    Patch run_query in each query module the attachment endpoints use.
    Takes the return value (or side_effect tuple / exception) for each module's run_query.
    Yields a dict of the mocks keyed by module name.
    """
    def kwargs(value):
        if isinstance(value, tuple) or isinstance(value, BaseException):
            return {"side_effect": value}
        return {"return_value": value}

    with patch("api.queries.idrs.run_query", **kwargs(idrs)) as i, \
         patch("api.queries.idr_reports.run_query", **kwargs(idr_reports)) as ir, \
         patch("api.queries.users.run_query", **kwargs(users)) as u, \
         patch("api.queries.projects.run_query", **kwargs(projects)) as p, \
         patch("api.queries.report_attachments.run_query", **kwargs(attachments)) as a:
        yield {"idrs": i, "idr_reports": ir, "users": u, "projects": p, "attachments": a}


def upload_ok(**overrides):
    """
    Build the query results for an upload request that passes every check.
    Takes any patched() keyword to override.
    Returns the keyword dict for patched().
    """
    results = {
        "idrs": [MOCK_IDR_ROW],
        "idr_reports": [MOCK_REPORT_ROW],
        "projects": [MOCK_ASSIGNMENT_ROW],
        "attachments": [MOCK_PENDING_ROW],
    }
    return {**results, **overrides}


def assert_only_lookup(attachments_mock: MagicMock) -> None:
    """
    Check that the attachment query module ran exactly one query: the SELECT of this attachment on this report.
    Takes the report_attachments run_query mock.
    Returns nothing; fails the test if anything else (an UPDATE, a DELETE) ran.
    """
    assert attachments_mock.call_args_list == [call(ANY, (UUID(REPORT_ID), UUID(ATTACHMENT_ID)))]
    assert attachments_mock.call_args.args[0].strip().startswith("SELECT")


def on_report(attachments, idrs=None):
    """
    Build the query results for a request on an existing report.
    Takes the report_attachments run_query result and, optionally, the IDR row list (a draft by default).
    Returns the keyword dict for patched().
    """
    return {"idrs": idrs or [MOCK_IDR_ROW], "idr_reports": [MOCK_REPORT_ROW], "attachments": attachments}


# ---------------------------------------------------------------------------
# POST .../attachments/upload-request
# ---------------------------------------------------------------------------

class TestUploadRequest:

    def test_returns_201_with_upload_url_and_metadata(self, admin_client):
        with patched(**upload_ok()):
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "success"
        data = body["data"]
        assert data["attachment_id"] == ATTACHMENT_ID
        assert data["storage_path"] == STORAGE_PATH
        assert data["upload_url"] == UPLOAD_URL
        assert data["attachment_name"] == "North wall"
        assert data["attachment_description"] == "Crack along the north wall footing."

    def test_response_exposes_only_upload_fields(self, admin_client):
        with patched(**upload_ok()):
            data = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY).json()["data"]
        assert set(data) == UPLOAD_REQUEST_FIELDS
        assert "is_uploaded" not in data

    def test_signs_the_path_then_inserts_a_pending_row(self, admin_client, bucket):
        # Pin the generated attachment id so the exact storage path is known up front.
        with patch("api.services.attachments.uuid4", return_value=UUID(ATTACHMENT_ID)), \
             patched(**upload_ok()) as mocks:
            admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)

        # {report_id}/{attachment_id}_{sanitized name} ("site photo.jpg" -> "site_photo.jpg").
        bucket.create_signed_upload_url.assert_called_once_with(STORAGE_PATH)
        bucket.upload.assert_not_called()  # the client uploads the bytes, not the backend

        assert len(mocks["attachments"].call_args_list) == 1
        sql, params = mocks["attachments"].call_args.args
        assert "INSERT INTO icid.report_attachments" in sql
        assert "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)" in sql  # pending until upload-complete
        assert params == (
            UUID(ATTACHMENT_ID),
            UUID(REPORT_ID),
            "site photo.jpg",  # original name kept for display and downloads
            "image/jpeg",
            11,
            STORAGE_PATH,
            ADMIN_USER_ROW["uuid"],  # the signed-in user
            "North wall",
            "Crack along the north wall footing.",
        )

    def test_upload_url_expiry_is_supabases_7200_second_lifetime(self, admin_client):
        # 7200 is written out, not imported, so a change to the service constant fails here.
        before = datetime.now(timezone.utc)
        with patched(**upload_ok()):
            data = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY).json()["data"]
        expires_at = datetime.fromisoformat(data["upload_url_expires_at"])
        assert before + timedelta(seconds=7200) <= expires_at
        assert expires_at <= datetime.now(timezone.utc) + timedelta(seconds=7200)

    def test_upload_headers_carry_the_normalized_content_type(self, admin_client):
        with patched(**upload_ok()) as mocks:
            data = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "file_type": "image/JPEG; charset=binary"}).json()["data"]
        assert data["upload_headers"] == {"Content-Type": "image/jpeg"}
        assert mocks["attachments"].call_args.args[1][3] == "image/jpeg"

    def test_name_and_description_are_trimmed(self, admin_client):
        body = {**REQUEST_BODY, "attachment_name": "  North wall  ", "attachment_description": "\tCrack.\n"}
        with patched(**upload_ok()) as mocks:
            admin_client.post(UPLOAD_REQUEST, json=body)
        assert mocks["attachments"].call_args.args[1][7:] == ("North wall", "Crack.")

    def test_accepts_pdf(self, admin_client):
        with patched(**upload_ok()) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "file_name": "plan.pdf", "file_type": "application/pdf"})
        assert response.status_code == 201
        assert mocks["attachments"].call_args.args[1][3] == "application/pdf"

    def test_file_too_large_returns_413(self, admin_client, bucket):
        with patched(**upload_ok()) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "file_size_bytes": MAX_FILE_SIZE_BYTES + 1})
        assert response.status_code == 413
        assert response.json()["detail"] == "File is larger than the 10 MB limit"
        bucket.create_signed_upload_url.assert_not_called()
        mocks["attachments"].assert_not_called()

    def test_file_at_limit_is_accepted(self, admin_client):
        with patched(**upload_ok()):
            response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "file_size_bytes": MAX_FILE_SIZE_BYTES})
        assert response.status_code == 201

    @pytest.mark.parametrize("size", [0, -1])
    def test_empty_or_negative_size_returns_400(self, admin_client, bucket, size):
        with patched(**upload_ok()):
            response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "file_size_bytes": size})
        assert response.status_code == 400
        assert response.json()["detail"] == "File is empty"
        bucket.create_signed_upload_url.assert_not_called()

    @pytest.mark.parametrize("file_type", ["text/plain", "image/svg+xml", "application/zip", ""])
    def test_unsupported_type_returns_415(self, admin_client, bucket, file_type):
        with patched(**upload_ok()):
            response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "file_type": file_type})
        assert response.status_code == 415
        assert response.json()["detail"].startswith("Unsupported file type")
        bucket.create_signed_upload_url.assert_not_called()

    @pytest.mark.parametrize("field, value, detail", [
        ("attachment_name", "", "attachment_name must not be blank"),
        ("attachment_name", "   ", "attachment_name must not be blank"),
        ("attachment_name", "n" * 201, "attachment_name must be at most 200 characters"),
        ("attachment_description", "", "attachment_description must not be blank"),
        ("attachment_description", "\n\t", "attachment_description must not be blank"),
        ("attachment_description", "d" * 2001, "attachment_description must be at most 2000 characters"),
    ])
    def test_invalid_name_or_description_returns_400(self, admin_client, bucket, field, value, detail):
        with patched(**upload_ok()) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, field: value})
        assert response.status_code == 400
        assert response.json()["detail"] == detail
        bucket.create_signed_upload_url.assert_not_called()
        mocks["attachments"].assert_not_called()

    def test_name_and_description_at_limit_are_accepted(self, admin_client):
        body = {**REQUEST_BODY, "attachment_name": "n" * 200, "attachment_description": "d" * 2000}
        with patched(**upload_ok()):
            assert admin_client.post(UPLOAD_REQUEST, json=body).status_code == 201

    def test_auto_generated_general_returns_400(self, admin_client, bucket):
        with patched(**upload_ok(idr_reports=[MOCK_AUTO_GENERAL_ROW])) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Attachments cannot be added to auto-generated General reports; edit child reports instead."
        )
        bucket.create_signed_upload_url.assert_not_called()
        mocks["attachments"].assert_not_called()

    def test_an_uploaded_by_in_the_body_is_ignored(self, admin_client):
        # the uploader is always the signed-in user, whatever an old client still sends
        for sent in (UPLOADER_UUID, "28"):
            with patched(**upload_ok()) as mocks:
                response = admin_client.post(UPLOAD_REQUEST, json={**REQUEST_BODY, "uploaded_by": sent})
            assert response.status_code == 201
            assert mocks["attachments"].call_args.args[1][6] == ADMIN_USER_ROW["uuid"]
            assert mocks["projects"].call_args.args[1] == (ADMIN_USER_ROW["uuid"], "HWS0023")
            mocks["users"].assert_not_called()  # the session already vouches for the user

    def test_uploader_fk_violation_returns_400(self, admin_client, bucket):
        with patched(**upload_ok(attachments=UploaderFkViolation("violates report_attachments_uploaded_by_fkey"))):
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 400
        assert response.json()["detail"] == "Uploader user not found."
        bucket.remove.assert_not_called()  # nothing was uploaded, so nothing to clean up

    def test_uploader_not_on_project_returns_403(self, admin_client, bucket):
        with patched(**upload_ok(projects=[])):
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 403
        assert response.json()["detail"] == "Uploader is not assigned to this project"
        bucket.create_signed_upload_url.assert_not_called()

    def test_report_not_found_returns_404(self, admin_client, bucket):
        with patched(**upload_ok(idr_reports=[])):
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "Report not found in this IDR"
        bucket.create_signed_upload_url.assert_not_called()

    def test_idr_not_found_returns_404(self, admin_client):
        with patched(**upload_ok(idrs=[])) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_submitted_idr_returns_409(self, admin_client, bucket):
        with patched(**upload_ok(idrs=[MOCK_SUBMITTED_IDR_ROW])) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 409
        bucket.create_signed_upload_url.assert_not_called()
        mocks["attachments"].assert_not_called()

    def test_storage_signing_failure_returns_502_without_insert(self, admin_client, bucket):
        bucket.create_signed_upload_url.side_effect = RuntimeError("storage down")
        with patched(**upload_ok()) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 502
        assert response.json()["detail"] == "Could not create an upload link"
        mocks["attachments"].assert_not_called()

    def test_storage_returning_no_url_returns_502(self, admin_client, bucket):
        bucket.create_signed_upload_url.return_value = {"token": "up"}
        with patched(**upload_ok()) as mocks:
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 502
        mocks["attachments"].assert_not_called()

    def test_insert_failure_returns_500(self, admin_client, bucket):
        with patched(**upload_ok(attachments=None)):
            response = admin_client.post(UPLOAD_REQUEST, json=REQUEST_BODY)
        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to save attachment"
        bucket.remove.assert_not_called()

    @pytest.mark.parametrize("missing", sorted(REQUEST_BODY))
    def test_missing_field_returns_422(self, admin_client, missing):
        body = {k: v for k, v in REQUEST_BODY.items() if k != missing}
        with patched(**upload_ok()) as mocks:
            assert admin_client.post(UPLOAD_REQUEST, json=body).status_code == 422
        mocks["idrs"].assert_not_called()

    def test_old_single_step_upload_is_gone(self, admin_client):
        with patched(**upload_ok()):
            response = admin_client.post(BASE, files={"file": ("a.jpg", b"x", "image/jpeg")}, data={"uploaded_by": UPLOADER_UUID})
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# POST .../attachments/upload-complete
# ---------------------------------------------------------------------------

class TestUploadComplete:
    body = {"attachment_id": ATTACHMENT_ID}

    def test_marks_the_attachment_uploaded(self, admin_client):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])) as mocks:
            response = admin_client.post(UPLOAD_COMPLETE, json=self.body)
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Attachment uploaded"
        assert set(body["data"]) == PUBLIC_FIELDS
        assert body["data"]["attachment_id"] == ATTACHMENT_ID

        assert len(mocks["attachments"].call_args_list) == 1
        sql, params = mocks["attachments"].call_args.args
        assert "UPDATE icid.report_attachments" in sql
        assert "SET is_uploaded = true" in sql
        assert params == (UUID(REPORT_ID), UUID(ATTACHMENT_ID))

    def test_trusts_the_client_without_checking_storage(self, admin_client, bucket):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            admin_client.post(UPLOAD_COMPLETE, json=self.body)
        assert bucket.mock_calls == []

    def test_repeating_it_is_harmless(self, admin_client):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])) as mocks:
            assert admin_client.post(UPLOAD_COMPLETE, json=self.body).status_code == 200
            assert admin_client.post(UPLOAD_COMPLETE, json=self.body).status_code == 200
        # The same idempotent UPDATE both times.
        expected = call(ANY, (UUID(REPORT_ID), UUID(ATTACHMENT_ID)))
        assert mocks["attachments"].call_args_list == [expected, expected]
        assert all("SET is_uploaded = true" in c.args[0] for c in mocks["attachments"].call_args_list)

    def test_attachment_not_on_report_returns_404(self, admin_client):
        with patched(**on_report([])):
            response = admin_client.post(UPLOAD_COMPLETE, json=self.body)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment not found"

    def test_query_failure_returns_500(self, admin_client):
        with patched(**on_report(None)):
            response = admin_client.post(UPLOAD_COMPLETE, json=self.body)
        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to complete upload"

    def test_submitted_idr_returns_409(self, admin_client):
        with patched(**on_report([MOCK_ATTACHMENT_ROW], idrs=[MOCK_SUBMITTED_IDR_ROW])) as mocks:
            response = admin_client.post(UPLOAD_COMPLETE, json=self.body)
        assert response.status_code == 409
        mocks["attachments"].assert_not_called()

    def test_report_not_found_returns_404(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = admin_client.post(UPLOAD_COMPLETE, json=self.body)
        assert response.status_code == 404
        mocks["attachments"].assert_not_called()

    @pytest.mark.parametrize("body", [{}, {"attachment_id": "not-a-uuid"}])
    def test_missing_or_bad_attachment_id_returns_422(self, admin_client, body):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            assert admin_client.post(UPLOAD_COMPLETE, json=body).status_code == 422


# ---------------------------------------------------------------------------
# PUT .../attachments/{attachment_id}
# ---------------------------------------------------------------------------

class TestUpdateAttachmentMetadata:

    def test_replaces_name_and_description(self, admin_client):
        updated = {**MOCK_ATTACHMENT_ROW, "attachment_name": "South wall", "attachment_description": "Spalling."}
        body = {"attachment_name": "  South wall ", "attachment_description": "Spalling."}
        with patched(**on_report(([MOCK_ATTACHMENT_ROW], [updated]))) as mocks:
            response = admin_client.put(ONE, json=body)
        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data) == PUBLIC_FIELDS
        assert (data["attachment_name"], data["attachment_description"]) == ("South wall", "Spalling.")

        sql, params = mocks["attachments"].call_args.args
        # Only the two metadata columns are written; the file, its path and is_uploaded are untouched.
        set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
        assert " ".join(set_clause.split()) == "attachment_name = %s, attachment_description = %s"
        assert params == ("South wall", "Spalling.", UUID(REPORT_ID), UUID(ATTACHMENT_ID))
        # Every other field comes back as it was.
        assert UUID(data["attachment_id"]) == MOCK_ATTACHMENT_ROW["attachment_id"]
        assert UUID(data["uploaded_by"]) == MOCK_ATTACHMENT_ROW["uploaded_by"]
        assert datetime.fromisoformat(data["uploaded_at"]) == MOCK_ATTACHMENT_ROW["uploaded_at"]
        for field in ("file_name", "file_type", "file_size_bytes"):
            assert data[field] == MOCK_ATTACHMENT_ROW[field]

    def test_works_on_a_pending_attachment(self, admin_client):
        with patched(**on_report(([MOCK_PENDING_ROW], [MOCK_PENDING_ROW]))):
            assert admin_client.put(ONE, json=METADATA_BODY).status_code == 200

    def test_does_not_touch_storage(self, admin_client, bucket):
        with patched(**on_report(([MOCK_ATTACHMENT_ROW], [MOCK_ATTACHMENT_ROW]))):
            admin_client.put(ONE, json=METADATA_BODY)
        assert bucket.mock_calls == []

    @pytest.mark.parametrize("field, value", [
        ("attachment_name", " "),
        ("attachment_name", "n" * 201),
        ("attachment_description", ""),
        ("attachment_description", "d" * 2001),
    ])
    def test_invalid_name_or_description_returns_400_without_update(self, admin_client, field, value):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])) as mocks:
            response = admin_client.put(ONE, json={**METADATA_BODY, field: value})
        assert response.status_code == 400
        assert_only_lookup(mocks["attachments"])

    def test_submitted_idr_returns_409_without_update(self, admin_client):
        with patched(**on_report([MOCK_ATTACHMENT_ROW], idrs=[MOCK_SUBMITTED_IDR_ROW])) as mocks:
            response = admin_client.put(ONE, json=METADATA_BODY)
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be edited"
        assert_only_lookup(mocks["attachments"])

    def test_attachment_not_found_returns_404(self, admin_client):
        with patched(**on_report([])) as mocks:
            response = admin_client.put(ONE, json=METADATA_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment not found"
        assert_only_lookup(mocks["attachments"])

    def test_row_gone_before_update_returns_404(self, admin_client):
        with patched(**on_report(([MOCK_ATTACHMENT_ROW], []))):
            assert admin_client.put(ONE, json=METADATA_BODY).status_code == 404

    def test_update_failure_returns_500(self, admin_client):
        with patched(**on_report(([MOCK_ATTACHMENT_ROW], None))):
            response = admin_client.put(ONE, json=METADATA_BODY)
        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to update attachment"

    def test_report_not_found_returns_404(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            assert admin_client.put(ONE, json=METADATA_BODY).status_code == 404
        mocks["attachments"].assert_not_called()

    @pytest.mark.parametrize("missing", sorted(METADATA_BODY))
    def test_missing_field_returns_422(self, admin_client, missing):
        body = {k: v for k, v in METADATA_BODY.items() if k != missing}
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            assert admin_client.put(ONE, json=body).status_code == 422


# ---------------------------------------------------------------------------
# GET .../attachments
# ---------------------------------------------------------------------------

class TestListAttachments:

    def test_returns_200_with_attachments(self, admin_client):
        second = {**MOCK_ATTACHMENT_ROW, "attachment_id": UUID("4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f70")}
        with patched(**on_report([MOCK_ATTACHMENT_ROW, second])) as mocks:
            response = admin_client.get(BASE)
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "2 attachment(s)"
        assert [a["attachment_id"] for a in body["data"]] == [ATTACHMENT_ID, str(second["attachment_id"])]
        assert all(set(a) == PUBLIC_FIELDS for a in body["data"])
        assert body["data"][0]["attachment_name"] == "North wall"
        assert body["data"][0]["attachment_description"] == "Crack along the north wall footing."
        sql, params = mocks["attachments"].call_args.args
        assert "ORDER BY uploaded_at" in sql
        assert params == (UUID(REPORT_ID),)

    def test_pending_attachments_are_filtered_out(self, admin_client):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])) as mocks:
            admin_client.get(BASE)
        sql = mocks["attachments"].call_args.args[0]
        assert "WHERE report_id = %s AND is_uploaded" in sql

    def test_empty_list(self, admin_client):
        with patched(**on_report([])):
            response = admin_client.get(BASE)
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_works_on_submitted_idr(self, admin_client):
        with patched(**on_report([MOCK_ATTACHMENT_ROW], idrs=[MOCK_SUBMITTED_IDR_ROW])):
            assert admin_client.get(BASE).status_code == 200

    def test_report_not_found_returns_404(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = admin_client.get(BASE)
        assert response.status_code == 404
        mocks["attachments"].assert_not_called()

    def test_query_failure_returns_500(self, admin_client):
        with patched(**on_report(None)):
            assert admin_client.get(BASE).status_code == 500


# ---------------------------------------------------------------------------
# GET .../attachments/{attachment_id}/download-url
# ---------------------------------------------------------------------------

class TestDownloadUrl:
    url = f"{ONE}/download-url"

    def test_returns_signed_url_and_expiry(self, admin_client, bucket):
        before = datetime.now(timezone.utc)
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            response = admin_client.get(self.url)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["download_url"] == SIGNED_URL
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert before + timedelta(seconds=STORAGE_URL_EXPIRY_SECONDS) <= expires_at
        assert expires_at <= datetime.now(timezone.utc) + timedelta(seconds=STORAGE_URL_EXPIRY_SECONDS)

    def test_signs_the_stored_path_under_the_original_name(self, admin_client, bucket):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            admin_client.get(self.url)
        bucket.create_signed_url.assert_called_once_with(
            STORAGE_PATH, STORAGE_URL_EXPIRY_SECONDS, {"download": "site photo.jpg"}
        )

    def test_generates_a_new_url_every_request(self, admin_client, bucket):
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            admin_client.get(self.url)
            admin_client.get(self.url)
        expected = call(STORAGE_PATH, STORAGE_URL_EXPIRY_SECONDS, {"download": "site photo.jpg"})
        assert bucket.create_signed_url.call_args_list == [expected, expected]

    def test_pending_attachment_returns_404(self, admin_client, bucket):
        with patched(**on_report([MOCK_PENDING_ROW])):
            response = admin_client.get(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment upload has not been completed"
        bucket.create_signed_url.assert_not_called()

    def test_attachment_not_found_returns_404(self, admin_client, bucket):
        with patched(**on_report([])):
            response = admin_client.get(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment not found"
        bucket.create_signed_url.assert_not_called()

    def test_report_not_found_returns_404(self, admin_client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            response = admin_client.get(self.url)
        assert response.status_code == 404
        bucket.create_signed_url.assert_not_called()

    def test_storage_failure_returns_502(self, admin_client, bucket):
        bucket.create_signed_url.side_effect = RuntimeError("storage down")
        with patched(**on_report([MOCK_ATTACHMENT_ROW])):
            response = admin_client.get(self.url)
        assert response.status_code == 502
        assert response.json()["detail"] == "Could not create a download link"


# ---------------------------------------------------------------------------
# DELETE .../attachments/{attachment_id}
# ---------------------------------------------------------------------------

class TestDeleteAttachment:

    @pytest.mark.parametrize("row", [MOCK_ATTACHMENT_ROW, MOCK_PENDING_ROW], ids=["uploaded", "pending"])
    def test_returns_204_and_removes_file_then_row(self, admin_client, bucket, row):
        order = []
        bucket.remove.side_effect = lambda paths: order.append(("storage", paths))

        def query(sql, params):
            order.append(("delete" if sql.strip().startswith("DELETE") else "select", params))
            return [row]

        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW]) as mocks:
            mocks["attachments"].side_effect = query
            response = admin_client.delete(ONE)

        assert response.status_code == 204
        assert response.content == b""
        # A pending row's file may be in Storage even though upload-complete never ran.
        assert order == [
            ("select", (UUID(REPORT_ID), UUID(ATTACHMENT_ID))),
            ("storage", [STORAGE_PATH]),
            ("delete", (UUID(REPORT_ID), UUID(ATTACHMENT_ID))),
        ]

    def test_storage_failure_still_deletes_row_and_logs_warning(self, admin_client, bucket, caplog):
        bucket.remove.side_effect = RuntimeError("storage down")
        with caplog.at_level(logging.WARNING, logger="api.services.attachments"):
            with patched(**on_report([MOCK_ATTACHMENT_ROW])) as mocks:
                response = admin_client.delete(ONE)
        assert response.status_code == 204
        assert "DELETE FROM icid.report_attachments" in mocks["attachments"].call_args.args[0]
        assert STORAGE_PATH in caplog.text

    def test_attachment_not_found_returns_404(self, admin_client, bucket):
        with patched(**on_report([])):
            response = admin_client.delete(ONE)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment not found"
        bucket.remove.assert_not_called()

    def test_report_not_found_returns_404(self, admin_client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = admin_client.delete(ONE)
        assert response.status_code == 404
        mocks["attachments"].assert_not_called()
        bucket.remove.assert_not_called()

    def test_submitted_idr_returns_409(self, admin_client, bucket):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW], idr_reports=[MOCK_REPORT_ROW]) as mocks:
            response = admin_client.delete(ONE)
        assert response.status_code == 409
        mocks["attachments"].assert_not_called()
        bucket.remove.assert_not_called()


# ---------------------------------------------------------------------------
# File name sanitizer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, safe", [
    ("site photo.jpg", "site_photo.jpg"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\me\\scan.pdf", "scan.pdf"),
    ("Café résumé.pdf", "Caf__r_sum_.pdf"),
    ("..", "unnamed"),
    ("///", "unnamed"),
    ("", "unnamed"),
    (".jpg", "unnamed.jpg"),
])
def test_sanitize_file_name(raw, safe):
    assert sanitize_file_name(raw) == safe


def test_sanitize_file_name_caps_length_keeping_extension():
    result = sanitize_file_name("a" * 250 + ".jpeg")
    assert len(result) == 200
    assert result.endswith(".jpeg")
