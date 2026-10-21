"""
A pay item entered by hand on an imported site (Slice V1): a contract item of the IDR's project, a quantity and a note;
or an override of what the site's flags and curb segments come to for the pay item (overrides_auto, Slice X.1).
Also the IDR's pay-item sources: each pay item's total from its flags, curb segments, sites and reports.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The longest note on a site pay item (the table's CHECK holds the same)
MAX_NOTE = 2000
# A quantity's ceiling, as a pay item's bid quantity has
MAX_QUANTITY = Decimal(10) ** 15


def _note(value: Any) -> Any:
    """
    Clean a note: trimmed, blank is None, at most MAX_NOTE characters.
    Takes the note as sent.
    Returns it cleaned; raises ValueError when it is too long.
    """
    if not isinstance(value, str):
        return value
    text = value.strip() or None
    if text is not None and len(text) > MAX_NOTE:
        raise ValueError(f"note can be at most {MAX_NOTE} characters")
    return text


class SitePayItemCreate(BaseModel):
    """
    A pay item being entered on a site: the contract item, its quantity (above 0) and an optional note; or, with
    overrides_auto, the quantity (0 or more) that replaces what the site's flags and curb segments come to for it.
    """

    model_config = ConfigDict(extra="forbid")

    contract_item_id: UUID
    quantity: Decimal = Field(ge=0, lt=MAX_QUANTITY, allow_inf_nan=False)
    note: Optional[str] = None
    overrides_auto: bool = False

    @model_validator(mode="after")
    def _above_zero(self) -> "SitePayItemCreate":
        """
        Refuse a quantity of 0 on a pay item that isn't an override.
        Takes the parsed body.
        Returns it; raises ValueError for 0 without overrides_auto.
        """
        if not self.overrides_auto and self.quantity <= 0:
            raise ValueError("quantity must be above 0")
        return self

    @field_validator("note", mode="before")
    @classmethod
    def _clean_note(cls, value: Any) -> Any:
        """
        Trim the note; blank is None.
        Takes the note as sent.
        Returns it cleaned; raises ValueError when it is too long.
        """
        return _note(value)


class SitePayItemUpdate(BaseModel):
    """A site pay item changed: its quantity (0 only on an override; the route checks), its note (null clears it), or both; at least one."""

    model_config = ConfigDict(extra="forbid")

    quantity: Optional[Decimal] = Field(default=None, ge=0, lt=MAX_QUANTITY, allow_inf_nan=False)
    note: Optional[str] = None

    @field_validator("note", mode="before")
    @classmethod
    def _clean_note(cls, value: Any) -> Any:
        """
        Trim the note; blank is None.
        Takes the note as sent.
        Returns it cleaned; raises ValueError when it is too long.
        """
        return _note(value)

    @model_validator(mode="after")
    def _something(self) -> "SitePayItemUpdate":
        """
        Refuse a change that names nothing, or a null quantity.
        Takes the parsed body.
        Returns it; raises ValueError when neither field was sent or the quantity is null.
        """
        if not self.model_fields_set:
            raise ValueError("Send a quantity, a note or both")
        if "quantity" in self.model_fields_set and self.quantity is None:
            raise ValueError("quantity can't be cleared; remove the pay item instead")
        return self


class SitePayItemOut(BaseModel):
    """A site pay item: its row of icid.idr_report_site_pay_items and its contract item's number, budget code, description and unit."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    report_site_id: UUID
    contract_item_id: UUID
    quantity: float
    note: Optional[str] = None
    added_at: datetime
    added_by: UUID
    overrides_auto: bool = False
    pay_item_ref: str
    budget_code: str
    description: str
    unit: str


class SitePayItemResponse(BaseModel):
    status: str
    message: str
    data: SitePayItemOut


class OverrideAuditOut(BaseModel):
    """The latest override change on a site's pay item (Slice X.1): its action, its words, when and by whom."""

    model_config = ConfigDict(extra="ignore")

    contract_item_id: UUID
    action: str
    text: Optional[str] = None
    at: datetime
    actor_name: Optional[str] = None


class SitePayItemListResponse(BaseModel):
    status: str
    message: str
    data: list[SitePayItemOut]
    audit: list[OverrideAuditOut] = []


class SourceLine(BaseModel):
    """One source's part of a pay item's total: its amount, and where it comes from (a report, or a site)."""

    amount: float
    report_id: Optional[UUID] = None
    site_id: Optional[UUID] = None
    property_id: Optional[UUID] = None
    label: Optional[str] = None
    overrides_auto: Optional[bool] = None


class PayItemSources(BaseModel):
    """A pay item of an IDR and its total, with what each source adds: flags, curb segments, sites and reports (by hand)."""

    pay_item_ref: Optional[str] = None
    budget_code: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    total: float
    flags: float
    segments: float
    sites: float
    manual: float
    site_lines: list[SourceLine]
    manual_lines: list[SourceLine]


class PayItemSourcesData(BaseModel):
    idr_id: UUID
    report_id: Optional[UUID] = None
    frozen: bool
    pay_items: list[PayItemSources]


class PayItemSourcesResponse(BaseModel):
    status: str
    message: str
    data: PayItemSourcesData
