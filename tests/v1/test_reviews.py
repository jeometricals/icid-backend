from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
from psycopg.errors import UniqueViolation

from api.queries.idrs import IdrNumberTakenError, accept_stage1, list_approved_idrs
from api.services.signatures import SignatureStorageError
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

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
    "returned_from": None, "deleted_at": None, "deleted_by": None, "stage1_accepted_at": None,
    "stage2_accepted_at": None,
}
STAGE1_IDR ={**SUBMITTED_IDR, "status": "stage1_review", "idr_number": "005", "stage1_reviewer_uuid": REVIEWER["uuid"]}
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
def review(idr=SUBMITTED_IDR, roles=("oe",), moved="same", number_holder=None, reports=(), edits=(), accepted_at=None):
    """
    Patch the query layer under the review routes: the IDR read, the caller's roles on its project, the number lookup and the statement that moves the IDR; and, for the pay-item gate on the two approve routes, the IDR's reports, its edits and when its stage was last accepted.
    Takes the IDR row the reads return (None: no such IDR), the roles the caller holds on its project, what the moving statement returns ("same": the IDR row; or [] / None / an exception to raise), the uuid of an IDR already holding the number asked for, the IDR's report rows (none: no pay items to attest to), its field edits and the time its current stage was accepted (set on the row as stage1_accepted_at or stage2_accepted_at).
    Yields a dict: "moves" is the list of (sql, params) of every moving statement run, "lookups" the number lookups, "signature" the mock of the signature copy (it returns RE_SIGNATURE_COPY).
    """
    seen = {"moves": [], "lookups": []}
    if idr and accepted_at is not None:
        column = "stage2_accepted_at" if idr["status"] == "stage2_review" else "stage1_accepted_at"
        idr = {**idr, column: accepted_at}

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
         patch("api.queries.idr_reports.run_query", return_value=list(reports)), \
         patch("api.queries.idr_field_edits.run_query", return_value=list(edits)), \
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

    def test_it_starts_stage_ones_round_and_clears_stage_twos(self):
        accepted = {**STAGE1_IDR, "stage1_accepted_at": NOW}
        with signed_in(REVIEWER) as client, review(moved=[accepted]) as seen:
            data = client.post(self.url, json={"idr_number": "005"}).json()["data"]
        assert (data["stage1_accepted_at"], data["stage2_accepted_at"]) == ("2026-10-06T14:00:00Z", None)
        assert "stage1_accepted_at = now(), stage2_accepted_at = NULL" in seen["moves"][0][0]

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

    def test_it_starts_stage_twos_round_and_leaves_stage_ones(self):
        accepted = {**STAGE2_IDR, "stage1_accepted_at": NOW, "stage2_accepted_at": NOW}
        with signed_in(REVIEWER) as client, review(idr=UNACCEPTED_STAGE2_IDR, roles=("re",), moved=[accepted]) as seen:
            data = client.post(self.url).json()["data"]
        assert data["stage2_accepted_at"] == "2026-10-06T14:00:00Z"
        sql = flat(seen["moves"][0][0])
        assert "SET status = %s, updated_at = now(), re_reviewer_uuid = %s, stage2_accepted_at = now() FROM target" in sql
        assert "stage1_accepted_at" not in sql.split("RETURNING")[0]

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
        assert params[:-1] == (UUID(IDR_ID), "stage2_review", REVIEWER["uuid"], "approved", REVIEWER["uuid"],
                               RE_SIGNATURE_COPY, REVIEWER["uuid"], "approve_stage2", None)
        assert params[-1].obj == []  # the IDR's quantity rows: it has no pay items

    def test_the_approver_is_recorded_as_the_re_reviewer(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[APPROVED_IDR]) as seen:
            client.post(self.url)
        sql, params = seen["moves"][0]
        assert "SET status = %s, updated_at = now(), re_reviewer_uuid = %s, re_signature_path = %s" in " ".join(sql.split())
        assert params[4] == REVIEWER["uuid"]  # so the name on the IDR is the signer's

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
        sql, params = seen["moves"][0]
        assert "AND re_reviewer_uuid = %s" not in sql
        # the admin replaces whoever accepted: their name goes with their signature
        assert params[:-1] == (UUID(IDR_ID), "stage2_review", "approved", ADMIN_USER_ROW["uuid"], RE_SIGNATURE_COPY,
                               ADMIN_USER_ROW["uuid"], "approve_stage2", None)

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

    @pytest.mark.parametrize("idr,to,cleared", [
        (STAGE1_IDR, "inspector", "stage1_accepted_at = NULL, stage2_accepted_at = NULL"),
        (STAGE2_IDR, "inspector", "stage1_accepted_at = NULL, stage2_accepted_at = NULL, re_reviewer_uuid = NULL"),
        (STAGE2_IDR, "oe", "stage2_accepted_at = NULL, re_reviewer_uuid = NULL"),   # the OE's acceptance stands
    ])
    def test_a_return_ends_the_rounds_of_whoever_has_to_accept_again(self, idr, to, cleared):
        with signed_in(REVIEWER) as client, review(idr=idr, roles=("re",)) as seen:
            assert client.post(self.url, json={"to": to, "comment": "check the station"}).status_code == 200
        sql = flat(seen["moves"][0][0])
        assert f"return_reason = %s, returned_from = %s, {cleared} FROM target" in sql

    def test_a_stage_one_return_leaves_the_re_reviewer_column_alone(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR) as seen:
            client.post(self.url, json={"to": "inspector", "comment": "fix it"})
        assert "re_reviewer_uuid" not in seen["moves"][0][0].split("RETURNING")[0]

    def test_after_the_re_returns_it_the_same_re_must_accept_again_when_it_comes_back(self):
        # the RE returns it to the inspector: the statement checks they are the RE reviewer, then clears them
        returned = {**STAGE2_IDR, "status": "draft", "return_reason": "check the station", "returned_from": "stage2",
                    "re_reviewer_uuid": None}
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",), moved=[returned]) as seen:
            response = client.post(self.url, json={"to": "inspector", "comment": "check the station"})
        assert response.status_code == 200 and response.json()["data"]["re_reviewer_uuid"] is None
        sql = flat(seen["moves"][0][0])
        assert "AND re_reviewer_uuid = %s FOR UPDATE" in sql and "re_reviewer_uuid = NULL FROM target" in sql
        # resubmitted, accepted and approved by the OE: back in Stage 2 with the times and the RE reviewer as each
        # statement left them
        back = {**STAGE2_IDR, "stage1_accepted_at": NOW, "stage2_accepted_at": None, "re_reviewer_uuid": None}
        with signed_in(REVIEWER) as client, review(idr=back, roles=("re",)) as seen:
            refused = client.post(url("approve-stage2"))
            assert seen["moves"] == []
            accepted = client.post(url("accept-stage2"))
        assert refused.status_code == 403 and refused.json() == NOT_THE_REVIEWER
        assert accepted.status_code == 200 and "stage2_accepted_at = now()" in seen["moves"][0][0]

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


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/admin/unlock
# ---------------------------------------------------------------------------

UNLOCKED_IDR = {**APPROVED_IDR, "status": "stage2_review", "re_signature_path": None, "re_signed_at": None,
                "re_reviewer_uuid": None}
DELETED_IDR = {**APPROVED_IDR, "status": "deleted", "deleted_at": NOW, "deleted_by": ADMIN_USER_ROW["uuid"]}
NOTHING_TO_UNLOCK = {"detail": "Only an approved IDR, or one in Stage 2 review, can be unlocked"}


class TestAdminUnlock:
    url = url("admin/unlock")

    def test_an_admin_sends_an_approved_idr_back_to_stage_two_unsigned_and_unaccepted(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[UNLOCKED_IDR]) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "IDR unlocked for RE review"
        data = body["data"]
        assert (data["status"], data["re_signature_path"], data["re_signed_at"], data["re_reviewer_uuid"]) == (
            "stage2_review", None, None, None)
        assert data["idr_number"] == "005"  # the number stays
        assert data["inspector_signature_path"] == APPROVED_IDR["inspector_signature_path"]  # and so does the inspector's
        sql, params = seen["moves"][0]
        assert "re_signature_path = NULL, re_signed_at = NULL, re_reviewer_uuid = NULL, stage2_accepted_at = NULL" in sql
        assert "stage1_accepted_at" not in sql.split("RETURNING")[0]  # the OE's acceptance stands
        assert "idr_number" not in sql.split("RETURNING")[0] and "inspector_signature_path" not in sql.split("RETURNING")[0]
        assert "WHERE idr_id = %s AND status = ANY(%s) AND deleted_at IS NULL" in flat(sql)
        assert params == (UUID(IDR_ID), ["approved", "stage2_review"], "stage2_review", ADMIN_USER_ROW["uuid"],
                          "admin_unlock", None)
        seen["signature"].assert_not_called()  # the admin does not approve, so nothing is signed

    def test_it_is_logged_in_the_same_statement_as_admin_unlock(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[UNLOCKED_IDR]) as seen:
            admin_client.post(self.url)
        sql, params = seen["moves"][0]
        assert "INSERT INTO icid.idr_audit (idr_id, actor_uuid, action, from_status, to_status, note)" in flat(sql)
        assert sql.count(";") == 1 and params[-3:] == (ADMIN_USER_ROW["uuid"], "admin_unlock", None)

    def test_an_idr_already_in_stage_two_review_can_be_unlocked_to_clear_its_reviewer(self, admin_client):
        with review(idr=STAGE2_IDR, roles=(), moved=[UNLOCKED_IDR]) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200 and response.json()["data"]["re_reviewer_uuid"] is None
        assert len(seen["moves"]) == 1

    @pytest.mark.parametrize("idr", [{**SUBMITTED_IDR, "status": "draft"}, SUBMITTED_IDR, STAGE1_IDR, DELETED_IDR])
    def test_there_is_nothing_to_unlock_before_stage_two_or_once_deleted(self, admin_client, idr):
        with review(idr=idr, roles=()) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 400 and response.json() == NOTHING_TO_UNLOCK
        assert seen["moves"] == []

    def test_an_idr_that_doesnt_exist_is_404(self, admin_client):
        with review(idr=None) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}
        assert seen["moves"] == []

    @pytest.mark.parametrize("roles", [("inspector",), ("oe",), ("re",), ("inspector", "oe", "re")])
    def test_no_project_role_is_enough(self, roles):
        with signed_in(REVIEWER) as client, review(idr=APPROVED_IDR, roles=roles) as seen:
            response = client.post(self.url)
        assert response.status_code == 403 and response.json() == {"detail": "Admin access required"}
        assert seen["moves"] == []

    def test_a_demo_user_is_refused(self, demo_client):
        own = {**APPROVED_IDR, "reporter_uuid": DEMO_USER_ROW["uuid"]}
        with review(idr=own) as seen:
            assert demo_client.post(self.url).status_code == 403
        assert seen["moves"] == []

    def test_an_idr_that_moved_meanwhile_is_409(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[]):
            response = admin_client.post(self.url)
        assert response.status_code == 409 and response.json() == CHANGED

    def test_a_failed_statement_is_500(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=None):
            assert admin_client.post(self.url).status_code == 500

    def test_after_unlocking_an_re_must_accept_again_before_approving(self):
        # the unlocked IDR has no RE reviewer, so approve-stage2 refuses even the RE who approved it before
        with signed_in(REVIEWER) as client, review(idr=UNLOCKED_IDR, roles=("re",)) as seen:
            refused = client.post(url("approve-stage2"))
            accepted = client.post(url("accept-stage2"))
        assert refused.status_code == 403 and refused.json() == NOT_THE_REVIEWER
        assert accepted.status_code == 200 and len(seen["moves"]) == 1


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/admin/delete
# ---------------------------------------------------------------------------

class TestAdminDelete:
    url = url("admin/delete")

    def test_an_admin_soft_deletes_an_idr(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[DELETED_IDR]) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "IDR deleted"
        assert (body["data"]["status"], body["data"]["deleted_at"], body["data"]["deleted_by"]) == (
            "deleted", "2026-10-06T14:00:00Z", str(ADMIN_USER_ROW["uuid"]))
        sql, params = seen["moves"][0]
        assert "UPDATE icid.idrs i" in sql and "DELETE FROM icid.idrs" not in sql  # the row is kept
        assert "SET status = %s, updated_at = now(), deleted_at = now(), deleted_by = %s" in flat(sql)
        assert "WHERE idr_id = %s AND deleted_at IS NULL FOR UPDATE" in flat(sql)  # whatever its status
        assert params == (UUID(IDR_ID), "deleted", ADMIN_USER_ROW["uuid"], ADMIN_USER_ROW["uuid"], "admin_delete", None)

    def test_it_is_logged_in_the_same_statement_as_admin_delete(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[DELETED_IDR]) as seen:
            admin_client.post(self.url)
        sql, params = seen["moves"][0]
        assert "INSERT INTO icid.idr_audit" in sql and "t.from_status, m.status" in sql and sql.count(";") == 1
        assert params[-2] == "admin_delete"

    @pytest.mark.parametrize("idr", [{**SUBMITTED_IDR, "status": "draft"}, SUBMITTED_IDR, STAGE1_IDR, STAGE2_IDR,
                                     APPROVED_IDR])
    def test_an_idr_at_any_status_can_be_deleted(self, admin_client, idr):
        with review(idr=idr, roles=(), moved=[{**idr, "status": "deleted", "deleted_at": NOW,
                                               "deleted_by": ADMIN_USER_ROW["uuid"]}]) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200 and response.json()["data"]["status"] == "deleted"
        assert len(seen["moves"]) == 1

    def test_deleting_again_succeeds_and_changes_nothing(self, admin_client):
        with review(idr=DELETED_IDR, roles=()) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "IDR was already deleted" and body["data"]["status"] == "deleted"
        assert seen["moves"] == []  # no second update, no second audit row

    def test_losing_a_race_to_delete_is_the_same_success(self, admin_client):
        reads = iter([APPROVED_IDR, DELETED_IDR])  # live at the first read, deleted by the time of the statement

        def idrs_query(sql, params=None):
            """Stand in for run_query: the statement finds nothing left to delete."""
            return [] if "UPDATE icid.idrs" in sql else [next(reads)]

        with patch("api.queries.idrs.run_query", side_effect=idrs_query):
            response = admin_client.post(self.url)
        assert response.status_code == 200 and response.json()["message"] == "IDR was already deleted"
        assert response.json()["data"]["deleted_at"] == "2026-10-06T14:00:00Z"

    def test_an_idr_that_doesnt_exist_is_404(self, admin_client):
        with review(idr=None) as seen:
            response = admin_client.post(self.url)
        assert response.status_code == 404 and seen["moves"] == []

    @pytest.mark.parametrize("roles", [("inspector",), ("oe", "re")])
    def test_no_project_role_is_enough(self, roles):
        with signed_in(REVIEWER) as client, review(idr=APPROVED_IDR, roles=roles) as seen:
            response = client.post(self.url)
        assert response.status_code == 403 and response.json() == {"detail": "Admin access required"}
        assert seen["moves"] == []

    def test_the_inspector_cant_delete_their_own_idr(self):
        own_draft = {**SUBMITTED_IDR, "status": "draft"}
        with signed_in(INSPECTOR) as client, review(idr=own_draft, roles=("inspector",)) as seen:
            assert client.post(self.url).status_code == 403
        assert seen["moves"] == []

    def test_a_failed_statement_is_500(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=None):
            response = admin_client.post(self.url)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to delete IDR"}


class TestADeletedIdrIsOutOfReview:
    @pytest.mark.parametrize("action,body", [(a, b) for a, b, _ in REVIEW_ROUTES])
    def test_every_move_leaves_a_deleted_idr_alone(self, admin_client, action, body):
        # each statement only ever matches a row with deleted_at IS NULL, whatever the endpoint let through
        for status in ("submitted", "stage1_review", "stage2_review"):
            with review(idr={**STAGE2_IDR, "status": status, "idr_number": "005"}, roles=()) as seen:
                admin_client.post(url(action), json=body)
            for sql, _ in seen["moves"]:
                assert "AND deleted_at IS NULL" in flat(sql)


# ---------------------------------------------------------------------------
# The pay-item gate: a stage is approved only once its reviewer has attested to every pay item
# ---------------------------------------------------------------------------

SWCB_REPORT = UUID("e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091")
GEN_REPORT = UUID("4e5f6071-8293-4a41-b5c6-d7e8f9a0b1c2")
ACCEPTED_AT = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)  # when the stage was last accepted
BEFORE, AFTER = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc), datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc)


def pay_report(report_id: UUID, *items: dict, **columns) -> dict:
    """
    Build an idr_reports row holding pay items.
    Takes the report uuid, its pay items and any column overrides.
    Returns the row.
    """
    return {"report_id": report_id, "idr_id": UUID(IDR_ID), "report_type": "SWCB", "is_addendum": False,
            "parent_report_id": None, "page_number": 1, "report_data": {"payItems": list(items)},
            "is_auto_generated": False, "created_at": NOW, "updated_at": NOW, **columns}


def item(item_id: str, quantity: str = "60.00", **fields) -> dict:
    """
    Build a pay item.
    Takes its id, its quantity and any other fields.
    Returns the item dict.
    """
    return {"id": item_id, "itemNo": f"4.{item_id[-1]}0 A", "budgetCode": "12345", "payQuantity": quantity,
            "unit": "S.F.", "description": "Work", **fields}


def attest(kind: str, item_id: str, value, by: dict = REVIEWER, stage: str = "stage1", at: datetime = AFTER,
           report_id: UUID = SWCB_REPORT) -> dict:
    """
    Build an idr_field_edits row attesting to a pay item: an approval, a revision or an add.
    Takes the kind ('approve' | 'revise' | 'add'), the item's id, the quantity (for an add, the whole item), who made it, at which stage, when, and on which report.
    Returns the edit row as list_field_edits returns it.
    """
    path = f"payItems[{item_id}].payQuantity" if kind == "revise" else f"payItems[{item_id}]"
    edit_type = {"approve": "pay_item_approve", "revise": "pay_item_revision", "add": "pay_item_add"}[kind]
    return {"edit_id": UUID(int=hash((kind, item_id, str(value), stage)) % 2**64), "idr_id": UUID(IDR_ID),
            "report_id": report_id, "field_path": path, "edit_type": edit_type,
            "old_value": None if kind == "add" else value, "new_value": value, "editor_uuid": by["uuid"],
            "editor_stage": stage, "edited_at": at, "editor_first_name": by["first_name"],
            "editor_last_name": by["last_name"]}


TWO_ITEMS = (pay_report(SWCB_REPORT, item("item-1", "60.00"), item("item-2", "29.00")),)
STAGE1_APPROVE, STAGE2_APPROVE = url("approve-stage1"), url("approve-stage2")


class TestPayItemGate:
    def approve(self, idr=STAGE1_IDR, user=REVIEWER, **backend) -> tuple:
        """
        Call the approve route for the IDR's stage.
        Takes the IDR row, the caller and review()'s keyword arguments (reports, edits, accepted_at...).
        Returns (the response, the moving statements run).
        """
        route = STAGE1_APPROVE if idr["status"] == "stage1_review" else STAGE2_APPROVE
        backend.setdefault("roles", ("oe", "re"))
        backend.setdefault("accepted_at", ACCEPTED_AT)
        with signed_in(user) as client, review(idr=idr, **backend) as seen:
            return client.post(route), seen["moves"]

    def test_untouched_pay_items_refuse_the_approval_and_are_named(self):
        response, moves = self.approve(reports=TWO_ITEMS)
        assert response.status_code == 400 and moves == []
        assert response.json() == {
            "detail": "2 pay items still need your approval or revision before you can approve this IDR",
            "untouched": [
                {"pay_item_id": "item-1", "report_id": str(SWCB_REPORT), "item_no": "4.10 A", "budget_code": "12345"},
                {"pay_item_id": "item-2", "report_id": str(SWCB_REPORT), "item_no": "4.20 A", "budget_code": "12345"},
            ]}

    def test_one_left_is_worded_in_the_singular(self):
        response, _ = self.approve(reports=TWO_ITEMS, edits=[attest("approve", "item-1", "60.00")])
        assert response.status_code == 400
        assert response.json()["detail"] == "1 pay item still needs your approval or revision before you can approve this IDR"
        assert [u["pay_item_id"] for u in response.json()["untouched"]] == ["item-2"]

    @pytest.mark.parametrize("second", [
        attest("approve", "item-2", "29.00"),
        attest("revise", "item-2", "29.00"),   # their revision is the quantity the item now has
        attest("add", "item-2", item("item-2", "29.00")),   # they added it themselves
    ])
    def test_an_approval_a_revision_or_an_add_each_counts(self, second):
        response, moves = self.approve(reports=TWO_ITEMS, edits=[attest("approve", "item-1", "60.00"), second])
        assert response.status_code == 200 and len(moves) == 1

    def test_no_pay_items_means_nothing_to_attest_to(self):
        assert self.approve(reports=())[0].status_code == 200
        assert self.approve(reports=(pay_report(SWCB_REPORT),))[0].status_code == 200

    def test_an_auto_generated_generals_items_are_not_asked_for(self):
        auto = pay_report(GEN_REPORT, item("merged-1"), report_type="GEN", is_auto_generated=True)
        response, _ = self.approve(reports=(auto, *TWO_ITEMS),
                                   edits=[attest("approve", "item-1", "60.00"), attest("approve", "item-2", "29.00")])
        assert response.status_code == 200

    def test_an_inspectors_own_generals_items_are(self):
        own = pay_report(GEN_REPORT, item("gen-1"), report_type="GEN")
        response, _ = self.approve(reports=(own,))
        assert response.status_code == 400 and response.json()["untouched"][0]["report_id"] == str(GEN_REPORT)

    def test_someone_elses_attestation_doesnt_count(self):
        theirs = [attest("approve", "item-1", "60.00", by=OTHER_REVIEWER), attest("approve", "item-2", "29.00", by=OTHER_REVIEWER)]
        response, _ = self.approve(reports=TWO_ITEMS, edits=theirs)
        assert response.status_code == 400 and len(response.json()["untouched"]) == 2

    def test_stage_two_needs_the_res_own_attestations_whatever_was_done_at_stage_one(self):
        at_stage_one = [attest("approve", "item-1", "60.00"), attest("approve", "item-2", "29.00")]  # the same person, as OE
        refused, _ = self.approve(idr=STAGE2_IDR, reports=TWO_ITEMS, edits=at_stage_one)
        assert refused.status_code == 400 and len(refused.json()["untouched"]) == 2
        at_stage_two = [attest("approve", "item-1", "60.00", stage="stage2"), attest("revise", "item-2", "29.00", stage="stage2")]
        approved, moves = self.approve(idr=STAGE2_IDR, reports=TWO_ITEMS, edits=at_stage_one + at_stage_two)
        assert approved.status_code == 200 and len(moves) == 1

    def test_an_attestation_to_a_quantity_the_item_no_longer_has_doesnt_count(self):
        # approved at 60.00, then the quantity became 55.00 (their own later revision does count)
        changed = (pay_report(SWCB_REPORT, item("item-1", "55.00")),)
        stale, _ = self.approve(reports=changed, edits=[attest("approve", "item-1", "60.00")])
        assert stale.status_code == 400
        revised, _ = self.approve(reports=changed, edits=[attest("approve", "item-1", "60.00"), attest("revise", "item-1", "55.00")])
        assert revised.status_code == 200

    def test_the_same_amount_written_differently_is_the_same_quantity(self):
        response, _ = self.approve(reports=(pay_report(SWCB_REPORT, item("item-1", "55")),),
                                   edits=[attest("approve", "item-1", "55.00")])
        assert response.status_code == 200

    def test_attestations_from_before_the_stage_was_last_accepted_dont_count(self):
        # an earlier round: the IDR went back to its inspector and was accepted again since
        old = [attest("approve", "item-1", "60.00", at=BEFORE), attest("approve", "item-2", "29.00", at=BEFORE)]
        refused, _ = self.approve(reports=TWO_ITEMS, edits=old)
        assert refused.status_code == 400 and len(refused.json()["untouched"]) == 2
        fresh = [attest("approve", "item-1", "60.00"), attest("approve", "item-2", "29.00")]
        assert self.approve(reports=TWO_ITEMS, edits=old + fresh)[0].status_code == 200

    def test_with_no_acceptance_time_on_the_idr_every_attestation_at_the_stage_counts(self):
        edits = [attest("approve", "item-1", "60.00", at=BEFORE), attest("approve", "item-2", "29.00", at=BEFORE)]
        assert self.approve(reports=TWO_ITEMS, edits=edits, accepted_at=None)[0].status_code == 200

    def test_the_round_starts_at_the_idrs_own_acceptance_time_for_its_stage(self):
        # Stage 1 was accepted after these edits, Stage 2 before them: only the Stage 2 time is read at Stage 2
        edits = [attest("approve", "item-1", "60.00", stage="stage2", at=ACCEPTED_AT),
                 attest("approve", "item-2", "29.00", stage="stage2", at=ACCEPTED_AT)]
        in_round = {**STAGE2_IDR, "stage1_accepted_at": AFTER, "stage2_accepted_at": BEFORE}
        assert self.approve(idr=in_round, reports=TWO_ITEMS, edits=edits, accepted_at=None)[0].status_code == 200
        earlier_round = {**STAGE2_IDR, "stage1_accepted_at": BEFORE, "stage2_accepted_at": AFTER}
        refused, _ = self.approve(idr=earlier_round, reports=TWO_ITEMS, edits=edits, accepted_at=None)
        assert refused.status_code == 400 and len(refused.json()["untouched"]) == 2

    def test_an_edit_made_at_the_moment_of_acceptance_is_in_the_round(self):
        edits = [attest("approve", "item-1", "60.00", at=ACCEPTED_AT), attest("approve", "item-2", "29.00", at=ACCEPTED_AT)]
        assert self.approve(reports=TWO_ITEMS, edits=edits)[0].status_code == 200

    def test_the_audit_log_is_no_longer_read_for_it(self):
        import api.queries.idr_audit as idr_audit
        assert not hasattr(idr_audit, "run_query") and not hasattr(idr_audit, "last_action_time")

    def test_an_admin_standing_in_is_held_to_the_same(self, admin_client):
        with review(idr=STAGE1_IDR, roles=(), reports=TWO_ITEMS, accepted_at=ACCEPTED_AT) as seen:
            refused = admin_client.post(STAGE1_APPROVE)
        assert refused.status_code == 400 and len(refused.json()["untouched"]) == 2 and seen["moves"] == []
        # the reviewer's own attestations are not the admin's
        theirs = [attest("approve", "item-1", "60.00"), attest("approve", "item-2", "29.00")]
        with review(idr=STAGE1_IDR, roles=(), reports=TWO_ITEMS, edits=theirs, accepted_at=ACCEPTED_AT):
            assert admin_client.post(STAGE1_APPROVE).status_code == 400
        admin = {"uuid": ADMIN_USER_ROW["uuid"], "first_name": "Ada", "last_name": "Admin"}
        mine = [attest("approve", "item-1", "60.00", by=admin), attest("approve", "item-2", "29.00", by=admin)]
        with review(idr=STAGE1_IDR, roles=(), reports=TWO_ITEMS, edits=mine, accepted_at=ACCEPTED_AT) as seen:
            assert admin_client.post(STAGE1_APPROVE).status_code == 200
        assert len(seen["moves"]) == 1

    def test_the_gate_comes_before_the_signature_at_stage_two(self):
        unsigned = {**REVIEWER, "signature_path": None, "signature_type": None, "signature_set_at": None}
        with signed_in(unsigned) as client, review(idr=STAGE2_IDR, roles=("re",), reports=TWO_ITEMS,
                                                   accepted_at=ACCEPTED_AT) as seen:
            response = client.post(STAGE2_APPROVE)
        assert response.status_code == 400 and "untouched" in response.json()
        seen["signature"].assert_not_called()

    def test_the_reviewer_check_comes_before_the_gate(self):
        with signed_in(OTHER_REVIEWER) as client, review(idr=STAGE1_IDR, roles=("oe", "re"), reports=TWO_ITEMS) as seen:
            response = client.post(STAGE1_APPROVE)
        assert response.status_code == 403 and response.json() == NOT_THE_REVIEWER and seen["moves"] == []

    def test_reports_or_edits_that_cant_be_read_are_500_not_an_approval(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR, roles=("oe",)) as seen, \
                patch("api.queries.idr_reports.run_query", return_value=None):
            response = client.post(STAGE1_APPROVE)
        assert response.status_code == 500 and seen["moves"] == []

    def test_items_without_an_id_and_malformed_entries_are_skipped(self):
        odd = pay_report(SWCB_REPORT, {"itemNo": "no id", "payQuantity": "1"}, "not an item", item("item-1"))
        response, _ = self.approve(reports=(odd,), edits=[attest("approve", "item-1", "60.00")])
        assert response.status_code == 200

    def test_accept_and_return_are_not_gated(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR, roles=("oe",), reports=TWO_ITEMS) as seen:
            returned = client.post(url("return"), json={"to": "inspector", "comment": "fix the quantities"})
        assert returned.status_code == 200 and len(seen["moves"]) == 1


# ---------------------------------------------------------------------------
# icid.quantities changes in the statement that approves, unlocks or deletes the IDR
# ---------------------------------------------------------------------------

AC_REPORT = UUID("c0ffee00-0000-4000-8000-0000000000ac")
MIX_REPORT = UUID("c0ffee00-0000-4000-8000-0000000000c3")
DELETE_QUANTITIES = "deleted_quantities AS ( DELETE FROM icid.quantities WHERE idr_id IN (SELECT idr_id FROM moved) )"
INSERT_QUANTITIES = "inserted_quantities AS ( INSERT INTO icid.quantities ("
PAY_REPORTS = (
    pay_report(GEN_REPORT, item("gen-1", "2", unit="Each", description="Hydrant"), report_type="GEN"),
    pay_report(SWCB_REPORT, item("item-1", "1,200.50", unit="L.F."), item("item-2", "29.00")),
    pay_report(AC_REPORT, item("ac-1", "18.25", unit="Ton", budgetCode=""), report_type="AC"),
)
ALL_ATTESTED = [attest("approve", "gen-1", "2", stage="stage2", report_id=GEN_REPORT),
                attest("approve", "item-1", "1,200.50", stage="stage2"),
                attest("approve", "item-2", "29.00", stage="stage2"),
                attest("approve", "ac-1", "18.25", stage="stage2", report_id=AC_REPORT)]


def approve(reports=PAY_REPORTS, edits=ALL_ATTESTED, user=REVIEWER, **backend) -> tuple:
    """
    Approve the test IDR at Stage 2 as its RE reviewer.
    Takes the IDR's reports, the reviewer's attestations, the caller and review()'s other keyword arguments.
    Returns (the response, the moving statements run).
    """
    backend.setdefault("roles", ("re",))
    backend.setdefault("moved", [APPROVED_IDR])
    with signed_in(user) as client, review(idr=STAGE2_IDR, reports=reports, edits=edits, **backend) as seen:
        return client.post(STAGE2_APPROVE), seen["moves"]


class TestApprovalWritesQuantities:
    def test_the_idrs_pay_items_go_into_the_approving_statement_as_rows(self):
        response, moves = approve()
        assert response.status_code == 200 and len(moves) == 1
        sql, params = moves[0]
        rows = params[-1].obj
        assert [(r["report_type"], r["pay_item_ref"], r["amount"], r["unit"], r["budget_code"]) for r in rows] == [
            ("GEN", "4.10 A", "2", "EA", "12345"), ("SWCB", "4.10 A", "1200.50", "LF", "12345"),
            ("SWCB", "4.20 A", "29.00", "SF", "12345"), ("AC", "4.10 A", "18.25", "TN", None)]
        assert {(r["project_id"], r["report_date"], r["reporter_uuid"]) for r in rows} == {
            ("HWS0023", "2026-10-05", str(INSPECTOR["uuid"]))}
        assert rows[0]["description"] == "Hydrant"

    def test_the_status_the_log_and_the_quantities_change_in_one_statement(self):
        sql = flat(approve()[1][0][0])
        assert sql.count(";") == 1 and sql.count("UPDATE icid.idrs") == 1
        assert "INSERT INTO icid.idr_audit" in sql and DELETE_QUANTITIES in sql and INSERT_QUANTITIES in sql
        assert "CROSS JOIN moved i RETURNING quantity_id )" in sql  # no IDR moved, no rows written
        assert sql.index("moved AS") < sql.index("logged AS") < sql.index("deleted_quantities AS") \
            < sql.index("inserted_quantities AS")
        assert sql.endswith("FROM moved;")  # and it still returns the IDR

    def test_an_idr_without_pay_items_is_approved_with_no_rows(self):
        trucks = pay_report(MIX_REPORT, report_type="CONC_MIX", is_addendum=True)
        trucks["report_data"] = {"trucks": [{"id": "t1", "slump": "4"}]}
        cylinders = {**trucks, "report_type": "CONC_CYL", "report_data": {"cylinders": [{"id": "c1", "slump": "3"}]}}
        response, moves = approve(reports=(trucks, cylinders), edits=())
        assert response.status_code == 200 and response.json()["data"]["status"] == "approved"
        sql, params = moves[0]
        assert params[-1].obj == [] and DELETE_QUANTITIES in flat(sql)  # any rows it had still go

    def test_an_auto_generated_generals_sums_are_not_written(self):
        auto = pay_report(GEN_REPORT, item("merged-1", "89.00"), report_type="GEN", is_auto_generated=True)
        _, moves = approve(reports=(auto, *TWO_ITEMS), edits=[attest("approve", "item-1", "60.00", stage="stage2"),
                                                              attest("approve", "item-2", "29.00", stage="stage2")])
        assert [r["report_type"] for r in moves[0][1][-1].obj] == ["SWCB", "SWCB"]

    def test_a_quantity_that_isnt_a_number_is_left_out_and_the_idr_is_still_approved(self):
        reports = (pay_report(SWCB_REPORT, item("item-1", "abc"), item("item-2", "29.00")),)
        edits = [attest("approve", "item-1", "abc", stage="stage2"), attest("approve", "item-2", "29.00", stage="stage2")]
        response, moves = approve(reports=reports, edits=edits)
        assert response.status_code == 200
        assert [r["amount"] for r in moves[0][1][-1].obj] == ["29.00"]

    def test_a_lost_race_is_409_and_the_statement_that_ran_could_write_nothing(self):
        response, moves = approve(moved=[])
        assert response.status_code == 409 and response.json() == CHANGED
        sql = flat(moves[0][0])
        # the delete and the insert both read the moved CTE, which is empty when the IDR didn't move
        assert "WHERE idr_id IN (SELECT idr_id FROM moved)" in sql and "CROSS JOIN moved i" in sql
        assert "AND status = %s AND deleted_at IS NULL AND re_reviewer_uuid = %s FOR UPDATE" in sql

    def test_another_re_never_reaches_the_statement(self):
        response, moves = approve(user=OTHER_REVIEWER)
        assert response.status_code == 403 and moves == []

    def test_reports_that_cant_be_read_stop_the_approval_before_anything_is_signed(self):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",)) as seen, \
                patch("api.v1.reviews.quantity_rows_for_idr", return_value=None):
            response = client.post(STAGE2_APPROVE)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to load IDR reports"}
        assert seen["moves"] == []
        seen["signature"].assert_not_called()

    def test_a_statement_that_fails_approves_nothing(self):
        response, moves = approve(moved=None)
        assert response.status_code == 500 and len(moves) == 1  # one statement: the status didn't move either

    def test_stage_one_approval_writes_no_quantities(self):
        edits = [attest("approve", "item-1", "60.00"), attest("approve", "item-2", "29.00")]
        with signed_in(REVIEWER) as client, review(idr=STAGE1_IDR, roles=("oe",), reports=TWO_ITEMS, edits=edits) as seen:
            assert client.post(STAGE1_APPROVE).status_code == 200
        assert "icid.quantities" not in seen["moves"][0][0]


class TestUnlockAndDeleteRemoveQuantities:
    def test_an_unlock_deletes_the_idrs_quantities_in_its_own_statement(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[UNLOCKED_IDR]) as seen:
            assert admin_client.post(url("admin/unlock")).status_code == 200
        sql, params = seen["moves"][0]
        assert DELETE_QUANTITIES in flat(sql) and INSERT_QUANTITIES not in flat(sql) and sql.count(";") == 1
        assert params == (UUID(IDR_ID), ["approved", "stage2_review"], "stage2_review", ADMIN_USER_ROW["uuid"],
                          "admin_unlock", None)  # the delete takes no parameter: it reads the moved IDR

    def test_a_soft_delete_deletes_them_too_and_keeps_the_idr(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[DELETED_IDR]) as seen:
            assert admin_client.post(url("admin/delete")).status_code == 200
        sql = flat(seen["moves"][0][0])
        assert DELETE_QUANTITIES in sql and INSERT_QUANTITIES not in sql
        assert "DELETE FROM icid.idrs" not in sql and "DELETE FROM icid.idr_reports" not in sql

    def test_an_idr_already_deleted_runs_no_statement(self, admin_client):
        with review(idr=DELETED_IDR, roles=()) as seen:
            assert admin_client.post(url("admin/delete")).status_code == 200
        assert seen["moves"] == []

    def test_approving_again_after_an_unlock_writes_the_rows_afresh(self, admin_client):
        with review(idr=APPROVED_IDR, roles=(), moved=[UNLOCKED_IDR]) as seen:
            admin_client.post(url("admin/unlock"))
        assert DELETE_QUANTITIES in flat(seen["moves"][0][0])
        revised = (pay_report(SWCB_REPORT, item("item-1", "55.00"), item("item-2", "29.00")),)
        edits = [attest("revise", "item-1", "55.00", stage="stage2"), attest("approve", "item-2", "29.00", stage="stage2")]
        response, moves = approve(reports=revised, edits=edits)
        assert response.status_code == 200
        assert DELETE_QUANTITIES in flat(moves[0][0])  # whatever was there goes first
        assert [r["amount"] for r in moves[0][1][-1].obj] == ["55.00", "29.00"]

    @pytest.mark.parametrize("route,body", [("accept-stage2", None), ("return", {"to": "oe", "comment": "check"})])
    def test_no_other_move_touches_quantities(self, route, body):
        with signed_in(REVIEWER) as client, review(idr=STAGE2_IDR, roles=("re",)) as seen:
            assert client.post(url(route), json=body).status_code == 200
        assert "icid.quantities" not in seen["moves"][0][0]


# ---------------------------------------------------------------------------
# scripts/backfill_quantities.py
# ---------------------------------------------------------------------------

SECOND_IDR = {**APPROVED_IDR, "idr_id": UUID(OTHER_IDR_ID), "report_date": date(2026, 10, 6)}


class TestBackfillQuantities:
    def run(self, idrs, reports_by_idr, written=None):
        """
        Run the backfill over stubbed IDRs.
        Takes the approved IDR rows the listing returns (None: it fails), {idr uuid: its reports (None: unreadable)} and {idr uuid: what the replace statement returns} (the count of rows handed over unless given).
        Returns (the exit code, the (sql, params) of every statement that wrote quantities).
        """
        from scripts import backfill_quantities
        writes = []

        def reports_query(sql, params=None):
            return reports_by_idr.get(params[0])

        def quantities_query(sql, params=None):
            writes.append((sql, params))
            result = (written or {}).get(params[0], "count")
            return [{"inserted": len(params[1].obj)}] if result == "count" else result

        with patch("api.queries.idrs.run_query", return_value=idrs), \
                patch("api.queries.idr_reports.run_query", side_effect=reports_query), \
                patch("api.queries.quantities.run_query", side_effect=quantities_query):
            return backfill_quantities.main(), writes

    def test_every_approved_idrs_rows_are_replaced_one_statement_each(self, capsys):
        reports = {UUID(IDR_ID): list(PAY_REPORTS), UUID(OTHER_IDR_ID): list(TWO_ITEMS)}
        code, writes = self.run([APPROVED_IDR, SECOND_IDR], reports)
        assert code == 0 and [params[0] for _, params in writes] == [UUID(IDR_ID), UUID(OTHER_IDR_ID)]
        assert [len(params[1].obj) for _, params in writes] == [4, 2]
        assert all("DELETE FROM icid.quantities" in sql and "INSERT INTO icid.quantities" in sql for sql, _ in writes)
        assert writes[1][1][1].obj[0]["report_date"] == "2026-10-06"  # each row takes its own IDR's date
        assert capsys.readouterr().out.strip().endswith("backfill complete: 2 IDRs, 6 quantity rows")

    def test_an_idr_without_pay_items_is_still_written_so_stale_rows_go(self):
        code, writes = self.run([APPROVED_IDR], {UUID(IDR_ID): []})
        assert code == 0 and len(writes) == 1 and writes[0][1][1].obj == []

    def test_progress_is_reported_every_ten_idrs(self, capsys):
        idrs = [{**APPROVED_IDR, "idr_id": UUID(int=n)} for n in range(1, 13)]
        code, _ = self.run(idrs, {idr["idr_id"]: list(TWO_ITEMS) for idr in idrs})
        out = capsys.readouterr().out
        assert code == 0 and "processed 10/12, wrote 20 rows across 10 IDRs" in out
        assert out.strip().endswith("backfill complete: 12 IDRs, 24 quantity rows")

    def test_no_approved_idrs_is_a_clean_run(self, capsys):
        assert self.run([], {}) == (0, [])
        assert "backfill complete: 0 IDRs, 0 quantity rows" in capsys.readouterr().out

    def test_an_idr_that_fails_is_reported_and_the_rest_still_run(self, capsys):
        reports = {UUID(IDR_ID): None, UUID(OTHER_IDR_ID): list(TWO_ITEMS)}
        code, writes = self.run([APPROVED_IDR, SECOND_IDR], reports)
        assert code == 1 and [params[0] for _, params in writes] == [UUID(OTHER_IDR_ID)]
        out = capsys.readouterr().out
        assert f"IDR {IDR_ID} failed: its reports couldn't be read" in out and "1 IDRs failed" in out

    def test_a_write_that_fails_is_reported(self, capsys):
        code, _ = self.run([APPROVED_IDR], {UUID(IDR_ID): list(TWO_ITEMS)}, written={UUID(IDR_ID): None})
        assert code == 1 and f"IDR {IDR_ID} failed: its quantities couldn't be written" in capsys.readouterr().out

    def test_a_listing_that_fails_writes_nothing(self, capsys):
        assert self.run(None, {}) == (1, [])
        assert "the approved IDRs couldn't be read" in capsys.readouterr().out

    def test_only_approved_idrs_that_arent_deleted_are_listed_oldest_first(self):
        with patch("api.queries.idrs.run_query", return_value=[]) as query:
            assert list_approved_idrs() == []
        sql = flat(query.call_args.args[0])
        assert "FROM icid.idrs WHERE status = 'approved' AND deleted_at IS NULL ORDER BY report_date, idr_id;" in sql
