from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class QuantityRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # The work dates asked for, as "from" and "to" in the JSON; cumulative means they were not applied.
    date_from: Optional[date] = Field(None, alias="from")
    date_to: Optional[date] = Field(None, alias="to")
    cumulative: bool


class PayItemRollup(BaseModel):
    pay_item_ref: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    budget_code: Optional[str] = None
    actual_quantity: float
    # The five below are null when no contract item has this ref and budget code (unbudgeted).
    actual_value: Optional[float] = None
    budgeted_quantity: Optional[float] = None
    budgeted_value: Optional[float] = None
    unit_price: Optional[float] = None
    # Also null for a contract item bid at nothing.
    percent: Optional[float] = None
    unbudgeted: bool


class BudgetCodeTotal(BaseModel):
    budget_code: str
    actual_value: float
    budgeted_value: float
    percent: Optional[float] = None
    # True when quantities filed under this code match no contract item; they are in none of the values here.
    has_unbudgeted: bool


class ProjectTotal(BaseModel):
    actual_value: float
    budgeted_value: float
    percent: Optional[float] = None


class UnbudgetedTotal(BaseModel):
    # Always null: without a unit price the quantities have no value.
    actual_value: Optional[float] = None
    # How many entries of pay_items are unbudgeted.
    row_count: int


class QuantityList(BaseModel):
    range: QuantityRange
    rows: list[Quantity]
    # The sum of the rows' amounts per unit; rows without a unit are summed under "(unknown)".
    totals_by_unit: dict[str, float]
    row_count: int
    # True when more rows matched than one response carries; narrow the filters to see the rest.
    truncated: bool
    # Budget against actual, by contract value (api/services/budget_rollup.py). Never cut short by truncated.
    pay_items: list[PayItemRollup]
    totals_by_budget_code: list[BudgetCodeTotal]
    project_total: ProjectTotal
    unbudgeted_total: UnbudgetedTotal


class QuantityListResponse(BaseModel):
    status: str
    message: str
    data: QuantityList
