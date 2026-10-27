from datetime import date
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx
import pytest

from api.services.dof_tax_map import (
    DofTaxMapUnavailable,
    TaxMapPdfError,
    cache_tax_map_pdf,
    complete_upload,
    current_map,
    fetch_map_pdf,
    map_url,
    request_upload,
    store_current_map,
)

# ---------------------------------------------------------------------------
# api/services/dof_tax_map.py (Slice Y1.5): a sidewalk's block's DOF tax-map PDF. DOF, Storage and the statement are
# mocked: httpx.get answers as DOF's map library and portal do.
# ---------------------------------------------------------------------------

GET = "api.services.dof_tax_map.httpx.get"
PROPS = "api.queries.properties.run_query"
STORE = "api.services.dof_tax_map"
SITE_ID = UUID("e0000000-0000-4000-8000-000000000001")
SITE = {"id": SITE_ID, "kind": "sidewalk", "boro": "Brooklyn", "block": "700"}
PDF = b"%PDF-1.7 a tax map"
MAP_ID = "30070020130725102158"


def library(*maps: tuple[str, str]) -> MagicMock:
    """DOF's map library answering with the maps given, each (ID, EFFECTIVE_DATE)."""
    response = MagicMock()
    response.json.return_value = {"features": [{"attributes": {"ID": i, "EFFECTIVE_DATE": d}} for i, d in maps]}
    return response


def portal(content: bytes = PDF) -> MagicMock:
    """DOF's portal answering with the bytes given."""
    response = MagicMock()
    response.content = content
    return response


class TestCurrentMap:
    def test_it_asks_for_the_blocks_current_map(self):
        with patch(GET, return_value=library((MAP_ID, "2013/07/25"))) as get:
            assert current_map("Brooklyn", "700") == {"map_id": MAP_ID, "effective_date": date(2013, 7, 25)}
        assert get.call_args.kwargs["params"]["where"] == "BOROUGH='3' AND BLOCK=700 AND CURRENT_FLAG='Y'"

    def test_the_latest_of_several_current_maps(self):
        with patch(GET, return_value=library(("30070020080101000000", "2008/01/01"), (MAP_ID, "2013/07/25"))):
            assert current_map("Brooklyn", "700")["map_id"] == MAP_ID

    def test_none_for_a_block_dof_has_no_map_for(self):
        with patch(GET, return_value=library()):
            assert current_map("Brooklyn", "37502") is None

    @pytest.mark.parametrize("failure", [httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
    def test_a_library_that_cant_be_reached_raises(self, failure):
        with patch(GET, side_effect=failure), pytest.raises(DofTaxMapUnavailable):
            current_map("Brooklyn", "700")

    def test_an_arcgis_error_raises(self):
        response = MagicMock()
        response.json.return_value = {"error": {"code": 400}}
        with patch(GET, return_value=response), pytest.raises(DofTaxMapUnavailable):
            current_map("Brooklyn", "700")


class TestFetchMapPdf:
    def test_it_downloads_the_pdf(self):
        with patch(GET, return_value=portal()) as get:
            assert fetch_map_pdf(MAP_ID) == PDF
        assert get.call_args.args[0].endswith(f"/pdf/home/index/map_library/{MAP_ID}")

    def test_something_that_isnt_a_pdf_raises(self):
        with patch(GET, return_value=portal(b"<html>maintenance</html>")), pytest.raises(DofTaxMapUnavailable):
            fetch_map_pdf(MAP_ID)

    def test_the_portals_own_address(self):
        assert map_url(MAP_ID).endswith(f"/map_library/{MAP_ID}") and map_url(None) is None


class TestStoreCurrentMap:
    def test_it_stores_and_records_the_map_and_removes_the_one_it_replaced(self):
        recorded = {**SITE, "old_tax_map_pdf_path": f"{SITE_ID}/upload-old.pdf"}
        with patch(GET, side_effect=[library((MAP_ID, "2013/07/25")), portal()]), \
                patch(f"{STORE}.upload_file") as upload, patch(f"{STORE}.remove_files") as remove, \
                patch(PROPS, return_value=[recorded]) as run:
            store_current_map(SITE)
        assert upload.call_args.args == ("tax-maps", f"{SITE_ID}/{MAP_ID}.pdf", PDF, "application/pdf")
        sql, params = run.call_args.args
        assert "p.kind = 'sidewalk' AND p.boro = %s::icid.boro AND p.block = %s" in sql
        assert params == (f"{SITE_ID}/{MAP_ID}.pdf", "dof_map_library", MAP_ID, date(2013, 7, 25), None, SITE_ID,
                          "Brooklyn", "700")
        remove.assert_called_once_with([f"{SITE_ID}/upload-old.pdf"], "tax-maps")

    def test_no_map_is_422_and_stores_nothing(self):
        with patch(GET, return_value=library()), patch(f"{STORE}.upload_file") as upload:
            with pytest.raises(TaxMapPdfError) as refused:
                store_current_map(SITE)
        assert refused.value.status_code == 422 and "Brooklyn block 700" in refused.value.detail
        upload.assert_not_called()

    def test_a_site_moved_meanwhile_is_409_and_its_new_file_is_removed(self):
        with patch(GET, side_effect=[library((MAP_ID, "2013/07/25")), portal()]), patch(f"{STORE}.upload_file"), \
                patch(f"{STORE}.remove_files") as remove, patch(PROPS, return_value=[]):
            with pytest.raises(TaxMapPdfError) as refused:
                store_current_map(SITE)
        assert refused.value.status_code == 409
        remove.assert_called_once_with([f"{SITE_ID}/{MAP_ID}.pdf"], "tax-maps")


class TestBackgroundFetch:
    def test_it_never_raises(self):
        with patch(GET, side_effect=httpx.ConnectError("down")):
            cache_tax_map_pdf(SITE_ID, "Brooklyn", "700")
        with patch(GET, return_value=library()):
            cache_tax_map_pdf(SITE_ID, "Brooklyn", "700")

    def test_a_ped_ramp_isnt_looked_up(self):
        with patch(GET) as get:
            cache_tax_map_pdf(SITE_ID, "Brooklyn", None)
        get.assert_not_called()


class TestUpload:
    def test_a_request_signs_a_staging_path_of_the_site(self):
        with patch(f"{STORE}.create_signed_upload_url", return_value="https://signed") as sign:
            upload = request_upload(SITE_ID)
        assert upload["upload_url"] == "https://signed" and upload["staging_path"].startswith(f"staging/{SITE_ID}/")
        assert upload["staging_path"].endswith(".pdf") and sign.call_args.args[1] == "tax-maps"

    def test_complete_stores_the_pdf_as_the_admins_upload(self):
        admin = UUID("ac035174-0c3f-4e9f-8c51-17c84894fe3b")
        staged = f"staging/{SITE_ID}/abc.pdf"
        with patch(f"{STORE}.object_exists", return_value=True), patch(f"{STORE}.download_file", return_value=PDF), \
                patch(f"{STORE}.upload_file") as upload, patch(f"{STORE}.remove_files") as remove, \
                patch(PROPS, return_value=[{**SITE, "old_tax_map_pdf_path": None}]) as run:
            complete_upload(SITE, staged, admin)
        stored = upload.call_args.args[1]
        assert stored.startswith(f"{SITE_ID}/upload-") and stored.endswith(".pdf")
        assert run.call_args.args[1][:5] == (stored, "upload", None, None, admin)
        remove.assert_called_once_with([staged], "tax-maps")

    @pytest.mark.parametrize("path", ["staging/other-site/abc.pdf", f"staging/{SITE_ID}/../x.pdf", f"{SITE_ID}/x.pdf"])
    def test_a_path_that_isnt_the_sites_upload_is_400(self, path):
        with pytest.raises(TaxMapPdfError) as refused:
            complete_upload(SITE, path, SITE_ID)
        assert refused.value.status_code == 400

    def test_a_missing_upload_is_404(self):
        with patch(f"{STORE}.object_exists", return_value=False), pytest.raises(TaxMapPdfError) as refused:
            complete_upload(SITE, f"staging/{SITE_ID}/abc.pdf", SITE_ID)
        assert refused.value.status_code == 404

    def test_a_file_that_isnt_a_pdf_is_400_and_its_upload_removed(self):
        staged = f"staging/{SITE_ID}/abc.pdf"
        with patch(f"{STORE}.object_exists", return_value=True), \
                patch(f"{STORE}.download_file", return_value=b"\x89PNG\r\n"), patch(f"{STORE}.upload_file") as upload, \
                patch(f"{STORE}.remove_files") as remove, pytest.raises(TaxMapPdfError) as refused:
            complete_upload(SITE, staged, SITE_ID)
        assert (refused.value.status_code, refused.value.detail) == (400, "The file isn't a PDF")
        upload.assert_not_called()
        remove.assert_called_once_with([staged], "tax-maps")

    def test_a_file_too_large_is_413(self):
        with patch(f"{STORE}.object_exists", return_value=True), \
                patch(f"{STORE}.download_file", return_value=b"%PDF-" + b"0" * (10 * 1024 * 1024)), \
                patch(f"{STORE}.remove_files"), pytest.raises(TaxMapPdfError) as refused:
            complete_upload(SITE, f"staging/{SITE_ID}/abc.pdf", SITE_ID)
        assert refused.value.status_code == 413
