"""
telemetry.py — OpenTelemetry SDK initialisation for the Agent service.

Call ``setup_telemetry(app)`` once inside ``run()`` after ``app = FastAPI(...)``
and after ``setup_logging()`` has already been called.

What this does:
  - Creates a TracerProvider that exports spans via OTLP gRPC to the OTel
    Collector (which applies tail-based sampling before forwarding to
    OpenObserve).
  - Creates a MeterProvider that pushes metrics every 30 s.
  - Auto-instruments FastAPI (request/response spans on every route).
  - Auto-instruments httpx (propagates W3C traceparent header to Compilation
    and Storage services, creating a connected trace tree).
  - Auto-instruments the stdlib logging module (injects trace_id / span_id
    into every log record so logs and traces correlate in OpenObserve).
"""

import os
import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logger = logging.getLogger(__name__)

_SDK_INITIALISED = False


def setup_telemetry(app) -> None:
    """Initialise OTel SDK and instrument the FastAPI app.

    Idempotent — safe to call more than once (only runs on the first call).

    Args:
        app: The FastAPI application instance.
    """
    global _SDK_INITIALISED
    if _SDK_INITIALISED:
        return

    # Check if telemetry is disabled (default to True if not specified)
    disable_telemetry = os.getenv("DISABLE_TELEMETRY", "true").lower() in ("true", "1", "yes")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    if disable_telemetry or not endpoint:
        logger.info("Telemetry is disabled (DISABLE_TELEMETRY=true or OTEL_EXPORTER_OTLP_ENDPOINT is empty).")
        return

    _SDK_INITIALISED = True

    service_name = os.getenv("OTEL_SERVICE_NAME", "agent")

    resource = Resource.create({SERVICE_NAME: service_name})

    # ── Traces ────────────────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, insecure=True),
            max_export_batch_size=512,
            export_timeout_millis=10_000,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ───────────────────────────────────────────────────────────────
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=True),
        export_interval_millis=30_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # ── Logs ──────────────────────────────────────────────────────────────────
    # Ship stdlib logging records directly to the OTel Collector as LogRecords.
    # This makes them visible in OpenObserve under the logs tab.
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=endpoint, insecure=True),
        )
    )
    set_logger_provider(log_provider)

    # Attach as a standard logging handler at the root level so every
    # logger (including third-party ones) ships logs via OTLP.
    otel_log_handler = LoggingHandler(
        level=logging.INFO,
        logger_provider=log_provider,
    )
    logging.getLogger().addHandler(otel_log_handler)

    # ── Auto-instrumentation ──────────────────────────────────────────────────
    # Inject trace_id / span_id into every stdlib logging record
    LoggingInstrumentor().instrument(set_logging_format=False)

    # Add request/response spans to every FastAPI route
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,/health",   # skip noisy liveness probes
    )

    # Propagate W3C traceparent on every outgoing httpx request
    HTTPXClientInstrumentor().instrument()

    logger.info(
        "OTel SDK initialised",
        extra={"otel_endpoint": endpoint, "service": service_name},
    )
