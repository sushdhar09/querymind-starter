import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Header, HTTPException, Request

from backend.config import admin_api_key, rate_limit_per_min


def require_admin(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)) -> None:
    expected = admin_api_key()
    if not expected:
        raise HTTPException(503, "Admin API is disabled: set ADMIN_API_KEY on the server")
    supplied = x_api_key or (authorization[7:] if authorization and authorization.lower().startswith("bearer ") else "")
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing admin API key", headers={"WWW-Authenticate": "Bearer"})


class RateLimiter:
    def __init__(self, window: float = 60.0):
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: Optional[float] = None) -> Optional[int]:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(hits[0] + self.window - now) + 1)
            hits.append(now)
            if len(self._hits) > 10000:
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v and v[-1] > now - self.window})
            return None


limiter = RateLimiter()


def query_rate_limit(request: Request) -> None:
    limit = rate_limit_per_min()
    if limit <= 0:
        return
    retry = limiter.check(request.client.host if request.client else "unknown", limit)
    if retry:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(retry)})
