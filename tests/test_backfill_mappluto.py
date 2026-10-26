from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx

from scripts.backfill_mappluto import main

# ---------------------------------------------------------------------------
# scripts/backfill_mappluto.py (Slice Y1): the MapPLUTO frontage of the sidewalks created before Y1.
# The statements and MapPLUTO are mocked.
# ---------------------------------------------------------------------------

PROPS = "api.queries.properties.run_query"
GET = "api.services.mappluto.httpx.get"
SITE_1 = {"id": UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f"), "project_id": "HWS0023", "address": "313 38 Street",
          "boro": "Brooklyn", "block": "700", "lot": "62", "page_1_width_ft": None, "page_1_source": None}
SITE_2 = {**SITE_1, "id": UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e60"), "address": "350 Main St", "block": "37502",
          "lot": "52"}


def mappluto(*fronts: object, address: str = "313 38 STREET") -> list[MagicMock]:
    """
    Build MapPLUTO's answers, one per call: a lot's LotFront, or None for no lot.
    Takes the frontages in call order and the lots' address.
    Returns the mock responses.
    """
    answers = []
    for front in fronts:
        response = MagicMock()
        features = [] if front is None else [{"attributes": {"LotFront": front, "Version": "26v2",
                                                             "Address": address}}]
        response.json.return_value = {"features": features}
        answers.append(response)
    return answers


def test_a_dry_run_fetches_and_writes_nothing(capsys):
    with patch(PROPS, return_value=[SITE_1, SITE_2]) as run, patch(GET, side_effect=mappluto(16.67, None)):
        assert main(commit=False) == 0
    assert run.call_count == 1  # the list only
    out = capsys.readouterr().out
    assert ("HWS0023 313 38 Street (BBL 3007000062): 16.67 ft (mappluto_26v2, 313 38 STREET); page 1 has no width"
            in out)
    assert "CHECK" not in out
    assert "350 Main St (BBL 3375020052): no such lot in MapPLUTO" in out
    assert "dry run complete: 1 with a frontage, 1 without" in out


def test_commit_writes_as_a_refresh_does(capsys):
    refreshed = {"tax_map_frontage_ft": Decimal("16.67"), "tax_map_source": "mappluto_26v2", "tax_map_fetched_at": None,
                 "old_frontage_ft": None, "pages_updated": 1}
    with patch(PROPS, side_effect=[[SITE_1], [refreshed]]) as run, patch(GET, side_effect=mappluto(16.67)):
        assert main(commit=True) == 0
    sql, params = run.call_args.args
    assert "WHERE a.tax_map_source IS DISTINCT FROM 'manual'" in sql
    assert params == (Decimal("16.67"), "mappluto_26v2", SITE_1["id"], "Brooklyn", "700", "62")
    assert "backfill complete: 1 with a frontage, 0 without, 1 title blocks updated" in capsys.readouterr().out


def test_mappluto_down_fails_that_site_and_carries_on(capsys):
    with patch(PROPS, return_value=[SITE_1, SITE_2]), patch(GET, side_effect=[httpx.ConnectError("down"),
                                                                              *mappluto(None)]):
        assert main(commit=False) == 1
    assert "1 failed and were left as they were" in capsys.readouterr().out


def test_a_list_that_cant_be_read_is_1(capsys):
    with patch(PROPS, return_value=None):
        assert main(commit=True) == 1


def test_a_lot_whose_mapPLUTO_address_has_another_house_number_is_flagged(capsys):
    site = {**SITE_1, "address": "300 Main St, Brooklyn", "block": "1234", "lot": "51"}
    with patch(PROPS, return_value=[site]), patch(GET, side_effect=mappluto(28.83, address="296 MAIN STREET")):
        assert main(commit=False) == 0
    out = capsys.readouterr().out
    assert "28.83 ft (mappluto_26v2, 296 MAIN STREET); page 1 has no width  CHECK: MapPLUTO's lot is 296 MAIN STREET" in out
    assert "1 to check (MapPLUTO's address has another house number)" in out
