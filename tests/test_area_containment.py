import pytest

from api.services.area_containment import area_outside_flag, overlap_area, oversize_message, triangles

# ---------------------------------------------------------------------------
# Whether a non-assessable area lies within its flag (R1h.4)
# ---------------------------------------------------------------------------


def placed(shape: str, dimensions: dict, x: float, y: float, rotation: float = 0) -> dict:
    """
    Build a flag or an area as the sketch places it: its shape and dimensions (feet) and its middle (page pixels).
    Takes the shape, its dimensions, its middle and its rotation.
    Returns the dict.
    """
    return {"shape": shape, "dimensions": dimensions, "sketch_x": x, "sketch_y": y, "rotation": rotation}


# A 10 x 5 ft flag at 10 px/ft, its middle at (100, 100): it spans x 50..150, y 75..125
FLAG = placed("rectangle", {"L": 10, "W": 5}, 100, 100)
L_SHAPE = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]  # an L, 7 sq ft, its notch at the top right


class TestTriangles:
    def test_a_concave_polygon_is_cut_into_triangles_covering_it(self):
        pieces = triangles(L_SHAPE)
        total = sum(abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2 for a, b, c in pieces)
        assert total == pytest.approx(7)

    def test_a_flat_polygon_has_none(self):
        assert triangles([(0, 0), (1, 0), (2, 0)]) == []


class TestOverlap:
    def test_a_square_inside_another_overlaps_wholly(self):
        assert overlap_area([(1, 1), (2, 1), (2, 2), (1, 2)], [(0, 0), (3, 0), (3, 3), (0, 3)]) == pytest.approx(1)

    def test_half_over_the_edge_overlaps_by_half(self):
        assert overlap_area([(2, 0), (4, 0), (4, 1), (2, 1)], [(0, 0), (3, 0), (3, 3), (0, 3)]) == pytest.approx(1)

    def test_in_a_concave_polygons_notch_there_is_no_overlap(self):
        assert overlap_area([(2, 2), (3, 2), (3, 3), (2, 3)], L_SHAPE) == pytest.approx(0)


class TestAreaOutsideFlag:
    def test_an_area_inside_its_flag_has_nothing_outside(self):
        assert area_outside_flag(placed("rectangle", {"L": 2, "W": 2}, 100, 100), FLAG, 10) == pytest.approx(0)

    def test_an_area_reaching_past_its_flag_has_the_rest_outside(self):
        # 4 x 2 ft, its middle on the flag's right edge: 2 x 2 inside, 2 x 2 outside
        area = placed("rectangle", {"L": 4, "W": 2}, 150, 100)
        assert area_outside_flag(area, FLAG, 10) == pytest.approx(4)

    def test_a_turned_area_is_measured_as_placed(self):
        # A 2 x 2 ft square turned 45 degrees at the flag's top edge: its upper half lies outside
        area = placed("rectangle", {"L": 2, "W": 2}, 100, 75, rotation=45)
        assert area_outside_flag(area, FLAG, 10) == pytest.approx(2)

    def test_an_area_bigger_than_its_flag(self):
        # 10.00 x 9.16 on a 10 x 5 flag sharing its middle: 10 x 4.16 outside
        area = placed("rectangle", {"L": 10, "W": 9.16}, 100, 100)
        assert area_outside_flag(area, FLAG, 10) == pytest.approx(41.6)

    def test_the_scale_sets_the_size_on_the_page(self):
        # At 20 px/ft the flag spans x 0..200, so the same area now fits
        area = placed("rectangle", {"L": 4, "W": 2}, 150, 100)
        assert area_outside_flag(area, FLAG, 20) == pytest.approx(0)

    @pytest.mark.parametrize("area, flag, scale", [
        (placed("rectangle", {"L": 2, "W": 2}, 100, 100), FLAG, None),
        (placed("rectangle", {"L": 2, "W": 2}, None, 100), FLAG, 10),
        (placed("rectangle", {"L": 2, "W": 2}, 100, 100), {**FLAG, "sketch_x": None}, 10),
    ])
    def test_it_cant_be_told_without_a_scale_or_a_place(self, area, flag, scale):
        assert area_outside_flag(area, flag, scale) is None

    def test_the_message_names_the_area_the_flag_and_how_much(self):
        assert oversize_message(4, "f-1", 47.5) == ("Non-assessable area 4 extends past flag f-1's bounds by 47.50 SF; "
                                                    "please redraw or split across adjacent flags.")
