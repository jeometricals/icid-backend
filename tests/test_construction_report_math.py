import math
from decimal import Decimal
from uuid import UUID

import pytest

from api.services.construction_measure import calc_groups, compute_measure, flag_areas, shape_terms
from api.services.construction_report import (
    CALC_WIDTH,
    CALC_WIDTH_TWO,
    calc_lines,
    label_fits,
    SIGNATURE_MAX_WIDTH,
    material_label,
    property_class,
    reason_text,
    report_layout,
    signature_box,
)
from api.services.construction_sketch import (
    MIN_POINTS_PER_FOOT,
    MULTI_SHEET_MESSAGE,
    NO_SCALE_MESSAGE,
    SketchError,
    SketchItem,
    fit_to_pane,
    material,
    north_degrees,
    number_items,
    placed_outline,
    reading_order,
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
        assert measure.estimated_sf == Decimal("207.33")  # the assessable total, as DOT's sheets print it
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

    def test_the_estimate_counts_only_what_is_assessed(self):
        measure = compute_measure([flag("4in", "90.25", "90.25", assessable=False), flag("7in", "60", "40")], RATES)
        assert measure.estimated_sf == Decimal("40")


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
        assert fit.points_per_foot == pytest.approx(720 / 54)  # 50 ft wide, 4% padding each side
        # centred: the drawing's middle is the pane's
        assert fit.place((25, 5)) == pytest.approx((36 + 360, 98 + 144))

    def test_a_sketch_too_big_to_read_is_for_multi_sheet(self):
        wide = 720 / MIN_POINTS_PER_FOOT  # 180 ft, before padding
        items = [SketchItem("flag", [(0, 0), (wide, 0), (wide, 1), (0, 1)], {})]
        with pytest.raises(SketchError) as raised:
            fit_to_pane(items, self.PANE)
        assert str(raised.value) == MULTI_SHEET_MESSAGE

    def test_a_dimension_wider_than_the_drawing_gets_room(self):
        items = [SketchItem("flag", [(0, 0), (10, 0), (10, 5), (0, 5)], {})]
        narrow, wide = fit_to_pane(items, self.PANE), fit_to_pane(items, self.PANE, min_width_ft=40)
        assert wide.points_per_foot < narrow.points_per_foot
        # The drawing stays centred in the pane
        middle = (wide.place((0, 0))[0] + wide.place((10, 0))[0]) / 2
        assert middle == pytest.approx(self.PANE[0] + self.PANE[2] / 2)

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


# ---------------------------------------------------------------------------
# R1h.3: numbering, materials and the calculations panel
# ---------------------------------------------------------------------------

def piece(n: int, thickness: str = "4in", assessable: bool = True, x: float = 0, y: float = 0, L: float = 5,
          W: float = 5, quantity=None, areas=(), shape: str = "rectangle", dimensions=None) -> dict:
    """
    Build a flag with what the numbering and the calculations read: an id, its place (feet, y down; drawn at 1 px/ft), shape, thickness and areas.
    Takes its index, thickness, whether surveyed assessable, its top-left corner, its size, its quantity (default: its area), its areas, and a shape and dimensions other than an L x W rectangle.
    Returns the dict.
    """
    gross = Decimal(str(L * W)) if dimensions is None else Decimal("73.67")
    return {"id": UUID(f"f0000000-0000-4000-8000-0000000000{n:02d}"), "surveyed_thickness": thickness,
            "surveyed_is_assessable": assessable, "surveyed_non_assessable_reason": None if assessable else "tree",
            "shape": shape, "dimensions": dimensions or {"L": L, "W": W}, "sketch_x": x + L / 2,
            "sketch_y": y + W / 2, "rotation": 0, "pir_page_id": PAGE, "scale_px_per_ft": 1, "rotation_deg": 0,
            "computed_quantity": gross, "quantity": gross if quantity is None else Decimal(quantity),
            "areas": list(areas)}


def area(n: int, x: float = 0, y: float = 0, L: float = 2, W: float = 2, reason: str = "grass") -> dict:
    """
    Build a non-assessable area as list_report_flags nests it.
    Takes its index, top-left corner, size and reason.
    Returns the dict.
    """
    return {"id": f"a0000000-0000-4000-8000-0000000000{n:02d}", "display_order": n, "reason": reason,
            "shape": "rectangle", "dimensions": {"L": L, "W": W}, "sketch_x": x + L / 2, "sketch_y": y + W / 2,
            "rotation": 0, "computed_quantity": L * W}


class TestReadingOrder:
    def test_rows_that_arent_level_read_as_rows_left_to_right(self):
        # Two hand-drawn rows: the top row's shapes sit a little higher or lower than each other
        boxes = {"a": (10, 0.5, 15, 5.5), "b": (0, 0, 5, 5), "c": (20, -0.4, 25, 4.6), "d": (0, 5.2, 5, 10),
                 "e": (10, 5.0, 15, 9.8)}
        assert reading_order(boxes) == ["b", "a", "c", "d", "e"]

    def test_one_shape_below_another_comes_after_it(self):
        assert reading_order({"low": (0, 10, 5, 15), "high": (8, 0, 13, 5)}) == ["high", "low"]


class TestNumbering:
    def test_flags_are_numbered_and_what_isnt_assessed_gets_A_numbers(self):
        flags = [piece(1, x=20), piece(2, x=0, assessable=False), piece(3, x=10, areas=[area(1, x=11, y=1)]),
                 piece(4, x=0, y=10)]
        labels = number_items(flags, sketch_items(flags, NO_DRAWINGS))
        ids = [str(flag["id"]) for flag in flags]
        assert [labels[f"flag:{i}"] for i in ids] == ["3", "1", "2", "4"]
        # The non-assessable flag (left) reads before the area (middle)
        assert labels[f"a:flag:{ids[1]}"] == "A1"
        assert labels[f"a:area:{flags[2]['areas'][0]['id']}"] == "A2"
        assert f"a:flag:{ids[0]}" not in labels

    def test_an_area_on_a_non_assessable_flag_gets_no_A_number(self):
        flags = [piece(1, assessable=False, areas=[area(1, x=1, y=1)])]
        labels = number_items(flags, sketch_items(flags, NO_DRAWINGS))
        assert labels == {f"flag:{flags[0]['id']}": "1", f"a:flag:{flags[0]['id']}": "A1"}

    def test_a_flag_not_drawn_comes_last(self):
        flags = [{**piece(1), "sketch_x": None}, piece(2, x=50)]
        labels = number_items(flags, sketch_items(flags, NO_DRAWINGS))
        assert labels[f"flag:{flags[0]['id']}"] == "2" and labels[f"flag:{flags[1]['id']}"] == "1"


class TestMaterials:
    @pytest.mark.parametrize("thickness, assessable, expected, label", [
        ("4in", True, "4in", '4" Conc.'), ("7in", True, "7in", '7" Conc.'), ("7in_as_4in", True, "7in", '7" Conc.'),
        ("4in", False, "non_assessable", "Tree"), ("7in", False, "non_assessable", "Tree"),
    ])
    def test_a_flag_is_filled_and_labelled_by_what_it_is(self, thickness, assessable, expected, label):
        item = SketchItem("flag", [], piece(1, thickness=thickness, assessable=assessable))
        assert material(item) == expected and material_label(item) == label

    @pytest.mark.parametrize("reason, label", [("grass", "Grass"), ("dirt", "Dirt"), ("tree_pit", "Tree Pit"),
                                               ("other", "Non-Ass."), (None, "Non-Ass."),
                                               ("other_perm_obstruction", "Non-Ass.")])
    def test_an_area_is_non_assessable_and_labelled_by_its_reason(self, reason, label):
        item = SketchItem("area", [], area(1, reason=reason))
        assert material(item) == "non_assessable" and material_label(item) == label and reason_text(reason) == label

    def test_a_driveway_is_its_own_material_labelled_DW_with_its_width(self):
        driveway = SketchItem("area", [(0, 0), (17.16, 0), (17.16, 4.41), (0, 4.41)], area(1, reason="driveway"))
        assert material(driveway) == "driveway" and material_label(driveway) == "17.16' DW"
        flag = SketchItem("flag", [(0, 0), (5, 0), (5, 5), (0, 5)],
                          {**piece(1, assessable=False), "surveyed_non_assessable_reason": "driveway"})
        assert material(flag) == "driveway" and material_label(flag) == "5.00' DW"

    def test_a_label_prints_only_where_it_fits(self):
        assert label_fits('4" Conc.', [(0, 0), (80, 0), (80, 30), (0, 30)])
        assert not label_fits('4" Conc.', [(0, 0), (20, 0), (20, 30), (0, 30)])  # too narrow
        assert not label_fits('4" Conc.', [(0, 0), (80, 0), (80, 12), (0, 12)])  # too short
        assert not label_fits("", [(0, 0), (80, 0), (80, 30), (0, 30)])


class TestCalculations:
    @pytest.mark.parametrize("shape, dimensions, expected", [
        ("rectangle", {"L": 33.83, "W": 4.75}, ("33.83", "4.75")),
        ("rectangle", {"L": 19, "W": 4.75}, ("19.00", "4.75")),
        ("trapezoid", {"a": 16.25, "b": 17.16, "w": 4.41}, ("0.5(16.25+17.16)", "4.41")),
        ("triangle", {"a": 3, "b": 4}, ("0.5(3.00)", "4.00")),
        ("polygon", {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}]}, ("", "")),
        ("arc", {"r": 2, "theta": 1}, ("", "")),
        ("rectangle", None, ("", "")),
    ])
    def test_length_and_width_come_from_the_stored_dimensions(self, shape, dimensions, expected):
        assert shape_terms(shape, dimensions) == expected

    def test_each_thickness_lists_its_flags_its_pieces_and_the_measures_figures(self):
        flags = [piece(1, x=0, L=10, W=5, areas=[area(1, x=1, y=1, L=2, W=2)], quantity="46"),
                 piece(2, x=12, assessable=False), piece(3, "7in", x=20, L=4, W=5)]
        labels = number_items(flags, sketch_items(flags, NO_DRAWINGS))
        measure = compute_measure(flags, RATES)
        groups = calc_groups(flags, measure, labels)
        assert [group.label for group in groups] == ['4" Sidewalk Sq.Ft', '7" Sidewalk Sq.Ft']  # 7" as 4" has none
        four = groups[0]
        assert [(row.label, row.length, row.width, row.sub_total) for row in four.rows] == [
            ("1", "10.00", "5.00", Decimal("50")), ("2", "5.00", "5.00", Decimal("25"))]
        assert [(row.label, row.sub_total) for row in four.non_assessable_rows] == [("A1", Decimal("4")),
                                                                                   ("A2", Decimal("25"))]
        assert (four.total, four.non_assessable, four.assessable, four.adjustment) == (
            Decimal("75"), Decimal("29"), Decimal("46"), Decimal("0"))
        assert groups[1].non_assessable_rows == [] and groups[1].assessable == Decimal("20")

    def test_an_override_shows_as_an_adjustment_so_the_panel_agrees_with_box_2(self):
        flags = [piece(1, L=10, W=5, quantity="40")]  # 50 SF, nothing marked, 40 assessed
        groups = calc_groups(flags, compute_measure(flags, RATES), number_items(flags, sketch_items(flags, NO_DRAWINGS)))
        assert groups[0].non_assessable_rows == [] and groups[0].adjustment == Decimal("10")
        assert groups[0].non_assessable == Decimal("10")

    def test_an_area_larger_than_its_flag_is_capped_by_an_adjustment(self):
        flags = [piece(1, "7in", L=5, W=5, quantity="0", areas=[area(1, L=10, W=3)])]  # 30 SF of area on 25 SF
        groups = calc_groups(flags, compute_measure(flags, RATES), number_items(flags, sketch_items(flags, NO_DRAWINGS)))
        assert groups[0].adjustment == Decimal("-5") and groups[0].non_assessable == Decimal("25")

    def test_the_panel_lines_subtract_only_when_something_isnt_assessed(self):
        flags = [piece(1, L=10, W=5, areas=[area(1, x=1, y=1)], quantity="46"), piece(2, "7in", x=20)]
        groups = calc_groups(flags, compute_measure(flags, RATES), number_items(flags, sketch_items(flags, NO_DRAWINGS)))
        kinds = [line[0] for line in calc_lines(groups)]
        assert kinds == ["title", "heads", "row", "total", "title", "heads", "row", "total", "title", "subtraction",
                         "title", "heads", "row", "total", "title", "total"]
        assert calc_lines(groups)[9] == ("subtraction", Decimal("50"), Decimal("4"), Decimal("46"))


class TestLayout:
    def test_the_4_and_7_inch_tables_stand_side_by_side_and_the_sketch_gives_up_the_width(self):
        flags = [piece(1), piece(2, "7in", x=20)]
        groups = calc_groups(flags, compute_measure(flags, RATES), number_items(flags, sketch_items(flags, NO_DRAWINGS)))
        layout = report_layout(groups)
        assert [[group.thickness for group in column] for column in layout.columns] == [["4in"], ["7in"]]
        assert layout.calc_pane[2] == CALC_WIDTH_TWO and layout.sketch_width == 720 - CALC_WIDTH_TWO - 12
        assert layout.pane[2] == layout.sketch_width - 52

    def test_one_thickness_is_one_column_and_a_wider_sketch(self):
        flags = [piece(1), piece(2, x=20)]
        groups = calc_groups(flags, compute_measure(flags, RATES), number_items(flags, sketch_items(flags, NO_DRAWINGS)))
        layout = report_layout(groups)
        assert len(layout.columns) == 1 and layout.calc_pane[2] == CALC_WIDTH
        assert layout.sketch_width == 720 - CALC_WIDTH - 12

    def test_7_as_4_inch_shares_the_4_inch_column(self):
        flags = [piece(1), piece(2, "7in_as_4in", x=10), piece(3, "7in", x=20)]
        groups = calc_groups(flags, compute_measure(flags, RATES), number_items(flags, sketch_items(flags, NO_DRAWINGS)))
        assert [[group.thickness for group in column] for column in report_layout(groups).columns] == [
            ["4in", "7in_as_4in"], ["7in"]]

    def test_the_signature_fits_its_cell_above_the_line_at_most_1_3_inches_wide(self):
        x, y, w, h = signature_box(546.0, 14.0, 48.0)
        assert w == SIGNATURE_MAX_WIDTH == 94.0 and y == 25.5 and y + h <= 14 + 48
        # Centred across the 210 pt cell
        assert x == 546 + (210 - 94) / 2 and x + w <= 546 + 210

    @pytest.mark.parametrize("property_type, label", [
        ("1_3_family", "1-3 FAMILY"), ("4_plus_family", "4+ FAMILY"), ("expedited", "EXPEDITED"),
        (None, "CITY PROPERTY")])
    def test_the_header_prints_the_property_class(self, property_type, label):
        assert property_class(property_type) == label
