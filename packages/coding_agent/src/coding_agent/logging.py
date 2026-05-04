"""Structured JSON logging via structlog.

All services in the platform log JSON to stdout so logs can be collected by
any standard Kubernetes log shipper (Fluent Bit, Vector, CloudWatch agent).
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO", *, service: str = "coding-agent") -> None:
    """Initialise structlog + stdlib logging once per process."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None, **initial: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name).bind(**initial) if initial else structlog.get_logger(name)
