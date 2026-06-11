"""Source-shape regression coverage for openai's ``_computer_lock`` fan-out.

References: #378, #402, #522.

The shared ``_computer_lock`` contract is fan-out across three cooperating
sites in two files. Together they implement the #402 "single Lock
instance, eagerly initialised inside the serving loop" guarantee that
replaced the pre-#402 lazy check-and-assign inside ``_build_tools()``
which risked two ``asyncio.Lock()`` instances under concurrent first
computer-tool callers and the pre-#378 "module-level Lock binds to a
non-serving loop" regression:

1. ``backends/openai/executor.py`` declares the module-level
   ``_computer_lock: asyncio.Lock | None = None`` sentinel at import
   time. Module-level ``asyncio.Lock()`` is forbidden — it raises
   DeprecationWarning under Python 3.10+ and binds to a non-serving loop
   under 3.12+ (#378). The comment block immediately above the
   declaration documents this constraint explicitly so a future
   contributor cannot accidentally re-introduce the regression by
   "tidying up" the sentinel into an eager constructor.
2. ``_build_tools()`` uses the raw module-level ``_computer_lock`` name
   directly in ``async with _computer_lock:`` — unlike ``_sessions_lock``
   there is NO ``_get_computer_lock()`` getter. The lock guards the
   double-checked lazy init of the ``_browser_pool`` BrowserPool
   singleton (#522) so two concurrent first-touch callers cannot each
   instantiate their own BrowserPool. Because the use site is raw-name,
   the eager main.py init at step 3 is REQUIRED — without it the first
   computer-tool call would crash with
   ``TypeError: object NoneType can't be used in 'async with'``.
3. ``backends/openai/main.py`` eagerly assigns
   ``_executor_module._computer_lock = asyncio.Lock()`` inside
   ``asyncio.run()`` so the lock binds to the serving event loop and the
   raw-name use site at step 2 has a Lock to acquire from the first
   request onward. The main.py docstring step 1 declares this
   responsibility explicitly ("Initialise ``executor._computer_lock``
   and ``executor._sessions_lock`` inside the running loop ... #378 /
   #402 / #725 ... eliminates the check-and-assign race in
   ``_build_tools()``").

This file pins each fan-out point so a future refactor that:

- drops the eager main.py assignment (first computer-tool call raises
  ``TypeError: object NoneType can't be used in 'async with'``; the
  bug only surfaces in production once a request lands on a pod that
  has computer-tool enabled — unit tests that bypass main() would not
  notice),
- swaps the module-level sentinel for an eager ``asyncio.Lock()``
  constructor (re-introduces the #378 wrong-loop attachment
  regression),
- adds a second ``asyncio.Lock()`` constructor at the ``_build_tools``
  use site (re-introduces the #402 pre-fix lazy check-and-assign race
  where two concurrent first-touchers could each construct a separate
  Lock instance, defeating the BrowserPool single-instance guarantee),

...fails this file at unit time rather than silently re-enabling the
pre-#378 or pre-#402 regressions.

The openai backend's test convention (see ``test_mcp_config_path_prefix.py``
header: *"re-evaluate the equivalent guard in isolation rather than
importing the full executor module — its SDK chain is too heavy"*)
precludes importing ``executor.py`` directly. The local pattern is
source-shape pinning by regex/substring assertion against ``executor.py``
text (mirroring ``test_agent_md_revision.py``,
``test_mcp_config_path_prefix.py``, ``test_mcp_lifespan_source_shape.py``,
and ``test_sessions_lock_source_shape.py``).

This file is the openai-local sibling of ``test_sessions_lock_source_shape.py``
— covering the lock that protects ``_browser_pool`` rather than the
shared ``sessions`` OrderedDict. The two fan-outs are documented as
parallel pairs in the executor.py and main.py comments (the
``_sessions_lock`` docstring explicitly references "parity with
``_computer_lock``"); this file anchors the parallel from the
``_computer_lock`` side.
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


class ComputerLockDeclarationTests(unittest.TestCase):
    """Module-level ``_computer_lock`` sentinel + #378 forbidden-construction guard."""

    def test_module_level_computer_lock_declared_as_none(self):
        src = _executor_source()
        # Module-level declaration MUST be the ``None`` sentinel. A literal
        # ``asyncio.Lock()`` here would re-introduce the #378 wrong-loop
        # attachment regression (DeprecationWarning under Python 3.10+,
        # wrong-loop binding under 3.12+).
        self.assertIn(
            "_computer_lock: asyncio.Lock | None = None",
            src,
        )

    def test_no_module_level_asyncio_lock_constructor_on_computer_lock(self):
        src = _executor_source()
        # Defence-in-depth: forbid any module-level
        # ``_computer_lock = asyncio.Lock()`` assignment. The ONLY
        # assignment to ``_computer_lock = asyncio.Lock()`` lives in
        # ``main.py`` at runtime via attribute assignment on the
        # executor module under ``asyncio.run`` — never at executor.py
        # import time.
        bad = re.search(
            r"^_computer_lock\s*=\s*asyncio\.Lock\(\)",
            src,
            re.MULTILINE,
        )
        self.assertIsNone(
            bad,
            "Unexpected module-level _computer_lock = asyncio.Lock() "
            "assignment in executor.py — must be set from main() under "
            "asyncio.run() instead (#378, #402).",
        )

    def test_declaration_has_378_forbidden_construction_comment(self):
        src = _executor_source()
        # The comment block immediately above the declaration documents
        # WHY the sentinel must stay ``None`` at import time. Pinning the
        # comment makes a future tidy-up that drops the comment also fail
        # this test — the contributor then sees the #378 reference, learns
        # the constraint, and (hopefully) does not "fix" the sentinel.
        self.assertRegex(
            src,
            r"# _computer_lock is initialized in main\(\) inside asyncio\.run\(\) so that it is"
            r"(?:.|\n)*?#378"
            r"(?:.|\n)*?_computer_lock: asyncio\.Lock \| None = None",
        )


class ComputerLockUsageSiteTests(unittest.TestCase):
    """``_build_tools`` is the sole use site; it guards ``_browser_pool`` lazy init."""

    def test_build_tools_acquires_computer_lock_before_browser_pool_init(self):
        src = _executor_source()
        # The #402 contract: ``_build_tools`` must acquire ``_computer_lock``
        # BEFORE the ``if _browser_pool is None: _browser_pool = BrowserPool()``
        # double-checked lazy init. Without the lock, two concurrent
        # first-touch callers could each observe ``_browser_pool is None``
        # and each construct a separate BrowserPool instance — defeating
        # the per-process singleton guarantee that backs the per-session
        # PlaywrightComputer isolation (#522).
        self.assertRegex(
            src,
            r"async def _build_tools\([^)]*\)[^:]*:"
            r"(?:.|\n)*?async with _computer_lock:"
            r"(?:.|\n)*?if _browser_pool is None:"
            r"(?:.|\n)*?_browser_pool\s*=\s*BrowserPool\(\)",
        )

    def test_only_one_async_with_computer_lock_in_executor(self):
        src = _executor_source()
        # The lock is single-use-site. Adding a second ``async with
        # _computer_lock:`` elsewhere is not necessarily wrong but it
        # changes the fan-out shape this file pins — bumping this
        # expected count forces a reviewer to think about whether the
        # new site needs the same eager-init-or-crash semantics and
        # whether it should be added to this fan-out spec.
        matches = re.findall(r"async with _computer_lock\b", src)
        self.assertEqual(
            len(matches),
            1,
            f"Expected exactly one `async with _computer_lock` use site "
            f"(in _build_tools); found {len(matches)}. If you intentionally "
            f"added a new use site, update this test and consider whether "
            f"the new site has the same eager-init-required semantics.",
        )

    def test_no_get_computer_lock_helper_introduced(self):
        src = _executor_source()
        # Unlike ``_sessions_lock`` which has a ``_get_sessions_lock()``
        # sole-constructor getter, ``_computer_lock`` deliberately does
        # not — the eager main.py init makes a getter redundant, and
        # adding one would muddle the contract (is the getter the sole
        # constructor, or is main.py still authoritative?). This pin
        # catches a future refactor that adds a getter without removing
        # the main.py eager assignment.
        self.assertNotRegex(
            src,
            r"def _get_computer_lock\(\)",
            "_get_computer_lock() helper introduced — the _computer_lock "
            "contract is direct module-attr access with mandatory eager "
            "main.py init; a getter would create two sources of truth.",
        )


class ComputerLockBootstrapTests(unittest.TestCase):
    """main.py eagerly initialises ``_computer_lock`` under ``asyncio.run``."""

    def test_main_eagerly_assigns_computer_lock_asyncio_lock(self):
        src = _main_source()
        # The #378/#402 eager init: ``main.py`` MUST set
        # ``_executor_module._computer_lock = asyncio.Lock()`` inside
        # ``asyncio.run`` so the Lock binds to the serving loop and the
        # raw-name use site in ``_build_tools`` has something to acquire.
        # Dropping this line would crash the first computer-tool request
        # with ``TypeError: object NoneType can't be used in 'async with'``.
        self.assertIn(
            "_executor_module._computer_lock = asyncio.Lock()",
            src,
        )

    def test_main_eager_init_documented_in_docstring(self):
        src = _main_source()
        # The bootstrap docstring step 1 enumerates this responsibility
        # explicitly — pinning the docstring text means a future
        # refactor that drops the line ALSO has to drop the docstring,
        # which makes the omission visible in code review.
        self.assertRegex(
            src,
            r"Initialise ``executor\._computer_lock`` and ``executor\._sessions_lock``"
            r"(?:.|\n)*?inside the running loop",
        )

    def test_main_eager_init_precedes_sessions_lock_seed(self):
        src = _main_source()
        # Ordering: ``_computer_lock`` is seeded first, then
        # ``_sessions_lock`` — both within the same ``asyncio.run`` body.
        # Reversing the order is OK in principle but the comments
        # document this sequence, and a refactor that reordered them
        # likely also dropped one. Pin the order as a canary on the
        # bootstrap sequence as a whole — mirrors the parallel pin in
        # ``test_sessions_lock_source_shape.py``.
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
            "_computer_lock eager init must precede _get_sessions_lock() "
            "eager seed per the documented bootstrap order (#378/#402 "
            "before #725).",
        )

    def test_main_eager_init_has_378_402_comment_reference(self):
        src = _main_source()
        # The inline comment immediately above the eager assignment
        # references #378 (module-level Lock wrong-loop attachment) and
        # #402 (eliminates race in lazy check-and-assign inside
        # _build_tools). Pinning the comment makes a future tidy-up that
        # drops the comment also fail this test — the contributor then
        # sees the regression refs and (hopefully) does not also drop
        # the assignment itself.
        self.assertRegex(
            src,
            r"# Initialize _computer_lock here, inside asyncio\.run\(\), so it is always"
            r"(?:.|\n)*?#378"
            r"(?:.|\n)*?_executor_module\._computer_lock = asyncio\.Lock\(\)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
