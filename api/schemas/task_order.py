from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from api.schemas.property import PropertyOut


class TaskOrderCreate(BaseModel):
    """A task order an admin is adding to a project. The route trims the text; a malformed date is a 422."""

    task_order_number: str
    issued_date: Optional[date] = None
    estimate_notes: Optional[str] = None


class TaskOrderUpdate(BaseModel):
    """
    What an admin is changing on a task order. Any field may be left out; a null task_order_number counts as left
    out, while a null issued_date or estimate_notes (or blank notes) clears it.
    """

    task_order_number: Optional[str] = None
    issued_date: Optional[date] = None
    estimate_notes: Optional[str] = None


class TaskOrderBase(BaseModel):
    """A task order's own columns: a row of icid.task_orders, deleted_at and deleted_by None for one that isn't deleted."""

    id: UUID
    project_id: str
    task_order_number: str
    issued_date: Optional[date] = None
    estimate_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class TaskOrderListItem(TaskOrderBase):
    """
    A task order in a project's list: how many of its bundled properties aren't deleted, in all and of each kind
    (sidewalks, ped ramps), not the properties.
    """

    property_count: int
    sidewalk_count: int = 0
    ped_ramp_count: int = 0


class TaskOrderDetail(TaskOrderBase):
    """One task order with its bundled properties, those not deleted, by address."""

    properties: list[PropertyOut]


class TaskOrderResponse(BaseModel):
    status: str
    message: str
    data: TaskOrderDetail


class TaskOrderListResponse(BaseModel):
    status: str
    message: str
    data: list[TaskOrderListItem]


class TaskOrderConflict(BaseModel):
    """The 409 body when a project already has a task order with the number: the reason, and that task order."""

    detail: str
    existing_task_order_id: UUID
