"""
adk_telemetry.py — OpenTelemetry span labeling for Google ADK tool calls.

The google-adk framework runs tools (get_all_context, submit_tailored_resume,
render_latex, read_state …) sequentially within a session.  By default these
show up in traces only as unlabelled internal HTTP calls.

This module wires ADK's ``before_tool_callback`` / ``after_tool_callback``
hooks to create a named OTel span per tool call, so you can see in OpenObserve:

    frontend
    └── agent (FastAPI route)
        └── adk.tool.get_all_context
        └── adk.tool.submit_tailored_resume
            └── (httpx) POST compilation-service/render   ← from HttpxInstrumentor
        └── adk.tool.render_latex

Usage in agent.py::

    from adk_telemetry import adk_before_tool, adk_after_tool

    tailoring_agent = Agent(
        ...
        before_tool_callback=adk_before_tool,
        after_tool_callback=adk_after_tool,
    )

Implementation notes
--------------------
- ADK calls tools **sequentially** within a single session, so using a
  ContextVar keyed by tool-name is safe.  Concurrent sessions each have their
  own execution context so there is no cross-session contamination.
- ``before_tool_callback`` must return ``None`` to allow the tool to run
  normally.  Returning a non-None value would short-circuit execution.
- ``after_tool_callback`` must return the unmodified ``tool_response`` (or a
  replacement dict) to pass the result back to the model.
- Spans are ended in the after-callback so the duration covers the full tool
  execution time, including any IO the tool does.
"""

import logging
import contextvars
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("resume.agent.adk")

# Maps tool_name → (Span, token) within the current async execution context.
# Using ContextVar means each asyncio Task (= each ADK session coroutine) has
# its own isolated store even when many sessions run concurrently.
_span_store: contextvars.ContextVar[dict[str, tuple]] = contextvars.ContextVar(
    "_adk_span_store", default={}
)


def adk_before_tool(tool, args: dict, tool_context) -> None:
    """ADK before_tool_callback — starts an OTel span for the tool call.

    Args:
        tool:         The ADK Tool object being invoked.
        args:         The keyword arguments the model passed to the tool.
        tool_context: ADK ToolContext carrying session / agent metadata.

    Returns:
        None — tells ADK to proceed with the normal tool execution.
    """
    import os
    disable_telemetry = os.getenv("DISABLE_TELEMETRY", "true").lower() in ("true", "1", "yes")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if disable_telemetry or not endpoint:
        return None

    tool_name: str = getattr(tool, "name", str(tool))
    agent_name: str = getattr(tool_context, "agent_name", "unknown")

    # Truncate args to avoid storing PII / giant payloads in spans
    safe_args = str(args)[:500]

    span = tracer.start_span(
        f"adk.tool.{tool_name}",
        attributes={
            "adk.tool.name": tool_name,
            "adk.agent.name": agent_name,
            "adk.tool.args_preview": safe_args,
        },
    )

    # Use start_span (not start_as_current_span) so we control the lifetime
    # across the before/after boundary.  We activate it as current context so
    # any child spans (e.g. httpx calls inside the tool) are parented here.
    ctx = trace.use_span(span, end_on_exit=False)
    ctx.__enter__()

    # Persist span + context token so after_callback can retrieve them
    store = dict(_span_store.get())
    store[tool_name] = (span, ctx)
    _span_store.set(store)

    return None   # ← must be None to let ADK execute the tool


def adk_after_tool(tool, args: dict, tool_context, tool_response: Any) -> Any:
    """ADK after_tool_callback — ends the OTel span and records outcome.

    Args:
        tool:          The ADK Tool object that was invoked.
        args:          The keyword arguments that were passed to the tool.
        tool_context:  ADK ToolContext.
        tool_response: The dict/value returned by the tool.

    Returns:
        tool_response unchanged — ADK requires we pass it through.
    """
    import os
    disable_telemetry = os.getenv("DISABLE_TELEMETRY", "true").lower() in ("true", "1", "yes")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if disable_telemetry or not endpoint:
        return tool_response

    tool_name: str = getattr(tool, "name", str(tool))

    store = dict(_span_store.get())
    entry = store.pop(tool_name, None)
    _span_store.set(store)

    if entry is None:
        # Shouldn't happen, but don't crash the agent over missing telemetry
        logger.warning("adk_after_tool: no span found for tool '%s'", tool_name)
        return tool_response

    span, ctx = entry

    try:
        # Record whether the tool reported an error
        if isinstance(tool_response, dict):
            if "error" in tool_response:
                span.set_status(Status(StatusCode.ERROR, str(tool_response["error"])))
                span.set_attribute("adk.tool.error", str(tool_response["error"]))
            else:
                span.set_status(Status(StatusCode.OK))
                span.set_attribute("adk.tool.success", True)
        else:
            span.set_status(Status(StatusCode.OK))
            span.set_attribute("adk.tool.success", True)
    finally:
        ctx.__exit__(None, None, None)
        span.end()

    return tool_response   # ← pass through to ADK / model unchanged
