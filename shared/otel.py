"""Shared OpenTelemetry bootstrap + helper layer (#469).

Used by harness and the LLM backends (claude, openai, gemini). The ``echo``
backend does not import this module — tracing is out of scope for the
zero-dependency stub.
The module is entirely optional: when ``OTEL_ENABLED`` is falsy,
:func:`init_otel_if_enabled` does nothing and every helper below routes to
the OTel API's built-in no-op tracer. Call sites can therefore use
:func:`start_span` unconditionally without paying a runtime cost or pulling
in exporter-side imports when tracing is disabled.

When enabled, spans are emitted via OTLP/HTTP to the endpoint given by
``OTEL_EXPORTER_OTLP_ENDPOINT`` (standard OTel env var). Resource
attributes come from ``OTEL_SERVICE_NAME`` plus a small set of per-agent
labels (``agent``, ``agent_id``, ``backend``) stamped at init-time from
the container's own environment. Sampling is controlled by the standard
OTel env vars (``OTEL_TRACES_SAMPLER`` etc.) so operators can dial it
without touching code.

The OTel propagator uses W3C trace-context under the hood, so spans
emitted here will correlate with trace_ids produced by the bare
``harness/tracing.py`` helpers — the two layers share the wire format by
construction.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_otel_enabled: bool = False
_otel_tracer: Any = None  # opentelemetry.trace.Tracer | None, lazily populated

SPAN_KIND_SERVER = "server"
SPAN_KIND_CLIENT = "client"
SPAN_KIND_INTERNAL = "internal"

# Default capacity for the in-memory span ring buffer. Each span is
# ~1-2 KB of structured data, so 1000 spans ≈ 1-2 MB per container.
# Operators who want more (or less) history override via
# OTEL_IN_MEMORY_SPANS. Setting to 0 disables the in-memory sink while
# keeping OTLP export intact.
_IN_MEMORY_CAP_DEFAULT = 1000

# Global ring buffer of finished ReadableSpan objects, newest last.
# Populated by InMemorySpanProcessor below when OTel is enabled. We use
# collections.deque(maxlen=cap) so append + implicit eviction is a
# single atomic op under CPython's GIL (#662) — the previous list +
# `del ring[:excess]` composite was not atomic across on_end calls from
# the BatchSpanProcessor worker thread.
_span_ring: deque[Any] | None = None
# Capacity snapshot for convenience; set at init time.
_span_ring_cap: int = 0


def init_otel_if_enabled(
    service_name: str | None = None,
    resource_attributes: dict[str, str] | None = None,
) -> bool:
    """Initialise the OTel TracerProvider + OTLP exporter when enabled.

    Reads ``OTEL_ENABLED`` (default ``false``). When truthy, configures a
    single ``BatchSpanProcessor`` pointing at ``OTEL_EXPORTER_OTLP_ENDPOINT``
    and registers it as the process-wide TracerProvider. Safe to call more
    than once — subsequent calls are a no-op. Returns True when OTel is now
    active in this process, False when it stayed disabled or failed.

    *service_name* defaults to the ``OTEL_SERVICE_NAME`` env var, or a
    generic fallback. *resource_attributes* are merged on top of the
    baseline ``{agent, agent_id, backend}`` attributes sourced from env.
    """
    global _otel_enabled, _otel_tracer
    if _otel_enabled:
        return True
    # Two independent toggles:
    #   - OTEL_ENABLED → controls OTLP export to an external collector
    #   - OTEL_IN_MEMORY_SPANS → controls the in-cluster ring buffer
    # We initialise a TracerProvider when EITHER is active so the dashboard's
    # zero-config in-cluster Traces view works without requiring operators
    # to wire up a collector first.
    _otlp_on = os.environ.get("OTEL_ENABLED", "false").lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )
    try:
        _cap = int(os.environ.get("OTEL_IN_MEMORY_SPANS") or _IN_MEMORY_CAP_DEFAULT)
    except ValueError:
        _cap = _IN_MEMORY_CAP_DEFAULT
    _in_memory_on = _cap > 0
    if not _otlp_on and not _in_memory_on:
        return False
    try:
        from opentelemetry import trace as _otel_trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError as exc:
        logger.warning("OTel requested but opentelemetry packages missing: %s — staying disabled.", exc)
        return False

    _service = service_name or os.environ.get("OTEL_SERVICE_NAME") or "witwave"
    # #1291: Populate OTel semantic-convention Resource attributes
    # (service.name, service.namespace, service.instance.id) alongside
    # the legacy flat attributes (agent, agent_id, backend). Downstream
    # collectors that key off the OTel standard attrs (Tempo, Jaeger's
    # OTLP ingress, many SaaS offerings) now get a clean service tree
    # without forcing operators to relabel. We keep the legacy attrs so
    # pre-existing dashboards keep resolving — dropping them would be a
    # breaking change.
    attrs: dict[str, str] = {
        "service.name": _service,
        "service.namespace": os.environ.get("AGENT_OWNER") or os.environ.get("AGENT_NAME") or "",
        "service.instance.id": os.environ.get("AGENT_ID", ""),
        "agent": os.environ.get("AGENT_OWNER") or os.environ.get("AGENT_NAME") or "",
        "agent_id": os.environ.get("AGENT_ID", ""),
        "backend": os.environ.get("BACKEND_ID", ""),
    }
    # When running inside a backend container, prefer BACKEND_NAME /
    # BACKEND_ID for service.name so emitted spans self-identify as
    # (e.g.) "iris-claude" rather than the generic "witwave" fallback.
    _backend_name = os.environ.get("BACKEND_NAME") or os.environ.get("BACKEND_ID") or ""
    if _backend_name and not service_name and not os.environ.get("OTEL_SERVICE_NAME"):
        attrs["service.name"] = _backend_name
        _service = _backend_name
    if resource_attributes:
        attrs.update({k: str(v) for k, v in resource_attributes.items()})
    attrs = {k: v for k, v in attrs.items() if v}

    try:
        resource = Resource.create(attrs)
        provider = TracerProvider(resource=resource)
        if _otlp_on:
            # Primary exporter: OTLP/HTTP to whatever OTEL_EXPORTER_OTLP_ENDPOINT
            # points at. Failure to export is handled internally by the batch
            # processor.
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            except Exception as exc:  # pragma: no cover - optional dep path
                logger.warning("OTel OTLP exporter unavailable: %s — in-memory sink still active.", exc)
        # Secondary sink: in-memory ring buffer so the dashboard can render
        # traces without requiring an external Jaeger/Tempo. Always installed
        # unless OTEL_IN_MEMORY_SPANS=0.
        if _in_memory_on:
            _install_in_memory_sink(provider)
        _otel_trace.set_tracer_provider(provider)
        _otel_tracer = _otel_trace.get_tracer(_service)
    except Exception as exc:
        logger.warning("OTel init failed — staying disabled: %s", exc)
        return False

    _otel_enabled = True
    logger.info(
        "OTel tracing initialised: service=%s otlp=%s in_memory=%s attrs=%s",
        _service,
        _otlp_on,
        _in_memory_on,
        attrs,
    )
    return True


def otel_enabled() -> bool:
    """Return True iff OTel is active in this process."""
    return _otel_enabled


def _get_tracer() -> Any:
    """Return the active tracer, or the OTel-API default no-op tracer."""
    global _otel_tracer
    if _otel_tracer is not None:
        return _otel_tracer
    try:
        from opentelemetry import trace as _otel_trace

        return _otel_trace.get_tracer("witwave")
    except ImportError:
        return None


def extract_otel_context(carrier: dict[str, str] | None) -> Any:
    """Extract an OTel Context from a carrier dict (headers or metadata)."""
    if not carrier:
        return None
    try:
        from opentelemetry.propagate import extract

        return extract(carrier)
    except Exception:
        return None


def inject_traceparent(carrier: dict[str, str], context: Any = None) -> None:
    """Inject the current OTel span's traceparent into *carrier*.

    When OTel is disabled (no active span), this is a no-op.
    """
    try:
        from opentelemetry.propagate import inject

        inject(carrier, context=context)
    except Exception:
        pass


class TraceparentASGIMiddleware:
    """Tiny ASGI middleware that attaches the inbound ``traceparent`` header.

    The A2A SDK auto-traces its server-side classes via ``@trace_class`` —
    those spans use whatever OpenTelemetry context is current when the
    JSON-RPC handler runs. Without this middleware they are orphans
    (their trace_id is freshly minted, so the harness's upstream trace
    does not continue through the backend).

    This middleware reads the ``traceparent`` (and ``tracestate``) headers
    from the ASGI scope, extracts the OTel context, and attaches it for
    the duration of the request. After the middleware returns, the
    context is detached. Safe to mount unconditionally — when OTel is
    disabled or the header is absent it is a no-op.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        except Exception:
            headers = {}
        tp = headers.get("traceparent")
        if not tp:
            await self.app(scope, receive, send)
            return
        try:
            from opentelemetry import context as _ctx_mod
            from opentelemetry.propagate import extract

            ctx = extract(headers)
            token = _ctx_mod.attach(ctx)
        except Exception:
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            try:
                _ctx_mod.detach(token)
            except Exception:
                pass


@contextmanager
def start_span(
    name: str,
    kind: str = SPAN_KIND_INTERNAL,
    parent_context: Any = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Start an OTel span (or a no-op stand-in when OTel is disabled).

    Use as a context manager::

        with start_span("a2a.execute", kind="server") as span:
            ...
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    try:
        from opentelemetry import trace as _otel_trace

        span_kind_map = {
            SPAN_KIND_SERVER: _otel_trace.SpanKind.SERVER,
            SPAN_KIND_CLIENT: _otel_trace.SpanKind.CLIENT,
            SPAN_KIND_INTERNAL: _otel_trace.SpanKind.INTERNAL,
        }
        otel_kind = span_kind_map.get(kind, _otel_trace.SpanKind.INTERNAL)
    except ImportError:
        yield None
        return

    with tracer.start_as_current_span(
        name,
        kind=otel_kind,
        context=parent_context,
        attributes=attributes or {},
    ) as span:
        yield span


def set_span_error(span: Any, exc: BaseException) -> None:
    """Record *exc* on *span* and mark it as errored. No-op when span is None."""
    if span is None:
        return
    try:
        from opentelemetry import trace as _otel_trace

        span.record_exception(exc)
        span.set_status(_otel_trace.Status(_otel_trace.StatusCode.ERROR, str(exc)))
    except Exception:
        pass


# ── In-memory span sink (#643-followup, dashboard OTel-without-collector) ───
#
# A second span processor that captures finished spans into a bounded
# ring buffer so the dashboard can query /otel/spans on each container.
# Complements — does not replace — the OTLP exporter above: when both
# are configured, spans go to both sinks, so wiring a real collector
# later is additive, not a migration.


def _install_in_memory_sink(provider: Any) -> None:
    """Attach InMemorySpanProcessor to *provider* sized by env."""
    global _span_ring, _span_ring_cap
    try:
        cap = int(os.environ.get("OTEL_IN_MEMORY_SPANS") or _IN_MEMORY_CAP_DEFAULT)
    except ValueError:
        cap = _IN_MEMORY_CAP_DEFAULT
    if cap <= 0:
        logger.info("OTel in-memory span sink disabled (OTEL_IN_MEMORY_SPANS=%d).", cap)
        return
    _span_ring = deque(maxlen=cap)
    _span_ring_cap = cap
    try:
        provider.add_span_processor(_InMemorySpanProcessor())
        logger.info("OTel in-memory span sink active (capacity=%d).", cap)
    except Exception as exc:
        logger.warning("OTel in-memory sink install failed — continuing without: %s", exc)
        _span_ring = None
        _span_ring_cap = 0


# Allow-listed span names whose completion should be surfaced on the
# harness SSE event stream as ``trace.span`` events (#1110 phase 3).
# Keep this short — the stream carries the *high-level* shape of each
# turn, not every instrumented function. Operators who want the full
# span tree drill down via /api/traces on each backend.
_TRACE_SPAN_EMIT_ALLOWLIST: set[str] = {
    "llm.request",
    "shell",
    "mcp.handler",
    "backend.mcp.tools_call",
}


def _emit_trace_span_event(span: Any) -> None:
    """Best-effort emit of a ``trace.span`` event to the harness.

    Called from ``_InMemorySpanProcessor.on_end``. Swallows every
    exception — span persistence must never break on an event-emit
    failure. Only fires for spans whose ``name`` is in
    ``_TRACE_SPAN_EMIT_ALLOWLIST`` so we don't flood the stream with
    per-sub-span noise.
    """
    try:
        name = getattr(span, "name", "") or ""
        if name not in _TRACE_SPAN_EMIT_ALLOWLIST:
            return
        start_ns = int(getattr(span, "start_time", 0) or 0)
        end_ns = int(getattr(span, "end_time", 0) or 0)
        duration_ms = max(0, (end_ns - start_ns) // 1_000_000) if end_ns >= start_ns else 0
        # Status → "ok" | "error". Default to ok on any inspection failure.
        status = "ok"
        try:
            _st = getattr(span, "status", None)
            _code = getattr(_st, "status_code", None) if _st is not None else None
            if _code is not None and getattr(_code, "name", "") == "ERROR":
                status = "error"
        except Exception:
            pass
        # Service name from the span's resource, matching the shape the
        # /api/traces Jaeger JSON carries.
        service = ""
        try:
            res = getattr(span, "resource", None)
            if res is not None:
                service = (res.attributes or {}).get("service.name", "") or ""
        except Exception:
            service = ""
        payload: dict[str, Any] = {
            "span_name": name,
            "duration_ms": int(duration_ms),
            "status": status,
            "service": service or (os.environ.get("OTEL_SERVICE_NAME") or ""),
        }
        # Import schedule_event_post lazily so OTel bootstrap order
        # doesn't depend on hook_events being importable at module
        # load. Any failure here is silent — on_end never raises.
        try:
            from hook_events import schedule_event_post as _sep  # type: ignore
        except Exception:
            try:
                from shared.hook_events import schedule_event_post as _sep  # type: ignore
            except Exception:
                return
        agent_id = os.environ.get("AGENT_OWNER") or os.environ.get("AGENT_NAME")
        _sep("trace.span", payload, agent_id=agent_id)
    except Exception:
        # Never raise out of on_end — the SpanProcessor contract must hold.
        return


class _InMemorySpanProcessor:
    """Append finished spans to the global ring buffer.

    Implements the OTel SpanProcessor interface via duck typing; we don't
    inherit from SpanProcessor to keep this file free of SDK imports at
    module scope. Thread-safe because collections.deque(maxlen=cap).append
    is atomic under CPython's GIL and performs implicit left-eviction —
    no composite trim required (#662).
    """

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        return None

    def on_end(self, span: Any) -> None:
        ring = _span_ring
        if ring is None:
            return
        # deque.append + maxlen eviction is a single atomic op under the
        # GIL, so BatchSpanProcessor's worker thread can't interleave an
        # append with the old manual trim.
        ring.append(span)
        # Fire trace.span event (#1110 phase 3) — best-effort, filtered to
        # a small allow-list of top-level span names so the stream stays
        # useful without flooding.
        _emit_trace_span_event(span)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _format_span_id(val: Any, width: int) -> str:
    """Render a span/trace id as a zero-padded hex string.

    *width* must be explicit — 32 for trace_id (128-bit), 16 for span_id
    (64-bit). #1285: an earlier magnitude-based heuristic treated any
    trace_id whose integer value happened to fit in 64 bits as a
    span_id, producing malformed 16-char trace IDs whenever the leading
    64 bits of a trace_id were zero. Forcing callers to pass width
    removes that ambiguity.
    """
    try:
        n = int(val)
    except Exception:
        return ""
    return format(n, f"0{width}x")


def get_in_memory_traces() -> list[dict]:
    """Return the in-memory spans grouped by trace_id, newest-first.

    Shape matches the subset of Jaeger's v1 API the dashboard consumes:

        [
          {
            "traceID": "<hex>",
            "spans": [
              {"spanID": "<hex>", "operationName": "...", "startTime": <us>,
               "duration": <us>, "tags": [{"key": "...", "value": "..."}],
               "references": [{"refType": "CHILD_OF",
                               "traceID": "<hex>", "spanID": "<hex>"}],
               "process": {"serviceName": "..."},
               "status": {"code": "OK"|"ERROR", "message": "..."}}
            ],
            "processes": {"p1": {"serviceName": "..."}},
          },
          …
        ]

    The view flattens further; this module just hands over a JSON-able
    list. Returns empty when in-memory capture is disabled or no spans
    have been recorded yet.
    """
    ring = _span_ring
    if not ring:
        return []
    # Snapshot the deque into a list so iteration is stable even if the
    # BatchSpanProcessor worker thread appends concurrently — list(deque)
    # is atomic under the GIL.
    snapshot = list(ring)
    by_trace: dict[str, list[Any]] = {}
    for span in snapshot:
        try:
            ctx = span.get_span_context()
            tid = _format_span_id(ctx.trace_id, 32)
        except Exception:
            continue
        by_trace.setdefault(tid, []).append(span)

    # Newest first — trace ordering by most-recently-ended root.
    def _max_end(spans: list[Any]) -> int:
        try:
            return max(s.end_time or 0 for s in spans)
        except Exception:
            return 0

    traces = []
    for tid, spans in by_trace.items():
        json_spans = [_span_to_jaeger_json(s) for s in spans]
        if not json_spans:
            continue
        service = ""
        try:
            res = spans[0].resource
            if res is not None:
                service = (res.attributes or {}).get("service.name", "") or ""
        except Exception:
            service = ""
        traces.append(
            {
                "traceID": tid,
                "spans": json_spans,
                "processes": {"p1": {"serviceName": service}},
                "_end_ns": _max_end(spans),
            }
        )
    traces.sort(key=lambda t: t.pop("_end_ns", 0), reverse=True)
    return traces


def _span_to_jaeger_json(span: Any) -> dict:
    """Map an OTel ReadableSpan to Jaeger's JSON wire shape."""
    try:
        ctx = span.get_span_context()
        span_id = _format_span_id(ctx.span_id, 16)
        trace_id = _format_span_id(ctx.trace_id, 32)
    except Exception:
        return {}
    name = getattr(span, "name", "") or ""
    start_ns = int(getattr(span, "start_time", 0) or 0)
    end_ns = int(getattr(span, "end_time", 0) or 0)
    # Jaeger times are microseconds.
    start_us = start_ns // 1000
    duration_us = max(0, (end_ns - start_ns) // 1000) if end_ns >= start_ns else 0

    tags: list[dict] = []
    attrs = dict(getattr(span, "attributes", {}) or {})
    for k, v in attrs.items():
        tags.append({"key": str(k), "type": "string", "value": str(v)})
    kind = getattr(span, "kind", None)
    if kind is not None:
        tags.append({"key": "span.kind", "type": "string", "value": str(kind).split(".")[-1].lower()})

    # Status → Jaeger-style "error" tag + status block.
    status_code = "OK"
    status_msg = ""
    try:
        status = span.status
        if status is not None:
            sc = getattr(status, "status_code", None)
            if sc is not None and str(sc).endswith("ERROR"):
                status_code = "ERROR"
                tags.append({"key": "error", "type": "bool", "value": True})
                status_msg = getattr(status, "description", "") or ""
    except Exception:
        pass

    references: list[dict] = []
    try:
        parent = getattr(span, "parent", None)
        if parent is not None:
            references.append(
                {
                    "refType": "CHILD_OF",
                    "traceID": _format_span_id(parent.trace_id, 32),
                    "spanID": _format_span_id(parent.span_id, 16),
                }
            )
    except Exception:
        pass

    service = ""
    try:
        res = span.resource
        if res is not None:
            service = (res.attributes or {}).get("service.name", "") or ""
    except Exception:
        pass

    return {
        "traceID": trace_id,
        "spanID": span_id,
        "operationName": name,
        "startTime": start_us,
        "duration": duration_us,
        "tags": tags,
        "references": references,
        "processID": "p1",
        "process": {"serviceName": service},
        "status": {"code": status_code, "message": status_msg},
    }
