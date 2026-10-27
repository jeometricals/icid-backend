from decimal import Decimal

import pytest

from api.services.property_line import (
    StripError,
    check_strip,
    effective_frontage,
    outside_flags,
    strip_frontage_ft,
    strip_points,
)

# ---------------------------------------------------------------------------
# api/services/property_line.py (Slice Y1.5): the sidewalk strip's checks, frontage and auto-flip test, and the
# effective-frontage resolver. Pure functions, no database.
# ---------------------------------------------------------------------------

SCALE = 16  # px per foot: a 5 x 5 ft flag is 80 px square
# Its first edge, the property line, is 160 px = 10.00 ft
STRIP = [(300.0, 250.0), (460.0, 250.0), (460.0, 450.0), (300.0, 450.0)]


def flag(x: float, y: float, **extra) -> dict:
    """A 5 x 5 ft rectangle flag centred at (x, y) on the page."""
    return {"shape": "rectangle", "dimensions": {"L": 5, "W": 5}, "sketch_x": x, "sketch_y": y, "rotation": 0, **extra}


class TestCheckStrip:
    def test_a_simple_polygon_passes(self):
        check_strip(STRIP)

    def test_a_concave_one_passes(self):
        check_strip([(0, 0), (100, 0), (100, 100), (50, 40), (0, 100)])

    @pytest.mark.parametrize("points,words", [
        ([(0, 0), (1, 1)], "3 to 200 points"),
        ([(0, 0)] * 201, "3 to 200 points"),
        ([(0, 0), (0, 0), (1, 1)], "first edge, the property line, has no length"),
        ([(0, 0), (1, 1), (2, 2)], "enclose some area"),
        ([(0, 0), (100, 100), (100, 0), (0, 50)], "must not cross"),
        ([(0, 0), (float("nan"), 0), (1, 1)], "must be numbers"),
    ], ids=["two", "too-many", "zero-first-edge", "collinear", "bow-tie", "nan"])
    def test_a_bad_strip_is_refused(self, points, words):
        with pytest.raises(StripError, match=words):
            check_strip(points)


class TestFrontage:
    def test_it_is_the_first_edge_at_the_scale(self):
        assert strip_frontage_ft(STRIP, SCALE) == Decimal("10.00")

    def test_a_slanted_first_edge_is_its_length(self):
        assert strip_frontage_ft([(0, 0), (30, 40), (0, 40)], 2) == Decimal("25.00")

    def test_no_scale_no_frontage(self):
        assert strip_frontage_ft(STRIP, None) is None

    def test_points_are_read_from_the_stored_form(self):
        assert strip_points([{"x": 1, "y": 2}, {"x": 3.5, "y": 4}]) == [(1.0, 2.0), (3.5, 4.0)]


class TestOutsideFlags:
    def test_only_a_flag_wholly_outside_is_outside(self):
        inside, crossing, outside = flag(400, 300, id=1), flag(450, 450, id=2), flag(700, 300, id=3)
        assert [f["id"] for f in outside_flags(STRIP, [inside, crossing, outside], SCALE)] == [3]

    def test_a_flag_touching_the_edge_only_is_outside(self):
        # Centred at x 500, its left side is at 460, on the strip's right edge
        assert [f["id"] for f in outside_flags(STRIP, [flag(500, 300, id=4)], SCALE)] == [4]

    def test_a_flag_without_a_place_isnt_tested(self):
        assert outside_flags(STRIP, [{**flag(700, 300), "sketch_x": None}], SCALE) == []

    def test_a_concave_strip_leaves_a_flag_in_its_notch_outside(self):
        # A U: the notch between the arms (x 200..400, y 0..300) is not the strip's
        u = [(0, 0), (200, 0), (200, 300), (400, 300), (400, 0), (600, 0), (600, 500), (0, 500)]
        assert [f["id"] for f in outside_flags(u, [flag(300, 100, id=5), flag(100, 100, id=6)], SCALE)] == [5]


class TestEffectiveFrontage:
    STRIP_ROWS = [{"x": x, "y": y} for x, y in STRIP]

    def test_the_strip_wins(self):
        annotation = {"sidewalk_strip_points": self.STRIP_ROWS, "tax_map_width_ft": Decimal("16.67"),
                      "tax_map_source": "manual"}
        assert effective_frontage(annotation, {"tax_map_frontage_ft": Decimal("20")}, SCALE) == (
            Decimal("10.00"), "sidewalk_strip")

    def test_then_the_pages_width_with_its_source(self):
        annotation = {"sidewalk_strip_points": None, "tax_map_width_ft": Decimal("16.67"), "tax_map_source": "manual"}
        assert effective_frontage(annotation, {"tax_map_frontage_ft": Decimal("20")}, SCALE) == (
            Decimal("16.67"), "manual")

    def test_then_the_sites_mappluto_frontage(self):
        prop = {"tax_map_frontage_ft": Decimal("20"), "tax_map_source": "mappluto_26v2"}
        assert effective_frontage({"tax_map_width_ft": None}, prop, SCALE) == (Decimal("20.00"), "mappluto_26v2")

    def test_a_strip_on_a_page_without_a_scale_falls_through(self):
        annotation = {"sidewalk_strip_points": self.STRIP_ROWS, "tax_map_width_ft": Decimal("16.67"),
                      "tax_map_source": "mappluto_26v2"}
        assert effective_frontage(annotation, {}, None) == (Decimal("16.67"), "mappluto_26v2")

    def test_otherwise_unavailable(self):
        assert effective_frontage(None, {"tax_map_frontage_ft": None}, SCALE) == (None, "unavailable")
