from datetime import date as Date
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AssessmentFigures(BaseModel):
    """
    A site's (or the totals') square feet by pay type, to 2 decimal places, and their amounts at the DOT rates, to the
    cent; an amount is None when its rate isn't set.
    """

    four_assessable: float
    four_non_assessable: float
    four_total: float
    four_amount: Optional[float] = None
    seven_assessable: float
    seven_non_assessable: float
    seven_total: float
    seven_amount: Optional[float] = None
    grand_total: Optional[float] = None


class AssessmentSite(AssessmentFigures):
    """One constructed site on the As-Builts & Assessments page: who it is, and its figures summed over the range."""

    property_id: UUID
    kind: Literal["sidewalk", "ped_ramp"]
    display_address: str
    block: Optional[str] = None
    lot: Optional[str] = None
    violation_no: Optional[str] = None
    community_board: Optional[str] = None
    remarks: list[str]
    used_approval_date: bool


class AssessmentRange(BaseModel):
    """The days asked for (either None: open)."""

    from_: Optional[Date] = Field(None, alias="from")
    to: Optional[Date] = None

    model_config = {"populate_by_name": True}


class AssessmentSummary(BaseModel):
    """A project's constructed sites over a range of days, a row each, with the totals and the rates used."""

    range: AssessmentRange
    cost_per_sf_4in: Optional[float] = None
    cost_per_sf_7in: Optional[float] = None
    sites: list[AssessmentSite]
    totals: AssessmentFigures


class AssessmentSummaryResponse(BaseModel):
    status: str
    message: str
    data: AssessmentSummary
