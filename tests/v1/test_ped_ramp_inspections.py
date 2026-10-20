from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from api.services.signatures import SignatureStorageError
from tests.conftest import ADMIN_USER_ROW, UNSIGNED_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/ped-ramp-corners/{corner_id}/inspection and /v1/ped-ramp-inspections/{inspection_id} (Pedramp Phase 3, S1)
# Mock rows are dicts, as run_query returns them. A project row stands in for both checks that read project_users:
# being on the project (any row) and the roles held (each row's role).
# ---------------------------------------------------------------------------

INSPECTIONS = "api.queries.ped_ramp_inspections.run_query"
CORNERS = "api.queries.ped_ramp_corners.run_query"
PROJECTS = "api.queries.projects.run_query"
SNAPSHOT = "api.v1.ped_ramp_inspections.snapshot_signature_for_inspection"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
INSPECTION_ID = UUID("4f3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b6c")
URL = f"/v1/ped-ramp-inspections/{INSPECTION_ID}"
CORNER_URL = f"/v1/ped-ramp-corners/{CORNER_ID}/inspection"
CREATED = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
RE_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000003"), "role": None}
AS_INSPECTOR = [{"role": "inspector"}]
AS_RE = [{"role": "re"}]
AS_OE = [{"role": "oe"}]
CORNER_ROW = {"corner_id": CORNER_ID, "project_id": "HWS0023", "boro": "Queens", "north_south_street": "3rd Ave",
              "east_west_street": "38 Street", "corner_direction": "NE", "created_at": CREATED, "updated_at": CREATED,
              "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None, "ramps": []}
DRAFT = {"id": INSPECTION_ID, "corner_id": CORNER_ID, "project_id": "HWS0023", "north_south_street": "3rd Ave",
         "east_west_street": "38 Street", "corner_direction": "NE", "status": "draft", "requirements_met": None,
         "inspector_uuid": None, "inspector_name": None, "submitted_at": None, "inspector_signed_at": None,
         "inspector_signature_path": None, "re_uuid": None, "re_name": None, "re_signed_at": None,
         "re_signature_path": None, "return_comment": None, "returned_at": None, "returned_by": None,
         "returned_by_name": None, "dot_pdf_path": None, "created_at": CREATED, "updated_at": CREATED,
         "created_by": ADMIN_ID, "tif_count": 0, "draft_tif_count": 0}
SUBMITTED = {**DRAFT, "status": "submitted", "inspector_uuid": INSPECTOR_ROW["uuid"], "inspector_name": "Ada Admin",
             "submitted_at": CREATED, "inspector_signed_at": CREATED,
             "inspector_signature_path": f"ped-ramp-inspections/{INSPECTION_ID}/inspector_x.png"}
APPROVED = {**SUBMITTED, "status": "approved", "re_uuid": RE_ROW["uuid"], "re_signed_at": CREATED,
            "re_signature_path": f"ped-ramp-inspections/{INSPECTION_ID}/re_x.png"}


class TestCornerInspection:
    def test_a_corner_without_one_reads_null(self, admin_client):
        with patch(CORNERS, return_value=[CORNER_ROW]), patch(INSPECTIONS, return_value=[]) as run:
            response = admin_client.get(CORNER_URL)
        assert response.status_code == 200
        assert response.json()["data"] is None and response.json()["message"] == "No inspection yet"
        sql, params = run.call_args.args
        assert "ORDER BY (i.status <> 'approved') DESC, i.created_at DESC" in sql and params == (CORNER_ID,)

    def test_a_member_reads_the_corners_inspection_without_its_paths(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=AS_OE), patch(INSPECTIONS, return_value=[{**APPROVED, "dot_pdf_path": "x.pdf"}]):
            data = inspector.get(CORNER_URL).json()["data"]
        assert data["status"] == "approved" and data["has_dot_pdf"] is True and "dot_pdf_path" not in data
        assert data["inspector_name"] == "Ada Admin"

    def test_someone_off_the_project_is_403(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=[]), patch(INSPECTIONS) as run:
            assert inspector.get(CORNER_URL).status_code == 403
        run.assert_not_called()


class TestStart:
    def test_an_inspector_starts_a_draft(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=AS_INSPECTOR), patch(INSPECTIONS, return_value=[DRAFT]) as run:
            response = inspector.post(f"{CORNER_URL}/start")
        assert response.status_code == 201 and response.json()["data"]["status"] == "draft"
        sql, params = run.call_args.args
        assert "INSERT INTO icid.ped_ramp_inspections (corner_id, created_by)" in sql
        assert "NOT EXISTS (SELECT 1 FROM icid.ped_ramp_inspections other" in sql
        assert params == (INSPECTOR_ROW["uuid"], CORNER_ID)

    def test_a_second_start_is_409(self, admin_client):
        with patch(CORNERS, return_value=[CORNER_ROW]), patch(INSPECTIONS, return_value=[]):
            response = admin_client.post(f"{CORNER_URL}/start")
        assert (response.status_code, response.json()) == (409, {"detail": "This corner already has an inspection"})

    def test_an_oe_and_a_demo_user_cant_start(self, demo_client):
        with signed_in(INSPECTOR_ROW) as oe, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=AS_OE), patch(INSPECTIONS) as run:
            response = oe.post(f"{CORNER_URL}/start")
        assert (response.status_code, response.json()) == (403, {"detail": "Role required: inspector/re"})
        with patch(CORNERS, return_value=[{**CORNER_ROW, "project_id": "DEMO01"}]), patch(PROJECTS, return_value=AS_INSPECTOR):
            assert demo_client.post(f"{CORNER_URL}/start").status_code == 403
        run.assert_not_called()

    def test_a_deleted_corner_is_400(self, admin_client):
        with patch(CORNERS, return_value=[{**CORNER_ROW, "deleted_at": CREATED}]), patch(INSPECTIONS) as run:
            assert admin_client.post(f"{CORNER_URL}/start").json() == {"detail": "Corner is deleted"}
        run.assert_not_called()


class TestRequirementsMet:
    def test_an_inspector_answers_on_a_draft(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(INSPECTIONS, side_effect=[[DRAFT], [{**DRAFT, "requirements_met": False}]]) as run:
            response = inspector.patch(URL, json={"requirements_met": False})
        assert response.status_code == 200 and response.json()["data"]["requirements_met"] is False
        sql, params = run.call_args.args
        assert "WHERE id = %s AND status <> 'approved'" in sql and params == (False, INSPECTION_ID)

    def test_submitted_only_the_re_answers(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(INSPECTIONS, return_value=[SUBMITTED]) as run:
            response = inspector.patch(URL, json={"requirements_met": True})
        assert (response.status_code, response.json()) == (403, {"detail": "Inspection is submitted — only the RE can edit"})
        assert run.call_count == 1
        with signed_in(RE_ROW) as re_user, patch(PROJECTS, return_value=AS_RE), \
             patch(INSPECTIONS, side_effect=[[SUBMITTED], [SUBMITTED]]):
            assert re_user.patch(URL, json={"requirements_met": True}).status_code == 200

    def test_approved_is_frozen_for_everyone(self, admin_client):
        with patch(INSPECTIONS, return_value=[APPROVED]) as run:
            response = admin_client.patch(URL, json={"requirements_met": True})
        assert (response.status_code, response.json()) == (409, {"detail": "Inspection is approved — no further edits"})
        assert run.call_count == 1

    def test_only_the_answer_is_taken(self, admin_client):
        with patch(INSPECTIONS, return_value=[DRAFT]):
            assert admin_client.patch(URL, json={"requirements_met": True, "status": "approved"}).status_code == 422


class TestSubmit:
    def test_an_inspector_submits_signing_with_a_copy_of_their_signature(self):
        signer = {**INSPECTOR_ROW, "signature_path": "users/b2/signature.png"}
        with signed_in(signer) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(SNAPSHOT, return_value="ped-ramp-inspections/i/inspector_1.png") as snapshot, \
             patch(INSPECTIONS, side_effect=[[DRAFT], [SUBMITTED]]) as run:
            response = inspector.post(f"{URL}/submit")
        assert response.status_code == 200 and response.json()["message"] == "Inspection submitted"
        snapshot.assert_called_once_with("users/b2/signature.png", INSPECTION_ID, "inspector")
        sql, params = run.call_args.args
        assert "SET status = 'submitted'" in sql and "return_comment = NULL" in sql and "status = 'draft'" in sql
        assert params == (INSPECTOR_ROW["uuid"], "ped-ramp-inspections/i/inspector_1.png", INSPECTION_ID)

    def test_an_re_alone_cant_submit(self):
        with signed_in(RE_ROW) as re_user, patch(PROJECTS, return_value=AS_RE), patch(INSPECTIONS, return_value=[DRAFT]), \
             patch(SNAPSHOT) as snapshot:
            assert re_user.post(f"{URL}/submit").json() == {"detail": "Role required: inspector"}
        snapshot.assert_not_called()

    def test_no_signature_is_400_and_a_submitted_one_409(self):
        with signed_in(UNSIGNED_USER_ROW) as admin, patch(INSPECTIONS, return_value=[DRAFT]), patch(SNAPSHOT) as snapshot:
            response = admin.post(f"{URL}/submit")
        assert (response.status_code, response.json()) == (400, {"detail": "Signature required before signing an inspection"})
        snapshot.assert_not_called()
        with signed_in(ADMIN_USER_ROW) as admin, patch(INSPECTIONS, return_value=[SUBMITTED]), patch(SNAPSHOT) as snapshot:
            assert admin.post(f"{URL}/submit").status_code == 409
        snapshot.assert_not_called()

    def test_a_failed_copy_is_502_and_submits_nothing(self, admin_client):
        with patch(INSPECTIONS, return_value=[DRAFT]) as run, \
             patch(SNAPSHOT, side_effect=SignatureStorageError("Could not copy the signature for this inspection")):
            response = admin_client.post(f"{URL}/submit")
        assert (response.status_code, response.json()) == (502, {"detail": "Could not copy the signature for this inspection"})
        assert run.call_count == 1

    def test_a_lost_race_is_409(self, admin_client):
        with patch(INSPECTIONS, side_effect=[[DRAFT], []]), patch(SNAPSHOT, return_value="p"):
            assert admin_client.post(f"{URL}/submit").status_code == 409


class TestReturn:
    def test_the_re_returns_it_with_a_comment(self):
        with signed_in(RE_ROW) as re_user, patch(PROJECTS, return_value=AS_RE), \
             patch(INSPECTIONS, side_effect=[[SUBMITTED], [{**DRAFT, "return_comment": "Check slopes"}]]) as run:
            response = re_user.post(f"{URL}/return", json={"comment": "  Check slopes "})
        assert response.status_code == 200 and response.json()["data"]["return_comment"] == "Check slopes"
        sql, params = run.call_args.args
        assert "SET status = 'draft', return_comment = %s" in sql and "status = 'submitted'" in sql
        assert params == ("Check slopes", RE_ROW["uuid"], INSPECTION_ID)

    def test_a_comment_is_required(self, admin_client):
        with patch(INSPECTIONS, return_value=[SUBMITTED]) as run:
            assert admin_client.post(f"{URL}/return", json={"comment": "   "}).status_code == 422
            assert admin_client.post(f"{URL}/return", json={}).status_code == 422
        # the inspection is read, never written
        assert all("UPDATE" not in call.args[0] for call in run.call_args_list)

    def test_an_inspector_cant_return_and_a_draft_cant_be_returned(self, admin_client):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(INSPECTIONS, return_value=[SUBMITTED]):
            assert inspector.post(f"{URL}/return", json={"comment": "x"}).json() == {"detail": "Role required: re"}
        with patch(INSPECTIONS, return_value=[DRAFT]):
            assert admin_client.post(f"{URL}/return", json={"comment": "x"}).status_code == 409


class TestApprove:
    def test_the_re_approves_signing_it(self):
        signer = {**RE_ROW, "signature_path": "users/b3/signature.png"}
        with signed_in(signer) as re_user, patch(PROJECTS, return_value=AS_RE), \
             patch(SNAPSHOT, return_value="ped-ramp-inspections/i/re_1.png") as snapshot, \
             patch(INSPECTIONS, side_effect=[[SUBMITTED], [APPROVED]]) as run:
            response = re_user.post(f"{URL}/approve")
        assert response.status_code == 200 and response.json()["data"]["status"] == "approved"
        snapshot.assert_called_once_with("users/b3/signature.png", INSPECTION_ID, "re")
        sql, params = run.call_args.args
        assert "SET status = 'approved'" in sql and "t.status = 'draft'" in sql
        assert params == (RE_ROW["uuid"], "ped-ramp-inspections/i/re_1.png", None, INSPECTION_ID)

    def test_a_draft_tif_blocks_it(self, admin_client):
        with patch(INSPECTIONS, return_value=[{**SUBMITTED, "tif_count": 2, "draft_tif_count": 1}]), \
             patch(SNAPSHOT) as snapshot:
            response = admin_client.post(f"{URL}/approve")
        assert (response.status_code, response.json()) == (409, {"detail": "Approve or delete this inspection's draft TIFs first"})
        snapshot.assert_not_called()

    def test_an_inspector_cant_approve_and_a_draft_cant_be_approved(self, admin_client):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=AS_INSPECTOR), \
             patch(INSPECTIONS, return_value=[SUBMITTED]):
            assert inspector.post(f"{URL}/approve").status_code == 403
        with patch(INSPECTIONS, return_value=[DRAFT]):
            assert admin_client.post(f"{URL}/approve").status_code == 409


class TestTheLock:
    """A corner's inspection keeps its corner and its ramps: submitted, the RE's or an admin's; approved, nobody's."""

    PROPERTY = {"id": UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e61"), "project_id": "HWS0023", "kind": "ped_ramp",
                "boro": "Queens", "intersection_street_1": "3rd Ave", "intersection_street_2": "38 Street",
                "quadrant": "NE", "facing_street": "street_2", "ramp_type": "PERPENDICULAR", "corner_id": CORNER_ID,
                "created_at": CREATED, "updated_at": CREATED, "created_by": ADMIN_ID, "deleted_at": None,
                "deleted_by": None, "task_order_id": None, "task_order_no": None}

    def test_a_submitted_corner_refuses_its_inspector_and_takes_its_re(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]) as corners, \
             patch(PROJECTS, return_value=AS_INSPECTOR), patch(INSPECTIONS, return_value=[SUBMITTED]):
            response = inspector.patch(f"/v1/ped-ramp-corners/{CORNER_ID}", json={"inspected_by": "RG"})
        assert (response.status_code, response.json()) == (403, {"detail": "Inspection is submitted — only the RE can edit"})
        assert corners.call_count == 1  # read, never written
        with signed_in(RE_ROW) as re_user, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=AS_RE), patch(INSPECTIONS, return_value=[SUBMITTED]):
            assert re_user.patch(f"/v1/ped-ramp-corners/{CORNER_ID}", json={"inspected_by": "RG"}).status_code == 200

    def test_an_approved_corner_refuses_even_an_admin(self, admin_client):
        with patch(CORNERS, return_value=[CORNER_ROW]), patch(INSPECTIONS, return_value=[APPROVED]):
            for response in (admin_client.patch(f"/v1/ped-ramp-corners/{CORNER_ID}", json={"inspected_by": "RG"}),
                             admin_client.delete(f"/v1/ped-ramp-corners/{CORNER_ID}")):
                assert (response.status_code, response.json()) == (409, {"detail": "Inspection is approved — no further edits"})

    def test_a_ramps_attributes_and_the_ramp_itself_are_locked_with_it(self, admin_client):
        with patch("api.queries.properties.run_query", return_value=[self.PROPERTY]), \
             patch("api.queries.ped_ramp_attributes.run_query") as attributes, patch(INSPECTIONS, return_value=[APPROVED]):
            put = admin_client.put(f"/v1/properties/{self.PROPERTY['id']}/ped-ramp-attributes", json={})
            edit = admin_client.patch(f"/v1/properties/{self.PROPERTY['id']}", json={"quadrant": "NW"})
            delete = admin_client.post(f"/v1/properties/{self.PROPERTY['id']}/admin/soft-delete")
        assert [put.status_code, edit.status_code, delete.status_code] == [409, 409, 409]
        attributes.assert_not_called()

    def test_adding_a_ramp_at_a_locked_corner_is_refused(self, admin_client):
        body = {"boro": "Queens", "north_south_street": "3rd Ave", "east_west_street": "38 Street",
                "corner_direction": "NE", "quadrant": "NE", "facing_street": "street_2", "ramp_type": "PARALLEL"}
        with patch(PROJECTS, return_value=[{"project_id": "HWS0023"}]), \
             patch(CORNERS, return_value=[{"corner_id": CORNER_ID}]) as corners, patch(INSPECTIONS, return_value=[APPROVED]):
            response = admin_client.post("/v1/projects/HWS0023/pedramp-sites", json=body)
        assert response.status_code == 409 and corners.call_count == 1  # the lookup only, no insert

    def test_a_draft_or_no_inspection_locks_nothing(self, admin_client):
        for found in ([], [DRAFT]):
            with patch(CORNERS, return_value=[CORNER_ROW]), patch(INSPECTIONS, return_value=found):
                assert admin_client.patch(f"/v1/ped-ramp-corners/{CORNER_ID}", json={"inspected_by": "RG"}).status_code == 200
