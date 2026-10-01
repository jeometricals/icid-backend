from fastapi import APIRouter, HTTPException

from api.queries.contract_items import list_contract_items_for_project
from api.schemas.contract_item import ContractItem, ContractItemListResponse

router = APIRouter(prefix="/v1/contract_items", tags=["Contract Items"])


@router.get("/", response_model=ContractItemListResponse)
def list_contract_items(project_id: str) -> ContractItemListResponse:
    """
    Return a project's contract items (its Schedule of Bid Items), each with its spec item's details.
    Takes the project id as the project_id query parameter.
    Returns a ContractItemListResponse; data is empty for a project with no contract items.
    """
    rows = list_contract_items_for_project(project_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch contract items")

    return ContractItemListResponse(
        status="success",
        message=f"Contract items for project {project_id}",
        data=[ContractItem(**row) for row in rows],
    )
