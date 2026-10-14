import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "request_id", "taskName"}


def sanitize_request_id(value: str) -> str:
    return value if _REQUEST_ID.match(value or "") else ""


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "request_id", None) is None:
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                 "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
                 "request_id": getattr(record, "request_id", "-")}
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD and not k.startswith("_")})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_querymind", False):
            root.removeHandler(h)
    handler._querymind = True
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger("querymind.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = sanitize_request_id(incoming) or uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)
        started, status = time.perf_counter(), 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            self.log.info("%s %s -> %s", scope["method"], scope["path"], status,
                          extra={"method": scope["method"], "path": scope["path"], "status": status,
                                 "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                                 "client": (scope.get("client") or ["-"])[0]})
            request_id_var.reset(token)
