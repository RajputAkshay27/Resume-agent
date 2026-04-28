/**
 * Next.js Instrumentation Hook
 *
 * Runs once when the Next.js server starts, before any routes are handled.
 *
 * 1. Patches undici's global dispatcher to disable timeouts (fixes
 *    UND_ERR_BODY_TIMEOUT on long AI-agent streaming responses).
 *
 * 2. Initialises the OpenTelemetry Node.js SDK so that every incoming HTTP
 *    request and every outgoing fetch() call to the Agent service is traced.
 *    Spans are exported via OTLP gRPC to the in-cluster OTel Collector, which
 *    applies tail-based sampling and forwards to OpenObserve.
 *
 * 3. Initialises the OTel Logs SDK and bridges console.log/warn/error so that
 *    all server-side log output appears in OpenObserve under the logs tab.
 *
 * See: https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    // ── 1. Undici patch (unchanged) ─────────────────────────────────────────
    const { setGlobalDispatcher, Agent } = await import("undici");

    setGlobalDispatcher(
      new Agent({
        bodyTimeout: 0,
        headersTimeout: 0,
        keepAliveTimeout: 600_000,
        keepAliveMaxTimeout: 600_000,
      })
    );

    console.log("[instrumentation] undici global dispatcher patched: bodyTimeout=0, headersTimeout=0");

    const endpoint =
      process.env.OTEL_EXPORTER_OTLP_ENDPOINT ??
      "http://otel-collector-service.resume-agent.svc.cluster.local:4317";

    // ── 2. OTel Logs SDK ─────────────────────────────────────────────────────
    const { OTLPLogExporter } = await import(
      "@opentelemetry/exporter-logs-otlp-grpc"
    );
    const { LoggerProvider, BatchLogRecordProcessor, SeverityNumber } =
      await import("@opentelemetry/sdk-logs");
    const { logs } = await import("@opentelemetry/api-logs");
    const { Resource } = await import("@opentelemetry/resources");
    const { ATTR_SERVICE_NAME } = await import(
      "@opentelemetry/semantic-conventions"
    );

    const resource = new Resource({
      [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME ?? "frontend",
    });

    const loggerProvider = new LoggerProvider({ resource });
    loggerProvider.addLogRecordProcessor(
      new BatchLogRecordProcessor(
        new OTLPLogExporter({ url: endpoint })
      )
    );
    logs.setGlobalLoggerProvider(loggerProvider);

    // Bridge console → OTel LogRecords so Next.js API route logs reach OpenObserve
    const otelLogger = logs.getLogger("console-bridge");
    const makeBridge =
      (
        original: (...args: unknown[]) => void,
        severity: number,
        severityText: string
      ) =>
      (...args: unknown[]) => {
        original(...args);
        const body = args
          .map((a) =>
            typeof a === "object" ? JSON.stringify(a) : String(a)
          )
          .join(" ");
        otelLogger.emit({
          severityNumber: severity,
          severityText,
          body,
          attributes: { "log.type": "console" },
        });
      };

    console.log = makeBridge(console.log.bind(console), SeverityNumber.INFO, "INFO");
    console.warn = makeBridge(console.warn.bind(console), SeverityNumber.WARN, "WARN");
    console.error = makeBridge(console.error.bind(console), SeverityNumber.ERROR, "ERROR");

    console.log(`[instrumentation] OTel Logs SDK started → OTLP endpoint: ${endpoint}`);

    // ── 3. OpenTelemetry Traces + Metrics SDK ────────────────────────────────
    const { NodeSDK } = await import("@opentelemetry/sdk-node");
    const { OTLPTraceExporter } = await import(
      "@opentelemetry/exporter-trace-otlp-grpc"
    );
    const { OTLPMetricExporter } = await import(
      "@opentelemetry/exporter-metrics-otlp-grpc"
    );
    const { PeriodicExportingMetricReader } = await import(
      "@opentelemetry/sdk-metrics"
    );
    const { getNodeAutoInstrumentations } = await import(
      "@opentelemetry/auto-instrumentations-node"
    );

    const sdk = new NodeSDK({
      resource,
      traceExporter: new OTLPTraceExporter({ url: endpoint }),
      metricReader: new PeriodicExportingMetricReader({
        exporter: new OTLPMetricExporter({ url: endpoint }),
        exportIntervalMillis: 30_000,
      }),
      instrumentations: [
        getNodeAutoInstrumentations({
          // Trace all incoming HTTP requests and outgoing fetch() calls
          "@opentelemetry/instrumentation-http": { enabled: true },
          // Disable very noisy / low-value instrumentations
          "@opentelemetry/instrumentation-dns": { enabled: false },
          "@opentelemetry/instrumentation-fs": { enabled: false },
        }),
      ],
    });

    sdk.start();

    // Graceful shutdown so spans and logs are flushed before the process exits
    process.on("SIGTERM", () => {
      Promise.all([
        sdk.shutdown(),
        loggerProvider.shutdown(),
      ]).catch((err) =>
        console.error("[instrumentation] OTel SDK shutdown error:", err)
      );
    });

    console.log(
      `[instrumentation] OTel SDK started → OTLP endpoint: ${endpoint}`
    );
  }
}
