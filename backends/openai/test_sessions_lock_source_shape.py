"""Source-shape regression coverage for openai's ``_get_sessions_lock`` fan-out.

References: #506, #668, #725, #378, #402, #1494, #1499.

The shared ``_sessions_lock`` contract is fan-out across six cooperating
sites in two files. Together they implement the #725 "single Lock
instance, no double-checked lazy init duplicated across call sites"
guarantee that replaced the pre-#725 pattern of inline
``if _sessions_lock is None: _sessions_lock = asyncio.Lock()`` at every
caller (which risked two ``asyncio.Lock()`` instances under concurrent
first-touch, and made it easy for a future caller to omit the
double-check entirely):

1. ``backends/openai/executor.py`` declares the module-level
   ``_sessions_lock: asyncio.Lock | None = None`` sentinel at import
   time. Module-level ``asyncio.Lock()`` is forbidden — it raises
   DeprecationWarning under Python 3.10+ and binds to a non-serving loop
   under 3.12+ (#378).
2. ``_get_sessions_lock()`` is the SOLE constructor — it owns the
   ``if _sessions_lock is None: _sessions_lock = asyncio.Lock()``
   transition exactly once, relying on CPython's GIL for atomicity (no
   I/O, no awaits inside the constructor). The docstring asserts
   "_get_sessions_lock() is now the sole constructor" — every other
   caller must route through this helper rather than re-implementing the
   lazy init.
3. ``_track_session`` wraps the LRU evict/insert block in
   ``async with _get_sessions_lock():`` so concurrent ``popitem(last=False)``
   and post-await ``sessions[session_id] = ...`` cannot interleave
   (#506).
4. ``execute()`` reads the membership snapshot under
   ``async with _get_sessions_lock():`` so the
   ``session_id in sessions`` check cannot race ``_track_session``'s
   ``move_to_end`` and mis-label ``backend_session_starts_total`` (#1499).
5. ``execute()`` performs the timeout-eviction
   (``sessions.pop(session_id, None)``) under
   ``async with _get_sessions_lock():`` so the pop + _release_computer +
   metric updates cannot interleave with ``_track_session``'s mutations
   (#668).
6. ``backends/openai/main.py`` eagerly invokes
   ``_executor_module._get_sessions_lock()`` inside ``asyncio.run`` at
   startup — mirroring the eager ``_computer_lock = asyncio.Lock()``
   init two lines above (#402 / #725) — so the single helper is
   guaranteed to return the same instance from the first request onward.
   Without the eager seed, the first concurrent pair of callers could
   each observe ``_sessions_lock is None`` simultaneously and end up
   with separate Lock instances.
7. ``main.py``'s post-execute session-stream-registry pop also routes
   through ``async with executor._get_sessions_lock():`` (#1494) so it
   cannot interleave with ``_track_session``'s popitem/move_to_end and
   skew the #506/#725 session gauges.

This file pins each fan-out point so a future refactor that:
- drops the eager main.py seed (race window returns at first concurrent
  request),
- adds a second ``asyncio.Lock()`` constructor outside the getter (two
  separate locks coexist; serialisation guarantee silently broken),
- swaps ``async with _get_sessions_lock()`` for the raw module-level
  ``_sessions_lock`` name at any use site (None-crash if the eager seed
  path is skipped, e.g. under unit tests that bypass main()),
- forgets the ``async with`` wrap on a new evict/insert site,

...fails this file at unit time rather than silently re-enabling the
pre-#725 duplicate-Lock race or the pre-#506 interleaved-OrderedDict
mutations.

The openai backend's test convention (see ``test_mcp_config_path_prefix.py``
header: *"re-evaluate the equivalent guard in isolation rather than
importing the full executor module — its SDK chain is too heavy"*)
precludes importing ``executor.py`` directly. The local pattern is
source-shape pinning by regex/substring assertion against ``executor.py``
text (mirroring ``test_agent_md_revision.py``,
``test_mcp_config_path_prefix.py``, and ``test_mcp_lifespan_source_shape.py``).

This file is the openai-local sibling of the lock-contract pin pattern;
the parallel claude pin lives at
``backends/claude/test_run_query_collision_retry_source_shape.py``
covering claude's run_query/_tool_use_flag fan-out.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXECUTOR_PATH = _HERE / "executor.py"
_MAIN_PATH = _HERE / "main.py"


def _executor_source() -> str:
    return _EXECUTOR_PATH.read_text(encoding="utf-8")


def _main_source() -> str:
    return _MAIN_PATH.read_text(encoding="utf-8")


class SessionsLockDeclarationTests(unittest.TestCase):
    """Module-level ``_sessions_lock`` sentinel + sole-constructor helper."""

    def test_module_level_sessions_lock_declared_as_none(self):
        src = _executor_source()
        # Module-level declaration MUST be the ``None`` sentinel. A literal
        # ``asyncio.Lock()`` here would re-introduce the #378 wrong-loop
        # attachment regression (DeprecationWarning under Python 3.10+,
        # wrong-loop binding under 3.12+).
        self.assertIn(
            "_sessions_lock: asyncio.Lock | None = None",
            src,
        )

    def test_no_module_level_asyncio_lock_constructor_on_sessions_lock(self):
        src = _executor_source()
        # Defence-in-depth: forbid any module-level
        # ``_sessions_lock = asyncio.Lock()`` line. The ONLY assignment to
        # ``_sessions_lock = asyncio.Lock()`` must live inside
        # ``_get_sessions_lock``'s function body (after the
        # ``if _sessions_lock is None:`` gate).
        # Walk every line; allow only the gated assignment under the
        # getter, reject any unconditional module-level assignment.
        bad = re.search(
            r"^_sessions_lock\s*=\s*asyncio\.Lock\(\)",
            src,
            re.MULTILINE,
        )
        self.assertIsNone(
            bad,
            "Unexpected module-level _sessions_lock = asyncio.Lock() "
            "assignment — must route through _get_sessions_lock() (#378, #725).",
        )

    def test_get_sessions_lock_helper_is_sole_constructor(self):
        src = _executor_source()
        # The getter MUST contain the ``if _sessions_lock is None:`` →
        # ``_sessions_lock = asyncio.Lock()`` transition. CPython's GIL
        # makes the is-None + assignment effectively atomic for the
        # ``asyncio.Lock`` constructor (no I/O, no awaits).
        self.assertRegex(
            src,
            r"def _get_sessions_lock\(\)\s*->\s*asyncio\.Lock:"
            r"(?:.|\n)*?global _sessions_lock"
            r"(?:.|\n)*?if _sessions_lock is None:"
            r"(?:.|\n)*?_sessions_lock\s*=\s*asyncio\.Lock\(\)"
            r"(?:.|\n)*?return _sessions_lock",
        )


class SessionsLockUsageSitesTests(unittest.TestCase):
    """Every shared-OrderedDict mutation site routes through ``_get_sessions_lock()``."""

    def test_track_session_uses_get_sessions_lock_wrapper(self):
        src = _executor_source()
        # ``_track_session`` is the primary LRU evict/insert site (#506).
        # The body MUST acquire the lock before the
        # ``if session_id in sessions:`` membership branch — otherwise a
        # concurrent caller can observe a transient under-capacity state
        # between popitem(last=False) and the post-await reinsertion.
        self.assertRegex(
            src,
            r"async def _track_session\([^)]*\)\s*->\s*None:" r"(?:.|\n)*?async with _get_sessions_lock\(\):",
        )

    def test_execute_membership_snapshot_under_lock(self):
        src = _executor_source()
        # The #1499 fix: the membership check + last-used capture must
        # happen under the lock so a concurrent ``_track_session``
        # ``move_to_end`` cannot flip ``is_new`` between the check and
        # the SQLite probe, which would mis-label
        # ``backend_session_starts_total``.
        self.assertRegex(
            src,
            r"async with _get_sessions_lock\(\):"
            r"(?:.|\n)*?_in_memory\s*=\s*session_id\s+in\s+sessions"
            r"(?:.|\n)*?_last_used\s*=\s*sessions\.get\(session_id\)",
        )

    def test_execute_timeout_eviction_under_lock(self):
        src = _executor_source()
        # The #668 fix: the timeout-path ``sessions.pop(session_id, None)``
        # MUST run under the same lock as ``_track_session`` so the pop
        # cannot interleave with a concurrent insert/move_to_end. Pairs
        # with ``_release_computer`` and the SQLite delete inside the
        # same critical section so the eviction-metric write reflects
        # the actual OrderedDict state.
        self.assertRegex(
            src,
            r"async with _get_sessions_lock\(\):" r"(?:.|\n)*?sessions\.pop\(session_id,\s*None\)",
        )

    def test_no_raw_module_level_sessions_lock_in_async_with(self):
        src = _executor_source()
        # Use sites MUST route through ``_get_sessions_lock()`` rather
        # than the raw module-level name. Raw-name usage would None-crash
        # under any unit test that bypasses main()'s eager seed.
        bad = re.search(
            r"async with _sessions_lock\b(?!\s*\()",
            src,
        )
        self.assertIsNone(
            bad,
            "Found `async with _sessions_lock` without going through "
            "_get_sessions_lock() — risks None-crash when main()'s eager "
            "seed has not run (#725).",
        )


class SessionsLockBootstrapTests(unittest.TestCase):
    """main.py eagerly seeds ``_get_sessions_lock`` under ``asyncio.run``."""

    def test_main_eagerly_invokes_get_sessions_lock_under_asyncio_run(self):
        src = _main_source()
        # The #725 eager-seed: mirrors the line above that creates
        # ``_executor_module._computer_lock = asyncio.Lock()``.
        # Invoking the getter at startup (inside the serving loop)
        # guarantees subsequent callers see the same Lock instance and
        # eliminates the race where two concurrent first-touchers could
        # each observe ``_sessions_lock is None``.
        self.assertIn(
            "_executor_module._get_sessions_lock()",
            src,
        )

    def test_main_eager_seed_appears_after_computer_lock_seed(self):
        src = _main_source()
        # Ordering: ``_computer_lock`` is seeded first (mirroring its
        # historical bootstrap position), then ``_sessions_lock`` — both
        # within the same ``asyncio.run`` body BEFORE OTel init or
        # executor construction. Reversing the order is OK in principle
        # but the comments document this sequence, and a refactor that
        # reordered them likely also dropped one — so pin the order as a
        # canary on the bootstrap sequence as a whole.
        computer_idx = src.find("_executor_module._computer_lock = asyncio.Lock()")
        sessions_idx = src.find("_executor_module._get_sessions_lock()")
        self.assertGreater(
            computer_idx,
            -1,
            "main.py missing _computer_lock eager init (#378/#402).",
        )
        self.assertGreater(
            sessions_idx,
            -1,
            "main.py missing _get_sessions_lock() eager seed (#725).",
        )
        self.assertLess(
            computer_idx,
            sessions_idx,
            "_get_sessions_lock() eager seed must follow _computer_lock "
            "init per the documented bootstrap order (#725 mirrors #402).",
        )

    def test_main_post_execute_pop_routes_through_get_sessions_lock(self):
        src = _main_source()
        # The #1494 contract: the post-execute session-stream-registry
        # pop path also routes through ``executor._get_sessions_lock()``.
        # Without this, the cleanup pop on a non-continuation request
        # could interleave with ``_track_session``'s
        # popitem/move_to_end on the shared OrderedDict and skew the
        # #506/#725 session gauges.
        self.assertRegex(
            src,
            r"async with executor\._get_sessions_lock\(\):" r"(?:.|\n)*?executor\._sessions\.pop\(session_id,\s*None\)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
