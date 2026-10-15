import copy
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# POST /v1/pirs/{pir_id}/import
# Mock rows are dicts, as run_query returns them: the PIR (with its project), its live pages, the project's pay items,
# and the import statement's counts.
# ---------------------------------------------------------------------------

PIRS = "api.queries.pirs.run_query"
PAGES = "api.queries.pir_pages.run_query"
CONTRACT = "api.queries.contract_items.run_query"
PROJECTS = "api.queries.projects.run_query"
PROPS = "api.queries.properties.run_query"
IMPORT = "api.queries.pir_import.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
PAGE_1 = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PAGE_2 = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d2e")
PAY_ITEM_ID = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e")
URL = f"/v1/pirs/{PIR_ID}/import"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
PIR_ROW = {"id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023", "source_image_url": None,
           "source_image_scale": None, "inspector_initials": "JD", "inspector_sign_date": date(2026, 9, 30),
           "digitized_by": ADMIN_ID, "digitized_at": CREATED, "as_built_signed_by": None, "as_built_signed_at": None,
           "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None}


def page_row(page_id: UUID, number: int) -> dict:
    """
    A live page as list_pages_for_pir returns it.
    Takes the page's id and number.
    Returns the row.
    """
    return {"pir_page_id": page_id, "pir_id": PIR_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "page_number": number, "image_path": f"pirs/{PIR_ID}/u1/page-{number}.png", "image_mime": "image/png",
            "width_px": 1275, "height_px": 1650, "scale_px_per_ft": Decimal("15.0000"), "rotation_deg": 0,
            "created_at": CREATED, "updated_at": CREATED, "deleted_at": None, "deleted_by": None,
            "pir_deleted_at": None, "property_deleted_at": None}


PAGE_ROWS = [page_row(PAGE_1, 1), page_row(PAGE_2, 2)]
PAY_ITEM_ROWS = [{"pay_item_uuid": PAY_ITEM_ID, "pay_item_ref": "4.01", "description": "Sidewalk", "unit": "SF",
                  "quantity": Decimal("100"), "unit_price": Decimal("12"), "budget_code": "SE01", "spec_section": None}]
COUNTS = {"pages_updated": 1, "segments_rescaled": 0, "flags": 5, "non_assessable_areas": 2, "features": 3,
          "segments": 2, "annotations": 1}
ON_PROJECT = [{"?column?": 1}]
SAMPLE = json.loads(Path(__file__).parent.parent.joinpath("fixtures", "smart_pir_sample.json").read_text(encoding="utf-8"))


def body(**changes) -> dict:
    """
    The sample file for this PIR.
    Takes top-level keys to replace.
    Returns the body as a dict.
    """
    return {**copy.deepcopy(SAMPLE), "pir_id": str(PIR_ID), **changes}


def post(client, payload: dict, counts: dict = COUNTS, pir: dict = PIR_ROW):
    """
    Post an import with the reads and the statement mocked.
    Takes the client, the body, the statement's counts and the PIR row.
    Returns (the response, the pages mock, the pay items mock, the statement mock).
    """
    with patch(PIRS, return_value=[pir]), patch(PAGES, return_value=PAGE_ROWS) as pages, \
            patch(CONTRACT, return_value=PAY_ITEM_ROWS) as items, patch(IMPORT, return_value=[counts]) as run:
        response = client.post(URL, json=payload)
    return response, pages, items, run


class TestImport:
    def test_an_admin_imports_the_sample(self, admin_client):
        response, pages, items, run = post(admin_client, body())
        assert response.status_code == 200
        assert response.json() == {
            "status": "success", "message": "Imported 13 items, dropped 0",
            "data": {"inserted": {"flags": 5, "non_assessable_areas": 2, "features": 3, "segments": 2, "annotations": 1},
                     "dropped": {"flags": 0, "non_assessable_areas": 0, "features": 0, "segments": 0, "annotations": 0},
                     "dropped_items": [], "pages_updated": 1}}
        assert pages.call_count == 1 and items.call_count == 1 and run.call_count == 1
        assert items.call_args.args[1] == ("HWS0023",)

    def test_dropped_items_come_back_with_their_reasons(self, admin_client):
        payload = body()
        payload["pages"][0]["flags"][1]["shape"] = "hexagon"
        response, *_ = post(admin_client, payload, {**COUNTS, "flags": 4})
        data = response.json()["data"]
        assert response.json()["message"] == "Imported 12 items, dropped 1"
        assert data["dropped"]["flags"] == 1
        (item,) = data["dropped_items"]
        assert (item["kind"], item["page_number"], item["index"]) == ("flag", 1, 1) and item["reason"].startswith("shape:")

    def test_the_statement_is_one_with_every_cte(self, admin_client):
        _, _, _, run = post(admin_client, body())
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        for cte in ("live AS (", "page_settings AS (", "rescaled AS (", "new_flags AS (", "new_areas AS (",
                    "new_features AS (", "new_segments AS (", "new_annotations AS ("):
            assert cte in one_line
        assert "JOIN new_flags nf ON nf.id = x.flag_id" in one_line
        assert "LEFT JOIN new_flags nf ON nf.id = x.flag_id" in one_line
        assert "COALESCE(x.scale_px_per_ft, live.scale_px_per_ft)" in one_line
        assert "ON CONFLICT (pir_page_id) DO UPDATE" in one_line
        assert len(params) == 11
        assert params[0] == PIR_ID and params[2] == params[4] == params[6] == params[8] == ADMIN_ID

    def test_the_rows_go_as_json_with_decimals_and_uuids_as_text(self, admin_client):
        _, _, _, run = post(admin_client, body())
        params = run.call_args.args[1]
        pages, flags, areas, features, segments, annotations = (params[i].obj for i in (1, 3, 5, 7, 9, 10))
        assert pages == [{"pir_page_id": str(PAGE_1), "scale_px_per_ft": "15", "rotation_deg": 90}]
        assert len(flags) == 5 and flags[0]["pir_page_id"] == str(PAGE_1) and flags[0]["computed_quantity"] == "20.00"
        assert UUID(flags[0]["id"]) and areas[0]["flag_id"] == flags[0]["id"]
        assert features[0]["flag_id"] == flags[0]["id"] and features[0]["computed_quantity"] == "3.14"
        assert [segment["scale_px_per_ft"] for segment in segments] == ["15", None]
        assert annotations[0]["pir_page_id"] == str(PAGE_1) and annotations[0]["tax_map_width_ft"] == "25"

    def test_a_member_imports(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response, *_ = post(member, body())
        assert response.status_code == 200


class TestRefusals:
    def test_a_file_for_another_pir_is_400(self, admin_client):
        response, pages, _, run = post(admin_client, body(pir_id="00000000-0000-4000-8000-0000000000a1"))
        assert response.status_code == 400 and response.json() == {"detail": "The file's pir_id is not this PIR"}
        pages.assert_not_called()
        run.assert_not_called()

    @pytest.mark.parametrize("payload", [
        {"schema_version": "2.0", "pir_id": str(PIR_ID), "pages": []},
        {"schema_version": "1.0", "pir_id": str(PIR_ID)},
    ], ids=["version", "no-pages"])
    def test_a_file_that_isnt_an_import_is_422(self, admin_client, payload):
        response, _, _, run = post(admin_client, payload)
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_demo_user_is_403(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT):
            response, _, _, run = post(demo_client, body())
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: PIRs are read-only"}
        run.assert_not_called()

    def test_a_user_who_isnt_on_the_project_is_403(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]):
            response, _, _, run = post(member, body())
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_an_unknown_pir_is_404(self, admin_client):
        with patch(PIRS, return_value=[]), patch(IMPORT) as run:
            response = admin_client.post(URL, json=body())
        assert response.status_code == 404 and response.json() == {"detail": "PIR not found"}
        run.assert_not_called()

    def test_a_member_cant_import_onto_a_deleted_pir(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response, _, _, run = post(member, body(), pir={**PIR_ROW, "deleted_at": DELETED})
        assert response.status_code == 400 and response.json() == {"detail": "PIR is deleted"}
        run.assert_not_called()

    def test_an_admin_can_import_onto_a_deleted_pir(self, admin_client):
        response, *_ = post(admin_client, body(), pir={**PIR_ROW, "deleted_at": DELETED})
        assert response.status_code == 200

    def test_a_failed_statement_is_500(self, admin_client):
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, return_value=PAGE_ROWS), \
                patch(CONTRACT, return_value=PAY_ITEM_ROWS), patch(IMPORT, return_value=[]):
            response = admin_client.post(URL, json=body())
        assert response.status_code == 500 and response.json() == {"detail": "Failed to import the PIR"}


class TestFlagMeasurablePayItems:
    """R1f: the route hands the service the project's pay items a flag can be measured in, apart from the rest."""

    def test_only_pay_items_in_a_flags_units_are_passed_for_flags(self, admin_client):
        lf_item = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4f")
        rows = PAY_ITEM_ROWS + [{**PAY_ITEM_ROWS[0], "pay_item_uuid": lf_item, "unit": "LF"}]
        with patch(PIRS, return_value=[PIR_ROW]), patch(PAGES, return_value=PAGE_ROWS), \
                patch(CONTRACT, return_value=rows), patch("api.v1.pir_import.run_import", return_value=None) as run:
            admin_client.post(URL, json=body())
        args = run.call_args.args
        assert args[4] == {PAY_ITEM_ID, lf_item} and args[5] == {PAY_ITEM_ID}
