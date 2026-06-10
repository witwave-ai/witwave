"""Source-shape regression coverage for openai's live_mcp_servers plumbing (#526, #667).

The MCP lifespan-scoped stack contract is fan-out across ``AgentExecutor``
init, ``_apply_mcp_config``, ``_snapshot_live_mcp_servers`` /
``_acquire_mcp_stack`` / ``_release_mcp_stack``, the ``execute()`` call
site, and ``run_query``'s ``Agent(mcp_servers=...)`` construction.
Together they implement the #526 "enter once, reuse across requests"
guarantee that keeps stateful MCP servers (kubeconfig / HTTP pool) alive
across A2A turns, and the #667 refcounted hot-reload that keeps
in-flight requests safe while a config swap parks the previous stack.

The openai backend's test convention (see
``test_mcp_config_path_prefix.py`` header: *"re-evaluate the equivalent
guard in isolation rather than importing the full executor module — its
SDK chain is too heavy"*) precludes mirroring gemini's full-stub-import
``test_mcp_lifespan.py`` pattern. The local pattern is source-shape
pinning by regex/substring assertion against ``executor.py`` text
(mirroring ``test_agent_md_revision.py`` and
``test_mcp_config_path_prefix.py``).

These tests pin each fan-out point so a future refactor that drops a
piece of the wiring — e.g. forgets to pass ``live_mcp_servers=`` into
``run_query``, or replaces ``mcp_servers=_live_mcp_servers`` with a
fresh ``[]``, or removes the lock around ``_live_mcp_servers`` reads —
fails this file at unit time rather than silently re-enabling per-request
stdio subprocess spawn (the pre-#526 behaviour) or worse, exposing
in-flight requests to a half-torn-down server during hot-reload.

PARITY.md row 35 lists openai as "Full" on MCP consumption; this file
anchors that claim with a source-shape regression check.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXECUTOR_PATH = _HERE / "executor.py"


def _source() -> str:
    return _EXECUTOR_PATH.read_text(encoding="utf-8")


class RunQuerySignatureTests(unittest.TestCase):
    """run_query accepts ``live_mcp_servers`` and snapshots it before use."""

    def test_run_query_signature_declares_live_mcp_servers_kwarg(self):
        src = _source()
        # Module-level ``async def run_query(...)`` carries the kwarg with a
        # ``list | None`` annotation and a ``None`` default. The default
        # matters: passing ``None`` from a synthetic /mcp call must not
        # require the caller to know about the live-server list.
        self.assertRegex(
            src,
            r"async def run_query\([^)]*live_mcp_servers:\s*list\s*\|\s*None\s*=\s*None",
        )

    def test_run_query_defensively_snapshots_live_mcp_servers(self):
        src = _source()
        # ``list(live_mcp_servers or [])`` rather than aliasing the caller's
        # list. Aliasing would let a concurrent reload mutate the caller's
        # snapshot mid-Agent-construction; the per-call copy isolates the
        # request from hot-reload churn.
        self.assertIn(
            "_live_mcp_servers: list = list(live_mcp_servers or [])",
            src,
        )

    def test_run_query_threads_snapshot_into_agent_construction(self):
        src = _source()
        # ``Agent(...)`` is constructed with ``mcp_servers=_live_mcp_servers``.
        # Without this, the SDK would receive an empty list even when the
        # MCP stack is live, and tool calls would silently fall through to
        # built-in tools only (the pre-#526 silent-degradation failure
        # mode).
        self.assertIn("mcp_servers=_live_mcp_servers,", src)


class AgentExecutorInitTests(unittest.TestCase):
    """AgentExecutor declares the lifespan-scoped MCP triad in __init__."""

    def test_init_declares_mcp_stack_field(self):
        src = _source()
        # The stack-or-None invariant: only ``_apply_mcp_config`` may set
        # this; everything else reads it through ``_acquire_mcp_stack`` or
        # the snapshot helper. Initial None means "no MCP servers entered
        # yet" — the readiness gate in perform_initial_loads waits for the
        # first apply call before declaring the pod ready.
        self.assertIn("self._mcp_stack: AsyncExitStack | None = None", src)

    def test_init_declares_live_mcp_servers_field(self):
        src = _source()
        # Empty-list initial value; populated by ``_apply_mcp_config`` only
        # under the lock. Reads from outside the lock (older patterns)
        # would race with hot-reload.
        self.assertIn("self._live_mcp_servers: list = []", src)

    def test_init_declares_mcp_servers_lock_field(self):
        src = _source()
        # The lock is the synchronisation primitive that serialises
        # reload-vs-request access (#526). Created lazily so __init__ does
        # not require a running event loop — actual creation happens in
        # ``_apply_mcp_config`` and the snapshot helpers on first use.
        self.assertIn(
            "self._mcp_servers_lock: asyncio.Lock | None = None",
            src,
        )

    def test_init_declares_mcp_stack_refcount_for_hot_reload(self):
        src = _source()
        # The #667 refcount keeps a parked old stack alive until the last
        # in-flight request releases it. Removing this field would re-open
        # the "hot-reload aclose racing with in-flight request" window.
        self.assertIn("self._mcp_stack_refcount: int = 0", src)


class ApplyMcpConfigTests(unittest.TestCase):
    """_apply_mcp_config is the only writer of _live_mcp_servers / _mcp_stack."""

    def test_apply_mcp_config_method_exists(self):
        src = _source()
        self.assertIn(
            "async def _apply_mcp_config(self, mcp_config: dict) -> None:",
            src,
        )

    def test_apply_mcp_config_acquires_lock_before_mutation(self):
        src = _source()
        # The mutation block must be under ``async with self._mcp_servers_lock``
        # so a concurrent ``_snapshot_live_mcp_servers`` / ``_acquire_mcp_stack``
        # call observes a coherent (stack, server-list) pair, never a
        # half-applied swap.
        self.assertIn("async with self._mcp_servers_lock:", src)

    def test_apply_mcp_config_clears_live_servers_during_teardown(self):
        src = _source()
        # Old-stack teardown path resets the visible server list to ``[]``
        # before the new stack is entered. Without this, a snapshot taken
        # between teardown and re-entry would still point at the
        # about-to-be-closed sessions.
        self.assertIn("self._live_mcp_servers = []", src)

    def test_apply_mcp_config_assigns_new_live_servers_after_entry(self):
        src = _source()
        # After ``new_stack.__aenter__()`` and per-server
        # ``enter_async_context`` succeed, the new live list is published.
        # The assignment happens to ``self._live_mcp_servers`` (not a
        # local) so concurrent snapshots see the new servers immediately
        # once the lock is released.
        self.assertIn("self._live_mcp_servers = new_live", src)


class SnapshotAndStackHelpersTests(unittest.TestCase):
    """The three snapshot/acquire/release helpers form the reader-side API."""

    def test_snapshot_returns_defensive_copy_under_lock(self):
        src = _source()
        # Pin both the signature and the body so a refactor that drops
        # the lock OR returns the live list by reference fails this test.
        self.assertIn(
            "async def _snapshot_live_mcp_servers(self) -> list:",
            src,
        )
        # The return-under-lock pattern: snapshot taken inside the
        # ``async with`` block so a reload cannot swap mid-copy.
        self.assertRegex(
            src,
            r"async with self\._mcp_servers_lock:\s*\n" r"\s*return list\(self\._live_mcp_servers\)",
        )

    def test_acquire_mcp_stack_returns_snapshot_and_stack_under_lock(self):
        src = _source()
        # The refcounted-acquire helper (#667). Signature must return the
        # snapshot list and the stack object as a tuple so the caller can
        # pair it with the matching release call. The refcount bump must
        # happen under the lock — otherwise a concurrent reload could
        # park a stack we are about to acquire.
        self.assertIn(
            'async def _acquire_mcp_stack(self) -> tuple[list, "AsyncExitStack | None"]:',
            src,
        )
        self.assertIn("self._mcp_stack_refcount += 1", src)
        self.assertIn(
            "return list(self._live_mcp_servers), stack",
            src,
        )

    def test_release_mcp_stack_helper_exists(self):
        src = _source()
        # Symmetry partner of _acquire_mcp_stack. Without this method the
        # refcount would only ever climb, parked old stacks would never
        # be aclosed, and subprocesses would leak across hot-reloads.
        self.assertIn(
            'async def _release_mcp_stack(self, stack: "AsyncExitStack | None") -> None:',
            src,
        )


class ExecuteCallSiteTests(unittest.TestCase):
    """The execute() / run path acquires-pass-release the snapshot pair."""

    def test_execute_call_site_acquires_then_passes_snapshot(self):
        src = _source()
        # The acquire returns ``(snapshot, stack_held)`` as a pair.
        # Pinning the variable names keeps the release-in-finally path
        # below readable — if the names change, the matching finally
        # block stops protecting the right stack.
        self.assertRegex(
            src,
            r"_mcp_servers_snapshot,\s*_mcp_stack_held\s*=\s*await self\._acquire_mcp_stack\(\)",
        )

    def test_execute_call_site_forwards_snapshot_into_run_query(self):
        src = _source()
        # The snapshot must reach run_query via the documented
        # ``live_mcp_servers=`` kwarg. Without this, the executor
        # acquires the stack (taking a refcount) but never tells the
        # SDK which servers it pinned — a silent degradation back to
        # zero MCP tools for the request.
        self.assertIn("live_mcp_servers=_mcp_servers_snapshot,", src)

    def test_execute_call_site_releases_in_finally(self):
        src = _source()
        # The release call paired with the acquire above. ``await
        # self._release_mcp_stack(_mcp_stack_held)`` must execute even
        # on exception paths so a parked old stack can be aclosed once
        # the last user releases. Pinning the literal call to ensure
        # the variable name still matches the acquire above.
        self.assertIn(
            "await self._release_mcp_stack(_mcp_stack_held)",
            src,
        )


class ContractClaimTests(unittest.TestCase):
    """Anchor PARITY.md row 35 (MCP consumption=Full) against source reality."""

    def test_run_query_docstring_names_lifespan_contract(self):
        src = _source()
        # The docstring must keep naming ``AgentExecutor._apply_mcp_config``
        # as the lifespan-entry point so future maintainers understand
        # the run_query side is a downstream consumer, not a per-request
        # initialiser. Drift here is a leading indicator of the
        # whole-contract slipping (the docstring usually changes first
        # when someone is about to re-spawn-per-request).
        self.assertIn("AgentExecutor._apply_mcp_config", src)

    def test_run_query_docstring_references_issue_526(self):
        src = _source()
        # #526 is the canonical issue for the lifespan-scoped MCP stack
        # contract. The docstring references it so spelunking from a
        # bug report lands on the right historical context. Remove the
        # ref and the next maintainer loses the breadcrumb.
        self.assertRegex(src, r"#526\b")


if __name__ == "__main__":
    unittest.main()
