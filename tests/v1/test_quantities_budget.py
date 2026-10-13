from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from api.services.budget_rollup import compute_rollup, money, percent
from tests.conftest import signed_in

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the columns list_quantities_with_budget and list_project_contract_totals select.
# ---------------------------------------------------------------------------

PROJECT_ID = "SE384"
URL = f"/v1/projects/{PROJECT_ID}/quantities"
PROJECT_ROW = {"project_id": PROJECT_ID, "project_name": "Sewers Queens", "project_description": None,
               "registration_code": None, "borough": "Queens", "status": "Active"}
INSPECTOR = {"uuid": UUID("c0000000-0000-4000-8000-000000000003"), "email": "KhanG@magnoleng.pc",
             "first_name": "Genghis", "last_name": "Khan", "client_id": "C00001", "role": None, "is_demo": False,
             "signature_path": None, "signature_type": None, "signature_set_at": None}


def contract_item(item_no: str, budget_code: str, bid_quantity: str, bid_unit_price: str) -> dict:
    """
    Build a row as list_project_contract_totals returns one.
    Takes the item number, the budget code, the bid quantity and the bid unit price.
    Returns the row, its budgeted value worked out as the statement does.
    """
    return {"item_no": item_no, "budget_code": budget_code, "bid_quantity": Decimal(bid_quantity),
            "bid_unit_price": Decimal(bid_unit_price), "budgeted_value": Decimal(bid_quantity) * Decimal(bid_unit_price)}


def actual(item_no, budget_code, quantity: str, bid_quantity=None, unit_price=None, unit="SF",
           description="A pay item") -> dict:
    """
    Build a row as list_quantities_with_budget returns one.
    Takes the item number, the budget code, the summed quantity, the matching contract item's bid quantity and unit price (both None: no contract item matches), the unit and the description.
    Returns the row.
    """
    return {"pay_item_ref": item_no, "budget_code": budget_code, "description": description, "unit": unit,
            "actual_quantity": Decimal(quantity),
            "budgeted_quantity": None if bid_quantity is None else Decimal(bid_quantity),
            "unit_price": None if unit_price is None else Decimal(unit_price), "unbudgeted": unit_price is None}


# Five contract items under three budget codes: 84,675.00 + 4,500.00 | 350.00 + 0.00 | 500.00 = 90,025.00
CATALOG = [
    contract_item("4.08 AA", "HW-CONS", "100", "45"),
    contract_item("4.13 AAS", "HW-CONS", "7500", "11.29"),
    contract_item("6.87", "SE-CONS", "10", "35"),
    contract_item("9.99", "SE-CONS", "0", "20"),       # bid at no quantity
    contract_item("7.01", "WM-CONS", "200", "2.5"),    # nothing booked against it
]

# What was booked: 4.08 AA and 7.01 not at all; two rows that no contract item matches
ACTUALS = [
    actual("4.02 CA", "HW-CONS", "2", unit="TN"),                       # the ref is under another code: unbudgeted
    actual("4.13 AAS", "HW-CONS", "885.000", "7500", "11.29",
           description='4" CONCRETE SIDEWALK (UNPIGMENTED)'),          # 9,991.65 of 84,675.00
    actual("6.87", "SE-CONS", "12", "10", "35", unit="EA"),            # 420.00 of 350.00: over budget
    actual("9.99", "SE-CONS", "3", "0", "20", unit="EA"),              # 60.00 of 0.00
    actual(None, None, "36", unit="EA", description="Extra bollards"),  # no ref at all: unbudgeted
]


@contextmanager
def backend(actuals=ACTUALS, catalog=CATALOG, on_project=True):
    """
    Patch the query layer under the quantities route; the listing of single rows finds none.
    Takes the summed rows (None: that read fails), the project's contract items (None: that read fails) and whether the caller is assigned to the project.
    Yields (the mock of run_query in api.queries.quantities, the one in api.queries.contract_items).
    """
    def projects_query(sql, params=None):
        """Stand in for run_query in api.queries.projects."""
        if "FROM icid.project_users" in sql:
            return [{"?column?": 1}] if on_project else []
        return [PROJECT_ROW]

    def quantities_query(sql, params=None):
        """Stand in for run_query in api.queries.quantities: the summed rows, and no single ones."""
        return actuals if "GROUP BY" in sql else []

    with patch("api.queries.projects.run_query", side_effect=projects_query), \
         patch("api.queries.contract_items.run_query", return_value=catalog) as contract_query, \
         patch("api.queries.quantities.run_query", side_effect=quantities_query) as query:
        yield query, contract_query


def get(params=None, user=INSPECTOR, **stubs) -> tuple:
    """
    Call the route as a signed-in user.
    Takes the query parameters, the caller and backend()'s keyword arguments.
    Returns (the response, [(SQL on one line, parameters)] for each statement run against icid.quantities, the same for icid.contract_items).
    """
    with signed_in(user) as client, backend(**stubs) as (query, contract_query):
        response = client.get(URL, params=params)
    calls = [[(" ".join(call.args[0].split()), call.args[1]) for call in mock.call_args_list]
             for mock in (query, contract_query)]
    return response, calls[0], calls[1]


def data(params=None, **stubs) -> dict:
    """
    Call the route and read what it returned.
    Takes the query parameters and backend()'s keyword arguments.
    Returns the response's data; fails unless it is a 200.
    """
    response = get(params, **stubs)[0]
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/quantities — budget against actual
# ---------------------------------------------------------------------------

class TestRollupLevels:
    def test_a_pay_item_carries_its_actual_and_budgeted_value_and_percent(self):
        item = data()["pay_items"][1]
        assert item == {
            "pay_item_ref": "4.13 AAS", "description": '4" CONCRETE SIDEWALK (UNPIGMENTED)', "unit": "SF",
            "budget_code": "HW-CONS", "actual_quantity": 885.0, "actual_value": 9991.65, "budgeted_quantity": 7500.0,
            "budgeted_value": 84675.0, "unit_price": 11.29, "percent": 11.8, "unbudgeted": False}

    def test_each_budget_code_is_summed_over_all_its_contract_items(self):
        assert data()["totals_by_budget_code"] == [
            # 9,991.65 of 84,675.00 + 4,500.00: 4.08 AA has nothing booked and still counts
            {"budget_code": "HW-CONS", "actual_value": 9991.65, "budgeted_value": 89175.0, "percent": 11.2,
             "has_unbudgeted": True},
            {"budget_code": "SE-CONS", "actual_value": 480.0, "budgeted_value": 350.0, "percent": 137.1,
             "has_unbudgeted": False},
            {"budget_code": "WM-CONS", "actual_value": 0.0, "budgeted_value": 500.0, "percent": 0.0,
             "has_unbudgeted": False}]

    def test_the_project_total_is_every_contract_item_against_every_budgeted_actual(self):
        # 9,991.65 + 420.00 + 60.00 of 90,025.00
        assert data()["project_total"] == {"actual_value": 10471.65, "budgeted_value": 90025.0, "percent": 11.6}

    def test_amounts_are_json_numbers(self):
        body = data()
        assert isinstance(body["pay_items"][1]["actual_value"], float)
        assert isinstance(body["project_total"]["budgeted_value"], float)

    def test_the_rows_as_before_come_with_it(self):
        body = data()
        assert set(body) == {"range", "rows", "totals_by_unit", "row_count", "truncated", "pay_items",
                             "totals_by_budget_code", "project_total", "unbudgeted_total"}
        assert (body["rows"], body["totals_by_unit"], body["row_count"], body["truncated"]) == ([], {}, 0, False)


class TestRollupEdges:
    def test_a_contract_item_with_nothing_booked_is_in_the_totals_but_not_the_pay_items(self):
        body = data()
        assert "7.01" not in [item["pay_item_ref"] for item in body["pay_items"]]
        assert "4.08 AA" not in [item["pay_item_ref"] for item in body["pay_items"]]
        assert [total["budget_code"] for total in body["totals_by_budget_code"]] == ["HW-CONS", "SE-CONS", "WM-CONS"]

    def test_an_over_budget_item_has_a_percent_over_100(self):
        item = data()["pay_items"][2]
        assert (item["pay_item_ref"], item["actual_value"], item["budgeted_value"], item["percent"]) == (
            "6.87", 420.0, 350.0, 120.0)

    def test_an_item_bid_at_nothing_is_still_budgeted_with_no_percent(self):
        item = data()["pay_items"][3]
        assert (item["pay_item_ref"], item["unbudgeted"], item["actual_value"], item["budgeted_value"],
                item["budgeted_quantity"], item["percent"]) == ("9.99", False, 60.0, 0.0, 0.0, None)

    def test_an_unbudgeted_row_has_a_quantity_and_no_value(self):
        body = data()
        assert body["pay_items"][0] == {
            "pay_item_ref": "4.02 CA", "description": "A pay item", "unit": "TN", "budget_code": "HW-CONS",
            "actual_quantity": 2.0, "actual_value": None, "budgeted_quantity": None, "budgeted_value": None,
            "unit_price": None, "percent": None, "unbudgeted": True}
        assert body["pay_items"][4]["pay_item_ref"] is None and body["pay_items"][4]["unbudgeted"] is True
        assert body["unbudgeted_total"] == {"actual_value": None, "row_count": 2}

    def test_unbudgeted_rows_are_in_no_total(self):
        with_them = data()
        without = data(actuals=[row for row in ACTUALS if not row["unbudgeted"]])
        assert with_them["project_total"] == without["project_total"]
        strip = lambda totals: [{k: v for k, v in total.items() if k != "has_unbudgeted"} for total in totals]
        assert strip(with_them["totals_by_budget_code"]) == strip(without["totals_by_budget_code"])
        assert without["unbudgeted_total"] == {"actual_value": None, "row_count": 0}
        assert [total["has_unbudgeted"] for total in without["totals_by_budget_code"]] == [False, False, False]

    def test_a_project_with_no_contract_items_has_only_unbudgeted_rows(self):
        actuals = [actual("4.13 AAS", "HW-CONS", "885"), actual("6.87", "SE-CONS", "12", unit="EA")]
        body = data(actuals=actuals, catalog=[])
        assert [item["unbudgeted"] for item in body["pay_items"]] == [True, True]
        assert body["totals_by_budget_code"] == []
        assert body["project_total"] == {"actual_value": 0.0, "budgeted_value": 0.0, "percent": None}
        assert body["unbudgeted_total"] == {"actual_value": None, "row_count": 2}

    def test_a_project_with_contract_items_and_no_approved_idrs_is_at_zero_percent(self):
        body = data(actuals=[])
        assert body["pay_items"] == []
        assert [(total["budget_code"], total["actual_value"], total["budgeted_value"], total["percent"])
                for total in body["totals_by_budget_code"]] == [
            ("HW-CONS", 0.0, 89175.0, 0.0), ("SE-CONS", 0.0, 350.0, 0.0), ("WM-CONS", 0.0, 500.0, 0.0)]
        assert body["project_total"] == {"actual_value": 0.0, "budgeted_value": 90025.0, "percent": 0.0}
        assert body["unbudgeted_total"] == {"actual_value": None, "row_count": 0}

    def test_a_budget_code_worth_nothing_has_no_percent(self):
        body = data(actuals=[actual("9.99", "SE-CONS", "3", "0", "20")], catalog=[contract_item("9.99", "SE-CONS", "0", "20")])
        assert body["totals_by_budget_code"] == [{"budget_code": "SE-CONS", "actual_value": 60.0, "budgeted_value": 0.0,
                                                  "percent": None, "has_unbudgeted": False}]
        assert body["project_total"]["percent"] is None

    def test_a_failed_read_of_either_is_500(self):
        for stubs in ({"actuals": None}, {"catalog": None}):
            response = get(**stubs)[0]
            assert response.status_code == 500 and response.json() == {"detail": "Failed to fetch quantities"}


class TestRollupRangeAndCumulative:
    def test_the_range_is_echoed(self):
        assert data()["range"] == {"from": None, "to": None, "cumulative": False}
        assert data({"from": "2026-10-01", "to": "2026-10-04"})["range"] == {
            "from": "2026-10-01", "to": "2026-10-04", "cumulative": False}
        assert data({"from": "2026-10-01", "cumulative": "true"})["range"] == {
            "from": "2026-10-01", "to": None, "cumulative": True}

    def test_a_date_range_narrows_the_actuals_and_never_the_budget(self):
        response, statements, contract_statements = get({"from": "2026-10-01", "to": "2026-10-04"})
        assert response.status_code == 200
        listing, summed = statements
        for sql, params in (listing, summed):
            assert "AND q.report_date >= %s AND q.report_date <= %s" in sql
            assert params[:3] == (PROJECT_ID, date(2026, 10, 1), date(2026, 10, 4))
        assert contract_statements == [(contract_statements[0][0], (PROJECT_ID,))]
        assert "report_date" not in contract_statements[0][0]
        # and the budget side of the answer is the same as with no range
        assert response.json()["data"]["project_total"]["budgeted_value"] == 90025.0

    def test_cumulative_ignores_the_range_for_the_actuals(self):
        response, statements, _ = get({"from": "2026-10-01", "to": "2026-10-04", "cumulative": "true"})
        assert response.status_code == 200
        for sql, params in statements:
            assert "report_date >=" not in sql and "report_date <=" not in sql
            assert params[0] == PROJECT_ID and date(2026, 10, 1) not in params
        assert response.json()["data"]["project_total"] == {
            "actual_value": 10471.65, "budgeted_value": 90025.0, "percent": 11.6}

    def test_cumulative_keeps_the_other_filters(self):
        _, statements, _ = get({"from": "2026-10-01", "cumulative": "true", "budget_code": "HW-CONS",
                                "discipline": "Sidewalk"})
        assert statements[1][1] == (PROJECT_ID, ["HW-CONS"], ["SWCB"])

    def test_cumulative_still_refuses_a_bad_date(self):
        response, statements, _ = get({"from": "notadate", "cumulative": "true"})
        assert response.status_code == 400 and statements == []

    def test_the_other_filters_narrow_the_actuals_too(self):
        _, statements, contract_statements = get({"pay_item": "4.13 AAS", "inspector_uuid": str(INSPECTOR["uuid"])})
        assert "AND q.pay_item_ref = ANY(%s) AND q.reporter_uuid = %s" in statements[1][0]
        assert statements[1][1] == (PROJECT_ID, ["4.13 AAS"], INSPECTOR["uuid"])
        assert contract_statements[0][1] == (PROJECT_ID,)


class TestRollupStatements:
    def test_quantities_are_summed_per_ref_and_budget_code_with_a_left_join(self):
        sql, params = get()[1][1]
        assert ("LEFT JOIN icid.contract_items c ON c.project_id = q.project_id AND c.item_no = q.pay_item_ref "
                "AND c.budget_code = q.budget_code") in sql
        assert "SUM(q.amount) AS actual_quantity" in sql and "(c.contract_item_id IS NULL) AS unbudgeted" in sql
        assert ("GROUP BY q.pay_item_ref, q.budget_code, c.contract_item_id, "
                "CASE WHEN c.contract_item_id IS NULL THEN q.unit END") in sql
        assert "WHERE q.project_id = %s AND i.deleted_at IS NULL GROUP BY" in sql and "LIMIT" not in sql
        assert params == (PROJECT_ID,) and sql.count(";") == 1

    def test_every_contract_item_of_the_project_is_read_with_its_value(self):
        sql, params = get()[2][0]
        assert "ci.bid_quantity * ci.bid_unit_price AS budgeted_value" in sql
        assert "FROM icid.contract_items ci WHERE ci.project_id = %s ORDER BY ci.budget_code, ci.item_no;" in sql
        assert params == (PROJECT_ID,)

    def test_three_statements_one_each(self):
        _, statements, contract_statements = get()
        assert (len(statements), len(contract_statements)) == (2, 1)


class TestWhoMayReadTheRollup:
    def test_a_user_who_isnt_on_the_project_is_403(self):
        response, statements, contract_statements = get(on_project=False)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert statements == [] and contract_statements == []

    def test_no_token_is_401(self, client):
        with backend() as (query, contract_query):
            response = client.get(URL)
        assert response.status_code == 401 and not query.called and not contract_query.called

    def test_an_admin_reads_any_project(self, admin_client):
        with backend(on_project=False):
            response = admin_client.get(URL)
        assert response.status_code == 200 and response.json()["data"]["project_total"]["percent"] == 11.6


# ---------------------------------------------------------------------------
# api/services/budget_rollup.py
# ---------------------------------------------------------------------------

class TestComputeRollup:
    def test_it_reads_nothing(self):
        with patch("api.db.runner.run_query") as query:
            compute_rollup(ACTUALS, CATALOG)
        assert not query.called

    def test_nothing_at_all(self):
        assert compute_rollup([], []) == {
            "pay_items": [], "totals_by_budget_code": [],
            "project_total": {"actual_value": 0.0, "budgeted_value": 0.0, "percent": None},
            "unbudgeted_total": {"actual_value": None, "row_count": 0}}

    def test_totals_are_summed_exactly_and_rounded_once(self):
        # three items of 0.004 each: 0.00 apiece, 0.01 together
        catalog = [contract_item(str(n), "A", "1", "1") for n in range(3)]
        actuals = [actual(str(n), "A", "0.004", "1", "1") for n in range(3)]
        rollup = compute_rollup(actuals, catalog)
        assert [item["actual_value"] for item in rollup["pay_items"]] == [0.0, 0.0, 0.0]
        assert rollup["totals_by_budget_code"][0]["actual_value"] == 0.01
        assert rollup["project_total"] == {"actual_value": 0.01, "budgeted_value": 3.0, "percent": 0.4}

    def test_a_negative_quantity_takes_away(self):
        rollup = compute_rollup([actual("1", "A", "-5", "100", "2")], [contract_item("1", "A", "100", "2")])
        assert (rollup["pay_items"][0]["actual_value"], rollup["pay_items"][0]["percent"]) == (-10.0, -5.0)

    def test_money_rounds_halves_up_to_the_cent(self):
        assert [money(Decimal(value)) for value in ("0.125", "0.124", "2.675", "-0.125")] == [0.13, 0.12, 2.68, -0.13]
        assert money(None) is None

    def test_percent_is_to_one_decimal_and_none_over_nothing(self):
        assert percent(Decimal("9991.65"), Decimal("84675")) == 11.8
        assert percent(Decimal("1"), Decimal("2000")) == 0.1  # 0.05 rounds up
        assert percent(Decimal("5"), Decimal("0")) is None and percent(Decimal("0"), Decimal("0")) is None
