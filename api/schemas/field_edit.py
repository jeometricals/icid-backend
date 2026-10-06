from datetime import datetime
from typing import Any, Optional, Union
from uuid import UUID

from pydantic import BaseModel


class FieldEdit(BaseModel):
    """One edit a reviewer made on an IDR: which field, what it held, what they put, and who they are."""

    edit_id: UUID
    report_id: Optional[UUID] = None   # None for a header field
    field_path: str
    edit_type: str                     # 'field_change', 'pay_item_revision', 'pay_item_add' or 'pay_item_approve'
    old_value: Any = None              # None for 'pay_item_add', where there was nothing
    new_value: Any = None
    editor_uuid: UUID
    editor_stage: str                  # 'stage1' or 'stage2'
    edited_at: datetime
    editor_name: Optional[str] = None
    editor_initials: str = ""


class FieldEditRequest(BaseModel):
    """A reviewer's change to one field: a header field (no report_id) or a field of one report."""

    report_id: Optional[UUID] = None
    field_path: str
    new_value: Any = None


class PayItemRevision(BaseModel):
    """A reviewer's new quantity for one pay item."""

    revised_quantity: Union[str, int, float]


class PayItemAdd(BaseModel):
    """A pay item a reviewer adds to one report."""

    report_id: UUID
    item_no: str = ""
    budget_code: str = ""
    quantity: Union[str, int, float]
    unit: str = ""
    description: str = ""


class UntouchedPayItem(BaseModel):
    """A pay item the stage's reviewer has not yet approved, revised or added."""

    pay_item_id: str
    report_id: UUID
    item_no: Optional[str] = None
    budget_code: Optional[str] = None


class PayItemsUntouched(BaseModel):
    """Why a stage can't be approved yet: the pay items still waiting on its reviewer."""

    detail: str
    untouched: list[UntouchedPayItem]
