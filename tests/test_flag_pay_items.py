from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.services.flag_pay_items import default_pay_item_id, default_pay_items, sole_pay_item

# ---------------------------------------------------------------------------
# api/services/flag_pay_items.py (pilot sweep #2): the pay item a non-assessable flag takes by default
# ---------------------------------------------------------------------------

CONTRACT = "api.queries.contract_items.run_query"
AAS, BAS, OTHER = (UUID(f"c1000000-0000-4000-8000-00000000000{n}") for n in (1, 2, 3))


def item(pay_item_id: UUID, ref: str, budget_code: str = "HW-200(S)", unit: str = "SF") -> dict:
    """A pay item as list_project_pay_items returns it."""
    return {"pay_item_uuid": pay_item_id, "pay_item_ref": ref, "description": "Sidewalk", "unit": unit,
            "quantity": Decimal("100"), "unit_price": Decimal("14.50"), "budget_code": budget_code, "spec_section": None}


HWS0023 = [item(AAS, "4.13 AAS"), item(BAS, "4.13 BAS"), item(OTHER, "4.08 AA", unit="LF")]


class TestSolePayItem:
    def test_the_one_item_under_the_number_whatever_its_budget_code(self):
        assert sole_pay_item(HWS0023, "4.13 AAS") == AAS
        assert sole_pay_item([item(AAS, "4.13 AAS", "SE0200Q-CONS")], "4.13 AAS") == AAS

    @pytest.mark.parametrize("rows", [
        [],
        [item(AAS, "4.13 AAS"), item(OTHER, "4.13 AAS", "SE0002Q-CONS")],  # SE384's several budget codes
        [item(AAS, "4.13 AAS", unit="LF")],  # one a flag can't be measured in
    ])
    def test_none_several_or_one_a_flag_cant_measure_is_none(self, rows):
        assert sole_pay_item(rows, "4.13 AAS") is None


class TestDefaults:
    def test_each_thickness_takes_its_item_and_7in_as_4in_the_4in_one(self):
        assert default_pay_items(HWS0023) == {"4in": AAS, "7in": BAS, "7in_as_4in": AAS}

    def test_a_thickness_without_one_item_is_left_out(self):
        assert default_pay_items([item(AAS, "4.13 AAS"), item(OTHER, "4.13 AAS", "X")]) == {}

    def test_one_flags_lookup_reads_the_number_alone(self):
        with patch(CONTRACT, return_value=[item(BAS, "4.13 BAS")]) as run:
            assert default_pay_item_id("HWS0023", "7in") == BAS
        assert run.call_args.args[1] == ("HWS0023", "4.13 BAS")

    def test_a_thickness_it_doesnt_know_or_a_failed_read_is_none(self):
        with patch(CONTRACT) as run:
            assert default_pay_item_id("HWS0023", None) is None
        run.assert_not_called()
        with patch(CONTRACT, return_value=None):
            assert default_pay_item_id("HWS0023", "4in") is None
