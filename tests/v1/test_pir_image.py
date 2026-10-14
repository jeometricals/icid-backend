import io
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pypdfium2 as pdfium
import pytest
from PIL import Image

from api.services.pir_image import PirImageError, render_pdf_first_page, sniff_type
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/pirs/{pir_id}/image: upload-request, complete, GET, PATCH (scale), DELETE
# Storage is mocked where api/services/pir_image.py calls it; mock rows are dicts, as run_query returns them.
# ---------------------------------------------------------------------------

PIRS = "api.queries.pirs.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
SERVICE = "api.services.pir_image"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
URL = f"/v1/pirs/{PIR_ID}/image"
UPLOAD_PATH = f"pirs/{PIR_ID}/uploads/0123456789abcdef0123456789abcdef"
OLD_PATH = f"pirs/{PIR_ID}/ffffffffffffffffffffffffffffffff.png"
SIGNED = "https://storage.example/pir-images/signed?token=t"
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

PNG = b"\x89PNG\r\n\x1a\n" + b"rest of a png"
JPG = b"\xff\xd8\xff\xe0" + b"rest of a jpeg"
PDF = b"%PDF-1.7\n" + b"rest of a pdf"

PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": date(2026, 9, 30),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}
WITH_IMAGE = {**PIR_ROW, "source_image_url": OLD_PATH, "source_image_scale": 15.0}
ON_PROJECT = [{"?column?": 1}]


def swapped(path, scale=15.0, previous=None) -> dict:
    """
    The row the image-swapping statement returns.
    Takes the new path and scale and the path the PIR had before.
    Returns the PIR row with previous_image_path.
    """
    return {**PIR_ROW, "source_image_url": path, "source_image_scale": scale, "previous_image_path": previous}


class FakeStorage:
    """The Storage calls the service makes, recorded; the upload holds the given bytes."""

    def __init__(self, upload: bytes = PNG, there: bool = True) -> None:
        """
        Start an empty record.
        Takes what the raw upload holds and whether it is there at all.
        Returns nothing.
        """
        self.upload, self.there = upload, there
        self.stored: list[tuple[str, bytes, str]] = []
        self.removed: list[str] = []

    def patches(self):
        """
        Patch the service's Storage calls with this fake.
        Takes nothing.
        Returns the list of patchers, to enter.
        """
        return [
            patch(f"{SERVICE}.create_signed_upload_url", return_value="https://storage.example/upload?token=u"),
            patch(f"{SERVICE}.object_exists", side_effect=lambda bucket, path: self.there),
            patch(f"{SERVICE}.download_file", side_effect=lambda path, bucket: self.upload),
            patch(f"{SERVICE}.upload_file", side_effect=lambda bucket, path, data, kind: self.stored.append((path, data, kind))),
            patch(f"{SERVICE}.create_signed_url", return_value=SIGNED),
            patch(f"{SERVICE}.remove_storage_files", side_effect=lambda paths, bucket: self.removed.extend(paths)),
        ]


@pytest.fixture
def storage():
    """
    A FakeStorage whose patches are in place for one test.
    """
    fake = FakeStorage()
    patchers = fake.patches()
    for patcher in patchers:
        patcher.start()
    yield fake
    for patcher in patchers:
        patcher.stop()


def member():
    """
    A client signed in as a project member who isn't an admin.
    """
    return signed_in(MEMBER_ROW)


class TestUploadRequest:
    @pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "image/jpeg"])
    def test_a_member_gets_a_signed_url_at_a_fresh_path(self, storage, content_type):
        with member() as client, patch(PIRS, return_value=[PIR_ROW]), patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = client.post(f"{URL}/upload-request", json={"content_type": content_type})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["upload_url"].startswith("https://storage.example/upload")
        assert data["upload_path"].startswith(f"pirs/{PIR_ID}/uploads/") and data["expires_in"] > 0

    def test_another_type_is_400(self, storage, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{URL}/upload-request", json={"content_type": "text/plain"})
        assert response.status_code == 400 and response.json() == {"detail": "Unsupported file type"}

    def test_storage_that_cant_sign_is_502(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(f"{SERVICE}.create_signed_upload_url", side_effect=RuntimeError("down")):
            response = admin_client.post(f"{URL}/upload-request", json={"content_type": "image/png"})
        assert response.status_code == 502 and response.json() == {"detail": "Could not create an upload link"}


class TestComplete:
    def test_a_member_completes_a_png_upload(self, storage):
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, side_effect=lambda sql, params: [swapped(params[1], params[2])] if "WITH old" in sql else [PIR_ROW]) as run:
            response = client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH, "scale": 20})
        assert response.status_code == 200 and response.json()["message"] == "PIR image set"
        (path, data, kind), = storage.stored
        assert path.startswith(f"pirs/{PIR_ID}/") and path.endswith(".png") and "/uploads/" not in path
        assert (data, kind) == (PNG, "image/png")
        result = response.json()["data"]
        assert result["source_image_url"] == path and result["source_image_scale"] == 20.0
        assert result["signed_url"] == SIGNED and result["expires_at"]
        assert run.call_args.args[1] == (PIR_ID, path, 20.0)
        assert storage.removed == [UPLOAD_PATH]  # no earlier image to remove

    def test_the_scale_is_15_when_left_out(self, storage, admin_client):
        with patch(PIRS, side_effect=lambda sql, params: [swapped(params[1], params[2])] if "WITH old" in sql else [PIR_ROW]) as run:
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 200 and run.call_args.args[1][2] == 15.0

    def test_a_pdf_is_rendered_to_png(self, storage, admin_client):
        storage.upload = PDF
        with patch(PIRS, side_effect=lambda sql, params: [swapped(params[1])] if "WITH old" in sql else [PIR_ROW]), \
                patch(f"{SERVICE}.render_pdf_first_page", return_value=PNG) as render:
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 200
        render.assert_called_once_with(PDF)
        (path, data, kind), = storage.stored
        assert path.endswith(".png") and (data, kind) == (PNG, "image/png")

    def test_a_jpeg_is_stored_as_it_is(self, storage, admin_client):
        storage.upload = JPG
        with patch(PIRS, side_effect=lambda sql, params: [swapped(params[1])] if "WITH old" in sql else [PIR_ROW]):
            assert admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH}).status_code == 200
        (path, data, kind), = storage.stored
        assert path.endswith(".jpg") and (data, kind) == (JPG, "image/jpeg")

    def test_a_file_that_isnt_a_pdf_png_or_jpeg_is_400_whatever_it_was_declared(self, storage, admin_client):
        storage.upload = b"just some text, declared image/png"
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 400 and response.json() == {"detail": "Unsupported file type"}
        assert storage.stored == [] and storage.removed == [UPLOAD_PATH]
        assert run.call_count == 1  # the PIR read, nothing written

    def test_a_file_too_large_is_413(self, storage, admin_client):
        storage.upload = PNG + b"\0" * (15 * 1024 * 1024)
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 413 and response.json() == {"detail": "File too large"}
        assert storage.stored == [] and storage.removed == [UPLOAD_PATH]

    def test_an_unreadable_pdf_is_400(self, storage, admin_client):
        storage.upload = PDF
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 400 and response.json() == {"detail": "Could not read the PDF"}
        assert storage.stored == [] and storage.removed == [UPLOAD_PATH]

    @pytest.mark.parametrize("scale", [0, -3])
    def test_a_scale_that_isnt_positive_is_400(self, storage, admin_client, scale):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH, "scale": scale})
        assert response.status_code == 400 and response.json() == {"detail": "scale must be a positive number"}
        assert storage.stored == []

    def test_replacing_removes_the_previous_image_after_the_pir_points_at_the_new_one(self, storage, admin_client):
        with patch(PIRS, side_effect=lambda sql, params: [swapped(params[1], previous=OLD_PATH)] if "WITH old" in sql
                   else [WITH_IMAGE]):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        new_path = response.json()["data"]["source_image_url"]
        assert new_path != OLD_PATH and storage.stored[0][0] == new_path
        assert storage.removed == [UPLOAD_PATH, OLD_PATH]

    def test_the_swap_reads_the_previous_path_in_the_same_statement(self, storage, admin_client):
        with patch(PIRS, side_effect=lambda sql, params: [swapped(params[1])] if "WITH old" in sql else [PIR_ROW]) as run:
            admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        one_line = " ".join(run.call_args.args[0].split())
        assert "SELECT id, source_image_url FROM icid.pirs WHERE id = %s FOR UPDATE" in one_line
        assert "(SELECT source_image_url FROM old) AS previous_image_path" in one_line

    @pytest.mark.parametrize("path", [
        f"pirs/{PIR_ID}/ffffffffffffffffffffffffffffffff.png",            # not an upload
        "pirs/00000000-0000-4000-8000-000000000000/uploads/abc",          # another PIR's upload
        f"pirs/{PIR_ID}/uploads/../ffffffffffffffffffffffffffffffff.png",  # out of the folder
        f"pirs/{PIR_ID}/uploads/",
    ])
    def test_a_path_that_isnt_one_of_the_pirs_uploads_is_400(self, storage, admin_client, path):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": path})
        assert response.status_code == 400 and response.json() == {"detail": "upload_path is not an upload of this PIR"}
        assert storage.stored == [] and storage.removed == []

    def test_completing_before_uploading_is_400(self, storage, admin_client):
        storage.there = False
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 400 and response.json() == {"detail": "Upload the file before completing"}

    def test_storage_that_cant_store_is_502_and_the_pir_is_untouched(self, storage, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run, patch(f"{SERVICE}.upload_file", side_effect=RuntimeError("down")):
            response = admin_client.post(f"{URL}/complete", json={"upload_path": UPLOAD_PATH})
        assert response.status_code == 502 and response.json() == {"detail": "Could not store the image"}
        assert run.call_count == 1


class TestGetAndScale:
    def test_a_member_gets_a_signed_link_and_the_scale(self, storage):
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PIRS, return_value=[WITH_IMAGE]):
            response = client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["signed_url"] == SIGNED and data["scale"] == 15.0 and data["expires_at"]

    def test_a_pir_without_an_image_is_404(self, storage, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "No image for this PIR"}

    def test_the_pir_responses_carry_the_path_not_a_signed_link(self, admin_client):
        with patch(PIRS, return_value=[WITH_IMAGE]):
            data = admin_client.get(f"/v1/pirs/{PIR_ID}").json()["data"]
        assert data["source_image_url"] == OLD_PATH and "signed_url" not in data

    def test_a_member_recalibrates_the_scale(self):
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, side_effect=[[WITH_IMAGE], [{**WITH_IMAGE, "source_image_scale": 12.5}]]) as run:
            response = client.patch(URL, json={"scale": 12.5})
        assert response.status_code == 200 and response.json()["data"]["source_image_scale"] == 12.5
        sql, params = run.call_args.args
        assert "source_image_url IS NOT NULL" in sql and params == (12.5, PIR_ID)

    def test_recalibrating_a_pir_without_an_image_is_404(self, admin_client):
        with patch(PIRS, side_effect=[[PIR_ROW], []]):
            response = admin_client.patch(URL, json={"scale": 12.5})
        assert response.status_code == 404 and response.json() == {"detail": "No image for this PIR"}

    @pytest.mark.parametrize("scale", [0, -1])
    def test_a_scale_that_isnt_positive_is_400(self, admin_client, scale):
        with patch(PIRS, return_value=[WITH_IMAGE]) as run:
            assert admin_client.patch(URL, json={"scale": scale}).status_code == 400
        assert run.call_count == 1


class TestDelete:
    def test_a_member_deletes_the_image(self, storage):
        cleared = {**PIR_ROW, "previous_image_path": OLD_PATH}
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, side_effect=[[WITH_IMAGE], [cleared]]) as run:
            response = client.delete(URL)
        assert response.status_code == 204 and response.content == b""
        assert "source_image_url = NULL, source_image_scale = NULL" in run.call_args.args[0]
        assert storage.removed == [OLD_PATH]

    def test_deleting_when_there_is_no_image_is_204_and_removes_nothing(self, storage, admin_client):
        with patch(PIRS, side_effect=[[PIR_ROW], [{**PIR_ROW, "previous_image_path": None}]]):
            assert admin_client.delete(URL).status_code == 204
        assert storage.removed == []


WRITES = [
    ("post", f"{URL}/upload-request", {"content_type": "image/png"}),
    ("post", f"{URL}/complete", {"upload_path": UPLOAD_PATH}),
    ("patch", URL, {"scale": 12}),
    ("delete", URL, None),
]


class TestWhoMayWrite:
    @pytest.mark.parametrize("method,url,body", WRITES)
    def test_a_member_cant_change_a_deleted_pir(self, storage, method, url, body):
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, return_value=[{**WITH_IMAGE, "deleted_at": DELETED}]) as run:
            response = client.request(method, url, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "PIR is deleted"}
        assert run.call_count == 1 and storage.stored == [] and storage.removed == []

    @pytest.mark.parametrize("method,url,body", WRITES)
    def test_a_member_cant_change_a_pir_of_a_deleted_property(self, storage, method, url, body):
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[{**PROPERTY_ROW, "deleted_at": DELETED}]), \
                patch(PIRS, return_value=[WITH_IMAGE]) as run:
            response = client.request(method, url, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "Property is deleted"}
        assert run.call_count == 1 and storage.stored == []

    def test_an_admin_changes_a_deleted_pir(self, storage, admin_client):
        cleared = {**PIR_ROW, "deleted_at": DELETED, "previous_image_path": OLD_PATH}
        with patch(PIRS, side_effect=[[{**WITH_IMAGE, "deleted_at": DELETED}], [cleared]]), patch(PROPS) as props:
            assert admin_client.delete(URL).status_code == 204
        props.assert_not_called()

    @pytest.mark.parametrize("method,url,body", WRITES + [("get", URL, None)])
    def test_a_user_who_isnt_on_the_project_is_forbidden(self, storage, method, url, body):
        with member() as client, patch(PROJECTS, return_value=[]), patch(PIRS, return_value=[WITH_IMAGE]) as run:
            response = client.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert run.call_count == 1

    @pytest.mark.parametrize("method,url,body", WRITES)
    def test_a_demo_user_cant_write_on_their_own_project(self, storage, demo_client, method, url, body):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(PIRS, return_value=[WITH_IMAGE]) as run:
            response = demo_client.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        assert run.call_count == 1

    def test_a_demo_user_reads_their_own_projects_image(self, storage, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(PIRS, return_value=[WITH_IMAGE]):
            assert demo_client.get(URL).status_code == 200

    @pytest.mark.parametrize("method,url,body", WRITES + [("get", URL, None)])
    def test_a_demo_user_gets_404_on_another_project(self, storage, demo_client, method, url, body):
        with patch(PROJECTS, return_value=[]), patch(PIRS, return_value=[WITH_IMAGE]):
            response = demo_client.request(method, url, json=body)
        assert response.status_code == 404 and response.json() == {"detail": "PIR not found"}


class TestFileHandling:
    @pytest.mark.parametrize("data,kind", [
        (PDF, "application/pdf"), (PNG, "image/png"), (JPG, "image/jpeg"), (b"GIF89a", None), (b"", None),
    ])
    def test_a_file_is_told_by_its_first_bytes(self, data, kind):
        assert sniff_type(data) == kind

    def test_a_real_pdf_renders_its_first_page_at_the_dpi(self):
        document = pdfium.PdfDocument.new()
        document.new_page(144, 72)   # 2 in x 1 in
        document.new_page(72, 72)
        buffer = io.BytesIO()
        document.save(buffer)
        png = render_pdf_first_page(buffer.getvalue(), dpi=100)
        assert sniff_type(png) == "image/png"
        assert Image.open(io.BytesIO(png)).size == (200, 100)

    def test_a_broken_pdf_is_refused(self):
        with pytest.raises(PirImageError) as refused:
            render_pdf_first_page(b"%PDF-1.7 not really")
        assert (refused.value.status_code, refused.value.detail) == (400, "Could not read the PDF")
