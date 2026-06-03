package operator

import (
	"fmt"
	"sort"
	"strings"

	"helm.sh/helm/v3/pkg/cli"
	"helm.sh/helm/v3/pkg/cli/values"
	"helm.sh/helm/v3/pkg/getter"
)

// MergeValues turns the `-f/--values`, `--set`, and `--set-string` flag
// inputs that `ww operator install` / `ww operator upgrade` accept into
// the single map[string]interface{} that HelmClient.Install / .Upgrade
// hand to the Helm SDK (InstallOptions.Values / UpgradeOptions.Values).
//
// It delegates to Helm's own values.Options so ww's precedence and type
// coercion are byte-for-byte identical to `helm install -f … --set …`:
//   - later --values files override earlier ones;
//   - --set / --set-string override --values;
//   - --set-string forces string typing (so e.g. an image tag "01" or a
//     numeric-looking SA suffix is not silently coerced to an int).
//
// getter.All(cli.New()) wires the standard local-file + http(s) getters
// (and honours HELM_* env) exactly as the helm CLI does. Returns a
// non-nil empty map when no inputs are supplied so callers can pass the
// result straight through to the Helm action without a nil check.
func MergeValues(valueFiles, setValues, setStringValues []string) (map[string]interface{}, error) {
	opts := values.Options{
		ValueFiles:   valueFiles,
		Values:       setValues,
		StringValues: setStringValues,
	}
	merged, err := opts.MergeValues(getter.All(cli.New()))
	if err != nil {
		return nil, fmt.Errorf("merge chart values: %w", err)
	}
	if merged == nil {
		merged = map[string]interface{}{}
	}
	return merged, nil
}

// describeValues renders a one-line summary of a merged values map for
// the install/upgrade preflight banner (KC-4) — e.g.
// "1 override (agentImagePatchPolicy)". Only the top-level keys are
// listed (the banner is a heads-up, not a values dump); the full overlay
// is whatever the caller passed via -f/--set. Values themselves are not
// printed so a future credential-bearing override can't leak to stdout.
func describeValues(v map[string]interface{}) string {
	if len(v) == 0 {
		return "none"
	}
	keys := make([]string, 0, len(v))
	for k := range v {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	noun := "override"
	if len(keys) != 1 {
		noun = "overrides"
	}
	return fmt.Sprintf("%d %s (%s)", len(keys), noun, strings.Join(keys, ", "))
}
