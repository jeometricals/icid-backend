import logging
from typing import Annotated, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from api.v1._shared import day, day_range, idr_list_items, refuse_flag_filled_pay_items, report_types_for

from api.queries.idr_reports import (
    DspTakenError,
    create_idr_report,
    delete_idr_report,
    get_dsp_report_id,
    get_general_report_id,
    get_idr_report,
    list_reports_for_idr,
    save_report_data,
)
from api.queries.idrs import (
    create_idr,
    get_idr_by_id,
    get_idr_id_for_day,
    list_idrs,
    set_dismissed_auto_general,
    submit_idr,
    touch_idr,
    update_idr_header,
)
from api.queries.projects import get_project_by_id, is_user_on_project
from api.queries.task_orders import get_task_order
from api.services.attachments import delete_all_storage_files_for_report
from api.services.auth import current_user, demo_idr_fence, require_full_user, require_project_role
from api.services.auto_general import regenerate_auto_general
from api.services.field_edits import field_edits_for
from api.services.signatures import SignatureStorageError, snapshot_signature_for_idr
from api.schemas.auth import UserOut
from api.schemas.idr import (
    Idr,
    IdrConflict,
    IdrCreate,
    IdrHeaderUpdate,
    IdrListResponse,
    IdrResponse,
    IdrStatus,
    IdrWithReports,
    IdrWithReportsResponse,
)
from api.schemas.dsp_report import DspReportError, check_dsp_report
from api.schemas.idr_report import (
    DSP_PARENT_MESSAGE,
    DSP_PARENT_TYPES,
    DSP_TAKEN_MESSAGE,
    IdrReport,
    IdrReportConflict,
    IdrReportCreate,
    IdrReportResponse,
    ReportData,
    ReportType,
)

logger = logging.getLogger(__name__)

# Every route needs a signed-in user (any role); a demo user reaches only their own IDRs
router = APIRouter(prefix="/v1/idrs", tags=["IDRs"], dependencies=[Depends(current_user), Depends(demo_idr_fence)])


def _check_task_order(project_id: str, task_order_id: Optional[UUID]) -> None:
    """
    Refuse a task order an IDR can't take: one that isn't a live task order of the IDR's project.
    Takes the IDR's project id and the task order's id (None for none, which is always fine).
    Returns nothing; raises 400 for an unknown task order, another project's or a deleted one.
    """
    if task_order_id is None:
        return
    task_order = get_task_order(task_order_id, include_properties=False)
    if task_order is None or task_order["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="Task order is not one of this project's")
    if task_order["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail="Task order is deleted")


@router.post(
    "/",
    response_model=IdrResponse,
    status_code=201,
    responses={409: {"model": IdrConflict, "description": "An IDR already exists for this day"}},
)
def create_draft_idr(body: IdrCreate, user: UserOut = Depends(current_user)) -> Union[IdrResponse, JSONResponse]:
    """
    Create a new draft IDR for the signed-in user (its reporter) on a project for the given date, with an optional task order (its default flag scope).
    Takes an IdrCreate body with the project id, report date and task order, and the signed-in user.
    Returns an IdrResponse, raising 404 for an unknown project, 403 for an unassigned reporter, 400 for a task order that isn't a live one of the project, and 409 (with existing_idr_id) if that day's IDR exists.
    """
    if get_project_by_id(body.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not is_user_on_project(user.uuid, body.project_id):
        raise HTTPException(status_code=403, detail="Reporter is not assigned to this project")

    _check_task_order(body.project_id, body.task_order_id)

    rows = create_idr(body.project_id, user.uuid, body.report_date, body.task_order_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to create IDR")

    if not rows:
        existing_idr_id = get_idr_id_for_day(body.project_id, user.uuid, body.report_date)

        if existing_idr_id is None:
            raise HTTPException(status_code=500, detail="Failed to create IDR")

        conflict = IdrConflict(
            detail="IDR already exists for this project and date",
            existing_idr_id=existing_idr_id,
        )
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return IdrResponse(
        status="success",
        message="Draft IDR created",
        data=Idr.model_validate(rows[0]),
    )


@router.get("/{idr_id}", response_model=IdrWithReportsResponse)
def get_idr(idr_id: UUID) -> IdrWithReportsResponse:
    """
    Return an IDR with its header fields, every report inside it (report_data as stored) and every edit reviewers made on it, oldest first. The header and the reports already hold the edited values; the edits are their history.
    Takes the IDR uuid as a path parameter.
    Returns an IdrWithReportsResponse (reports and field_edits are [] when none), or raises 404 if the IDR does not exist.
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    reports = list_reports_for_idr(idr_id)

    if reports is None:
        raise HTTPException(status_code=500, detail="Failed to load IDR reports")

    edits = field_edits_for(idr_id)

    if edits is None:
        raise HTTPException(status_code=500, detail="Failed to load IDR edits")

    return IdrWithReportsResponse(
        status="success",
        message="IDR detail",
        data=IdrWithReports.model_validate({**idr, "reports": reports, "field_edits": edits}),
    )


def _report_conflict(detail: str, existing_report_id: UUID) -> JSONResponse:
    """
    Build the 409 for a report the IDR may hold only one of (its General, its Daily Site Patrol).
    Takes the message and the uuid of the report the IDR already holds.
    Returns the JSONResponse carrying an IdrReportConflict.
    """
    conflict = IdrReportConflict(detail=detail, existing_report_id=existing_report_id)
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


@router.post(
    "/{idr_id}/reports",
    response_model=IdrReportResponse,
    status_code=201,
    responses={409: {"model": IdrReportConflict,
                     "description": "IDR is not a draft, or already has a General or a Daily Site Patrol"}},
)
def add_report(idr_id: UUID, body: IdrReportCreate) -> Union[IdrReportResponse, JSONResponse]:
    """
    Add an empty report of the given type to a draft IDR, optionally as an addendum to one of its main reports; a DSP only as the addendum of a DSP_PARENT_TYPES main report, one per IDR.
    Takes the IDR uuid as a path parameter and an IdrReportCreate body.
    Returns an IdrReportResponse; raises 404 (no IDR), 409 (not draft, or a second General or DSP, with existing_report_id) and 400 (bad parent).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    is_dsp = body.report_type == ReportType.DSP

    if is_dsp and (not body.is_addendum or body.parent_report_id is None):
        raise HTTPException(status_code=400, detail=DSP_PARENT_MESSAGE)

    if body.parent_report_id is not None:
        if not body.is_addendum:
            raise HTTPException(status_code=400, detail="Only addendums can have a parent report")

        parent = get_idr_report(idr_id, body.parent_report_id)

        if parent is None:
            raise HTTPException(status_code=400, detail="Parent report not found in this IDR")

        if is_dsp and (parent["is_addendum"] or parent["report_type"] not in DSP_PARENT_TYPES):
            raise HTTPException(status_code=400, detail=DSP_PARENT_MESSAGE)

        if parent["is_addendum"]:
            raise HTTPException(status_code=400, detail="An addendum's parent must be a main report, not another addendum")

    if is_dsp:
        existing_dsp_id = get_dsp_report_id(idr_id)

        if existing_dsp_id is not None:
            return _report_conflict(DSP_TAKEN_MESSAGE, existing_dsp_id)

    try:
        rows = create_idr_report(idr_id, body.report_type.value, body.is_addendum, body.parent_report_id)
    except DspTakenError:
        existing_dsp_id = get_dsp_report_id(idr_id)

        if existing_dsp_id is None:
            raise HTTPException(status_code=500, detail="Failed to add report")

        return _report_conflict(DSP_TAKEN_MESSAGE, existing_dsp_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to add report")

    if not rows:
        existing_report_id = get_general_report_id(idr_id)

        if existing_report_id is None:
            raise HTTPException(status_code=500, detail="Failed to add report")

        return _report_conflict("IDR already has a General report", existing_report_id)

    touch_idr(idr_id)

    if body.report_type != ReportType.GEN:
        regenerate_auto_general(idr_id)

    return IdrReportResponse(
        status="success",
        message="Report added",
        data=IdrReport.model_validate(rows[0]),
    )


@router.put("/{idr_id}/reports/{report_id}", response_model=IdrReportResponse)
def save_report(
    idr_id: UUID, report_id: UUID, body: Annotated[ReportData, Body()]
) -> IdrReportResponse:
    """
    Replace a report's data with the body, bumping updated_at on the report and its IDR. Its pay items can't include one the IDR's flags already fill. Any JSON object is stored as-is, but a Daily Site Patrol's, which must be a DspReport and is stored tidied (check_dsp_report).
    Takes the IDR and report uuids as path parameters and any JSON object as the body.
    Returns an IdrReportResponse; raises 404 (no IDR), 409 (IDR not draft), 400 (a pay item the IDR's flags fill), 404 (report not in this IDR) and 400 (a DSP body that isn't a DspReport).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    refuse_flag_filled_pay_items(idr, body.get("payItems"))

    report = get_idr_report(idr_id, report_id)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    if report["report_type"] == ReportType.DSP:
        try:
            body = check_dsp_report(body)
        except DspReportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = save_report_data(idr_id, report_id, body)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to save report")

    if not rows:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    if rows[0]["report_type"] != ReportType.GEN:
        regenerate_auto_general(idr_id)

    return IdrReportResponse(
        status="success",
        message="Report saved",
        data=IdrReport.model_validate(rows[0]),
    )


@router.put("/{idr_id}/header", response_model=IdrResponse)
def save_header(idr_id: UUID, body: IdrHeaderUpdate) -> IdrResponse:
    """
    Update any subset of a draft IDR's header fields and its task order (null clears one) and bump its updated_at.
    Takes the IDR uuid as a path parameter and an IdrHeaderUpdate body.
    Returns an IdrResponse; raises 404 (no IDR), 409 (not draft) and 400 (temp_low above temp_high after the update, or a task order that isn't a live one of its project).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    fields = body.model_dump(exclude_unset=True)

    temp_low = fields.get("temp_low", idr["temp_low"])
    temp_high = fields.get("temp_high", idr["temp_high"])

    if temp_low is not None and temp_high is not None and temp_low > temp_high:
        raise HTTPException(status_code=400, detail="temp_low cannot be greater than temp_high")

    _check_task_order(idr["project_id"], fields.get("task_order_id"))

    rows = update_idr_header(idr_id, fields)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to save IDR header")

    if not rows:
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    return IdrResponse(
        status="success",
        message="IDR header saved",
        data=Idr.model_validate(rows[0]),
    )


@router.delete("/{idr_id}/reports/{report_id}", status_code=204, response_class=Response)
def delete_report(idr_id: UUID, report_id: UUID) -> Response:
    """
    Remove a report from a draft IDR, together with its addendums and every attachment on them, and bump the IDR's updated_at.
    Takes the IDR and report uuids as path parameters.
    Returns an empty 204; raises 404 (no IDR), 409 (IDR not draft) and 404 (report not in this IDR).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")

    # Confirm the report is in this IDR before touching Storage, so a mismatched
    # idr_id can never remove another IDR's files.
    if get_idr_report(idr_id, report_id) is None:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    # Storage files first (best-effort, never raises), then the row; ON DELETE CASCADE
    # removes the attachment rows. Not atomic: see delete_all_storage_files_for_report.
    delete_all_storage_files_for_report(report_id)

    rows = delete_idr_report(idr_id, report_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to delete report")

    if not rows:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    deleted = rows[0]
    is_general = deleted["report_type"] == ReportType.GEN and not deleted["is_addendum"]

    if is_general:
        # Deleting the auto-General is a dismissal; an inspector's own General does not cascade.
        if deleted["is_auto_generated"]:
            set_dismissed_auto_general(idr_id)
    else:
        regenerate_auto_general(idr_id)

    return Response(status_code=204)


@router.get("/", response_model=IdrListResponse)
def list_project_idrs(
    project_id: Optional[str] = None,
    status: Optional[IdrStatus] = None,
    reporter_uuid: Optional[UUID] = None,
    include_deleted: bool = False,
    include_all_drafts: bool = False,
    idr_number: Optional[str] = None,
    work_date: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    discipline: Optional[list[str]] = Query(None),
    user: UserOut = Depends(current_user),
) -> IdrListResponse:
    """
    List IDRs most recently edited first, each with report_count, has_general, disciplines and the names of its inspector and reviewers; every filter is optional, and filters of different kinds must all hold. Deleted IDRs and other people's drafts are left out unless an admin asks for them. A demo user is always listed their own IDRs only, whatever reporter_uuid says.
    Takes optional project_id, status and reporter_uuid query parameters, include_deleted and include_all_drafts (admin only), idr_number (the start of the number, any case), work_date (one day) or from and to (work dates, inclusive), discipline (repeatable, by name: an IDR with a report covering any of them), and the signed-in user.
    Returns an IdrListResponse (empty data list when nothing matches); raises 400 when someone who isn't an admin asks for deleted IDRs or all drafts, for a date that isn't one, work_date sent with from or to, from after to, or an unknown discipline, and 500 on a query failure.
    """
    if (include_deleted or include_all_drafts) and user.role != "admin":
        raise HTTPException(status_code=400, detail="include_deleted and include_all_drafts are for admins only")

    if work_date is not None and (date_from is not None or date_to is not None):
        raise HTTPException(status_code=400, detail="work_date cannot be combined with from or to")

    first, last = day_range(date_from, date_to)

    if user.is_demo:
        reporter_uuid = user.uuid

    rows = list_idrs(
        user.uuid,
        project_id,
        status,
        reporter_uuid,
        include_deleted,
        include_all_drafts,
        idr_number=(idr_number or "").strip() or None,
        work_date=day(work_date, "work_date"),
        date_from=first,
        date_to=last,
        report_types=report_types_for(discipline),
    )

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to list IDRs")

    return IdrListResponse(status="success", message=f"{len(rows)} IDR(s)", data=idr_list_items(rows))


@router.post(
    "/{idr_id}/submit",
    response_model=IdrWithReportsResponse,
    # In this order: a demo user is turned away before their role is looked up
    dependencies=[Depends(require_full_user), Depends(require_project_role("inspector"))],
)
def submit_draft_idr(idr_id: UUID, user: UserOut = Depends(require_full_user)) -> IdrWithReportsResponse:
    """
    Submit a draft IDR, signed by the signed-in user: copy their signature to the IDR, then lock it, stamp submitted_at and the signature, number every report, set total_pages and clear any return. Open to an inspector on the IDR's project (or an admin), never to a demo user.
    Takes the IDR uuid as a path parameter and the signed-in user; no body.
    Returns an IdrWithReportsResponse with the reports in page order; raises 403 (demo user, or not an inspector on the project), 404 (no IDR), 409 (not draft), 400 (no reports, or the user has no signature) and 502 (the signature couldn't be copied).
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be submitted")

    reports = list_reports_for_idr(idr_id)

    if reports is None:
        raise HTTPException(status_code=500, detail="Failed to load IDR reports")

    if not reports:
        raise HTTPException(status_code=400, detail="IDR must contain at least one report before submission.")

    if user.signature_path is None:
        raise HTTPException(status_code=400, detail="Signature required before submitting")

    # The copy comes first: Storage and Postgres can't share a transaction, and a submitted IDR must never point at
    # a signature that isn't there. If the submit below doesn't go through, the copy is left behind, unreferenced.
    try:
        signature_copy = snapshot_signature_for_idr(user.signature_path, idr_id)
    except SignatureStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        rows = submit_idr(idr_id, signature_copy, user.uuid)
    except Exception:
        logger.warning("Submit of IDR %s failed after its signature was copied; %s is left orphaned", idr_id, signature_copy)
        raise

    if not rows:
        logger.warning("IDR %s was not submitted after its signature was copied; %s is left orphaned", idr_id, signature_copy)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to submit IDR")

    if not rows:
        raise HTTPException(status_code=409, detail="IDR changed during submission; reload and try again")

    numbered = list_reports_for_idr(idr_id)

    if numbered is None:
        raise HTTPException(status_code=500, detail="Failed to load IDR reports")

    return IdrWithReportsResponse(
        status="success",
        message="IDR submitted",
        data=IdrWithReports.model_validate({**rows[0], "reports": numbered}),
    )
