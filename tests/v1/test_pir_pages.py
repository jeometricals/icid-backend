import io
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pypdfium2 as pdfium
import pytest
from PIL import Image

from api.services.pir_image import image_to_page, render_pdf_pages, sniff_type
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# A PIR's pages: /v1/pirs/{pir_id}/image/upload-request and /complete, /v1/pirs/{pir_id}/pages,
# /v1/pir-pages/{pir_page_id}/scale and /rotation, and R1d's deprecated /v1/pirs/{pir_id}/image routes.
# The page table is a small in-memory stand-in (FakePages) behind run_query; Storage is mocked where
# api/services/pir_image.py calls it; PDFs are rendered for real, from tests/fixtures/sample_3page.pdf.
# ---------------------------------------------------------------------------

PIRS = "api.queries.pirs.run_query"
PAGES = "api.queries.pir_pages.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
SERVICE = "api.services.pir_image"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_3page.pdf"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
MEMBER_ID = MEMBER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
PIR_URL = f"/v1/pirs/{PIR_ID}"
STAGED = f"staging/{PIR_ID}/0123456789abcdef0123456789abcdef.pdf"
SIGNED = "https://storage.example/pir-images/signed?token=t"
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
ON_PROJECT = [{"?column?": 1}]

PNG = b"\x89PNG\r\n\x1a\n"
PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": date(2026, 9, 30),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}


def image_bytes(kind: str, size=(400, 300)) -> bytes:
    """
    Make a small real image.
    Takes the format ('PNG' or 'JPEG') and the size in pixels.
    Returns its bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(buffer, format=kind)
    return buffer.getvalue()


def pdf_bytes(*sizes) -> bytes:
    """
    Make a PDF with blank pages of the given sizes, in points.
    Takes each page's (width, height).
    Returns the PDF's bytes.
    """
    document = pdfium.PdfDocument.new()
    for width, height in sizes:
        document.new_page(width, height)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class FakePages:
    """icid.pir_pages for one PIR, answering the statements api/queries/pir_pages.py runs; it records them too."""

    def __init__(self, pages: int = 0) -> None:
        """
        Start with the given number of live pages.
        Takes how many.
        Returns nothing.
        """
        self.rows: list[dict] = []
        self.calls: list[tuple[str, tuple]] = []
        if pages:
            self._insert([{"page_number": n, "image_path": f"pirs/{PIR_ID}/old/page-{n}.png", "image_mime": "image/png",
                           "width_px": 1275, "height_px": 1650, "scale_px_per_ft": None} for n in range(1, pages + 1)])

    def _insert(self, pages: list[dict]) -> list[dict]:
        """
        Add page rows as the INSERT would.
        Takes the pages as replace_pages passes them.
        Returns the new rows.
        """
        new = [{"pir_page_id": uuid4(), "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
                **page, "scale_px_per_ft": None if page["scale_px_per_ft"] is None else Decimal(page["scale_px_per_ft"]),
                "rotation_deg": 0, "created_at": CREATED, "updated_at": CREATED, "deleted_at": None,
                "deleted_by": None, "pir_deleted_at": None, "property_deleted_at": None} for page in pages]
        self.rows += new
        return new

    def live(self) -> list[dict]:
        """
        The live pages, in order.
        Takes nothing.
        Returns the rows.
        """
        return sorted((row for row in self.rows if row["deleted_at"] is None), key=lambda row: row["page_number"])

    def __call__(self, sql: str, params: tuple = ()):
        """
        Answer one statement.
        Takes the SQL and its parameters.
        Returns what run_query would.
        """
        self.calls.append((sql, params))
        if "WITH old_pages" in sql:
            retired = self.live()
            for row in retired:
                row["deleted_at"], row["deleted_by"] = DELETED, params[0]
            if "INSERT INTO icid.pir_pages" in sql:
                return self._insert(params[-1].obj)
            return [{"pir_page_id": row["pir_page_id"], "image_path": row["image_path"]} for row in retired]
        if sql.lstrip().startswith("WITH pg AS"):  # a page update
            page_id = params[-1]
            row = next((row for row in self.live() if row["pir_page_id"] == page_id), None)
            if row is None:
                return []
            if "SET scale_px_per_ft" in sql:
                row["scale_px_per_ft"] = params[0]
            else:
                row["rotation_deg"] = params[0]
            return [row]
        if "WHERE pg.pir_page_id = %s" in sql:
            return [row for row in self.rows if row["pir_page_id"] == params[0]]
        if "WHERE pg.pir_id = %s AND pg.deleted_at IS NULL" in sql:
            return self.live()
        raise AssertionError(f"unexpected statement: {sql}")


class FakeStorage:
    """The Storage calls the service makes, recorded; the staged upload holds the given bytes."""

    def __init__(self, upload: bytes = b"", there: bool = True) -> None:
        """
        Start an empty record.
        Takes what the staged upload holds and whether it is there at all.
        Returns nothing.
        """
        self.upload, self.there = upload, there
        self.stored: list[tuple[str, bytes, str]] = []
        self.removed: list[str] = []
        self.signed: list[str] = []

    def patches(self) -> list:
        """
        Patch the service's Storage calls with this fake.
        Takes nothing.
        Returns the patchers, to start.
        """
        return [
            patch(f"{SERVICE}.create_signed_upload_url", return_value="https://storage.example/upload?token=u"),
            patch(f"{SERVICE}.object_exists", side_effect=lambda bucket, path: self.there),
            patch(f"{SERVICE}.download_file", side_effect=lambda path, bucket: self.upload),
            patch(f"{SERVICE}.upload_file",
                  side_effect=lambda bucket, path, data, kind: self.stored.append((path, data, kind))),
            patch(f"{SERVICE}.create_signed_url",
                  side_effect=lambda path, expires, name, bucket: self.signed.append(path) or f"{SIGNED}&p={path}"),
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


def pir_with(pages: FakePages, **changes):
    """
    The run_query for PIR reads, page 1 standing in for the deprecated image columns.
    Takes the fake page table and any columns to change on the PIR.
    Returns the side effect.
    """
    def run(sql, params=()):
        """
        Answer a PIR read.
        Takes the SQL and its parameters.
        Returns the PIR row.
        """
        first = pages.live()[0] if pages.live() else None
        return [{**PIR_ROW, "source_image_url": first and first["image_path"],
                 "source_image_scale": first and first["scale_px_per_ft"] and float(first["scale_px_per_ft"]), **changes}]
    return run


def member():
    """
    A client signed in as a project member who isn't an admin.
    """
    return signed_in(MEMBER_ROW)


class TestUploadRequest:
    def test_a_member_gets_a_signed_url_at_a_staging_path(self, storage):
        with member() as client, patch(PIRS, return_value=[PIR_ROW]), patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = client.post(f"{PIR_URL}/image/upload-request",
                                   json={"filename": "letter.pdf", "mime": "application/pdf", "size_bytes": 5601})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["staging_path"].startswith(f"staging/{PIR_ID}/") and data["staging_path"].endswith(".pdf")
        assert data["upload_url"].startswith("https://storage.example/upload") and data["expires_at"]
        assert data["upload_path"] == data["staging_path"] and data["expires_in"] > 0  # R1d's names, deprecated

    def test_r1ds_content_type_still_works(self, storage, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{PIR_URL}/image/upload-request", json={"content_type": "image/jpeg"})
        assert response.status_code == 200 and response.json()["data"]["staging_path"].endswith(".jpg")

    @pytest.mark.parametrize("body,status,detail", [
        ({"mime": "text/plain"}, 400, "Unsupported file type"),
        ({}, 400, "mime is required"),
        ({"mime": "application/pdf", "size_bytes": 15 * 1024 * 1024}, 413, "File too large"),
    ])
    def test_what_cant_be_uploaded_is_refused_up_front(self, storage, admin_client, body, status, detail):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{PIR_URL}/image/upload-request", json=body)
        assert response.status_code == status and response.json() == {"detail": detail}


class TestComplete:
    def test_a_3_page_letter_pdf_becomes_3_pages_and_get_lists_them(self, storage):
        storage.upload = FIXTURE.read_bytes()
        pages = FakePages()
        with member() as client, patch(PIRS, side_effect=pir_with(pages)), patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PAGES, side_effect=pages):
            response = client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED, "scale_px_per_ft": 15})
            listed = client.get(f"{PIR_URL}/pages")
        assert response.status_code == 200 and response.json()["message"] == "PIR image set: 3 pages"
        data = response.json()["data"]
        assert [page["page_number"] for page in data["pages"]] == [1, 2, 3]
        sizes = {(page["width_px"], page["height_px"]) for page in data["pages"]}
        assert len(sizes) == 1 and sizes.pop() in {(1275, 1650), (1275, 1651)}  # letter at 150 DPI (PDFium rounds up)
        assert {page["scale_px_per_ft"] for page in data["pages"]} == {15.0}  # the upload's scale on all three
        paths = [path for path, _, _ in storage.stored]
        folder = paths[0].rsplit("/", 1)[0]
        assert paths == [f"{folder}/page-{n}.png" for n in (1, 2, 3)] and folder.startswith(f"pirs/{PIR_ID}/")
        assert all(kind == "image/png" and sniff_type(png) == "image/png" for _, png, kind in storage.stored)
        assert storage.removed == [STAGED]
        assert data["source_image_url"] == paths[0] and data["signed_url"].endswith(paths[0])  # R1d's card
        assert listed.status_code == 200
        assert [page["image_url"] for page in listed.json()["data"]] == [f"{SIGNED}&p={path}" for path in paths]

    def test_r1ds_body_still_works(self, storage, admin_client):
        storage.upload = image_bytes("PNG")
        pages = FakePages()
        with patch(PIRS, side_effect=pir_with(pages)), patch(PAGES, side_effect=pages):
            response = admin_client.post(f"{PIR_URL}/image/complete",
                                         json={"upload_path": STAGED.replace(".pdf", ".png"), "scale": 20})
        assert response.status_code == 200 and response.json()["data"]["pages"][0]["scale_px_per_ft"] == 20.0

    def test_a_jpeg_becomes_page_1_as_png(self, storage, admin_client):
        storage.upload = image_bytes("JPEG", (640, 480))
        pages = FakePages()
        with patch(PIRS, side_effect=pir_with(pages)), patch(PAGES, side_effect=pages):
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED})
        page, = response.json()["data"]["pages"]
        assert (page["page_number"], page["width_px"], page["height_px"], page["scale_px_per_ft"]) == (1, 640, 480, None)
        (path, data, kind), = storage.stored
        assert path.endswith("/page-1.png") and kind == "image/png" and sniff_type(data) == "image/png"

    def test_replacing_retires_the_old_pages_and_their_flags_in_one_statement_and_keeps_their_files(self, storage, admin_client):
        storage.upload = FIXTURE.read_bytes()
        pages = FakePages(pages=2)
        old = [row["image_path"] for row in pages.live()]
        with patch(PIRS, side_effect=pir_with(pages)), patch(PAGES, side_effect=pages):
            assert admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED}).status_code == 200
        assert len(pages.live()) == 3 and all(row["deleted_at"] == DELETED for row in pages.rows[:2])
        assert not set(old) & set(storage.removed)  # the old pages' files stay, for their flags' history
        replace_sql, replace_params = next(call for call in pages.calls if "INSERT INTO icid.pir_pages" in call[0])
        one_line = " ".join(replace_sql.split())
        assert "UPDATE icid.flags SET deleted_at = now(), deleted_by = %s" in one_line
        assert "WHERE pir_page_id IN (SELECT pir_page_id FROM old_pages) AND deleted_at IS NULL" in one_line
        assert "CROSS JOIN (SELECT count(*) FROM old_pages) AS replaced" in one_line  # old out of the index first
        assert replace_params[0] == ADMIN_ID and replace_params[2:6] == (ADMIN_ID,) * 4
        # the flags' non-assessable areas, and the pages' features and segments, go in the same statement
        assert "UPDATE icid.non_assessable_areas SET deleted_at = now(), deleted_by = %s, updated_at = now()" in one_line
        assert "WHERE flag_id IN (SELECT id FROM old_flags) AND deleted_at IS NULL" in one_line
        for table in ("features", "segments"):
            assert (f"UPDATE icid.{table} SET deleted_at = now(), deleted_by = %s, updated_at = now() "
                    "WHERE pir_page_id IN (SELECT pir_page_id FROM old_pages) AND deleted_at IS NULL") in one_line
        for retired in ("flags", "areas", "features", "segments"):
            assert f"FROM old_{retired}) AS {retired}_retired" in one_line

    @pytest.mark.parametrize("upload,status,detail", [
        (b"just text", 400, "Unsupported file type"),
        (b"%PDF-1.7 not really", 400, "Could not read the PDF"),
        (PNG + b"\0" * (15 * 1024 * 1024), 413, "File too large"),
    ], ids=["text", "broken-pdf", "15mb"])
    def test_a_file_that_cant_be_used_is_refused_and_its_upload_removed(self, storage, admin_client, upload, status, detail):
        storage.upload = upload
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES) as run:
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED})
        assert response.status_code == status and response.json() == {"detail": detail}
        assert storage.stored == [] and storage.removed == [STAGED]
        run.assert_not_called()

    def test_more_pages_than_the_cap_is_400_naming_it(self, storage, admin_client):
        storage.upload = pdf_bytes(*[(612, 792)] * 16)
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES) as run:
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED})
        assert response.status_code == 400
        assert response.json()["detail"] == "The PDF has 16 pages; a PIR can have at most 15 (PIR_UPLOAD_MAX_PAGES)"
        run.assert_not_called()

    def test_a_page_too_big_to_render_is_400_naming_the_cap(self, storage, admin_client):
        storage.upload = pdf_bytes((612, 792), (3000, 3000))  # page 2: 6250 x 6250 px at 150 DPI, 39 MP
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES) as run:
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED})
        assert response.status_code == 400
        assert response.json()["detail"] == ("Page 2 at 150 DPI is 39.1 megapixels; a page can be at most 25 "
                                             "(PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE)")
        assert storage.stored == [] and run.call_count == 0

    @pytest.mark.parametrize("scale", [0, -2])
    def test_a_scale_that_isnt_positive_is_400(self, storage, admin_client, scale):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED, "scale_px_per_ft": scale})
        assert response.status_code == 400 and response.json() == {"detail": "scale must be a positive number"}

    @pytest.mark.parametrize("path", [
        f"pirs/{PIR_ID}/somewhere/page-1.png", "staging/00000000-0000-4000-8000-000000000000/x.pdf",
        f"staging/{PIR_ID}/../x.pdf", f"staging/{PIR_ID}/",
    ])
    def test_a_path_that_isnt_one_of_the_pirs_uploads_is_400(self, storage, admin_client, path):
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": path})
        assert response.status_code == 400 and response.json() == {"detail": "staging_path is not an upload of this PIR"}

    def test_completing_before_uploading_is_400(self, storage, admin_client):
        storage.there = False
        with patch(PIRS, return_value=[PIR_ROW]):
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED})
        assert response.status_code == 400 and response.json() == {"detail": "Upload the file before completing"}

    def test_storage_that_cant_store_a_page_is_502_and_the_pages_are_untouched(self, storage, admin_client):
        storage.upload = FIXTURE.read_bytes()
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES) as run, \
                patch(f"{SERVICE}.upload_file", side_effect=RuntimeError("down")):
            response = admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED})
        assert response.status_code == 502 and response.json() == {"detail": "Could not store the pages"}
        run.assert_not_called()


class TestPages:
    def test_a_pir_without_pages_lists_none(self, storage, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, side_effect=FakePages()):
            response = admin_client.get(f"{PIR_URL}/pages")
        assert response.status_code == 200 and response.json()["data"] == []

    def test_scaling_one_page_leaves_the_others_alone(self, storage):
        pages = FakePages(pages=3)
        second = pages.live()[1]["pir_page_id"]
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PAGES, side_effect=pages):
            response = client.patch(f"/v1/pir-pages/{second}/scale", json={"scale_px_per_ft": 12.5})
        assert response.status_code == 200 and response.json()["data"]["scale_px_per_ft"] == 12.5
        assert [row["scale_px_per_ft"] for row in pages.live()] == [None, Decimal("12.5"), None]

    @pytest.mark.parametrize("angle", [0, 90, 180, 270])
    def test_a_quarter_turn_is_taken(self, storage, admin_client, angle):
        pages = FakePages(pages=1)
        page_id = pages.live()[0]["pir_page_id"]
        with patch(PAGES, side_effect=pages):
            response = admin_client.patch(f"/v1/pir-pages/{page_id}/rotation", json={"rotation_deg": angle})
        assert response.status_code == 200 and response.json()["data"]["rotation_deg"] == angle

    def test_45_degrees_is_422(self, storage, admin_client):
        pages = FakePages(pages=1)
        with patch(PAGES, side_effect=pages):
            response = admin_client.patch(f"/v1/pir-pages/{pages.live()[0]['pir_page_id']}/rotation", json={"rotation_deg": 45})
        assert response.status_code == 422

    def test_deleting_the_pages_retires_them_and_their_flags_and_keeps_the_files(self, storage):
        pages = FakePages(pages=3)
        with member() as client, patch(PIRS, return_value=[PIR_ROW]), patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PAGES, side_effect=pages):
            response = client.delete(f"{PIR_URL}/pages")
        assert response.status_code == 204 and pages.live() == [] and storage.removed == []
        sql, params = pages.calls[-1]
        assert "UPDATE icid.flags" in sql and "INSERT" not in sql and params == (MEMBER_ID, PIR_ID, *(MEMBER_ID,) * 5)
        assert "UPDATE icid.pir_match_lines" in sql  # its match lines go with it (R1h.1a)
        for table in ("non_assessable_areas", "features", "segments"):
            assert f"UPDATE icid.{table}" in sql

    @pytest.mark.parametrize("parent,detail", [
        ("deleted_at", "Page is deleted"), ("pir_deleted_at", "PIR is deleted"), ("property_deleted_at", "Property is deleted"),
    ])
    def test_a_member_cant_change_a_page_under_something_deleted(self, storage, parent, detail):
        pages = FakePages(pages=1)
        row = pages.live()[0]
        row[parent] = DELETED
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PAGES, side_effect=pages):
            response = client.patch(f"/v1/pir-pages/{row['pir_page_id']}/rotation", json={"rotation_deg": 90})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert row["rotation_deg"] == 0


WRITES = [
    ("post", f"{PIR_URL}/image/upload-request", {"mime": "image/png"}),
    ("post", f"{PIR_URL}/image/complete", {"staging_path": STAGED}),
    ("delete", f"{PIR_URL}/pages", None),
    ("patch", f"{PIR_URL}/image", {"scale": 12}),
    ("delete", f"{PIR_URL}/image", None),
]


class TestWhoMayWrite:
    @pytest.mark.parametrize("method,url,body", WRITES)
    def test_a_member_cant_change_a_deleted_pir(self, storage, method, url, body):
        with member() as client, patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, return_value=[{**PIR_ROW, "deleted_at": DELETED}]), patch(PAGES) as run:
            response = client.request(method, url, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "PIR is deleted"}
        run.assert_not_called()

    @pytest.mark.parametrize("method,url,body", WRITES)
    def test_a_demo_user_cant_write_on_their_own_project(self, storage, demo_client, method, url, body):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(PIRS, return_value=[PIR_ROW]), patch(PAGES) as run:
            response = demo_client.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        run.assert_not_called()

    @pytest.mark.parametrize("path,body", [("scale", {"scale_px_per_ft": 10}), ("rotation", {"rotation_deg": 90})])
    def test_a_demo_user_cant_change_a_page(self, storage, demo_client, path, body):
        pages = FakePages(pages=1)
        with patch(PROJECTS, return_value=ON_PROJECT), patch(PAGES, side_effect=pages):
            response = demo_client.patch(f"/v1/pir-pages/{pages.live()[0]['pir_page_id']}/{path}", json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}

    @pytest.mark.parametrize("method,url,body", WRITES + [("get", f"{PIR_URL}/pages", None)])
    def test_a_user_who_isnt_on_the_project_is_forbidden(self, storage, method, url, body):
        with member() as client, patch(PROJECTS, return_value=[]), patch(PIRS, return_value=[PIR_ROW]), \
                patch(PAGES) as run:
            response = client.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_gets_404_on_another_projects_pages(self, storage, demo_client):
        pages = FakePages(pages=1)
        with patch(PROJECTS, return_value=[]), patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, side_effect=pages):
            assert demo_client.get(f"{PIR_URL}/pages").status_code == 404
            response = demo_client.patch(f"/v1/pir-pages/{pages.live()[0]['pir_page_id']}/rotation",
                                         json={"rotation_deg": 90})
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}

    def test_an_unknown_page_is_404(self, storage, admin_client):
        with patch(PAGES, return_value=[]):
            response = admin_client.patch(f"/v1/pir-pages/{uuid4()}/scale", json={"scale_px_per_ft": 10})
        assert response.status_code == 404 and response.json() == {"detail": "Page not found"}


class TestDeprecatedImageRoutes:
    def test_get_gives_page_1s_link_and_scale(self, storage, admin_client):
        pages = FakePages(pages=2)
        pages.live()[0]["scale_px_per_ft"] = Decimal("15")
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, side_effect=pages):
            response = admin_client.get(f"{PIR_URL}/image")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["signed_url"].endswith(pages.live()[0]["image_path"]) and data["scale"] == 15.0

    def test_a_pir_without_pages_has_no_image(self, storage, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, side_effect=FakePages()):
            response = admin_client.get(f"{PIR_URL}/image")
        assert response.status_code == 404 and response.json() == {"detail": "No image for this PIR"}

    def test_patch_scales_page_1(self, storage, admin_client):
        pages = FakePages(pages=2)
        with patch(PIRS, side_effect=pir_with(pages)), patch(PAGES, side_effect=pages):
            response = admin_client.patch(f"{PIR_URL}/image", json={"scale": 12})
        assert response.status_code == 200 and response.json()["data"]["source_image_scale"] == 12.0
        assert [row["scale_px_per_ft"] for row in pages.live()] == [Decimal("12"), None]

    def test_delete_takes_the_pages_away(self, storage, admin_client):
        pages = FakePages(pages=2)
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, side_effect=pages):
            assert admin_client.delete(f"{PIR_URL}/image").status_code == 204
        assert pages.live() == []

    def test_the_pir_reads_its_image_from_page_1(self):
        from api.queries.pirs import get_pir
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            get_pir(PIR_ID)
        one_line = " ".join(run.call_args.args[0].split())
        assert ("WHERE pg.pir_id = pir.id AND pg.page_number = 1 AND pg.deleted_at IS NULL) AS source_image_url"
                in one_line)


class TestRendering:
    def test_the_fixture_renders_to_3_letter_pages(self):
        rendered = render_pdf_pages(FIXTURE.read_bytes(), dpi=100)
        assert [(width, height) for _, width, height in rendered] == [(850, 1100)] * 3
        assert all(sniff_type(png) == "image/png" for png, _, _ in rendered)

    def test_a_png_is_kept_and_a_jpeg_converted(self):
        png = image_bytes("PNG", (30, 20))
        assert image_to_page(png) == (png, 30, 20)
        converted, width, height = image_to_page(image_bytes("JPEG", (30, 20)))
        assert sniff_type(converted) == "image/png" and (width, height) == (30, 20)

    def test_an_image_over_the_cap_is_refused(self):
        from api.services.pir_image import PirImageError
        with pytest.raises(PirImageError) as refused:
            image_to_page(image_bytes("PNG", (6000, 5000)))
        assert refused.value.detail.startswith("The image is 30.0 megapixels")


def test_a_new_page_1_starts_with_the_tax_map_width(storage, admin_client):
    """Slice Y1: the replaced page 1's manual width carries forward, else the property's MapPLUTO frontage."""
    storage.upload = FIXTURE.read_bytes()
    pages = FakePages(pages=2)
    with patch(PIRS, side_effect=pir_with(pages)), patch(PAGES, side_effect=pages):
        assert admin_client.post(f"{PIR_URL}/image/complete", json={"staging_path": STAGED}).status_code == 200
    replace_sql, _ = next(call for call in pages.calls if "INSERT INTO icid.pir_pages" in call[0])
    one_line = " ".join(replace_sql.split())
    assert "RETURNING pir_page_id, page_number" in one_line
    assert "WHERE op.page_number = 1 AND a.tax_map_source = 'manual'" in one_line
    assert ("CASE WHEN carried.tax_map_source IS NOT NULL THEN carried.tax_map_width_ft ELSE p.tax_map_frontage_ft END"
            in one_line)
    assert "WHERE pg.page_number = 1 AND COALESCE(carried.tax_map_source, p.tax_map_source) IS NOT NULL" in one_line
    assert "ON CONFLICT (pir_page_id) DO NOTHING" in one_line
    assert "CROSS JOIN (SELECT count(*) FROM title_block) AS title_blocks_started" in one_line
