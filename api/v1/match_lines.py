from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.match_lines import (
    create_match_line,
    list_match_lines_for_page,
    list_match_lines_for_pir,
    soft_delete_match_line,
    update_match_line,
)
from api.schemas.auth import UserOut
from api.schemas.match_line import (
    MatchLineCreate,
    MatchLineListResponse,
    MatchLineOut,
    MatchLineResponse,
    MatchLineUpdate,
)
from api.services.auth import current_user, match_line_member, no_demo_pir_writes, pir_member, pir_page_member
from api.v1._shared import PAGE_CHAIN, PAGE_DELETED, PAGE_PARENTS, refuse_deleted_chain, refuse_deleted_for_member, validated

# A page's match lines under /v1/pir-pages, a PIR's under /v1/pirs, and one line by its id (R1h.1a). pir_page_member,
# pir_member and match_line_member keep demo users to their own project; demo users only read.
page_router = APIRouter(prefix="/v1/pir-pages", tags=["Match lines"], dependencies=[Depends(current_user)])
pir_router = APIRouter(prefix="/v1/pirs", tags=["Match lines"], dependencies=[Depends(current_user)])
router = APIRouter(prefix="/v1/match-lines", tags=["Match lines"], dependencies=[Depends(current_user)])


def _writer_on_page(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for drawing a match line on a page: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user.
    """
    return found


def _writable_match_line(found: dict = Depends(match_line_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for the writes to one match line: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the line (loaded and checked by match_line_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the line dict; raises 403 for a demo user.
    """
    return found


def _response(row: dict[str, Any], message: str) -> MatchLineResponse:
    """
    Wrap one match line in the response envelope.
    Takes the line dict and the message.
    Returns the MatchLineResponse.
    """
    return MatchLineResponse(status="success", message=message, data=MatchLineOut.model_validate(row))


def _list_response(rows: Optional[list[dict[str, Any]]]) -> MatchLineListResponse:
    """
    Wrap a list of match lines in the response envelope.
    Takes the line dicts (None when they couldn't be read).
    Returns the MatchLineListResponse; raises 500 for None.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch match lines")
    return MatchLineListResponse(
        status="success", message="Match lines retrieved", data=[MatchLineOut.model_validate(row) for row in rows]
    )


def refuse_taken_label(pir_id: UUID, pir_page_id: UUID, label: str, match_line_id: Optional[UUID] = None) -> None:
    """
    Refuse a label a page can't take: one another live line of the page has, or one already on two other pages of its PIR.
    Takes the PIR's and the page's ids, the label and the line being changed (None for a new one, which is left out of the count).
    Returns nothing; raises 409 naming where the label is, or 500 when the PIR's lines can't be read.
    """
    lines = list_match_lines_for_pir(pir_id)
    if lines is None:
        raise HTTPException(status_code=500, detail="Failed to fetch match lines")
    others = [line for line in lines if line["label"] == label and line["match_line_id"] != match_line_id]
    if any(line["pir_page_id"] == pir_page_id for line in others):
        raise HTTPException(status_code=409, detail=f'Match line "{label}" is already on this page')
    pages = sorted({line["page_number"] for line in others})
    if len(pages) >= 2:
        raise HTTPException(status_code=409,
                            detail=f'Match line "{label}" is already on pages {pages[0]} and {pages[1]}')


@page_router.get("/{pir_page_id}/match-lines", response_model=MatchLineListResponse)
def list_page_match_lines(found: dict = Depends(pir_page_member)) -> MatchLineListResponse:
    """
    List the live match lines drawn on one page, by label. For a user on its project, or an admin.
    Takes the page (loaded and checked by pir_page_member from the pir_page_id path parameter).
    Returns a MatchLineListResponse; raises 403 for a user who isn't on its project, 404 for an unknown page and 500 if the list can't be read.
    """
    return _list_response(list_match_lines_for_page(found["pir_page_id"]))


@pir_router.get("/{pir_id}/match-lines", response_model=MatchLineListResponse)
def list_pir_match_lines(found: dict = Depends(pir_member)) -> MatchLineListResponse:
    """
    List a PIR's live match lines across its live pages, by page then label. For a user on its project, or an admin.
    Takes the PIR (loaded and checked by pir_member from the pir_id path parameter).
    Returns a MatchLineListResponse; raises 403 for a user who isn't on its project, 404 for an unknown PIR and 500 if the list can't be read.
    """
    return _list_response(list_match_lines_for_pir(found["id"]))


@page_router.post("/{pir_page_id}/match-lines", response_model=MatchLineResponse, status_code=201)
def create_page_match_line(
    body: MatchLineCreate, page: dict = Depends(_writer_on_page), user: UserOut = Depends(current_user)
) -> MatchLineResponse:
    """
    Draw a match line on a page, created by the signed-in user. Its label pairs it with the page that carries the same label in the PIR. For a user on the page's project, or an admin; not a demo user. Nobody adds to a replaced page; only an admin adds to a page of a soft-deleted PIR or property.
    Takes the line in the body (checked by MatchLineCreate: label A..Z or AA..ZZ, two distinct points), the page (loaded by _writer_on_page) and the user.
    Returns 201 with a MatchLineResponse; raises 400 for a deleted page, PIR or property, 403 for a demo user or a user who isn't on the project, 404 for an unknown page, 409 when the page already has the label or it is already on two other pages, and 422 for a value that breaks a rule.
    """
    if page["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(page, user, PAGE_CHAIN)
    refuse_taken_label(page["pir_id"], page["pir_page_id"], body.label)

    row = create_match_line(page["pir_page_id"], user.uuid, body.columns())

    if row is None:
        # The page replaced, or the label taken, between the checks and the statement
        refuse_taken_label(page["pir_id"], page["pir_page_id"], body.label)
        raise HTTPException(status_code=400, detail=PAGE_DELETED)

    return _response(row, "Match line created")


@router.get("/{match_line_id}", response_model=MatchLineResponse)
def get_match_line_record(found: dict = Depends(match_line_member)) -> MatchLineResponse:
    """
    Fetch one match line, soft-deleted or not. For a user on its project, or an admin.
    Takes the line (loaded and checked by match_line_member from the match_line_id path parameter).
    Returns a MatchLineResponse; raises 403 for a user who isn't on its project and 404 for an unknown line.
    """
    return _response(found, "Match line retrieved")


@router.patch("/{match_line_id}", response_model=MatchLineResponse)
def update_match_line_record(
    match_line_id: UUID, body: MatchLineUpdate, found: dict = Depends(_writable_match_line),
    user: UserOut = Depends(current_user),
) -> MatchLineResponse:
    """
    Change a match line's label, its endpoints, or both. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted line, or one on a soft-deleted page, PIR or property. The body is merged over the stored line and the whole is checked as a new line is.
    Takes the line's id as a path parameter, the fields to change in the body, the line (loaded by _writable_match_line) and the user.
    Returns a MatchLineResponse with the line as it now is; raises 400 when nothing is sent or the line or something above it is deleted (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, 404 for an unknown line, 409 when the new label is taken, and 422 when the merged line breaks a rule.
    """
    refuse_deleted_for_member(found, user, "Match line is deleted")
    refuse_deleted_chain(found, user, PAGE_PARENTS)
    sent = body.model_dump(exclude_unset=True, exclude_none=True)
    if not sent:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    stored = {"label": found["label"], "points": [{"x": found["x1"], "y": found["y1"]}, {"x": found["x2"], "y": found["y2"]}]}
    merged = validated(MatchLineCreate, {**stored, **sent})
    refuse_taken_label(found["pir_id"], found["pir_page_id"], merged.label, match_line_id)

    row = update_match_line(match_line_id, merged.columns())

    if row is None:
        refuse_taken_label(found["pir_id"], found["pir_page_id"], merged.label, match_line_id)
        raise HTTPException(status_code=500, detail="Failed to update match line")

    return _response(row, "Match line updated")


@router.delete("/{match_line_id}", status_code=204, response_class=Response)
def delete_match_line_record(
    match_line_id: UUID, found: dict = Depends(_writable_match_line), user: UserOut = Depends(current_user)
) -> None:
    """
    Delete a match line: a soft delete, with when and by whom. For a user on its project, or an admin; not a demo user. Only an admin deletes one on a soft-deleted page, PIR or property. Deleting one that is already deleted succeeds and changes nothing.
    Takes the line's id as a path parameter, the line (loaded by _writable_match_line) and the user.
    Returns nothing (204); raises 400 for something deleted above it (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, and 404 for an unknown line.
    """
    refuse_deleted_chain(found, user, PAGE_PARENTS)
    if found["deleted_at"] is None:
        soft_delete_match_line(match_line_id, user.uuid)  # None: deleted by someone else meanwhile, the same outcome
