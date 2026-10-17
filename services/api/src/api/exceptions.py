"""Typed domain errors and centralized HTTP mapping for the TraceAML API.

Every domain error carries a fixed, public ``detail`` string and status code.
Handlers return only that string, so hosts, ports, file paths, and upstream
exception text never reach a response. A last-resort handler maps anything
unclassified to a generic 500 without the raw message.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("api")


class TraceAMLError(Exception):
    """Base domain error with a safe public detail and HTTP status."""

    status_code: int = 500
    detail: str = "internal error"

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFoundError(TraceAMLError):
    """A requested record does not exist."""

    status_code = 404
    detail = "not found"


class ConflictError(TraceAMLError):
    """The request conflicts with the current record state."""

    status_code = 409
    detail = "conflict"


class ServiceUnavailableError(TraceAMLError):
    """A required dependency or artifact is unavailable."""

    status_code = 503
    detail = "service unavailable"


class GraphUnavailableError(ServiceUnavailableError):
    """FalkorDB is not configured, unreachable, or dropped the connection."""

    detail = "graph unavailable"


class GraphTimeoutError(TraceAMLError):
    """A bounded graph query exceeded its timeout."""

    status_code = 504
    detail = "graph query timed out"


class TransactionNotFoundError(NotFoundError):
    """The transaction is not in the score artifact or the graph."""

    detail = "transaction not scored"


class InvestigationError(TraceAMLError):
    """Evidence could not be assembled or failed deterministic validation."""

    status_code = 500
    detail = "graph evidence unavailable"


def setup_exception_handlers(app: FastAPI) -> None:
    """Register the domain and last-resort handlers on the application."""

    @app.exception_handler(TraceAMLError)
    async def domain_error_handler(_request: Request, exc: TraceAMLError) -> JSONResponse:
        if exc.status_code == 500:
            logger.error("request failed: %s", type(exc).__name__, exc_info=exc.__cause__)
        elif exc.status_code > 500:
            logger.warning("dependency failure: %s (%s)", exc.detail, type(exc.__cause__).__name__)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        """Last-resort guard: never leak raw exception text."""
        logger.error("unhandled error: %s", type(exc).__name__, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal error"})
