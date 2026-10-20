import io
import zipfile
from unittest.mock import patch
from uuid import UUID

import pytest
from PIL import Image

from api.services import dot_inspection_pdf
from api.services.ped_ramp_exports import (
    PedRampExportError,
    corner_file_stem,
    dot_package,
    fill_tif,
    inspection_download_name,
    signed_download,
    store_inspection_package,
    tif_member_name,
)

# ---------------------------------------------------------------------------
# api/services/ped_ramp_exports.py: an approved inspection's DOT forms, filled with the real fillers (S2)
# ---------------------------------------------------------------------------

CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
INSPECTION_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
CORNER = {"corner_id": CORNER_ID, "project_id": "HWS0023", "boro": "Queens", "north_south_street": "3rd Ave",
          "east_west_street": "38 Street", "corner_direction": "NE", "ramps": []}


@pytest.fixture(autouse=True)
def photos():
    """
    Give every inspection and TIF no photos unless a test says otherwise, so no test reads them from Storage.
    Yields {inspection, tif}: the mocks of inspection_photo_bytes and tif_photo_bytes.
    """
    with patch("api.services.ped_ramp_exports.inspection_photo_bytes", return_value={}) as inspection, \
         patch("api.services.ped_ramp_exports.tif_photo_bytes", return_value=([], None)) as tif_photos:
        yield {"inspection": inspection, "tif": tif_photos}


def jpeg() -> bytes:
    """
    Make a small JPEG photo.
    Takes nothing.
    Returns its bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 140, 160)).save(buffer, "JPEG")
    return buffer.getvalue()


def ramp(number: int, ramp_type: str = "PERPENDICULAR", label: str | None = None) -> dict:
    """
    Make a ramp as list_corner_ramps returns it, without attributes.
    Takes its number, its type and its label.
    Returns the ramp dict.
    """
    return {"property_id": UUID(f"9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e6{number}"), "intersection_street_1": "3rd Ave",
            "intersection_street_2": "38 Street", "quadrant": "NE", "facing_street": "street_1", "ramp_type": ramp_type,
            "ramp_id_label": label, "has_attributes": False, "attributes": None}


def tif(for_ramp: dict, status: str = "approved") -> dict:
    """
    Make a TIF of a ramp as list_inspection_tifs returns it.
    Takes the ramp and the TIF's status.
    Returns the TIF dict.
    """
    return {"id": UUID("5a4b5c6d-7e8f-4a9b-8c0d-2e3f4a5b6c7d"), "property_id": for_ramp["property_id"], "status": status,
            "ramp_id_label": for_ramp["ramp_id_label"], "signer_signature_path": None, "signer_name": "Rita Engineer",
            "signer_ny_license_no": "PE-1", "signed_at": None}


def members(data: bytes) -> list[str]:
    """
    List a ZIP's members.
    Takes the ZIP's bytes.
    Returns the member names, in order.
    """
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


class TestPackage:
    def test_one_copy_and_no_tif_is_a_single_pdf(self):
        data, kind = dot_package(CORNER, [ramp(1)], [], {})
        assert kind == "pdf" and data.startswith(b"%PDF")

    def test_an_approved_tif_makes_it_a_zip_and_a_draft_tif_is_left_out(self):
        one, two = ramp(1, label="CT-A"), ramp(2, "PARALLEL")
        data, kind = dot_package(CORNER, [one, two], [tif(one), tif(two, "draft")], {})
        assert kind == "zip" and members(data) == ["inspection.pdf", "tif_CT-A.pdf"]

    def test_a_corner_of_three_measured_ramps_is_two_copies(self):
        measured = [{**ramp(number, ramp_type), "attributes": {"ramp_width_in": 48}}
                    for number, ramp_type in ((1, "PERPENDICULAR"), (2, "PARALLEL"), (3, "PERPENDICULAR"))]
        data, kind = dot_package(CORNER, measured, [], {})
        assert kind == "zip" and members(data) == ["inspection_1.pdf", "inspection_2.pdf"]

    def test_unmeasured_ramps_take_no_copy(self):
        data, kind = dot_package(CORNER, [ramp(1), ramp(2, "PARALLEL"), ramp(3)], [], {})
        assert kind == "pdf" and data.startswith(b"%PDF")

    def test_a_tifs_photos_and_layout_are_filled_into_it(self, photos):
        one = ramp(1, label="R1")
        photos["tif"].return_value = ([(1, "Existing", jpeg())], jpeg())
        with patch("api.services.ped_ramp_exports.fill_tif_form", return_value=b"%PDF") as fill:
            fill_tif(tif(one), CORNER, [one])
        photos["tif"].assert_called_once_with(tif(one)["id"])
        assert fill.call_args.args[3][0][:2] == (1, "Existing") and fill.call_args.args[4] is not None

    def test_a_tif_of_a_ramp_no_longer_at_the_corner_is_an_error(self):
        with pytest.raises(PedRampExportError):
            fill_tif(tif(ramp(5)), CORNER, [ramp(1)])


class TestStore:
    def test_it_reads_fills_and_stores_the_package_with_the_photos(self, photos):
        one = ramp(1)
        photos["inspection"].return_value = {str(one["property_id"]): [(1, "Ramp from the left", jpeg())]}
        with patch("api.services.ped_ramp_exports.fill_inspection_forms",
                   wraps=dot_inspection_pdf.fill_inspection_forms) as fill, \
             patch("api.services.ped_ramp_exports.get_corner", return_value=CORNER), \
             patch("api.services.ped_ramp_exports.list_corner_ramps", return_value=[one]), \
             patch("api.services.ped_ramp_exports.list_inspection_tifs", return_value=[]), \
             patch("api.services.ped_ramp_exports.upload_file") as upload:
            path = store_inspection_package({"id": INSPECTION_ID, "corner_id": CORNER_ID})
        bucket, stored, data, content_type = upload.call_args.args
        assert bucket == "pedramp-exports" and stored == path and content_type == "application/pdf"
        assert path.startswith(f"inspections/{INSPECTION_ID}/") and path.endswith(".pdf") and data.startswith(b"%PDF")
        photos["inspection"].assert_called_once_with(INSPECTION_ID)
        assert fill.call_args.args[2] == photos["inspection"].return_value

    def test_a_read_or_a_storage_failure_is_an_error(self):
        with patch("api.services.ped_ramp_exports.get_corner", return_value=None), \
             patch("api.services.ped_ramp_exports.list_corner_ramps", return_value=[]), \
             patch("api.services.ped_ramp_exports.list_inspection_tifs", return_value=[]):
            with pytest.raises(PedRampExportError):
                store_inspection_package({"id": INSPECTION_ID, "corner_id": CORNER_ID})
        with patch("api.services.ped_ramp_exports.get_corner", return_value=CORNER), \
             patch("api.services.ped_ramp_exports.list_corner_ramps", return_value=[ramp(1)]), \
             patch("api.services.ped_ramp_exports.list_inspection_tifs", return_value=[]), \
             patch("api.services.ped_ramp_exports.upload_file", side_effect=RuntimeError("down")):
            with pytest.raises(PedRampExportError, match="Could not store the DOT forms"):
                store_inspection_package({"id": INSPECTION_ID, "corner_id": CORNER_ID})


class TestNames:
    def test_files_are_named_by_the_corner(self):
        assert corner_file_stem({**CORNER, "north_south_street": "St. Mark's Pl"}) == "St._Mark_s_Pl_and_38_Street_NE"
        assert inspection_download_name(CORNER, "inspections/x/y.zip") == "3rd_Ave_and_38_Street_NE_DOT_ped_ramp_inspection.zip"
        assert tif_member_name({"ramp_id_label": None}, 2) == "tif_ramp_2.pdf"

    def test_a_download_is_a_signed_url(self):
        with patch("api.services.ped_ramp_exports.create_signed_url", return_value="https://s/x") as sign:
            data = signed_download("inspections/x/y.pdf", "f.pdf")
        assert data["url"] == "https://s/x" and data["file_name"] == "f.pdf"
        sign.assert_called_once_with("inspections/x/y.pdf", 600, "f.pdf", "pedramp-exports")
