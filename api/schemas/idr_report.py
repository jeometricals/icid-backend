from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel


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
