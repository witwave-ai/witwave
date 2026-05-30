package agent

import (
	"context"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func TestNormalizeKubernetesApiAccessMode(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in   string
		want string
	}{
		{"", KubernetesApiAccessModeReadOnly},
		{"readOnly", KubernetesApiAccessModeReadOnly},
		{"r/o", KubernetesApiAccessModeReadOnly},
		{"namespaceWrite", KubernetesApiAccessModeNamespaceWrite},
		{"namespace-write", KubernetesApiAccessModeNamespaceWrite},
		{"rw", KubernetesApiAccessModeNamespaceWrite},
		{"agentLifecycle", KubernetesApiAccessModeAgentLifecycle},
		{"agent-lifecycle", KubernetesApiAccessModeAgentLifecycle},
		{"lifecycle", KubernetesApiAccessModeAgentLifecycle},
	}
	for _, tc := range cases {
		t.Run(tc.in, func(t *testing.T) {
			t.Parallel()
			got, err := NormalizeKubernetesApiAccessMode(tc.in)
			if err != nil {
				t.Fatalf("NormalizeKubernetesApiAccessMode(%q) returned error: %v", tc.in, err)
			}
			if got != tc.want {
				t.Fatalf("NormalizeKubernetesApiAccessMode(%q) = %q; want %q", tc.in, got, tc.want)
			}
		})
	}
}

func TestNormalizeKubernetesApiAccessModeRejectsUnknown(t *testing.T) {
	t.Parallel()
	if _, err := NormalizeKubernetesApiAccessMode("cluster-admin"); err == nil {
		t.Fatal("expected an error for unsupported mode")
	}
}

func TestNewKubernetesApiAccessSpec(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in   string
		want string
	}{
		{"", KubernetesApiAccessModeReadOnly},
		{"readOnly", KubernetesApiAccessModeReadOnly},
		{"r/o", KubernetesApiAccessModeReadOnly},
		{"namespaceWrite", KubernetesApiAccessModeNamespaceWrite},
		{"namespace-write", KubernetesApiAccessModeNamespaceWrite},
		{"rw", KubernetesApiAccessModeNamespaceWrite},
		{"agentLifecycle", KubernetesApiAccessModeAgentLifecycle},
		{"agent-lifecycle", KubernetesApiAccessModeAgentLifecycle},
		{"lifecycle", KubernetesApiAccessModeAgentLifecycle},
	}
	for _, tc := range cases {
		t.Run(tc.in, func(t *testing.T) {
			t.Parallel()
			got, err := NewKubernetesApiAccessSpec(tc.in)
			if err != nil {
				t.Fatalf("NewKubernetesApiAccessSpec(%q) returned error: %v", tc.in, err)
			}
			if got == nil {
				t.Fatalf("NewKubernetesApiAccessSpec(%q) returned nil spec", tc.in)
			}
			if !got.Enabled {
				t.Fatalf("NewKubernetesApiAccessSpec(%q).Enabled = false; want true", tc.in)
			}
			if got.Mode != tc.want {
				t.Fatalf("NewKubernetesApiAccessSpec(%q).Mode = %q; want %q", tc.in, got.Mode, tc.want)
			}
		})
	}
}

func TestNewKubernetesApiAccessSpecRejectsUnknown(t *testing.T) {
	t.Parallel()
	got, err := NewKubernetesApiAccessSpec("cluster-admin")
	if err == nil {
		t.Fatal("expected an error for unsupported mode")
	}
	if got != nil {
		t.Fatalf("expected nil spec on error; got %+v", got)
	}
}

func TestApplyKubernetesApiAccessInPlace_AddsReadOnly(t *testing.T) {
	cr := seedAgent("mira", "witwave-self", nil)
	changed, err := applyKubernetesApiAccessInPlace(cr, &KubernetesApiAccessSpec{
		Enabled: true,
		Mode:    KubernetesApiAccessModeReadOnly,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected Kubernetes API access change")
	}
	enabled, found, err := unstructured.NestedBool(cr.Object, "spec", "kubernetesApiAccess", "enabled")
	if err != nil || !found || !enabled {
		t.Fatalf("enabled = %v found=%v err=%v; want true", enabled, found, err)
	}
	mode, found, err := unstructured.NestedString(cr.Object, "spec", "kubernetesApiAccess", "mode")
	if err != nil || !found || mode != KubernetesApiAccessModeReadOnly {
		t.Fatalf("mode = %q found=%v err=%v; want readOnly", mode, found, err)
	}
}

func TestApplyKubernetesApiAccessInPlace_UpdatesMode(t *testing.T) {
	cr := seedAgent("mira", "witwave-self", func(spec map[string]interface{}) {
		spec["kubernetesApiAccess"] = map[string]interface{}{
			"enabled": true,
			"mode":    KubernetesApiAccessModeReadOnly,
		}
	})
	changed, err := applyKubernetesApiAccessInPlace(cr, &KubernetesApiAccessSpec{
		Enabled: true,
		Mode:    KubernetesApiAccessModeNamespaceWrite,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected mode update")
	}
	mode, _, _ := unstructured.NestedString(cr.Object, "spec", "kubernetesApiAccess", "mode")
	if mode != KubernetesApiAccessModeNamespaceWrite {
		t.Fatalf("mode = %q; want namespaceWrite", mode)
	}
}

func TestApplyKubernetesApiAccessInPlace_IsIdempotent(t *testing.T) {
	cr := seedAgent("mira", "witwave-self", func(spec map[string]interface{}) {
		spec["kubernetesApiAccess"] = map[string]interface{}{
			"enabled": true,
			"mode":    KubernetesApiAccessModeReadOnly,
		}
	})
	changed, err := applyKubernetesApiAccessInPlace(cr, &KubernetesApiAccessSpec{
		Enabled: true,
		Mode:    KubernetesApiAccessModeReadOnly,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if changed {
		t.Fatal("expected no changes")
	}
}

func TestKubernetesApiAccessPlanValue(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "namespaceWrite renders the namespace-write banner",
			in:   KubernetesApiAccessModeNamespaceWrite,
			want: "namespaceWrite (bounded namespace-local remediation; no secrets/RBAC/cluster resources)",
		},
		{
			name: "readOnly renders the read-only banner",
			in:   KubernetesApiAccessModeReadOnly,
			want: "readOnly (get/list/watch + pod logs; no mutating verbs)",
		},
		{
			name: "empty input falls through to the read-only banner",
			in:   "",
			want: "readOnly (get/list/watch + pod logs; no mutating verbs)",
		},
		{
			name: "unknown input falls through to the read-only banner",
			in:   "cluster-admin",
			want: "readOnly (get/list/watch + pod logs; no mutating verbs)",
		},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := kubernetesApiAccessPlanValue(tc.in)
			if got != tc.want {
				t.Fatalf("kubernetesApiAccessPlanValue(%q) = %q; want %q", tc.in, got, tc.want)
			}
		})
	}
}

func TestRemoveKubernetesApiAccessInPlace(t *testing.T) {
	cr := seedAgent("mira", "witwave-self", func(spec map[string]interface{}) {
		spec["kubernetesApiAccess"] = map[string]interface{}{
			"enabled": true,
			"mode":    KubernetesApiAccessModeReadOnly,
		}
	})
	changed, err := removeKubernetesApiAccessInPlace(cr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !changed {
		t.Fatal("expected removal")
	}
	if _, found, err := unstructured.NestedMap(cr.Object, "spec", "kubernetesApiAccess"); err != nil || found {
		t.Fatalf("kubernetesApiAccess found=%v err=%v; want removed", found, err)
	}
}

// ---------------------------------------------------------------------------
// KubernetesApiAccessEnable / KubernetesApiAccessDisable — full-flow tests
// using the package's shared fake-client fixtures (cr_helpers_test.go).
// Mirrors the (ctx, target, cfg, opts) shape of TestDelete_HappyPath_NoFlags
// and TestBackendAdd_*. Wait:false short-circuits the rollout-wait code path
// so the dynamic+core fakes alone are sufficient.
// ---------------------------------------------------------------------------

func TestKubernetesApiAccessEnable_AddsReadOnly(t *testing.T) {
	cr := seedAgent("hello", "default", nil)
	dyn := makeFakeDynamic(cr)
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	out := captureOut()
	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		Mode:      "readOnly",
		AssumeYes: true,
		Out:       out,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	updated := readAgent(t, dyn, "default", "hello")
	enabled, found, err := unstructured.NestedBool(updated.Object, "spec", "kubernetesApiAccess", "enabled")
	if err != nil || !found || !enabled {
		t.Fatalf("enabled = %v found=%v err=%v; want true", enabled, found, err)
	}
	mode, _, _ := unstructured.NestedString(updated.Object, "spec", "kubernetesApiAccess", "mode")
	if mode != KubernetesApiAccessModeReadOnly {
		t.Errorf("mode = %q; want readOnly", mode)
	}
	mustContain(t, out.String(), "Updated WitwaveAgent default/hello.")
	// Wait:false → rollout-wait short-circuits with the skip banner.
	mustContain(t, out.String(), "Skipping rollout wait")
}

func TestKubernetesApiAccessEnable_UpdatesMode(t *testing.T) {
	cr := seedAgent("hello", "default", func(spec map[string]interface{}) {
		spec["kubernetesApiAccess"] = map[string]interface{}{
			"enabled": true,
			"mode":    KubernetesApiAccessModeReadOnly,
		}
	})
	dyn := makeFakeDynamic(cr)
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	out := captureOut()
	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		Mode:      "namespaceWrite",
		AssumeYes: true,
		Out:       out,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	updated := readAgent(t, dyn, "default", "hello")
	mode, _, _ := unstructured.NestedString(updated.Object, "spec", "kubernetesApiAccess", "mode")
	if mode != KubernetesApiAccessModeNamespaceWrite {
		t.Errorf("mode = %q; want namespaceWrite", mode)
	}
	mustContain(t, out.String(), "Updated WitwaveAgent default/hello.")
}

func TestKubernetesApiAccessEnable_AlreadyConfigured_NoOp(t *testing.T) {
	cr := seedAgent("hello", "default", func(spec map[string]interface{}) {
		spec["kubernetesApiAccess"] = map[string]interface{}{
			"enabled": true,
			"mode":    KubernetesApiAccessModeReadOnly,
		}
	})
	dyn := makeFakeDynamic(cr)
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	out := captureOut()
	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		Mode:      "readOnly",
		AssumeYes: true,
		Out:       out,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	mustContain(t, out.String(), "already has Kubernetes API access configured as readOnly")
	mustNotContain(t, out.String(), "Updated WitwaveAgent")
}

func TestKubernetesApiAccessEnable_AgentNotFound(t *testing.T) {
	dyn := makeFakeDynamic() // no agents seeded
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "ghost",
		Namespace: "default",
		Mode:      "readOnly",
		AssumeYes: true,
		Out:       captureOut(),
	})
	if err == nil {
		t.Fatal("expected not-found error")
	}
}

func TestKubernetesApiAccessEnable_RejectsUnknownMode(t *testing.T) {
	cr := seedAgent("hello", "default", nil)
	dyn := makeFakeDynamic(cr)
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		Mode:      "cluster-admin",
		AssumeYes: true,
		Out:       captureOut(),
	})
	if err == nil {
		t.Fatal("expected unsupported-mode error")
	}
}

func TestKubernetesApiAccessEnable_RequiresOut(t *testing.T) {
	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		// Out intentionally nil.
	})
	if err == nil {
		t.Fatal("expected error when Out is nil")
	}
}

func TestKubernetesApiAccessEnable_RequiresName(t *testing.T) {
	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Namespace: "default",
		Out:       captureOut(),
	})
	if err == nil {
		t.Fatal("expected error when Name is empty")
	}
}

func TestKubernetesApiAccessEnable_RequiresNamespace(t *testing.T) {
	err := KubernetesApiAccessEnable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name: "hello",
		Out:  captureOut(),
	})
	if err == nil {
		t.Fatal("expected error when Namespace is empty")
	}
}

func TestKubernetesApiAccessDisable_RemovesAccess(t *testing.T) {
	cr := seedAgent("hello", "default", func(spec map[string]interface{}) {
		spec["kubernetesApiAccess"] = map[string]interface{}{
			"enabled": true,
			"mode":    KubernetesApiAccessModeReadOnly,
		}
	})
	dyn := makeFakeDynamic(cr)
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	out := captureOut()
	err := KubernetesApiAccessDisable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		AssumeYes: true,
		Out:       out,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	updated := readAgent(t, dyn, "default", "hello")
	if _, found, err := unstructured.NestedMap(updated.Object, "spec", "kubernetesApiAccess"); err != nil || found {
		t.Fatalf("kubernetesApiAccess found=%v err=%v; want removed", found, err)
	}
	mustContain(t, out.String(), "Updated WitwaveAgent default/hello.")
}

func TestKubernetesApiAccessDisable_AlreadyDisabled_NoOp(t *testing.T) {
	cr := seedAgent("hello", "default", nil) // no kubernetesApiAccess seeded
	dyn := makeFakeDynamic(cr)
	t.Cleanup(withFakeClients(t, dyn, makeFakeK8s()))

	out := captureOut()
	err := KubernetesApiAccessDisable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		AssumeYes: true,
		Out:       out,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	mustContain(t, out.String(), "already has Kubernetes API access disabled")
	mustNotContain(t, out.String(), "Updated WitwaveAgent")
}

func TestKubernetesApiAccessDisable_RequiresOut(t *testing.T) {
	err := KubernetesApiAccessDisable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name:      "hello",
		Namespace: "default",
		// Out intentionally nil.
	})
	if err == nil {
		t.Fatal("expected error when Out is nil")
	}
}

func TestKubernetesApiAccessDisable_RequiresName(t *testing.T) {
	err := KubernetesApiAccessDisable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Namespace: "default",
		Out:       captureOut(),
	})
	if err == nil {
		t.Fatal("expected error when Name is empty")
	}
}

func TestKubernetesApiAccessDisable_RequiresNamespace(t *testing.T) {
	err := KubernetesApiAccessDisable(context.Background(), smokeTarget(), nil, KubernetesApiAccessOptions{
		Name: "hello",
		Out:  captureOut(),
	})
	if err == nil {
		t.Fatal("expected error when Namespace is empty")
	}
}
