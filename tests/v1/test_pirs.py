from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/properties/{property_id}/pir(s) and /v1/pirs/{pir_id}
# Mock rows are dicts, as run_query returns them: a PIR's own columns plus its property's project.
# ---------------------------------------------------------------------------

PIRS = "api.queries.pirs.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
ROOT = Path(__file__).resolve().parents[2]

ADMIN_ID = ADMIN_USER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
OTHER_PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9d")
PROPERTY_PIR_URL = f"/v1/properties/{PROPERTY_ID}/pir"
PROPERTY_PIRS_URL = f"/v1/properties/{PROPERTY_ID}/pirs"
URL = f"/v1/pirs/{PIR_ID}"
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": date(2026, 9, 30),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}
DELETED_ROW = {**PIR_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID, "updated_at": DELETED}
OTHER_ROW = {**PIR_ROW, "id": OTHER_PIR_ID, "inspector_initials": "MK"}
BODY = {"inspector_initials": "JD", "inspector_sign_date": "2026-09-30"}

INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})
ON_PROJECT = [{"?column?": 1}]


class TestCreatePir:
    def test_an_admin_digitizes_a_pir_as_themselves_now(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            response = admin_client.post(PROPERTY_PIR_URL, json={**BODY, "inspector_initials": " JD "})
        assert response.status_code == 201 and response.json()["message"] == "PIR created"
        data = response.json()["data"]
        assert data["digitized_by"] == str(ADMIN_ID) and data["digitized_at"] is not None
        assert data["as_built_signed_by"] is None and data["as_built_signed_at"] is None
        assert data["source_image_url"] is None and data["project_id"] == "HWS0023"
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        assert "INSERT INTO icid.pirs AS pir (property_id, inspector_initials, inspector_sign_date, digitized_by)" \
            in one_line
        assert "FROM icid.properties p WHERE p.id = %s AND p.deleted_at IS NULL ON CONFLICT DO NOTHING" in one_line
        assert params == ("JD", date(2026, 9, 30), ADMIN_ID, PROPERTY_ID)

    def test_the_body_cant_name_the_digitizer(self, admin_client):
        other = "b0000000-0000-4000-8000-000000000002"
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            admin_client.post(PROPERTY_PIR_URL, json={**BODY, "digitized_by": other, "digitized_at": "2020-01-01"})
        assert run.call_args.args[1][2] == ADMIN_ID

    def test_a_second_live_pir_on_the_property_is_409(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, side_effect=[[], [OTHER_ROW]]):
            response = admin_client.post(PROPERTY_PIR_URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": "This property already has a PIR", "existing_pir_id": str(OTHER_PIR_ID)}

    def test_soft_deleting_the_live_pir_lets_a_new_one_be_created(self, admin_client):
        with patch(PIRS, side_effect=[[PIR_ROW], [DELETED_ROW]]):
            assert admin_client.post(f"{URL}/admin/soft-delete").status_code == 200
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[OTHER_ROW]):
            response = admin_client.post(PROPERTY_PIR_URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["id"] == str(OTHER_PIR_ID)

    def test_the_unique_index_leaves_deleted_pirs_out(self):
        for path in (ROOT / "schema.sql", ROOT / "migrations" / "031_pirs.sql"):
            assert "CREATE UNIQUE INDEX uq_pirs_property ON icid.pirs (property_id) WHERE deleted_at IS NULL;" \
                in path.read_text(encoding="utf-8")

    def test_a_deleted_property_is_400(self, admin_client):
        with patch(PROPS, return_value=[{**PROPERTY_ROW, "deleted_at": DELETED}]), patch(PIRS) as run:
            response = admin_client.post(PROPERTY_PIR_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Cannot add a PIR to a deleted property"}
        run.assert_not_called()

    @pytest.mark.parametrize("initials", ["  ", "X" * 17])
    def test_blank_or_over_long_initials_are_400(self, admin_client, initials):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS) as run:
            response = admin_client.post(PROPERTY_PIR_URL, json={**BODY, "inspector_initials": initials})
        assert response.status_code == 400
        run.assert_not_called()

    def test_sixteen_characters_of_initials_are_taken(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            assert admin_client.post(PROPERTY_PIR_URL, json={**BODY, "inspector_initials": "X" * 16}).status_code == 201
        assert run.call_args.args[1][0] == "X" * 16

    def test_a_malformed_date_is_422(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS) as run:
            assert admin_client.post(PROPERTY_PIR_URL, json={**BODY, "inspector_sign_date": "9/30/26"}).status_code \
                == 422
        run.assert_not_called()

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]), patch(PIRS) as run:
            response = admin_client.post(PROPERTY_PIR_URL, json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}
        run.assert_not_called()


class TestGetPropertyPir:
    def test_a_user_on_the_project_gets_the_live_pir(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            response = inspector.get(PROPERTY_PIR_URL)
        assert response.status_code == 200 and response.json()["data"]["id"] == str(PIR_ID)
        sql, params = run.call_args.args
        assert "pir.property_id = %s AND pir.deleted_at IS NULL" in sql and params == (PROPERTY_ID,)

    def test_a_property_without_a_live_pir_is_404(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[]):
            response = admin_client.get(PROPERTY_PIR_URL)
        assert response.status_code == 404 and response.json() == {"detail": "No PIR for this property"}

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS) as run:
            response = inspector.get(PROPERTY_PIR_URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_on_another_project_gets_404(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS) as run:
            response = demo_client.get(PROPERTY_PIR_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}
        run.assert_not_called()


class TestListPropertyPirs:
    def test_an_admin_can_ask_for_the_deleted_ones_newest_first(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[OTHER_ROW, DELETED_ROW]) as run:
            response = admin_client.get(PROPERTY_PIRS_URL, params={"include_deleted": "true"})
        assert response.status_code == 200
        assert [p["id"] for p in response.json()["data"]] == [str(OTHER_PIR_ID), str(PIR_ID)]
        sql, params = run.call_args.args
        assert "(%s OR pir.deleted_at IS NULL)" in sql and "ORDER BY pir.digitized_at DESC;" in sql
        assert params == (PROPERTY_ID, True)

    def test_by_default_only_the_live_one(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            admin_client.get(PROPERTY_PIRS_URL)
        assert run.call_args.args[1] == (PROPERTY_ID, False)

    def test_include_deleted_from_a_user_who_isnt_an_admin_is_ignored(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            assert inspector.get(PROPERTY_PIRS_URL, params={"include_deleted": "true"}).status_code == 200
        assert run.call_args.args[1] == (PROPERTY_ID, False)

    def test_a_failed_read_is_500(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=None):
            assert admin_client.get(PROPERTY_PIRS_URL).status_code == 500


class TestGetPir:
    def test_a_user_on_its_project_reads_it(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PIRS, return_value=[DELETED_ROW]):
            response = inspector.get(URL)
        assert response.status_code == 200 and response.json()["data"]["deleted_at"] is not None

    def test_a_user_who_isnt_on_its_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(PIRS, return_value=[PIR_ROW]):
            assert inspector.get(URL).status_code == 403

    def test_a_demo_user_gets_404_for_another_projects(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PIRS, return_value=[PIR_ROW]):
            response = demo_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "PIR not found"}

    def test_an_unknown_pir_is_404(self, admin_client):
        with patch(PIRS, return_value=[]):
            assert admin_client.get(URL).status_code == 404


class TestUpdatePir:
    def test_an_admin_changes_the_initials_and_date(self, admin_client):
        changed = {**PIR_ROW, "inspector_initials": "JDX", "inspector_sign_date": date(2026, 10, 1)}
        with patch(PIRS, return_value=[changed]) as run:
            response = admin_client.patch(URL, json={"inspector_initials": " JDX ", "inspector_sign_date": "2026-10-01"})
        assert response.status_code == 200 and response.json()["data"]["inspector_initials"] == "JDX"
        sql, params = run.call_args.args
        assert "UPDATE icid.pirs pir" in sql and "updated_at = now()" in sql and "as_built" not in sql.split("RETURNING")[0]
        assert params == ("JDX", date(2026, 10, 1), False, None, PIR_ID)

    def test_a_null_counts_as_not_sent(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            assert admin_client.patch(URL, json={"inspector_initials": None, "inspector_sign_date": "2026-10-01"}) \
                .status_code == 200
        assert run.call_args.args[1] == (None, date(2026, 10, 1), False, None, PIR_ID)

    def test_the_north_bearing_is_set(self, admin_client):
        with patch(PIRS, return_value=[{**PIR_ROW, "north_bearing": "NE"}]) as run:
            response = admin_client.patch(URL, json={"north_bearing": "NE"})
        assert response.status_code == 200 and response.json()["data"]["north_bearing"] == "NE"
        sql, params = run.call_args.args
        assert "north_bearing = CASE WHEN %s THEN %s::icid.compass_point ELSE pir.north_bearing END" in sql
        assert params == (None, None, True, "NE", PIR_ID)

    def test_a_null_north_bearing_clears_it(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            assert admin_client.patch(URL, json={"north_bearing": None}).status_code == 200
        assert run.call_args.args[1] == (None, None, True, None, PIR_ID)

    def test_a_north_bearing_that_isnt_a_compass_point_is_422(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            assert admin_client.patch(URL, json={"north_bearing": "NNE"}).status_code == 422
        assert run.call_count <= 1  # nothing written

    def test_nothing_to_change_is_400(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            response = admin_client.patch(URL, json={"inspector_initials": None, "inspector_sign_date": None})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1  # the PIR read, nothing written

    def test_the_as_built_signature_cant_be_set_here(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            response = admin_client.patch(URL, json={"as_built_signed_by": str(ADMIN_ID),
                                                     "as_built_signed_at": "2026-10-14T09:00:00Z"})
        assert response.status_code == 400  # the fields aren't PirUpdate's, so nothing is sent
        assert run.call_count == 1

    def test_blank_initials_are_400(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            assert admin_client.patch(URL, json={"inspector_initials": " "}).status_code == 400
        assert run.call_count == 1

    def test_an_admin_edits_a_deleted_pir_or_one_of_a_deleted_property(self, admin_client):
        with patch(PIRS, return_value=[DELETED_ROW]), patch(PROPS) as props:
            assert admin_client.patch(URL, json={"inspector_initials": "JD"}).status_code == 200
        props.assert_not_called()  # an admin's edit doesn't look at the property

    def test_an_unknown_pir_is_404(self, admin_client):
        with patch(PIRS, return_value=[]):
            response = admin_client.patch(URL, json={"inspector_initials": "JD"})
        assert response.status_code == 404 and response.json() == {"detail": "PIR not found"}


class TestProjectMembersWritePirs:
    """Creating and editing a PIR are open to anyone on the property's project; demo users and outsiders stay out."""

    def test_a_member_digitizes_a_pir_as_themselves(self):
        mine = {**PIR_ROW, "digitized_by": INSPECTOR_ROW["uuid"]}
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[mine]) as run:
            response = inspector.post(PROPERTY_PIR_URL, json=BODY)
        assert response.status_code == 201
        assert response.json()["data"]["digitized_by"] == str(INSPECTOR_ROW["uuid"])
        assert run.call_args.args[1][2] == INSPECTOR_ROW["uuid"]

    def test_a_member_cant_add_a_pir_to_a_deleted_property(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[{**PROPERTY_ROW, "deleted_at": DELETED}]), patch(PIRS) as run:
            response = inspector.post(PROPERTY_PIR_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Cannot add a PIR to a deleted property"}
        run.assert_not_called()

    def test_a_member_edits_a_pir(self):
        changed = {**PIR_ROW, "inspector_initials": "MK"}
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PIRS, return_value=[changed]), patch(PROPS, return_value=[PROPERTY_ROW]):
            response = inspector.patch(URL, json={"inspector_initials": "MK"})
        assert response.status_code == 200 and response.json()["data"]["inspector_initials"] == "MK"

    def test_a_member_cant_edit_a_deleted_pir(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PIRS, return_value=[DELETED_ROW]) as run, patch(PROPS, return_value=[PROPERTY_ROW]):
            response = inspector.patch(URL, json={"inspector_initials": "MK"})
        assert response.status_code == 400 and response.json() == {"detail": "PIR is deleted"}
        assert run.call_count == 1  # read, nothing written

    def test_a_member_cant_edit_a_live_pir_of_a_deleted_property(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PIRS, return_value=[PIR_ROW]) as run, \
                patch(PROPS, return_value=[{**PROPERTY_ROW, "deleted_at": DELETED}]):
            response = inspector.patch(URL, json={"inspector_initials": "MK"})
        assert response.status_code == 400 and response.json() == {"detail": "Property is deleted"}
        assert run.call_count == 1

    def test_a_user_who_isnt_on_the_project_cant_create_or_edit(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PIRS, return_value=[PIR_ROW]) as run:
            created = inspector.post(PROPERTY_PIR_URL, json=BODY)
            edited = inspector.patch(URL, json={"inspector_initials": "MK"})
        for response in (created, edited):
            assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert run.call_count == 1  # only the PIR read for the edit's check

    def test_a_demo_user_cant_create_or_edit_on_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, return_value=[PIR_ROW]) as run:
            created = demo_client.post(PROPERTY_PIR_URL, json=BODY)
            edited = demo_client.patch(URL, json={"inspector_initials": "MK"})
        for response in (created, edited):
            assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        assert run.call_count == 1

    def test_a_demo_user_gets_404_on_another_project(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PIRS, return_value=[PIR_ROW]):
            created = demo_client.post(PROPERTY_PIR_URL, json=BODY)
            edited = demo_client.patch(URL, json={"inspector_initials": "MK"})
        assert created.status_code == 404 and created.json() == {"detail": "Property not found"}
        assert edited.status_code == 404 and edited.json() == {"detail": "PIR not found"}

    @pytest.mark.parametrize("action", ["soft-delete", "undelete"])
    def test_a_member_who_isnt_an_admin_cant_delete_or_restore(self, action):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT), patch(PIRS) as run:
            response = inspector.post(f"{URL}/admin/{action}")
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()


class TestSoftDeleteAndUndeletePir:
    def test_an_admin_soft_deletes_it(self, admin_client):
        with patch(PIRS, side_effect=[[PIR_ROW], [DELETED_ROW]]) as run:
            response = admin_client.post(f"{URL}/admin/soft-delete")
        assert response.status_code == 200 and response.json()["message"] == "PIR deleted"
        assert response.json()["data"]["deleted_by"] == str(ADMIN_ID)
        sql, params = run.call_args.args
        assert "SET deleted_at = now()" in sql and "pir.deleted_at IS NULL" in sql and params == (ADMIN_ID, PIR_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(PIRS, return_value=[DELETED_ROW]) as run:
            response = admin_client.post(f"{URL}/admin/soft-delete")
        assert response.status_code == 200 and response.json()["message"] == "PIR was already deleted"
        assert run.call_count == 1

    def test_an_admin_brings_it_back(self, admin_client):
        with patch(PIRS, side_effect=[[DELETED_ROW], [PIR_ROW]]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 200 and response.json()["message"] == "PIR restored"
        assert response.json()["data"]["deleted_at"] is None and response.json()["data"]["deleted_by"] is None
        sql = run.call_args.args[0]
        assert "SET deleted_at = NULL" in sql and "deleted_by = NULL" in sql and "other.property_id = pir.property_id" in sql

    def test_undeleting_one_that_isnt_deleted_is_404(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 404 and response.json() == {"detail": "PIR is not deleted"}
        assert run.call_count == 1

    def test_another_live_pir_on_the_property_is_409(self, admin_client):
        with patch(PIRS, side_effect=[[DELETED_ROW], [], [OTHER_ROW]]):
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 409
        assert response.json() == {"detail": "This property now has another PIR", "existing_pir_id": str(OTHER_PIR_ID)}

    def test_an_unknown_pir_is_404(self, admin_client):
        with patch(PIRS, return_value=[]):
            assert admin_client.post(f"{URL}/admin/soft-delete").status_code == 404
            assert admin_client.post(f"{URL}/admin/undelete").status_code == 404

    @pytest.mark.parametrize("action", ["soft-delete", "undelete"])
    def test_a_user_who_isnt_an_admin_is_forbidden(self, action):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PIRS) as run:
            response = inspector.post(f"{URL}/admin/{action}")
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()
