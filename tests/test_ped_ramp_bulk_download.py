import io
import zipfile
from unittest.mock import patch

import pytest

from api.services.ped_ramp_bulk_download import BulkDownloadError, build_bulk_zip, bulk_download, folder_name

# ---------------------------------------------------------------------------
# api/services/ped_ramp_bulk_download.py (Pedramp Phase 3, S5): a folder per corner, a stored ZIP unpacked into it,
# and the ZIP stored and signed. Storage is patched.
# ---------------------------------------------------------------------------

SERVICE = "api.services.ped_ramp_bulk_download"


def _zip(members: dict[str, bytes]) -> bytes:
    """
    Make a ZIP holding the members given.
    Takes {name: bytes}.
    Returns the ZIP's bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _row(path: str, ns: str = "3rd Ave", ew: str = "38 Street", direction: str = "NE") -> dict:
    """
    A listing row as the bulk download reads it.
    Takes its export's path, its streets and its direction.
    Returns the dict.
    """
    return {"dot_pdf_path": path, "north_south_street": ns, "east_west_street": ew, "corner_direction": direction}


class TestFolders:
    def test_streets_and_direction_made_safe(self):
        assert folder_name(_row("x")) == "3rd_Ave_and_38_Street_NE"
        assert folder_name(_row("x", ns="W. 4th St / Ave", ew="../B'way", direction=None)) == "W._4th_St_Ave_and_.._B_way"


class TestBuild:
    def test_a_pdf_goes_in_as_inspection_pdf_and_a_zip_is_unpacked(self):
        files = {
            "inspections/1/a.pdf": b"%PDF one",
            "inspections/2/b.zip": _zip({"inspection_1.pdf": b"%PDF c1", "nested/../tif_R1.pdf": b"%PDF t"}),
        }
        rows = [_row("inspections/1/a.pdf"), _row("inspections/2/b.zip", ns="5th Ave", direction="SW")]
        with patch(f"{SERVICE}.download_file", side_effect=lambda path, bucket: files[path]):
            data, count = build_bulk_zip(rows)
        assert count == 2
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert sorted(archive.namelist()) == [
                "3rd_Ave_and_38_Street_NE/inspection.pdf",
                "5th_Ave_and_38_Street_SW/inspection_1.pdf",
                "5th_Ave_and_38_Street_SW/tif_R1.pdf",
            ]
            assert archive.read("3rd_Ave_and_38_Street_NE/inspection.pdf") == b"%PDF one"

    def test_a_missing_file_and_a_bad_zip_are_left_out_and_a_twin_folder_numbered(self):
        def download(path: str, bucket: str) -> bytes:
            """Return a PDF, or fail for gone.pdf."""
            if path == "gone.pdf":
                raise RuntimeError("404")
            return b"%PDF"

        with patch(f"{SERVICE}.download_file", side_effect=download):
            data, count = build_bulk_zip([_row("a.pdf"), _row("gone.pdf", ns="x"), _row("b.pdf"), _row("c.zip", ns="y")])
        assert count == 2
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert sorted(archive.namelist()) == ["3rd_Ave_and_38_Street_NE/inspection.pdf",
                                                  "3rd_Ave_and_38_Street_NE_2/inspection.pdf"]


class TestStore:
    def test_stored_under_bulk_and_signed(self):
        stored = []
        with patch(f"{SERVICE}.download_file", return_value=b"%PDF"), \
             patch(f"{SERVICE}.upload_file", side_effect=lambda bucket, path, data, kind: stored.append((bucket, path, kind))), \
             patch(f"{SERVICE}.create_signed_url", return_value="https://storage.example/z") as sign:
            result = bulk_download("HWS0023", [_row("a.pdf")])
        ((bucket, path, kind),) = stored
        assert bucket == "pedramp-exports" and path.startswith("bulk/HWS0023/") and path.endswith(".zip")
        assert kind == "application/zip"
        assert result["url"] == "https://storage.example/z" and result["corner_count"] == 1
        assert result["file_name"].startswith("HWS0023_pedramp_inspections_") and result["file_name"].endswith(".zip")
        assert sign.call_args.args[2] == result["file_name"]

    def test_a_storage_failure_is_a_bulk_download_error(self):
        with patch(f"{SERVICE}.download_file", return_value=b"%PDF"), \
             patch(f"{SERVICE}.upload_file", side_effect=RuntimeError("down")), pytest.raises(BulkDownloadError):
            bulk_download("HWS0023", [_row("a.pdf")])
