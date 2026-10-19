"""
A ramp's ADA warnings: its values checked against the advisories of NYC DOT's Pedestrian Ramp Inspection Form
(version 1.6, its instructions). They are shown with the ramp and printed on its inspection report; they never stop a
save. Slopes are compared by size, whichever way they fall.
"""

from decimal import Decimal
from typing import Any, Optional

from api.schemas.ped_ramp import FIELD_LABELS, AdaWarning

# The advisories' limits: slopes in percent, lengths in inches
RAMP_RUNNING_MAX = Decimal("8.3")
CROSS_SLOPE_MAX = Decimal("2.0")
LANDING_SLOPE_MAX = Decimal("2.0")
LANDING_MIN_IN = Decimal("48")
FLARE_MAX = Decimal("10.0")
FLARE_IN_PATH_MAX = Decimal("8.3")
GUTTER_CROSS_MAX = Decimal("5.0")
GUTTER_FLOW_MAX = Decimal("2.0")
APPROACH_WIDTH_MIN_IN = Decimal("48")
APPROACH_RUNNING_MAX = Decimal("5.0")
RAMP_WIDTH_EXISTING_MIN_IN = Decimal("36")
RAMP_WIDTH_NEW_MIN_IN = Decimal("60")
CURB_REVEAL_MAX_IN = Decimal("0.25")
DWS_OFFSET_IN = Decimal("2")
# The constructions the form calls new (a ramp's width and its landing's are held to the new installations' minimums)
NEW_CONSTRUCTION = ("new_install", "upgrade")

# The answers the form treats as a failure, and what it says of each
FAILING_ANSWERS = (
    ("is_there_ponding", True, "should be No", "Ponding at the ramp"),
    ("clear_space_within_crosswalk", False, "should be Yes", "A 48 x 48 in clear space must be within the crosswalk"),
    ("dws_length_min_24_inch", False, "should be Yes", "DWS running length must be at least 24 in"),
    ("dws_full_ramp_width", False, "should be Yes", "DWS must span the full ramp width"),
    ("dws_aligned_with_ramp", "no", "should be Yes or N/A", "DWS must be aligned with the ramp"),
)


def _number(value: Any) -> Optional[Decimal]:
    """
    Read a stored or sent number as a Decimal.
    Takes the value (a Decimal, an int, a float or None).
    Returns the Decimal, or None for none.
    """
    return None if value is None else Decimal(str(value))


def _shown(value: Decimal, unit: str) -> str:
    """
    Print a value with its unit, as a warning quotes it.
    Takes the value and its unit ("%" or "in").
    Returns the text, as "9.1%" or "44 in".
    """
    text = format(value.normalize(), "f")
    return f"{text}%" if unit == "%" else f"{text} in"


def _over(attrs: dict[str, Any], field: str, limit: Decimal, unit: str, note: str = "") -> Optional[AdaWarning]:
    """
    Warn when a value is larger than its limit (a slope by its size).
    Takes the ramp's values, the field, its limit, its unit and anything the message adds.
    Returns the AdaWarning, or None when the value is within it or not entered.
    """
    value = _number(attrs.get(field))
    if value is None:
        return None
    size = abs(value) if unit == "%" else value
    if size <= limit:
        return None
    return AdaWarning(field=field, value=value, limit=f"at most {_shown(limit, unit)}",
                      message=f"{FIELD_LABELS[field]} {_shown(value, unit)} exceeds {_shown(limit, unit)}{note}")


def _under(attrs: dict[str, Any], field: str, limit: Decimal, note: str = "") -> Optional[AdaWarning]:
    """
    Warn when a length is shorter than its minimum.
    Takes the ramp's values, the field, its minimum in inches and anything the message adds.
    Returns the AdaWarning, or None when the value meets it or is not entered.
    """
    value = _number(attrs.get(field))
    if value is None or value >= limit:
        return None
    return AdaWarning(field=field, value=value, limit=f"at least {_shown(limit, 'in')}",
                      message=f"{FIELD_LABELS[field]} {_shown(value, 'in')} is less than {_shown(limit, 'in')}{note}")


def _common(attrs: dict[str, Any], ramp_type: str) -> list[Optional[AdaWarning]]:
    """
    Check what every ramp has: the curb reveal, the DWS offsets (at most 2 in from the curb; a parallel ramp's at least 2 in) and the failing answers.
    Takes the ramp's values and its ramp type.
    Returns the warnings, None for each check passed.
    """
    checks: list[Optional[AdaWarning]] = [_over(attrs, "maximum_curb_reveal_at_ramp_in", CURB_REVEAL_MAX_IN, "in")]
    for field in ("dws_left_offset_from_curb_in", "dws_right_offset_from_curb_in"):
        if ramp_type == "PARALLEL":
            checks.append(_under(attrs, field, DWS_OFFSET_IN, " (on a parallel ramp, not closer than 2 in to the curb)"))
        else:
            checks.append(_over(attrs, field, DWS_OFFSET_IN, "in"))
    for field, failing, wanted, message in FAILING_ANSWERS:
        if field in attrs and attrs.get(field) == failing:
            checks.append(AdaWarning(field=field, value=failing, limit=wanted, message=message))
    return checks


def _ramp_run(attrs: dict[str, Any], prefix: str, width_min: Decimal) -> list[Optional[AdaWarning]]:
    """
    Check a ramp run: its running slope (8.3%), cross slope (2%) and width (36 in, or 60 in when new).
    Takes the ramp's values, the run's field prefix ("ramp", "left_ramp" or "right_ramp") and the width's minimum.
    Returns the warnings, None for each check passed.
    """
    return [
        _over(attrs, f"{prefix}_running_slope_pct", RAMP_RUNNING_MAX, "%"),
        _over(attrs, f"{prefix}_cross_slope_pct", CROSS_SLOPE_MAX, "%"),
        _under(attrs, f"{prefix}_width_in", width_min),
    ]


def _landing(attrs: dict[str, Any], prefix: str, new: bool) -> list[Optional[AdaWarning]]:
    """
    Check a landing: both slopes (2%), its length (48 in) and, for a new or upgraded ramp, its width (48 in).
    Takes the ramp's values, the landing's field prefix ("landing" or "bottom_landing") and whether the ramp is new.
    Returns the warnings, None for each check passed.
    """
    checks = [
        _over(attrs, f"{prefix}_running_slope_pct", LANDING_SLOPE_MAX, "%"),
        _over(attrs, f"{prefix}_cross_slope_pct", LANDING_SLOPE_MAX, "%"),
        _under(attrs, f"{prefix}_length_in", LANDING_MIN_IN, " (60 in where constrained at the back)"),
    ]
    if new:
        checks.append(_under(attrs, f"{prefix}_width_in", LANDING_MIN_IN))
    return checks


def _gutter(attrs: dict[str, Any]) -> list[Optional[AdaWarning]]:
    """
    Check the gutter: cross slope 5% (2% where it is the clear space), flow slope 2%.
    Takes the ramp's values.
    Returns the warnings, None for each check passed.
    """
    return [
        _over(attrs, "gutter_cross_slope_pct", GUTTER_CROSS_MAX, "%", " (2.0% where the gutter is the clear space)"),
        _over(attrs, "gutter_flow_slope_pct", GUTTER_FLOW_MAX, "%"),
    ]


def ada_warnings(attrs: dict[str, Any], ramp_type: str) -> list[AdaWarning]:
    """
    Check a ramp's attributes against the form's advisories, for its ramp type.
    Takes the attributes keyed by field (as stored or as sent) and the ramp type (PERPENDICULAR, PARALLEL or CUT_THROUGH).
    Returns the AdaWarnings, in the form's order ([] when every value entered is within its limit).
    """
    new = attrs.get("construction") in NEW_CONSTRUCTION
    width_min = RAMP_WIDTH_NEW_MIN_IN if new else RAMP_WIDTH_EXISTING_MIN_IN
    checks = _common(attrs, ramp_type)
    if ramp_type == "PERPENDICULAR":
        checks += _ramp_run(attrs, "ramp", width_min) + _landing(attrs, "landing", new)
        for side in ("left", "right"):
            in_path = attrs.get(f"{side}_flare_traversable_path") is True
            limit = FLARE_IN_PATH_MAX if in_path else FLARE_MAX
            checks.append(_over(attrs, f"{side}_flare_slope_pct", limit, "%",
                                " (the flare is in the traversable path)" if in_path else ""))
        checks += _gutter(attrs)
        for side in ("left", "right"):
            checks += [
                _under(attrs, f"{side}_sidewalk_approach_width_in", APPROACH_WIDTH_MIN_IN),
                _over(attrs, f"{side}_sidewalk_running_slope_pct", APPROACH_RUNNING_MAX, "%"),
                _over(attrs, f"{side}_sidewalk_cross_slope_pct", CROSS_SLOPE_MAX, "%"),
            ]
    elif ramp_type == "PARALLEL":
        checks += _landing(attrs, "bottom_landing", new)
        checks += _ramp_run(attrs, "left_ramp", width_min) + _ramp_run(attrs, "right_ramp", width_min)
        checks += _gutter(attrs)
    else:
        checks += _ramp_run(attrs, "ramp", width_min) + _gutter(attrs)
    return [warning for warning in checks if warning is not None]
