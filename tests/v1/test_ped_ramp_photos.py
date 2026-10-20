import io
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from PIL import Image

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# Ped ramp photos (Pedramp Phase 3, S4): /v1/ped-ramp-inspections/{id}/photos, /v1/ped-ramp-tifs/{id}/photos and
# /v1/ped-ramp-photos/{id}. Mock rows are dicts, as run_query returns them; Storage is patched in the service.
# ---------------------------------------------------------------------------

INSPECTIONS = "api.queries.ped_ramp_inspections.run_query"
PHOTOS = "api.queries.ped_ramp_photos.run_query"
PROJECTS = "api.queries.projects.run_query"
SERVICE = "api.services.ped_ramp_photos"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
INSPECTION_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
TIF_ID = UUID("5a4b5c6d-7e8f-4a9b-8c1d-2e3f4a5b6c7d")
RAMP_ID = UUID("6b5c6d7e-8f9a-4b0c-9d2e-3f4a5b6c7d8e")
PHOTO_ID = UUID("7c6d7e8f-9a0b-4c1d-8e3f-4a5b6c7d8e9f")
CREATED = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
STAGED = f"staging/{INSPECTION_ID}/abc.jpg"
TIF_STAGED = f"staging/{TIF_ID}/abc.png"

INSPECTION = {"id": INSPECTION_ID, "corner_id": CORNER_ID, "project_id": "HWS0023", "north_south_street": "3rd Ave",
              "east_west_street": "38 Street", "corner_direction": "NE", "status": "draft", "tif_count": 0,
              "draft_tif_count": 0}
TIF = {"id": TIF_ID, "property_id": RAMP_ID, "inspection_id": INSPECTION_ID, "status": "draft",
       "project_id": "HWS0023", "inspection_status": "draft"}
PHOTO = {"id": PHOTO_ID, "inspection_id": INSPECTION_ID, "tif_id": None, "property_id": RAMP_ID, "kind": "photo",
         "photo_number": 3, "caption": "Looking north", "storage_path": f"inspections/{INSPECTION_ID}/{PHOTO_ID}.jpg",
         "content_type": "image/jpeg", "byte_size": 1234, "uploaded_at": CREATED, "uploaded_by": ADMIN_ID}
PHOTO_WITH_PARENT = {**PHOTO, "project_id": "HWS0023", "tif_status": None, "inspection_status": "draft"}
TIF_PHOTO = {**PHOTO_WITH_PARENT, "inspection_id": None, "tif_id": TIF_ID, "tif_status": "draft",
             "storage_path": f"tifs/{TIF_ID}/{PHOTO_ID}.jpg"}
ADDED = {"ramp_ok": True, "photo_count": 2, "number_taken": False, **PHOTO}
REFUSED = {"ramp_ok": True, "photo_count": 2, "number_taken": False, **{key: None for key in PHOTO}}


def _jpeg() -> bytes:
    """
    Make a small JPEG.
    Takes nothing.
    Returns its bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeStorage:
    """Patches the service's Storage calls and records what they did."""

    def __init__(self, upload: bytes = b"", there: bool = True) -> None:
        """
        Set up what the staged upload holds.
        Takes its bytes and whether it is there.
        Returns nothing.
        """
        self.upload = upload
        self.there = there
        self.stored: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self._stack = ExitStack()

    def __enter__(self) -> "FakeStorage":
        """
        Start the patches.
        Takes nothing.
        Returns itself.
        """
        for name, kwargs in {
            "create_signed_upload_url": {"return_value": "https://storage.example/upload?token=u"},
            "object_exists": {"side_effect": lambda bucket, path: self.there},
            "download_file": {"side_effect": lambda path, bucket: self.upload},
            "upload_file": {"side_effect": lambda bucket, path, data, kind: self.stored.append((path, kind))},
            "create_signed_url": {"side_effect": lambda path, expires, name, bucket: f"https://storage.example/{path}"},
            "remove_storage_files": {"side_effect": lambda paths, bucket: self.removed.extend(paths)},
        }.items():
            self._stack.enter_context(patch(f"{SERVICE}.{name}", **kwargs))
        return self

    def __exit__(self, *exc: object) -> None:
        """
        Stop the patches.
        Takes the exception details.
        Returns nothing.
        """
        self._stack.close()


class TestInspectionUploadRequest:
    URL = f"/v1/ped-ramp-inspections/{INSPECTION_ID}/photos/upload-request"

    def test_a_member_gets_a_staging_path_for_a_heic(self):
        with signed_in(MEMBER_ROW) as member, patch(INSPECTIONS, return_value=[INSPECTION]), \
             patch(PROJECTS, return_value=[{"role": "inspector"}]), \
             patch(PHOTOS, return_value=[{"is_ramp": True}]) as run, FakeStorage():
            response = member.post(self.URL, json={"property_id": str(RAMP_ID), "file_name": "IMG_0001.HEIC"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["staging_path"].startswith(f"staging/{INSPECTION_ID}/") and data["staging_path"].endswith(".heic")
        sql, params = run.call_args.args
        assert "p.kind = 'ped_ramp' AND p.deleted_at IS NULL" in sql and params == (INSPECTION_ID, RAMP_ID)

    def test_a_ramp_at_another_corner_is_400(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(PHOTOS, return_value=[{"is_ramp": False}]):
            response = admin_client.post(self.URL, json={"property_id": str(RAMP_ID)})
        assert (response.status_code, response.json()) == (400, {"detail": "That ramp isn't at this inspection's corner"})

    def test_a_name_that_isnt_a_photos_is_400(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(PHOTOS, return_value=[{"is_ramp": True}]), FakeStorage():
            response = admin_client.post(self.URL, json={"property_id": str(RAMP_ID), "file_name": "plan.pdf"})
        assert response.status_code == 400 and "JPEG, PNG or HEIC" in response.json()["detail"]

    def test_the_lock(self, admin_client):
        with patch(INSPECTIONS, return_value=[{**INSPECTION, "status": "approved"}]), patch(PHOTOS) as run:
            response = admin_client.post(self.URL, json={"property_id": str(RAMP_ID)})
        assert (response.status_code, response.json()) == (409, {"detail": "Inspection is approved — no further edits"})
        run.assert_not_called()
        with signed_in(MEMBER_ROW) as member, patch(INSPECTIONS, return_value=[{**INSPECTION, "status": "submitted"}]), \
             patch(PROJECTS, return_value=[{"role": "inspector"}]):
            response = member.post(self.URL, json={"property_id": str(RAMP_ID)})
        assert (response.status_code, response.json()) == (403, {"detail": "Inspection is submitted — only the RE can edit"})
        with signed_in(MEMBER_ROW) as re_user, patch(INSPECTIONS, return_value=[{**INSPECTION, "status": "submitted"}]), \
             patch(PROJECTS, return_value=[{"role": "re"}]), patch(PHOTOS, return_value=[{"is_ramp": True}]), FakeStorage():
            assert re_user.post(self.URL, json={"property_id": str(RAMP_ID)}).status_code == 200

    def test_a_demo_user_and_someone_off_the_project_cant(self, demo_client):
        with patch(INSPECTIONS, return_value=[{**INSPECTION, "project_id": "DEMO01"}]), \
             patch(PROJECTS, return_value=[{"role": "inspector"}]), patch(PHOTOS) as run:
            assert demo_client.post(self.URL, json={"property_id": str(RAMP_ID)}).status_code == 403
        with signed_in(MEMBER_ROW) as stranger, patch(INSPECTIONS, return_value=[INSPECTION]), \
             patch(PROJECTS, return_value=[]):
            assert stranger.post(self.URL, json={"property_id": str(RAMP_ID)}).status_code == 403
        run.assert_not_called()


class TestInspectionComplete:
    URL = f"/v1/ped-ramp-inspections/{INSPECTION_ID}/photos"
    BODY = {"storage_path": STAGED, "property_id": str(RAMP_ID), "photo_number": 3, "caption": "  Looking north "}

    def test_a_jpeg_is_stored_and_added(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(PHOTOS, return_value=[ADDED]) as run, \
             FakeStorage(_jpeg()) as storage:
            response = admin_client.post(self.URL, json=self.BODY)
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["photo_number"] == 3 and data["image_url"].startswith("https://storage.example/")
        assert "storage_path" not in data
        (path, kind), = storage.stored
        assert path.startswith(f"inspections/{INSPECTION_ID}/") and path.endswith(".jpg") and kind == "image/jpeg"
        assert storage.removed == [STAGED]
        sql, params = run.call_args.args
        assert "parent.photo_count < 20 AND NOT parent.number_taken" in sql and "ON CONFLICT DO NOTHING" in sql
        assert params[:3] == (RAMP_ID, 3, INSPECTION_ID)
        assert params[4:9] == (RAMP_ID, 3, "Looking north", path, "image/jpeg")

    def test_a_taken_number_is_409_and_keeps_the_upload(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), \
             patch(PHOTOS, return_value=[{**REFUSED, "number_taken": True}]), FakeStorage(_jpeg()) as storage:
            response = admin_client.post(self.URL, json=self.BODY)
        assert (response.status_code, response.json()) == (409, {"detail": "Photo number 3 is already used on this form"})
        assert storage.removed == [storage.stored[0][0]]

    def test_the_twenty_first_photo_is_400(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), \
             patch(PHOTOS, return_value=[{**REFUSED, "photo_count": 20}]), FakeStorage(_jpeg()):
            response = admin_client.post(self.URL, json=self.BODY)
        assert (response.status_code, response.json()) == (400, {"detail": "A form takes at most 20 photos"})

    def test_a_ramp_not_at_the_corner_is_400(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), \
             patch(PHOTOS, return_value=[{**REFUSED, "ramp_ok": False}]), FakeStorage(_jpeg()):
            response = admin_client.post(self.URL, json=self.BODY)
        assert (response.status_code, response.json()) == (400, {"detail": "That ramp isn't at this inspection's corner"})

    def test_approved_meanwhile_is_409(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(PHOTOS, return_value=[]), FakeStorage(_jpeg()):
            response = admin_client.post(self.URL, json=self.BODY)
        assert (response.status_code, response.json()) == (409, {"detail": "Inspection is approved — no further edits"})

    def test_a_file_that_isnt_a_photo_is_400_and_removed(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(PHOTOS) as run, \
             FakeStorage(b"%PDF-1.7 ...") as storage:
            response = admin_client.post(self.URL, json=self.BODY)
        assert response.status_code == 400 and "JPEG, PNG or HEIC" in response.json()["detail"]
        assert storage.removed == [STAGED] and storage.stored == []
        run.assert_not_called()

    def test_too_large_is_413(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(PHOTOS) as run, \
             FakeStorage(b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024)) as storage:
            response = admin_client.post(self.URL, json=self.BODY)
        assert (response.status_code, response.json()) == (413, {"detail": "Photo too large: at most 10 MB"})
        assert storage.removed == [STAGED]
        run.assert_not_called()

    def test_a_path_outside_its_staging_or_not_uploaded_is_400(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), FakeStorage(_jpeg()):
            response = admin_client.post(self.URL, json={**self.BODY, "storage_path": f"staging/{TIF_ID}/abc.jpg"})
        assert response.json() == {"detail": "storage_path is not an upload of this form"}
        with patch(INSPECTIONS, return_value=[INSPECTION]), FakeStorage(_jpeg(), there=False):
            response = admin_client.post(self.URL, json=self.BODY)
        assert response.json() == {"detail": "Upload the photo before completing"}


class TestInspectionList:
    def test_a_member_lists_with_signed_urls(self):
        with signed_in(MEMBER_ROW) as member, patch(INSPECTIONS, return_value=[{**INSPECTION, "status": "approved"}]), \
             patch(PROJECTS, return_value=[{"role": "oe"}]), patch(PHOTOS, return_value=[PHOTO]) as run, FakeStorage():
            response = member.get(f"/v1/ped-ramp-inspections/{INSPECTION_ID}/photos")
        assert response.status_code == 200
        (photo,) = response.json()["data"]
        assert photo["image_url"] == f"https://storage.example/{PHOTO['storage_path']}" and photo["caption"] == "Looking north"
        sql, params = run.call_args.args
        assert "WHERE f.inspection_id = %s" in sql and params == (INSPECTION_ID,)


class TestTifPhotos:
    URL = f"/v1/ped-ramp-tifs/{TIF_ID}/photos"

    def test_a_layout_is_numbered_1(self, admin_client):
        added = {**ADDED, "inspection_id": None, "tif_id": TIF_ID, "kind": "layout", "photo_number": 1}
        with patch(PHOTOS, side_effect=[[TIF], [added]]) as run, FakeStorage(b"\x89PNG\r\n\x1a\n...") as storage:
            response = admin_client.post(self.URL, json={"storage_path": TIF_STAGED, "kind": "layout", "photo_number": 4})
        assert response.status_code == 201 and response.json()["message"] == "Site layout added"
        (path, kind), = storage.stored
        assert path.startswith(f"tifs/{TIF_ID}/") and path.endswith(".png") and kind == "image/png"
        sql, params = run.call_args.args
        assert "t.status = 'draft' AND i.status <> 'approved'" in sql
        assert params[:3] == ("layout", 1, TIF_ID) and params[4:6] == ("layout", 1)

    def test_a_second_layout_is_409(self, admin_client):
        with patch(PHOTOS, side_effect=[[TIF], [{**REFUSED, "number_taken": True}]]), FakeStorage(_jpeg()):
            response = admin_client.post(self.URL, json={"storage_path": f"staging/{TIF_ID}/a.jpg", "kind": "layout"})
        assert (response.status_code, response.json()) == (409, {"detail": "This TIF already has a site layout"})

    def test_a_photo_needs_a_number(self, admin_client):
        with patch(PHOTOS, return_value=[TIF]):
            response = admin_client.post(self.URL, json={"storage_path": TIF_STAGED})
        assert (response.status_code, response.json()) == (400, {"detail": "photo_number is required"})

    def test_an_approved_tif_or_inspection_is_409(self, admin_client):
        with patch(PHOTOS, return_value=[{**TIF, "status": "approved"}]):
            response = admin_client.post(f"{self.URL}/upload-request", json={})
        assert (response.status_code, response.json()) == (409, {"detail": "TIF is approved — no further edits"})
        with patch(PHOTOS, return_value=[{**TIF, "inspection_status": "approved"}]):
            response = admin_client.post(f"{self.URL}/upload-request", json={})
        assert response.json() == {"detail": "Inspection is approved — no further edits"}

    def test_a_member_uploads_to_a_draft_whatever_the_inspection_says(self):
        with signed_in(MEMBER_ROW) as member, patch(PHOTOS, return_value=[{**TIF, "inspection_status": "submitted"}]), \
             patch(PROJECTS, return_value=[{"role": "inspector"}]), FakeStorage():
            response = member.post(f"{self.URL}/upload-request", json={"file_name": "site.png", "kind": "layout"})
        assert response.status_code == 200 and response.json()["data"]["staging_path"].endswith(".png")

    def test_an_unknown_tif_is_404_and_a_list_reads_its_photos(self, admin_client):
        with patch(PHOTOS, return_value=[]):
            assert admin_client.get(self.URL).status_code == 404
        with patch(PHOTOS, side_effect=[[TIF], [TIF_PHOTO]]) as run, FakeStorage():
            response = admin_client.get(self.URL)
        assert response.status_code == 200 and response.json()["data"][0]["tif_id"] == str(TIF_ID)
        assert "WHERE f.tif_id = %s" in run.call_args.args[0]


class TestEdit:
    URL = f"/v1/ped-ramp-photos/{PHOTO_ID}"

    def test_renumber_and_clear_the_caption(self, admin_client):
        changed = {"number_taken": False, **PHOTO, "photo_number": 5, "caption": None}
        with patch(PHOTOS, side_effect=[[PHOTO_WITH_PARENT], [changed]]) as run, FakeStorage():
            response = admin_client.patch(self.URL, json={"photo_number": 5, "caption": "  "})
        assert response.status_code == 200
        assert response.json()["data"]["photo_number"] == 5 and response.json()["data"]["caption"] is None
        sql, params = run.call_args.args
        assert "WHERE p.id = target.id AND NOT target.number_taken" in sql
        assert params == (5, PHOTO_ID, 5, True, None)

    def test_a_caption_alone_keeps_the_number(self, admin_client):
        with patch(PHOTOS, side_effect=[[PHOTO_WITH_PARENT], [{"number_taken": False, **PHOTO}]]) as run, FakeStorage():
            assert admin_client.patch(self.URL, json={"caption": "East"}).status_code == 200
        assert run.call_args.args[1] == (None, PHOTO_ID, None, True, "East")

    def test_a_taken_number_is_409(self, admin_client):
        refused = {"number_taken": True, **{key: None for key in PHOTO}}
        with patch(PHOTOS, side_effect=[[PHOTO_WITH_PARENT], [refused]]):
            response = admin_client.patch(self.URL, json={"photo_number": 2})
        assert (response.status_code, response.json()) == (409, {"detail": "Photo number 2 is already used on this form"})

    def test_a_layout_keeps_number_1(self, admin_client):
        with patch(PHOTOS, return_value=[{**TIF_PHOTO, "kind": "layout", "photo_number": 1}]):
            response = admin_client.patch(self.URL, json={"photo_number": 2})
        assert (response.status_code, response.json()) == (400, {"detail": "The site layout is always number 1"})

    def test_the_parents_lock(self, admin_client):
        with patch(PHOTOS, return_value=[{**PHOTO_WITH_PARENT, "inspection_status": "approved"}]) as run:
            assert admin_client.patch(self.URL, json={"caption": "x"}).status_code == 409
        assert run.call_count == 1
        with patch(PHOTOS, return_value=[{**TIF_PHOTO, "tif_status": "approved"}]):
            response = admin_client.delete(self.URL)
        assert (response.status_code, response.json()) == (409, {"detail": "TIF is approved — no further edits"})


class TestDelete:
    URL = f"/v1/ped-ramp-photos/{PHOTO_ID}"

    def test_the_row_then_its_file(self, admin_client):
        deleted = {"id": PHOTO_ID, "storage_path": PHOTO["storage_path"]}
        with patch(PHOTOS, side_effect=[[PHOTO_WITH_PARENT], [deleted]]) as run, FakeStorage() as storage:
            assert admin_client.delete(self.URL).status_code == 204
        assert "DELETE FROM icid.ped_ramp_photos" in run.call_args.args[0]
        assert storage.removed == [PHOTO["storage_path"]]

    def test_a_demo_user_cant_and_a_missing_photo_is_404(self, admin_client, demo_client):
        with patch(PHOTOS, return_value=[{**PHOTO_WITH_PARENT, "project_id": "DEMO01"}]), \
             patch(PROJECTS, return_value=[{"role": "inspector"}]):
            assert demo_client.delete(self.URL).status_code == 403
        with patch(PHOTOS, return_value=[]):
            assert admin_client.delete(self.URL).json() == {"detail": "Photo not found"}
