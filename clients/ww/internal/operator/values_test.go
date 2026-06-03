// Tests for MergeValues — the flag→values bridge that backs the
// `-f/--values`, `--set`, and `--set-string` options on
// `ww operator install` / `ww operator upgrade`. The Helm Install /
// Upgrade actions themselves run against a real cluster (see the note in
// helm_test.go), so this file pins the pure merge/precedence/typing
// contract that turns CLI flags into the values map those actions
// consume — the part that is wrong-able without a cluster.
package operator

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMergeValuesEmptyReturnsNonNilMap(t *testing.T) {
	got, err := MergeValues(nil, nil, nil)
	if err != nil {
		t.Fatalf("MergeValues(nil,nil,nil) error = %v", err)
	}
	if got == nil {
		t.Fatal("MergeValues returned a nil map; callers pass it straight to the Helm SDK and want an empty, non-nil map")
	}
	if len(got) != 0 {
		t.Fatalf("expected empty map, got %d keys: %v", len(got), got)
	}
}

func TestMergeValuesSetBuildsNestedMap(t *testing.T) {
	got, err := MergeValues(nil, []string{"agentImagePatchPolicy.enabled=true"}, nil)
	if err != nil {
		t.Fatalf("MergeValues error = %v", err)
	}
	policy, ok := got["agentImagePatchPolicy"].(map[string]interface{})
	if !ok {
		t.Fatalf("agentImagePatchPolicy not a nested map: %#v", got["agentImagePatchPolicy"])
	}
	// strvals coerces a bare "true" to a bool, matching `helm --set`.
	if policy["enabled"] != true {
		t.Errorf("enabled = %#v, want bool true", policy["enabled"])
	}
}

func TestMergeValuesSetStringForcesStringTyping(t *testing.T) {
	// Without --set-string, strvals would coerce "01" to the int 1; the
	// whole point of --set-string is to preserve the literal string.
	got, err := MergeValues(nil, nil, []string{"image.tag=01"})
	if err != nil {
		t.Fatalf("MergeValues error = %v", err)
	}
	img, ok := got["image"].(map[string]interface{})
	if !ok {
		t.Fatalf("image not a nested map: %#v", got["image"])
	}
	if img["tag"] != "01" {
		t.Errorf("tag = %#v, want string \"01\"", img["tag"])
	}
}

func TestMergeValuesFilePlusSetPrecedence(t *testing.T) {
	// A values file enables the policy with one SA; --set then overrides a
	// scalar in the same tree. Confirms file values load AND that --set
	// wins over the file (helm's documented precedence).
	dir := t.TempDir()
	vf := filepath.Join(dir, "operator-values.yaml")
	const body = `agentImagePatchPolicy:
  enabled: false
  constrainedServiceAccounts:
    - system:serviceaccount:witwave-self:milo
`
	if err := os.WriteFile(vf, []byte(body), 0o600); err != nil {
		t.Fatalf("write temp values file: %v", err)
	}

	got, err := MergeValues([]string{vf}, []string{"agentImagePatchPolicy.enabled=true"}, nil)
	if err != nil {
		t.Fatalf("MergeValues error = %v", err)
	}
	policy, ok := got["agentImagePatchPolicy"].(map[string]interface{})
	if !ok {
		t.Fatalf("agentImagePatchPolicy not a nested map: %#v", got["agentImagePatchPolicy"])
	}
	if policy["enabled"] != true {
		t.Errorf("enabled = %#v, want true (--set must override the file)", policy["enabled"])
	}
	sas, ok := policy["constrainedServiceAccounts"].([]interface{})
	if !ok || len(sas) != 1 || sas[0] != "system:serviceaccount:witwave-self:milo" {
		t.Errorf("constrainedServiceAccounts = %#v, want [system:serviceaccount:witwave-self:milo] from the file", policy["constrainedServiceAccounts"])
	}
}

func TestMergeValuesMalformedSetErrors(t *testing.T) {
	// strvals rejects a bare token with no '='; surface that as an error
	// rather than silently dropping the override.
	if _, err := MergeValues(nil, []string{"justakeynovalue"}, nil); err == nil {
		t.Fatal("expected error for malformed --set, got nil")
	}
}

func TestMergeValuesMissingFileErrors(t *testing.T) {
	if _, err := MergeValues([]string{filepath.Join(t.TempDir(), "does-not-exist.yaml")}, nil, nil); err == nil {
		t.Fatal("expected error for missing values file, got nil")
	}
}

func TestDescribeValues(t *testing.T) {
	cases := []struct {
		name string
		in   map[string]interface{}
		want string
	}{
		{"empty", map[string]interface{}{}, "none"},
		{"nil", nil, "none"},
		{
			"single",
			map[string]interface{}{"agentImagePatchPolicy": map[string]interface{}{"enabled": true}},
			"1 override (agentImagePatchPolicy)",
		},
		{
			"multiple sorted",
			map[string]interface{}{"rbac": map[string]interface{}{}, "agentImagePatchPolicy": map[string]interface{}{}},
			"2 overrides (agentImagePatchPolicy, rbac)",
		},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			if got := describeValues(tc.in); got != tc.want {
				t.Errorf("describeValues(%v) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}

// TestDescribeValuesOmitsValues pins the contract that the banner summary
// lists override KEYS only, never their values — so a future overlay that
// carries a token/password can't leak it to stdout via the preflight
// banner.
func TestDescribeValuesOmitsValues(t *testing.T) {
	got := describeValues(map[string]interface{}{"someSecret": "s3cr3t-token-value"})
	if got == "" || strings.Contains(got, "s3cr3t") {
		t.Errorf("describeValues leaked a value: %q", got)
	}
}
