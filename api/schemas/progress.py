from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ProgressBucket(BaseModel):
    """
    What a pay item's flags in one state come to: their quantity in its unit (unrounded), how many there are, and
    their value at its unit price (2 decimal places).
    """

    quantity: float
    flag_count: int
    value: float


class ProgressPayItem(BaseModel):
    """
    A pay item a flag can fill (listed when a flag in scope is on it, or for include_empty), and its flags split by
    state: approved (claimed by an approved IDR), in flight (attributed to an open IDR), unclaimed, and the three
    together (live_total). approved_pct is approved over the total, to 1 decimal place; None when the total is 0.
    """

    pay_item_id: UUID
    item_no: str
    budget_code: str
    description: str
    unit: str
    unit_price: float
    approved: ProgressBucket
    in_flight: ProgressBucket
    unclaimed: ProgressBucket
    live_total: ProgressBucket
    approved_pct: Optional[float] = None


class ProgressOverall(BaseModel):
    """The approved and total values across every pay item, and the one over the other (None when the total is 0)."""

    approved_value: float
    live_total_value: float
    approved_pct: Optional[float] = None


class UnmappedFlags(BaseModel):
    """How many live flags of a property come to nothing in a pay item: none mapped, or a unit a flag can't measure."""

    property_id: UUID
    display_address: str
    flag_count: int


class ProgressScope(BaseModel):
    """What a rollup covers: a project, narrowed to a task order's properties and to one property when given."""

    project_id: str
    task_order_id: Optional[UUID] = None
    property_id: Optional[UUID] = None


class PropertyProgress(BaseModel):
    """One property's rollup, in a task order's."""

    property_id: UUID
    display_address: str
    pay_items: list[ProgressPayItem]
    unmapped_flag_count: int
    overall: ProgressOverall


class ProgressRollup(BaseModel):
    """A project's progress from its flags, in the scope asked for, with its unmapped flags per property."""

    scope: ProgressScope
    pay_items: list[ProgressPayItem]
    unmapped_flag_count: int
    unmapped_by_property: list[UnmappedFlags]
    overall: ProgressOverall


class TaskOrderProgressRollup(ProgressRollup):
    """
    A task order's progress: as a project's, with each of its live bundled properties' own rollup, how many there
    are (site_count) and how many have a flag an approved IDR claimed (sites_started).
    """

    properties: list[PropertyProgress]
    site_count: int
    sites_started: int


class ProgressRollupResponse(BaseModel):
    status: str
    message: str
    data: ProgressRollup


class TaskOrderProgressRollupResponse(BaseModel):
    status: str
    message: str
    data: TaskOrderProgressRollup
