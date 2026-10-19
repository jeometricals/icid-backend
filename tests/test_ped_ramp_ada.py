from decimal import Decimal

import pytest

from api.schemas.ped_ramp import ALL_FIELDS, COMMON_FIELDS, FIELD_LABELS, TYPE_FIELDS, InspectionRamp
from api.services.ped_ramp_ada import ada_warnings
from api.services.ped_ramp_report import file_name, ramp_pages, shown

# ---------------------------------------------------------------------------
# A ramp's ADA warnings (the DOT form's advisories) and the inspection report's helpers
# ---------------------------------------------------------------------------


def fields(attrs: dict, ramp_type: str) -> list[str]:
    """
    The fields a ramp's values warn about.
    Takes the values and the ramp type.
    Returns the warned fields, in order.
    """
    return [warning.field for warning in ada_warnings(attrs, ramp_type)]


class TestPerpendicular:
    def test_values_within_the_advisories_raise_nothing(self):
        attrs = {"ramp_running_slope_pct": Decimal("8.3"), "ramp_cross_slope_pct": Decimal("-2.0"),
                 "ramp_width_in": 36, "landing_running_slope_pct": 1.5, "landing_length_in": 48,
                 "left_flare_slope_pct": 10, "gutter_cross_slope_pct": 5, "gutter_flow_slope_pct": 2,
                 "left_sidewalk_approach_width_in": 48, "left_sidewalk_running_slope_pct": 5,
                 "maximum_curb_reveal_at_ramp_in": Decimal("0.25"), "dws_left_offset_from_curb_in": 2,
                 "is_there_ponding": False, "dws_length_min_24_inch": True, "dws_aligned_with_ramp": "na"}
        assert ada_warnings(attrs, "PERPENDICULAR") == []

    def test_each_limit_is_checked(self):
        attrs = {"ramp_running_slope_pct": Decimal("8.4"), "ramp_cross_slope_pct": Decimal("-2.1"),
                 "ramp_width_in": 35, "landing_running_slope_pct": 2.1, "landing_cross_slope_pct": 3,
                 "landing_length_in": 47, "right_flare_slope_pct": 10.5, "gutter_cross_slope_pct": 5.5,
                 "gutter_flow_slope_pct": 2.5, "right_sidewalk_approach_width_in": 40,
                 "right_sidewalk_running_slope_pct": 6, "right_sidewalk_cross_slope_pct": 3,
                 "maximum_curb_reveal_at_ramp_in": Decimal("0.5"), "dws_right_offset_from_curb_in": 3}
        assert fields(attrs, "PERPENDICULAR") == [
            "maximum_curb_reveal_at_ramp_in", "dws_right_offset_from_curb_in", "ramp_running_slope_pct",
            "ramp_cross_slope_pct", "ramp_width_in", "landing_running_slope_pct", "landing_cross_slope_pct",
            "landing_length_in", "right_flare_slope_pct", "gutter_cross_slope_pct", "gutter_flow_slope_pct",
            "right_sidewalk_approach_width_in", "right_sidewalk_running_slope_pct", "right_sidewalk_cross_slope_pct",
        ]

    def test_a_flare_in_the_traversable_path_is_held_to_the_ramps_slope(self):
        attrs = {"left_flare_slope_pct": 9, "right_flare_slope_pct": 9, "left_flare_traversable_path": True}
        warnings = ada_warnings(attrs, "PERPENDICULAR")
        assert [warning.field for warning in warnings] == ["left_flare_slope_pct"]
        assert warnings[0].message == "Left flare slope 9% exceeds 8.3% (the flare is in the traversable path)"
        assert warnings[0].limit == "at most 8.3%"

    def test_a_new_ramp_is_held_to_the_new_widths(self):
        attrs = {"ramp_width_in": 48, "landing_width_in": 40}
        assert fields(attrs, "PERPENDICULAR") == []
        assert fields({**attrs, "construction": "new_install"}, "PERPENDICULAR") == ["ramp_width_in", "landing_width_in"]
        assert fields({**attrs, "construction": "upgrade"}, "PERPENDICULAR") == ["ramp_width_in", "landing_width_in"]

    def test_failing_answers_warn(self):
        attrs = {"is_there_ponding": True, "clear_space_within_crosswalk": False, "dws_length_min_24_inch": False,
                 "dws_full_ramp_width": False, "dws_aligned_with_ramp": "no"}
        warnings = ada_warnings(attrs, "PERPENDICULAR")
        assert [warning.field for warning in warnings] == list(attrs)
        assert warnings[0].limit == "should be No" and warnings[0].message == "Ponding at the ramp"

    def test_values_not_entered_raise_nothing(self):
        assert ada_warnings({field: None for field in ALL_FIELDS}, "PERPENDICULAR") == []


class TestOtherTypes:
    def test_a_parallel_ramp_checks_its_landing_and_both_runs(self):
        attrs = {"bottom_landing_cross_slope_pct": 2.5, "bottom_landing_length_in": 40, "left_ramp_running_slope_pct": 9,
                 "right_ramp_width_in": 30, "ramp_running_slope_pct": 12}
        assert fields(attrs, "PARALLEL") == ["bottom_landing_cross_slope_pct", "bottom_landing_length_in",
                                             "left_ramp_running_slope_pct", "right_ramp_width_in"]

    def test_a_parallel_ramps_dws_is_kept_at_least_2_in_from_the_curb(self):
        assert fields({"dws_left_offset_from_curb_in": 1, "dws_right_offset_from_curb_in": 6}, "PARALLEL") == [
            "dws_left_offset_from_curb_in"]

    def test_a_cut_through_checks_its_run_and_gutter(self):
        attrs = {"ramp_running_slope_pct": 9, "ramp_width_in": 30, "gutter_flow_slope_pct": 3,
                 "landing_length_in": 10}
        assert fields(attrs, "CUT_THROUGH") == ["ramp_running_slope_pct", "ramp_width_in", "gutter_flow_slope_pct"]


class TestTheFormsFields:
    def test_every_field_has_its_label(self):
        assert set(FIELD_LABELS) == set(ALL_FIELDS)

    @pytest.mark.parametrize("ramp_type", ["PERPENDICULAR", "PARALLEL", "CUT_THROUGH"])
    def test_no_type_repeats_a_common_field(self, ramp_type):
        assert not set(TYPE_FIELDS[ramp_type]) & set(COMMON_FIELDS)


def ramp(ramp_type: str, number: int) -> InspectionRamp:
    """
    A ramp at a corner, for the page grouping.
    Takes its ramp type and a number to tell it apart.
    Returns the InspectionRamp.
    """
    return InspectionRamp(property_id=f"00000000-0000-4000-8000-{number:012d}", intersection_street_1="Main St",
                          intersection_street_2="1st Ave", quadrant="NE", facing_street="street_1",
                          ramp_type=ramp_type, has_attributes=False)


class TestTheReportsHelpers:
    def test_a_cut_throughs_two_ramps_share_a_page(self):
        perpendicular, cut_1, cut_2, cut_3 = ramp("PERPENDICULAR", 1), ramp("CUT_THROUGH", 2), \
            ramp("CUT_THROUGH", 3), ramp("CUT_THROUGH", 4)
        assert ramp_pages([cut_1, perpendicular, cut_2, cut_3]) == [[perpendicular], [cut_1, cut_2], [cut_3]]

    def test_values_print_as_the_form_has_them(self):
        assert [shown("ramp_running_slope_pct", Decimal("7.50")), shown("ramp_width_in", 48),
                shown("is_there_ponding", False), shown("dws_material", "cast_iron"), shown("comments", None)] == [
            "7.5%", "48 in", "No", "Cast iron", ""]

    def test_the_file_is_named_after_the_intersection(self):
        from api.schemas.ped_ramp import CornerOut
        corner = CornerOut.model_validate({
            "corner_id": "00000000-0000-4000-8000-000000000001", "project_id": "HWS0023", "boro": "Queens",
            "north_south_street": "38th St", "east_west_street": "31st Ave.", "corner_direction": None,
            "created_at": "2026-10-19T09:00:00Z", "updated_at": "2026-10-19T09:00:00Z",
            "created_by": "00000000-0000-4000-8000-000000000002"})
        assert file_name(corner) == "38th_St_and_31st_Ave_ped_ramp_inspection.pdf"
