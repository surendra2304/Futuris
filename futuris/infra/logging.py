"""Structured timestamped logging configuration for Futuris."""

import logging
import sys
from typing import Any

import structlog

from futuris.infra.config import settings


def _format_event_message(_: Any, __: Any, event_dict: dict[str, Any]) -> str:
    """Format structlog event dictionary into a clean string message."""
    event = event_dict.pop("event", "")
    extras = [
        f"{k}={v}"
        for k, v in event_dict.items()
        if not k.startswith("_") and k not in {"level", "timestamp", "logger"}
    ]
    if extras:
        return f"{event} {' '.join(extras)}"
    return str(event)


def configure_logging() -> None:
    """Configure standard library and structlog to output structured timestamped logs.

    Format: [%(asctime)s] [%(levelname)s] [futuris]: %(message)s
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    log_format = "[%(asctime)s] [%(levelname)s] [futuris]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt=date_format),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _format_event_message,
        ],
        fmt=log_format,
        datefmt=date_format,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structured logger instance."""
    return structlog.get_logger(name)
