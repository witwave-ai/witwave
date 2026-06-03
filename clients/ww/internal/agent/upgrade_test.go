package agent

import (
	"os"
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

// ---------------------------------------------------------------------------
// resolveDesiredTags — priority chain (Tag → Harness/Backend → CLIVersion)
// ---------------------------------------------------------------------------

func TestResolveDesiredTags_TagWinsOverEverything(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13"},
	}
	got, err := resolveDesiredTags(current, UpgradeOptions{
		Tag:        "0.11.14",
		CLIVersion: "0.11.10", // should be ignored
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.HarnessTag != "0.11.14" {
		t.Errorf("HarnessTag = %q; want 0.11.14", got.HarnessTag)
	}
	if got.BackendTags["claude"] != "0.11.14" {
		t.Errorf("BackendTags[claude] = %q; want 0.11.14", got.BackendTags["claude"])
	}
}

func TestResolveDesiredTags_StripsLeadingV(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13"},
	}
	got, err := resolveDesiredTags(current, UpgradeOptions{Tag: "v0.11.14"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.HarnessTag != "0.11.14" {
		t.Errorf("HarnessTag = %q; want 0.11.14 (leading v stripped)", got.HarnessTag)
	}
}

func TestResolveDesiredTags_HarnessTagOverridesBackendsTakeDefault(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13", "codex": "0.11.13"},
	}
	got, err := resolveDesiredTags(current, UpgradeOptions{
		HarnessTag: "0.11.14",
		CLIVersion: "0.11.13", // backends fall through to this
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.HarnessTag != "0.11.14" {
		t.Errorf("HarnessTag = %q; want 0.11.14", got.HarnessTag)
	}
	if got.BackendTags["claude"] != "0.11.13" {
		t.Errorf("BackendTags[claude] = %q; want 0.11.13 (CLI version)", got.BackendTags["claude"])
	}
}

func TestResolveDesiredTags_BackendTagPerBackend(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13", "codex": "0.11.13"},
	}
	got, err := resolveDesiredTags(current, UpgradeOptions{
		BackendTags: map[string]string{"claude": "0.11.14"},
		CLIVersion:  "0.11.13",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.BackendTags["claude"] != "0.11.14" {
		t.Errorf("explicit backend override didn't apply; got %q", got.BackendTags["claude"])
	}
	if got.BackendTags["codex"] != "0.11.13" {
		t.Errorf("non-overridden backend should fall through to CLI version; got %q", got.BackendTags["codex"])
	}
}

func TestResolveDesiredTags_UnknownBackendNameRejects(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13"},
	}
	_, err := resolveDesiredTags(current, UpgradeOptions{
		BackendTags: map[string]string{"clade": "0.11.14"}, // typo
		CLIVersion:  "0.11.14",
	})
	if err == nil {
		t.Fatal("expected error for unknown backend name")
	}
	if !strings.Contains(err.Error(), "no backend named") {
		t.Errorf("error doesn't mention the unknown backend name: %q", err)
	}
}

func TestResolveDesiredTags_DevBuildWithoutOverridesRejects(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13"},
	}
	_, err := resolveDesiredTags(current, UpgradeOptions{CLIVersion: "dev"})
	if err == nil {
		t.Fatal("dev build with no --tag / --harness-tag / --backend-tag should reject")
	}
	if !strings.Contains(err.Error(), "dev build") {
		t.Errorf("error doesn't mention the dev build path: %q", err)
	}
}

// ---------------------------------------------------------------------------
// diffTransitions — only changed containers in the banner
// ---------------------------------------------------------------------------

func TestDiffTransitions_SkipsUnchanged(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.13",
		BackendTags: map[string]string{"claude": "0.11.13", "codex": "0.11.13"},
	}
	desired := imageTagSnapshot{
		HarnessTag:  "0.11.14",
		BackendTags: map[string]string{"claude": "0.11.13", "codex": "0.11.14"},
	}
	got := diffTransitions(current, desired)
	if len(got) != 2 {
		t.Fatalf("expected 2 transitions (harness + codex); got %d: %+v", len(got), got)
	}
	// Sorted: harness first.
	if got[0].Container != "harness" {
		t.Errorf("transitions[0].Container = %q; want harness", got[0].Container)
	}
	if got[1].Container != "backend:codex" {
		t.Errorf("transitions[1].Container = %q; want backend:codex", got[1].Container)
	}
}

func TestDiffTransitions_AllUnchangedReturnsEmpty(t *testing.T) {
	current := imageTagSnapshot{
		HarnessTag:  "0.11.14",
		BackendTags: map[string]string{"claude": "0.11.14"},
	}
	got := diffTransitions(current, current)
	if len(got) != 0 {
		t.Errorf("expected zero transitions for identical snapshots; got %+v", got)
	}
}

// ---------------------------------------------------------------------------
// applyDesiredTagsInPlace — mutates the unstructured CR; preserves
// every field the merge-patch path was clobbering
// ---------------------------------------------------------------------------

func TestApplyDesiredTagsInPlace_PreservesImageRepository(t *testing.T) {
	// Mirror the shape of a real CR: harness image with a repository,
	// one backend likewise. The merge-patch implementation dropped
	// these on Update; this test pins the regression.
	cr := &unstructured.Unstructured{Object: map[string]interface{}{
		"spec": map[string]interface{}{
			"image": map[string]interface{}{
				"repository": "ghcr.io/witwave-ai/images/harness",
				"tag":        "0.11.14",
			},
			"backends": []interface{}{
				map[string]interface{}{
					"name": "claude",
					"image": map[string]interface{}{
						"repository": "ghcr.io/witwave-ai/images/claude",
						"tag":        "0.11.14",
					},
				},
			},
		},
	}}
	desired := imageTagSnapshot{
		HarnessTag:  "0.11.15",
		BackendTags: map[string]string{"claude": "0.11.15"},
	}
	if err := applyDesiredTagsInPlace(cr, desired); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	repo, _, _ := unstructured.NestedString(cr.Object, "spec", "image", "repository")
	if repo != "ghcr.io/witwave-ai/images/harness" {
		t.Errorf("harness image.repository lost: got %q", repo)
	}
	tag, _, _ := unstructured.NestedString(cr.Object, "spec", "image", "tag")
	if tag != "0.11.15" {
		t.Errorf("harness image.tag = %q; want 0.11.15", tag)
	}

	backends, _, _ := unstructured.NestedSlice(cr.Object, "spec", "backends")
	if len(backends) != 1 {
		t.Fatalf("backends count changed: got %d", len(backends))
	}
	be := backends[0].(map[string]interface{})
	beImg := be["image"].(map[string]interface{})
	if beImg["repository"] != "ghcr.io/witwave-ai/images/claude" {
		t.Errorf("backend image.repository lost: got %v", beImg["repository"])
	}
	if beImg["tag"] != "0.11.15" {
		t.Errorf("backend image.tag = %v; want 0.11.15", beImg["tag"])
	}
}

func TestApplyDesiredTagsInPlace_SkipsBackendsNotInDesired(t *testing.T) {
	// claude in desired, codex absent — codex should keep its tag
	// untouched, no error on the unmentioned backend.
	cr := &unstructured.Unstructured{Object: map[string]interface{}{
		"spec": map[string]interface{}{
			"backends": []interface{}{
				map[string]interface{}{
					"name":  "claude",
					"image": map[string]interface{}{"repository": "r", "tag": "0.11.14"},
				},
				map[string]interface{}{
					"name":  "codex",
					"image": map[string]interface{}{"repository": "r2", "tag": "0.11.14"},
				},
			},
		},
	}}
	desired := imageTagSnapshot{BackendTags: map[string]string{"claude": "0.11.15"}}
	if err := applyDesiredTagsInPlace(cr, desired); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	backends, _, _ := unstructured.NestedSlice(cr.Object, "spec", "backends")
	codex := backends[1].(map[string]interface{})
	codexImg := codex["image"].(map[string]interface{})
	if codexImg["tag"] != "0.11.14" {
		t.Errorf("untouched backend's tag changed: got %v", codexImg["tag"])
	}
}

func TestApplyDesiredTagsInPlace_HarnessOnlyLeavesBackendsAlone(t *testing.T) {
	cr := &unstructured.Unstructured{Object: map[string]interface{}{
		"spec": map[string]interface{}{
			"image": map[string]interface{}{"repository": "r", "tag": "0.11.14"},
			"backends": []interface{}{
				map[string]interface{}{
					"name":  "claude",
					"image": map[string]interface{}{"repository": "r2", "tag": "0.11.14"},
				},
			},
		},
	}}
	desired := imageTagSnapshot{HarnessTag: "0.11.15"}
	if err := applyDesiredTagsInPlace(cr, desired); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	tag, _, _ := unstructured.NestedString(cr.Object, "spec", "image", "tag")
	if tag != "0.11.15" {
		t.Errorf("harness tag = %q; want 0.11.15", tag)
	}
	backends, _, _ := unstructured.NestedSlice(cr.Object, "spec", "backends")
	be := backends[0].(map[string]interface{})
	beImg := be["image"].(map[string]interface{})
	if beImg["tag"] != "0.11.14" {
		t.Errorf("backend tag = %v; want untouched 0.11.14", beImg["tag"])
	}
}

// ---------------------------------------------------------------------------
// Upgrade mutation verb — patch, never update
// ---------------------------------------------------------------------------

// TestUpgradeUsesPatchNotUpdate guards that `ww agent upgrade` mutates the
// WitwaveAgent with the patch verb (a JSON Patch), not update. The
// agentLifecycle RBAC preset grants the Agent-Resources canary (milo)
// `patch` on witwaveagents — not `update` — so a regression to a full
// dyn...Update() reintroduces "SA milo cannot update witwaveagents" and
// silently breaks Milo's autonomous team-upgrade (the 2026-06-02 incident).
//
// Source-level because Upgrade() builds its own dynamic client from
// *rest.Config and is not fake-injectable without a wider refactor.
func TestUpgradeUsesPatchNotUpdate(t *testing.T) {
	src, err := os.ReadFile("upgrade.go")
	if err != nil {
		t.Fatalf("read upgrade.go: %v", err)
	}
	s := string(src)

	if !strings.Contains(s, "types.JSONPatchType") {
		t.Error("ww agent upgrade must mutate the WitwaveAgent with a JSON Patch " +
			"(types.JSONPatchType) so the agentLifecycle `patch` grant suffices")
	}
	if !strings.Contains(s, ").Patch(") {
		t.Error("ww agent upgrade must call .Patch() on the witwaveagent resource, not .Update()")
	}
	if strings.Contains(s, "Namespace(opts.Namespace).Update(") {
		t.Error("ww agent upgrade must NOT Update() the witwaveagent — milo's " +
			"agentLifecycle RBAC grants `patch`, not `update`")
	}
}
