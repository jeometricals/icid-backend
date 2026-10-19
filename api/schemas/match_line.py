from datetime import datetime
import math
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# A match line's label as DOT letters them: A..Z, then AA..ZZ
LABEL_PATTERN = r"^[A-Z]{1,2}$"


class MatchLinePoint(BaseModel):
    """An endpoint of a match line, in its page's native pixels (x right, y down from the stored PNG's top-left)."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, value: float) -> float:
        """
        Refuse a coordinate that isn't a finite number.
        Takes the value.
        Returns it; raises ValueError for NaN or infinity.
        """
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value


def _clean_label(value: Any) -> Any:
    """
    Put a label in the form it is stored in: trimmed and in capitals.
    Takes the value sent.
    Returns it cleaned (anything that isn't text as sent, for the type check to refuse).
    """
    return value.strip().upper() if isinstance(value, str) else value


class MatchLineCreate(BaseModel):
    """
    A match line being drawn on a page (or imported): its label, trimmed and put in capitals, A..Z or AA..ZZ, and its
    two endpoints, which must differ. The route and the Smart PIR import check it the same way.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(pattern=LABEL_PATTERN)
    points: list[MatchLinePoint] = Field(min_length=2, max_length=2)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: Any) -> Any:
        """
        Trim a label and put it in capitals before it is checked.
        Takes the value sent.
        Returns it cleaned.
        """
        return _clean_label(value)

    @field_validator("points")
    @classmethod
    def _apart(cls, points: list[MatchLinePoint]) -> list[MatchLinePoint]:
        """
        Refuse a line whose two endpoints are the same point.
        Takes the endpoints.
        Returns them; raises ValueError when they coincide.
        """
        if points[0].x == points[1].x and points[0].y == points[1].y:
            raise ValueError("a match line's two points must differ")
        return points

    def columns(self) -> dict[str, Any]:
        """
        The line as the table stores it.
        Takes the line.
        Returns {label, x1, y1, x2, y2}.
        """
        first, second = self.points
        return {"label": self.label, "x1": first.x, "y1": first.y, "x2": second.x, "y2": second.y}


class MatchLineUpdate(BaseModel):
    """What is changing on a match line: its label, its endpoints, or both. A field left out keeps its value."""

    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = None
    points: Optional[list[MatchLinePoint]] = None

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: Any) -> Any:
        """
        Trim a label and put it in capitals; the merged line is checked as MatchLineCreate.
        Takes the value sent.
        Returns it cleaned.
        """
        return _clean_label(value)


class MatchLineOut(BaseModel):
    """A match line: a row of icid.pir_match_lines, its endpoints as points, with its page's number and its page's PIR, property and project."""

    match_line_id: UUID
    pir_page_id: UUID
    page_number: int
    pir_id: UUID
    property_id: UUID
    project_id: str
    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None

    @computed_field
    @property
    def points(self) -> list[dict[str, float]]:
        """
        The line's two endpoints, as a body sends them.
        Takes the line.
        Returns [{x, y}, {x, y}].
        """
        return [{"x": self.x1, "y": self.y1}, {"x": self.x2, "y": self.y2}]


class MatchLineResponse(BaseModel):
    status: str
    message: str
    data: MatchLineOut


class MatchLineListResponse(BaseModel):
    status: str
    message: str
    data: list[MatchLineOut]
