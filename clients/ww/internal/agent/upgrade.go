package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"

	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
)

// UpgradeOptions controls the `ww agent upgrade <name>` flow.
//
// Three tag-resolution modes, evaluated in priority order:
//
//  1. Tag — when non-empty, every container's image tag (harness +
//     each backend) gets pinned to this value. Equivalent to passing
//     --tag X on the CLI.
//
//  2. HarnessTag / BackendTags — fine-grained overrides for one or
//     more containers. Other containers fall through to (3).
//
//  3. CLIVersion — the brewed `ww` binary's own release version,
//     stripped of any leading `v`. The default when (1) and (2) are
//     unset; lets `brew upgrade ww && ww agent upgrade iris` Just
//     Work without re-typing the version.
//
// Validation: --tag is mutually exclusive with --harness-tag /
// --backend-tag. The CLI command enforces this with a Cobra check
// before constructing UpgradeOptions; the Upgrade function trusts
// the caller (the package-level test surface exercises both paths).
type UpgradeOptions struct {
	Name      string
	Namespace string

	// Tag pins every container to this image tag. Mutually exclusive
	// with HarnessTag and BackendTags — caller validates.
	Tag string

	// HarnessTag overrides the harness container's image tag only.
	HarnessTag string

	// BackendTags overrides specific backends' image tags. Map key
	// is the backend name (matching spec.backends[].name); value is
	// the desired tag. Backends not named here keep their current
	// tag unless Tag is set.
	BackendTags map[string]string

	// CLIVersion is the brewed ww's own release version, used as the
	// fallback when neither Tag nor an entry-specific override is
	// set. Threaded from cmd.Version. Empty / "dev" / "unknown"
	// triggers a clear error rather than silently rolling to :latest
	// (#1592 and friends — pinning is load-bearing for reproducibility).
	CLIVersion string

	// Force rolls the deployment even when every target tag already
	// matches the current value. Default behaviour is the no-op
	// fast path: print "already at <tag>" and exit 0.
	Force bool

	// Wait controls whether we block after the patch until the CR's
	// status.phase reads Ready. Timeout bounds the wait. Mirrors the
	// create flow's flag set so the upgrade UX stays familiar.
	Wait    bool
	Timeout time.Duration

	AssumeYes bool
	DryRun    bool
	Out       io.Writer
	In        io.Reader
}

// Upgrade patches the named WitwaveAgent's image tags to the resolved
// target version, then waits for the operator to roll the deployment
// to Ready. Idempotent on the no-op path: when every container's
// current tag already matches the desired one and Force is false,
// returns nil after printing a "no change" message.
//
// Patch shape: a JSON Patch (RFC 6902) replacing /spec with the
// tag-bumped spec. Uses the `patch` verb (not `update`) so the
// agentLifecycle preset's `patch` grant suffices. The operator
// reconciles the change and rolls the Deployment via kubelet's standard
// rollout path — no operator-side coordination required.
func Upgrade(
	ctx context.Context,
	target *k8s.Target,
	cfg *rest.Config,
	opts UpgradeOptions,
) error {
	if opts.Out == nil {
		return fmt.Errorf("UpgradeOptions.Out is required")
	}
	if opts.Name == "" {
		return fmt.Errorf("UpgradeOptions.Name is required")
	}
	if opts.Namespace == "" {
		return fmt.Errorf("UpgradeOptions.Namespace is required")
	}

	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build dynamic client: %w", err)
	}

	cr, err := dyn.Resource(GVR()).Namespace(opts.Namespace).Get(ctx, opts.Name, metav1.GetOptions{})
	if err != nil {
		if apierrors.IsNotFound(err) {
			return fmt.Errorf("WitwaveAgent %q not found in namespace %q — create it first with `ww agent create %s`",
				opts.Name, opts.Namespace, opts.Name)
		}
		return fmt.Errorf("get WitwaveAgent %s/%s: %w", opts.Namespace, opts.Name, err)
	}

	current, err := readCurrentImageTags(cr)
	if err != nil {
		return err
	}

	desired, err := resolveDesiredTags(current, opts)
	if err != nil {
		return err
	}

	transitions := diffTransitions(current, desired)
	if len(transitions) == 0 && !opts.Force {
		fmt.Fprintf(opts.Out, "Agent %s/%s already at the desired image tags — no upgrade needed. Pass --force to roll regardless.\n",
			opts.Namespace, opts.Name)
		return nil
	}

	plan := []k8s.PlanLine{
		{Key: "Action", Value: fmt.Sprintf("upgrade WitwaveAgent %q image tags", opts.Name)},
	}
	if len(transitions) > 0 {
		plan = append(plan, k8s.PlanLine{
			Key:   "Targets",
			Value: formatTransitions(transitions),
		})
	} else {
		// Force path with no actual diff — still rolling on operator request.
		plan = append(plan, k8s.PlanLine{
			Key:   "Targets",
			Value: fmt.Sprintf("(no tag changes — --force will roll the deployment anyway, current = %s)", formatCurrentSnapshot(current)),
		})
	}
	if IsDevVersion(opts.CLIVersion) && opts.Tag == "" && opts.HarnessTag == "" && len(opts.BackendTags) == 0 {
		plan = append(plan, k8s.PlanLine{
			Key:   "Note",
			Value: "dev build — defaulted target tag would be :latest (floating); pass --tag for a pinned upgrade",
		})
	}

	proceed, err := k8s.Confirm(opts.Out, opts.In, target, plan, k8s.PromptOptions{
		AssumeYes: opts.AssumeYes,
		DryRun:    opts.DryRun,
	})
	if err != nil {
		return err
	}
	if !proceed {
		return nil
	}

	// Use a JSON Patch (RFC 6902), not Update, so the agentLifecycle
	// `patch` grant is sufficient: the Agent-Resources canary (milo) is
	// granted `patch` on witwaveagents — not `update` — and `ww agent
	// upgrade` is the charter's intended driver of that grant.
	//
	// `replace` of the whole /spec preserves every spec field. This
	// deliberately avoids an RFC 7396 merge-patch, which REPLACES arrays
	// wholesale rather than merging by element key — a partial
	// spec.backends[] entry would clobber image.repository and admission
	// would reject on "spec.backends[i].image.repository: Required value".
	// Tag bumps are idempotent, so a lost race is harmless: re-run.
	if err := applyDesiredTagsInPlace(cr, desired); err != nil {
		return err
	}
	specVal, found, err := unstructured.NestedFieldNoCopy(cr.Object, "spec")
	if err != nil || !found {
		return fmt.Errorf("read spec of WitwaveAgent %s/%s for patch: %w", opts.Namespace, opts.Name, err)
	}
	patchBytes, err := json.Marshal([]map[string]any{
		{"op": "replace", "path": "/spec", "value": specVal},
	})
	if err != nil {
		return fmt.Errorf("marshal upgrade patch for %s/%s: %w", opts.Namespace, opts.Name, err)
	}
	if _, err := dyn.Resource(GVR()).Namespace(opts.Namespace).Patch(
		ctx, opts.Name, types.JSONPatchType, patchBytes, metav1.PatchOptions{},
	); err != nil {
		return fmt.Errorf("patch WitwaveAgent %s/%s: %w", opts.Namespace, opts.Name, err)
	}
	fmt.Fprintf(opts.Out, "Updated WitwaveAgent %s/%s.\n", opts.Namespace, opts.Name)

	if !opts.Wait {
		fmt.Fprintln(opts.Out, "Skipping rollout wait (--no-wait). Check with `ww agent status "+opts.Name+"`.")
		return nil
	}
	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 5 * time.Minute
	}
	fmt.Fprintf(opts.Out, "Waiting up to %s for the operator to roll the deployment to Ready...\n", timeout)
	k8sClient, err := newKubernetesClient(cfg)
	if err != nil {
		return err
	}
	if err := waitForReady(ctx, dyn, k8sClient, opts.Namespace, opts.Name, timeout, opts.Out); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "\nAgent %s upgraded.\n", opts.Name)
	return nil
}

// imageTagSnapshot captures the current image tags on every container
// in a single CR. Map key for backends is the backend name; harnessTag
// is the singleton.
type imageTagSnapshot struct {
	HarnessTag  string
	BackendTags map[string]string
}

// imageTagTransition is one container's old → new tag pair, used in the
// preflight banner and to build the merge patch.
type imageTagTransition struct {
	Container string // "harness" or "backend:<name>"
	OldTag    string
	NewTag    string
}

// readCurrentImageTags pulls spec.image.tag and each
// spec.backends[].image.tag off an unstructured CR. Returns a clear
// error when the CR is malformed (tag is required to be a string).
func readCurrentImageTags(cr *unstructured.Unstructured) (imageTagSnapshot, error) {
	out := imageTagSnapshot{BackendTags: map[string]string{}}

	tag, _, err := unstructured.NestedString(cr.Object, "spec", "image", "tag")
	if err != nil {
		return out, fmt.Errorf("read spec.image.tag: %w", err)
	}
	out.HarnessTag = tag

	backends, _, err := unstructured.NestedSlice(cr.Object, "spec", "backends")
	if err != nil {
		return out, fmt.Errorf("read spec.backends: %w", err)
	}
	for i, raw := range backends {
		entry, ok := raw.(map[string]interface{})
		if !ok {
			return out, fmt.Errorf("spec.backends[%d] is not an object", i)
		}
		name, _, _ := unstructured.NestedString(entry, "name")
		bTag, _, _ := unstructured.NestedString(entry, "image", "tag")
		if name == "" {
			return out, fmt.Errorf("spec.backends[%d] missing required name", i)
		}
		out.BackendTags[name] = bTag
	}
	return out, nil
}

// resolveDesiredTags applies the priority chain (Tag → HarnessTag /
// BackendTags → CLIVersion) on top of the current snapshot. Returns
// the new desired snapshot. Stripped leading `v` so users can pass
// either `0.11.14` or `v0.11.14` interchangeably and the operator
// always receives the GHCR-published shape.
func resolveDesiredTags(current imageTagSnapshot, opts UpgradeOptions) (imageTagSnapshot, error) {
	defaultTag := strings.TrimPrefix(strings.TrimSpace(opts.Tag), "v")
	if defaultTag == "" {
		// Fall back to CLI version. Empty / "dev" / "unknown" only
		// makes sense if a fine-grained override is set for every
		// container — otherwise the upgrade has no resolved target
		// and we'd silently roll to :latest, which is exactly the
		// reproducibility footgun pinned in #1592.
		if IsDevVersion(opts.CLIVersion) && opts.HarnessTag == "" && len(opts.BackendTags) == 0 {
			return imageTagSnapshot{}, fmt.Errorf(
				"this is a dev build of `ww` (no release version stamped) — pass --tag <version> " +
					"or per-container overrides (--harness-tag / --backend-tag) so the upgrade has a " +
					"concrete target")
		}
		defaultTag = strings.TrimPrefix(strings.TrimSpace(opts.CLIVersion), "v")
	}

	desired := imageTagSnapshot{BackendTags: map[string]string{}}

	// Harness: explicit --harness-tag wins, else --tag, else CLI version.
	switch {
	case strings.TrimSpace(opts.HarnessTag) != "":
		desired.HarnessTag = strings.TrimPrefix(strings.TrimSpace(opts.HarnessTag), "v")
	default:
		desired.HarnessTag = defaultTag
	}

	// Backends: each backend gets either an explicit --backend-tag
	// override or the default. Unknown backend names in BackendTags
	// (no matching entry on the CR) are a hard error — silently
	// ignoring them would let a typo (`--backend-tag clade=...`)
	// produce a no-op upgrade with no diagnostic.
	for name := range current.BackendTags {
		if v, ok := opts.BackendTags[name]; ok {
			desired.BackendTags[name] = strings.TrimPrefix(strings.TrimSpace(v), "v")
		} else {
			desired.BackendTags[name] = defaultTag
		}
	}
	for name := range opts.BackendTags {
		if _, ok := current.BackendTags[name]; !ok {
			return imageTagSnapshot{}, fmt.Errorf(
				"--backend-tag %s=...: no backend named %q on this agent (declared backends: %s)",
				name, name, strings.Join(sortedKeys(current.BackendTags), ", "))
		}
	}
	return desired, nil
}

// diffTransitions builds the per-container old → new list. Excludes
// containers whose tag is unchanged so the banner stays focused.
// Sorted deterministically (harness first, then backends by name) so
// the banner reads the same on every invocation.
func diffTransitions(current, desired imageTagSnapshot) []imageTagTransition {
	var out []imageTagTransition
	if current.HarnessTag != desired.HarnessTag {
		out = append(out, imageTagTransition{
			Container: "harness",
			OldTag:    current.HarnessTag,
			NewTag:    desired.HarnessTag,
		})
	}
	for _, name := range sortedKeys(desired.BackendTags) {
		if current.BackendTags[name] != desired.BackendTags[name] {
			out = append(out, imageTagTransition{
				Container: "backend:" + name,
				OldTag:    current.BackendTags[name],
				NewTag:    desired.BackendTags[name],
			})
		}
	}
	return out
}

// formatTransitions renders the targets-line of the preflight banner.
// One row per container, aligned old → new.
func formatTransitions(t []imageTagTransition) string {
	rows := make([]string, 0, len(t))
	for _, x := range t {
		rows = append(rows, fmt.Sprintf("%s: %s → %s", x.Container, displayTag(x.OldTag), x.NewTag))
	}
	return strings.Join(rows, "\n  ")
}

// formatCurrentSnapshot renders a one-line summary of every container's
// current tag, used on the --force-with-no-diff path so the banner
// still tells the user what they're about to roll.
func formatCurrentSnapshot(current imageTagSnapshot) string {
	parts := []string{fmt.Sprintf("harness=%s", displayTag(current.HarnessTag))}
	for _, name := range sortedKeys(current.BackendTags) {
		parts = append(parts, fmt.Sprintf("%s=%s", name, displayTag(current.BackendTags[name])))
	}
	return strings.Join(parts, ", ")
}

// displayTag turns an empty tag (operator-default territory) into a
// visible token so the banner doesn't render a confusing blank.
func displayTag(tag string) string {
	if tag == "" {
		return "(unset)"
	}
	return tag
}

// applyDesiredTagsInPlace mutates the unstructured CR's
// spec.image.tag and each spec.backends[<i>].image.tag entry to
// match the desired snapshot. Preserves every other field (image
// repository, env, volumes, etc.) so a subsequent Update sends a
// complete, admission-valid object.
//
// Why we don't merge-patch: RFC 7396 replaces arrays wholesale. A
// merge-patch carrying `spec.backends: [{name: claude, image: {tag: X}}]`
// would drop the existing image.repository on every backend, and
// the CRD's required-field validation rejects on Update.
func applyDesiredTagsInPlace(cr *unstructured.Unstructured, desired imageTagSnapshot) error {
	if desired.HarnessTag != "" {
		if err := unstructured.SetNestedField(cr.Object, desired.HarnessTag, "spec", "image", "tag"); err != nil {
			return fmt.Errorf("set spec.image.tag: %w", err)
		}
	}
	backends, _, err := unstructured.NestedSlice(cr.Object, "spec", "backends")
	if err != nil {
		return fmt.Errorf("read spec.backends: %w", err)
	}
	for i, raw := range backends {
		entry, ok := raw.(map[string]interface{})
		if !ok {
			return fmt.Errorf("spec.backends[%d] is not an object", i)
		}
		name, _, _ := unstructured.NestedString(entry, "name")
		tag, hasNew := desired.BackendTags[name]
		if !hasNew || tag == "" {
			continue
		}
		image, _, _ := unstructured.NestedMap(entry, "image")
		if image == nil {
			image = map[string]interface{}{}
		}
		image["tag"] = tag
		entry["image"] = image
		backends[i] = entry
	}
	if err := unstructured.SetNestedSlice(cr.Object, backends, "spec", "backends"); err != nil {
		return fmt.Errorf("set spec.backends: %w", err)
	}
	return nil
}

func sortedKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
