"""Streaming body-size cap for MCP request handlers (#1609, #1673, #1674).

Backends expose ``/mcp`` endpoints that accept JSON-RPC payloads. A
caller can lie about ``Content-Length`` (or omit it entirely under
chunked transfer encoding) and stream arbitrarily many bytes; without
a streaming check, ``await request.json()`` (which calls Starlette's
``request.body()``) concatenates every chunk into memory with no
internal cap, allowing OOM by a single hostile request.

This module provides ``read_capped_body``: an async helper that
stream-reads the request body chunk-by-chunk, aborts the moment the
running byte count exceeds the cap, and returns either the fully-
buffered body or a structured failure reason.

Used by every backend that hosts an ``/mcp`` endpoint
(``backends/claude``, ``backends/openai``, ``backends/gemini``) so the
defense lives in one place. Pair the streaming check with a fast-path
``Content-Length`` rejection at the call site for the cheap-reject
case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request


async def read_capped_body(request: Request, cap: int) -> tuple[bytes | None, str | None]:
    """Stream-read ``request`` body into a bounded buffer.

    Returns ``(body_bytes, None)`` on success or ``(None, reason)`` on
    failure where ``reason`` is one of:

    - ``"body_too_large"`` — actual bytes received exceeded ``cap``.
      The caller should respond with HTTP 413.
    - ``"parse_error"`` — the underlying ASGI receive raised. The
      caller should respond with HTTP 400 and a JSON-RPC parse-error
      envelope.

    The streaming check is the actual enforcement: a hostile or buggy
    caller may declare a small (or absent) ``Content-Length`` and then
    send arbitrarily many bytes, so we MUST count actual bytes
    received and abort BEFORE buffering them into ``json``.
    """
    buf = bytearray()
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            if len(buf) + len(chunk) > cap:
                return None, "body_too_large"
            buf.extend(chunk)
    except Exception:
        return None, "parse_error"
    return bytes(buf), None
