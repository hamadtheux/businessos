from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime

from app.core.config import Settings, settings


request_id_context: ContextVar[str | None] = ContextVar(
    "aibos_request_id", default=None
)

_SECRET_PATTERNS = (
    # Mapping/JSON-style headers such as:
    # {"Authorization": "Bearer secret"} and {"Cookie": "session=secret"}.
    # Keep the surrounding syntax intact while removing only the value.
    re.compile(
        r'(?i)(["\']authorization["\']\s*:\s*["\'])[^"\']+(?=["\'])'
    ),
    re.compile(
        r'(?i)(["\'](?:set-cookie|cookie)["\']\s*:\s*["\'])[^"\']+(?=["\'])'
    ),
    re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n,]+"),
    re.compile(r"(?i)((?:set-cookie|cookie)\s*[:=]\s*)[^\r\n]+"),
    re.compile(
        r'(?i)(["\']?(?:access_token|refresh_token|id_token|api_key|token|'
        r'client_secret|password|webhook_secret|signing_secret|consumer_secret)'
        r'["\']?\s*[:=]\s*)'
        r'(["\']?)[^"\'\s,;}]+\2'
    ),
    re.compile(r"(?i)\b(?:sk-(?:proj-)?|AIza)[A-Za-z0-9_-]{8,}\b"),
)


def redact_text(value: object) -> str:
    """Return bounded log text with common credential forms removed."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            r"\1[REDACTED]" if pattern.groups else "[REDACTED]",
            text,
        )
    return text[:16_384]


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            # Logging must not become an application failure. If a caller
            # supplied incompatible formatting arguments, retain only the
            # bounded/redacted template and discard those arguments.
            rendered = str(record.msg)
        record.msg = redact_text(rendered)
        record.args = ()
        return True


class JsonLogFormatter(logging.Formatter):
    _standard_fields = frozenset(
        {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        request_id = request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in self._standard_fields or key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = redact_text(value) if isinstance(value, str) else value
        if record.exc_info:
            # Exception classes aid diagnosis; exception text and locals can
            # contain provider bodies or credentials and are deliberately omitted.
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(
    *, role: str, configuration: Settings = settings
) -> None:
    """Configure one process without ever serializing Settings or secrets."""
    root = logging.getLogger()
    root.setLevel(configuration.log_level)
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    if configuration.log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                f"%(asctime)s %(levelname)s aibos.{role} %(name)s %(message)s"
            )
        )
    root.handlers.clear()
    root.addHandler(handler)
