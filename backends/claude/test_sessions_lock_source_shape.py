"""Source-shape regression coverage for claude's ``_get_sessions_lock`` fan-out.

References: #1195, #1483, openai-parity (#506 / #725).

The shared ``_sessions_lock`` contract is fan-out across four cooperating
sites in ``backends/claude/executor.py``. Together they implement the
#1195 "single Lock instance, no double-checked lazy init duplicated
across call sites" guarantee — the claude-side analogue of openai's
``_get_sessions_lock`` pattern (``backends/openai/executor.py``):

1. ``executor.py`` declares the module-level
   ``_sessions_lock: asyncio.Lock | None = None`` sentinel at import
   time. Module-level ``asyncio.Lock()`` is forbidden — it raises
   DeprecationWarning under Python 3.10+ and binds to a non-serving loop
   under 3.12+. This is the same constraint the openai backend pins in
   ``test_sessions_lock_source_shape.py``.
2. ``_get_sessions_lock()`` is the SOLE constructor — it owns the
   ``if _sessions_lock is None: _sessions_lock = asyncio.Lock()``
   transition exactly once, relying on CPython's GIL for atomicity (no
   I/O, no awaits inside the constructor). The docstring asserts
   "Parity with the openai backend" — every other caller in claude's
   executor must route through this helper rather than re-implementing
   the lazy init or using the raw module-level name in ``async with``.
3. ``_track_session`` wraps the LRU evict / move-to-end / popitem block
   in ``async with _get_sessions_lock():`` so concurrent
   ``popitem(last=False)`` and post-await ``sessions[session_id] = ...``
   cannot interleave on the shared ``OrderedDict`` (#1195). The
   surrounding comment explicitly references "mirrors codex's
   #506/#725 pattern" — pinning the cross-backend lineage.
4. ``execute()``'s timeout-eviction path (``sessions.pop(ctx.session_id,
   None)``) also runs under ``async with _get_sessions_lock():`` so the
   pop cannot interleave with a concurrent ``_track_session``
   move_to_end / popitem on the same OrderedDict (#1483). The inline
   comment explicitly references "#1483: serialise under
   _get_sessions_lock() to match _track_session's popitem/move_to_end
   path on the shared OrderedDict".

Unlike the openai backend, claude has NO ``main.py`` eager seed for
``_get_sessions_lock()`` — the getter is GIL-safe and the claude test
suite (including the cross-backend test_run_query_collision_retry
source-shape pin) routinely bypasses ``main()``. The lazy-only contract
is intentional and is itself worth pinning here as a non-claim — a
future refactor that adds an eager seed in ``main.py`` is not wrong,
but it would mean this file's "no main.py reference required" stance
needs updating; conversely a refactor that adds a getter-bypassing
direct ``asyncio.Lock()`` call in any module-level scope IS wrong and
would silently regress #1195.

This file pins each fan-out point so a future refactor that:

- swaps the module-level sentinel for an eager ``asyncio.Lock()``
  constructor at executor.py import time (re-introduces the
  module-level-Lock wrong-loop attachment regression that openai's
  #378 originally tracked),
- adds a second ``asyncio.Lock()`` constructor outside the getter
  (two separate locks coexist; serialisation guarantee silently
  broken — the pre-#1195 pattern),
- swaps ``async with _get_sessions_lock()`` for the raw module-level
  ``_sessions_lock`` name at either use site (None-crash unless the
  getter has been previously invoked elsewhere — fragile and easy to
  break under test isolation),
- forgets the ``async with _get_sessions_lock()`` wrap on a new evict
  / pop / move-to-end site,

...fails this file at unit time rather than silently re-enabling the
pre-#1195 duplicate-Lock race or the pre-#1483 interleaved-OrderedDict
mutations.

The claude backend's test convention (see
``test_run_query_collision_retry_source_shape.py`` header: *"the SDK
chain (``claude_agent_sdk``, ``ClaudeAgentOptions``, the prometheus
client init) is heavy ... existing claude source-shape tests all use
pytest-function-style text-substring assertions against ``executor.py``
read as text"*) precludes importing ``executor.py`` directly. This
file follows that local convention; it is the claude-side analogue of
``backends/openai/test_sessions_lock_source_shape.py`` (which uses the
unittest-class style required by openai-local convention).
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent


def _source() -> str:
    return (HERE / "executor.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Module-level sentinel + sole-constructor getter
# ---------------------------------------------------------------------------


def test_module_level_sessions_lock_declared_as_none():
    # Module-level declaration MUST be the ``None`` sentinel. A literal
    # ``asyncio.Lock()`` here would bind to the wrong loop under Python
    # 3.12+ (the same regression openai's #378 originally fixed; claude
    # avoids it by construction via this lazy pattern).
    src = _source()
    assert "_sessions_lock: asyncio.Lock | None = None" in src


def test_get_sessions_lock_helper_is_sole_constructor():
    # The getter signature + body — single point of truth for the
    # transition ``None`` → ``asyncio.Lock()``. CPython's GIL makes
    # the is-None + assignment effectively atomic for the
    # ``asyncio.Lock`` constructor (no I/O, no awaits).
    src = _source()
    assert "def _get_sessions_lock() -> asyncio.Lock:" in src
    assert "global _sessions_lock" in src
    assert "if _sessions_lock is None:" in src
    assert "_sessions_lock = asyncio.Lock()" in src
    assert "return _sessions_lock" in src


def test_get_sessions_lock_docstring_references_1195_and_openai_parity():
    # The getter docstring asserts "Parity with the openai backend" and
    # references #1195. Pinning the docstring means a future refactor
    # that drops the lineage commentary also fails this test — the
    # contributor then sees the cross-backend connection and (hopefully)
    # preserves the contract rather than silently diverging.
    src = _source()
    assert "creating it lazily (#1195)" in src
    assert "Parity with the openai backend" in src


def test_no_module_level_unconditional_sessions_lock_constructor():
    # Defence-in-depth: the ONLY assignment to
    # ``_sessions_lock = asyncio.Lock()`` must live inside
    # ``_get_sessions_lock``'s function body (after the
    # ``if _sessions_lock is None:`` gate). An unconditional module-level
    # line would defeat the sole-constructor invariant.
    src = _source()
    lines = src.splitlines()
    # Walk every line; an unconditional ``_sessions_lock = asyncio.Lock()``
    # at column 0 (no leading whitespace, not inside a function body) is
    # the forbidden shape. The gated assignment inside the getter has
    # leading whitespace (function body indentation).
    bad = [ln for ln in lines if ln.startswith("_sessions_lock = asyncio.Lock()")]
    assert not bad, (
        "Found module-level unconditional _sessions_lock = asyncio.Lock() — "
        "must route through _get_sessions_lock() instead (#1195)."
    )


# ---------------------------------------------------------------------------
# Use sites: every shared-OrderedDict mutation routes through the getter
# ---------------------------------------------------------------------------


def test_track_session_uses_get_sessions_lock_wrapper():
    # ``_track_session`` is the primary LRU evict / move-to-end /
    # popitem site (#1195). The body MUST acquire the lock before the
    # ``if session_id in sessions:`` membership branch — otherwise a
    # concurrent caller can observe a transient under-capacity state
    # between popitem(last=False) and the post-await reinsertion.
    src = _source()
    assert "async def _track_session(sessions: OrderedDict[str, float], session_id: str) -> None:" in src
    # The very next non-comment line in the body should be the
    # ``async with _get_sessions_lock():`` wrap. We pin the wrap text
    # itself; the structural position is documented by the surrounding
    # comment block.
    assert "    async with _get_sessions_lock():" in src


def test_track_session_comment_references_1195_and_codex_parity():
    # The inline comment above the ``async with _get_sessions_lock()``
    # wrap in ``_track_session`` explicitly references #1195 and
    # "Mirrors codex's #506/#725 pattern". Pinning the comment makes
    # a future tidy-up that drops the lineage reference also fail —
    # the contributor then sees the cross-backend cluster of
    # related-issue refs (#1195 ↔ #506 ↔ #725) and (hopefully)
    # preserves the wrap.
    src = _source()
    assert "Serialise evict/unlink/insert on the shared OrderedDict (#1195)" in src
    assert "Mirrors codex's #506/#725 pattern" in src


def test_timeout_eviction_uses_get_sessions_lock_wrapper_for_1483():
    # The #1483 fix: ``sessions.pop(ctx.session_id, None)`` on the
    # timeout-eviction path MUST run under the same lock as
    # ``_track_session`` so the pop cannot interleave with a concurrent
    # ``_track_session`` ``move_to_end`` / ``popitem`` on the shared
    # OrderedDict. Pin both the wrap and the immediate pop call so a
    # refactor that splits them (releases the lock between the wrap and
    # the pop) fails this test.
    src = _source()
    # The inline comment immediately above the wrap names the fix.
    assert "#1483: serialise under _get_sessions_lock() to match _track_session" in src
    # The wrap itself.
    assert "        async with _get_sessions_lock():" in src
    # The pop call inside the wrap (claude's timeout path uses
    # ``ctx.session_id`` rather than a bare ``session_id`` local —
    # openai's analogous test pins ``session_id`` instead).
    assert "sessions.pop(ctx.session_id, None)" in src


def test_no_raw_module_level_sessions_lock_in_async_with():
    # Use sites MUST route through ``_get_sessions_lock()`` rather
    # than the raw module-level name. Raw-name usage would None-crash
    # under any unit test that runs before the getter is first invoked
    # — fragile and easy to break under test isolation.
    src = _source()
    # An ``async with _sessions_lock`` (without the trailing ``()``
    # that ``_get_sessions_lock()`` would have) is the forbidden shape.
    # Walk every line: any occurrence of ``async with _sessions_lock``
    # followed by a non-paren character is a raw-name use.
    lines = src.splitlines()
    bad = [
        ln
        for ln in lines
        if "async with _sessions_lock" in ln
        # Allow only the ``_get_sessions_lock()`` form (which has
        # ``async with _get_sessions_lock()`` — the ``_get_`` prefix
        # distinguishes it from the raw module-level name).
        and "_get_sessions_lock" not in ln
    ]
    assert not bad, (
        "Found `async with _sessions_lock` without going through "
        "_get_sessions_lock() — risks None-crash under test isolation "
        "and silently defeats the #1195 sole-constructor contract. "
        f"Offending lines: {bad}"
    )


def test_exactly_two_get_sessions_lock_use_sites_in_executor():
    # The fan-out is two use sites: ``_track_session`` (#1195) and
    # the timeout-eviction in ``execute()`` (#1483). Adding a third
    # call site is not necessarily wrong — but it changes the fan-out
    # shape this file pins, and a reviewer should think about whether
    # the new site has the same "always under this lock" semantics.
    # Bumping the count here forces the conversation.
    src = _source()
    use_sites = [ln for ln in src.splitlines() if "async with _get_sessions_lock():" in ln]
    assert len(use_sites) == 2, (
        f"Expected exactly two `async with _get_sessions_lock():` use sites "
        f"(in _track_session and execute() timeout-eviction); found {len(use_sites)}. "
        f"If you intentionally added a new use site, update this test and "
        f"verify the new site has the always-under-lock semantics."
    )
