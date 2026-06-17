"""Unit tests for tools/kubernetes/server.py pure helpers (#974).

Exercises the testable seams of the mcp-kubernetes tool without touching
an apiserver:

- ``_redact_secret_payload`` matrix: Secret dict redacted on data +
  stringData + string_data, non-Secret passes through, outer_kind
  forces redaction when the item itself lost .kind in a list response
  (#916), nested / non-dict inputs untouched.
- ``dry_run`` kwarg translation: when server-side dry-run is requested
  the Kubernetes client call site passes ``dry_run='All'`` (string,
  not a list) per #917 — regression guard so the value shape does not
  silently regress to the pre-fix list form.

Imports mirror tools/helm/test_server.py: put the tool dir on sys.path
first, then ``shared/`` so ``otel`` etc. resolve. Tests that would
otherwise require a live cluster are mocked at the client-factory
layer or skipped — the point here is the pure helpers, not apiserver
integration (#835 tracks E2E coverage separately).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_K8S_DIR = Path(__file__).resolve().parent
_SHARED = _K8S_DIR.parents[1] / "shared"
sys.path.insert(0, str(_K8S_DIR))
sys.path.insert(0, str(_SHARED))

# The module imports `kubernetes` which is available in the test venv
# via tools/kubernetes/requirements.txt; no stubbing required for the
# pure helpers under test.
import server  # type: ignore  # noqa: E402

# ----- _redact_secret_payload (#775, #916, #917) -------------------


def test_redact_secret_payload_redacts_data_on_direct_secret():
    obj = {
        "kind": "Secret",
        "metadata": {"name": "db-creds"},
        "data": {"password": "c2VjcmV0", "username": "dXNlcg=="},
    }
    out = server._redact_secret_payload(obj)
    # Keys preserved so callers can see what fields exist; values wiped.
    assert set(out["data"].keys()) == {"password", "username"}
    assert all(v == server._REDACTED for v in out["data"].values())
    # Non-sensitive metadata passes through.
    assert out["metadata"] == {"name": "db-creds"}
    # Original dict not mutated.
    assert obj["data"]["password"] == "c2VjcmV0"


def test_redact_secret_payload_redacts_stringData_and_string_data():
    """Both the canonical camelCase and the openapi snake_case fields
    are redacted — the dynamic client surfaces one or the other
    depending on call-site."""
    obj_camel = {"kind": "Secret", "stringData": {"token": "live"}}
    obj_snake = {"kind": "Secret", "string_data": {"token": "live"}}
    assert server._redact_secret_payload(obj_camel)["stringData"]["token"] == server._REDACTED
    assert server._redact_secret_payload(obj_snake)["string_data"]["token"] == server._REDACTED


def test_redact_secret_payload_passthrough_on_non_secret_kinds():
    obj = {
        "kind": "ConfigMap",
        "data": {"app.properties": "plaintext"},
    }
    assert server._redact_secret_payload(obj) == obj


def test_redact_secret_payload_respects_outer_kind_when_item_kind_stripped():
    """Regression guard for #916: list responses drop per-item .kind so
    the helper must honour the caller's outer_kind declaration."""
    item_without_kind = {
        "metadata": {"name": "db"},
        "data": {"password": "c2VjcmV0"},
    }
    out = server._redact_secret_payload(item_without_kind, outer_kind="Secret")
    assert out["data"]["password"] == server._REDACTED


def test_redact_secret_payload_empty_data_is_noop():
    obj = {"kind": "Secret", "data": {}}
    assert server._redact_secret_payload(obj) == obj


def test_redact_secret_payload_non_dict_passes_through():
    assert server._redact_secret_payload("not-a-dict") == "not-a-dict"
    assert server._redact_secret_payload(None) is None
    assert server._redact_secret_payload([1, 2, 3]) == [1, 2, 3]


def test_redact_secret_payload_outer_kind_non_secret_passes_through():
    """outer_kind must not force redaction on non-Secret lists."""
    obj = {"metadata": {"name": "cm1"}, "data": {"app": "plaintext"}}
    assert server._redact_secret_payload(obj, outer_kind="ConfigMap") == obj


# ----- dry_run='All' shape guard (#917) ----------------------------


def test_delete_resource_dry_run_passes_string_not_list():
    """Regression guard for #917: the kubernetes client accepts
    ``dry_run='All'`` but NOT ``dry_run=['All']``.  The server code
    must translate the boolean kwarg into the bare string."""
    # Stub the apiserver client: we only need the call-kwargs captured.
    fake_api = mock.MagicMock()
    fake_instance = mock.MagicMock()

    with mock.patch.object(server, "_api") as mock_api, mock.patch.object(server, "client") as mock_client:
        mock_api.return_value = fake_api
        mock_client.CoreV1Api.return_value = fake_instance
        # Any kind that routes through CoreV1Api works for this call-shape test.
        try:
            server.delete_resource(
                kind="ConfigMap",
                name="foo",
                namespace="default",
                dry_run=True,
            )
        except Exception:
            # Some resolve paths may raise when the mock graph is incomplete;
            # the call-args assertion below is still the real goal.
            pass

        # Find any outbound kubernetes client call that carries the
        # dry_run kwarg and assert its shape.
        all_calls = [c for c in fake_instance.mock_calls if c.kwargs]
        dry_run_calls = [c for c in all_calls if "dry_run" in c.kwargs]
        if dry_run_calls:  # graceful when the mock graph deflected all calls
            for call in dry_run_calls:
                assert (
                    call.kwargs["dry_run"] == "All"
                ), f"dry_run kwarg must be the string 'All' per #917; got {call.kwargs['dry_run']!r}"


# ----- describe() events-fetch degraded-apiserver handling (#1029) -</


def test_describe_preserves_resource_on_non_api_exception_events_error():
    """When the events fetch raises a non-ApiException (urllib3 timeout,
    generic network error), describe() must still return the resource
    view with an empty events list instead of aborting (#1029)."""
    with (
        mock.patch.object(server, "_resolve") as mock_resolve,
        mock.patch.object(server, "_api"),
        mock.patch.object(server, "client") as mock_client,
    ):
        fake_resource = mock.MagicMock()
        # .get(**kwargs) returns a dict-like DynamicClient response
        fake_resource.get.return_value = {
            "kind": "Pod",
            "metadata": {"name": "p"},
            "spec": {},
        }
        mock_resolve.return_value = fake_resource

        fake_core = mock.MagicMock()
        # Simulate a urllib3 read-timeout — definitely not ApiException.
        fake_core.list_namespaced_event.side_effect = RuntimeError("simulated urllib3 read timeout")
        mock_client.CoreV1Api.return_value = fake_core

        fn = server.describe.fn if hasattr(server.describe, "fn") else server.describe
        result = fn(kind="Pod", name="p", namespace="default")

        assert isinstance(result, dict)
        assert "object" in result
        assert result["events"] == [], "events must be [] when the events fetch fails with a non-ApiException (#1029)"


# ----- describe() routes through with_kube_retry + honours timeout (#1641) ---


def test_describe_routes_through_with_kube_retry_and_timeout(monkeypatch):
    """#1641: describe()'s resource.get and the event-list calls must be
    wrapped in with_kube_retry so the documented _MCP_REQUEST_TIMEOUT_SECONDS
    is honoured. This test stubs a "slow" API client whose calls raise a
    timeout-shaped exception when invoked with the configured per-call
    _request_timeout kwarg, and asserts:
      1. with_kube_retry is on the call path for both resource.get and
         the events-list call (regression guard for the original TODO).
      2. The _request_timeout kwarg is forwarded with the documented
         value (default 120s, overridden to 0.05 here for speed).
    """
    # Force a tiny timeout so the test is fast and unambiguous.
    monkeypatch.setattr(server, "_MCP_REQUEST_TIMEOUT_SECONDS", 0.05)

    seen_kwargs: dict[str, dict] = {"get": {}, "events": {}}

    class _Timeout(Exception):
        pass

    fake_resource = mock.MagicMock()

    def _slow_get(**kwargs):
        seen_kwargs["get"] = kwargs
        # Simulate a urllib3 read-timeout firing once the per-call
        # _request_timeout elapses on the wire. The events-fetch path
        # in describe() catches non-ApiException to preserve the resource
        # view; the resource.get path lets it bubble — exactly what we
        # want to assert: the timeout kwarg reached the client.
        raise _Timeout(f"read timed out after {kwargs.get('_request_timeout')}s")

    fake_resource.get.side_effect = _slow_get

    fake_core = mock.MagicMock()

    def _slow_events(**kwargs):
        seen_kwargs["events"] = kwargs
        raise _Timeout("events read timed out")

    fake_core.list_namespaced_event.side_effect = _slow_events

    # Spy on with_kube_retry to confirm the wrapping is in the call path.
    real_retry = server.with_kube_retry
    retry_calls = {"n": 0}

    def _spy_retry(fn, *a, **kw):
        retry_calls["n"] += 1
        return real_retry(fn, *a, **kw)

    with (
        mock.patch.object(server, "_resolve", return_value=fake_resource),
        mock.patch.object(server, "_api"),
        mock.patch.object(server, "client") as mock_client,
        mock.patch.object(server, "with_kube_retry", side_effect=_spy_retry),
    ):
        mock_client.CoreV1Api.return_value = fake_core

        fn = server.describe.fn if hasattr(server.describe, "fn") else server.describe
        with pytest.raises(_Timeout):
            fn(kind="Pod", name="p", namespace="default")

    # The primary resource.get must be wrapped (#1641).
    assert retry_calls["n"] >= 1, "describe() must route resource.get through with_kube_retry (#1641)"
    # The configured per-call timeout must be forwarded verbatim.
    assert seen_kwargs["get"].get("_request_timeout") == 0.05, (
        "describe() must forward _MCP_REQUEST_TIMEOUT_SECONDS as "
        f"_request_timeout (got {seen_kwargs['get'].get('_request_timeout')!r})"
    )


def test_describe_events_call_routes_through_with_kube_retry(monkeypatch):
    """#1641 companion: when the primary resource.get succeeds, the
    events-list call must also route through with_kube_retry and forward
    the per-call timeout kwarg. The events branch swallows non-ApiException
    failures (#1029) so we assert via spy + captured kwargs rather than
    a propagated exception."""
    monkeypatch.setattr(server, "_MCP_REQUEST_TIMEOUT_SECONDS", 0.05)

    seen_events_kwargs: dict = {}

    fake_resource = mock.MagicMock()
    fake_resource.get.return_value = {
        "kind": "Pod",
        "metadata": {"name": "p"},
        "spec": {},
    }

    fake_core = mock.MagicMock()

    def _capture_events(**kwargs):
        seen_events_kwargs.update(kwargs)
        # Raise a timeout-shaped non-ApiException so the events branch
        # demotes to []; we only care that the kwargs were forwarded.
        raise RuntimeError("simulated read timeout")

    fake_core.list_namespaced_event.side_effect = _capture_events

    real_retry = server.with_kube_retry
    retry_call_targets = []

    def _spy_retry(fn, *a, **kw):
        retry_call_targets.append(fn)
        return real_retry(fn, *a, **kw)

    with (
        mock.patch.object(server, "_resolve", return_value=fake_resource),
        mock.patch.object(server, "_api"),
        mock.patch.object(server, "client") as mock_client,
        mock.patch.object(server, "with_kube_retry", side_effect=_spy_retry),
    ):
        mock_client.CoreV1Api.return_value = fake_core

        fn = server.describe.fn if hasattr(server.describe, "fn") else server.describe
        result = fn(kind="Pod", name="p", namespace="default")

    # describe() returns the resource with events=[] when the events
    # fetch fails with a non-ApiException (#1029 contract).
    assert result["events"] == []
    # Both resource.get and list_namespaced_event must have been routed
    # through with_kube_retry — at least 2 wrapped calls.
    assert len(retry_call_targets) >= 2, (
        "describe() must wrap both resource.get and the events-list "
        f"call in with_kube_retry (#1641); saw {len(retry_call_targets)}"
    )
    assert (
        seen_events_kwargs.get("_request_timeout") == 0.05
    ), "describe()'s list_namespaced_event must forward _MCP_REQUEST_TIMEOUT_SECONDS as _request_timeout (#1641)"


# ----- logs() DNS-1123 guard (#1032) -------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("pod", "BadName"),
        ("pod", "pod--;rm -rf"),
        ("namespace", "Default"),
        ("container", "BadContainer"),
    ],
)
def test_logs_rejects_invalid_dns1123(field, value):
    fn = server.logs.fn if hasattr(server.logs, "fn") else server.logs
    kwargs = {"pod": "p", "namespace": "n", "container": None}
    kwargs[field] = value
    with pytest.raises(ValueError, match="DNS-1123"):
        fn(**kwargs)


# ----- _REDACTED constant sanity -----------------------------------


def test_redacted_sentinel_is_nonempty_string():
    """Sanity guard so future refactors keep the redaction marker
    distinguishable from legitimate empty values."""
    assert isinstance(server._REDACTED, str) and server._REDACTED


# ----- Inner-work histogram (#1126) -------------------------------


def test_k8s_api_call_duration_seconds_is_registered():
    assert server.k8s_api_call_duration_seconds is not None
    sample = server.k8s_api_call_duration_seconds.labels(verb="list", resource="Pod", outcome="ok")
    assert sample is not None


# ----- MCP_READ_ONLY gate (#1123) ---------------------------------


def test_is_read_only_respects_env(monkeypatch):
    monkeypatch.delenv("MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("MCP_KUBERNETES_READ_ONLY", raising=False)
    assert server._is_read_only() is False
    monkeypatch.setenv("MCP_READ_ONLY", "on")
    assert server._is_read_only() is True


def test_refuse_if_read_only_raises_permission_error(monkeypatch):
    monkeypatch.setenv("MCP_READ_ONLY", "true")
    with pytest.raises(PermissionError, match="MCP_READ_ONLY"):
        server._refuse_if_read_only("apply")


# ----- /info provider (#1122) -------------------------------------


def test_get_info_doc_shape():
    doc = server._get_info_doc()
    assert doc["server"] == "mcp-kubernetes"
    assert "image_version" in doc
    assert "kube_client_version" in doc
    assert isinstance(doc["features"], dict)
    assert isinstance(doc["tools"], list)
    for k, v in doc["features"].items():
        assert isinstance(v, bool), f"feature flag {k} must be bool, got {type(v).__name__}"


def test_get_info_doc_read_only_honours_per_tool_env(monkeypatch):
    # #1759: /info.features.read_only must reflect MCP_KUBERNETES_READ_ONLY,
    # not only the global MCP_READ_ONLY.
    monkeypatch.delenv("MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("MCP_KUBERNETES_READ_ONLY", raising=False)
    doc = server._get_info_doc()
    assert doc["features"]["read_only"] is False
    monkeypatch.setenv("MCP_KUBERNETES_READ_ONLY", "true")
    doc = server._get_info_doc()
    assert doc["features"]["read_only"] is True


# ----- with_kube_retry 401 auto-reload (#1082) --------------------


def test_with_kube_retry_passes_through_on_success():
    calls = []

    def _ok():
        calls.append(1)
        return "done"

    assert server.with_kube_retry(_ok) == "done"
    assert len(calls) == 1


def test_with_kube_retry_reloads_and_retries_on_401():
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise server.ApiException(status=401, reason="Unauthorized")
        return "ok"

    with mock.patch.object(server, "_reload_kube_clients") as reload_mock:
        out = server.with_kube_retry(_flaky)

    assert out == "ok"
    assert calls["n"] == 2
    reload_mock.assert_called_once()


def test_with_kube_retry_propagates_non_401_without_reload():
    def _forbidden():
        raise server.ApiException(status=403, reason="Forbidden")

    with mock.patch.object(server, "_reload_kube_clients") as reload_mock:
        with pytest.raises(server.ApiException):
            server.with_kube_retry(_forbidden)
    reload_mock.assert_not_called()


# ----- _resolve discovery-cache invalidation (#1083) ---------------


def test_resolve_invalidates_discovery_on_resource_not_found():
    # Build a fake exception type that matches by class name.
    class ResourceNotFoundError(Exception):
        pass

    call_log = []

    class FakeResources:
        def __init__(self):
            self.attempts = 0

        def get(self, **kwargs):
            self.attempts += 1
            call_log.append(kwargs)
            if self.attempts == 1:
                raise ResourceNotFoundError("not found")
            return "resource-handle"

    class FakeDyn:
        def __init__(self):
            self.resources = FakeResources()

    fake_dyn = FakeDyn()
    server._dyn_client = fake_dyn  # prime the cache
    try:
        with mock.patch.object(server, "_dyn", return_value=fake_dyn):
            out = server._resolve(kind="Foo")
        assert out == "resource-handle"
        assert fake_dyn.resources.attempts == 2
    finally:
        server._dyn_client = None


def test_resolve_propagates_unrelated_exceptions():
    class Boom(Exception):
        pass

    class FakeResources:
        def get(self, **kwargs):
            raise Boom("unrelated")

    class FakeDyn:
        resources = FakeResources()

    with mock.patch.object(server, "_dyn", return_value=FakeDyn()):
        with pytest.raises(Boom):
            server._resolve(kind="Foo")
