from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SpecItem(BaseModel):
    spec_item_id: UUID
    item_no: str
    description: str
    spec_section: str
    pay_unit: str
    created_at: datetime
    updated_at: datetime
