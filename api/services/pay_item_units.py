"""
The units a project's pay item can be bid in, and the one short form each is stored in.

A bid document writes a unit the long way ("L.F.", "EACH", "TONS"); an admin may type either. Both are accepted, and
the short form is what icid.contract_items holds, so one unit is always spelled one way.
"""

import re
from typing import Optional

# Every accepted spelling, without its periods and spaces and in capitals, and the form it is stored in
UNIT_FORMS = {
    "EACH": "EA", "EA": "EA",
    "LF": "LF", "SF": "SF", "SY": "SY", "CY": "CY", "VF": "VF", "FS": "FS", "LS": "LS",
    "MONTH": "MONTH", "DAY": "DAY",
    "P/HR": "HR", "HR": "HR",
    "CREW/HR": "CREWHR", "CREWHR": "CREWHR",
    "TONS": "TN", "TON": "TN", "TN": "TN",
    "LBS": "LB", "LB": "LB",
    "BLOCK": "BLOCK",
    "UNITS": "UNIT", "UNIT": "UNIT",
    "SETS": "SET", "SET": "SET",
}


class UnknownUnit(ValueError):
    """A unit that isn't in UNIT_FORMS. Its message names the unit as it was given."""


def normalize_unit(raw: str) -> str:
    """
    Give a pay item's unit in the form it is stored in.
    Takes the unit as typed or as the bid document has it, e.g. "L.F.", "each" or "TN"; periods, spaces and capitals don't matter.
    Returns the short form, e.g. "LF", "EA" or "TN"; raises UnknownUnit for a unit that isn't in UNIT_FORMS.
    """
    unit = UNIT_FORMS.get(re.sub(r"[.\s]", "", raw).upper())
    if unit is None:
        raise UnknownUnit(f"unknown unit: '{raw.strip()}'")
    return unit


# The units a flag's quantity can be given in: its area (SF, SY), its volume at its thickness (CY), or the flag itself
# (EA); the flags' FLAG_AMOUNT computes each
FLAG_UNITS = ("SF", "SY", "CY", "EA")


def flag_unit(raw: Optional[str]) -> Optional[str]:
    """
    Tell whether a flag can be measured in a pay item's unit.
    Takes the pay item's unit as stored (short form, or a seeded row's long one, e.g. "S.F." or "Each").
    Returns the unit's short form when it is one of FLAG_UNITS, else None.
    """
    try:
        unit = normalize_unit(raw or "")
    except UnknownUnit:
        return None
    return unit if unit in FLAG_UNITS else None
