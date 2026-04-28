"""
telemetry.py — OpenTelemetry SDK initialisation for the Compilation service.

Call ``setup_telemetry(app)`` once inside the module-level setup after
``app = FastAPI(...)`` and after ``setup_logging()`` has been called.

What this does:
  - Exports spans via OTLP gRPC to the OTel Collector.
  - Exports metrics (request latency, compile duration histogram) every 30 s.
  - Auto-instruments FastAPI routes and outgoing httpx calls (to Storage).
  - Injects trace_id / span_id into every log record.
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
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HttpxInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logger = logging.getLogger(__name__)

_SDK_INITIALISED = False


def setup_telemetry(app) -> None:
    """Initialise OTel SDK and instrument the FastAPI app.

    Idempotent — safe to call more than once.

    Args:
        app: The FastAPI application instance.
    """
    global _SDK_INITIALISED
    if _SDK_INITIALISED:
        return
    _SDK_INITIALISED = True

    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://otel-collector-service.resume-agent.svc.cluster.local:4317",
    )
    service_name = os.getenv("OTEL_SERVICE_NAME", "compilation")

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

    # ── Auto-instrumentation ──────────────────────────────────────────────────
    LoggingInstrumentor().instrument(set_logging_format=False)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,/health",
    )
    HttpxInstrumentor().instrument()

    logger.info(
        "OTel SDK initialised",
        extra={"otel_endpoint": endpoint, "service": service_name},
    )


def get_compile_histogram():
    """Return an OTel histogram for measuring pdflatex compile duration.

    Use this as a context manager or record() call around the compile step.
    Returns the meter's histogram instrument.

    Example::

        histogram = get_compile_histogram()
        start = time.perf_counter()
        pdf_path = compile_pdf(...)
        histogram.record(
            time.perf_counter() - start,
            attributes={"thread_id": req.thread_id},
        )
    """
    meter = metrics.get_meter("compilation.latex")
    return meter.create_histogram(
        name="compilation.latex.compile_duration_seconds",
        description="Time taken for pdflatex to compile a .tex file to PDF",
        unit="s",
    )
