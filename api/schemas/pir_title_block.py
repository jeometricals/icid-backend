from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from api.schemas.pir import CompassPoint


class PirTitleBlock(BaseModel):
    """
    A PIR's title block as the Construction Report prints it: page 1's contract number and three dates, and the PIR's
    north bearing; None for a field not set.
    """

    pir_id: UUID
    contract_no: Optional[str] = None
    date_notice_issued: Optional[date] = None
    date_excavated: Optional[date] = None
    date_constructed: Optional[date] = None
    north_bearing: Optional[CompassPoint] = None


class PirTitleBlockResponse(BaseModel):
    status: str
    message: str
    data: PirTitleBlock
