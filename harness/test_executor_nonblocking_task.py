"""Source-inspection guard for the non-blocking A2A task path in execute().

When a caller sets configuration.blocking=false (e.g. `ww send`), the harness
must drive the A2A task lifecycle (submit -> start_work -> add_artifact ->
complete) instead of enqueuing a single terminal Message, so the SDK returns a
task id immediately and the client polls tasks/get — no single connection held
for the whole turn. The DEFAULT blocking path must stay a Message (existing
callers: zora call-peer, the dashboard). These checks are source-level (the
executor pulls in the full SDK + backend deps, so a behavioral test lives in
test_a2a_nonblocking_contract.py); they fail loudly if a refactor drops the
branch or the lifecycle calls.
"""

from __future__ import annotations

from pathlib import Path

EXECUTOR = Path(__file__).parent / "executor.py"


def test_taskupdater_imported() -> None:
    src = EXECUTOR.read_text()
    assert "from a2a.server.tasks.task_updater import TaskUpdater" in src
    assert "from a2a.types import Part, TextPart" in src


def test_nonblocking_branches_on_configuration() -> None:
    src = EXECUTOR.read_text()
    assert (
        'getattr(_cfg, "blocking", None) is False' in src
    ), "execute() must detect configuration.blocking=false to choose the task path"


def test_nonblocking_drives_task_lifecycle() -> None:
    src = EXECUTOR.read_text()
    for call in ("TaskUpdater(event_queue", ".submit()", ".start_work()", ".add_artifact(", ".complete("):
        assert call in src, f"non-blocking task path must call {call!r}"
    # On error the task is driven to a terminal failed state so the poller
    # doesn't spin until its own deadline.
    assert ".failed(" in src, "error path must mark the task failed for the poller"


def test_blocking_path_still_enqueues_message() -> None:
    # The default (blocking) path must remain a single Message enqueue so
    # existing callers are byte-for-byte unaffected (#harness-32603).
    src = EXECUTOR.read_text()
    assert "new_agent_text_message(" in src
    assert "event_queue.enqueue_event(_msg)" in src
