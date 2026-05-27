package agent

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	gogit "github.com/go-git/go-git/v5"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/rest"
)

// BackendAddOptions collects the runtime inputs for `ww agent backend add`.
//
// The inverse of BackendRemoveOptions — appends a new backend to an
// existing WitwaveAgent's `spec.backends[]`. Reuses the auth-resolver
// catalog from `create` so `--auth <profile>` reads the same env vars
// in the same way. When the agent has a gitSync wired AND RepoFolder
// is true, also writes a starter `.agents/<…>/.<name>/agent-card.md`
// (and, for LLM backends, a behavioural-instructions stub) to the
// repo so the new sidecar has content to pull on its first sync.
type BackendAddOptions struct {
	Agent     string
	Namespace string

	// Backend is the spec of the backend being added. Name must not
	// collide with any existing backend on the agent; Type must be a
	// known type (see KnownBackends). Port is picked automatically
	// when zero — the first free slot in the 8001..8050 range.
	Backend BackendSpec

	// Auth resolves a credential Secret for the new backend. Zero
	// value (BackendAuthNone) is legal for backends that don't need
	// credentials (echo). LLM backends without an Auth set will start
	// up but error on first call with a missing-key diagnostic — a
	// warning is surfaced in the preflight banner.
	Auth BackendAuthResolver

	// RepoFolder controls whether to scaffold the new backend's
	// repo folder + rewrite the repo's backend.yaml to list it. Runs
	// ONLY when the agent has exactly one gitSync wired AND a harness
	// gitMapping anchors `.witwave/`. Default true (preflight describes
	// whether the phase will actually fire); set false to skip.
	RepoFolder bool

	// CommitMessage overrides the default commit subject for the
	// repo-side scaffold. Empty → "Add backend <name> for agent <agent>".
	CommitMessage string

	// CLIVersion is threaded through for behavioural-stub rendering
	// so generated content carries the same scaffold provenance as
	// the original `ww agent scaffold` run.
	CLIVersion string

	AssumeYes bool
	DryRun    bool
	Out       io.Writer
	In        io.Reader
}

// BackendAdd appends a backend to an existing WitwaveAgent. Flow:
//
//  1. Fetch the CR; validate the new backend spec against it
//     (unique name, known type, room under the CRD's 50 cap).
//  2. Pick a port — first free slot in the 8001..8050 range.
//  3. Resolve the auth flag → maybe mint a credential Secret.
//  4. Banner + Confirm. Dry-run exits here with no API calls.
//  5. Update the CR: append the backend entry (with credentials
//     reference when auth resolved to a Secret). Regenerate the
//     inline backend.yaml when ww owns it.
//  6. Repo-side (when applicable): clone, write
//     `.<name>/agent-card.md` (+ behavioural stub for LLM types),
//     rewrite `.witwave/backend.yaml` to include the new backend,
//     commit, push. Best-effort — repo-side failures log a warning
//     but don't revert the CR update (same posture rename/remove use).
func BackendAdd(ctx context.Context, cfg *rest.Config, opts BackendAddOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("BackendAddOptions.Out is required")
	}
	if err := ValidateName(opts.Agent); err != nil {
		return err
	}
	if err := ValidateName(opts.Backend.Name); err != nil {
		return fmt.Errorf("backend name: %w", err)
	}
	if !IsKnownBackend(opts.Backend.Type) {
		return fmt.Errorf(
			"unknown backend type %q; valid: %v",
			opts.Backend.Type, KnownBackends(),
		)
	}

	dyn, err := newDynamicClient(cfg)
	if err != nil {
		return err
	}
	cr, err := fetchAgentCR(ctx, dyn, opts.Namespace, opts.Agent)
	if err != nil {
		return err
	}

	existing, err := readBackends(cr)
	if err != nil {
		return err
	}

	// Uniqueness gate. The CRD's apiserver validation would catch
	// the duplicate on Update, but failing here surfaces the error
	// before we mint any Secret.
	for _, b := range existing {
		if n, _ := b["name"].(string); n == opts.Backend.Name {
			return fmt.Errorf(
				"backend %q already exists on WitwaveAgent %s/%s. "+
					"Rename the existing one with `ww agent backend rename %s %s <new-name>` "+
					"or pick a different name for the new backend.",
				opts.Backend.Name, opts.Namespace, opts.Agent,
				opts.Agent, opts.Backend.Name,
			)
		}
	}

	// CRD cap: MaxItems=50 on spec.backends. Catch the 51st here
	// with a nicer diagnostic than the apiserver's schema blob.
	if len(existing) >= 50 {
		return fmt.Errorf(
			"WitwaveAgent %s/%s already has %d backends — the CRD caps at 50. "+
				"Remove one first with `ww agent backend remove`",
			opts.Namespace, opts.Agent, len(existing),
		)
	}

	// Port picking: first free slot in [8001, 8050]. Users can still
	// set Backend.Port explicitly to override; zero means "auto".
	if opts.Backend.Port == 0 {
		opts.Backend.Port, err = nextFreeBackendPort(existing)
		if err != nil {
			return err
		}
	}

	// Preflight banner — describe what WILL happen without actually
	// minting a Secret or touching the cluster. The real resolve()
	// call runs below, after the dry-run gate + Confirm.
	opts.Auth.Backend = opts.Backend.Name
	predictedSecretName := ""
	switch opts.Auth.Mode {
	case BackendAuthProfile, BackendAuthFromEnv:
		predictedSecretName = backendCredentialSecretName(opts.Agent, opts.Backend.Name)
	case BackendAuthExistingSecret:
		predictedSecretName = opts.Auth.ExistingSecret
	}

	fmt.Fprintf(opts.Out, "\nAction:    add backend %q (type=%s, port=%d) to WitwaveAgent %q in %s\n",
		opts.Backend.Name, opts.Backend.Type, opts.Backend.Port, opts.Agent, opts.Namespace)
	switch {
	case opts.Auth.Mode == BackendAuthProfile:
		fmt.Fprintf(opts.Out, "  credentials: profile %q → mint Secret %q\n", opts.Auth.Profile, predictedSecretName)
	case opts.Auth.Mode == BackendAuthFromEnv:
		fmt.Fprintf(opts.Out, "  credentials: from env (%s) → mint Secret %q\n", strings.Join(opts.Auth.EnvVars, ", "), predictedSecretName)
	case opts.Auth.Mode == BackendAuthExistingSecret:
		fmt.Fprintf(opts.Out, "  credentials: reference existing Secret %q (verified, not modified)\n", predictedSecretName)
	case opts.Backend.Type != "echo":
		fmt.Fprintf(opts.Out, "  credentials: none — %s is an LLM backend; expect runtime errors until you set --auth\n", opts.Backend.Type)
	}

	// Inline backend.yaml handling identical to backend_remove: if ww
	// owns the spec.config entry AND no gitMapping overrides .witwave/,
	// regenerate the inline yaml with the appended backend.
	inlineCfgIdx, inlineCfg := findInlineBackendYAML(cr)
	gitMounted := harnessBackendYAMLGitMounted(cr)
	if inlineCfgIdx >= 0 && !gitMounted {
		fmt.Fprintln(opts.Out,
			"  backend.yaml (inline, ww-managed) will be regenerated to include the new backend (routing unchanged)")
	} else if gitMounted {
		fmt.Fprintln(opts.Out,
			"  backend.yaml (gitSync-managed) — edit the repo's file to list the new backend (routing still flows through the primary)")
	}

	// Repo-side preview. Full resolution runs after Confirm.
	willScaffoldRepo := false
	if opts.RepoFolder {
		renameOpts := BackendRenameOptions{Agent: opts.Agent, OldName: opts.Backend.Name}
		sync, scope := decideRepoRenameScope(cr, renameOpts)
		switch {
		case sync == nil:
			fmt.Fprintln(opts.Out, "  repo scaffold: (skipped — no gitSync configured)")
		case scope.srcPath == "":
			fmt.Fprintln(opts.Out, "  repo scaffold: (skipped — multiple gitSyncs or no harness mapping)")
		default:
			willScaffoldRepo = true
			fmt.Fprintf(opts.Out, "  repo scaffold: write %s/agent-card.md to %s (branch %s)\n",
				scope.srcPath, scope.repoDisplay, scope.branch)
		}
	}

	if opts.DryRun {
		fmt.Fprintln(opts.Out, "\nDry-run mode — no API calls made.")
		return nil
	}

	// Real auth resolution — runs after dry-run exits so predicts in
	// the banner and real Secret writes stay in order. Populates
	// CredentialSecret so the CR entry below carries
	// credentials.existingSecret.
	if opts.Auth.Mode != BackendAuthNone {
		k8sClient, err := newKubernetesClient(cfg)
		if err != nil {
			return err
		}
		secretName, err := opts.Auth.resolve(ctx, k8sClient, opts.Namespace, opts.Agent, opts.Backend.Type)
		if err != nil {
			return err
		}
		opts.Backend.CredentialSecret = secretName
	}

	// Apply to the CR. Build the new entry shape Build() uses so the
	// operator sees identical structure regardless of whether the
	// backend came in via create or via add.
	entry := map[string]interface{}{
		"name": opts.Backend.Name,
		"port": int64(opts.Backend.Port),
		"image": map[string]interface{}{
			"repository": splitRepo(BackendImage(opts.Backend.Type, opts.CLIVersion)),
			"tag":        splitTag(BackendImage(opts.Backend.Type, opts.CLIVersion)),
		},
	}
	if opts.Backend.CredentialSecret != "" {
		entry["credentials"] = map[string]interface{}{
			"existingSecret": opts.Backend.CredentialSecret,
		}
	}
	updatedBackends := append(existing, entry) //nolint:gocritic // append to CR-owned slice

	if err := writeBackends(cr, updatedBackends); err != nil {
		return err
	}

	if inlineCfgIdx >= 0 && !gitMounted {
		if err := rewriteInlineBackendYAML(cr, inlineCfgIdx, inlineCfg, updatedBackends); err != nil {
			return err
		}
	}

	if _, err := updateAgentCR(ctx, dyn, cr); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "Added backend %q to WitwaveAgent %s/%s (port %d).\n",
		opts.Backend.Name, opts.Namespace, opts.Agent, opts.Backend.Port)

	// Repo-side scaffold.
	if willScaffoldRepo {
		if err := addRepoFolder(ctx, cr, opts); err != nil {
			fmt.Fprintf(opts.Out, "WARNING: repo-side scaffold failed: %v\n", err)
			fmt.Fprintf(opts.Out,
				"         Scaffold manually: clone, add `.agents/<…>/.%s/agent-card.md`, commit + push.\n",
				opts.Backend.Name)
		}
	}

	// "Next steps" pointer. Skip this when routing is inline-ww-
	// managed (no user action needed — routing already points at the
	// primary). In the gitSync-managed case, nudge toward the repo.
	if gitMounted {
		fmt.Fprintf(opts.Out,
			"\nNext: edit the repo's `.witwave/backend.yaml` to list %q and (if desired) route a concern to it.\n",
			opts.Backend.Name)
	}

	return nil
}

// nextFreeBackendPort returns the lowest port in [8001, 8050] not
// already claimed by an existing backend. Returns an error when every
// slot is taken — the CRD's 50-cap should have caught this upstream,
// but defensive second check protects against CRs with sparse ports.
func nextFreeBackendPort(existing []map[string]interface{}) (int32, error) {
	used := make(map[int32]bool, len(existing))
	for _, b := range existing {
		// Unstructured decoding is inconsistent on numeric fields:
		// apiserver-returned CRs deserialize as float64 (encoding/json's
		// default for numbers into interface{}), while CRs built
		// locally via map literals carry int64. Accept either so the
		// picker works against both live cluster state and seeded
		// fake-client state.
		switch p := b["port"].(type) {
		case int64:
			used[int32(p)] = true //nolint:gosec // G115: port values are bounded by [DefaultBackendBasePort=8001, DefaultBackendMaxPort=8050] via the CRD schema + nextFreeBackendPort range below; cannot exceed int32.
		case float64:
			used[int32(p)] = true //nolint:gosec // G115: same CRD-bounded port range as the int64 case above.
		case int32:
			used[p] = true
		}
	}
	for p := DefaultBackendBasePort; p <= DefaultBackendMaxPort; p++ {
		if !used[p] {
			return p, nil
		}
	}
	return 0, fmt.Errorf(
		"no free backend port in [%d, %d] — all 50 slots are claimed",
		DefaultBackendBasePort, DefaultBackendMaxPort,
	)
}

// allBackendSpecs converts the raw []map{} backend list into
// []BackendSpec for downstream helpers that expect the typed shape
// (renderBackendYAML, rewriteInlineBackendYAML's regeneration).
func allBackendSpecs(entries []map[string]interface{}) []BackendSpec {
	out := make([]BackendSpec, 0, len(entries))
	for _, b := range entries {
		name, _ := b["name"].(string)
		typ, _ := b["type"].(string)
		if typ == "" {
			typ = name // fall back to name-is-type convention
		}
		port, _ := b["port"].(int64)
		out = append(out, BackendSpec{Name: name, Type: typ, Port: int32(port)}) //nolint:gosec // G115: port bounded by CRD schema [DefaultBackendBasePort=8001, DefaultBackendMaxPort=8050]; cannot exceed int32.
	}
	return out
}

// addRepoFolder handles the repo-side scaffolding when the user left
// --repo-folder at its default (true) and the agent has a single
// wired gitSync. Mirrors backend_remove.removeRepoFolder's shape:
// clone → mutate → commit → push. Best-effort from the caller's
// perspective; errors bubble up as WARNINGs, never reverting the CR.
func addRepoFolder(ctx context.Context, cr *unstructured.Unstructured, opts BackendAddOptions) error {
	renameOpts := BackendRenameOptions{Agent: opts.Agent, OldName: opts.Backend.Name}
	sync, scope := decideRepoRenameScope(cr, renameOpts)
	if sync == nil || scope.srcPath == "" {
		// Already reported in the preflight banner — no-op here.
		return nil
	}

	ref, err := parseRepoRef(scope.repoURL)
	if err != nil {
		return fmt.Errorf("parse repo %q: %w", scope.repoURL, err)
	}
	auth, err := resolveGitAuth(ref)
	if err != nil {
		return fmt.Errorf("resolve git auth: %w", err)
	}

	tmp, err := os.MkdirTemp("", fmt.Sprintf("ww-backend-add-%s-", opts.Agent))
	if err != nil {
		return fmt.Errorf("temp dir: %w", err)
	}
	defer os.RemoveAll(tmp)

	repo, _, err := cloneOrInit(ctx, tmp, ref, scope.branch, auth, opts.Out)
	if err != nil {
		return err
	}

	// Write the new backend's folder. Mirror the scaffold's layout:
	// `.<name>/agent-card.md` always, plus the behavioural stub
	// (CLAUDE.md / AGENTS.md / GEMINI.md) for LLM backends.
	folderAbs := filepath.Join(tmp, filepath.FromSlash(scope.srcPath))
	if err := os.MkdirAll(folderAbs, 0o755); err != nil {
		return fmt.Errorf("mkdir %s: %w", scope.srcPath, err)
	}
	agentCardPath := filepath.Join(folderAbs, "agent-card.md")
	if err := os.WriteFile(agentCardPath, []byte(renderAgentCard(opts.Agent, opts.Backend.Name, opts.Backend.Type)), 0o644); err != nil {
		return fmt.Errorf("write agent-card.md: %w", err)
	}
	if behaviorName, ok := behaviorFileName(opts.Backend.Type); ok {
		behaviorPath := filepath.Join(folderAbs, behaviorName)
		if err := os.WriteFile(behaviorPath, []byte(renderBehaviorStub(opts.Agent, opts.Backend.Name, opts.Backend.Type)), 0o644); err != nil {
			return fmt.Errorf("write %s: %w", behaviorName, err)
		}
	}

	// Regenerate the repo's backend.yaml to list the new backend.
	// Build a BackendSpec slice from the CR's (already-updated)
	// spec.backends so the regenerated file matches the cluster.
	updated, _ := readBackends(cr)
	allSpecs := allBackendSpecs(updated)
	backendYAMLPath := filepath.Join(tmp, filepath.FromSlash(scope.agentDir), ".witwave", "backend.yaml")
	if err := os.WriteFile(backendYAMLPath, []byte(renderBackendYAML(allSpecs)), 0o644); err != nil {
		return fmt.Errorf("rewrite backend.yaml: %w", err)
	}

	// Stage everything under the agent dir so the fresh folder +
	// the regenerated backend.yaml both land in the commit.
	relAgentDir := filepath.ToSlash(scope.agentDir)
	if out, err := exec.CommandContext(ctx, "git", "-C", tmp, "add", relAgentDir).CombinedOutput(); err != nil {
		return fmt.Errorf("git add %s: %w (output: %s)", relAgentDir, err, strings.TrimSpace(string(out)))
	}

	wt, err := repo.Worktree()
	if err != nil {
		return fmt.Errorf("worktree: %w", err)
	}
	status, err := wt.Status()
	if err != nil {
		return fmt.Errorf("worktree status: %w", err)
	}
	if status.IsClean() {
		fmt.Fprintln(opts.Out, "Repo already in sync — no commit.")
		return nil
	}

	msg := opts.CommitMessage
	if msg == "" {
		msg = fmt.Sprintf("Add backend %s for agent %s", opts.Backend.Name, opts.Agent)
	}
	msg += "\n\nAdded-by: ww agent backend add\n"

	sig := signatureFromGitConfig()
	hash, err := wt.Commit(msg, &gogit.CommitOptions{Author: sig, Committer: sig})
	if err != nil {
		return fmt.Errorf("commit: %w", err)
	}
	fmt.Fprintf(opts.Out, "Committed %s: %s\n", shortSHA(hash), strings.Split(msg, "\n")[0])
	return pushBranch(ctx, repo, scope.branch, auth, opts.Out)
}
