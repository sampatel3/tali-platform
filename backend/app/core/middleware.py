import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("tali.middleware")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Log (skip health checks to reduce noise)
        if request.url.path != "/health":
            logger.info(
                "method=%s path=%s status=%d duration=%.1fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )

        # Add timing header
        response.headers["X-Process-Time-Ms"] = f"{duration_ms:.1f}"

        return response
