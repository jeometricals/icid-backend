import math
from decimal import Decimal
from uuid import UUID

import pytest

from api.services.construction_measure import compute_measure, flag_areas
from api.services.construction_sketch import (
    MIN_POINTS_PER_FOOT,
    MULTI_SHEET_MESSAGE,
    NO_SCALE_MESSAGE,
    SketchError,
    SketchItem,
    fit_to_pane,
    north_degrees,
    placed_outline,
    shape_outline,
    sketch_items,
)

# ---------------------------------------------------------------------------
# The Construction Measure: per flag, then per thickness at the DOT rates
# ---------------------------------------------------------------------------

RATES = {"cost_per_sf_4in": Decimal("11.54"), "cost_per_sf_7in": Decimal("12.64")}


def flag(thickness: str, gross: str, quantity: str, assessable: bool = True) -> dict:
    """
    Build a flag as list_report_flags gives it, for the measure.
    Takes its thickness, its gross area, its quantity (override or net) and whether it was surveyed assessable.
    Returns the dict.
    """
    return {"surveyed_thickness": thickness, "computed_quantity": Decimal(gross), "quantity": Decimal(quantity),
            "surveyed_is_assessable": assessable}


def row(measure, thickness: str):
    """
    Find a thickness's row in a Measure.
    Takes the Measure and the thickness.
    Returns the row.
    """
    return next(r for r in measure.rows if r.thickness == thickness)


class TestFlagAreas:
    def test_an_assessable_flag_is_assessed_at_its_net_quantity(self):
        assert flag_areas(flag("4in", "25", "20")) == (Decimal(25), Decimal(20), Decimal(5))

    def test_a_flag_surveyed_non_assessable_is_all_non_assessable(self):
        assert flag_areas(flag("4in", "90.25", "90.25", assessable=False)) == (Decimal("90.25"), 0, Decimal("90.25"))

    def test_an_override_above_the_gross_area_never_makes_non_assessable_negative(self):
        assert flag_areas(flag("4in", "25", "30")) == (Decimal(25), Decimal(30), Decimal(0))


class TestComputeMeasure:
    def test_every_row_is_listed_in_the_forms_order(self):
        measure = compute_measure([], RATES)
        assert [r.thickness for r in measure.rows] == ["4in", "7in_as_4in", "7in"]
        assert measure.estimated_sf == 0 and measure.estimated_cost == 0

    def test_the_138_22_227_street_walkthrough(self):
        # 4": the non-assessable flag (90.25) and one assessable flag, 160.69 gross of which 113.19 net; 7": 171.81 / 94.14
        flags = [flag("4in", "90.25", "90.25", assessable=False), flag("4in", "160.69", "113.19"),
                 flag("7in", "171.81", "94.14")]
        measure = compute_measure(flags, RATES)
        four = row(measure, "4in")
        assert (four.total_sf, four.non_assessable_sf, four.assessable_sf) == (
            Decimal("250.94"), Decimal("137.75"), Decimal("113.19"))
        assert four.cost == Decimal("113.19") * Decimal("11.54")
        assert row(measure, "7in").cost == Decimal("94.14") * Decimal("12.64")
        assert measure.estimated_sf == Decimal("422.75")
        assert round(measure.estimated_cost, 2) == Decimal("2496.14")

    def test_7in_assessed_as_4in_is_billed_at_the_4in_rate(self):
        measure = compute_measure([flag("7in_as_4in", "50", "50")], RATES)
        as_four = row(measure, "7in_as_4in")
        assert as_four.rate == Decimal("11.54") and as_four.cost == Decimal("577.00")
        assert row(measure, "7in").assessable_sf == 0

    def test_decimal_precision_is_kept_until_display(self):
        measure = compute_measure([flag("4in", "1.005", "1.005")] * 3, RATES)
        assert row(measure, "4in").assessable_sf == Decimal("3.015")
        assert row(measure, "4in").cost == Decimal("3.015") * Decimal("11.54")

    def test_without_rates_there_is_no_cost(self):
        measure = compute_measure([flag("4in", "10", "10")], {"cost_per_sf_4in": None, "cost_per_sf_7in": None})
        assert row(measure, "4in").cost is None and measure.estimated_cost is None
        assert measure.estimated_sf == 10


# ---------------------------------------------------------------------------
# The sketch: outlines as lib/flagGeometry.js draws them, and the fit
# ---------------------------------------------------------------------------

PAGE = UUID("50000000-0000-4000-8000-000000000001")
OTHER_PAGE = UUID("50000000-0000-4000-8000-000000000002")


def sketch_flag(x=100.0, y=100.0, L=5, W=5, rotation=0, page=PAGE, scale=10, page_rotation=0, areas=()) -> dict:
    """
    Build a rectangle flag with what the sketch reads.
    Takes its place in page pixels, its size in feet, its rotation, its page, the page's scale and rotation, and its areas.
    Returns the dict.
    """
    return {"shape": "rectangle", "dimensions": {"L": L, "W": W}, "sketch_x": x, "sketch_y": y, "rotation": rotation,
            "pir_page_id": page, "scale_px_per_ft": scale, "rotation_deg": page_rotation, "areas": list(areas)}


NO_DRAWINGS = {"features": [], "segments": []}


class TestShapes:
    def test_a_rectangle_is_L_wide_and_W_tall(self):
        assert shape_outline("rectangle", {"L": 5, "W": 3}) == [(0, 0), (5, 0), (5, 3), (0, 3)]

    def test_a_trapezoids_parallel_sides_are_centred(self):
        assert shape_outline("trapezoid", {"a": 4, "b": 6, "w": 2}) == [(1, 0), (5, 0), (6, 2), (0, 2)]

    def test_a_triangles_right_angle_is_at_the_bottom_left(self):
        assert shape_outline("triangle", {"a": 3, "b": 4}) == [(0, 0), (3, 4), (0, 4)]

    def test_an_arc_has_its_chord_at_the_bottom(self):
        points = shape_outline("arc", {"r": 2, "theta": math.pi})
        assert len(points) == 33
        assert points[0] == pytest.approx((-2, 0), abs=1e-9) and points[-1] == pytest.approx((2, 0), abs=1e-9)
        assert points[16] == pytest.approx((0, -2), abs=1e-9)

    def test_an_unknown_shape_or_no_dimensions_has_no_outline(self):
        assert shape_outline("hexagon", {"r": 1}) == [] and shape_outline("rectangle", None) == []

    def test_a_flag_is_centred_on_its_place_and_scaled(self):
        points = placed_outline(sketch_flag(L=4, W=2, scale=10), 10)
        assert points == [(80, 90), (120, 90), (120, 110), (80, 110)]

    def test_a_rotation_turns_it_clockwise_about_its_place(self):
        points = placed_outline(sketch_flag(L=4, W=2, rotation=90), 10)
        assert points[0] == pytest.approx((110, 80)) and points[2] == pytest.approx((90, 120))


class TestSketchItems:
    def test_flags_and_their_areas_are_laid_out_in_feet(self):
        area = {"shape": "rectangle", "dimensions": {"L": 1, "W": 1}, "sketch_x": 100, "sketch_y": 100, "rotation": 0}
        items = sketch_items([sketch_flag(areas=[area])], NO_DRAWINGS)
        assert [item.kind for item in items] == ["flag", "area"]
        assert items[0].points[0] == pytest.approx((7.5, 7.5))

    def test_the_page_rotation_turns_the_whole_sketch(self):
        items = sketch_items([sketch_flag(x=100, y=0, L=2, W=2, page_rotation=90)], NO_DRAWINGS)
        # (90, -10) px is (9, -1) ft, which a quarter turn clockwise puts at (1, 9)
        assert items[0].points[0] == pytest.approx((1, 9))

    def test_flags_on_two_pages_are_for_multi_sheet(self):
        with pytest.raises(SketchError, match="R1h.1"):
            sketch_items([sketch_flag(), sketch_flag(page=OTHER_PAGE)], NO_DRAWINGS)

    def test_a_page_without_a_scale_is_refused(self):
        with pytest.raises(SketchError) as raised:
            sketch_items([sketch_flag(scale=None)], NO_DRAWINGS)
        assert str(raised.value) == NO_SCALE_MESSAGE

    def test_features_and_segments_join_the_flags(self):
        drawings = {"features": [{"feature_type": "hydrant", "sketch_x": 150, "sketch_y": 100, "rotation": 0}],
                    "segments": [{"segment_type": "curb_concrete", "points": [{"x": 0, "y": 200}, {"x": 300, "y": 200}]}]}
        items = sketch_items([sketch_flag()], drawings)
        assert [item.kind for item in items] == ["flag", "feature", "segment"]
        assert items[2].points == [(0, 20), (30, 20)]


class TestFit:
    PANE = (36.0, 98.0, 720.0, 288.0)

    def test_a_sketch_is_fitted_to_the_pane_keeping_its_proportions(self):
        items = [SketchItem("flag", [(0, 0), (50, 0), (50, 10), (0, 10)], {})]
        fit = fit_to_pane(items, self.PANE)
        assert fit.points_per_foot == pytest.approx(720 / 60)  # 50 ft wide, 10% padding each side
        # centred: the drawing's middle is the pane's
        assert fit.place((25, 5)) == pytest.approx((36 + 360, 98 + 144))

    def test_a_sketch_too_big_to_read_is_for_multi_sheet(self):
        wide = 720 / MIN_POINTS_PER_FOOT  # 180 ft, before padding
        items = [SketchItem("flag", [(0, 0), (wide, 0), (wide, 1), (0, 1)], {})]
        with pytest.raises(SketchError) as raised:
            fit_to_pane(items, self.PANE)
        assert str(raised.value) == MULTI_SHEET_MESSAGE

    def test_y_runs_up_on_the_sheet(self):
        items = [SketchItem("flag", [(0, 0), (10, 0), (10, 10), (0, 10)], {})]
        fit = fit_to_pane(items, self.PANE)
        assert fit.place((0, 0))[1] > fit.place((0, 10))[1]


class TestNorth:
    @pytest.mark.parametrize("bearing, expected", [("N", 0), ("NE", 45), ("E", 90), ("S", 180), ("W", 270),
                                                   ("NW", 315)])
    def test_the_arrow_follows_the_bearing(self, bearing, expected):
        assert north_degrees(bearing) == expected

    def test_no_bearing_no_arrow(self):
        assert north_degrees(None) is None
