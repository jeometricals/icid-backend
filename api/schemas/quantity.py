from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class Quantity(BaseModel):
    quantity_id: UUID
    idr_id: UUID
    idr_number: Optional[str] = None
    report_date: date
    reporter_uuid: UUID
    reporter_name: Optional[str] = None
    report_type: str
    # Read off report_type (api/services/disciplines.py), not stored.
    disciplines: list[str]
    pay_item_ref: Optional[str] = None
    budget_code: Optional[str] = None
    description: Optional[str] = None
    amount: float
    unit: Optional[str] = None


class QuantityList(BaseModel):
    rows: list[Quantity]
    # The sum of the rows' amounts per unit; rows without a unit are summed under "(unknown)".
    totals_by_unit: dict[str, float]
    row_count: int
    # True when more rows matched than one response carries; narrow the filters to see the rest.
    truncated: bool


class QuantityListResponse(BaseModel):
    status: str
    message: str
    data: QuantityList
