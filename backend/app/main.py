import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.videos import router as videos_router
from app.jobs import router as jobs_router

app = FastAPI(title="VisionFlow")
app.include_router(videos_router)
app.include_router(jobs_router)
logger = logging.getLogger(__name__)


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": {"code": "invalid_request", "message": "Request validation failed."}})


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unexpected server error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": {"code": "internal_error", "message": "Internal server error."}})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
