"""Regression tests for the 2026-05-11 A2A response-path ReadError bug.

Symptom (observed across multiple incidents on 2026-05-10 and 2026-05-11):
iris's release-skill replies were silently dropped at the A2A layer.
From the caller's perspective (zora), iris appeared stuck — release
dispatches at 22:30Z (2026-05-10), 15:00Z (2026-05-11), and 17:26Z
(2026-05-11) all "hung" with zero artifacts. From iris's side, the
release skill ran cleanly and returned a reply within ~2 minutes;
the response was lost in transit.

Root cause (per evan's risk-work analysis, 2026-05-11):

1. `httpx.ReadError` fires on outbound POSTs to localhost:8001 when
   the server-side connection has been reaped by uvicorn's keepalive
   reaper while the client still considers the connection pooled.
2. The retry-eligible tuple at the response-read site listed
   ReadTimeout, WriteTimeout, PoolTimeout — but not ReadError.
   Unhandled ReadError fell through to `except Exception` which
   re-raised without retry, surfacing the call as -32603 to the
   caller. The server-side LLM work had already completed; only the
   response transport failed.
3. The `httpx.Limits` config didn't set `keepalive_expiry`, defaulting
   to 5s. Uvicorn's default keepalive idle timeout is ~5s. Under
   sustained slow-POST traffic the timings overlapped and the
   server-side reaper closed pooled connections the client still
   considered valid.

Fix (this commit):

1. Add `httpx.ReadError` to the retry-eligible tuple in
   `_post_with_retry` at the read site. ReadError joins ReadTimeout as a
   network-class read failure.

   REFINED LATER (slow-read guard): a read failure is only SAFELY retried
   when it is FAST — the keepalive reaper race, where the turn never ran.
   A SLOW read failure (elapsed > A2A_RETRY_FAST_ONLY_MS) means the backend
   most likely completed the (long) turn and only the response transport
   dropped; retrying would re-run + re-bill it and, for agentic backends,
   duplicate side effects (commits/pushes). The slow case is now refused by
   the slow-read guard (`_SlowReadGuardRefusal`, see
   `test_slow_read_guard_present`), mirroring the #1457 slow-5xx guard;
   only `A2A_RETRY_POLICY=always` retries it. The original premise here —
   "retried regardless of policy … OR the retry is idempotent" — was wrong
   for non-idempotent agentic turns and is superseded.
2. Set `keepalive_expiry=2.0` on the shared AsyncClient's Limits so
   the client retires keepalive connections faster than uvicorn's
   default 5s reaper. The reaper-race window closes.

These tests assert the fix is in place via source inspection — strong
enough to catch a future regression that removes either guard. The
test is intentionally source-level (not a httpx-mock integration test)
because the failure mode is configuration drift, not subtle logic.
"""

from __future__ import annotations

import re
from pathlib import Path

HARNESS = Path(__file__).parent
A2A_PATH = HARNESS / "backends" / "a2a.py"


def test_readerror_in_retry_tuple() -> None:
    """ReadError must be in the retry-eligible exception tuple.

    The tuple lives in `_post_with_retry` alongside ReadTimeout /
    WriteTimeout / PoolTimeout. Without ReadError, response-path
    transport errors fall through to `except Exception` which raises
    without retry — the surface that caused iris's lost replies.
    """
    src = A2A_PATH.read_text()
    match = re.search(
        r"except \([^)]*ReadTimeout[^)]*\)",
        src,
        re.DOTALL,
    )
    assert match is not None, "expected an `except (...ReadTimeout...)` clause in harness/backends/a2a.py"
    clause = match.group(0)
    assert "ReadError" in clause, (
        "httpx.ReadError must be in the retry-eligible tuple alongside ReadTimeout/WriteTimeout/PoolTimeout. "
        "Without it, response-path errors silently drop replies (root cause of the 2026-05-11 "
        f"iris-release-stuck cascade). Found: {clause}"
    )


def test_httpx_limits_keepalive_expiry_set() -> None:
    """httpx.Limits must explicitly set keepalive_expiry shorter than the upstream's reaper.

    Default keepalive_expiry is 5.0s. Uvicorn's default keepalive idle
    timeout is also ~5s. The matching timeouts produce a reaper race:
    server reaps a pooled connection that the client still considers
    valid; the next reuse fails with ReadError. Setting an explicit
    short value (≤3s) makes the client retire connections before the
    server's reaper can race the close.
    """
    src = A2A_PATH.read_text()
    matches = re.findall(r"httpx\.Limits\([^)]*\)", src)
    assert matches, "expected httpx.Limits(...) call in harness/backends/a2a.py"
    for m in matches:
        assert "keepalive_expiry" in m, (
            "httpx.Limits call missing keepalive_expiry= param. Without an explicit value, the default "
            "5.0s matches uvicorn's server-side reaper and produces the connection-reaper race that "
            "manifested as the 2026-05-11 lost-reply bug. Set keepalive_expiry to ≤3.0 to retire pooled "
            f"connections client-side first. Found: {m}"
        )
        # Pull the numeric value and assert it's <= 3.0 (per the rationale above).
        ke_match = re.search(r"keepalive_expiry\s*=\s*([0-9.]+)", m)
        assert ke_match is not None, f"unable to parse keepalive_expiry value from {m}"
        value = float(ke_match.group(1))
        assert value <= 3.0, (
            f"keepalive_expiry should be <=3.0s to safely undercut uvicorn's default 5s reaper. Got {value} in {m}"
        )


def test_retry_policy_comment_mentions_readerror() -> None:
    """The retry-policy header comment must mention ReadError as a retried network-class error.

    The comment is the load-bearing place where future contributors learn which exceptions retry
    regardless of policy. If ReadError is in the tuple but missing from the comment, the next person
    editing this code may forget the comment update and ship inconsistent docstrings.
    """
    src = A2A_PATH.read_text()
    # The retry-policy comment block is in the first ~150 lines.
    header = "\n".join(src.splitlines()[:150])
    assert "ReadError" in header, (
        "The retry-policy comment header must enumerate ReadError alongside ConnectTimeout/ReadTimeout/"
        "ConnectError as a retried network-class error. Comment + code must stay in sync to prevent "
        "the next contributor from removing the ReadError guard."
    )


def test_slow_read_guard_present() -> None:
    """A SLOW ReadError/ReadTimeout must be refused, not retried.

    Retrying a read failure that surfaced after A2A_RETRY_FAST_ONLY_MS
    re-runs a turn the backend most likely already completed — double-
    billing and, for agentic backends, duplicating side effects
    (commits/pushes). The guard raises ``_SlowReadGuardRefusal`` from the
    dedicated ``(ReadError, ReadTimeout)`` branch instead of falling
    through to the retry/backoff path. Pins the guard so a future edit
    that drops it (regressing to the old always-retry premise) fails here.
    """
    src = A2A_PATH.read_text()
    assert "class _SlowReadGuardRefusal" in src, "slow-read guard exception class must exist"
    assert "except (httpx.ReadError, httpx.ReadTimeout)" in src, (
        "ReadError/ReadTimeout must have a dedicated except branch (separate from the "
        "always-retry send-side errors) so the slow-read guard can gate them."
    )
    assert "raise _SlowReadGuardRefusal" in src, "the slow-read branch must raise the refusal to skip retry"
    assert "_elapsed_ms > _A2A_RETRY_FAST_ONLY_MS" in src and '_A2A_RETRY_POLICY != "always"' in src, (
        "the slow-read guard must gate on elapsed > A2A_RETRY_FAST_ONLY_MS AND policy != always, "
        "mirroring the #1457 slow-5xx guard."
    )


def test_send_side_errors_retry_unconditionally() -> None:
    """WriteTimeout / PoolTimeout (request not fully sent) always retry.

    A failure before/while sending means the backend did no work, so it is
    always safe to retry — these must live in their own branch and NOT be
    gated by the slow-read guard.
    """
    src = A2A_PATH.read_text()
    assert re.search(r"except \(httpx\.WriteTimeout, httpx\.PoolTimeout\)", src), (
        "WriteTimeout/PoolTimeout must be handled in their own always-retry branch, separate "
        "from the slow-read-guarded (ReadError, ReadTimeout) branch."
    )
