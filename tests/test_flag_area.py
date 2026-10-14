import math
from decimal import Decimal

import pytest

from api.services.flag_area import compute_area, polygon_area

# ---------------------------------------------------------------------------
# compute_area: each shape against a value worked out by hand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape,dimensions,area", [
    ("rectangle", {"L": 5, "W": 4}, "20.00"),                          # 5 x 4
    ("rectangle", {"L": 0.1, "W": 0.3}, "0.03"),                       # exact, not 0.030000000000000002
    ("rectangle", {"L": 1.005, "W": 1}, "1.01"),                       # halves round up
    ("trapezoid", {"a": 3, "b": 5, "w": 4}, "16.00"),                  # (3 + 5) / 2 x 4
    ("trapezoid", {"a": 2.5, "b": 3.5, "w": 5}, "15.00"),              # (2.5 + 3.5) / 2 x 5
    ("triangle", {"a": 3, "b": 4}, "6.00"),                            # 3 x 4 / 2
    ("triangle", {"a": 5, "b": 5}, "12.50"),                           # 5 x 5 / 2
    ("arc", {"r": 2, "theta": math.pi}, "6.28"),                       # 4 / 2 x (pi - 0) = 2 pi: a half disc
    ("arc", {"r": 10, "theta": math.pi / 2}, "28.54"),                 # 100 / 2 x (pi/2 - 1) = 28.5398...
    ("arc", {"r": 1, "theta": 2 * math.pi}, "3.14"),                   # the whole disc, pi
])
def test_each_shape_comes_to_its_hand_worked_area(shape, dimensions, area):
    assert compute_area(shape, dimensions) == Decimal(area)


@pytest.mark.parametrize("vertices,area", [
    ([(0, 0), (4, 0), (4, 3), (0, 3)], "12.00"),                       # a 4 x 3 rectangle
    ([(0, 3), (4, 3), (4, 0), (0, 0)], "12.00"),                       # the same, clockwise
    ([(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)], "12.00"),       # an L: 4 x 2 + 2 x 2
    ([(0, 0), (6, 0), (3, 4)], "12.00"),                               # a triangle, base 6 height 4
])
def test_a_polygon_comes_to_its_shoelace_area(vertices, area):
    dimensions = {"vertices": [{"x": x, "y": y} for x, y in vertices]}
    assert compute_area("polygon", dimensions) == Decimal(area)


def test_collinear_points_enclose_nothing():
    assert polygon_area([{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}]) == 0


def test_every_area_has_two_decimal_places():
    assert compute_area("triangle", {"a": 1, "b": 1}).as_tuple().exponent == -2


def test_an_unknown_shape_is_refused():
    with pytest.raises(ValueError):
        compute_area("hexagon", {})
