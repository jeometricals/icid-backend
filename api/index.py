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
    from api.v1.users import router as users_router
    from api.v1.projects import router as projects_router
    from api.v1.idrs import router as idrs_router
    from api.v1.debug import router as debug_router

    app.include_router(users_router)
    app.include_router(projects_router)
    app.include_router(idrs_router)
    app.include_router(debug_router)


include_routers(app)


@app.get("/status")
def get_status():
    return {"status": "ok", "message": "ICID API is running"}
