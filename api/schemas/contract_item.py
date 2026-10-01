from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ContractItem(BaseModel):
    contract_item_id: UUID
    project_id: str
    spec_item_id: UUID
    budget_code: str
    bid_quantity: float
    bid_unit_price: float
    # From the joined spec item, so the pay-item picker needs no second request.
    item_no: str
    description: str
    spec_section: str
    pay_unit: str
    created_at: datetime
    updated_at: datetime


class ContractItemListResponse(BaseModel):
    status: str
    message: str
    data: list[ContractItem]
