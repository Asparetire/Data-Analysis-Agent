from __future__ import annotations

import logging
import sys

from pythonjsonlogger import jsonlogger

from ..config import settings
from .log_scrub import ScrubFilter
from .request_id import request_id_ctx

_CONFIGURED = False

_TEXT_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_TEXT_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _RequestIdFilter(logging.Filter):
    """Stamp every record with the current request_id from the ContextVar.

    Runs after ScrubFilter (which rewrites msg) and before the formatter,
    so both text and JSON output carry the id. Library code that logs via
    ``logging.getLogger("uvicorn.access")`` etc. also picks it up because
    the filter is attached to the root handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_ctx.get()
        if rid:
            record.request_id = rid
        return True


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)

    if settings.LOG_FORMAT == "text":
        handler.setFormatter(logging.Formatter(_TEXT_FMT, datefmt=_TEXT_DATEFMT))
    else:
        # Flat JSON: {"timestamp": ..., "level": ..., "logger": ..., "message": ..., "request_id": ...}
        # request_id is added by _RequestIdFilter; python-json-logger
        # serializes any LogRecord attribute it finds, so it flows through
        # automatically once the filter sets record.request_id.
        handler.setFormatter(
            jsonlogger.JsonFormatter(
                # Use standard LogRecord attribute names; rename_fields maps
                # them to the output keys we want.
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={
                    "asctime": "timestamp",
                    "levelname": "level",
                    "name": "logger",
                },
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )

    # Order matters: ScrubFilter rewrites record.msg (PII redaction) BEFORE
    # _RequestIdFilter stamps the id. Both run before the formatter.
    handler.addFilter(ScrubFilter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)
