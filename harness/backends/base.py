"""AgentBackend protocol — the interface every backend must implement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tracing import TraceContext


@runtime_checkable
class AgentBackend(Protocol):
    """Common interface for all agent backends (Claude, OpenAI, etc.)."""

    id: str
    """Unique identifier for this backend instance, matching backends.yaml."""

    async def run_query(
        self,
        prompt: str,
        session_id: str,
        is_new: bool,
        model: str | None = None,
        max_tokens: int | None = None,
        trace_context: TraceContext | None = None,
    ) -> list[str]:
        """Execute a prompt and return a list of collected text responses.

        Args:
            prompt:     The user prompt to execute.
            session_id: Stable identifier for the conversation session.
                        Backends use this to resume prior context.
            is_new:     True if this is the first message in the session,
                        False if the session already has history.
            model:      Optional per-call model override.
            max_tokens: Optional per-call token budget.
            trace_context: Optional W3C trace context (#468). Backends that
                        forward to downstream services should mint a fresh
                        child span_id for each outbound call.

        Returns:
            A list of text response chunks. May be empty if the backend
            produced no textual output (e.g. only tool calls).
        """
        ...

    async def close(self) -> None:
        """Release any resources held by this backend (connections, subprocesses, etc.)."""
        ...
