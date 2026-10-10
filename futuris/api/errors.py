"""Consistent JSON error envelopes and exception handlers conforming to RFC 7807.

Two failure modes found by driving every route with hostile input are fixed
here, because both are cross-cutting:

* A request whose body was not JSON (or whose headers were absurd) produced a
  422 whose ``details`` contained raw ``bytes``; the JSONResponse then failed to
  serialise, so a validation error surfaced as an opaque ``Object of type bytes
  is not JSON serializable`` 500. Validation details are now sanitised before
  they are rendered.
* A database that was never initialised surfaced as a 500 carrying a raw
  ``sqlite3.OperationalError``. Storage problems are now mapped to 503 with a
  machine-readable code and a remediation hint, and a missing schema on SQLite
  triggers a one-shot, bounded repair so the next request can succeed.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLATimeoutError

from futuris.infra.logging import get_logger

logger = get_logger("futuris.api.errors")

MAX_DETAIL_CHARS = 300


class ErrorDetail(BaseModel):
    """Structured error payload details."""

    code: str
    message: str
    details: dict[str, Any] | list[Any] | None = None


class ErrorEnvelope(BaseModel):
    """Standardized API error envelope."""

    error: ErrorDetail


class FuturisAPIError(Exception):
    """Base API domain exception."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def sanitise_for_json(value: Any) -> Any:
    """Return a JSON-serialisable copy of ``value``, never raising.

    Validation errors can carry arbitrary objects -- bytes from a non-JSON
    body, exception instances in ``ctx``, sets, numpy scalars -- and the error
    handler is the last place that may fail.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:MAX_DETAIL_CHARS]
    if isinstance(value, dict):
        return {str(k): sanitise_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitise_for_json(v) for v in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)[:MAX_DETAIL_CHARS]


def storage_error_response(exc: OperationalError) -> JSONResponse:
    """Map a database OperationalError onto an actionable 503."""
    message = str(getattr(exc, "orig", exc))
    lowered = message.lower()
    if "no such table" in lowered or "no such column" in lowered or "does not exist" in lowered:
        # "no such column" is the stale-database shape of the same problem: an
        # existing SQLite file whose table predates a column the ORM now reads.
        # The scheduled repair adds those columns (see db.add_missing_columns).
        code = "storage_schema_missing"
        public = (
            "The database is reachable but its schema is not initialised. Run the "
            "startup migration (the application does this automatically on boot) or "
            "`alembic upgrade head`, then retry."
        )
        repair = True
    elif "unable to open database file" in lowered or "connection refused" in lowered:
        code = "storage_unavailable"
        public = (
            "The configured database could not be opened. Check DATABASE_URL and "
            "that the directory exists and is writable."
        )
        repair = False
    elif "database is locked" in lowered or "database table is locked" in lowered:
        code = "storage_busy"
        public = "The database is locked by another writer. Retry shortly."
        repair = False
    else:
        code = "storage_error"
        public = "The database rejected the request."
        repair = False

    logger.error("storage_error_mapped", code=code, error=message[:400])
    if repair:
        # Best-effort, non-blocking repair for development SQLite deployments:
        # an ASGI host that skipped the lifespan hook (or a DB file created by a
        # plain import) leaves an empty schema, and the next request should not
        # pay for it. Non-SQLite dialects keep Alembic as the single owner of
        # their schema.
        try:
            from futuris.storage.db import schedule_schema_repair

            schedule_schema_repair()
        except Exception as repair_exc:  # noqa: BLE001 - repair must never break the response
            logger.warning("schema_repair_schedule_failed", error=str(repair_exc))
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": {
                "code": code,
                "message": public,
                "details": {"dialect_error": message[:MAX_DETAIL_CHARS]},
            }
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register custom exception handlers on FastAPI application."""

    @app.exception_handler(FuturisAPIError)
    async def futuris_exception_handler(request: Request, exc: FuturisAPIError) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": sanitise_for_json(exc.details),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request payload failed schema validation",
                    "details": sanitise_for_json(exc.errors()),
                }
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        _ = request
        logger.warning("integrity_error", error=str(exc.orig)[:300])
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "conflict",
                    "message": (
                        "The request conflicts with an existing record (unique or "
                        "foreign-key constraint)."
                    ),
                    "details": {"dialect_error": str(exc.orig)[:MAX_DETAIL_CHARS]},
                }
            },
        )

    @app.exception_handler(OperationalError)
    async def operational_exception_handler(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        _ = request
        return storage_error_response(exc)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        _ = request
        code_map = {
            404: "not_found",
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            409: "conflict",
            429: "rate_limited",
            501: "not_implemented",
            503: "service_unavailable",
        }
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": code_map.get(exc.status_code, "http_error"),
                    "message": exc.detail if isinstance(exc.detail, str) else "HTTP error",
                    "details": (
                        sanitise_for_json(exc.detail) if isinstance(exc.detail, dict) else {}
                    ),
                }
            },
        )

    @app.exception_handler(SQLATimeoutError)
    async def pool_timeout_handler(request: Request, exc: SQLATimeoutError) -> JSONResponse:
        """Map a database pool checkout timeout onto an actionable 503.

        A pool timeout means the server is at its concurrency ceiling, not that
        anything is broken: the client should back off and retry, and the
        response must say so rather than surfacing as an opaque 500.
        """
        _ = request
        logger.error("pool_timeout", error=str(exc)[:300])
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "server_busy",
                    "message": (
                        "The server is at its database concurrency limit. "
                        "Retry shortly."
                    ),
                    "details": {"remediation": "retry with backoff; reduce concurrency"},
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # The exception detail (driver messages, file paths, library internals)
        # is logged for the operator but never returned to the client: an
        # unhandled error is exactly the moment an API must not leak internals.
        logger.error(
            "unhandled_exception",
            error=type(exc).__name__,
            detail=str(exc)[:400],
            request_id=request.headers.get("X-Request-ID"),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected internal error occurred",
                    "details": {
                        "error_type": type(exc).__name__,
                        "request_id": request.headers.get("X-Request-ID"),
                        "remediation": "check server logs for the request id",
                    },
                }
            },
        )
