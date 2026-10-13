from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ContractItem(BaseModel):
    contract_item_id: UUID
    project_id: str
    # Only on an item that came from the spec_items catalog.
    spec_item_id: Optional[UUID] = None
    budget_code: str
    bid_quantity: float
    bid_unit_price: float
    # The row's own; pay_unit is its unit column, under the name the pay-item picker reads.
    item_no: str
    description: str
    spec_section: Optional[str] = None
    pay_unit: str
    created_at: datetime
    updated_at: datetime


class ContractItemListResponse(BaseModel):
    status: str
    message: str
    data: list[ContractItem]
