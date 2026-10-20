import io
from unittest.mock import patch
from uuid import UUID

import pytest
from PIL import Image

from api.services.ped_ramp_photos import (
    PhotoError,
    inspection_photo_bytes,
    sniff_photo_type,
    stored_form,
    tif_photo_bytes,
)

# ---------------------------------------------------------------------------
# api/services/ped_ramp_photos.py (Pedramp Phase 3, S4): what a photo is, how it is kept, and reading a form's photos
# back for DOT's PDFs. Storage and the photo statements are patched.
# ---------------------------------------------------------------------------

SERVICE = "api.services.ped_ramp_photos"
INSPECTION_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
TIF_ID = UUID("5a4b5c6d-7e8f-4a9b-8c1d-2e3f4a5b6c7d")
RAMP_A = UUID("6b5c6d7e-8f9a-4b0c-9d2e-3f4a5b6c7d8e")
RAMP_B = UUID("6b5c6d7e-8f9a-4b0c-9d2e-3f4a5b6c7d8f")


def _image(fmt: str) -> bytes:
    """
    Make a small image in a format.
    Takes Pillow's name for the format.
    Returns its bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (16, 8), "blue").save(buffer, format=fmt)
    return buffer.getvalue()


def _row(path: str, number: int, kind: str = "photo", property_id: UUID = RAMP_A, caption: str = None) -> dict:
    """
    A photo row as list_photos returns it (the columns the readers use).
    Takes its path, number, kind, ramp and caption.
    Returns the dict.
    """
    return {"storage_path": path, "photo_number": number, "kind": kind, "property_id": property_id, "caption": caption}


class TestWhatAPhotoIs:
    def test_sniffs_by_first_bytes(self):
        assert sniff_photo_type(_image("JPEG")) == "image/jpeg"
        assert sniff_photo_type(_image("PNG")) == "image/png"
        assert sniff_photo_type(_image("HEIF")) == "image/heic"
        assert sniff_photo_type(b"%PDF-1.7") is None
        assert sniff_photo_type(b"\x00\x00\x00\x18ftypmp42") is None  # a video, not a photo

    def test_a_jpeg_and_a_png_are_kept_as_they_are(self):
        jpeg, png = _image("JPEG"), _image("PNG")
        assert stored_form(jpeg) == (jpeg, "image/jpeg", "jpg")
        assert stored_form(png) == (png, "image/png", "png")

    def test_a_heic_becomes_a_jpeg(self):
        data, kind, extension = stored_form(_image("HEIF"))
        assert (kind, extension) == ("image/jpeg", "jpg")
        assert Image.open(io.BytesIO(data)).format == "JPEG"

    def test_anything_else_is_refused(self):
        with pytest.raises(PhotoError) as caught:
            stored_form(b"GIF89a")
        assert caught.value.status_code == 400
        with pytest.raises(PhotoError):
            stored_form(b"\x00\x00\x00\x1cftypheic" + b"broken")


class TestPhotosForTheForms:
    def test_an_inspections_photos_by_ramp_skipping_a_missing_file(self):
        rows = [_row("a1.jpg", 1, caption="North"), _row("b2.jpg", 2, property_id=RAMP_B), _row("a3.jpg", 3)]

        def download(path: str, bucket: str) -> bytes:
            """Return a file's bytes, or fail for a3.jpg."""
            if path == "a3.jpg":
                raise RuntimeError("gone")
            return path.encode()

        with patch(f"{SERVICE}.list_photos", return_value=rows) as listed, \
             patch(f"{SERVICE}.download_file", side_effect=download):
            photos = inspection_photo_bytes(INSPECTION_ID)
        listed.assert_called_once_with(inspection_id=INSPECTION_ID)
        assert photos == {str(RAMP_A): [(1, "North", b"a1.jpg")], str(RAMP_B): [(2, None, b"b2.jpg")]}

    def test_a_tifs_photos_and_layout(self):
        rows = [_row("p1.jpg", 1, caption="Ramp"), _row("p2.jpg", 2), _row("layout.png", 1, kind="layout")]
        with patch(f"{SERVICE}.list_photos", return_value=rows), \
             patch(f"{SERVICE}.download_file", side_effect=lambda path, bucket: path.encode()):
            photos, layout = tif_photo_bytes(TIF_ID)
        assert photos == [(1, "Ramp", b"p1.jpg"), (2, None, b"p2.jpg")] and layout == b"layout.png"

    def test_no_layout_and_a_failed_listing(self):
        with patch(f"{SERVICE}.list_photos", return_value=[]):
            assert tif_photo_bytes(TIF_ID) == ([], None)
        with patch(f"{SERVICE}.list_photos", return_value=None), pytest.raises(RuntimeError):
            inspection_photo_bytes(INSPECTION_ID)
