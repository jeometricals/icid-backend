from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx
import pytest

from api.services.mappluto import (
    MapPlutoUnavailable,
    bbl,
    cache_frontage,
    fetch_frontage_by_bbl,
    lookup_lot,
    source_display,
    source_label,
)

# ---------------------------------------------------------------------------
# api/services/mappluto.py (Slice Y1): a sidewalk's tax-map frontage from DCP's MapPLUTO.
# The call is mocked: httpx.get returns what the ArcGIS layer answers.
# ---------------------------------------------------------------------------

GET = "api.services.mappluto.httpx.get"
PROPS = "api.queries.properties.run_query"
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")


def answer(body: object) -> MagicMock:
    """
    Build MapPLUTO's response.
    Takes the JSON body it carries.
    Returns the mock response.
    """
    response = MagicMock()
    response.json.return_value = body
    return response


def lot(front: object, version: object = "26v2") -> MagicMock:
    """
    Build MapPLUTO's response for one lot.
    Takes its LotFront and Version.
    Returns the mock response.
    """
    return answer({"features": [{"attributes": {"BBL": 1008350041, "LotFront": front, "Version": version}}]})


class TestBbl:
    @pytest.mark.parametrize("boro,code", [("Manhattan", "1"), ("Bronx", "2"), ("Brooklyn", "3"), ("Queens", "4"),
                                           ("Staten Island", "5")])
    def test_the_borough_is_its_code(self, boro, code):
        assert bbl(boro, "835", "41") == f"{code}008350041"

    def test_block_and_lot_are_padded_to_5_and_4_digits(self):
        assert bbl("Queens", "1", "0") == "4000010000" and bbl("Queens", "99999", "9999") == "4999999999"


class TestSourceLabel:
    @pytest.mark.parametrize("version,label", [("26v2", "mappluto_26v2"), ("25v1.1", "mappluto_25v1.1"),
                                               (" 26V3 ", "mappluto_26v3")])
    def test_dcps_version_is_kept(self, version, label):
        assert source_label(version) == label

    @pytest.mark.parametrize("version", [None, "", "2026q3", "v2", "26"])
    def test_anything_else_is_none(self, version):
        assert source_label(version) is None

    @pytest.mark.parametrize("source,words", [("mappluto_26v2", "MapPLUTO 26v2"), ("manual", "Manual"),
                                              ("pip_scrape", "PIP"), ("r1k", "R1k"), (None, None)])
    def test_sources_are_worded_for_the_report(self, source, words):
        assert source_display(source) == words


class TestFetchFrontage:
    def test_a_known_lot_gives_its_frontage_and_version(self):
        with patch(GET, return_value=lot(197.5)) as get:
            assert fetch_frontage_by_bbl("Manhattan", "835", "41") == (Decimal("197.50"), "mappluto_26v2")
        params = get.call_args.kwargs["params"]
        assert params["where"] == "BBL=1008350041" and params["returnGeometry"] == "false"
        assert get.call_args.kwargs["timeout"] == 10

    def test_the_frontage_is_kept_to_the_cent(self):
        with patch(GET, return_value=lot(25.005)):
            assert fetch_frontage_by_bbl("Queens", "1", "1")[0] == Decimal("25.01")

    def test_a_lot_mapPLUTO_doesnt_have_is_none(self):
        with patch(GET, return_value=answer({"features": []})):
            assert fetch_frontage_by_bbl("Queens", "99999", "9999") is None

    @pytest.mark.parametrize("front", [0, -5, 1000, 9999, 999.996, None, "NaN", "abc"])
    def test_a_suspect_frontage_is_none(self, front):
        with patch(GET, return_value=lot(front)):
            assert fetch_frontage_by_bbl("Queens", "1", "1") is None

    def test_a_version_that_isnt_dcps_is_none(self):
        with patch(GET, return_value=lot(40, "2026q3")):
            assert fetch_frontage_by_bbl("Queens", "1", "1") is None

    def test_several_records_take_the_first(self):
        body = {"features": [{"attributes": {"LotFront": 40, "Version": "26v2"}},
                             {"attributes": {"LotFront": 50, "Version": "26v2"}}]}
        with patch(GET, return_value=answer(body)):
            assert fetch_frontage_by_bbl("Queens", "1", "1") == (Decimal("40.00"), "mappluto_26v2")

    @pytest.mark.parametrize("failure", [httpx.ConnectTimeout("slow"), httpx.ConnectError("down")])
    def test_a_service_that_cant_be_reached_raises(self, failure):
        with patch(GET, side_effect=failure), pytest.raises(MapPlutoUnavailable):
            fetch_frontage_by_bbl("Queens", "1", "1")

    def test_an_http_error_raises(self):
        response = answer({})
        response.raise_for_status.side_effect = httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock())
        with patch(GET, return_value=response), pytest.raises(MapPlutoUnavailable):
            fetch_frontage_by_bbl("Queens", "1", "1")

    @pytest.mark.parametrize("body", [{"error": {"code": 400, "message": "Invalid query"}}, {"nothing": 1}, []])
    def test_an_arcgis_error_or_a_strange_answer_raises(self, body):
        with patch(GET, return_value=answer(body)), pytest.raises(MapPlutoUnavailable):
            fetch_frontage_by_bbl("Queens", "1", "1")

    def test_an_answer_that_isnt_json_raises(self):
        response = answer(None)
        response.json.side_effect = ValueError("not JSON")
        with patch(GET, return_value=response), pytest.raises(MapPlutoUnavailable):
            fetch_frontage_by_bbl("Queens", "1", "1")


class TestCacheFrontage:
    def test_a_frontage_found_is_written_for_the_lot_it_was_fetched_for(self):
        with patch(GET, return_value=lot(40)), patch(PROPS, return_value=[{"id": PROPERTY_ID}]) as run:
            cache_frontage(PROPERTY_ID, "Queens", "1234", "56")
        sql, params = run.call_args.args
        assert "SET tax_map_frontage_ft = %s, tax_map_source = %s, tax_map_fetched_at = now()" in sql
        assert "p.kind = 'sidewalk' AND p.boro = %s::icid.boro AND p.block = %s AND p.lot = %s" in sql
        assert params == (Decimal("40.00"), "mappluto_26v2", PROPERTY_ID, "Queens", "1234", "56")

    def test_no_lot_writes_nothing(self):
        with patch(GET, return_value=answer({"features": []})), patch(PROPS) as run:
            cache_frontage(PROPERTY_ID, "Queens", "1234", "56")
        run.assert_not_called()

    def test_a_service_down_writes_nothing_and_raises_nothing(self):
        with patch(GET, side_effect=httpx.ConnectError("down")), patch(PROPS) as run:
            cache_frontage(PROPERTY_ID, "Queens", "1234", "56")
        run.assert_not_called()

    def test_a_failed_write_raises_nothing(self):
        with patch(GET, return_value=lot(40)), patch(PROPS, side_effect=RuntimeError("db down")):
            cache_frontage(PROPERTY_ID, "Queens", "1234", "56")

    def test_a_ped_ramp_isnt_looked_up(self):
        with patch(GET) as get:
            cache_frontage(PROPERTY_ID, "Brooklyn", None, None)
        get.assert_not_called()


class TestLookupLot:
    def test_it_gives_the_lots_address_too(self):
        body = {"features": [{"attributes": {"LotFront": 16.67, "Version": "26v2", "Address": " 313 38 STREET "}}]}
        with patch(GET, return_value=answer(body)) as get:
            assert lookup_lot("Brooklyn", "700", "62") == {"frontage_ft": Decimal("16.67"), "source": "mappluto_26v2",
                                                           "address": "313 38 STREET"}
        assert get.call_args.kwargs["params"]["outFields"] == "BBL,LotFront,Version,Address"

    def test_a_suspect_frontage_is_none_with_the_address_kept(self):
        body = {"features": [{"attributes": {"LotFront": 0, "Version": "26v2", "Address": "1 MAIN STREET"}}]}
        with patch(GET, return_value=answer(body)):
            assert lookup_lot("Brooklyn", "1", "1")["frontage_ft"] is None
