from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# PATCH /v1/pirs/{pir_id}/title-block
# ---------------------------------------------------------------------------

PIRS = "api.queries.pirs.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
BLOCK = "api.queries.pir_title_block.run_query"

PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
URL = f"/v1/pirs/{PIR_ID}/title-block"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "RC", "inspector_sign_date": date(2026, 10, 15),
           "digitized_by": ADMIN_USER_ROW["uuid"], "digitized_at": CREATED, "as_built_signed_by": None,
           "as_built_signed_at": None, "north_bearing": None, "created_at": CREATED, "updated_at": CREATED,
           "deleted_at": None, "deleted_by": None}
PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "deleted_at": None}
EMPTY = {"pir_id": PIR_ID, "pir_page_id": PAGE_ID, "north_bearing": None, "contract_no": "HWS2014Q",
         "date_notice_issued": None, "date_excavated": None, "date_constructed": None, "cross_street_left": None,
         "cross_street_right": None, "frontage_street": None, "tax_map_width_ft": None, "cc_curb_to_curb_ft": None}
STREETS = {"cross_street_left": None, "cross_street_right": None, "frontage_street": None, "tax_map_width_ft": None,
           "cc_curb_to_curb_ft": None}
SET = {**EMPTY, "contract_no": "HWS0023", "date_notice_issued": date(2026, 9, 15), "date_excavated": date(2026, 10, 1),
       "date_constructed": date(2026, 10, 5), "north_bearing": "NE"}
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}


def block(before=EMPTY, after=SET, bearing=1, written=1):
    """
    Answer the title-block statements: the read before, the write, the read after.
    Takes the title block before and after, and how many rows the write reports for the PIR and the page.
    Returns the side effect.
    """
    def answer(sql, params=None):
        if sql.lstrip().startswith("WITH"):
            return [{"bearing_written": bearing, "block_written": written}]
        answer.reads += 1
        return [before] if answer.reads == 1 else [after]
    answer.reads = 0
    return answer


def write_call(run):
    """
    Find the write among the title-block statements run.
    Takes the patched run_query.
    Returns (sql, params) of the write, one line of whitespace.
    """
    sql, params = next(call.args for call in run.call_args_list if call.args[0].lstrip().startswith("WITH"))
    return " ".join(sql.split()), params


def patch_all(pir=PIR_ROW, prop=PROPERTY_ROW, title=None):
    """
    Patch the PIR, property and title-block statements together.
    Takes the PIR row, the property row and the title-block side effect.
    Returns the three patches, to enter.
    """
    return patch(PIRS, return_value=[pir]), patch(PROPS, return_value=[prop]), \
        patch(BLOCK, side_effect=title or block())


class TestFields:
    def test_all_five_are_written_and_the_whole_block_comes_back(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"contract_no": " HWS0023 ", "date_notice_issued": "2026-09-15",
                                                     "date_excavated": "2026-10-01", "date_constructed": "2026-10-05",
                                                     "north_bearing": "NE"})
        assert response.status_code == 200 and response.json()["message"] == "Title block saved"
        assert response.json()["data"] == {"pir_id": str(PIR_ID), "contract_no": "HWS0023",
                                           "date_notice_issued": "2026-09-15", "date_excavated": "2026-10-01",
                                           "date_constructed": "2026-10-05", "north_bearing": "NE", **STREETS}
        sql, params = write_call(run)
        assert "UPDATE icid.pirs pir SET north_bearing = %s::icid.compass_point" in sql
        assert "INSERT INTO icid.pir_annotations AS a (pir_page_id, contract_no, date_notice_issued, date_excavated, date_constructed)" in sql
        assert params[:3] == ("NE", PIR_ID, "NE")
        assert params[3:7] == ("HWS0023", date(2026, 9, 15), date(2026, 10, 1), date(2026, 10, 5))
        assert params[7:9] == (PAGE_ID, PIR_ID)

    @pytest.mark.parametrize("field, value, stored", [
        ("contract_no", "HWS0023", "HWS0023"), ("date_notice_issued", "2026-09-15", date(2026, 9, 15)),
        ("date_excavated", "2026-10-01", date(2026, 10, 1)), ("date_constructed", "2026-10-05", date(2026, 10, 5)),
    ])
    def test_one_page_field_alone_writes_only_it(self, admin_client, field, value, stored):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={field: value}).status_code == 200
        sql, params = write_call(run)
        assert f"(pir_page_id, {field})" in sql and f"SET {field} = EXCLUDED.{field}, updated_at = now()" in sql
        assert "icid.pirs pir SET" not in sql and "0 AS bearing_written" in sql
        assert params[0] == stored

    def test_the_bearing_alone_writes_only_the_pir(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={"north_bearing": "N"}).status_code == 200
        sql, params = write_call(run)
        assert "UPDATE icid.pirs pir" in sql and "pir_annotations" not in sql and "0 AS block_written" in sql
        assert params == ("N", PIR_ID, "N")

    def test_a_subset_writes_only_those(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={"contract_no": "HWS0023", "date_constructed": "2026-10-05"}).status_code == 200
        sql, _ = write_call(run)
        assert "(pir_page_id, contract_no, date_constructed)" in sql
        assert "date_excavated" not in sql and "date_notice_issued" not in sql and "north_bearing" not in sql

    @pytest.mark.parametrize("field", ["contract_no", "date_notice_issued", "date_excavated", "date_constructed",
                                       "north_bearing", "cross_street_left", "cross_street_right", "frontage_street",
                                       "tax_map_width_ft", "cc_curb_to_curb_ft"])
    def test_a_null_clears_a_field(self, admin_client, field):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={field: None}).status_code == 200
        _, params = write_call(run)
        assert params[0] is None

    def test_a_blank_contract_no_clears_it(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={"contract_no": "   "}).status_code == 200
        assert write_call(run)[1][0] is None

    def test_a_block_is_made_only_with_something_in_it_and_only_changes_are_written(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            admin_client.patch(URL, json={"date_excavated": None})
        sql, _ = write_call(run)
        assert "(%s::date IS NOT NULL) OR EXISTS (SELECT 1 FROM icid.pir_annotations o" in sql
        assert "WHERE a.date_excavated IS DISTINCT FROM EXCLUDED.date_excavated" in sql

    def test_saving_what_is_stored_changes_nothing(self, admin_client):
        p1, p2, p3 = patch_all(title=block(before=SET, after=SET, bearing=0, written=0))
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"north_bearing": "NE", "contract_no": "HWS0023"})
        assert response.status_code == 200 and response.json()["message"] == "Nothing changed"
        assert "pir.north_bearing IS DISTINCT FROM %s::icid.compass_point" in write_call(run)[0]


class TestStreetsAndWidths:
    def test_the_streets_and_widths_are_written_and_come_back(self, admin_client):
        after = {**EMPTY, "cross_street_left": "139 AVENUE", "cross_street_right": "138 AVENUE",
                 "frontage_street": "227 STREET", "tax_map_width_ft": Decimal("40.00"),
                 "cc_curb_to_curb_ft": Decimal("34.25")}
        p1, p2, p3 = patch_all(title=block(after=after))
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"cross_street_left": " 139 AVENUE ", "cross_street_right": "138 AVENUE",
                                                     "frontage_street": "227 STREET", "tax_map_width_ft": 40,
                                                     "cc_curb_to_curb_ft": "34.25"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["cross_street_left"], data["frontage_street"]) == ("139 AVENUE", "227 STREET")
        assert (data["tax_map_width_ft"], data["cc_curb_to_curb_ft"]) == (40.0, 34.25)
        sql, params = write_call(run)
        assert ("(pir_page_id, cross_street_left, cross_street_right, frontage_street, tax_map_width_ft, "
                "cc_curb_to_curb_ft)") in sql
        assert "%s::numeric" in sql
        assert params[:5] == ("139 AVENUE", "138 AVENUE", "227 STREET", Decimal("40"), Decimal("34.25"))

    def test_a_blank_street_clears_it(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={"frontage_street": "  "}).status_code == 200
        assert write_call(run)[1][0] is None

    def test_a_street_over_60_characters_is_400(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"cross_street_right": "x" * 61})
        assert response.status_code == 400
        assert response.json() == {"detail": "cross_street_right cannot be longer than 60 characters"}
        run.assert_not_called()

    def test_60_characters_are_fine(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3:
            assert admin_client.patch(URL, json={"cross_street_left": "x" * 60}).status_code == 200

    def test_a_street_that_isnt_text_is_400(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3:
            response = admin_client.patch(URL, json={"frontage_street": 227})
        assert response.status_code == 400 and response.json() == {"detail": "frontage_street must be text"}

    @pytest.mark.parametrize("value", [-1, "-0.01", 40.005, "forty", True, [40], "NaN", "Infinity", 100000000])
    def test_a_width_that_doesnt_fit_is_400_naming_it(self, admin_client, value):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"tax_map_width_ft": value})
        assert response.status_code == 400
        assert response.json() == {"detail": "tax_map_width_ft must be a number of feet, at least 0, "
                                             "with at most 2 decimal places"}
        run.assert_not_called()

    @pytest.mark.parametrize("value, stored", [(0, Decimal("0")), (34.25, Decimal("34.25")), ("40.00", Decimal("40.00")),
                                               (" 12.5 ", Decimal("12.5"))])
    def test_a_width_is_taken_as_a_number_or_number_text(self, admin_client, value, stored):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            assert admin_client.patch(URL, json={"cc_curb_to_curb_ft": value}).status_code == 200
        assert write_call(run)[1][0] == stored


class TestRefusals:
    def test_nothing_sent_is_400(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        run.assert_not_called()

    @pytest.mark.parametrize("field, value", [("date_constructed", "2026-13-01"), ("date_excavated", "10/01/2026"),
                                              ("date_notice_issued", 20261001), ("date_constructed", "2026-02-30")])
    def test_a_date_that_isnt_one_is_400_naming_it(self, admin_client, field, value):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={field: value})
        assert response.status_code == 400 and response.json() == {"detail": f"{field} must be a date (YYYY-MM-DD)"}
        run.assert_not_called()

    @pytest.mark.parametrize("value", ["XX", "ne", "NNE", 45])
    def test_a_bearing_that_isnt_a_compass_point_is_400(self, admin_client, value):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"north_bearing": value})
        assert response.status_code == 400 and response.json() == {"detail": "Invalid north bearing"}
        run.assert_not_called()

    def test_a_contract_no_over_200_characters_is_400(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"contract_no": "x" * 201})
        assert response.status_code == 400
        assert response.json() == {"detail": "contract_no cannot be longer than 200 characters"}
        run.assert_not_called()

    def test_200_characters_are_fine(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3:
            assert admin_client.patch(URL, json={"contract_no": "x" * 200}).status_code == 200

    def test_an_unknown_field_is_400(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"violation_no": "61188"})
        assert response.status_code == 400 and response.json() == {"detail": "Unknown field: violation_no"}
        run.assert_not_called()

    def test_a_pir_without_a_live_page_1_is_400(self, admin_client):
        p1, p2, p3 = patch_all(title=block(before={**EMPTY, "pir_page_id": None}))
        with p1, p2, p3 as run:
            response = admin_client.patch(URL, json={"north_bearing": "N"})
        assert response.status_code == 400 and response.json() == {"detail": "PIR has no page 1 to edit"}
        assert run.call_count == 1  # read, nothing written

    def test_a_deleted_pir_is_400_for_a_member(self):
        deleted = {**PIR_ROW, "deleted_at": CREATED, "deleted_by": ADMIN_USER_ROW["uuid"]}
        p1, p2, p3 = patch_all(pir=deleted)
        with signed_in(MEMBER_ROW) as client, p1, p2, p3 as run, patch(PROJECTS, return_value=[{"?column?": 1}]):
            response = client.patch(URL, json={"north_bearing": "N"})
        assert response.status_code == 400 and response.json() == {"detail": "PIR is deleted"}
        run.assert_not_called()

    def test_a_pir_of_a_deleted_property_is_400_for_a_member(self):
        p1, p2, p3 = patch_all(prop={**PROPERTY_ROW, "deleted_at": CREATED})
        with signed_in(MEMBER_ROW) as client, p1, p2, p3 as run, patch(PROJECTS, return_value=[{"?column?": 1}]):
            response = client.patch(URL, json={"north_bearing": "N"})
        assert response.status_code == 400 and response.json() == {"detail": "Property is deleted"}
        run.assert_not_called()

    def test_an_admin_can_still_edit_a_deleted_pir(self, admin_client):
        p1, p2, p3 = patch_all(pir={**PIR_ROW, "deleted_at": CREATED})
        with p1, p2, p3:
            assert admin_client.patch(URL, json={"north_bearing": "N"}).status_code == 200

    def test_an_unknown_pir_is_404(self, admin_client):
        with patch(PIRS, return_value=[]), patch(BLOCK) as run:
            assert admin_client.patch(URL, json={"north_bearing": "N"}).status_code == 404
        run.assert_not_called()


class TestWho:
    def test_a_member_of_the_project_edits_it(self):
        p1, p2, p3 = patch_all()
        with signed_in(MEMBER_ROW) as client, p1, p2, p3, patch(PROJECTS, return_value=[{"?column?": 1}]):
            assert client.patch(URL, json={"north_bearing": "N"}).status_code == 200

    def test_a_user_who_isnt_on_the_project_is_403(self):
        p1, p2, p3 = patch_all()
        with signed_in(MEMBER_ROW) as client, p1, p2, p3 as run, patch(PROJECTS, return_value=[]):
            assert client.patch(URL, json={"north_bearing": "N"}).status_code == 403
        run.assert_not_called()

    def test_an_admin_edits_any_projects(self, admin_client):
        p1, p2, p3 = patch_all()
        with p1, p2, p3, patch(PROJECTS, return_value=[]):
            assert admin_client.patch(URL, json={"north_bearing": "N"}).status_code == 200

    def test_a_demo_user_is_403(self, demo_client):
        p1, p2, p3 = patch_all(pir={**PIR_ROW, "project_id": "DEMO01"})
        with p1, p2, p3 as run, patch(PROJECTS, return_value=[{"?column?": 1}]):
            response = demo_client.patch(URL, json={"north_bearing": "N"})
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        run.assert_not_called()
