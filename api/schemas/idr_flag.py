from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, computed_field

from api.schemas.property import display_address_of

# Where a flag stands with an IDR: attributed to it (counted when it is approved), excluded by an override, claimed by
# it (approved), or claimed by another approved IDR (not attributable)
FlagState = Literal["attributed", "excluded", "claimed", "claimed_elsewhere"]


class IdrFlag(BaseModel):
    """
    A flag as an IDR lists it: where it is (its site and page), its pay item and unit, its quantity in square feet
    (quantity_sf: its override, or else its net area) and in that unit (amount; None without a pay item, or for a
    unit a flag can't be measured in), its state, the override that put it there (override), and who claimed it.
    """

    flag_id: UUID
    pir_id: UUID
    pir_page_id: Optional[UUID] = None
    page_number: Optional[int] = None
    display_order: int
    shape: str
    surveyed_thickness: str
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
    quantity_sf: float
    amount: Optional[float] = None
    state: FlagState
    override: Optional[Literal["exclude", "include"]] = None
    on_imported_site: bool
    claimed_by_idr_id: Optional[UUID] = None
    claimed_by_idr_number: Optional[str] = None
    is_constructed: bool
    constructed_on: Optional[date] = None
    deleted_at: Optional[datetime] = None

    @computed_field
    @property
    def display_address(self) -> str:
        """
        Label the flag's site as a property is labelled.
        Takes the flag.
        Returns the label.
        """
        return display_address_of(self.property_kind, self.address, self.intersection_street_1,
                                  self.intersection_street_2, self.quadrant, self.facing_street)


class IdrFlagPayItem(BaseModel):
    """
    One line of an IDR's flag rollup: a pay item and what its flags come to in its unit (2 decimal places). Before
    approval it is summed from the attributed flags (pay_item_id and flag_count set); once approved it is the
    from_flags quantity row frozen at approval (neither set).
    """

    pay_item_id: Optional[UUID] = None
    pay_item_ref: Optional[str] = None
    budget_code: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    amount: float
    flag_count: Optional[int] = None


class IdrFlagCounts(BaseModel):
    """How many of the flags an IDR lists are attributed (or, approved, claimed), excluded, claimed elsewhere, and in all."""

    attributed: int
    excluded: int
    claimed_elsewhere: int
    total: int


class IdrFlags(BaseModel):
    """
    An IDR's flags: frozen once it is approved (the flags it claimed and the rollup it wrote), live before. unmapped
    counts the attributed (or claimed) flags that add nothing to the rollup: without a pay item, or in a unit a flag
    can't be measured in.
    """

    idr_id: UUID
    status: str
    task_order_id: Optional[UUID] = None
    frozen: bool
    counts: IdrFlagCounts
    flags: list[IdrFlag]
    pay_items: list[IdrFlagPayItem]
    unmapped: int


class IdrFlagsResponse(BaseModel):
    status: str
    message: str
    data: IdrFlags
