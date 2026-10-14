from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

# What a property can be; the table's CHECK holds the same three
PropertyType = Literal["1_3_family", "4_plus_family", "expedited"]


class PropertyCreate(BaseModel):
    """A property an admin is adding to a project. The route trims the text; an unknown property_type is a 422."""

    bbl: str
    address: str
    property_type: PropertyType
    notes: Optional[str] = None


class PropertyUpdate(BaseModel):
    """
    What an admin is changing on a property. Any field may be left out; a null counts as left out, except for
    notes, where a null or a blank clears them.
    """

    bbl: Optional[str] = None
    address: Optional[str] = None
    property_type: Optional[PropertyType] = None
    notes: Optional[str] = None


class PropertyOut(BaseModel):
    """A property: a row of icid.properties, deleted_at and deleted_by None for one that isn't deleted."""

    id: UUID
    project_id: str
    bbl: str
    address: str
    property_type: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class PropertyResponse(BaseModel):
    status: str
    message: str
    data: PropertyOut


class PropertyListResponse(BaseModel):
    status: str
    message: str
    data: list[PropertyOut]


class PropertyConflict(BaseModel):
    """The 409 body when a project already has a property with the BBL: the reason, and that property."""

    detail: str
    existing_property_id: UUID
