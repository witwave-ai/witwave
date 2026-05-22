"""Source-shape regression for gemini's _emit_chunk metric ordering.

History: #1721 (mirroring codex #1199) introduced the invariant that the
``_chunks_emitted`` counter and ``backend_streaming_events_emitted_total``
metric must only advance AFTER the per-chunk delivery surface has been
attempted — counting before the surface overstated traffic on delivery
failure and tripped the ``_chunks_emitted > 0`` skip on the aggregated
final-flush, leaving the client with neither the dropped chunk nor the
recovered aggregate.

When the original test was written, the delivery surface was the
per-chunk ``await event_queue.enqueue_event(...)`` call. That call was
later removed (see ``backends/gemini/executor.py`` ``_emit_chunk``
comments around L3893–3899: per-chunk A2A enqueue caused blocking
``message/send`` consumers to receive only the first chunk because the
A2A SDK's blocking aggregator returns on the first ``Message`` event).
After the removal, the only remaining per-chunk delivery surface is the
session_stream broadcaster:

    _sess_stream.publish_chunk(role="assistant", seq=..., content=text, final=False)

The #1721 invariant is preserved relative to the new surface: counter +
metric increments must come AFTER ``_sess_stream.publish_chunk(...)`` so
that a publish failure is not counted as a delivered chunk. This test
pins that ordering so the regression pattern can't be re-introduced via
either reshuffle (counter-before-publish) or by re-adding a per-chunk
delivery surface that gets counted *before* its attempt.

The matching openai shape lives at ``backends/openai/executor.py`` —
codex orders publish_chunk → _chunks_emitted += 1 → metric.inc() the
same way. (No codex test pins this today; the gemini test is the
canonical pin.) Claude diverges intentionally — see the comment in
``backends/claude/executor.py`` ``_emit_chunk`` for that rationale.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_EXECUTOR_PATH = Path(__file__).resolve().parent / "executor.py"


class GeminiEmitChunkMetricOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _EXECUTOR_PATH.read_text(encoding="utf-8")

    def _emit_chunk_body(self) -> str:
        m = re.search(
            r"async def _emit_chunk\(text: str\) -> None:(.*?)(?=\n\s{8}\S|\Z)",
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "_emit_chunk function body not found")
        return m.group(1)

    def test_increments_after_publish_chunk(self):
        # The delivery-surface call (_sess_stream.publish_chunk) must precede
        # both _chunks_emitted += 1 and the streaming-events metric .inc().
        # Preserves the #1721 invariant against the new (post-enqueue-removal)
        # delivery surface.
        body = self._emit_chunk_body()
        publish_idx = body.find("_sess_stream.publish_chunk(")
        increment_idx = body.find("_chunks_emitted += 1")
        metric_idx = body.find("backend_streaming_events_emitted_total")
        self.assertGreater(publish_idx, 0, "_sess_stream.publish_chunk(...) not found in body")
        self.assertGreater(
            increment_idx,
            publish_idx,
            "_chunks_emitted += 1 must come AFTER _sess_stream.publish_chunk(...) (#1721 invariant "
            "rebased onto the post-enqueue-removal delivery surface)",
        )
        self.assertGreater(
            metric_idx,
            publish_idx,
            "backend_streaming_events_emitted_total.inc() must come AFTER "
            "_sess_stream.publish_chunk(...) (#1721 invariant)",
        )

    def test_per_chunk_enqueue_stays_removed(self):
        # The A2A per-chunk enqueue removal is load-bearing for blocking
        # message/send consumers (see _emit_chunk comments). If a future
        # edit re-adds it, this test fails so the author has to revisit
        # the rationale rather than silently re-introducing the bug.
        body = self._emit_chunk_body()
        self.assertNotIn(
            "await event_queue.enqueue_event",
            body,
            "Per-chunk A2A event_queue.enqueue_event must NOT be re-added — "
            "the A2A SDK's blocking aggregator returns on the first Message "
            "event, so per-chunk Message emission breaks blocking message/send "
            "consumers (see backends/claude/executor.py _emit_chunk for the "
            "full rationale).",
        )


if __name__ == "__main__":
    unittest.main()
