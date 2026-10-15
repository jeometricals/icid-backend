from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.features import (
    create_feature,
    list_features_for_page,
    list_features_for_pir,
    soft_delete_feature,
    update_feature,
)
from api.queries.flags import get_flag
from api.schemas.auth import UserOut
from api.schemas.feature import FeatureCreate, FeatureListResponse, FeatureOut, FeatureResponse, FeatureUpdate
from api.services.auth import current_user, feature_member, no_demo_pir_writes, pir_member, pir_page_member
from api.services.flag_area import compute_area
from api.v1._shared import (
    PAGE_CHAIN,
    PAGE_DELETED,
    PAGE_PARENTS,
    changes_sent,
    check_pay_item,
    refuse_deleted_chain,
    refuse_deleted_for_member,
    require_dimensions_with_shape,
    validated,
)

# A page's features under /v1/pir-pages, a PIR's under /v1/pirs, and one feature by its id. pir_page_member,
# pir_member and feature_member keep demo users to their own project; demo users only read.
page_router = APIRouter(prefix="/v1/pir-pages", tags=["Features"], dependencies=[Depends(current_user)])
pir_router = APIRouter(prefix="/v1/pirs", tags=["Features"], dependencies=[Depends(current_user)])
router = APIRouter(prefix="/v1/features", tags=["Features"], dependencies=[Depends(current_user)])

# The fields a PATCH can clear with a null; for any other, a null counts as not sent
CLEARABLE = {"shape", "dimensions", "label", "pay_item_id", "flag_id", "reported_quantity"}
FLAG_ELSEWHERE = "Flag is not a live flag on this page"


def _feature_writer_on_page(found: dict = Depends(pir_page_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for adding a feature to a page: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the page (loaded and checked by pir_page_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the page dict; raises 403 for a demo user.
    """
    return found


def _writable_feature(found: dict = Depends(feature_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for the writes to one feature: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the feature (loaded and checked by feature_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the feature dict; raises 403 for a demo user.
    """
    return found


def _area(feature: FeatureCreate) -> Optional[Any]:
    """
    A feature's computed area.
    Takes the checked feature.
    Returns its shape's area in square feet, or None when it has no shape.
    """
    return None if feature.shape is None else compute_area(feature.shape, feature.dimensions)


def _check_flag(pir_page_id: UUID, flag_id: Optional[UUID]) -> None:
    """
    Refuse a flag that isn't a live flag on the page.
    Takes the page's id and the flag's id (None for none).
    Returns nothing; raises 400 otherwise.
    """
    if flag_id is None:
        return
    flag = get_flag(flag_id)
    if flag is None or flag["deleted_at"] is not None or flag["pir_page_id"] != pir_page_id:
        raise HTTPException(status_code=400, detail=FLAG_ELSEWHERE)


def _response(row: dict[str, Any], message: str) -> FeatureResponse:
    """
    Wrap one feature in the response envelope.
    Takes the feature dict and the message.
    Returns the FeatureResponse.
    """
    return FeatureResponse(status="success", message=message, data=FeatureOut.model_validate(row))


def _list_response(rows: Optional[list[dict[str, Any]]]) -> FeatureListResponse:
    """
    Wrap a list of features in the response envelope.
    Takes the feature dicts (None when they couldn't be read).
    Returns the FeatureListResponse; raises 500 for None.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch features")
    return FeatureListResponse(
        status="success", message="Features retrieved", data=[FeatureOut.model_validate(row) for row in rows]
    )


@page_router.get("/{pir_page_id}/features", response_model=FeatureListResponse)
def list_page_features(found: dict = Depends(pir_page_member)) -> FeatureListResponse:
    """
    List the live features drawn on one page, in the order they were drawn. For a user on its project, or an admin.
    Takes the page (loaded and checked by pir_page_member from the pir_page_id path parameter).
    Returns a FeatureListResponse; raises 403 for a user who isn't on its project, 404 for an unknown page and 500 if the list can't be read.
    """
    return _list_response(list_features_for_page(found["pir_page_id"]))


@pir_router.get("/{pir_id}/features", response_model=FeatureListResponse)
def list_pir_features(found: dict = Depends(pir_member)) -> FeatureListResponse:
    """
    List a PIR's live features across its live pages, by page. For a user on its project, or an admin.
    Takes the PIR (loaded and checked by pir_member from the pir_id path parameter).
    Returns a FeatureListResponse; raises 403 for a user who isn't on its project, 404 for an unknown PIR and 500 if the list can't be read.
    """
    return _list_response(list_features_for_pir(found["id"]))


@page_router.post("/{pir_page_id}/features", response_model=FeatureResponse, status_code=201)
def create_page_feature(
    body: FeatureCreate, page: dict = Depends(_feature_writer_on_page), user: UserOut = Depends(current_user)
) -> FeatureResponse:
    """
    Add a feature to a page, created by the signed-in user, its area computed from its shape (when it has one). For a user on the page's project, or an admin; not a demo user. Nobody adds to a replaced page; only an admin adds to a page of a soft-deleted PIR or property.
    Takes the feature in the body (checked by FeatureCreate), the page (loaded by _feature_writer_on_page) and the user.
    Returns 201 with a FeatureResponse; raises 400 for a deleted page, PIR or property, a pay item of another project or a flag that isn't a live flag on the page, 403 for a demo user or a user who isn't on the project, 404 for an unknown page and 422 for values that break a rule.
    """
    if page["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail=PAGE_DELETED)
    refuse_deleted_chain(page, user, PAGE_CHAIN)
    check_pay_item(page["project_id"], body.pay_item_id)
    _check_flag(page["pir_page_id"], body.flag_id)

    row = create_feature(page["pir_page_id"], user.uuid, body.model_dump(), _area(body))

    if row is None:
        # The page replaced, the pay item moved or the flag deleted between the checks and the statement
        check_pay_item(page["project_id"], body.pay_item_id)
        _check_flag(page["pir_page_id"], body.flag_id)
        raise HTTPException(status_code=400, detail=PAGE_DELETED)

    return _response(row, "Feature created")


@router.get("/{feature_id}", response_model=FeatureResponse)
def get_feature_record(found: dict = Depends(feature_member)) -> FeatureResponse:
    """
    Fetch one feature, soft-deleted or not. For a user on its project, or an admin.
    Takes the feature (loaded and checked by feature_member from the feature_id path parameter).
    Returns a FeatureResponse; raises 403 for a user who isn't on its project and 404 for an unknown feature.
    """
    return _response(found, "Feature retrieved")


@router.patch("/{feature_id}", response_model=FeatureResponse)
def update_feature_record(
    feature_id: UUID, body: FeatureUpdate, found: dict = Depends(_writable_feature), user: UserOut = Depends(current_user)
) -> FeatureResponse:
    """
    Change some of a feature. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted feature, or one on a soft-deleted page, PIR or property. The body is merged over the stored feature and the whole is checked as a new feature is; its area is computed again. A null clears shape, dimensions, label, pay_item_id, flag_id or reported_quantity; on anything else it counts as not sent.
    Takes the feature's id as a path parameter, the fields to change in the body, the feature (loaded by _writable_feature) and the user.
    Returns a FeatureResponse with the feature as it now is; raises 400 when nothing is sent, the feature or something above it is deleted (a user who isn't an admin), the pay item is another project's or the flag isn't a live flag on its page, 403 for a demo user or a user who isn't on its project, 404 for an unknown feature and 422 when the merged feature breaks a rule.
    """
    refuse_deleted_for_member(found, user, "Feature is deleted")
    refuse_deleted_chain(found, user, PAGE_PARENTS)

    changes = changes_sent(body, CLEARABLE)
    require_dimensions_with_shape(changes, found["shape"])
    merged = validated(FeatureCreate, {**{field: found[field] for field in FeatureCreate.model_fields}, **changes})
    check_pay_item(found["project_id"], merged.pay_item_id)
    _check_flag(found["pir_page_id"], merged.flag_id)

    row = update_feature(feature_id, merged.model_dump(), _area(merged))

    if row is None:
        check_pay_item(found["project_id"], merged.pay_item_id)
        _check_flag(found["pir_page_id"], merged.flag_id)
        raise HTTPException(status_code=500, detail="Failed to update feature")

    return _response(row, "Feature updated")


@router.delete("/{feature_id}", status_code=204, response_class=Response)
def delete_feature_record(
    feature_id: UUID, found: dict = Depends(_writable_feature), user: UserOut = Depends(current_user)
) -> None:
    """
    Delete a feature: a soft delete, with when and by whom. For a user on its project, or an admin; not a demo user. Only an admin deletes one on a soft-deleted page, PIR or property. Deleting one that is already deleted succeeds and changes nothing.
    Takes the feature's id as a path parameter, the feature (loaded by _writable_feature) and the user.
    Returns nothing (204); raises 400 for something deleted above it (a user who isn't an admin), 403 for a demo user or a user who isn't on its project, and 404 for an unknown feature.
    """
    refuse_deleted_chain(found, user, PAGE_PARENTS)
    if found["deleted_at"] is None:
        soft_delete_feature(feature_id, user.uuid)  # None: deleted by someone else meanwhile, the same outcome
