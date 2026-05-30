// Tests for the pure-helper functions in list.go that the `ww agent
// list` renderer composes over k8s data. Mirrors the table-driven
// shape used in internal/operator/events_test.go (TestAgeOrExact)
// and internal/operator/snapshot_test.go (TestFormatTime); the
// k8s-backed ListAgents path is exercised against a real cluster,
// so this file just pins the pure age-formatter contract so a
// future tweak to the duration thresholds (or the unit suffix
// strings) fails loudly rather than silently shifting the column
// content for every cluster operator running `ww agent list`.
package agent

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

// TestFormatAge pins the `Age` column formatter. Contract:
//   - zero time → "-" sentinel (the CR somehow had no creation TS).
//   - d <  1m → "<n>s" (integer seconds, truncated).
//   - d <  1h → "<n>m" (integer minutes, truncated).
//   - d < 24h → "<n>h" (integer hours, truncated).
//   - d ≥ 24h → "<n>d" (integer days, truncated).
//
// Future-clock-skew (now - t < 0) currently flows through the
// "<n>s" branch as a negative integer; callers are k8s-server
// timestamps so the case is exotic but allowed — pinning behaviour
// rather than asserting a specific sign here so a CR scheduled
// fractions of a second in the future doesn't surprise the table
// renderer (the column truncates to "0s" for the typical case).
//
// Drift here would change every `ww agent list` row's Age column.
func TestFormatAge(t *testing.T) {
	now := time.Now()

	cases := []struct {
		name string
		in   time.Time
		want string
	}{
		{"zero time returns dash sentinel", time.Time{}, "-"},
		{"just-now (30s) renders as seconds", now.Add(-30 * time.Second), "30s"},
		{"sub-minute boundary (59s) stays in seconds", now.Add(-59 * time.Second), "59s"},
		{"exactly one minute renders as minutes", now.Add(-1 * time.Minute), "1m"},
		{"mid-hour (45m) renders as minutes", now.Add(-45 * time.Minute), "45m"},
		{"sub-hour boundary (59m) stays in minutes", now.Add(-59 * time.Minute), "59m"},
		{"exactly one hour renders as hours", now.Add(-1 * time.Hour), "1h"},
		{"mid-day (12h) renders as hours", now.Add(-12 * time.Hour), "12h"},
		{"sub-day boundary (23h) stays in hours", now.Add(-23 * time.Hour), "23h"},
		{"exactly one day renders as days", now.Add(-24 * time.Hour), "1d"},
		{"multi-day (7d) renders as days", now.Add(-7 * 24 * time.Hour), "7d"},
		{"long-lived agent (90d) still renders as days", now.Add(-90 * 24 * time.Hour), "90d"},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			got := FormatAge(tc.in)
			if got != tc.want {
				t.Errorf("FormatAge(%v) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}

func TestAgentSummaryDetectsExplicitlyDisabledAgent(t *testing.T) {
	cr := &unstructured.Unstructured{Object: map[string]interface{}{
		"spec": map[string]interface{}{
			"enabled": false,
		},
		"status": map[string]interface{}{
			"phase":         "Ready",
			"readyReplicas": int64(1),
		},
	}}

	got := agentSummary(cr)
	if !got.Disabled {
		t.Fatalf("Disabled = false, want true")
	}
	if got.Phase != "Ready" || got.Ready != 1 {
		t.Fatalf("status fields changed unexpectedly: %+v", got)
	}
}

func TestEnabledDisplay(t *testing.T) {
	if got := enabledDisplay(AgentSummary{}); got != "true" {
		t.Fatalf("enabledDisplay(default) = %q, want true", got)
	}
	if got := enabledDisplay(AgentSummary{Disabled: true}); got != "false" {
		t.Fatalf("enabledDisplay(disabled) = %q, want false", got)
	}
}

func TestListRendererShowsEnabledColumn(t *testing.T) {
	var out strings.Builder
	summaries := []AgentSummary{
		{Namespace: "witwave-self", Name: "zora", Phase: "Ready", Ready: 1},
		{Namespace: "witwave-self", Name: "evan", Phase: "Ready", Ready: 1, Disabled: true},
	}
	if err := renderList(&out, summaries); err != nil {
		t.Fatalf("renderList returned error: %v", err)
	}
	got := out.String()
	for _, want := range []string{"ENABLED", "zora", "true", "evan", "false"} {
		if !strings.Contains(got, want) {
			t.Fatalf("rendered list missing %q:\n%s", want, got)
		}
	}
}

// TestAgentSummaryParsesImages pins the harness + per-backend image
// extraction that feeds the VERSION column, --json, and the upgrade
// path's "what version is this agent on?" read.
func TestAgentSummaryParsesImages(t *testing.T) {
	cr := &unstructured.Unstructured{Object: map[string]any{
		"spec": map[string]any{
			"image": map[string]any{"repository": "ghcr.io/witwave-ai/images/harness", "tag": "0.33.6"},
			"backends": []any{
				map[string]any{"name": "claude", "image": map[string]any{"repository": "ghcr.io/witwave-ai/images/claude", "tag": "0.33.6"}},
			},
		},
		"status": map[string]any{"phase": "Ready", "readyReplicas": int64(1)},
	}}

	got := agentSummary(cr)
	if got.Harness.Tag != "0.33.6" || got.Harness.Version() != "0.33.6" {
		t.Fatalf("harness image = %+v, want tag 0.33.6", got.Harness)
	}
	if len(got.Backends) != 1 || got.Backends[0] != "claude" {
		t.Fatalf("Backends = %v, want [claude]", got.Backends)
	}
	if len(got.BackendImages) != 1 || got.BackendImages[0].Name != "claude" || got.BackendImages[0].Image.Tag != "0.33.6" {
		t.Fatalf("BackendImages = %+v, want one claude@0.33.6", got.BackendImages)
	}
}

func TestAgentImageVersionAndRef(t *testing.T) {
	cases := []struct {
		name        string
		img         AgentImage
		wantVersion string
		wantRef     string
	}{
		{"tag", AgentImage{Repository: "r", Tag: "0.33.6"}, "0.33.6", "r:0.33.6"},
		{"digest", AgentImage{Repository: "r", Digest: "sha256:abc"}, "sha256:abc", "r@sha256:abc"},
		{"repo only", AgentImage{Repository: "r"}, "-", "r"},
		{"empty", AgentImage{}, "-", "-"},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.img.Version(); got != tc.wantVersion {
				t.Errorf("Version() = %q, want %q", got, tc.wantVersion)
			}
			if got := tc.img.Ref(); got != tc.wantRef {
				t.Errorf("Ref() = %q, want %q", got, tc.wantRef)
			}
		})
	}
}

func TestListRendererShowsVersionColumn(t *testing.T) {
	var out strings.Builder
	summaries := []AgentSummary{
		{Namespace: "witwave-self", Name: "zora", Phase: "Ready", Ready: 1, Harness: AgentImage{Repository: "h", Tag: "0.33.6"}},
	}
	if err := renderList(&out, summaries); err != nil {
		t.Fatalf("renderList returned error: %v", err)
	}
	got := out.String()
	for _, want := range []string{"VERSION", "0.33.6"} {
		if !strings.Contains(got, want) {
			t.Fatalf("rendered list missing %q:\n%s", want, got)
		}
	}
}

// TestListJSONIncludesVersions pins the --json contract that roster-audit,
// platform-health, and the team-upgrade skill parse.
func TestListJSONIncludesVersions(t *testing.T) {
	var out strings.Builder
	summaries := []AgentSummary{{
		Namespace:     "witwave-self",
		Name:          "iris",
		Phase:         "Ready",
		Ready:         1,
		Harness:       AgentImage{Repository: "ghcr.io/h", Tag: "0.33.6"},
		BackendImages: []BackendSummary{{Name: "claude", Image: AgentImage{Repository: "ghcr.io/c", Tag: "0.33.6"}}},
	}}
	if err := renderListJSON(&out, summaries); err != nil {
		t.Fatalf("renderListJSON returned error: %v", err)
	}
	var decoded []AgentJSON
	if err := json.Unmarshal([]byte(out.String()), &decoded); err != nil {
		t.Fatalf("output is not valid JSON: %v\n%s", err, out.String())
	}
	if len(decoded) != 1 {
		t.Fatalf("want 1 agent, got %d", len(decoded))
	}
	a := decoded[0]
	if a.Name != "iris" || !a.Enabled || a.Harness.Tag != "0.33.6" {
		t.Fatalf("unexpected agent json: %+v", a)
	}
	if len(a.Backends) != 1 || a.Backends[0].Image.Tag != "0.33.6" {
		t.Fatalf("backends json wrong: %+v", a.Backends)
	}
}
