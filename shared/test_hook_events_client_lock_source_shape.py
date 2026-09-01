"""Source-shape regression coverage for ``shared/hook_events.py``'s ``_client_lock`` fan-out.

References: #1581 (loop-binding fix), sibling-pattern parity with
``backends/claude/test_sessions_lock_source_shape.py`` /
``backends/openai/test_sessions_lock_source_shape.py``.

The shared ``_client_lock`` contract in ``shared/hook_events.py``
enforces the #1581 "lazy-init under the running loop" guarantee for
the module-level ``httpx.AsyncClient`` used to POST hook.decision
events to the harness. Historically, instantiating ``asyncio.Lock()``
at import bound the lock to whichever loop existed (or none), so
backends using fresh loops or hot-restart paths hit "different loop"
errors the first time they awaited ``_get_client()``.

The fan-out is narrower than the claude/openai ``_sessions_lock``
patterns (a single caller of ``_get_client()`` + a single ``async
with _client_lock:`` acquire, both inside ``_get_client()`` itself),
but the same source-shape invariants apply:

1. ``shared/hook_events.py`` declares
   ``_client_lock: asyncio.Lock | None = None`` at module scope. A
   refactor that swaps this back to an eager ``asyncio.Lock()`` at
   import time re-introduces the #1581 regression.
2. ``_get_client()`` owns the SOLE ``None`` → ``asyncio.Lock()``
   transition via ``if _client_lock is None: _client_lock =
   asyncio.Lock()``, relying on CPython's GIL for atomicity (no I/O,
   no awaits between the gate and the assignment). The ``global``
   declaration inside ``_get_client()`` is what makes the reassignment
   observable to future callers.
3. The ``async with _client_lock:`` acquire lives inside
   ``_get_client()`` immediately after the gated init — no external
   caller acquires the lock directly. That co-location keeps the
   "lock acquired after the lazy init completes" ordering trivially
   correct.
4. The #1581 rationale is pinned in the comment block preceding the
   module-level declaration so a future tidy-up that strips it forces
   the contributor to see the concrete failure mode ("different loop"
   errors on hot-restart / fresh-loop paths) and (hopefully) preserve
   the lazy-init pattern rather than "simplifying" to an eager
   module-level ``Lock``.
5. ``_get_client()`` has exactly ONE call site in the module (inside
   ``_post_once``, on the transport request path). Adding a second
   caller is not necessarily wrong — but it changes the fan-out shape
   this file pins, and a reviewer should think about whether the new
   caller preserves the same lazy-init semantics and doesn't
   short-circuit the shared client. Bumping the count here forces the
   conversation.

This file follows the pytest-function-style text-substring assertion
convention established by
``backends/claude/test_sessions_lock_source_shape.py`` (importing
``shared/hook_events.py`` directly would pull in httpx + prometheus +
the module's threading-Lock state at import; text-substring assertions
avoid the import cost and stay valid under lint-only / static-check
test runs).
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent


def _source() -> str:
    return (HERE / "hook_events.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Module-level sentinel + rationale comment
# ---------------------------------------------------------------------------


def test_module_level_client_lock_declared_as_none():
    # Module-level declaration MUST be the ``None`` sentinel. A literal
    # ``asyncio.Lock()`` here binds the lock to whichever loop existed
    # at import time — the #1581 regression that surfaces as
    # "different loop" errors on hot-restart / fresh-loop test paths.
    src = _source()
    assert "_client_lock: asyncio.Lock | None = None" in src


def test_client_lock_comment_references_1581_rationale():
    # The comment block preceding the module-level declaration names
    # #1581, the lazy-init pattern, and the "different loop" failure
    # mode. Pinning the rationale means a future tidy-up that strips
    # it also fails this test — the contributor then sees the concrete
    # failure mode and (hopefully) preserves the lazy-init pattern
    # rather than "simplifying" to an eager module-level Lock.
    src = _source()
    assert "#1581" in src
    assert "lazy-init the lock under the running loop" in src
    assert "different loop" in src


# ---------------------------------------------------------------------------
# Sole-constructor gate inside _get_client
# ---------------------------------------------------------------------------


def test_get_client_declares_globals_and_gated_init():
    # ``_get_client()`` is the SOLE constructor. Pin the signature, the
    # ``global`` declaration (needed to reassign the module-level
    # names), the gate, and the assignment. CPython's GIL makes the
    # is-None + assignment effectively atomic for the ``asyncio.Lock``
    # constructor (no I/O, no awaits between them).
    src = _source()
    assert "async def _get_client() -> httpx.AsyncClient:" in src
    assert "global _client, _client_lock" in src
    assert "if _client_lock is None:" in src
    assert "_client_lock = asyncio.Lock()" in src


def test_no_module_level_unconditional_client_lock_constructor():
    # Defence-in-depth: the ONLY assignment to
    # ``_client_lock = asyncio.Lock()`` must live inside
    # ``_get_client``'s function body (after the ``if _client_lock is
    # None:`` gate). An unconditional module-level line — one that
    # starts at column 0 with no leading whitespace — would defeat
    # the sole-constructor invariant and re-introduce the #1581
    # loop-binding regression at import time.
    src = _source()
    lines = src.splitlines()
    bad = [ln for ln in lines if ln.startswith("_client_lock = asyncio.Lock()")]
    assert not bad, (
        "Found module-level unconditional _client_lock = asyncio.Lock() — "
        "must remain gated inside _get_client() (#1581)."
    )


# ---------------------------------------------------------------------------
# Use site: the acquire lives inside _get_client itself
# ---------------------------------------------------------------------------


def test_get_client_acquires_client_lock_inline():
    # The ``async with _client_lock:`` wrap MUST live inside
    # ``_get_client()`` — that co-location keeps the "lock acquired
    # after the lazy init completes" ordering trivially correct.
    # Pinning the exact indented line documents both the wrap and its
    # position (function body, one level of indentation).
    src = _source()
    assert "    async with _client_lock:" in src


def test_exactly_one_async_with_client_lock_acquisition():
    # Every acquire of ``_client_lock`` MUST route through
    # ``_get_client()`` — no external caller acquires the lock
    # directly. Raw-name acquires elsewhere risk hitting the ``None``
    # sentinel under any code path that runs before the getter is
    # first invoked, and they silently defeat the sole-constructor
    # contract. Pin the count at exactly one so a second acquire
    # anywhere in the module fails this test and forces reviewer
    # attention.
    src = _source()
    lines = src.splitlines()
    hits = [ln for ln in lines if "async with _client_lock" in ln]
    assert len(hits) == 1, (
        f"Expected exactly one `async with _client_lock:` acquire "
        f"(inside _get_client); found {len(hits)}. External acquires "
        f"risk hitting the None sentinel under test isolation and "
        f"silently defeat the sole-constructor contract. Offending "
        f"lines: {hits}"
    )


def test_exactly_one_get_client_call_site():
    # ``_get_client()`` has ONE caller (inside ``_post_once``, at the
    # top of the transport request path). Adding a second caller is
    # not necessarily wrong, but it changes the fan-out shape this
    # file pins — bumping the count forces a reviewer to think about
    # whether the new caller respects the same lazy-init semantics
    # and doesn't short-circuit the shared client instance.
    src = _source()
    lines = src.splitlines()
    # Count call-shaped occurrences of ``_get_client()`` in the
    # module. Exclude the definition line (``async def _get_client()
    # -> ...``); the docstring/comment references (line 185 in the
    # current source) don't have the trailing parens and so are
    # already excluded by the substring match on ``_get_client()``.
    call_sites = [ln for ln in lines if "_get_client()" in ln and "async def _get_client()" not in ln]
    assert len(call_sites) == 1, (
        f"Expected exactly one `_get_client()` call site (inside "
        f"_post_once); found {len(call_sites)}. If you intentionally "
        f"added a new caller, update this test and verify the new "
        f"caller preserves the single-shared-client + lazy-init "
        f"semantics. Offending lines: {call_sites}"
    )
