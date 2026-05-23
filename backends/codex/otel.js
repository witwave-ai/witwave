import { SpanKind, SpanStatusCode, context, propagation, trace } from "@opentelemetry/api";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";

const DEFAULT_IN_MEMORY_SPANS = 1000;

let initialized = false;
let tracer = trace.getTracer("witwave-codex");
let spanRing = [];
let spanRingCap = 0;

function parseBool(value) {
  if (value === undefined || value === null || value === "") {
    return false;
  }
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function parseSpanCap(value, defaultValue) {
  if (value === undefined || value === null || value === "") {
    return defaultValue;
  }
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : defaultValue;
}

function hrTimeToMicroseconds(value) {
  if (!Array.isArray(value) || value.length < 2) {
    return 0;
  }
  return Number(value[0] || 0) * 1_000_000 + Math.floor(Number(value[1] || 0) / 1000);
}

function spanKindName(kind) {
  switch (kind) {
    case SpanKind.CLIENT:
      return "client";
    case SpanKind.SERVER:
      return "server";
    case SpanKind.PRODUCER:
      return "producer";
    case SpanKind.CONSUMER:
      return "consumer";
    default:
      return "internal";
  }
}

function spanKindForName(kind) {
  switch (String(kind || "").toLowerCase()) {
    case "client":
      return SpanKind.CLIENT;
    case "server":
      return SpanKind.SERVER;
    case "producer":
      return SpanKind.PRODUCER;
    case "consumer":
      return SpanKind.CONSUMER;
    default:
      return SpanKind.INTERNAL;
  }
}

function stringAttributes(attributes = {}) {
  return Object.fromEntries(
    Object.entries(attributes)
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(([key, value]) => [key, String(value)]),
  );
}

class InMemorySpanProcessor {
  onStart() {}

  onEnd(span) {
    if (spanRingCap <= 0) {
      return;
    }
    spanRing.push(span);
    if (spanRing.length > spanRingCap) {
      spanRing = spanRing.slice(-spanRingCap);
    }
  }

  shutdown() {
    return Promise.resolve();
  }

  forceFlush() {
    return Promise.resolve();
  }
}

export function initOtelIfEnabled({ serviceName, resourceAttributes = {} } = {}) {
  if (initialized) {
    return true;
  }

  const otlpEnabled = parseBool(process.env.OTEL_ENABLED);
  const inMemoryCap = parseSpanCap(process.env.OTEL_IN_MEMORY_SPANS, DEFAULT_IN_MEMORY_SPANS);
  const inMemoryEnabled = inMemoryCap > 0;
  if (!otlpEnabled && !inMemoryEnabled) {
    return false;
  }

  const resolvedServiceName = serviceName || process.env.OTEL_SERVICE_NAME || "codex";
  const resource = resourceFromAttributes(
    stringAttributes({
      "service.name": resolvedServiceName,
      "service.namespace": process.env.AGENT_OWNER || process.env.AGENT_NAME || "",
      "service.instance.id": process.env.AGENT_ID || "",
      agent: process.env.AGENT_OWNER || process.env.AGENT_NAME || "",
      agent_id: process.env.AGENT_ID || "",
      backend: "codex",
      ...resourceAttributes,
    }),
  );

  try {
    const spanProcessors = [];
    if (otlpEnabled) {
      spanProcessors.push(new BatchSpanProcessor(new OTLPTraceExporter()));
    }
    if (inMemoryEnabled) {
      spanRingCap = inMemoryCap;
      spanRing = [];
      spanProcessors.push(new InMemorySpanProcessor());
    }
    const provider = new NodeTracerProvider({ resource, spanProcessors });
    provider.register();
    tracer = trace.getTracer(resolvedServiceName);
    initialized = true;
    return true;
  } catch (error) {
    console.warn(`codex backend: OTel init failed; tracing disabled: ${error?.message || error}`);
    spanRingCap = 0;
    spanRing = [];
    return false;
  }
}

export function extractOtelContext(carrier = {}) {
  return propagation.extract(context.active(), carrier);
}

export function setSpanError(span, error) {
  if (!span) {
    return;
  }
  span.recordException?.(error);
  span.setStatus?.({ code: SpanStatusCode.ERROR, message: error?.message || String(error) });
}

export async function runWithSpan(name, { kind = "internal", attributes = {}, parentContext } = {}, fn) {
  const start = async () =>
    await tracer.startActiveSpan(
      name,
      { kind: spanKindForName(kind), attributes: stringAttributes(attributes) },
      async (span) => {
        try {
          return await fn(span);
        } catch (error) {
          setSpanError(span, error);
          throw error;
        } finally {
          span.end();
        }
      },
    );
  return parentContext ? await context.with(parentContext, start) : await start();
}

function spanToJaeger(span) {
  const spanContext = span.spanContext();
  const parentContext = span.parentSpanContext;
  const startUs = hrTimeToMicroseconds(span.startTime);
  const durationUs = hrTimeToMicroseconds(span.duration);
  const serviceName = span.resource?.attributes?.["service.name"] || process.env.OTEL_SERVICE_NAME || "";
  const tags = Object.entries(span.attributes || {}).map(([key, value]) => ({
    key,
    type: typeof value === "boolean" ? "bool" : typeof value === "number" ? "number" : "string",
    value,
  }));
  tags.push({ key: "span.kind", type: "string", value: spanKindName(span.kind) });

  const statusCode = span.status?.code === SpanStatusCode.ERROR ? "ERROR" : "OK";
  const statusMessage = span.status?.message || "";
  if (statusCode === "ERROR") {
    tags.push({ key: "error", type: "bool", value: true });
  }

  return {
    traceID: spanContext.traceId,
    spanID: spanContext.spanId,
    operationName: span.name,
    startTime: startUs,
    duration: durationUs,
    tags,
    references: parentContext
      ? [{ refType: "CHILD_OF", traceID: parentContext.traceId, spanID: parentContext.spanId }]
      : [],
    processID: "p1",
    process: { serviceName },
    status: { code: statusCode, message: statusMessage },
  };
}

export function getInMemoryTraces() {
  if (spanRing.length === 0) {
    return [];
  }

  const byTrace = new Map();
  for (const span of [...spanRing]) {
    const traceId = span.spanContext().traceId;
    if (!byTrace.has(traceId)) {
      byTrace.set(traceId, []);
    }
    byTrace.get(traceId).push(span);
  }

  const traces = [];
  for (const [traceId, spans] of byTrace.entries()) {
    const jsonSpans = spans.map(spanToJaeger).filter((span) => span.spanID);
    if (jsonSpans.length === 0) {
      continue;
    }
    const serviceName = spans[0]?.resource?.attributes?.["service.name"] || process.env.OTEL_SERVICE_NAME || "";
    const newestEnd = Math.max(...spans.map((span) => hrTimeToMicroseconds(span.endTime)));
    traces.push({
      traceID: traceId,
      spans: jsonSpans,
      processes: { p1: { serviceName } },
      _newestEnd: newestEnd,
    });
  }

  traces.sort((a, b) => b._newestEnd - a._newestEnd);
  return traces.map(({ _newestEnd, ...traceItem }) => traceItem);
}
