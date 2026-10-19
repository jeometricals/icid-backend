"""
A Daily Site Patrol (DSP) report's report_data: the one report body the server checks.

The form is DDC's Daily Site Patrol sheet (DSP Fr): a time of patrol, the Maintenance of Site checklist (6 items) and
the Maintenance and Protection of Traffic checklist (16), each answered yes / no / N/A with details, and whether a
field order was issued. The day of the week, the sheet number and the I.R. No. are not stored: the export works them
out from the IDR. An answer may be left blank on a draft.
"""

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# The Maintenance of Site items, in the form's order
MOS_ITEMS: tuple[str, ...] = (
    "waste_rubbish_debris_removed",
    "open_trenches_plated_fenced",
    "stored_materials_secured",
    "open_paved_areas_swept",
    "trees_protected",
    "temporary_pavement_maintained",
)

# The Maintenance and Protection of Traffic items, in the form's order
MPT_ITEMS: tuple[str, ...] = (
    "traffic_maintained_per_plan",
    "advance_warning_signs",
    "traffic_control_signs",
    "tapers_transitions",
    "barrels",
    "flashing_lights",
    "breakaway_barricades",
    "arrow_boards",
    "timber_curbs_concrete_barriers",
    "steel_pedestrian_fencing",
    "pedestrian_building_access",
    "fire_hydrant_access",
    "open_sidewalks_clean",
    "closed_sidewalk_signs",
    "commercial_area_access",
    "flaggers_temp_signs",
)

DETAILS_MAX_CHARS = 2000
FIELD_ORDER_NUMBER_MAX_CHARS = 50
TIME_OF_PATROL = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _blank_to_none(value: Any) -> Any:
    """
    Trim a text value, turning a blank one into None.
    Takes the value as sent.
    Returns the trimmed text, None for blank text, or anything else unchanged (for the type check to refuse).
    """
    if isinstance(value, str):
        return value.strip() or None
    return value


class DspChecklistRow(BaseModel):
    """One checklist row: the item's key, its answer (None while unanswered) and the details."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item: str
    answer: Optional[Literal["yes", "no", "na"]] = None
    details: Optional[str] = Field(default=None, max_length=DETAILS_MAX_CHARS)

    @field_validator("details", mode="before")
    @classmethod
    def _trim_details(cls, value: Any) -> Any:
        """
        Trim the details; blank is None.
        Takes the value as sent.
        Returns it trimmed.
        """
        return _blank_to_none(value)


def _check_items(rows: list[DspChecklistRow], items: tuple[str, ...], name: str) -> list[DspChecklistRow]:
    """
    Check a checklist holds exactly its items, in the form's order, each once.
    Takes the rows, the items they must be and the checklist's name (for the message).
    Returns the rows; raises ValueError naming the items expected.
    """
    if [row.item for row in rows] != list(items):
        raise ValueError(f"must list the {len(items)} {name} items in order: {', '.join(items)}")
    return rows


class DspReport(BaseModel):
    """A DSP report's report_data, as the report form saves it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    timeOfPatrol: Optional[str] = None
    mosChecklist: list[DspChecklistRow]
    mptChecklist: list[DspChecklistRow]
    fieldOrderIssued: Optional[bool] = False
    fieldOrderNumber: Optional[str] = Field(default=None, max_length=FIELD_ORDER_NUMBER_MAX_CHARS)

    @field_validator("timeOfPatrol", mode="before")
    @classmethod
    def _check_time(cls, value: Any) -> Any:
        """
        Check the time of patrol is HH:MM on a 24-hour clock; blank is None.
        Takes the value as sent.
        Returns it trimmed; raises ValueError for any other text.
        """
        value = _blank_to_none(value)
        if isinstance(value, str) and not TIME_OF_PATROL.fullmatch(value):
            raise ValueError("must be HH:MM, 24-hour (e.g. 14:30)")
        return value

    @field_validator("fieldOrderNumber", mode="before")
    @classmethod
    def _trim_number(cls, value: Any) -> Any:
        """
        Trim the field order number; blank is None.
        Takes the value as sent.
        Returns it trimmed.
        """
        return _blank_to_none(value)

    @field_validator("mosChecklist")
    @classmethod
    def _check_mos(cls, rows: list[DspChecklistRow]) -> list[DspChecklistRow]:
        """
        Check the Maintenance of Site checklist's items.
        Takes its rows.
        Returns them; raises ValueError when they aren't MOS_ITEMS in order.
        """
        return _check_items(rows, MOS_ITEMS, "Maintenance of Site")

    @field_validator("mptChecklist")
    @classmethod
    def _check_mpt(cls, rows: list[DspChecklistRow]) -> list[DspChecklistRow]:
        """
        Check the Maintenance and Protection of Traffic checklist's items.
        Takes its rows.
        Returns them; raises ValueError when they aren't MPT_ITEMS in order.
        """
        return _check_items(rows, MPT_ITEMS, "Maintenance and Protection of Traffic")

    @model_validator(mode="after")
    def _field_order_number_when_issued(self) -> "DspReport":
        """
        Require a field order number when a field order was issued.
        Takes the checked report.
        Returns it; raises ValueError when fieldOrderIssued is true without a number.
        """
        if self.fieldOrderIssued and not self.fieldOrderNumber:
            raise ValueError("fieldOrderNumber is required when fieldOrderIssued is true")
        return self


class DspReportError(ValueError):
    """A DSP report body that doesn't fit DspReport; the message names the first problem."""


def _location(loc: tuple[Any, ...]) -> str:
    """
    Name a field the way report_data spells it, e.g. "mptChecklist[3].answer".
    Takes Pydantic's error location.
    Returns the path.
    """
    path = ""
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path


def check_dsp_report(data: dict[str, Any]) -> dict[str, Any]:
    """
    Check a DSP report's body and tidy it: text trimmed, blanks as null, every key present.
    Takes the report_data as sent.
    Returns it as it should be stored; raises DspReportError with the first problem, e.g.
    "DSP report: mptChecklist[3].answer: Input should be 'yes', 'no' or 'na'".
    """
    try:
        return DspReport.model_validate(data).model_dump()
    except ValidationError as exc:
        error = exc.errors()[0]
        where = _location(error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        raise DspReportError(f"DSP report: {where}: {message}" if where else f"DSP report: {message}") from exc


def blank_dsp_report() -> dict[str, Any]:
    """
    Build an empty DSP report's report_data: nothing answered, no field order.
    Takes nothing.
    Returns the dict, which check_dsp_report accepts.
    """
    return {
        "timeOfPatrol": None,
        "mosChecklist": [{"item": item, "answer": None, "details": None} for item in MOS_ITEMS],
        "mptChecklist": [{"item": item, "answer": None, "details": None} for item in MPT_ITEMS],
        "fieldOrderIssued": False,
        "fieldOrderNumber": None,
    }
