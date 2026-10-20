from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, computed_field

from api.schemas.idr_flag import FlagState, IdrFlagCounts
from api.schemas.property import display_address_of


class IdrSegment(BaseModel):
    """
    A curb segment as an IDR lists it: where it is (its site and page), its kind and length (feet; None while its page
    has no scale), its pay item and what it comes to in that unit (amount: its length for a pay item in LF; None
    without a pay item or a length), its state, the override that put it there, and who claimed it.
    """

    segment_id: UUID
    pir_id: UUID
    pir_page_id: UUID
    page_number: int
    segment_type: str
    length_ft: Optional[float] = None
    note: Optional[str] = None
    property_id: UUID
    property_kind: str
    boro: Optional[str] = None
    block: Optional[str] = None
    lot: Optional[str] = None
    address: Optional[str] = None
    intersection_street_1: Optional[str] = None
    intersection_street_2: Optional[str] = None
    quadrant: Optional[str] = None
    facing_street: Optional[str] = None
    pay_item_id: Optional[UUID] = None
    pay_item_ref: Optional[str] = None
    budget_code: Optional[str] = None
    pay_item_description: Optional[str] = None
    unit: Optional[str] = None
    amount: Optional[float] = None
    state: FlagState
    override: Optional[Literal["exclude", "include"]] = None
    on_imported_site: bool
    claimed_by_idr_id: Optional[UUID] = None
    claimed_by_idr_number: Optional[str] = None
    constructed_on: Optional[date] = None
    deleted_at: Optional[datetime] = None

    @computed_field
    @property
    def display_address(self) -> str:
        """
        Label the segment's site as a property is labelled.
        Takes the segment.
        Returns the label.
        """
        return display_address_of(self.property_kind, self.address, self.intersection_street_1,
                                  self.intersection_street_2, self.quadrant, self.facing_street)


class IdrSegmentPayItem(BaseModel):
    """
    One line of an IDR's segment rollup: a pay item and the length its segments come to (LF, 2 decimal places). Before
    approval it is summed from the attributed segments (pay_item_id and segment_count set); once approved it is the
    from_segments quantity row frozen at approval (neither set).
    """

    pay_item_id: Optional[UUID] = None
    pay_item_ref: Optional[str] = None
    budget_code: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    amount: float
    segment_count: Optional[int] = None


class IdrSegments(BaseModel):
    """
    An IDR's curb segments: frozen once it is approved (the segments it claimed and the rollup it wrote), live before.
    unmapped counts the attributed (or claimed) segments that add nothing to the rollup: without a pay item in LF, or
    without a length.
    """

    idr_id: UUID
    status: str
    task_order_id: Optional[UUID] = None
    frozen: bool
    counts: IdrFlagCounts
    segments: list[IdrSegment]
    pay_items: list[IdrSegmentPayItem]
    unmapped: int


class IdrSegmentsResponse(BaseModel):
    status: str
    message: str
    data: IdrSegments
