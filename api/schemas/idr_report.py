from datetime import datetime
from enum import StrEnum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel


class ReportType(StrEnum):
    GEN = "GEN"
    SWR = "SWR"
    HC = "HC"
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
    created_at: datetime
    updated_at: datetime


class IdrReportResponse(BaseModel):
    status: str
    message: str
    data: IdrReport


class IdrReportConflict(BaseModel):
    detail: str
    existing_report_id: UUID
