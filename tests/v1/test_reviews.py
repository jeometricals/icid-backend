from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
from psycopg.errors import UniqueViolation
from starlette.testclient import TestClient

from api.index import app
from api.queries.idrs import IdrNumberTakenError, accept_stage1
from api.schemas.auth import UserOut
from api.services.auth import auth_provider
from api.services.signatures import SignatureStorageError
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match IDR_COLUMNS in api/queries/idrs.py.
# ---------------------------------------------------------------------------

IDR_ID = "9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e"
OTHER_IDR_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)

# Three people on HWS0023, none of them an admin: the inspector, and two reviewers
INSPECTOR = {"uuid": UUID("c0000000-0000-4000-8000-000000000003"), "email": "KhanG@magnoleng.pc",
             "first_name": "Genghis", "last_name": "Khan", "client_id": "C00001", "role": None, "is_demo": False,
             "signature_path": "users/c0000000-0000-4000-8000-000000000003/signature.png", "signature_type": "drawn",
             "signature_set_at": NOW}
REVIEWER = {**INSPECTOR, "uuid": UUID("f0000000-0000-4000-8000-000000000006"), "email": "olive@icid.local",
            "first_name": "Olive", "last_name": "Engineer",
            "signature_path": "users/f0000000-0000-4000-8000-000000000006/signature.png"}
OTHER_REVIEWER = {**REVIEWER, "uuid": UUID("f0000000-0000-4000-8000-000000000007"), "email": "rex@icid.local",
                  "first_name": "Rex", "signature_path": "users/f0000000-0000-4000-8000-000000000007/signature.png"}

SUBMITTED_IDR = {
    "idr_id": UUID(IDR_ID), "project_id": "HWS0023", "reporter_uuid": INSPECTOR["uuid"],
    "report_date": date(2026, 10, 5), "work_start_time": None, "work_end_time": None, "inspector_start_time": None,
    "inspector_end_time": None, "temp_low": None, "temp_high": None, "weather_am": None, "weather_pm": None,
    "total_pages": 2, "has_dismissed_auto_general": False, "status": "submitted", "submitted_at": NOW,
    "created_at": NOW, "updated_at": NOW,
    "inspector_signature_path": f"idrs/{IDR_ID}/inspector_0123456789abcdef0123456789abcdef.png",
    "inspector_signed_at": NOW, "idr_number": None, "stage1_reviewer_uuid": None, "stage1_reviewed_at": None,
    "re_reviewer_uuid": None, "re_signature_path": None, "re_signed_at": None, "return_reason": None,
    "returned_from": None,
}
STAGE1_IDR = {**SUBMITTED_IDR, "status": "stage1_review", "idr_number": "005", "stage1_reviewer_uuid": REVIEWER["uuid"]}
STAGE2_IDR = {**STAGE1_IDR, "status": "stage2_review", "stage1_reviewed_at": NOW, "re_reviewer_uuid": REVIEWER["uuid"]}
UNACCEPTED_STAGE2_IDR = {**STAGE2_IDR, "re_reviewer_uuid": None}
RE_SIGNATURE_COPY = f"idrs/{IDR_ID}/re_0123456789abcdef0123456789abcdef.png"
APPROVED_IDR = {**STAGE2_IDR, "status": "approved", "re_signature_path": RE_SIGNATURE_COPY, "re_signed_at": NOW}

CHANGED = {"detail": "IDR changed during review; reload and try again"}
NOT_THE_REVIEWER = {"detail": "Only the reviewer who accepted this IDR can do this"}


def url(action: str) -> str:
    """
    Build a review route's URL for the test IDR.
    Takes the action (the last path segment).
    Returns the path.
    """
    return f"/v1/idrs/{IDR_ID}/{action}"


@contextmanager
def signed_in(user_row: dict):
    """
    A TestClient signed in as a user, like the conftest fixtures but for any user row.
    Takes the row get_user_by_uuid returns for them.
    Yields the client; every request carries their bearer token.
    """
    token = auth_provider.issue_token(UserOut.model_validate(user_row))
    with patch("api.services.auth.get_user_by_uuid", return_value=user_row):
        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
            yield client


@contextmanager
def review(idr=SUBMITTED_IDR, roles=("oe",), moved="same", number_holder=None):
    """
    Patch the query layer under the review routes: the IDR read, the caller's roles on its project, the number lookup and the statement that moves the IDR.
    Takes the IDR row the reads return (None: no such IDR), the roles the caller holds on its project, what the moving statement returns ("same": the IDR row; or [] / None / an exception to raise) and the uuid of an IDR already holding the number asked for.
    Yields a dict: "moves" is the list of (sql, params) of every moving statement run, "lookups" the number lookups, "signature" the mock of the signature copy (it returns RE_SIGNATURE_COPY).
    """
    seen = {"moves": [], "lookups": []}

    def idrs_query(sql, params=None):
        """Stand in for run_query in api.queries.idrs."""
        if "UPDATE icid.idrs" in sql:
            seen["moves"].append((sql, params))
            if isinstance(moved, Exception):
                raise moved
            return [idr] if moved == "same" else moved
        if "idr_number = %s" in sql:
            seen["lookups"].append((sql, params))
            return [{"idr_id": UUID(number_holder)}] if number_holder else []
        return [idr] if idr else []

    with patch("api.queries.idrs.run_query", side_effect=idrs_query), \
         patch("api.queries.projects.run_query", return_value=[{"role": role} for role in roles]) as projects, \
         patch("api.v1.reviews.snapshot_signature_for_idr", return_value=RE_SIGNATURE_COPY) as signature:
        seen["projects"] = projects
        seen["signature"] = signature
        yield seen


def flat(sql: str) -> str:
    """
    Put a statement on one line.
    Takes the SQL.
    Returns it with every run of whitespace as one space.
    """
    return " ".join(sql.split())


# ---------------------------------------------------------------------------
# Who may call what: the project roles on each route
# ---------------------------------------------------------------------------

REVIEW_ROUTES = [
    ("accept-stage1", {"idr_number": "005"}, "oe/re"), ("approve-stage1", None, "oe/re"),
    ("accept-stage2", None, "re"), ("approve-stage2", None, "re"),
    ("return", {"to": "inspector", "comment": "fix it"}, "oe/re"),
]


class TestReviewRoles:
    @pytest.mark.parametrize("action,body,needed", REVIEW_ROUTES)
    def test_an_inspector_cant_call_a_review_route(self, action, body, needed):
        with signed_in(INSPECTOR) as client, review(roles=("inspector",)) as seen:
            response = client.post(url(action), json=body)
        assert response.status_code == 403 and response.json() == {"detail": f"Role required: {needed}"}
        assert seen["moves"] == []

    @pytest.mark.parametrize("action,body,needed", REVIEW_ROUTES)
    def test_a_user_with_no_role_on_the_project_cant_either(self, action, body, needed):
        with signed_in(REVIEWER) as client, review(roles=()) as seen:
            response = client.post(url(action), json=body)
        assert response.status_code == 403 and seen["moves"] == []

    @pytest.mark.parametrize("action", ["accept-stage2", "approve-stage2"])
    def test_stage_two_is_for_the_re_only(self, action):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("inspector", "oe")) as seen:
            response = client.post(url(action))
        assert response.status_code == 403 and response.json() == {"detail": "Role required: re"}
        assert seen["moves"] == []

    def test_an_inspector_who_is_also_an_oe_gets_in(self):
        with signed_in(INSPECTOR) as client, review(roles=("inspector", "oe")):
            response = client.post(url("accept-stage1"), json={"idr_number": "005"})
        assert response.status_code == 200

    def test_the_role_is_read_for_the_idrs_project(self):
        with signed_in(REVIEWER) as client, review() as seen:
            client.post(url("accept-stage1"), json={"idr_number": "005"})
        assert seen["projects"].call_args.args[1] == (REVIEWER["uuid"], "HWS0023")

    @pytest.mark.parametrize("action,body,needed", REVIEW_ROUTES)
    def test_an_idr_that_doesnt_exist_is_404(self, action, body, needed):
        for user in (REVIEWER, ADMIN_USER_ROW):
            with signed_in(user) as client, review(idr=None) as seen:
                response = client.post(url(action), json=body)
            assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}
            assert seen["moves"] == []

    @pytest.mark.parametrize("action,body,needed", REVIEW_ROUTES)
    def test_a_demo_user_gets_nowhere(self, demo_client, action, body, needed):
        own = {**STAGE2_IDR, "reporter_uuid": DEMO_USER_ROW["uuid"], "project_id": "DEMO01"}
        with review(idr=own, roles=("inspector",)) as seen:
            response = demo_client.post(url(action), json=body)
        assert response.status_code == 403 and seen["moves"] == []


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/accept-stage1
# ---------------------------------------------------------------------------

class TestAcceptStage1:
    url = url("accept-stage1")

    def test_an_oe_picks_a_submitted_idr_up_and_numbers_it(self):
        with signed_in(REVIEWER) as client, review(moved=[STAGE1_IDR]) as seen:
            response = client.post(self.url, json={"idr_number": "005"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["status"], data["idr_number"]) == ("stage1_review", "005")
        assert data["stage1_reviewer_uuid"] == str(REVIEWER["uuid"])
        sql, params = seen["moves"][0]
        assert "WHERE idr_id = %s AND status = %s AND deleted_at IS NULL" in flat(sql)
        assert "stage1_reviewer_uuid = %s" in sql and "idr_number = COALESCE(i.idr_number, %s)" in sql
        assert params == (UUID(IDR_ID), "submitted", "stage1_review", REVIEWER["uuid"], "005",
                          REVIEWER["uuid"], "accept_stage1", None)

    def test_an_re_can_take_stage_one_too(self):
        with signed_in(REVIEWER) as client, review(roles=("re",), moved=[STAGE1_IDR]):
            assert client.post(self.url, json={"idr_number": "005"}).status_code == 200

    def test_the_number_is_trimmed(self):
        with signed_in(REVIEWER) as client, review() as seen:
            client.post(self.url, json={"idr_number": "  005 "})
        assert seen["moves"][0][1][4] == "005" and seen["lookups"][0][1] == ("HWS0023", "005", UUID(IDR_ID))

    @pytest.mark.parametrize("body", [None, {}, {"idr_number": None}, {"idr_number": ""}, {"idr_number": "   "}])
    def test_no_number_is_400(self, body):
        with signed_in(REVIEWER) as client, review() as seen:
            response = client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json() == {"detail": "An IDR number is required to accept this IDR"}
        assert seen["moves"] == []

    def test_a_number_in_use_on_the_project_is_409_naming_the_other_idr(self):
        with signed_in(REVIEWER) as client, review(number_holder=OTHER_IDR_ID) as seen:
            response = client.post(self.url, json={"idr_number": "005"})
        assert response.status_code == 409
        assert response.json() == {"detail": "This IDR number is already in use on this project",
                                   "existing_idr_id": OTHER_IDR_ID}
        assert seen["moves"] == []
        sql, params = seen["lookups"][0]
        assert "deleted_at IS NULL AND idr_id <> %s" in sql  # a deleted IDR's number is free again
        assert params == ("HWS0023", "005", UUID(IDR_ID))

    def test_losing_the_race_for_a_number_is_the_same_409(self):
        holders = iter([[], [{"idr_id": UUID(OTHER_IDR_ID)}]])  # free at the check, taken by the time of the statement

        def idrs_query(sql, params=None):
            """Stand in for run_query: the second number lookup finds the IDR that won."""
            if "UPDATE icid.idrs" in sql:
                raise UniqueViolation("uq_idrs_project_number")
            return next(holders) if "idr_number = %s" in sql else [SUBMITTED_IDR]

        with signed_in(REVIEWER) as client, \
                patch("api.queries.idrs.run_query", side_effect=idrs_query), \
                patch("api.queries.projects.run_query", return_value=[{"role": "oe"}]):
            response = client.post(self.url, json={"idr_number": "005"})
        assert response.status_code == 409 and response.json()["existing_idr_id"] == OTHER_IDR_ID

    def test_the_query_turns_a_unique_violation_into_its_own_error(self):
        with patch("api.queries.idrs.run_query", side_effect=UniqueViolation("uq_idrs_project_number")):
            with pytest.raises(IdrNumberTakenError):
                accept_stage1(UUID(IDR_ID), REVIEWER["uuid"], "005")

    def test_a_resubmitted_idr_keeps_its_number_and_needs_none_sent(self):
        resubmitted = {**SUBMITTED_IDR, "idr_number": "005", "stage1_reviewer_uuid": OTHER_REVIEWER["uuid"]}
        for body in (None, {}, {"idr_number": "999"}):
            with signed_in(REVIEWER) as client, review(idr=resubmitted, moved=[STAGE1_IDR]) as seen:
                response = client.post(self.url, json=body)
            assert response.status_code == 200 and response.json()["data"]["idr_number"] == "005"
            assert seen["lookups"] == []  # its own number can't collide
            assert seen["moves"][0][1][4] == "005"

    @pytest.mark.parametrize("idr", [STAGE1_IDR, STAGE2_IDR, APPROVED_IDR, {**SUBMITTED_IDR, "status": "draft"}])
    def test_only_a_submitted_idr_can_be_accepted(self, idr):
        with signed_in(REVIEWER) as client, review(idr=idr) as seen:
            response = client.post(self.url, json={"idr_number": "005"})
        assert response.status_code == 409
        assert response.json() == {"detail": "Only a submitted IDR can be accepted for Stage 1"}
        assert seen["moves"] == []

    def test_an_idr_someone_else_just_accepted_is_409(self):
        with signed_in(REVIEWER) as client, review(moved=[]):
            response = client.post(self.url, json={"idr_number": "005"})
        assert response.status_code == 409 and response.json() == CHANGED

    def test_a_failed_statement_is_500(self):
        with signed_in(REVIEWER) as client, review(moved=None):
            assert client.post(self.url, json={"idr_number": "005"}).status_code == 500

    def test_the_acceptance_is_logged_in_the_same_statement(self):
        with signed_in(REVIEWER) as client, review() as seen:
            client.post(self.url, json={"idr_number": "005"})
        sql = flat(seen["moves"][0][0])
        assert "INSERT INTO icid.idr_audit (idr_id, actor_uuid, action, from_status, to_status, note)" in sql
        assert "SELECT m.idr_id, %s, %s, t.from_status, m.status, %s FROM moved m JOIN target t" in sql
        assert sql.count(";") == 1 and "FOR UPDATE" in sql


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/approve-stage1
# ---------------------------------------------------------------------------

class TestApproveStage1:
    url = url("approve-stage1")

    def test_the_reviewer_who_accepted_passes_it_to_stage_two(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR, moved=[UNACCEPTED_STAGE2_IDR]) as seen:
            response = client.post(self.url)
        assert response.status_code == 200 and response.json()["data"]["status"] == "stage2_review"
        sql, params = seen["moves"][0]
        assert "AND stage1_reviewer_uuid = %s" in sql
        assert "stage1_reviewed_at = now(), return_reason = NULL, returned_from = NULL" in sql
        assert params == (UUID(IDR_ID), "stage1_review", REVIEWER["uuid"], "stage2_review",
                          REVIEWER["uuid"], "approve_stage1", None)

    def test_another_reviewer_on_the_project_is_403(self):
        with signed_in(OTHER_REVIEWER) as client, review(idr=STAGE1_IDR, roles=("oe", "re")) as seen:
            response = client.post(self.url)
        assert response.status_code == 403 and response.json() == NOT_THE_REVIEWER
        assert seen["moves"] == []

    def test_an_admin_stands_in_for_the_reviewer(self, admin_client):
        with review(idr=STAGE1_IDR, roles=(), moved=[UNACCEPTED_STAGE2_IDR]) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200
        sql, params = seen["moves"][0]
        assert "stage1_reviewer_uuid = %s" not in sql  # no reviewer check for an admin
        assert params == (UUID(IDR_ID), "stage1_review", "stage2_review", ADMIN_USER_ROW["uuid"], "approve_stage1", None)
        seen["projects"].assert_not_called()

    @pytest.mark.parametrize("idr", [SUBMITTED_IDR, STAGE2_IDR, APPROVED_IDR])
    def test_only_an_idr_in_stage_one_review_can_be_approved(self, idr):
        with signed_in(REVIEWER) as client, review(idr=idr) as seen:
            response = client.post(self.url)
        assert response.status_code == 409
        assert response.json() == {"detail": "Only an IDR in Stage 1 review can be approved for Stage 2"}
        assert seen["moves"] == []

    def test_an_idr_that_moved_meanwhile_is_409(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR, moved=[]):
            response = client.post(self.url)
        assert response.status_code == 409 and response.json() == CHANGED


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/accept-stage2
# ---------------------------------------------------------------------------

class TestAcceptStage2:
    url = url("accept-stage2")

    def test_an_re_becomes_the_re_reviewer_and_the_status_stays(self):
        with signed_in(REVIEWER) as client, review(idr=UNACCEPTED_STAGE2_IDR, roles=("re",), moved=[STAGE2_IDR]) as seen:
            response = client.post(self.url)
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["status"], data["re_reviewer_uuid"]) == ("stage2_review", str(REVIEWER["uuid"]))
        sql, params = seen["moves"][0]
        assert "re_reviewer_uuid = %s" in sql
        assert params == (UUID(IDR_ID), "stage2_review", "stage2_review", REVIEWER["uuid"],
                          REVIEWER["uuid"], "accept_stage2", None)

    def test_accepting_again_is_allowed_and_the_last_to_accept_wins(self):
        taken = {**STAGE2_IDR, "re_reviewer_uuid": OTHER_REVIEWER["uuid"]}
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[taken]) as seen:
            first = client.post(self.url)
        with signed_in(OTHER_REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[taken]) as later:
            second = client.post(self.url)
        assert (first.status_code, second.status_code) == (200, 200)
        assert "AND re_reviewer_uuid = %s" not in seen["moves"][0][0]  # whoever holds it now doesn't matter
        assert later["moves"][0][1][3] == OTHER_REVIEWER["uuid"]

    @pytest.mark.parametrize("idr", [SUBMITTED_IDR, STAGE1_IDR, APPROVED_IDR])
    def test_only_an_idr_in_stage_two_review_can_be_accepted(self, idr):
        with signed_in(REVIEWER) as client, review(idr=idr, roles=("re",)) as seen:
            response = client.post(self.url)
        assert response.status_code == 409
        assert response.json() == {"detail": "Only an IDR in Stage 2 review can be accepted for Stage 2"}
        assert seen["moves"] == []


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/approve-stage2
# ---------------------------------------------------------------------------

class TestApproveStage2:
    url = url("approve-stage2")

    def test_the_re_who_accepted_approves_and_signs(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[APPROVED_IDR]) as seen:
            response = client.post(self.url)
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["status"], data["re_signature_path"]) == ("approved", RE_SIGNATURE_COPY)
        assert data["re_signed_at"] == "2026-10-06T14:00:00Z"
        seen["signature"].assert_called_once_with(REVIEWER["signature_path"], UUID(IDR_ID), "re")
        sql, params = seen["moves"][0]
        assert "re_signature_path = %s, re_signed_at = now()" in sql and "AND re_reviewer_uuid = %s" in sql
        assert params == (UUID(IDR_ID), "stage2_review", REVIEWER["uuid"], "approved", RE_SIGNATURE_COPY,
                          REVIEWER["uuid"], "approve_stage2", None)

    def test_the_inspectors_signature_is_left_alone(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[APPROVED_IDR]) as seen:
            data = client.post(self.url).json()["data"]
        assert "inspector_signature_path" not in seen["moves"][0][0].split("RETURNING")[0]
        assert data["inspector_signature_path"] == SUBMITTED_IDR["inspector_signature_path"]

    def test_another_re_is_403_and_nothing_is_copied(self):
        with signed_in(OTHER_REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",)) as seen:
            response = client.post(self.url)
        assert response.status_code == 403 and response.json() == NOT_THE_REVIEWER
        seen["signature"].assert_not_called()
        assert seen["moves"] == []

    def test_an_idr_nobody_accepted_at_stage_two_cant_be_approved_by_an_re(self):
        with signed_in(REVIEWER) as client, review(idr=UNACCEPTED_STAGE2_IDR, roles=("re",)) as seen:
            response = client.post(self.url)
        assert response.status_code == 403 and seen["moves"] == []

    def test_an_admin_stands_in_and_signs_with_their_own_signature(self, admin_client):
        with review(idr=STAGE2_IDR, roles=(), moved=[APPROVED_IDR]) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200
        seen["signature"].assert_called_once_with(ADMIN_USER_ROW["signature_path"], UUID(IDR_ID), "re")
        assert "AND re_reviewer_uuid = %s" not in seen["moves"][0][0]

    def test_an_re_without_a_signature_is_400(self):
        unsigned = {**REVIEWER, "signature_path": None, "signature_type": None, "signature_set_at": None}
        with signed_in(unsigned) as client, review(idr=STAGE2_IDR, roles=("re",)) as seen:
            response = client.post(self.url)
        assert response.status_code == 400 and response.json() == {"detail": "Signature required before approving"}
        seen["signature"].assert_not_called()
        assert seen["moves"] == []

    def test_a_failed_copy_is_502_and_the_idr_stays_in_review(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",)) as seen:
            seen["signature"].side_effect = SignatureStorageError("Could not copy the signature for this IDR")
            response = client.post(self.url)
        assert response.status_code == 502
        assert response.json() == {"detail": "Could not copy the signature for this IDR"}
        assert seen["moves"] == []

    def test_a_copy_left_behind_by_a_lost_race_is_logged(self, caplog):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[]):
            response = client.post(self.url)
        assert response.status_code == 409 and response.json() == CHANGED
        assert RE_SIGNATURE_COPY in caplog.text and "left orphaned" in caplog.text

    @pytest.mark.parametrize("idr", [SUBMITTED_IDR, STAGE1_IDR, APPROVED_IDR])
    def test_only_an_idr_in_stage_two_review_can_be_approved(self, idr):
        with signed_in(REVIEWER) as client, review(idr=idr, roles=("re",)) as seen:
            response = client.post(self.url)
        assert response.status_code == 409
        assert response.json() == {"detail": "Only an IDR in Stage 2 review can be approved"}
        seen["signature"].assert_not_called()


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/return
# ---------------------------------------------------------------------------

class TestReturn:
    url = url("return")

    def test_stage_one_returns_to_the_inspector_as_a_draft(self):
        returned = {**STAGE1_IDR, "status": "draft", "return_reason": "fix the pay-item quantity",
                    "returned_from": "stage1"}
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR, moved=[returned]) as seen:
            response = client.post(self.url, json={"to": "inspector", "comment": " fix the pay-item quantity "})
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["status"], data["return_reason"], data["returned_from"]) == (
            "draft", "fix the pay-item quantity", "stage1")
        assert data["idr_number"] == "005"  # kept for the resubmit
        sql, params = seen["moves"][0]
        assert "return_reason = %s, returned_from = %s" in sql and "AND stage1_reviewer_uuid = %s" in sql
        assert "idr_number" not in sql.split("RETURNING")[0]
        assert params == (UUID(IDR_ID), "stage1_review", REVIEWER["uuid"], "draft", "fix the pay-item quantity",
                          "stage1", REVIEWER["uuid"], "return_to_inspector", "fix the pay-item quantity")

    @pytest.mark.parametrize("to,status,action", [("inspector", "draft", "return_to_inspector"),
                                                  ("oe", "stage1_review", "return_to_oe")])
    def test_the_re_returns_from_stage_two_to_the_inspector_or_the_oe(self, to, status, action):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",)) as seen:
            response = client.post(self.url, json={"to": to, "comment": "check the station"})
        assert response.status_code == 200
        sql, params = seen["moves"][0]
        assert "AND re_reviewer_uuid = %s" in sql
        assert params == (UUID(IDR_ID), "stage2_review", REVIEWER["uuid"], status, "check the station", "stage2",
                          REVIEWER["uuid"], action, "check the station")

    def test_stage_one_cant_return_to_the_oe(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR) as seen:
            response = client.post(self.url, json={"to": "oe", "comment": "x"})
        assert response.status_code == 409
        assert response.json() == {"detail": "Only an IDR in Stage 2 review can be returned to the OE"}
        assert seen["moves"] == []

    @pytest.mark.parametrize("comment", ["", "   ", "\n\t"])
    def test_a_blank_comment_is_400(self, comment):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR) as seen:
            response = client.post(self.url, json={"to": "inspector", "comment": comment})
        assert response.status_code == 400
        assert response.json() == {"detail": "A comment is required to return an IDR"}
        assert seen["moves"] == []

    @pytest.mark.parametrize("body", [None, {}, {"to": "inspector"}, {"comment": "x"}, {"to": "re", "comment": "x"}])
    def test_a_malformed_body_is_422(self, body):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR) as seen:
            assert client.post(self.url, json=body).status_code == 422
        assert seen["moves"] == []

    @pytest.mark.parametrize("idr", [STAGE1_IDR, STAGE2_IDR])
    def test_only_the_stages_reviewer_can_return(self, idr):
        with signed_in(OTHER_REVIEWER) as client, review(idr=idr, roles=("oe", "re")) as seen:
            response = client.post(self.url, json={"to": "inspector", "comment": "x"})
        assert response.status_code == 403 and response.json() == NOT_THE_REVIEWER
        assert seen["moves"] == []

    def test_an_admin_can_return_from_either_stage(self, admin_client):
        for idr, column in ((STAGE1_IDR, "stage1_reviewer_uuid"), (STAGE2_IDR, "re_reviewer_uuid")):
            with review(idr=idr, roles=()) as seen:
                response = admin_client.post(self.url, json={"to": "inspector", "comment": "x"})
            assert response.status_code == 200 and f"AND {column} = %s" not in seen["moves"][0][0]

    @pytest.mark.parametrize("idr", [SUBMITTED_IDR, APPROVED_IDR, {**SUBMITTED_IDR, "status": "draft"}])
    def test_only_an_idr_under_review_can_be_returned(self, idr):
        with signed_in(REVIEWER) as client, review(idr=idr) as seen:
            response = client.post(self.url, json={"to": "inspector", "comment": "x"})
        assert response.status_code == 409 and response.json() == {"detail": "Only an IDR under review can be returned"}
        assert seen["moves"] == []


# ---------------------------------------------------------------------------
# GET /v1/idrs/queue
# ---------------------------------------------------------------------------

QUEUE_ROW = {**STAGE1_IDR, "report_count": 2, "has_general": True, "reporter_name": "Genghis Khan",
             "stage1_reviewer_name": "Olive Engineer", "re_reviewer_name": None}


class TestReviewQueue:
    url = "/v1/idrs/queue"

    def queue(self, client, status: str, rows=None):
        """
        Ask for a queue with the query layer patched.
        Takes the client, the queue's status and the rows the query returns.
        Returns (response, sql, params) of the one query run.
        """
        with patch("api.queries.idrs.run_query", return_value=[QUEUE_ROW] if rows is None else rows) as run:
            response = client.get(self.url, params={"status": status})
        assert run.call_count == 1
        return response, flat(run.call_args.args[0]), run.call_args.args[1]

    @pytest.mark.parametrize("status,roles", [("submitted", ["oe", "re"]), ("stage1_review", ["oe", "re"]),
                                              ("stage2_review", ["re"])])
    def test_a_queue_is_limited_to_projects_where_the_user_holds_a_role_that_works_it(self, status, roles):
        with signed_in(REVIEWER) as client:
            response, sql, params = self.queue(client, status)
        assert response.status_code == 200
        assert "i.status = %s AND i.deleted_at IS NULL AND EXISTS ( SELECT 1 FROM icid.project_users pu" in sql
        assert "pu.project_id = i.project_id AND pu.user_uuid = %s AND pu.role = ANY(%s)" in sql
        assert params == (status, REVIEWER["uuid"], roles)

    def test_items_carry_the_number_the_names_and_the_counts(self):
        with signed_in(REVIEWER) as client:
            response, _, _ = self.queue(client, "stage1_review")
        item = response.json()["data"][0]
        assert (item["idr_number"], item["status"], item["report_count"]) == ("005", "stage1_review", 2)
        assert (item["reporter_name"], item["stage1_reviewer_name"]) == ("Genghis Khan", "Olive Engineer")

    def test_oldest_submission_first(self):
        with signed_in(REVIEWER) as client:
            _, sql, _ = self.queue(client, "submitted")
        assert sql.endswith("ORDER BY i.submitted_at ASC NULLS LAST, i.created_at, i.idr_id;")

    def test_an_admin_sees_every_project(self, admin_client):
        response, sql, params = self.queue(admin_client, "stage2_review")
        assert response.status_code == 200 and "project_users" not in sql
        assert params == ("stage2_review",)

    def test_a_user_with_no_reviewing_role_gets_an_empty_queue(self):
        with signed_in(INSPECTOR) as client:
            response, _, _ = self.queue(client, "stage1_review", rows=[])
        assert response.status_code == 200 and response.json()["data"] == []

    def test_a_demo_user_gets_an_empty_queue_from_the_same_role_filter(self, demo_client):
        response, _, params = self.queue(demo_client, "submitted", rows=[])
        assert response.json()["data"] == [] and params[1] == DEMO_USER_ROW["uuid"]

    @pytest.mark.parametrize("params", [{}, {"status": "draft"}, {"status": "approved"}, {"status": "bogus"}])
    def test_the_status_must_be_a_review_queue(self, admin_client, params):
        with patch("api.queries.idrs.run_query") as run:
            assert admin_client.get(self.url, params=params).status_code == 422
        run.assert_not_called()

    def test_queue_is_not_read_as_an_idr_id(self, admin_client):
        with patch("api.queries.idrs.run_query", return_value=[]):
            assert admin_client.get(self.url, params={"status": "submitted"}).status_code == 200

    def test_a_query_failure_is_500(self, admin_client):
        with patch("api.queries.idrs.run_query", return_value=None):
            assert admin_client.get(self.url, params={"status": "submitted"}).status_code == 500


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/submit: the inspector role
# ---------------------------------------------------------------------------

class TestSubmitNeedsTheInspectorRole:
    url = url("submit")
    draft = {**SUBMITTED_IDR, "status": "draft", "submitted_at": None}

    @pytest.mark.parametrize("roles", [(), ("oe",), ("oe", "re")])
    def test_a_user_who_isnt_an_inspector_on_the_project_is_403(self, roles):
        with signed_in(REVIEWER) as client, review(idr=self.draft, roles=roles) as seen, \
                patch("api.queries.idr_reports.run_query") as reports:
            response = client.post(self.url)
        assert response.status_code == 403 and response.json() == {"detail": "Role required: inspector"}
        assert seen["moves"] == []
        reports.assert_not_called()

    def test_an_inspector_submits(self):
        report = {"report_id": UUID(OTHER_IDR_ID), "idr_id": UUID(IDR_ID), "report_type": "GEN", "is_addendum": False,
                  "parent_report_id": None, "page_number": 1, "report_data": {}, "is_auto_generated": False,
                  "created_at": NOW, "updated_at": NOW}
        with signed_in(INSPECTOR) as client, review(idr=self.draft, roles=("inspector",), moved=[SUBMITTED_IDR]) as seen, \
                patch("api.queries.idr_reports.run_query", return_value=[report]), \
                patch("api.v1.idrs.snapshot_signature_for_idr", return_value=SUBMITTED_IDR["inspector_signature_path"]):
            response = client.post(self.url)
        assert response.status_code == 200 and response.json()["data"]["status"] == "submitted"
        assert seen["moves"][0][1][2:] == (INSPECTOR["uuid"], "submit", None)

    def test_a_demo_user_is_refused_as_a_demo_user_before_any_role_lookup(self, demo_client):
        own = {**self.draft, "reporter_uuid": DEMO_USER_ROW["uuid"], "project_id": "DEMO01"}
        with review(idr=own, roles=("inspector",)) as seen:
            response = demo_client.post(self.url)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: submit is disabled"}
        seen["projects"].assert_not_called()
