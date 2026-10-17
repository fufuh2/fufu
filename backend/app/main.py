import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.api.routes import router
from app.config import load_settings
from app.db import get_engine
from app.errors import DomainError

STATIC_DIR = Path(__file__).parent / "static"
# The UI uses no inline scripts/styles and no third-party origins.
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

log = logging.getLogger("opsx")


def create_app() -> FastAPI:
    settings = load_settings()  # fail fast on missing secrets
    app = FastAPI(
        title="OperationOS-X",
        version="0.1.0",
        # Interactive docs and the schema are opt-in: only when OPSX_ENV=development.
        docs_url="/docs" if settings.env == "development" else None,
        openapi_url="/openapi.json" if settings.env == "development" else None,
        redoc_url=None,
    )
    app.include_router(router)
    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop the echoed "input": it can contain passwords or other submitted secrets.
        errors = [{k: v for k, v in e.items() if k in ("type", "loc", "msg")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(DataError)
    async def data_error(_: Request, exc: DataError) -> JSONResponse:
        # Raised by PostgreSQL for values outside NUMERIC precision; the request's
        # transaction is rolled back when its session closes.
        log.info("rejected out-of-range value: %s", exc.orig)
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "out_of_range", "message": "a value is out of range"}},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        # Safety net: services translate expected conflicts themselves; anything reaching
        # here is a constraint the service did not anticipate. Never a 500, never details.
        log.warning("unhandled integrity error: %s", exc.orig)
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "constraint", "message": "conflicts with existing data"}},
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces or SQL to clients.
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500, content={"detail": {"code": "internal", "message": "internal error"}}
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        path = request.url.path
        if path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = UI_CSP
        elif path.startswith("/api"):
            # JSON only: nothing may be loaded or framed from an API response.
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - any failure means not ready
            log.exception("readiness check failed")
            return JSONResponse(
                status_code=503, content={"status": "unavailable", "database": "error"}
            )
        return JSONResponse(content={"status": "ok", "database": "ok"})

    return app
