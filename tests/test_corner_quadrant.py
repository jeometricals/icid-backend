import math
from decimal import Decimal
from uuid import UUID

import pytest

from api.services.construction_measure import calc_groups, compute_measure, flag_terms
from api.services.construction_sketch import number_items, shape_outline, sketch_items
from api.services.corner_quadrant import (
    COEF_TABLE, CORRECTION_TOO_LARGE_MESSAGE, RATIO_RANGE_MESSAGE, corner_quadrant, corner_quadrant_outline,
    corner_quadrant_terms, hc_ratio, truncated,
)
from api.services.flag_area import compute_area
from api.services.measurement_bundles import join_target

# BLK_13136_LOT_133 sheet 2 (7" sidewalk assessed as 4"): the OE sheet's worked example
OE = {"H": 5.66, "C": 18.0, "R1": 12.58, "R2": 12.0, "A": 4.83, "B": 3.83}
RATES = {"cost_per_sf_4in": Decimal("11.54"), "cost_per_sf_7in": Decimal("12.64")}
NO_DRAWINGS = {"features": [], "segments": []}


def segment_coef(ratio: float) -> float:
    """
    The exact circular-segment coefficient for a height over chord: the segment's area over H x C.
    Takes H/C (above 0, at most 0.5).
    Returns the coefficient.
    """
    height, chord = ratio, 1.0
    radius = (chord * chord / 4 + height * height) / (2 * height)
    half = math.asin(min(1.0, chord / (2 * radius)))
    return radius * radius * (2 * half - math.sin(2 * half)) / 2 / (height * chord)


class TestTheFormula:
    def test_the_oe_sheets_worked_example_is_129_87(self):
        worked = corner_quadrant(OE)
        assert (worked.ratio, worked.coef) == (Decimal("0.31"), Decimal("0.7154"))
        assert worked.arc == Decimal("72.884952") and worked.triangle == Decimal("75.48")
        assert worked.correction == Decimal("18.4989") and worked.area == Decimal("129.87")
        assert compute_area("corner_quadrant", OE) == Decimal("129.87")

    def test_the_rows_print_cut_to_the_cent_as_the_oe_sheet_does(self):
        worked = corner_quadrant(OE)
        assert [truncated(part) for part in (worked.arc, worked.triangle, worked.correction)] == [
            "72.88", "75.48", "18.49"]

    @pytest.mark.parametrize("height, chord, ratio, coef", [
        (0.18, 18, "0.01", "0.6667"), (9, 18, "0.50", "0.7854"), (5.66, 18, "0.31", "0.7154")])
    def test_the_tables_ends_are_reached(self, height, chord, ratio, coef):
        worked = corner_quadrant({**OE, "H": height, "C": chord})
        assert (worked.ratio, worked.coef) == (Decimal(ratio), Decimal(coef))

    @pytest.mark.parametrize("height, chord", [(0.001, 100), (0.01, 100), (10, 18)], ids=["0.00", "0.0001", "0.56"])
    def test_a_ratio_off_the_table_is_refused(self, height, chord):
        with pytest.raises(ValueError, match=RATIO_RANGE_MESSAGE.replace("(", r"\(").replace(")", r"\)")):
            corner_quadrant({**OE, "H": height, "C": chord})

    def test_h_over_c_rounds_halves_up_without_interpolating(self):
        assert hc_ratio(Decimal("2.25"), Decimal("18")) == Decimal("0.13")  # 0.125
        assert hc_ratio(Decimal("5.66"), Decimal("18.00")) == Decimal("0.31")  # 0.3144...

    def test_no_correction_is_the_arc_and_the_triangle(self):
        worked = corner_quadrant({**OE, "A": 0, "B": 0})
        assert worked.correction == 0 and worked.area == Decimal("148.36")  # 72.884952 + 75.48

    def test_r1_and_r2_go_either_way(self):
        assert corner_quadrant({**OE, "R1": 12.0, "R2": 12.58}).area == Decimal("129.87")

    def test_a_correction_past_the_arc_and_triangle_is_refused(self):
        with pytest.raises(ValueError, match="Correction rectangle exceeds arc"):
            corner_quadrant({**OE, "A": 20, "B": 10})
        assert CORRECTION_TOO_LARGE_MESSAGE.startswith("Correction rectangle exceeds arc + triangle")


class TestTheTable:
    def test_it_has_fifty_entries_from_0_01_to_0_50_rising(self):
        assert list(COEF_TABLE) == [Decimal(f"0.{n:02d}") for n in range(1, 51)]
        values = list(COEF_TABLE.values())
        assert all(a < b for a, b in zip(values, values[1:]))

    def test_every_entry_is_the_exact_segment_to_four_places(self):
        # 0.24's exact coefficient is 0.69645 to five places, so the OE's 0.6965 is its rounding either way
        for ratio, coef in COEF_TABLE.items():
            assert abs(segment_coef(float(ratio)) - float(coef)) <= 0.00006, ratio
        assert COEF_TABLE[Decimal("0.01")] == Decimal("0.6667") and COEF_TABLE[Decimal("0.50")] == Decimal("0.7854")


class TestTheOutline:
    def test_the_right_angle_is_at_the_bottom_left_with_the_notch_cut_from_it(self):
        points = corner_quadrant_outline(OE)
        assert points[:5] == [(0.0, 0.0), (0.0, 12.0 - 3.83), (4.83, 12.0 - 3.83), (4.83, 12.0), (12.58, 12.0)]
        assert shape_outline("corner_quadrant", OE) == points

    def test_without_a_correction_there_is_no_notch(self):
        points = corner_quadrant_outline({**OE, "A": 0, "B": 0})
        assert points[:3] == [(0.0, 0.0), (0.0, 12.0), (12.58, 12.0)]

    def test_the_arc_bulges_h_out_of_the_hypotenuse_away_from_the_corner(self):
        arc = corner_quadrant_outline(OE)[5:]
        chord = math.hypot(12.58, 12.0)

        def side(x: float, y: float) -> float:
            """Signed distance from the hypotenuse (0, 0)-(12.58, 12): negative on the right angle's side."""
            return (12.0 * x - 12.58 * y) / chord

        assert side(0.0, 12.0) < 0
        assert len(arc) == 31 and all(side(x, y) > 0 for x, y in arc)
        assert max(side(x, y) for x, y in arc) == pytest.approx(5.66, abs=0.01)


class TestTheConstructionReport:
    def test_its_working_is_coef_h_c_plus_half_r1_r2_less_a_b(self):
        assert corner_quadrant_terms(OE) == "0.7154×5.66×18.00 + ½×12.58×12.00 - 4.83×3.83"
        assert corner_quadrant_terms({**OE, "A": 0, "B": 0}).endswith("12.00")
        assert flag_terms("rectangle", {"L": 5, "W": 5}) == ""

    def test_its_row_carries_the_working_and_the_area(self):
        flags = [{"id": UUID("f0000000-0000-4000-8000-000000000001"), "surveyed_thickness": "7in_as_4in",
                  "surveyed_is_assessable": True, "surveyed_non_assessable_reason": None, "shape": "corner_quadrant",
                  "dimensions": OE, "sketch_x": 10, "sketch_y": 10, "rotation": 0,
                  "pir_page_id": UUID("e2000000-0000-4000-8000-000000000001"), "scale_px_per_ft": 1,
                  "rotation_deg": 0, "computed_quantity": Decimal("129.87"), "quantity": Decimal("129.87"),
                  "areas": []}]
        labels = number_items(flags, sketch_items(flags, NO_DRAWINGS))
        groups = calc_groups(flags, compute_measure(flags, RATES), labels)
        (row,) = groups[0].rows
        assert (row.label, row.length, row.width, row.sub_total) == ("1", "", "", Decimal("129.87"))
        assert row.terms == corner_quadrant_terms(OE)
        assert groups[0].assessable == Decimal("129.87") and groups[0].adjustment == Decimal("0")


class TestNeverBundled:
    def test_a_corner_quadrant_joins_no_bundle(self):
        me = {"id": UUID("f3000000-0000-4000-8000-000000000001"), "shape": "corner_quadrant", "dimensions": OE,
              "sketch_x": 400, "sketch_y": 300, "rotation": 0, "surveyed_thickness": "4in",
              "surveyed_is_assessable": True, "pay_item_id": None}
        bundle = {"id": UUID("f3000000-0000-4000-8000-000000000002"), "shape": "rectangle", "dimensions": {"L": 5, "W": 5},
                  "sketch_x": 480, "sketch_y": 300, "rotation": 0, "surveyed_thickness": "4in",
                  "surveyed_is_assessable": True, "pay_item_id": None, "bundled": True, "frozen": False,
                  "measurement_id": UUID("b1000000-0000-4000-8000-000000000001"), "scale_px_per_ft": 16}
        neighbours = [{**me, "bundled": False, "frozen": False, "scale_px_per_ft": 16,
                       "measurement_id": UUID("a1000000-0000-4000-8000-000000000001")}, bundle]
        assert join_target(me, neighbours) is None
