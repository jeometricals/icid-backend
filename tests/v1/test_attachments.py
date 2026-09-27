import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from psycopg.errors import ForeignKeyViolation

from api.core.config import STORAGE_URL_EXPIRY_SECONDS
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

BASE = f"/v1/idrs/{IDR_ID}/reports/{REPORT_ID}/attachments"
ONE = f"{BASE}/{ATTACHMENT_ID}"

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

MOCK_USER_ROW = {
    "user_id": UUID(UPLOADER_UUID),
    "email": "inspector@example.com",
    "first_name": "Ada",
    "last_name": "Inspector",
    "phone_number": None,
    "employer": "C1",
}
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
}

PUBLIC_FIELDS = {"attachment_id", "file_name", "file_type", "file_size_bytes", "uploaded_by", "uploaded_at"}

JPEG = ("site photo.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")
FORM = {"uploaded_by": UPLOADER_UUID}


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
    Build the query results for an upload that passes every check.
    Takes any patched() keyword to override.
    Returns the keyword dict for patched().
    """
    results = {
        "idrs": [MOCK_IDR_ROW],
        "idr_reports": [MOCK_REPORT_ROW],
        "users": [MOCK_USER_ROW],
        "projects": [MOCK_ASSIGNMENT_ROW],
        "attachments": [MOCK_ATTACHMENT_ROW],
    }
    return {**results, **overrides}


# ---------------------------------------------------------------------------
# POST .../attachments
# ---------------------------------------------------------------------------

class TestUploadAttachment:

    def test_returns_201_with_metadata(self, client):
        with patched(**upload_ok()):
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "success"
        assert body["data"]["attachment_id"] == ATTACHMENT_ID
        assert body["data"]["file_name"] == "site photo.jpg"

    def test_response_exposes_only_public_fields(self, client):
        with patched(**upload_ok()):
            data = client.post(BASE, files={"file": JPEG}, data=FORM).json()["data"]
        assert set(data) == PUBLIC_FIELDS
        assert "storage_path" not in data

    def test_uploads_to_storage_then_inserts_metadata(self, client, bucket):
        # Pin the generated attachment id so the exact storage path is known up front.
        with patch("api.services.attachments.uuid4", return_value=UUID(ATTACHMENT_ID)), \
             patched(**upload_ok()) as mocks:
            client.post(BASE, files={"file": JPEG}, data=FORM)

        # {report_id}/{attachment_id}_{sanitized name} ("site photo.jpg" -> "site_photo.jpg"),
        # the exact bytes sent, and the declared content type.
        bucket.upload.assert_called_once_with(
            f"{REPORT_ID}/{ATTACHMENT_ID}_site_photo.jpg",
            JPEG[1],
            {"content-type": "image/jpeg", "upsert": "false"},
        )

        mocks["attachments"].assert_called_once()
        sql, params = mocks["attachments"].call_args.args
        assert "INSERT INTO icid.report_attachments" in sql
        assert params == (
            UUID(ATTACHMENT_ID),
            UUID(REPORT_ID),
            "site photo.jpg",  # original name kept for display and downloads
            "image/jpeg",
            len(JPEG[1]),
            f"{REPORT_ID}/{ATTACHMENT_ID}_site_photo.jpg",
            UUID(UPLOADER_UUID),
        )

    def test_accepts_pdf(self, client, bucket):
        with patched(**upload_ok()):
            response = client.post(BASE, files={"file": ("plan.pdf", b"%PDF-1.7", "application/pdf")}, data=FORM)
        assert response.status_code == 201
        assert bucket.upload.call_args.args[2]["content-type"] == "application/pdf"

    def test_file_too_large_returns_413(self, client, bucket):
        too_big = ("big.jpg", b"\0" * (MAX_FILE_SIZE_BYTES + 1), "image/jpeg")
        with patched(**upload_ok()) as mocks:
            response = client.post(BASE, files={"file": too_big}, data=FORM)
        assert response.status_code == 413
        assert response.json()["detail"] == "File is larger than the 10 MB limit"
        bucket.upload.assert_not_called()
        mocks["attachments"].assert_not_called()

    def test_file_at_limit_is_accepted(self, client):
        at_limit = ("big.jpg", b"\0" * MAX_FILE_SIZE_BYTES, "image/jpeg")
        with patched(**upload_ok()):
            response = client.post(BASE, files={"file": at_limit}, data=FORM)
        assert response.status_code == 201

    @pytest.mark.parametrize("file_type", ["text/plain", "image/svg+xml", "application/zip"])
    def test_unsupported_type_returns_415(self, client, bucket, file_type):
        with patched(**upload_ok()):
            response = client.post(BASE, files={"file": ("x.bin", b"data", file_type)}, data=FORM)
        assert response.status_code == 415
        assert response.json()["detail"].startswith("Unsupported file type")
        bucket.upload.assert_not_called()

    def test_auto_generated_general_returns_400(self, client, bucket):
        with patched(**upload_ok(idr_reports=[MOCK_AUTO_GENERAL_ROW])) as mocks:
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Attachments cannot be added to auto-generated General reports; edit child reports instead."
        )
        bucket.upload.assert_not_called()
        mocks["attachments"].assert_not_called()

    def test_unknown_uploader_returns_400(self, client, bucket):
        with patched(**upload_ok(users=[])) as mocks:
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 400
        assert response.json()["detail"] == "Uploader user not found."
        bucket.upload.assert_not_called()
        mocks["projects"].assert_not_called()

    def test_uploader_fk_violation_returns_400_and_removes_file(self, client, bucket):
        with patched(**upload_ok(attachments=UploaderFkViolation("violates report_attachments_uploaded_by_fkey"))):
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 400
        assert response.json()["detail"] == "Uploader user not found."
        bucket.remove.assert_called_once_with([bucket.upload.call_args.args[0]])

    def test_uploader_not_on_project_returns_403(self, client, bucket):
        with patched(**upload_ok(projects=[])):
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 403
        assert response.json()["detail"] == "Uploader is not assigned to this project"
        bucket.upload.assert_not_called()

    def test_report_not_found_returns_404(self, client, bucket):
        with patched(**upload_ok(idr_reports=[])):
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 404
        assert response.json()["detail"] == "Report not found in this IDR"
        bucket.upload.assert_not_called()

    def test_idr_not_found_returns_404(self, client):
        with patched(**upload_ok(idrs=[])) as mocks:
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_submitted_idr_returns_409(self, client, bucket):
        with patched(**upload_ok(idrs=[MOCK_SUBMITTED_IDR_ROW])):
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 409
        bucket.upload.assert_not_called()

    def test_empty_file_returns_400(self, client, bucket):
        with patched(**upload_ok()):
            response = client.post(BASE, files={"file": ("empty.jpg", b"", "image/jpeg")}, data=FORM)
        assert response.status_code == 400
        assert response.json()["detail"] == "File is empty"
        bucket.upload.assert_not_called()

    def test_storage_upload_failure_returns_502_without_insert(self, client, bucket):
        bucket.upload.side_effect = RuntimeError("storage down")
        with patched(**upload_ok()) as mocks:
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 502
        assert response.json()["detail"] == "Could not store the file"
        mocks["attachments"].assert_not_called()

    def test_insert_failure_returns_500_and_removes_file(self, client, bucket):
        with patched(**upload_ok(attachments=None)):
            response = client.post(BASE, files={"file": JPEG}, data=FORM)
        assert response.status_code == 500
        bucket.remove.assert_called_once_with([bucket.upload.call_args.args[0]])

    def test_missing_file_or_uploader_returns_422(self, client):
        with patched(**upload_ok()):
            assert client.post(BASE, data=FORM).status_code == 422
            assert client.post(BASE, files={"file": JPEG}).status_code == 422


# ---------------------------------------------------------------------------
# GET .../attachments
# ---------------------------------------------------------------------------

class TestListAttachments:

    def test_returns_200_with_attachments(self, client):
        second = {**MOCK_ATTACHMENT_ROW, "attachment_id": UUID("4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f70")}
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW, second]) as mocks:
            response = client.get(BASE)
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "2 attachment(s)"
        assert [a["attachment_id"] for a in body["data"]] == [ATTACHMENT_ID, str(second["attachment_id"])]
        assert all(set(a) == PUBLIC_FIELDS for a in body["data"])
        sql, params = mocks["attachments"].call_args.args
        assert "ORDER BY uploaded_at" in sql
        assert params == (UUID(REPORT_ID),)

    def test_empty_list(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[]):
            response = client.get(BASE)
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_works_on_submitted_idr(self, client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW]):
            assert client.get(BASE).status_code == 200

    def test_report_not_found_returns_404(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = client.get(BASE)
        assert response.status_code == 404
        mocks["attachments"].assert_not_called()

    def test_query_failure_returns_500(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=None):
            assert client.get(BASE).status_code == 500


# ---------------------------------------------------------------------------
# GET .../attachments/{attachment_id}/download-url
# ---------------------------------------------------------------------------

class TestDownloadUrl:
    url = f"{ONE}/download-url"

    def test_returns_signed_url_and_expiry(self, client, bucket):
        before = datetime.now(timezone.utc)
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW]):
            response = client.get(self.url)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["download_url"] == SIGNED_URL
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert before + timedelta(seconds=STORAGE_URL_EXPIRY_SECONDS) <= expires_at
        assert expires_at <= datetime.now(timezone.utc) + timedelta(seconds=STORAGE_URL_EXPIRY_SECONDS)

    def test_signs_the_stored_path_under_the_original_name(self, client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW]):
            client.get(self.url)
        bucket.create_signed_url.assert_called_once_with(
            STORAGE_PATH, STORAGE_URL_EXPIRY_SECONDS, {"download": "site photo.jpg"}
        )

    def test_generates_a_new_url_every_request(self, client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW]):
            client.get(self.url)
            client.get(self.url)
        assert bucket.create_signed_url.call_count == 2

    def test_attachment_not_found_returns_404(self, client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[]):
            response = client.get(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment not found"
        bucket.create_signed_url.assert_not_called()

    def test_report_not_found_returns_404(self, client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            response = client.get(self.url)
        assert response.status_code == 404
        bucket.create_signed_url.assert_not_called()

    def test_storage_failure_returns_502(self, client, bucket):
        bucket.create_signed_url.side_effect = RuntimeError("storage down")
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW]):
            response = client.get(self.url)
        assert response.status_code == 502
        assert response.json()["detail"] == "Could not create a download link"


# ---------------------------------------------------------------------------
# DELETE .../attachments/{attachment_id}
# ---------------------------------------------------------------------------

class TestDeleteAttachment:

    def test_returns_204_and_removes_file_then_row(self, client, bucket):
        order = []
        bucket.remove.side_effect = lambda paths: order.append(("storage", paths))

        def query(sql, params):
            order.append(("delete" if sql.strip().startswith("DELETE") else "select", params))
            return [MOCK_ATTACHMENT_ROW]

        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW]) as mocks:
            mocks["attachments"].side_effect = query
            response = client.delete(ONE)

        assert response.status_code == 204
        assert response.content == b""
        assert order == [
            ("select", (UUID(REPORT_ID), UUID(ATTACHMENT_ID))),
            ("storage", [STORAGE_PATH]),
            ("delete", (UUID(REPORT_ID), UUID(ATTACHMENT_ID))),
        ]

    def test_storage_failure_still_deletes_row_and_logs_warning(self, client, bucket, caplog):
        bucket.remove.side_effect = RuntimeError("storage down")
        with caplog.at_level(logging.WARNING, logger="api.services.attachments"):
            with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[MOCK_ATTACHMENT_ROW]) as mocks:
                response = client.delete(ONE)
        assert response.status_code == 204
        assert "DELETE FROM icid.report_attachments" in mocks["attachments"].call_args.args[0]
        assert STORAGE_PATH in caplog.text

    def test_attachment_not_found_returns_404(self, client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_REPORT_ROW], attachments=[]):
            response = client.delete(ONE)
        assert response.status_code == 404
        assert response.json()["detail"] == "Attachment not found"
        bucket.remove.assert_not_called()

    def test_report_not_found_returns_404(self, client, bucket):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = client.delete(ONE)
        assert response.status_code == 404
        mocks["attachments"].assert_not_called()
        bucket.remove.assert_not_called()

    def test_submitted_idr_returns_409(self, client, bucket):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW], idr_reports=[MOCK_REPORT_ROW]) as mocks:
            response = client.delete(ONE)
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
