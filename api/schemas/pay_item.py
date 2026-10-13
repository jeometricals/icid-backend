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


class PayItemValues(BaseModel):
    """A pay item's values, as a row of an imported CSV gives them once checked."""

    pay_item_ref: str
    description: str
    unit: str
    quantity: float
    unit_price: float
    budget_code: str
    spec_section: Optional[str] = None


class PayItemImportRow(PayItemValues):
    """A row of the file that would be a new pay item. row is its number in the file; the header is row 1."""

    row: int


class PayItemImportOverwrite(BaseModel):
    """A row of the file whose ref and budget code the project already has: the item as it is, and as the row would leave it."""

    row: int
    pay_item_ref: str
    budget_code: str
    existing: PayItemOut
    incoming: PayItemValues


class PayItemImportError(BaseModel):
    """A row of the file that can't be imported: why, and its cells as typed."""

    row: int
    reason: str
    raw: dict[str, str]


class PayItemImportPreview(BaseModel):
    """What importing a file would do. The three lists are each in file order."""

    parsed_rows: int
    new_count: int
    overwrite_count: int
    error_count: int
    # Columns of the file that aren't pay-item columns, as their headers are written.
    ignored_columns: list[str]
    new: list[PayItemImportRow]
    overwrites: list[PayItemImportOverwrite]
    errors: list[PayItemImportError]


class PayItemImportPreviewResponse(BaseModel):
    """The answer to an import preview."""

    status: str
    message: str
    data: PayItemImportPreview


class PayItemImportResult(BaseModel):
    """How many pay items an import added and how many it overwrote."""

    inserted: int
    updated: int


class PayItemImportCommitResponse(BaseModel):
    """The answer to an import."""

    status: str
    message: str
    data: PayItemImportResult


class PayItemImportRefused(BaseModel):
    """The 400 body when a file has rows that can't be imported: nothing was written."""

    detail: str
    error_count: int
    errors: list[PayItemImportError]
