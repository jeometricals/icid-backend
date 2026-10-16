from decimal import Decimal
from uuid import UUID

from api.services.progress_rollup import compute_progress

# ---------------------------------------------------------------------------
# compute_progress: rows as list_flag_pay_items and list_flag_progress return them
# ---------------------------------------------------------------------------

SIDEWALK = UUID("10000000-0000-4000-8000-000000000001")
CONCRETE = UUID("10000000-0000-4000-8000-000000000002")
SIGN = UUID("10000000-0000-4000-8000-000000000003")
MAIN_ST = UUID("20000000-0000-4000-8000-000000000001")
ELM_ST = UUID("20000000-0000-4000-8000-000000000002")
OAK_ST = UUID("20000000-0000-4000-8000-000000000003")

PAY_ITEMS = [
    {"pay_item_id": SIDEWALK, "item_no": "4.13 AAS", "budget_code": "HW-CONS", "description": 'Sidewalk 4"',
     "unit": "SF", "unit_price": Decimal("11.28")},
    {"pay_item_id": CONCRETE, "item_no": "73.21AC", "budget_code": "HW-CONS", "description": "Concrete base",
     "unit": "CY", "unit_price": Decimal("141.16")},
    {"pay_item_id": SIGN, "item_no": "9.01", "budget_code": "HW-CONS", "description": "Sign", "unit": "EA",
     "unit_price": Decimal("50")},
]
ADDRESSES = {MAIN_ST: "350 Main St", ELM_ST: "12 Elm St", OAK_ST: "7 Oak St"}


def place(property_id: UUID) -> dict:
    """
    Build a sidewalk's kind and address fields, as a row of either read carries them.
    Takes the property's uuid.
    Returns the fields.
    """
    return {"property_kind": "sidewalk", "address": ADDRESSES[property_id], "intersection_street_1": None,
            "intersection_street_2": None, "quadrant": None, "facing_street": None}


def row(property_id: UUID, pay_item_id, state: str, flag_count: int, quantity: str) -> dict:
    """
    Build a row as list_flag_progress returns one.
    Takes the property, the pay item (None when unmapped), the state, the flag count and the summed quantity.
    Returns the row.
    """
    return {"property_id": property_id, **place(property_id), "pay_item_id": pay_item_id, "state": state,
            "flag_count": flag_count, "quantity": Decimal(quantity)}


def bundled(property_id: UUID) -> dict:
    """
    Build a task order's bundled property, as jsonb hands it back.
    Takes the property's uuid.
    Returns the dict.
    """
    fields = place(property_id)
    return {"id": str(property_id), "kind": fields.pop("property_kind"), **fields}


def line(result: dict, pay_item_id: UUID) -> dict:
    """
    Find a pay item's line in a rollup.
    Takes the rollup and the pay item's uuid.
    Returns the line.
    """
    return next(item for item in result["pay_items"] if item["pay_item_id"] == pay_item_id)


# The walkthrough: every flag on the task order approved
WALKTHROUGH = [row(MAIN_ST, SIDEWALK, "approved", 10, "383.44"), row(ELM_ST, CONCRETE, "approved", 5, "3.01")]


class TestStates:
    def test_the_walkthrough_is_all_approved(self):
        result = compute_progress(PAY_ITEMS, WALKTHROUGH)
        sidewalk, concrete = line(result, SIDEWALK), line(result, CONCRETE)
        assert sidewalk["approved"] == {"quantity": 383.44, "flag_count": 10, "value": 4325.20}
        assert sidewalk["in_flight"] == sidewalk["unclaimed"] == {"quantity": 0.0, "flag_count": 0, "value": 0.0}
        assert sidewalk["live_total"] == sidewalk["approved"]
        assert concrete["approved"] == {"quantity": 3.01, "flag_count": 5, "value": 424.89}
        assert result["overall"] == {"approved_value": 4750.09, "live_total_value": 4750.09, "approved_pct": 100.0}

    def test_each_state_goes_in_its_own_column_and_the_total_adds_them(self):
        rows = [row(MAIN_ST, SIDEWALK, "approved", 2, "100"), row(MAIN_ST, SIDEWALK, "in_flight", 3, "50.5"),
                row(ELM_ST, SIDEWALK, "unclaimed", 1, "25")]
        sidewalk = line(compute_progress(PAY_ITEMS, rows), SIDEWALK)
        assert sidewalk["approved"]["quantity"] == 100.0
        assert sidewalk["in_flight"] == {"quantity": 50.5, "flag_count": 3, "value": 569.64}
        assert sidewalk["unclaimed"]["flag_count"] == 1
        assert sidewalk["live_total"] == {"quantity": 175.5, "flag_count": 6, "value": 1979.64}
        assert sidewalk["approved_pct"] == 57.0

    def test_quantities_are_not_rounded(self):
        rows = [row(MAIN_ST, CONCRETE, "unclaimed", 1, "1.2345679")]
        assert line(compute_progress(PAY_ITEMS, rows), CONCRETE)["unclaimed"]["quantity"] == 1.2345679

    def test_only_pay_items_a_flag_is_on_are_listed(self):
        result = compute_progress(PAY_ITEMS, WALKTHROUGH)
        assert [item["item_no"] for item in result["pay_items"]] == ["4.13 AAS", "73.21AC"]

    def test_a_pay_item_with_only_unclaimed_flags_is_listed(self):
        result = compute_progress(PAY_ITEMS, [row(MAIN_ST, SIGN, "unclaimed", 2, "2")])
        assert [item["item_no"] for item in result["pay_items"]] == ["9.01"]

    def test_include_empty_lists_every_pay_item_at_zero_in_the_catalogs_order(self):
        result = compute_progress(PAY_ITEMS, WALKTHROUGH, include_empty=True)
        assert [item["item_no"] for item in result["pay_items"]] == ["4.13 AAS", "73.21AC", "9.01"]
        sign = line(result, SIGN)
        assert sign["live_total"] == {"quantity": 0.0, "flag_count": 0, "value": 0.0}
        assert sign["approved_pct"] is None

    def test_include_empty_leaves_the_overall_as_it_is(self):
        assert (compute_progress(PAY_ITEMS, WALKTHROUGH, include_empty=True)["overall"]
                == compute_progress(PAY_ITEMS, WALKTHROUGH)["overall"])

    def test_unmapped_flags_are_counted_and_in_no_column(self):
        rows = WALKTHROUGH + [row(MAIN_ST, None, "unmapped", 2, "0"), row(OAK_ST, None, "unmapped", 1, "0")]
        result = compute_progress(PAY_ITEMS, rows)
        assert result["unmapped_flag_count"] == 3
        assert result["unmapped_by_property"] == [
            {"property_id": MAIN_ST, "display_address": "350 Main St", "flag_count": 2},
            {"property_id": OAK_ST, "display_address": "7 Oak St", "flag_count": 1},
        ]
        assert line(result, SIDEWALK)["live_total"]["flag_count"] == 10


class TestOverall:
    def test_the_overall_percent_is_weighted_by_value(self):
        # 100 SF approved of 200 (1,128.00 of 2,256.00); 1 CY of 4 approved (141.16 of 564.64)
        rows = [row(MAIN_ST, SIDEWALK, "approved", 1, "100"), row(MAIN_ST, SIDEWALK, "unclaimed", 1, "100"),
                row(MAIN_ST, CONCRETE, "approved", 1, "1"), row(MAIN_ST, CONCRETE, "in_flight", 1, "3")]
        overall = compute_progress(PAY_ITEMS, rows)["overall"]
        assert overall["approved_value"] == 1269.16
        assert overall["live_total_value"] == 2820.64
        assert overall["approved_pct"] == 45.0  # 1269.16 / 2820.64 = 44.995...

    def test_nothing_surveyed_has_no_percent(self):
        overall = compute_progress(PAY_ITEMS, [])["overall"]
        assert overall == {"approved_value": 0.0, "live_total_value": 0.0, "approved_pct": None}

    def test_a_project_with_no_pay_items_a_flag_can_fill(self):
        result = compute_progress([], [row(MAIN_ST, None, "unmapped", 1, "0")], include_empty=True)
        assert result["pay_items"] == []
        assert result["unmapped_flag_count"] == 1


class TestTaskOrder:
    def test_each_bundled_property_gets_its_own_rollup_in_the_bundles_order(self):
        rows = WALKTHROUGH + [row(OAK_ST, SIDEWALK, "unclaimed", 4, "40")]
        result = compute_progress(PAY_ITEMS, rows, [bundled(ELM_ST), bundled(MAIN_ST), bundled(OAK_ST)])
        assert [entry["display_address"] for entry in result["properties"]] == ["12 Elm St", "350 Main St", "7 Oak St"]
        main = result["properties"][1]
        assert line(main, SIDEWALK)["approved"]["quantity"] == 383.44
        assert [item["item_no"] for item in main["pay_items"]] == ["4.13 AAS"]
        assert result["properties"][2]["overall"]["approved_pct"] == 0.0

    def test_include_empty_lists_every_pay_item_for_each_property(self):
        result = compute_progress(PAY_ITEMS, WALKTHROUGH, [bundled(MAIN_ST), bundled(OAK_ST)], include_empty=True)
        assert [len(entry["pay_items"]) for entry in result["properties"]] == [3, 3]
        assert line(result["properties"][0], CONCRETE)["live_total"]["flag_count"] == 0

    def test_a_property_with_no_flags_lists_no_pay_items(self):
        result = compute_progress(PAY_ITEMS, WALKTHROUGH, [bundled(OAK_ST)])
        assert result["properties"][0]["pay_items"] == []

    def test_the_properties_add_up_to_the_task_order(self):
        rows = WALKTHROUGH + [row(OAK_ST, SIDEWALK, "unclaimed", 4, "40")]
        result = compute_progress(PAY_ITEMS, rows, [bundled(MAIN_ST), bundled(ELM_ST), bundled(OAK_ST)])
        total = sum(item["live_total"]["quantity"] for entry in result["properties"] for item in entry["pay_items"]
                    if item["pay_item_id"] == SIDEWALK)
        assert total == line(result, SIDEWALK)["live_total"]["quantity"]

    def test_sites_started_counts_properties_with_an_approved_flag(self):
        rows = WALKTHROUGH + [row(OAK_ST, SIDEWALK, "in_flight", 4, "40")]
        properties = [bundled(MAIN_ST), bundled(ELM_ST), bundled(OAK_ST),
                      {**bundled(OAK_ST), "id": "20000000-0000-4000-8000-000000000009"}]
        result = compute_progress(PAY_ITEMS, rows, properties)
        assert (result["site_count"], result["sites_started"]) == (4, 2)

    def test_a_property_with_only_unmapped_flags_counts_them(self):
        result = compute_progress(PAY_ITEMS, [row(OAK_ST, None, "unmapped", 3, "0")], [bundled(OAK_ST)])
        assert result["properties"][0]["unmapped_flag_count"] == 3
        assert result["properties"][0]["overall"]["approved_pct"] is None
        assert result["sites_started"] == 0

    def test_a_project_rollup_has_no_properties(self):
        assert "properties" not in compute_progress(PAY_ITEMS, WALKTHROUGH)
