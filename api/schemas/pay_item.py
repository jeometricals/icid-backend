from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PayItemCreate(BaseModel):
    """
    A pay item an admin is adding to a project. The route checks every value itself, so a bad one is a 400.
    """

    pay_item_ref: str
    description: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    budget_code: str
    spec_section: Optional[str] = None


class PayItemUpdate(BaseModel):
    """
    What an admin is changing on a pay item. Any field may be left out; a null counts as left out, except for
    spec_section, where a null or a blank clears it.
    """

    pay_item_ref: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    budget_code: Optional[str] = None
    spec_section: Optional[str] = None


class PayItemOut(BaseModel):
    """A project's pay item, as an admin sees it: a row of icid.contract_items."""

    pay_item_uuid: UUID
    pay_item_ref: str
    description: str
    unit: str
    quantity: float
    unit_price: float
    budget_code: str
    spec_section: Optional[str] = None


class PayItemResponse(BaseModel):
    """One pay item."""

    data: PayItemOut


class PayItemListResponse(BaseModel):
    """Every pay item of a project."""

    status: str
    message: str
    data: list[PayItemOut]


class PayItemClearResult(BaseModel):
    """How many pay items a clear deleted."""

    deleted_count: int


class PayItemClearResponse(BaseModel):
    """The answer to clearing a project's pay items."""

    status: str
    message: str
    data: PayItemClearResult


class PayItemConflict(BaseModel):
    """The 409 body when the item number and budget code are taken: the reason, and the item that has them."""

    detail: str
    existing_pay_item_uuid: UUID
