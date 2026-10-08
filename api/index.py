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
    from api.v1.reviews import router as reviews_router
    from api.v1.field_edits import router as field_edits_router
    from api.v1.idrs import router as idrs_router
    from api.v1.attachments import router as attachments_router
    from api.v1.contract_items import router as contract_items_router
    from api.v1.exports import router as exports_router
    from api.v1.signatures import router as signatures_router
    from api.v1.admin import router as admin_router

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(projects_router)
    app.include_router(quantities_router)
    # Before the IDRs router, so /v1/idrs/queue isn't read as /v1/idrs/{idr_id}
    app.include_router(reviews_router)
    app.include_router(idrs_router)
    app.include_router(field_edits_router)
    app.include_router(attachments_router)
    app.include_router(contract_items_router)
    app.include_router(exports_router)
    app.include_router(signatures_router)
    app.include_router(admin_router)


include_routers(app)


@app.get("/status")
def get_status():
    return {"status": "ok", "message": "ICID API is running"}
