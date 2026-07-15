"""Correlation-ID middleware + logging filter (M2) — request tracing without a
new dependency. Each request gets an id (from X-Request-ID or generated), stored
in a contextvar, echoed back in the response header, and injected into `app.*`
log records so a request can be followed across log lines."""
import contextvars
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Request-ID"] = cid
        return response


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


def configure_correlation_logging() -> None:
    """Attach a correlation-id-aware handler to the `app` logger (idempotent).
    Kept separate from uvicorn's loggers so we don't disturb access logs."""
    logger = logging.getLogger("app")
    if any(getattr(h, "_nexora_cid", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [cid=%(correlation_id)s] %(name)s: %(message)s"
    ))
    handler.addFilter(CorrelationIdFilter())
    handler._nexora_cid = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
