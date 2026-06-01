"""Behavioral proof of the A2A task-poll contract the harness relies on.

Drives the pinned a2a-sdk's DefaultRequestHandler with an executor that mirrors
the harness's blocking/non-blocking branch, and asserts:
  1. non-blocking message/send returns a Task id immediately (before the work),
  2. tasks/get polls submitted->working->completed and carries the reply artifact,
  3. the DEFAULT blocking path still returns a Message (existing callers unaffected).

This guards against an a2a-sdk upgrade silently changing the non-blocking
semantics that the poll-based `ww send` depends on. Skipped where the SDK is
not installed (it is present in the harness test image via requirements.txt).
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("a2a")

from a2a.server.agent_execution import AgentExecutor  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.server.tasks.task_updater import TaskUpdater  # noqa: E402
from a2a.types import (  # noqa: E402
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    Role,
    TaskQueryParams,
    TaskState,
    TextPart,
)
from a2a.utils import new_agent_text_message  # noqa: E402


class _MirrorExecutor(AgentExecutor):
    """Mirrors harness executor.execute()'s blocking/non-blocking branch."""

    async def execute(self, context, event_queue):  # noqa: ANN001
        blocking = True
        cfg = getattr(context, "configuration", None)
        if cfg is not None and getattr(cfg, "blocking", None) is False:
            blocking = False
        if not blocking and context.task_id:
            up = TaskUpdater(event_queue, context.task_id, context.context_id)
            await up.submit()
            await up.start_work()
            await asyncio.sleep(0.3)  # simulate the turn
            reply = "RESULT-OK"
            await up.add_artifact([Part(root=TextPart(text=reply))], name="response")
            await up.complete(message=up.new_agent_message([Part(root=TextPart(text=reply))]))
        else:
            await event_queue.enqueue_event(new_agent_text_message("blocking-reply"))

    async def cancel(self, context, event_queue):  # noqa: ANN001
        pass


def _params(message_id: str, blocking: bool) -> MessageSendParams:
    return MessageSendParams(
        message=Message(role=Role.user, parts=[Part(root=TextPart(text="hi"))], message_id=message_id),
        configuration=MessageSendConfiguration(blocking=blocking, accepted_output_modes=["text/plain"]),
    )


def test_nonblocking_returns_task_fast_then_polls_to_completed() -> None:
    async def run() -> None:
        import time

        handler = DefaultRequestHandler(agent_executor=_MirrorExecutor(), task_store=InMemoryTaskStore())
        t0 = time.monotonic()
        result = await handler.on_message_send(_params("m1", blocking=False))
        elapsed = time.monotonic() - t0
        assert type(result).__name__ == "Task", f"expected Task, got {type(result).__name__}"
        # Returned on the submit event, BEFORE the 0.3s simulated turn.
        assert elapsed < 0.25, f"expected fast non-blocking return, got {elapsed:.3f}s"
        for _ in range(100):
            task = await handler.on_get_task(TaskQueryParams(id=result.id))
            if task.status.state in (
                TaskState.completed,
                TaskState.failed,
                TaskState.canceled,
                TaskState.rejected,
            ):
                assert task.status.state == TaskState.completed, task.status.state
                art = "".join(p.root.text for a in (task.artifacts or []) for p in a.parts)
                assert art == "RESULT-OK", f"artifact text={art!r}"
                return
            await asyncio.sleep(0.05)
        raise AssertionError("task never reached a terminal state")

    asyncio.run(run())


def test_blocking_default_returns_message() -> None:
    async def run() -> None:
        handler = DefaultRequestHandler(agent_executor=_MirrorExecutor(), task_store=InMemoryTaskStore())
        result = await handler.on_message_send(_params("m2", blocking=True))
        assert type(result).__name__ == "Message", f"expected Message, got {type(result).__name__}"

    asyncio.run(run())
