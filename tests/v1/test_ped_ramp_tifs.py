from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from api.services.ped_ramp_exports import PedRampExportError
from tests.conftest import ADMIN_USER_ROW, UNSIGNED_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/ped-ramp-inspections/{id}/tifs, .../ramps/{property_id}/tif and /v1/ped-ramp-tifs/{tif_id} (Pedramp Phase 3, S3)
# Mock rows are dicts, as run_query returns them. A project row stands in for both checks that read project_users.
# ---------------------------------------------------------------------------

TIFS = "api.queries.ped_ramp_tifs.run_query"
INSPECTIONS = "api.queries.ped_ramp_inspections.run_query"
CORNERS = "api.queries.ped_ramp_corners.run_query"
PROJECTS = "api.queries.projects.run_query"
SNAPSHOT = "api.v1.ped_ramp_tifs.snapshot_signature_for_tif"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
INSPECTION_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
TIF_ID = UUID("5a4b5c6d-7e8f-4a9b-8c0d-2e3f4a5b6c7d")
RAMP_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e61")
URL = f"/v1/ped-ramp-tifs/{TIF_ID}"
CREATED = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
RE_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000003"), "role": None,
          "signature_path": "users/b3/signature.png"}
AS_INSPECTOR = [{"role": "inspector"}]
AS_RE = [{"role": "re"}]
AS_OE = [{"role": "oe"}]
INSPECTION = {"id": INSPECTION_ID, "corner_id": CORNER_ID, "project_id": "HWS0023", "north_south_street": "3rd Ave",
              "east_west_street": "38 Street", "corner_direction": "NE", "status": "draft", "requirements_met": False,
              "dot_pdf_path": None, "created_at": CREATED, "updated_at": CREATED, "created_by": ADMIN_ID,
              "tif_count": 0, "draft_tif_count": 0}
CORNER_ROW = {"corner_id": CORNER_ID, "project_id": "HWS0023", "boro": "Queens", "north_south_street": "3rd Ave",
              "east_west_street": "38 Street", "created_at": CREATED, "updated_at": CREATED, "created_by": ADMIN_ID,
              "deleted_at": None, "deleted_by": None,
              "ramps": [{"property_id": str(RAMP_ID), "intersection_street_1": "3rd Ave", "intersection_street_2": "38 Street",
                         "quadrant": "NE", "facing_street": "street_1", "ramp_type": "PERPENDICULAR",
                         "ramp_id_label": "R1", "has_attributes": True}]}
CHECKS = {name: False for name in (
    "reason_limited_row", "reason_existing_topography", "reason_existing_utilities", "reason_structures",
    "reason_trees_natural", "reason_nyct_structures", "reason_historic_features", "reason_other",
    "element_ramp_width", "element_running_slope", "element_cross_slope", "element_flare_slopes", "element_dws",
    "element_landing_dimensions", "element_landing_slopes", "element_gutter_cross_slope", "element_gutter_flow_slope",
    "element_other", "investigation_confirmed")}
DRAFT = {**CHECKS, "id": TIF_ID, "property_id": RAMP_ID, "inspection_id": INSPECTION_ID, "project_id": "HWS0023",
         "corner_id": CORNER_ID, "inspection_status": "draft", "ramp_type": "PERPENDICULAR", "quadrant": "NE",
         "ramp_id_label": "R1", "status": "draft", "dot_corner_id": "QN-123", "dot_ramp_id": "R1",
         "ramp_construction": "new_installation", "signer_name": None, "signer_ny_license_no": None,
         "signer_signature_path": None, "signed_at": None, "signed_by_uuid": None, "signed_by_name": None,
         "created_at": CREATED, "updated_at": CREATED, "created_by": ADMIN_ID}
APPROVED = {**DRAFT, "status": "approved", "signer_name": "Rita Engineer", "signer_ny_license_no": "PE-1234",
            "signer_signature_path": f"ped-ramp-tifs/{TIF_ID}/re_x.png", "signed_at": CREATED,
            "signed_by_uuid": RE_ROW["uuid"], "signed_by_name": "Rita Engineer"}


class TestDraft:
    URL = f"/v1/ped-ramp-inspections/{INSPECTION_ID}/ramps/{RAMP_ID}/tif"

    def test_an_inspector_drafts_one_prefilled_from_the_corner_and_ramp(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(INSPECTIONS, return_value=[INSPECTION]), patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(TIFS, return_value=[DRAFT]) as run:
            response = inspector.post(self.URL)
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["dot_corner_id"] == "QN-123" and data["ramp_id_label"] == "R1" and "signer_signature_path" not in data
        sql, params = run.call_args.args
        assert "INSERT INTO icid.ped_ramp_tifs" in sql and "c.dot_corner_id, a.ramp_id_label, c.permittee_id" in sql
        assert "WHEN 'new_install' THEN 'new_installation'" in sql and "i.status <> 'approved'" in sql
        assert params == (INSPECTOR_ROW["uuid"], RAMP_ID, INSPECTION_ID)

    def test_a_ramp_elsewhere_is_400_and_a_second_tif_409(self, admin_client):
        other = {**CORNER_ROW, "ramps": []}
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(CORNERS, return_value=[other]), patch(TIFS) as run:
            response = admin_client.post(self.URL)
        assert (response.status_code, response.json()) == (400, {"detail": "Only a ramp at the inspection's corner takes a TIF"})
        run.assert_not_called()
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(TIFS, return_value=[]):
            response = admin_client.post(self.URL)
        assert (response.status_code, response.json()) == (409, {"detail": "This ramp already has a TIF on this inspection"})

    def test_an_oe_cant_and_the_inspections_lock_holds(self, admin_client):
        with signed_in(INSPECTOR_ROW) as oe, patch(PROJECTS, return_value=AS_OE), \
             patch(INSPECTIONS, return_value=[INSPECTION]), patch(TIFS) as run:
            assert oe.post(self.URL).json() == {"detail": "Role required: inspector/re"}
        with patch(INSPECTIONS, return_value=[{**INSPECTION, "status": "approved", "re_uuid": ADMIN_ID}]), patch(TIFS):
            assert admin_client.post(self.URL).status_code == 409
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(INSPECTIONS, return_value=[{**INSPECTION, "status": "submitted"}]):
            assert inspector.post(self.URL).json() == {"detail": "Inspection is submitted — only the RE can edit"}
        run.assert_not_called()

    def test_an_inspection_lists_its_tifs(self, admin_client):
        with patch(INSPECTIONS, return_value=[INSPECTION]), patch(TIFS, return_value=[DRAFT, APPROVED]) as run:
            data = admin_client.get(f"/v1/ped-ramp-inspections/{INSPECTION_ID}/tifs").json()["data"]
        assert [tif["status"] for tif in data] == ["draft", "approved"] and data[1]["signed"] is True
        assert "ORDER BY t.created_at" in run.call_args.args[0]


class TestEdit:
    def test_a_draft_is_merged_and_written_whole(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(TIFS, side_effect=[[DRAFT], [{**DRAFT, "element_running_slope": True}]]) as run:
            response = inspector.patch(URL, json={"element_running_slope": True, "why_requirements_cannot_be_achieved": "  Vault ",
                                                  "dot_ramp_id": None})
        assert response.status_code == 200 and response.json()["data"]["element_running_slope"] is True
        sql, params = run.call_args.args
        assert "UPDATE icid.ped_ramp_tifs tif" in sql and "tif.status = 'draft'" in sql and params[-1] == TIF_ID
        assert "Vault" in params and "QN-123" in params and True in params
        assert "R1" not in params  # cleared

    def test_approved_is_frozen(self, admin_client):
        with patch(TIFS, return_value=[APPROVED]) as run:
            response = admin_client.patch(URL, json={"element_dws": True})
        assert (response.status_code, response.json()) == (409, {"detail": "TIF is approved — no further edits"})
        assert run.call_count == 1

    def test_unknown_fields_and_long_text_are_422(self, admin_client):
        for body in ({"status": "approved"}, {"project_category": "federal"}, {"email": "x" * 201}):
            with patch(TIFS, return_value=[DRAFT]) as run:
                assert admin_client.patch(URL, json=body).status_code == 422, body
            assert all("UPDATE" not in call.args[0] for call in run.call_args_list)


class TestApprove:
    BODY = {"signer_name": " Rita Engineer ", "signer_ny_license_no": "PE-1234"}

    def test_the_re_signs_with_name_licence_and_a_copy_of_their_signature(self):
        with signed_in(RE_ROW) as re_user, patch(PROJECTS, return_value=AS_RE), \
             patch(SNAPSHOT, return_value=f"ped-ramp-tifs/{TIF_ID}/re_1.png") as snapshot, \
             patch(TIFS, side_effect=[[DRAFT], [APPROVED]]) as run:
            response = re_user.post(f"{URL}/approve", json=self.BODY)
        assert response.status_code == 200 and response.json()["data"]["status"] == "approved"
        snapshot.assert_called_once_with("users/b3/signature.png", TIF_ID)
        sql, params = run.call_args.args
        assert "SET status = 'approved'" in sql and "i.status = 'approved'" in sql
        assert params == (RE_ROW["uuid"], "Rita Engineer", "PE-1234", f"ped-ramp-tifs/{TIF_ID}/re_1.png", TIF_ID)

    def test_an_inspector_cant_and_blanks_are_422(self, admin_client):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(TIFS, return_value=[DRAFT]), patch(SNAPSHOT) as snapshot:
            assert inspector.post(f"{URL}/approve", json=self.BODY).json() == {"detail": "Role required: re"}
            assert admin_client.post(f"{URL}/approve", json={**self.BODY, "signer_ny_license_no": "  "}).status_code == 422
        snapshot.assert_not_called()

    def test_no_signature_is_400_and_an_approved_inspection_409(self):
        with signed_in(UNSIGNED_USER_ROW) as admin, patch(TIFS, return_value=[DRAFT]), patch(SNAPSHOT) as snapshot:
            assert admin.post(f"{URL}/approve", json=self.BODY).status_code == 400
        with signed_in(ADMIN_USER_ROW) as admin, patch(TIFS, return_value=[{**DRAFT, "inspection_status": "approved"}]), \
             patch(SNAPSHOT):
            assert admin.post(f"{URL}/approve", json=self.BODY).json() == {"detail": "Inspection is approved — no further edits"}
        snapshot.assert_not_called()


class TestDeleteAndDownload:
    def test_only_an_admin_deletes_with_its_photos(self, admin_client):
        with signed_in(RE_ROW) as re_user, patch(PROJECTS, return_value=AS_RE), patch(TIFS, return_value=[DRAFT]) as run:
            assert re_user.delete(URL).status_code == 403
        assert run.call_count == 1
        with patch(TIFS, side_effect=[[DRAFT], [{"id": TIF_ID, "photo_paths": []}]]) as run:
            assert admin_client.delete(URL).status_code == 204
        sql = run.call_args.args[0]
        assert "DELETE FROM icid.ped_ramp_photos ph USING target" in sql and "i.status <> 'approved'" in sql

    def test_an_approved_inspections_tif_stays(self, admin_client):
        with patch(TIFS, return_value=[{**APPROVED, "inspection_status": "approved"}]) as run:
            assert admin_client.delete(URL).status_code == 409
        assert run.call_count == 1

    def test_its_form_downloads_through_a_signed_url(self, admin_client):
        signed = {"url": "https://s/t", "expires_at": CREATED.isoformat(), "file_name": "t.pdf"}
        with patch(TIFS, return_value=[APPROVED]), patch(CORNERS, return_value=[CORNER_ROW]), \
             patch("api.v1.ped_ramp_tifs.store_tif", return_value="tifs/x.pdf") as store, \
             patch("api.v1.ped_ramp_tifs.signed_download", return_value=signed) as sign:
            response = admin_client.get(f"{URL}/dot-pdf")
        assert response.status_code == 200 and response.json()["data"]["url"] == "https://s/t"
        store.assert_called_once()
        sign.assert_called_once_with("tifs/x.pdf", "3rd_Ave_and_38_Street_TIF_R1.pdf")

    def test_a_form_that_cant_be_filled_is_502(self, admin_client):
        with patch(TIFS, return_value=[DRAFT]), \
             patch("api.v1.ped_ramp_tifs.store_tif", side_effect=PedRampExportError("Could not fill the DOT TIF")):
            response = admin_client.get(f"{URL}/dot-pdf")
        assert (response.status_code, response.json()) == (502, {"detail": "Could not fill the DOT TIF"})
