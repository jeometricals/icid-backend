from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.contract_items import bulk_upsert_pay_items
from api.services import auth
from api.services.pay_item_import import CsvFormatError, classify_rows, parse_csv
from api.services.pay_item_units import UnknownUnit, normalize_unit
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# POST /v1/admin/projects/{project_id}/pay-items/import/preview and /commit
# The file goes up as the multipart field "file". Mock rows are dicts, as run_query returns them. Each route
# looks the project up (api.queries.projects), reads the project's pay items once, and commit then runs the
# one upsert statement (both api.queries.contract_items).
# ---------------------------------------------------------------------------

PROJECT_ID = "SE384"
URL = f"/v1/admin/projects/{PROJECT_ID}/pay-items/import"
PREVIEW = f"{URL}/preview"
COMMIT = f"{URL}/commit"
PROJECTS = "api.queries.projects.run_query"
ITEMS = "api.queries.contract_items.run_query"
CRON_SECRET = "cron-secret-for-tests-only"
CRON = {"Authorization": f"Bearer {CRON_SECRET}"}
PROJECT_ROW = {"project_id": PROJECT_ID, "project_name": "Sewers", "project_description": "Sewers",
               "registration_code": None, "borough": "Queens", "status": None}
INSPECTOR_ROW = {**ADMIN_USER_ROW, "role": None}
HEADER = "pay_item_ref,description,quantity,unit,unit_price,budget_code"


def csv_file(*lines: str, header: str = HEADER) -> bytes:
    """
    Build a CSV upload.
    Takes its data lines and, optionally, another header line.
    Returns the file's bytes, UTF-8.
    """
    return "\r\n".join([header, *lines, ""]).encode("utf-8")


def item_line(number: int, unit: str = "LF", budget_code: str = "12345") -> str:
    """
    Build one valid data line.
    Takes a number that makes the ref unique, the unit and the budget code.
    Returns the line: ref UTL-<n>, quantity n * 10, unit price n + 0.5.
    """
    return f"UTL-{number},Item {number},{number * 10},{unit},{number}.5,{budget_code}"


def existing(number: int, budget_code: str = "12345", **changes) -> dict:
    """
    Build a pay item the project already has, matching item_line(number) by ref and budget code.
    Takes the number, the budget code and any columns that differ.
    Returns the row as list_project_pay_items returns it.
    """
    return {"pay_item_uuid": UUID(int=number), "pay_item_ref": f"UTL-{number}", "description": f"Old item {number}",
            "unit": "L.F.", "quantity": Decimal("1.00"), "unit_price": Decimal("2.00"), "budget_code": budget_code,
            "spec_section": "6.01", **changes}


def upload(client, url: str, content: bytes, **kwargs):
    """
    Send a file to an import route.
    Takes the client, the route, the file's bytes and anything else for the request.
    Returns the response.
    """
    return client.post(url, files={"file": ("items.csv", content, "text/csv")}, **kwargs)


@pytest.fixture
def project():
    """
    The project in the URL exists: the project lookup returns it.
    """
    with patch(PROJECTS, return_value=[PROJECT_ROW]) as run:
        yield run


@pytest.fixture
def cron_secret(monkeypatch):
    """
    Give the app a CRON_SECRET for one test.
    """
    monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)


TEN_NEW = csv_file(*[item_line(n) for n in range(1, 11)])
FIVE_EXISTING = [existing(n) for n in range(1, 6)]


class TestPreviewImport:
    def test_ten_new_rows(self, admin_client, project):
        with patch(ITEMS, return_value=[]) as run:
            response = upload(admin_client, PREVIEW, TEN_NEW)
        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["message"]) == ("ok", "Preview ready.")
        data = body["data"]
        assert (data["parsed_rows"], data["new_count"], data["overwrite_count"], data["error_count"]) == (10, 10, 0, 0)
        assert data["ignored_columns"] == [] and data["overwrites"] == [] and data["errors"] == []
        assert [item["row"] for item in data["new"]] == list(range(2, 12))  # file order; the header is row 1
        assert data["new"][0] == {"row": 2, "pay_item_ref": "UTL-1", "description": "Item 1", "unit": "LF",
                                  "quantity": 10.0, "unit_price": 1.5, "budget_code": "12345", "spec_section": None}
        # One read of the project's items, and nothing written
        assert run.call_count == 1
        sql, params = run.call_args.args
        assert sql.lstrip().startswith("SELECT") and "INSERT" not in sql and params == (PROJECT_ID,)

    def test_rows_the_project_already_has_are_overwrites_carrying_both_versions(self, admin_client, project):
        content = csv_file(*[item_line(n) for n in range(1, 6)])
        with patch(ITEMS, return_value=FIVE_EXISTING):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["new_count"], data["overwrite_count"], data["error_count"]) == (0, 5, 0)
        assert data["new"] == []
        assert data["overwrites"][2] == {
            "row": 4, "pay_item_ref": "UTL-3", "budget_code": "12345",
            "existing": {"pay_item_uuid": str(UUID(int=3)), "pay_item_ref": "UTL-3", "description": "Old item 3",
                         "unit": "L.F.", "quantity": 1.0, "unit_price": 2.0, "budget_code": "12345",
                         "spec_section": "6.01"},
            # No spec_section column in the file, so the item would keep the section it has
            "incoming": {"pay_item_ref": "UTL-3", "description": "Item 3", "unit": "LF", "quantity": 30.0,
                         "unit_price": 3.5, "budget_code": "12345", "spec_section": "6.01"},
        }

    def test_the_same_ref_under_another_budget_code_is_new(self, admin_client, project):
        content = csv_file(item_line(1), item_line(1, budget_code="SE0944(CONED-CONS"))
        with patch(ITEMS, return_value=[existing(1)]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["new_count"], data["overwrite_count"]) == (1, 1)
        assert (data["new"][0]["row"], data["new"][0]["budget_code"]) == (3, "SE0944(CONED-CONS")

    def test_an_unknown_unit_is_that_rows_error(self, admin_client, project):
        content = csv_file(item_line(1), item_line(2, unit="XYZ"), item_line(3))
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["parsed_rows"], data["new_count"], data["error_count"]) == (3, 2, 1)
        assert data["errors"] == [{"row": 3, "reason": "unknown unit: 'XYZ'",
                                   "raw": {"pay_item_ref": "UTL-2", "description": "Item 2", "quantity": "20",
                                           "unit": "XYZ", "unit_price": "2.5", "budget_code": "12345"}}]
        assert [item["row"] for item in data["new"]] == [2, 4]

    def test_a_blank_ref_is_that_rows_error(self, admin_client, project):
        lines = [item_line(n) for n in range(1, 8)]
        lines[5] = " ,No ref,1,LF,1,12345"
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, csv_file(*lines)).json()["data"]
        assert data["error_count"] == 1
        assert (data["errors"][0]["row"], data["errors"][0]["reason"]) == (7, "pay_item_ref cannot be blank")

    @pytest.mark.parametrize("line,reason", [
        ("R,,1,LF,1,B", "description cannot be blank"),
        ("R,D,1,,1,B", "unit cannot be blank"),
        ("R,D,1,LF,1,", "budget_code cannot be blank"),
        ("R,D,,LF,1,B", "quantity cannot be blank"),
        ("R,D,lots,LF,1,B", "quantity must be a number: 'lots'"),
        ("R,D,-1,LF,1,B", "quantity cannot be negative"),
        ("R,D,1.12345678901,LF,1,B", "quantity cannot have more than 10 decimal places"),
        ("R,D,1,LF,,B", "unit_price cannot be blank"),
        ("R,D,1,LF,-0.01,B", "unit_price cannot be negative"),
        ("R,D,1,LF,1.1234567,B", "unit_price cannot have more than 6 decimal places"),
        ("R" * 33 + ",D,1,LF,1,B", "pay_item_ref cannot be longer than 32 characters"),
        ("R," + "D" * 501 + ",1,LF,1,B", "description cannot be longer than 500 characters"),
        ("R,D,1,LF,1," + "B" * 51, "budget_code cannot be longer than 50 characters"),
        ("R,D", "quantity cannot be blank"),  # a short row: its missing cells are blank
    ])
    def test_a_row_is_checked_as_the_form_checks_a_pay_item(self, admin_client, project, line, reason):
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, csv_file(line)).json()["data"]
        assert data["error_count"] == 1 and data["errors"][0]["reason"] == reason

    def test_rows_that_share_a_ref_and_budget_code_are_all_errors(self, admin_client, project):
        content = csv_file(item_line(1), item_line(2), item_line(1), item_line(1, budget_code="67890"))
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["new_count"], data["error_count"]) == (2, 2)
        assert [(error["row"], error["reason"]) for error in data["errors"]] == [
            (2, "duplicated in CSV, see also row 4"), (4, "duplicated in CSV, see also row 2")]
        assert [item["row"] for item in data["new"]] == [3, 5]

    def test_three_rows_with_one_key_name_each_other(self, admin_client, project):
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, csv_file(item_line(1), item_line(1), item_line(1))).json()["data"]
        assert [error["reason"] for error in data["errors"]] == [
            "duplicated in CSV, see also rows 3, 4", "duplicated in CSV, see also rows 2, 4",
            "duplicated in CSV, see also rows 2, 3"]

    def test_long_form_units_are_stored_short(self, admin_client, project):
        content = csv_file(item_line(1, unit="L.F."), item_line(2, unit="EACH"), item_line(3, unit="TONS"),
                           item_line(4, unit="P/HR"), item_line(5, unit="CREW/HR"), item_line(6, unit="LBS."),
                           item_line(7, unit="c.y."), item_line(8, unit="SF"))
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert data["error_count"] == 0
        assert [item["unit"] for item in data["new"]] == ["LF", "EA", "TN", "HR", "CREWHR", "LB", "CY", "SF"]

    def test_columns_in_any_order_any_case_with_spec_section_and_extras(self, admin_client, project):
        content = csv_file("1,Sidewalk,S.F.,9500,14.5,4.13 AAS,4.13,12345,x", "2,Curb,L.F.,10,45,4.08 AA,,12345,y",
                           header="Line No,Description,UNIT,Quantity,Unit_Price, pay_item_ref ,spec_section,"
                                  "BUDGET_CODE,Notes")
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["new_count"], data["error_count"]) == (2, 0)
        assert data["ignored_columns"] == ["Line No", "Notes"]
        assert data["new"][1] == {"row": 3, "pay_item_ref": "4.08 AA", "description": "Curb", "unit": "LF",
                                  "quantity": 10.0, "unit_price": 45.0, "budget_code": "12345", "spec_section": None}
        assert set(data["errors"]) == set() and "Notes" not in data["new"][0]

    def test_quoted_cells_thousands_commas_and_a_dollar_sign_are_read(self, admin_client, project):
        content = csv_file('4.13 AAS,"4"" Concrete Sidewalk, unpigmented","9,500.00",S.F.,$14.50,12345,4.13',
                           "4.08 AA,Curb,10,L.F.,45,12345,",
                           header="PAY_ITEM_REF,Description,Quantity,Unit,Unit_Price,Budget_Code,Spec_Section")
        current = existing(1, pay_item_ref="4.08 AA")
        with patch(ITEMS, return_value=[current]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["new_count"], data["overwrite_count"], data["error_count"]) == (1, 1, 0)
        assert data["new"][0] == {"row": 2, "pay_item_ref": "4.13 AAS",
                                  "description": '4" Concrete Sidewalk, unpigmented', "unit": "SF",
                                  "quantity": 9500.0, "unit_price": 14.5, "budget_code": "12345",
                                  "spec_section": "4.13"}
        # The file has a spec_section column, so a blank cell clears the item's section
        assert data["overwrites"][0]["existing"]["spec_section"] == "6.01"
        assert data["overwrites"][0]["incoming"]["spec_section"] is None

    def test_blank_lines_are_skipped_and_rows_keep_their_file_numbers(self, admin_client, project):
        content = ("﻿" + HEADER + "\n" + item_line(1) + "\n\n,,,,,\n" + item_line(2) + "\n").encode("utf-8")
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, content).json()["data"]
        assert (data["parsed_rows"], data["error_count"]) == (2, 0)
        assert [item["row"] for item in data["new"]] == [2, 5]

    def test_a_header_alone_previews_nothing(self, admin_client, project):
        with patch(ITEMS, return_value=[]):
            data = upload(admin_client, PREVIEW, csv_file()).json()["data"]
        assert (data["parsed_rows"], data["new_count"], data["overwrite_count"], data["error_count"]) == (0, 0, 0, 0)

    @pytest.mark.parametrize("header,detail", [
        ("pay_item_ref,description,quantity,unit,unit_price",
         "The file is missing the required column(s): budget_code"),
        ("item,desc,qty", "The file is missing the required column(s): pay_item_ref, description, quantity, unit, "
                          "unit_price, budget_code"),
        (HEADER + ",Unit", "The column unit appears more than once"),
    ])
    def test_a_header_without_the_required_columns_is_400(self, admin_client, project, header, detail):
        with patch(ITEMS) as run:
            response = upload(admin_client, PREVIEW, csv_file(item_line(1), header=header))
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    @pytest.mark.parametrize("content,detail", [
        (HEADER.encode("utf-16"), "The file is not UTF-8 text"),
        (b"PK\x03\x04\xff\xfe binary", "The file is not UTF-8 text"),
        (b"pay_item_ref\x00,description", "The file is not a CSV"),
        (b"", "The file has no header row"),
        (b"\n  \n,,\n", "The file has no header row"),
    ])
    def test_a_file_that_isnt_a_utf8_csv_is_400(self, admin_client, project, content, detail):
        with patch(ITEMS) as run:
            response = upload(admin_client, PREVIEW, content)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_a_file_over_2_mb_is_413_and_one_of_2_mb_is_read(self, admin_client, project):
        # Short lines up to exactly 2 MB: each is a row with an error, which is still a file that can be read
        lines = (HEADER + "\n").encode()
        lines += b"x,y\n" * ((2 * 1024 * 1024 - len(lines)) // 4)
        lines += b" " * (2 * 1024 * 1024 - len(lines))
        assert len(lines) == 2 * 1024 * 1024
        with patch(ITEMS, return_value=[]) as run:
            too_big = upload(admin_client, PREVIEW, lines + b" ")
            assert run.call_count == 0
            at_the_limit = upload(admin_client, PREVIEW, lines)
        assert too_big.status_code == 413 and too_big.json() == {"detail": "The file cannot be larger than 2 MB"}
        assert at_the_limit.status_code == 200 and at_the_limit.json()["data"]["new_count"] == 0

    def test_no_file_is_422(self, admin_client, project):
        with patch(ITEMS) as run:
            assert admin_client.post(PREVIEW).status_code == 422
        run.assert_not_called()

    def test_an_unknown_project_is_404(self, admin_client):
        with patch(PROJECTS, return_value=[]), patch(ITEMS) as run:
            response = upload(admin_client, PREVIEW, TEN_NEW)
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"}
        run.assert_not_called()

    def test_a_failed_read_of_the_projects_items_is_500(self, admin_client, project):
        with patch(ITEMS, return_value=None):
            assert upload(admin_client, PREVIEW, TEN_NEW).status_code == 500

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = upload(inspector, PREVIEW, TEN_NEW)
        assert (response.status_code, response.json()) == (403, {"detail": "Admin access required"})
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS) as projects, patch(ITEMS) as run:
            assert upload(client, PREVIEW, TEN_NEW, headers=CRON).status_code == 401
        projects.assert_not_called()
        run.assert_not_called()


class TestCommitImport:
    def test_ten_new_rows_are_inserted(self, admin_client, project):
        with patch(ITEMS, side_effect=[[], [{"inserted": 10, "updated": 0}]]) as run:
            response = upload(admin_client, COMMIT, TEN_NEW)
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "message": "Imported pay items.",
                                   "data": {"inserted": 10, "updated": 0}}
        assert run.call_count == 2

    def test_five_rows_the_project_has_are_updated(self, admin_client, project):
        content = csv_file(*[item_line(n) for n in range(1, 6)])
        with patch(ITEMS, side_effect=[FIVE_EXISTING, [{"inserted": 0, "updated": 5}]]):
            response = upload(admin_client, COMMIT, content)
        assert response.status_code == 200 and response.json()["data"] == {"inserted": 0, "updated": 5}

    def test_a_mixed_file_inserts_and_updates(self, admin_client, project):
        content = csv_file(*[item_line(n) for n in range(1, 6)])
        with patch(ITEMS, side_effect=[[existing(2), existing(4)], [{"inserted": 3, "updated": 2}]]):
            response = upload(admin_client, COMMIT, content)
        assert response.status_code == 200 and response.json()["data"] == {"inserted": 3, "updated": 2}

    def test_every_row_goes_in_one_upsert_statement_with_its_checked_values(self, admin_client, project):
        content = csv_file("4.13 AAS,Sidewalk,\"9,500.1234567890\",S.F.,14.123456,12345", item_line(2, unit="EACH"))
        with patch(ITEMS, side_effect=[[], [{"inserted": 2, "updated": 0}]]) as run:
            upload(admin_client, COMMIT, content)
        sql, (payload, project_id, replace_spec_section) = run.call_args.args
        assert "INSERT INTO icid.contract_items AS ci" in sql and "FROM jsonb_to_recordset(%s) AS r(" in sql
        assert "ON CONFLICT (project_id, item_no, budget_code) DO UPDATE" in sql
        for column in ("description", "unit", "bid_quantity", "bid_unit_price"):
            assert f"{column} = EXCLUDED.{column}" in sql
        assert "spec_section = CASE WHEN %s THEN EXCLUDED.spec_section ELSE ci.spec_section END" in sql
        assert "updated_at = now()" in sql and "RETURNING (ci.xmax = 0) AS was_inserted" in sql
        assert sql.count("INSERT") == 1 and "DELETE" not in sql
        assert (project_id, replace_spec_section) == (PROJECT_ID, False)
        # Amounts travel as text, so every digit typed reaches the NUMERIC column
        assert payload.obj == [
            {"item_no": "4.13 AAS", "description": "Sidewalk", "unit": "SF", "bid_quantity": "9500.1234567890",
             "bid_unit_price": "14.123456", "budget_code": "12345"},
            {"item_no": "UTL-2", "description": "Item 2", "unit": "EA", "bid_quantity": "20",
             "bid_unit_price": "2.5", "budget_code": "12345"},
        ]

    def test_a_spec_section_column_replaces_the_section_of_overwritten_items(self, admin_client, project):
        content = csv_file("R,D,1,LF,1,B,4.13", "S,D,1,LF,1,B,", header=HEADER + ",spec_section")
        with patch(ITEMS, side_effect=[[], [{"inserted": 2, "updated": 0}]]) as run:
            upload(admin_client, COMMIT, content)
        payload, _, replace_spec_section = run.call_args.args[1]
        assert replace_spec_section is True
        assert [row["spec_section"] for row in payload.obj] == ["4.13", None]

    def test_one_row_with_an_error_refuses_the_file_and_writes_nothing(self, admin_client, project):
        content = csv_file(item_line(1), item_line(2, unit="XYZ"), item_line(3))
        with patch(ITEMS, side_effect=[[]]) as run:
            response = upload(admin_client, COMMIT, content)
        assert response.status_code == 400
        assert response.json() == {
            "detail": "The file has 1 row(s) with errors; fix them and upload it again", "error_count": 1,
            "errors": [{"row": 3, "reason": "unknown unit: 'XYZ'",
                        "raw": {"pay_item_ref": "UTL-2", "description": "Item 2", "quantity": "20", "unit": "XYZ",
                                "unit_price": "2.5", "budget_code": "12345"}}]}
        assert run.call_count == 1 and "INSERT" not in run.call_args.args[0]

    def test_rows_duplicated_in_the_file_refuse_it(self, admin_client, project):
        with patch(ITEMS, side_effect=[[]]) as run:
            response = upload(admin_client, COMMIT, csv_file(item_line(1), item_line(1)))
        assert response.status_code == 400 and response.json()["error_count"] == 2
        assert run.call_count == 1

    def test_a_file_without_rows_is_400(self, admin_client, project):
        with patch(ITEMS, side_effect=[[]]) as run:
            response = upload(admin_client, COMMIT, csv_file())
        assert response.status_code == 400 and response.json() == {"detail": "The file has no pay items"}
        assert run.call_count == 1

    def test_a_file_that_cant_be_read_or_is_too_big_is_refused_as_on_preview(self, admin_client, project):
        with patch(ITEMS) as run:
            missing = upload(admin_client, COMMIT, csv_file(header="pay_item_ref,description"))
            not_utf8 = upload(admin_client, COMMIT, HEADER.encode("utf-16"))
            too_big = upload(admin_client, COMMIT, b"x" * (2 * 1024 * 1024 + 1))
        assert (missing.status_code, not_utf8.status_code, too_big.status_code) == (400, 400, 413)
        run.assert_not_called()

    def test_no_counts_back_is_500(self, admin_client, project):
        with patch(ITEMS, side_effect=[[], None]):
            response = upload(admin_client, COMMIT, TEN_NEW)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to import pay items"}

    def test_an_unknown_project_is_404(self, admin_client):
        with patch(PROJECTS, return_value=[]), patch(ITEMS) as run:
            response = upload(admin_client, COMMIT, TEN_NEW)
        assert response.status_code == 404
        run.assert_not_called()

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = upload(inspector, COMMIT, TEN_NEW)
        assert (response.status_code, response.json()) == (403, {"detail": "Admin access required"})
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS) as projects, patch(ITEMS) as run:
            assert upload(client, COMMIT, TEN_NEW, headers=CRON).status_code == 401
        projects.assert_not_called()
        run.assert_not_called()


class TestImportService:
    def test_parse_keeps_file_order_and_checked_values(self):
        parsed = parse_csv(csv_file(item_line(2, unit="L.F."), item_line(1)))
        assert [row.row for row in parsed.rows] == [2, 3]
        assert parsed.rows[0].fields == {"item_no": "UTL-2", "description": "Item 2", "unit": "LF",
                                         "budget_code": "12345", "bid_quantity": Decimal("20"),
                                         "bid_unit_price": Decimal("2.5")}
        assert (parsed.ignored_columns, parsed.has_spec_section) == ([], False)

    def test_parse_refuses_a_file_it_cant_read(self):
        for content in (b"\xff\xfe", b"", b"a,b\n1,2\n"):
            with pytest.raises(CsvFormatError):
                parse_csv(content)

    def test_classify_reads_the_projects_items_once_and_passes_a_failure_on(self):
        parsed = parse_csv(TEN_NEW)
        with patch(ITEMS, return_value=[existing(1)]) as run:
            result = classify_rows(PROJECT_ID, parsed)
        assert run.call_count == 1
        assert (len(result.new), len(result.overwrites), len(result.errors), len(result.to_write)) == (9, 1, 0, 10)
        with patch(ITEMS, return_value=None):
            assert classify_rows(PROJECT_ID, parsed) is None

    def test_existing_items_are_matched_exactly(self):
        parsed = parse_csv(csv_file(item_line(1)))
        with patch(ITEMS, return_value=[existing(1, pay_item_ref="utl-1"), existing(1, budget_code="12345 ")]):
            result = classify_rows(PROJECT_ID, parsed)
        assert (len(result.new), len(result.overwrites)) == (1, 0)


class TestNormalizeUnit:
    @pytest.mark.parametrize("raw,stored", [
        ("EACH", "EA"), ("L.F.", "LF"), ("S.F.", "SF"), ("S.Y.", "SY"), ("C.Y.", "CY"), ("V.F.", "VF"),
        ("F.S.", "FS"), ("L.S.", "LS"), ("MONTH", "MONTH"), ("DAY", "DAY"), ("P/HR", "HR"), ("CREW/HR", "CREWHR"),
        ("TONS", "TN"), ("LBS.", "LB"), ("BLOCK", "BLOCK"), ("UNITS", "UNIT"), ("SETS", "SET"),
        ("EA", "EA"), ("LF", "LF"), ("SF", "SF"), ("SY", "SY"), ("CY", "CY"), ("VF", "VF"), ("FS", "FS"),
        ("LS", "LS"), ("HR", "HR"), ("CREWHR", "CREWHR"), ("TN", "TN"), ("LB", "LB"), ("UNIT", "UNIT"),
        ("SET", "SET"), (" l.f. ", "LF"), ("Each", "EA"), ("Ton", "TN"), ("L. F.", "LF"),
    ])
    def test_long_and_short_forms_give_the_short_form(self, raw, stored):
        assert normalize_unit(raw) == stored

    @pytest.mark.parametrize("raw", ["XYZ", "", "FT", "L/F", "HOURS", "M"])
    def test_anything_else_is_unknown(self, raw):
        with pytest.raises(UnknownUnit) as error:
            normalize_unit(raw)
        assert str(error.value) == f"unknown unit: '{raw}'"


class TestBulkUpsertQuery:
    def test_it_returns_the_two_counts_or_none(self):
        rows = [{"item_no": "R", "description": "D", "unit": "LF", "bid_quantity": Decimal("1"),
                 "bid_unit_price": Decimal("2"), "budget_code": "B"}]
        with patch(ITEMS, return_value=[{"inserted": 1, "updated": 0}]):
            assert bulk_upsert_pay_items(PROJECT_ID, rows, False) == (1, 0)
        with patch(ITEMS, return_value=None):
            assert bulk_upsert_pay_items(PROJECT_ID, rows, False) is None
