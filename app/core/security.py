import re
import time
import uuid
from collections import defaultdict
from threading import Lock
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def is_valid_uuid(val: str) -> bool:
    if not val or not isinstance(val, str):
        return False
    return bool(UUID_REGEX.match(val.strip()))


class InMemoryRateLimiter:
    """
    Sliding window in-memory rate limiter per client IP or Session ID.
    Thread-safe with automatic window expiration cleanup.
    """
    def __init__(self, requests_per_minute: int = 60, burst_limit: int = 30):
        self.rpm = requests_per_minute
        self.burst = burst_limit
        self.window = 60.0
        self._history = defaultdict(list)
        self._lock = Lock()
        self._last_cleanup = time.time()

    def _cleanup_stale(self, now: float) -> None:
        """Prune empty entries and expired timestamps to bound memory usage."""
        cutoff = now - self.window
        stale_keys = []
        for key, timestamps in self._history.items():
            self._history[key] = [t for t in timestamps if t > cutoff]
            if not self._history[key]:
                stale_keys.append(key)
        for key in stale_keys:
            del self._history[key]

    def is_allowed(self, key: str, max_requests: int | None = None) -> tuple[bool, int]:
        limit = max_requests or self.rpm
        now = time.time()
        with self._lock:
            # Periodic cleanup to prevent unbounded memory growth
            if now - self._last_cleanup > 60.0:
                self._cleanup_stale(now)
                self._last_cleanup = now
            timestamps = self._history[key]
            # Prune timestamps older than window
            cutoff = now - self.window
            self._history[key] = [t for t in timestamps if t > cutoff]
            if len(self._history[key]) >= limit:
                retry_after = int(self.window - (now - self._history[key][0])) + 1
                return False, max(1, retry_after)
            self._history[key].append(now)
            return True, 0


# Global rate limiter instances:
# 1. Standard API rate limiter (120 req/min)
# 2. Expensive chat / LLM rate limiter (30 req/min)
general_limiter = InMemoryRateLimiter(requests_per_minute=120)
chat_limiter = InMemoryRateLimiter(requests_per_minute=30)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "connect-src 'self' http://127.0.0.1:* http://localhost:* "
            "https://eliza-ai-voice-agent-bd.onrender.com "
            "https://elizaaivoice.vercel.app "
            "ws: wss:; "
            "img-src 'self' data:; "
            "frame-ancestors 'none';"
        )
        # HSTS only over HTTPS in production
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_size_bytes: int = 1048576):
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(self, request: Request, call_next):
        # Check Content-Length header first (fast path)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_size_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request payload exceeds maximum allowed size (1MB)."},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )

        # For chunked transfer encoding, enforce by reading body up to limit
        if request.headers.get("transfer-encoding", "").lower() == "chunked":
            try:
                body = await request.body()
                if len(body) > self.max_size_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request payload exceeds maximum allowed size (1MB)."},
                    )
                # Re-inject body so downstream handlers can still read it
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                request._receive = receive
            except Exception:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Unable to read request body."},
                )
        return await call_next(request)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = req_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


def get_client_identifier(request: Request) -> str:
    """Identifies the client using a validated session header or client IP."""
    session_id = request.headers.get("X-Session-ID")
    if session_id and is_valid_uuid(session_id):
        return f"sess:{session_id}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"

