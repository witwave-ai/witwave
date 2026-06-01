// Tests for the pure-helper functions in status.go that the `ww agent
// status` renderer composes over k8s data. Mirrors the table-driven
// shape in list_test.go (TestListJSONIncludesVersions); the k8s-backed
// Status path is exercised against a real cluster, so this file pins
// the pure renderStatusJSON contract that roster-audit and the
// team-upgrade skill parse — generation / observedGeneration are the
// load-bearing fields an upgrade reads to confirm the operator has
// observed the new spec before declaring readiness.
package agent

import (
	"encoding/json"
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

// TestStatusJSONIncludesGenerations pins the `ww agent status <name>
// --json` contract. Adds metadata.generation + status.observedGeneration
// on top of the list JSON shape so an upgrade can confirm the operator
// has observed the new spec (observedGeneration >= generation) before
// checking readiness. Mirrors TestListJSONIncludesVersions in list_test.go.
func TestStatusJSONIncludesGenerations(t *testing.T) {
	cr := &unstructured.Unstructured{Object: map[string]any{
		"metadata": map[string]any{
			"name":       "iris",
			"namespace":  "witwave-self",
			"generation": int64(7),
		},
		"spec": map[string]any{
			"image": map[string]any{"repository": "ghcr.io/witwave-ai/images/harness", "tag": "0.35.0"},
			"backends": []any{
				map[string]any{
					"name":  "claude",
					"image": map[string]any{"repository": "ghcr.io/witwave-ai/images/claude", "tag": "0.35.0"},
				},
			},
		},
		"status": map[string]any{
			"phase":              "Ready",
			"readyReplicas":      int64(1),
			"observedGeneration": int64(7),
			"message":            "ok",
		},
	}}

	var out strings.Builder
	if err := renderStatusJSON(&out, cr); err != nil {
		t.Fatalf("renderStatusJSON returned error: %v", err)
	}

	var sj StatusJSON
	if err := json.Unmarshal([]byte(out.String()), &sj); err != nil {
		t.Fatalf("output is not valid JSON: %v\n%s", err, out.String())
	}

	if sj.Name != "iris" || sj.Namespace != "witwave-self" {
		t.Fatalf("metadata wrong: name=%q namespace=%q", sj.Name, sj.Namespace)
	}
	if sj.Phase != "Ready" || sj.Ready != 1 {
		t.Fatalf("status fields wrong: phase=%q ready=%d", sj.Phase, sj.Ready)
	}
	if !sj.Enabled {
		t.Fatalf("Enabled = false, want true (no spec.enabled=false set)")
	}
	if sj.Generation != 7 || sj.ObservedGeneration != 7 {
		t.Fatalf("generation/observedGeneration wrong: gen=%d obs=%d, want 7/7",
			sj.Generation, sj.ObservedGeneration)
	}
	if sj.Harness.Tag != "0.35.0" {
		t.Fatalf("harness tag wrong: %+v", sj.Harness)
	}
	if len(sj.Backends) != 1 || sj.Backends[0].Image.Tag != "0.35.0" || sj.Backends[0].Name != "claude" {
		t.Fatalf("backends wrong: %+v", sj.Backends)
	}
	if sj.Message != "ok" {
		t.Fatalf("message wrong: %q", sj.Message)
	}
}

// TestStatusJSONReconcilePending covers the in-flight-upgrade case the
// team-upgrade skill uses to decide "wait, don't declare done":
// status.observedGeneration < metadata.generation. The poller must
// see the older observedGeneration in JSON so it can wait for
// reconciliation to catch up before checking readiness.
func TestStatusJSONReconcilePending(t *testing.T) {
	cr := &unstructured.Unstructured{Object: map[string]any{
		"metadata": map[string]any{
			"name":       "milo",
			"namespace":  "witwave-self",
			"generation": int64(9),
		},
		"spec": map[string]any{
			"image": map[string]any{"repository": "ghcr.io/witwave-ai/images/harness", "tag": "0.35.0"},
		},
		"status": map[string]any{
			"phase":              "Reconciling",
			"observedGeneration": int64(8),
		},
	}}

	var out strings.Builder
	if err := renderStatusJSON(&out, cr); err != nil {
		t.Fatalf("renderStatusJSON returned error: %v", err)
	}

	var sj StatusJSON
	if err := json.Unmarshal([]byte(out.String()), &sj); err != nil {
		t.Fatalf("output is not valid JSON: %v\n%s", err, out.String())
	}
	if sj.Generation != 9 || sj.ObservedGeneration != 8 {
		t.Fatalf("generation/observedGeneration wrong: gen=%d obs=%d, want 9/8",
			sj.Generation, sj.ObservedGeneration)
	}
	if sj.ObservedGeneration >= sj.Generation {
		t.Fatalf("observedGeneration >= generation — the JSON should surface "+
			"the in-flight-reconcile case so the poller knows to wait: %+v", sj)
	}
	if sj.Backends == nil {
		t.Fatalf("Backends must be non-nil for stable JSON consumers; got nil")
	}
}
