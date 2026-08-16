"""Structured JSON logging (doc 2.3 tooling expectations; feeds the
"Observability" layer L8, doc 2.1). Every plain `logging.getLogger(...)`
call (e.g. orchestration/worker.py) is routed through the same JSON
renderer via structlog's stdlib integration, so app logs and structlog
calls end up in one consistent format regardless of which one a module uses.
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
