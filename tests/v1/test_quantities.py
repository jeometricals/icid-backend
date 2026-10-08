from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.v1.quantities import MAX_ROWS
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the columns list_quantities selects.
# ---------------------------------------------------------------------------

PROJECT_ID = "HWS0023"
URL = f"/v1/projects/{PROJECT_ID}/quantities"
PROJECT_ROW = {"project_id": PROJECT_ID, "project_name": "S/W Queens 2025", "project_description": None,
               "registration_code": None, "borough": "Queens", "status": "Active"}
INSPECTOR = {"uuid": UUID("c0000000-0000-4000-8000-000000000003"), "email": "KhanG@magnoleng.pc",
             "first_name": "Genghis", "last_name": "Khan", "client_id": "C00001", "role": None, "is_demo": False,
             "signature_path": None, "signature_type": None, "signature_set_at": None}
IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")


def quantity(number: int, report_type: str = "SWCB", item: str = "4.13 AAS", amount: str = "460.00", unit="SF",
             day: date = date(2026, 10, 4), **columns) -> dict:
    """
    Build a row as list_quantities returns one.
    Takes a number for its id, the report type, the item number, the amount, the unit, the work date and any column overrides.
    Returns the row.
    """
    return {"quantity_id": UUID(int=number), "idr_id": IDR_ID, "idr_number": "SW0001", "report_date": day,
            "reporter_uuid": INSPECTOR["uuid"], "reporter_name": "Genghis Khan", "report_type": report_type,
            "pay_item_ref": item, "budget_code": None, "description": '4" Concrete Sidewalk (Unpigmented)',
            "amount": Decimal(amount), "unit": unit, **columns}


ROWS = [
    quantity(1),
    quantity(2, item="4.02 CA", amount="320.5", unit="LF", budget_code="XYZ", description="Concrete Curb"),
    quantity(3, "AC", "4.02 AB", "48.5", "TN", date(2026, 10, 2), description="Binder course"),
    quantity(4, "GEN", None, "36", "EA", date(2026, 10, 1), description="Extra bollards"),
    quantity(5, amount="790", day=date(2026, 10, 1)),
]


@contextmanager
def backend(rows=ROWS, project=PROJECT_ROW, on_project=True):
    """
    Patch the query layer under the quantities route.
    Takes the rows the listing returns (None: it fails), the project row (None: no such project) and whether the caller is assigned to it.
    Yields the mock of run_query in api.queries.quantities.
    """
    def projects_query(sql, params=None):
        """Stand in for run_query in api.queries.projects."""
        if "FROM icid.project_users" in sql:
            return [{"?column?": 1}] if on_project else []
        return [project] if project else []

    with patch("api.queries.projects.run_query", side_effect=projects_query), \
         patch("api.queries.quantities.run_query", return_value=rows) as query:
        yield query


def flat(sql: str) -> str:
    """
    Put a statement on one line.
    Takes the SQL.
    Returns it with every run of whitespace as one space.
    """
    return " ".join(sql.split())


def get(params=None, user=INSPECTOR, **stubs) -> tuple:
    """
    Call the route as a signed-in user.
    Takes the query parameters (a dict, or a list of pairs to repeat one), the caller and backend()'s keyword arguments.
    Returns (the response, the listing's SQL on one line, its parameters); the last two are None when it didn't run.
    """
    with signed_in(user) as client, backend(**stubs) as query:
        response = client.get(URL, params=params)
    if not query.called:
        return response, None, None
    return response, flat(query.call_args.args[0]), query.call_args.args[1]


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/quantities
# ---------------------------------------------------------------------------

class TestListQuantities:
    def test_no_filters_gives_every_row_of_the_project(self):
        response, sql, params = get()
        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["message"]) == ("success", f"Quantities for project {PROJECT_ID}")
        data = body["data"]
        assert [row["quantity_id"] for row in data["rows"]] == [str(UUID(int=n)) for n in range(1, 6)]
        assert (data["row_count"], data["truncated"]) == (5, False)
        assert "WHERE q.project_id = %s AND i.deleted_at IS NULL ORDER BY" in sql
        assert params == (PROJECT_ID, MAX_ROWS + 1)

    def test_a_row_carries_its_idrs_number_its_inspectors_name_and_its_disciplines(self):
        row = get()[0].json()["data"]["rows"][0]
        assert row == {
            "quantity_id": str(UUID(int=1)), "idr_id": str(IDR_ID), "idr_number": "SW0001",
            "report_date": "2026-10-04", "reporter_uuid": str(INSPECTOR["uuid"]), "reporter_name": "Genghis Khan",
            "report_type": "SWCB", "disciplines": ["Sidewalk", "Curb"], "pay_item_ref": "4.13 AAS",
            "budget_code": None, "description": '4" Concrete Sidewalk (Unpigmented)', "amount": 460.0, "unit": "SF"}

    def test_each_rows_disciplines_come_from_its_report_type(self):
        rows = get()[0].json()["data"]["rows"]
        assert [(row["report_type"], row["disciplines"]) for row in rows] == [
            ("SWCB", ["Sidewalk", "Curb"]), ("SWCB", ["Sidewalk", "Curb"]), ("AC", ["AC Pavement"]),
            ("GEN", ["General"]), ("SWCB", ["Sidewalk", "Curb"])]

    def test_totals_are_summed_per_unit_over_the_rows_returned(self):
        assert get()[0].json()["data"]["totals_by_unit"] == {"EA": 36.0, "LF": 320.5, "SF": 1250.0, "TN": 48.5}

    def test_a_row_without_a_unit_is_summed_under_unknown_and_a_negative_amount_takes_away(self):
        rows = [quantity(1, amount="100"), quantity(2, amount="-5"), quantity(3, unit=None, amount="2.5"),
                quantity(4, unit=None, amount="1")]
        data = get(rows=rows)[0].json()["data"]
        assert data["totals_by_unit"] == {"(unknown)": 3.5, "SF": 95.0}
        assert [row["unit"] for row in data["rows"]] == ["SF", "SF", None, None]

    def test_rows_are_read_newest_day_first_then_by_item_number(self):
        _, sql, _ = get()
        assert sql.endswith("ORDER BY q.report_date DESC, q.pay_item_ref ASC NULLS LAST, q.quantity_id LIMIT %s;")

    def test_the_idr_number_and_the_inspectors_name_are_joined_in_one_statement(self):
        _, sql, _ = get()
        assert "FROM icid.quantities q JOIN icid.idrs i ON i.idr_id = q.idr_id LEFT JOIN icid.users u ON u.uuid = q.reporter_uuid" in sql
        assert "i.idr_number" in sql and "NULLIF(concat_ws(' ', u.first_name, u.last_name), '') AS reporter_name" in sql
        assert sql.count(";") == 1

    def test_a_deleted_idrs_rows_are_never_read(self):
        # admin delete removes an IDR's rows; the listing leaves out any that slipped through
        for params in (None, {"from": "2026-10-01", "discipline": "Sidewalk"}):
            assert "AND i.deleted_at IS NULL" in get(params)[1]

    def test_no_rows_is_an_empty_list_with_no_totals(self):
        data = get({"from": "2026-11-01"}, rows=[])[0].json()["data"]
        assert data == {"rows": [], "totals_by_unit": {}, "row_count": 0, "truncated": False}

    def test_a_failed_listing_is_500(self):
        response, _, _ = get(rows=None)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to fetch quantities"}


class TestQuantityFilters:
    def test_from_and_to_bound_the_work_date_inclusively(self):
        response, sql, params = get({"from": "2026-10-01", "to": "2026-10-04"})
        assert response.status_code == 200
        assert "AND q.report_date >= %s AND q.report_date <= %s" in sql
        assert params == (PROJECT_ID, date(2026, 10, 1), date(2026, 10, 4), MAX_ROWS + 1)

    def test_either_bound_works_alone(self):
        _, sql, params = get({"from": "2026-10-01"})
        assert "q.report_date >= %s" in sql and "q.report_date <= %s" not in sql and params[1] == date(2026, 10, 1)
        _, sql, params = get({"to": "2026-10-04"})
        assert "q.report_date <= %s" in sql and "q.report_date >= %s" not in sql and params[1] == date(2026, 10, 4)

    def test_from_and_to_can_be_the_same_day(self):
        assert get({"from": "2026-10-04", "to": "2026-10-04"})[0].status_code == 200

    def test_a_repeated_pay_item_matches_any_of_them_exactly(self):
        response, sql, params = get([("pay_item", "4.13 AAS"), ("pay_item", "4.02 CA")])
        assert response.status_code == 200
        assert "AND q.pay_item_ref = ANY(%s)" in sql and params == (PROJECT_ID, ["4.13 AAS", "4.02 CA"], MAX_ROWS + 1)

    def test_a_budget_code_filter(self):
        _, sql, params = get({"budget_code": "XYZ"})
        assert "AND q.budget_code = ANY(%s)" in sql and params == (PROJECT_ID, ["XYZ"], MAX_ROWS + 1)

    def test_a_discipline_resolves_to_its_report_types(self):
        response, sql, params = get({"discipline": "Sidewalk"})
        assert response.status_code == 200
        assert "AND q.report_type = ANY(%s)" in sql and params == (PROJECT_ID, ["SWCB"], MAX_ROWS + 1)

    def test_several_disciplines_match_any_of_their_report_types_each_once(self):
        _, _, params = get([("discipline", "Sidewalk"), ("discipline", "AC Pavement"), ("discipline", "Curb")])
        assert params == (PROJECT_ID, ["SWCB", "AC"], MAX_ROWS + 1)

    def test_a_discipline_with_no_pay_items_is_a_valid_filter(self):
        response, _, params = get({"discipline": "Concrete (testing)"}, rows=[])
        assert response.status_code == 200 and params[1] == ["CONC_CYL"]

    def test_an_inspector_filter(self):
        _, sql, params = get({"inspector_uuid": str(INSPECTOR["uuid"])})
        assert "AND q.reporter_uuid = %s" in sql and params == (PROJECT_ID, INSPECTOR["uuid"], MAX_ROWS + 1)

    def test_filters_of_different_kinds_must_all_hold(self):
        _, sql, params = get([("from", "2026-10-01"), ("to", "2026-10-31"), ("pay_item", "4.13 AAS"),
                              ("budget_code", "XYZ"), ("discipline", "Sidewalk"),
                              ("inspector_uuid", str(INSPECTOR["uuid"]))])
        assert ("WHERE q.project_id = %s AND i.deleted_at IS NULL AND q.report_date >= %s AND q.report_date <= %s "
                "AND q.pay_item_ref = ANY(%s) AND q.budget_code = ANY(%s) AND q.report_type = ANY(%s) "
                "AND q.reporter_uuid = %s ORDER BY") in sql
        assert params == (PROJECT_ID, date(2026, 10, 1), date(2026, 10, 31), ["4.13 AAS"], ["XYZ"], ["SWCB"],
                          INSPECTOR["uuid"], MAX_ROWS + 1)

    def test_a_filters_value_is_never_written_into_the_statement(self):
        _, sql, params = get({"pay_item": "x'; DROP TABLE icid.quantities; --"})
        assert "DROP" not in sql and params[1] == ["x'; DROP TABLE icid.quantities; --"]

    @pytest.mark.parametrize("discipline", ["Nonsense", "sidewalk", "SWCB", ""])
    def test_an_unknown_discipline_is_400(self, discipline):
        response, sql, _ = get([("discipline", "Sidewalk"), ("discipline", discipline)])
        assert response.status_code == 400 and response.json() == {"detail": f"Unknown discipline: {discipline}"}
        assert sql is None

    @pytest.mark.parametrize("name", ["from", "to"])
    @pytest.mark.parametrize("value", ["notadate", "10/04/2026", "2026-13-01", "2026-10-32", ""])
    def test_a_malformed_date_is_400(self, name, value):
        response, sql, _ = get({name: value})
        assert response.status_code == 400 and response.json() == {"detail": f"{name} must be a date, YYYY-MM-DD"}
        assert sql is None

    def test_from_after_to_is_400(self):
        response, sql, _ = get({"from": "2026-10-05", "to": "2026-10-04"})
        assert response.status_code == 400 and response.json() == {"detail": "from cannot be after to"}
        assert sql is None

    def test_an_inspector_uuid_that_isnt_one_is_422(self):
        assert get({"inspector_uuid": "genghis"})[0].status_code == 422


class TestQuantityCap:
    def test_the_listing_asks_for_one_row_more_than_a_response_carries(self):
        assert MAX_ROWS == 10000 and get()[2][-1] == 10001

    def test_more_than_the_cap_is_cut_to_it_and_flagged(self):
        rows = [quantity(n, amount="1") for n in range(1, MAX_ROWS + 2)]
        response, _, _ = get(rows=rows)
        assert response.status_code == 200
        data = response.json()["data"]
        assert (len(data["rows"]), data["row_count"], data["truncated"]) == (MAX_ROWS, MAX_ROWS, True)
        assert data["totals_by_unit"] == {"SF": float(MAX_ROWS)}  # the totals are of the rows returned
        assert data["rows"][-1]["quantity_id"] == str(UUID(int=MAX_ROWS))

    def test_exactly_the_cap_isnt_truncated(self):
        data = get(rows=[quantity(n, amount="1") for n in range(1, MAX_ROWS + 1)])[0].json()["data"]
        assert (data["row_count"], data["truncated"]) == (MAX_ROWS, False)


class TestWhoMayReadQuantities:
    def test_a_user_on_the_project_reads_them_whatever_their_role(self):
        assert get()[0].status_code == 200

    def test_a_signed_in_user_who_isnt_on_the_project_is_403(self):
        response, sql, _ = get(on_project=False)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert sql is None

    def test_an_admin_reads_any_project(self, admin_client):
        with backend(on_project=False) as query:
            response = admin_client.get(URL)
        assert response.status_code == 200 and query.called

    def test_a_project_that_doesnt_exist_is_404(self, admin_client):
        with backend(project=None) as query:
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"}
        assert not query.called
        response, sql, _ = get(project=None, on_project=False)  # and for anyone else: 404 comes before 403
        assert response.status_code == 404 and sql is None

    def test_a_demo_user_reaches_only_their_own_project(self):
        response, sql, _ = get(user=DEMO_USER_ROW, on_project=False)
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"} and sql is None
        assert get(user=DEMO_USER_ROW, rows=[])[0].status_code == 200

    def test_no_token_is_401(self, client):
        with backend() as query:
            response = client.get(URL)
        assert response.status_code == 401 and not query.called
