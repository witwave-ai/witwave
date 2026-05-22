package agent

import (
	"context"
	"fmt"
	"io"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/rest"
)

// orDefault returns s when non-empty, else def. Tiny helper to keep
// banner formatting compact.
func orDefault(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

// describeCredSource produces a human-readable summary of how the
// credentials were resolved, suitable for the preflight banner.
func describeCredSource(auth GitAuthResolver, resolvedSecret string) string {
	switch auth.Mode {
	case GitAuthNone:
		return "<none — public repo>"
	case GitAuthExistingSecret:
		return fmt.Sprintf("existing Secret %q (verified present)", resolvedSecret)
	case GitAuthFromGH:
		return fmt.Sprintf("minted Secret %q from `gh auth token`", resolvedSecret)
	case GitAuthFromEnv:
		return fmt.Sprintf("minted Secret %q from $%s", resolvedSecret, auth.EnvVar)
	}
	return fmt.Sprintf("<unknown mode %d>", auth.Mode)
}

// pickBackendsForMappings returns the set of backend names that should
// receive their per-backend `.<backend>/` gitMapping. Every known
// backend (echo/claude/openai/gemini) gets one if it's present on the
// agent; unknown backends are surfaced so the user isn't silently
// left with no sync coverage on a custom backend.
func pickBackendsForMappings(backends []map[string]interface{}) ([]string, error) {
	var out []string
	for _, b := range backends {
		name, _ := b["name"].(string)
		if name == "" {
			return nil, fmt.Errorf("backend entry missing required `name` field: %+v", b)
		}
		out = append(out, name)
	}
	return out, nil
}

// GitAddOptions collects the runtime inputs for `ww agent git add`.
type GitAddOptions struct {
	Agent     string
	Namespace string

	// Repo — accepted shorthands match `ww agent scaffold --repo`
	// (owner/repo, host/owner/repo, full URL, git@host:owner/repo).
	Repo string

	// RepoPath — path within the repo that holds this agent's config
	// directory, relative to the repo root. Default:
	// `.agents/<agent>/` or `.agents/<group>/<agent>/` when Group is set.
	// Explicit path wins; this flag is the escape hatch for repos that
	// don't follow the ww scaffold convention.
	RepoPath string

	// Group — only used when RepoPath is empty, to derive the default
	// path from the `.agents/<group>/<agent>/` convention.
	Group string

	// Branch / Period — mirror the gitSyncs[] field shapes.
	// Empty Branch → operator defaults to HEAD (the remote's default).
	// Empty Period → DefaultGitPeriod ("60s").
	Branch string
	Period string

	// SyncName is the gitSyncs[].name value. Default DefaultGitSyncName
	// ("witwave"); override when wiring multiple repos to one agent.
	SyncName string

	// Auth carries the user's --auth-* flag choice. Empty zero-value
	// resolves to GitAuthNone (public repo, no Secret reference).
	Auth GitAuthResolver

	// AssumeYes + DryRun mirror the patterns on every other mutating
	// `ww agent` verb.
	AssumeYes bool
	DryRun    bool

	Out io.Writer
	In  io.Reader
}

// GitAdd attaches (or re-attaches, idempotent) a gitSync sidecar +
// mappings to an existing WitwaveAgent. Flow:
//
//  1. Fetch the CR. Refuse if the agent doesn't exist.
//  2. Parse --repo shorthand, resolve auth (--auth-secret / --auth-
//     from-gh / --secret-from-env) to a K8s Secret name.
//  3. Derive the repo-relative path if --repo-path wasn't supplied.
//  4. Patch the CR in-place:
//     - Replace (or add) the gitSyncs[] entry with matching name.
//     - Replace the harness-level gitMappings[] tied to that sync.
//     - Replace the per-backend gitMappings[] tied to that sync on
//     each backend.
//  5. Update the CR via the dynamic client.
//
// Idempotence: if a gitSyncs[] entry with the same SyncName already
// exists, this overwrites it (same sidecar name = same mount behaviour,
// so the user's intent is "update"). The operator's reconciler is
// idempotent at the Deployment/Service layer.
func GitAdd(
	ctx context.Context,
	cfg *rest.Config,
	opts GitAddOptions,
) error {
	if opts.Out == nil {
		return fmt.Errorf("GitAddOptions.Out is required")
	}
	if err := validateGitAddOptions(&opts); err != nil {
		return err
	}
	ref, err := parseRepoRef(opts.Repo)
	if err != nil {
		return err
	}

	// Apply conventional defaults. Sync name derives from the repo's
	// last path segment (sanitised to DNS-1123) when not overridden, so
	// `ww agent git add hello --repo owner/my-repo` produces
	// `/git/my-repo/` on the pod filesystem — matches what the user
	// typed rather than a generic product-name label.
	syncName := opts.SyncName
	if syncName == "" {
		syncName = DeriveGitSyncName(opts.Repo)
	}
	period := opts.Period
	if period == "" {
		period = DefaultGitPeriod
	}
	repoPath := opts.RepoPath
	if repoPath == "" {
		repoPath = agentRepoRoot(opts.Agent, opts.Group)
	}

	// Build typed clients. Two clients — dynamic for the CR, typed for
	// Secret operations — the latter has a friendlier API for Secret
	// CRUD than fighting unstructured.
	dyn, err := newDynamicClient(cfg)
	if err != nil {
		return err
	}
	k8sClient, err := newKubernetesClient(cfg)
	if err != nil {
		return err
	}

	// Fetch current CR early so any schema mismatch surfaces before
	// we mint a K8s Secret we don't end up using.
	cr, err := fetchAgentCR(ctx, dyn, opts.Namespace, opts.Agent)
	if err != nil {
		return err
	}

	// Resolve the credential Secret. For --auth-from-gh / --auth-from-
	// env this mints / updates an actual K8s Secret; for --auth-secret
	// it merely verifies the existing Secret is present.
	secretName, err := opts.Auth.resolve(ctx, k8sClient, opts.Namespace, opts.Agent)
	if err != nil {
		return err
	}

	// Plan + banner.
	plan := []string{
		fmt.Sprintf("gitSync  %q  repo=%s  ref=%s  period=%s",
			syncName, ref.Display, orDefault(opts.Branch, "<remote default>"), period),
		fmt.Sprintf("  credentials: %s", describeCredSource(opts.Auth, secretName)),
		fmt.Sprintf("mapping   %s → /home/agent/.witwave/ (harness)", repoPath+"/.witwave/"),
	}
	backends, err := readBackends(cr)
	if err != nil {
		return err
	}
	backendsCovered, err := pickBackendsForMappings(backends)
	if err != nil {
		return err
	}
	for _, b := range backendsCovered {
		plan = append(plan, fmt.Sprintf("mapping   %s → /home/agent/.%s/ (backend)", repoPath+"/."+b+"/", b))
	}

	fmt.Fprintf(opts.Out, "\nAction:    attach gitSync %q to WitwaveAgent %q in %s\n",
		syncName, opts.Agent, opts.Namespace)
	for _, line := range plan {
		fmt.Fprintf(opts.Out, "  %s\n", line)
	}
	fmt.Fprintln(opts.Out, "")

	if opts.DryRun {
		fmt.Fprintln(opts.Out, "Dry-run mode — no API calls made.")
		return nil
	}

	// Mutate the CR in-place.
	if err := attachGitSync(cr, syncName, ref.CloneURL, opts.Branch, period, secretName, repoPath, backendsCovered); err != nil {
		return err
	}

	if _, err := updateAgentCR(ctx, dyn, cr); err != nil {
		return err
	}

	fmt.Fprintf(opts.Out, "Attached gitSync %q to WitwaveAgent %s/%s.\n",
		syncName, opts.Namespace, opts.Agent)
	fmt.Fprintln(opts.Out, "The operator will reconcile a git-sync sidecar shortly.")
	fmt.Fprintln(opts.Out, "Watch progress with:")
	fmt.Fprintf(opts.Out, "  ww agent status %s\n", opts.Agent)
	fmt.Fprintf(opts.Out, "  ww agent events %s\n", opts.Agent)
	fmt.Fprintf(opts.Out, "  ww agent logs   %s -c git-sync-%s\n", opts.Agent, syncName)
	return nil
}

func validateGitAddOptions(opts *GitAddOptions) error {
	if err := ValidateName(opts.Agent); err != nil {
		return err
	}
	if opts.Repo == "" {
		return fmt.Errorf("--repo is required")
	}
	if opts.Namespace == "" {
		return fmt.Errorf("namespace is required")
	}
	if opts.SyncName != "" {
		if err := ValidateName(opts.SyncName); err != nil {
			return fmt.Errorf("sync name: %w", err)
		}
	}
	return nil
}

// attachGitSync mutates the CR in-place to install (or replace) the
// named gitSync entry + its mappings. Idempotent: re-running with the
// same syncName overwrites every field ww owns for that sync,
// preserving any unrelated mappings (different syncName) untouched.
//
// Workflow:
//  1. Replace (or append) the named entry in spec.gitSyncs[].
//  2. Drop any existing harness-level mappings tied to this syncName;
//     add the fresh harness mapping.
//  3. For each backend in `backendsCovered`, drop existing backend-
//     level mappings tied to this syncName and add the fresh one.
func attachGitSync(
	cr *unstructured.Unstructured,
	syncName, repoURL, ref, period, credSecret, repoPath string,
	backendsCovered []string,
) error {
	// --- gitSyncs[] entry ---
	syncs, err := readGitSyncs(cr)
	if err != nil {
		return err
	}
	entry := buildGitSyncEntry(syncName, repoURL, ref, period, credSecret)
	if idx, _ := syncEntryByName(syncs, syncName); idx >= 0 {
		syncs[idx] = entry
	} else {
		syncs = append(syncs, entry)
	}
	if err := writeGitSyncs(cr, syncs); err != nil {
		return err
	}

	// --- harness-level gitMappings[] ---
	harnessMaps, err := readHarnessGitMappings(cr)
	if err != nil {
		return err
	}
	harnessMaps = filterMappingsByGitSync(harnessMaps, syncName)
	harnessMaps = append(harnessMaps, buildHarnessMapping(syncName, repoPath))
	if err := writeHarnessGitMappings(cr, harnessMaps); err != nil {
		return err
	}

	// --- per-backend gitMappings[] ---
	backends, err := readBackends(cr)
	if err != nil {
		return err
	}
	covered := make(map[string]bool, len(backendsCovered))
	for _, n := range backendsCovered {
		covered[n] = true
	}
	for i, b := range backends {
		name, _ := b["name"].(string)
		if !covered[name] {
			continue
		}
		existing, _ := b["gitMappings"].([]interface{})
		kept := make([]interface{}, 0, len(existing))
		for _, e := range existing {
			m, ok := e.(map[string]interface{})
			if !ok {
				kept = append(kept, e) // preserve opaque entries as-is
				continue
			}
			if n, _ := m["gitSync"].(string); n == syncName {
				continue
			}
			kept = append(kept, m)
		}
		kept = append(kept, buildBackendMapping(syncName, repoPath, name))
		b["gitMappings"] = kept
		backends[i] = b
	}
	return writeBackends(cr, backends)
}
