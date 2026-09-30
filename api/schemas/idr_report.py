from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, BeforeValidator
from pydantic_core import PydanticCustomError


class ReportType(StrEnum):
    GEN = "GEN"
    SWR = "SWR"
    HC = "HC"
    SWCB = "SWCB"
    WM_1 = "WM_1"
    WM_2 = "WM_2"
    WM_3 = "WM_3"
    AC = "AC"
    CONC = "CONC"
    BOX = "BOX"
    PILE = "PILE"
    JACK = "JACK"
    CCL = "CCL"
    RE = "RE"
    DSP = "DSP"
    OFF = "OFF"
    SKETCH = "SKETCH"
    CONT = "CONT"
    CONC_MIX = "CONC_MIX"
    CONC_CYL = "CONC_CYL"
    FIELD_MEMO = "FIELD_MEMO"
    FIELD_ORDER = "FIELD_ORDER"


# Types that are addendums by nature. A frontend hint only; the backend does not
# derive is_addendum from it (DSP can be either and is deliberately absent).
ADDENDUM_TYPES = frozenset({
    ReportType.SKETCH,
    ReportType.CONT,
    ReportType.CONC_MIX,
    ReportType.WM_2,
    ReportType.WM_3,
    ReportType.CONC_CYL,
})


# Human-readable label per report type, used when composing the auto-generated
# General's Description of Work (e.g. "Sewer: <desc>" or "Sewer work"). Only
# non-addendum, non-General reports reach the summary, but every type is mapped
# so the label lookup never falls through. Must stay in sync with the frontend's
# REPORT_TYPES labels (icid-frontend/src/data/reportTypes.js).
TYPE_LABELS: dict[str, str] = {
    ReportType.GEN.value: "General",
    ReportType.SWR.value: "Sewer",
    ReportType.HC.value: "House Connection",
    ReportType.SWCB.value: "Sidewalk, Curb, Concrete Base",
    ReportType.WM_1.value: "Water Main (Sheet 1)",
    ReportType.WM_2.value: "Water Main (Sheet 2)",
    ReportType.WM_3.value: "Water Main (Sheet 3)",
    ReportType.AC.value: "Asphaltic Concrete",
    ReportType.CONC.value: "Concrete (Structures)",
    ReportType.BOX.value: "Box Sewer",
    ReportType.PILE.value: "Pile Driving",
    ReportType.JACK.value: "Jacking",
    ReportType.CCL.value: "Community Construction Liaison",
    ReportType.RE.value: "Resident Engineer's Daily Diary",
    ReportType.DSP.value: "Daily Site Patrol",
    ReportType.OFF.value: "Office Report",
    ReportType.SKETCH.value: "Sketch Sheet",
    ReportType.CONT.value: "Report Continuation",
    ReportType.CONC_MIX.value: "Concrete Truck & Mix Info",
    ReportType.CONC_CYL.value: "Concrete Cylinder Data",
    ReportType.FIELD_MEMO.value: "Field Memo",
    ReportType.FIELD_ORDER.value: "Field Order",
}


def label_for(report_type: str) -> str:
    """
    Give the human-readable label for a report type code.
    Takes the stored report_type string.
    Returns its label from TYPE_LABELS, or the raw code if unmapped.
    """
    return TYPE_LABELS.get(report_type, report_type)


def require_json_object(value: Any) -> Any:
    """
    Reject a report_data body whose top level is not a JSON object.
    Takes the raw parsed JSON value.
    Returns it unchanged, or raises a validation error (surfaced as 422).
    """
    if not isinstance(value, dict):
        raise PydanticCustomError("report_data_not_object", "report_data must be a JSON object")
    return value


# Unvalidated report body: any JSON object, contents stored as-is.
ReportData = Annotated[dict[str, Any], BeforeValidator(require_json_object)]


class IdrReportCreate(BaseModel):
    report_type: ReportType
    is_addendum: bool = False
    parent_report_id: Optional[UUID] = None


class IdrReport(BaseModel):
    report_id: UUID
    idr_id: UUID
    report_type: str
    is_addendum: bool
    parent_report_id: Optional[UUID] = None
    page_number: Optional[int] = None
    report_data: dict[str, Any]
    is_auto_generated: bool = False
    created_at: datetime
    updated_at: datetime


class IdrReportResponse(BaseModel):
    status: str
    message: str
    data: IdrReport


class IdrReportConflict(BaseModel):
    detail: str
    existing_report_id: UUID
