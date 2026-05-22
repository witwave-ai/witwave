"""Regression coverage for the openai executor's prompt-size cap (#1620).

Risk: a pathological caller could ship a multi-GB prompt body. Without
a hard cap, the prompt would be UTF-8 decoded, reflected through every
log/redaction path, and forwarded to the OpenAI Agents SDK before any
backpressure took effect — OOM-killing the pod long before the model
saw the request.

Fix: reject in ``execute()`` when ``len(prompt.encode("utf-8")) >
MAX_PROMPT_BYTES`` (default 10 MiB), bump ``backend_prompt_too_large_total``,
and surface a clean A2A error. The ``PromptTooLargeError`` type lives in
``shared/exceptions`` so callers/tests can detect the rejection
programmatically.

We follow the test_budget_check.py style: pin the source shape with
regexes (so the cap can't silently regress) and unit-test the small
isolatable pieces (the exception type + the metric registration)
without importing the full ``executor`` module — its SDK chain
(``agents``, ``computer``, …) is far too heavy for a focused unit test.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_EXECUTOR_PATH = _HERE / "executor.py"
_METRICS_PATH = _HERE / "metrics.py"


def _counter_value(counter, labels: dict) -> float:
    """Read a labeled counter's current value via the stable `.collect()` API.

    The previous test reached into prometheus-client internals via
    `c.labels(...)._value.get()`, which broke under the CI environment where
    `c.labels(...)` returned a `_Metric` (not a `Counter`) — likely a
    multi-version dependency-resolution edge case. `.collect()` is the
    documented public API and works uniformly across versions.
    """
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name == counter._name + "_total" and sample.labels == labels:
                return sample.value
    return 0.0


class PromptTooLargeErrorTests(unittest.TestCase):
    """Verify the shared exception carries the right diagnostic payload."""

    def setUp(self):
        sys.path.insert(0, str(_REPO_ROOT / "shared"))
        self.addCleanup(lambda: sys.path.remove(str(_REPO_ROOT / "shared")))
        # Reload to pick up any concurrent edits during dev.
        if "exceptions" in sys.modules:
            del sys.modules["exceptions"]
        self.exceptions = importlib.import_module("exceptions")

    def test_error_type_exists(self):
        self.assertTrue(hasattr(self.exceptions, "PromptTooLargeError"))

    def test_error_attributes(self):
        err = self.exceptions.PromptTooLargeError(2_000_000_000, 10 * 1024 * 1024)
        self.assertEqual(err.size_bytes, 2_000_000_000)
        self.assertEqual(err.limit_bytes, 10 * 1024 * 1024)
        self.assertIn("2000000000", str(err))
        self.assertIn(str(10 * 1024 * 1024), str(err))

    def test_error_is_exception(self):
        with self.assertRaises(self.exceptions.PromptTooLargeError):
            raise self.exceptions.PromptTooLargeError(1, 0)


class PromptSizeCapMetricRegistrationTests(unittest.TestCase):
    """``backend_prompt_too_large_total`` must register when METRICS_ENABLED."""

    def setUp(self):
        sys.path.insert(0, str(_HERE))
        self.addCleanup(lambda: sys.path.remove(str(_HERE)))
        # Force a fresh import under METRICS_ENABLED=1 so the counter is
        # actually instantiated rather than left at the module-level None.
        self._prev = os.environ.get("METRICS_ENABLED")
        os.environ["METRICS_ENABLED"] = "1"
        # Displace any cached `prometheus_client` (sibling
        # `test_health_ready_route_openai.py` installs a `_Metric` test-double
        # at module-import time that stubs labels()/inc()/observe() but NOT
        # `.collect()` — the stable read API `_counter_value()` below relies
        # on) and the cached `metrics` module so this test re-imports against
        # the real `prometheus_client` available in CI deps. The displaced
        # modules are restored in tearDown so siblings that imported the
        # stubbed versions at module load still see them for the rest of the
        # pytest run.
        self._displaced: dict[str, object] = {}
        for mod_name in ("prometheus_client", "metrics"):
            if mod_name in sys.modules:
                self._displaced[mod_name] = sys.modules.pop(mod_name)
        # prometheus_client uses a process-wide default registry; clear any
        # collectors from a previous import to avoid duplicate-registration
        # errors when this test re-imports metrics.py against the real
        # prometheus_client we just exposed above.
        try:
            import prometheus_client

            for name in list(prometheus_client.REGISTRY._names_to_collectors.keys()):
                try:
                    prometheus_client.REGISTRY.unregister(prometheus_client.REGISTRY._names_to_collectors[name])
                except Exception:
                    pass
        except Exception:
            pass
        self.metrics = importlib.import_module("metrics")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("METRICS_ENABLED", None)
        else:
            os.environ["METRICS_ENABLED"] = self._prev
        # Restore any prometheus_client / metrics stubs that sibling tests
        # installed before us so they keep working for tests that run later
        # in the same pytest session.
        for mod_name, mod_obj in self._displaced.items():
            sys.modules[mod_name] = mod_obj

    def test_counter_registered(self):
        self.assertIsNotNone(self.metrics.backend_prompt_too_large_total)

    def test_counter_increments(self):
        labels = {"agent": "test", "agent_id": "test", "backend": "openai"}
        before = _counter_value(self.metrics.backend_prompt_too_large_total, labels)
        self.metrics.backend_prompt_too_large_total.labels(**labels).inc()
        after = _counter_value(self.metrics.backend_prompt_too_large_total, labels)
        self.assertEqual(after - before, 1.0)


class PromptSizeCapSourceShapeTests(unittest.TestCase):
    """Pin the executor's cap shape so a future edit can't silently regress."""

    @classmethod
    def setUpClass(cls):
        cls.executor_source = _EXECUTOR_PATH.read_text(encoding="utf-8")
        cls.metrics_source = _METRICS_PATH.read_text(encoding="utf-8")

    def test_module_constant_present(self):
        # Default must be 10 MiB exactly. Bigger defaults defeat the point.
        pattern = re.compile(
            r'_MAX_PROMPT_BYTES\s*=\s*int\(\s*os\.environ\.get\(\s*"MAX_PROMPT_BYTES"\s*,'
            r"\s*str\(\s*10\s*\*\s*1024\s*\*\s*1024\s*\)\s*\)\s*\)"
        )
        self.assertRegex(self.executor_source, pattern)

    def test_size_check_present(self):
        # The check must compute UTF-8 byte length, not str length.
        pattern = re.compile(r'_prompt_bytes\s*=\s*len\(\s*prompt\.encode\(\s*"utf-8"\s*\)\s*\)')
        self.assertRegex(self.executor_source, pattern)
        guard = re.compile(r"if\s+_prompt_bytes\s*>\s*_MAX_PROMPT_BYTES\s*:")
        self.assertRegex(self.executor_source, guard)

    def test_counter_bumped_on_overflow(self):
        # On the rejection path the new counter must be incremented.
        bump = re.compile(r"backend_prompt_too_large_total\.labels\(\*\*_LABELS\)\.inc\(\)")
        self.assertRegex(self.executor_source, bump)

    def test_a2a_error_returned(self):
        # The rejection must surface as an A2A text message (clean
        # rejection), not a bare raise that crashes the worker.
        # PromptTooLargeError is constructed and stringified into the
        # response.
        ctor = re.compile(r"PromptTooLargeError\(\s*_prompt_bytes\s*,\s*_MAX_PROMPT_BYTES\s*\)")
        self.assertRegex(self.executor_source, ctor)
        msg = re.compile(r'new_agent_text_message\(\s*f"Error:\s*\{_too_large_err\}"\s*\)')
        self.assertRegex(self.executor_source, msg)

    def test_issue_cited(self):
        # Issue tag must appear at both the cap declaration and the
        # rejection path so future readers can find the rationale.
        # Two distinct comment occurrences expected.
        self.assertGreaterEqual(self.executor_source.count("#1620"), 2)

    def test_metric_registered_in_metrics_py(self):
        decl = re.compile(r"backend_prompt_too_large_total\s*=\s*prometheus_client\.Counter\(")
        self.assertRegex(self.metrics_source, decl)


if __name__ == "__main__":
    unittest.main()
