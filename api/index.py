import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ICID Reporting API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # A download's file name (the Construction Reports), readable from the frontend's origin
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


def include_routers(app: FastAPI):
    from api.v1.auth import router as auth_router
    from api.v1.users import router as users_router
    from api.v1.projects import router as projects_router
    from api.v1.quantities import router as quantities_router
    from api.v1.assessment_summary import project_router as assessment_summary_router
    from api.v1.properties import project_router as project_properties_router
    from api.v1.properties import router as properties_router
    from api.v1.task_orders import project_router as project_task_orders_router
    from api.v1.task_orders import router as task_orders_router
    from api.v1.progress import project_router as project_progress_router
    from api.v1.progress import router as task_order_progress_router
    from api.v1.pirs import property_router as property_pirs_router
    from api.v1.construction_reports import property_router as property_construction_report_router
    from api.v1.construction_reports import router as idr_construction_reports_router
    from api.v1.pirs import router as pirs_router
    from api.v1.pir_pages import pir_router as pir_pages_router
    from api.v1.pir_pages import router as page_router
    from api.v1.flags import pir_router as pir_flags_router
    from api.v1.flags import page_router as page_flags_router
    from api.v1.flags import router as flags_router
    from api.v1.non_assessable_areas import flag_router as flag_areas_router
    from api.v1.non_assessable_areas import router as areas_router
    from api.v1.features import page_router as page_features_router
    from api.v1.features import pir_router as pir_features_router
    from api.v1.features import router as features_router
    from api.v1.segments import page_router as page_segments_router
    from api.v1.segments import pir_router as pir_segments_router
    from api.v1.segments import router as segments_router
    from api.v1.pir_import import router as pir_import_router
    from api.v1.pir_title_block import pir_router as pir_title_block_router
    from api.v1.reviews import router as reviews_router
    from api.v1.field_edits import router as field_edits_router
    from api.v1.idr_flags import router as idr_flags_router
    from api.v1.idr_segments import router as idr_segments_router
    from api.v1.idrs import router as idrs_router
    from api.v1.attachments import router as attachments_router
    from api.v1.contract_items import router as contract_items_router
    from api.v1.exports import router as exports_router
    from api.v1.signatures import router as signatures_router
    from api.v1.admin import router as admin_router
    from api.v1.admin_users import router as admin_users_router
    from api.v1.admin_projects import router as admin_projects_router
    from api.v1.admin_pay_items import router as admin_pay_items_router

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(projects_router)
    app.include_router(quantities_router)
    app.include_router(assessment_summary_router)
    app.include_router(project_properties_router)
    app.include_router(properties_router)
    app.include_router(project_task_orders_router)
    app.include_router(task_orders_router)
    app.include_router(project_progress_router)
    app.include_router(task_order_progress_router)
    app.include_router(property_pirs_router)
    app.include_router(property_construction_report_router)
    app.include_router(pirs_router)
    app.include_router(pir_pages_router)
    app.include_router(page_router)
    app.include_router(pir_flags_router)
    app.include_router(page_flags_router)
    app.include_router(flags_router)
    app.include_router(flag_areas_router)
    app.include_router(areas_router)
    app.include_router(page_features_router)
    app.include_router(pir_features_router)
    app.include_router(features_router)
    app.include_router(page_segments_router)
    app.include_router(pir_segments_router)
    app.include_router(segments_router)
    app.include_router(pir_import_router)
    app.include_router(pir_title_block_router)
    # Before the IDRs router, so /v1/idrs/queue isn't read as /v1/idrs/{idr_id}
    app.include_router(reviews_router)
    app.include_router(idrs_router)
    app.include_router(field_edits_router)
    app.include_router(idr_flags_router)
    app.include_router(idr_segments_router)
    app.include_router(idr_construction_reports_router)
    app.include_router(attachments_router)
    app.include_router(contract_items_router)
    app.include_router(exports_router)
    app.include_router(signatures_router)
    app.include_router(admin_router)
    app.include_router(admin_users_router)
    app.include_router(admin_projects_router)
    app.include_router(admin_pay_items_router)


include_routers(app)


@app.get("/status")
def get_status():
    return {"status": "ok", "message": "ICID API is running"}
