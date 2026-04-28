"""
logging_config.py — Structured JSON logging setup for the Agent service.

Call setup_logging() once at the very start of main.py (before any other
imports that touch logging) so that every log line — including those emitted
by third-party libraries — is formatted as JSON.

When OpenTelemetry's LoggingInstrumentor is active it will inject
``trace_id`` and ``span_id`` as extra fields into each record, making it
trivial to correlate a log line with its trace in OpenObserve.
"""

import logging
import os
from pythonjsonlogger.jsonlogger import JsonFormatter


def setup_logging(service_name: str = "agent") -> None:
    """Configure root logger for structured JSON output.

    Args:
        service_name: Value placed in the ``service`` field of every log line.
    """
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    handler = logging.StreamHandler()
    formatter = JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        static_fields={"service": service_name},
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Clear any handlers added by basicConfig / third-party libs before us
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Reduce noise from very chatty libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
