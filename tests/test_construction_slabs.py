import pytest

from api.services.construction_sketch import SketchItem
from api.services.construction_slabs import abut, edge_runs, group_bounds, slab_groups

# ---------------------------------------------------------------------------
# Slabs (R1h.4): abutting flags of one thickness and assessment drawn as one piece of sidewalk
# ---------------------------------------------------------------------------


def square(x: float, y: float, size: float = 5, thickness: str = "4in", assessable: bool = True) -> SketchItem:
    """
    A square flag on the sketch, in feet.
    Takes its top-left corner, its side, its thickness and whether surveyed assessable.
    Returns the flag item.
    """
    points = [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]
    return SketchItem("flag", points, {"surveyed_thickness": thickness, "surveyed_is_assessable": assessable})


class TestAbut:
    def test_side_by_side_squares_abut(self):
        assert abut(square(0, 0).points, square(5, 0).points)

    def test_a_gap_within_a_tenth_of_a_foot_still_abuts(self):
        assert abut(square(0, 0).points, square(5.08, 0).points)

    def test_a_wider_gap_does_not(self):
        assert not abut(square(0, 0).points, square(5.2, 0).points)

    def test_touching_at_a_corner_does_not(self):
        assert not abut(square(0, 0).points, square(5, 5).points)


class TestGroups:
    def test_abutting_flags_of_one_material_are_one_slab(self):
        a, b, c = square(0, 0), square(5, 0), square(10, 0)
        assert slab_groups([a, b, c]) == [[a, b, c]]

    def test_another_thickness_or_assessment_is_its_own_slab(self):
        a, b, c = square(0, 0), square(5, 0, thickness="7in"), square(10, 0, assessable=False)
        assert slab_groups([a, b, c]) == [[a], [b], [c]]

    def test_flags_joined_through_another_are_one_slab(self):
        # a and c don't touch; both abut b
        a, b, c = square(0, 0), square(5, 0), square(5, 5)
        assert slab_groups([a, c, b]) == [[a, c, b]]

    def test_flags_that_overlap_fall_back_to_single_flags(self):
        a, b, c = square(0, 0), square(5, 0), square(4, 0)  # c lies over a and b
        groups = slab_groups([a, b, c])
        assert all(len(group) == 1 for group in groups) and len(groups) == 3

    def test_non_flags_are_left_out(self):
        area, flag = SketchItem("area", square(0, 0).points, {}), square(0, 0)
        assert slab_groups([area, flag]) == [[flag]]


class TestEdges:
    def test_the_shared_edge_is_inner_and_the_rest_is_the_border(self):
        runs = edge_runs([square(0, 0), square(5, 0)])
        inner = [run for run in runs if run.inner]
        outer = sum(abs(run.end[0] - run.start[0]) + abs(run.end[1] - run.start[1]) for run in runs if not run.inner)
        assert len(inner) == 2  # the shared edge, once from each side
        assert all(run.start[0] == pytest.approx(5) and run.end[0] == pytest.approx(5) for run in inner)
        assert outer == pytest.approx(30)  # the 10 x 5 slab's perimeter

    def test_a_partly_shared_edge_is_cut_where_the_neighbour_ends(self):
        # A 10 x 5 flag above a 5 x 5 one at its left: the long bottom edge is half inner, half border
        long_flag = SketchItem("flag", [(0, 0), (10, 0), (10, 5), (0, 5)], {})
        runs = [run for run in edge_runs([long_flag, square(0, 5)])
                if run.start[1] == pytest.approx(5) and run.end[1] == pytest.approx(5)]
        assert sorted((round(min(r.start[0], r.end[0]), 2), round(max(r.start[0], r.end[0]), 2), r.inner)
                      for r in runs) == [(0.0, 5.0, True), (0.0, 5.0, True), (5.0, 10.0, False)]

    def test_a_slabs_bounds_cover_its_flags(self):
        assert group_bounds([square(0, 0), square(5, 2)]) == (0, 0, 10, 7)
