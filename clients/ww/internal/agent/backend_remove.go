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

// BackendRemoveOptions collects the runtime inputs for `ww agent backend remove`.
type BackendRemoveOptions struct {
	Agent       string
	Namespace   string
	BackendName string

	// RemoveRepoFolder, when true, also deletes the repo-side
	// `.agents/<…>/.<backend>/` folder via a git rm + commit + push.
	// Needs a single configured gitSync + a harness mapping anchoring
	// `.witwave/` (same preconditions as rename's repo-side move).
	// Default false — CRs and repos are independent by default, and
	// a user who wants the config preserved for later re-attach needs
	// the folder kept.
	RemoveRepoFolder bool

	// CommitMessage overrides the auto-generated commit subject on
	// the repo-side remove. Empty → "Remove backend <name> for agent <agent>".
	CommitMessage string

	AssumeYes bool
	DryRun    bool
	Out       io.Writer
	In        io.Reader
}

// BackendRemove drops a backend from a WitwaveAgent's spec.backends[]
// and rewrites the inline spec.config backend.yaml (if ww owns it)
// to exclude the removed backend. Order of operations:
//
//  1. Fetch the CR.
//  2. Locate the backend by name; refuse cleanly when it's missing so
//     the user doesn't silently think they removed something that
//     wasn't there.
//  3. Refuse when removing would leave zero backends — the CRD's
//     minItems:1 validation would reject the Update anyway, but
//     failing here produces a much nicer diagnostic than the
//     apiserver's schema-validation blob.
//  4. If ww owns the inline backend.yaml (scaffolded by `ww agent
//     create` under spec.config[0]), regenerate it using the
//     remaining backends. Default routing: every concern → the new
//     primary (first remaining backend). Any user-customised routing
//     in the inline file is lost — this is documented in the verb's
//     help text.
//  5. When backend.yaml is gitSync-mounted, leave spec.config alone
//     and emit a warning. The repo's copy is the source of truth in
//     that configuration, and editing it lives outside this verb's
//     scope.
//  6. Update the CR.
func BackendRemove(ctx context.Context, cfg *rest.Config, opts BackendRemoveOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("BackendRemoveOptions.Out is required")
	}
	if err := ValidateName(opts.Agent); err != nil {
		return err
	}
	if opts.BackendName == "" {
		return fmt.Errorf("backend name is required (positional arg)")
	}

	dyn, err := newDynamicClient(cfg)
	if err != nil {
		return err
	}
	cr, err := fetchAgentCR(ctx, dyn, opts.Namespace, opts.Agent)
	if err != nil {
		return err
	}

	backends, err := readBackends(cr)
	if err != nil {
		return err
	}

	// Locate target + collect the remaining entries in a single pass.
	var kept []map[string]interface{}
	found := false
	for _, b := range backends {
		name, _ := b["name"].(string)
		if name == opts.BackendName {
			found = true
			continue
		}
		kept = append(kept, b)
	}
	if !found {
		names := make([]string, 0, len(backends))
		for _, b := range backends {
			if n, _ := b["name"].(string); n != "" {
				names = append(names, n)
			}
		}
		return fmt.Errorf(
			"backend %q not found on WitwaveAgent %s/%s. Current backends: [%s]",
			opts.BackendName, opts.Namespace, opts.Agent, strings.Join(names, ", "),
		)
	}
	if len(kept) == 0 {
		return fmt.Errorf(
			"refusing to remove the last backend from %s/%s — the CRD requires at least one. "+
				"Add a replacement first (future: `ww agent backend add`) or delete the whole agent with `ww agent delete %s`",
			opts.Namespace, opts.Agent, opts.Agent,
		)
	}

	// Classify where backend.yaml lives. If ww still owns the inline
	// spec.config entry AND no gitMapping overrides /home/agent/.witwave/,
	// we rewrite it. Otherwise we leave it alone and surface a warning.
	inlineCfgIdx, inlineCfg := findInlineBackendYAML(cr)
	gitMounted := harnessBackendYAMLGitMounted(cr)

	planLines := []string{
		fmt.Sprintf("backend   %q (removed)", opts.BackendName),
	}
	keptNames := make([]string, 0, len(kept))
	for _, b := range kept {
		if n, _ := b["name"].(string); n != "" {
			keptNames = append(keptNames, n)
		}
	}
	planLines = append(planLines, fmt.Sprintf("remaining [%s]", strings.Join(keptNames, ", ")))
	switch {
	case inlineCfgIdx >= 0 && !gitMounted:
		planLines = append(planLines,
			"backend.yaml (inline, ww-managed) will be regenerated with default routing → "+keptNames[0])
	case gitMounted:
		planLines = append(planLines,
			"backend.yaml (gitSync-managed) — edit the repo's file manually to drop references to "+opts.BackendName)
	}

	fmt.Fprintf(opts.Out, "\nAction:    remove backend %q from WitwaveAgent %q in %s\n",
		opts.BackendName, opts.Agent, opts.Namespace)
	for _, line := range planLines {
		fmt.Fprintf(opts.Out, "  %s\n", line)
	}
	fmt.Fprintln(opts.Out, "")

	if opts.DryRun {
		fmt.Fprintln(opts.Out, "Dry-run mode — no API calls made.")
		return nil
	}

	// Mutate the CR in-place.
	if err := writeBackends(cr, kept); err != nil {
		return err
	}
	if inlineCfgIdx >= 0 && !gitMounted {
		if err := rewriteInlineBackendYAML(cr, inlineCfgIdx, inlineCfg, kept); err != nil {
			return err
		}
	}
	if _, err := updateAgentCR(ctx, dyn, cr); err != nil {
		return err
	}

	fmt.Fprintf(opts.Out, "Removed backend %q from WitwaveAgent %s/%s.\n",
		opts.BackendName, opts.Namespace, opts.Agent)

	// Repo-side removal (optional). Mirrors rename's phase-2 structure:
	// clone → modify → commit → push, best-effort (failures log a
	// warning but don't revert the CR update — the cluster state is
	// already correct either way).
	if opts.RemoveRepoFolder {
		keptSpecs := make([]BackendSpec, 0, len(kept))
		for _, b := range kept {
			name, _ := b["name"].(string)
			port, _ := b["port"].(int64)
			keptSpecs = append(keptSpecs, BackendSpec{Name: name, Port: int32(port)}) //nolint:gosec // G115: port bounded by CRD schema [DefaultBackendBasePort=8001, DefaultBackendMaxPort=8050]; cannot exceed int32.
		}
		if err := removeRepoFolder(ctx, cr, opts, keptSpecs); err != nil {
			fmt.Fprintf(opts.Out, "WARNING: repo-side folder removal failed: %v\n", err)
			fmt.Fprintf(opts.Out,
				"         Remove manually: git rm -r .agents/<…>/.%s && push\n", opts.BackendName)
		}
	} else if gitMounted {
		fmt.Fprintln(opts.Out,
			"NOTE: backend.yaml is gitSync-managed. If it still routes any concern to "+
				opts.BackendName+", the harness will fail to dial a sidecar that no longer exists — "+
				"edit the repo's .witwave/backend.yaml to remove the entry + reroute affected concerns. "+
				"Pass --remove-repo-folder on the next rerun to automate this.")
	}
	return nil
}

// removeRepoFolder handles the repo-side cleanup when the user passed
// --remove-repo-folder. Shares most of its shape with the rename's
// repo-side move: discover the sync via decideRepoRenameScope
// (re-used because the "which sync is the primary" decision is
// identical), clone, git rm -r the backend folder, rewrite the repo's
// backend.yaml to drop the removed backend, commit, push.
func removeRepoFolder(ctx context.Context, cr *unstructured.Unstructured, opts BackendRemoveOptions, keptBackends []BackendSpec) error {
	// Reuse rename's scope calculator — the "find the sync + the
	// agent's repo dir" logic is identical. Pass a synthetic
	// BackendRenameOptions with OldName=BackendName so the scope's
	// srcPath resolves to the folder we want to remove.
	renameOpts := BackendRenameOptions{
		Agent:   opts.Agent,
		OldName: opts.BackendName,
	}
	sync, scope := decideRepoRenameScope(cr, renameOpts)
	if sync == nil {
		fmt.Fprintln(opts.Out, "No gitSync configured on this agent — skipping repo-side folder removal.")
		return nil
	}
	if scope.srcPath == "" {
		fmt.Fprintln(opts.Out,
			"Multiple gitSyncs or no harness mapping — skipping repo-side folder removal (ambiguous).")
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

	tmp, err := os.MkdirTemp("", fmt.Sprintf("ww-backend-remove-%s-", opts.Agent))
	if err != nil {
		return fmt.Errorf("temp dir: %w", err)
	}
	defer os.RemoveAll(tmp)

	repo, _, err := cloneOrInit(ctx, tmp, ref, scope.branch, auth, opts.Out)
	if err != nil {
		return err
	}

	// git rm -r the folder. If the folder doesn't exist, short-circuit
	// with an informative message — the CR was already updated, so a
	// missing repo folder is "already in the desired state."
	folderAbs := filepath.Join(tmp, filepath.FromSlash(scope.srcPath))
	if _, statErr := os.Stat(folderAbs); os.IsNotExist(statErr) {
		fmt.Fprintf(opts.Out, "Repo folder %q not present — nothing to remove on the repo side.\n", scope.srcPath)
		return nil
	}
	cmd := exec.CommandContext(ctx, "git", "-C", tmp, "rm", "-r", scope.srcPath)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git rm -r %s: %w (output: %s)", scope.srcPath, err, strings.TrimSpace(string(out)))
	}

	// Rewrite backend.yaml if it exists in the repo.
	backendYAMLPath := filepath.Join(tmp, filepath.FromSlash(scope.agentDir), ".witwave", "backend.yaml")
	if _, statErr := os.Stat(backendYAMLPath); statErr == nil {
		if len(keptBackends) == 0 {
			// Shouldn't happen — we already refused in BackendRemove
			// when kept was zero — but guard just in case.
			return fmt.Errorf("cannot regenerate backend.yaml with zero backends")
		}
		updated := renderBackendYAML(keptBackends)
		if err := os.WriteFile(backendYAMLPath, []byte(updated), 0o644); err != nil {
			return fmt.Errorf("rewrite %s: %w", backendYAMLPath, err)
		}
		relYAML := filepath.ToSlash(filepath.Join(scope.agentDir, ".witwave", "backend.yaml"))
		if out, err := exec.CommandContext(ctx, "git", "-C", tmp, "add", relYAML).CombinedOutput(); err != nil {
			return fmt.Errorf("git add %s: %w (output: %s)", relYAML, err, strings.TrimSpace(string(out)))
		}
	}

	msg := opts.CommitMessage
	if msg == "" {
		msg = fmt.Sprintf("Remove backend %s for agent %s", opts.BackendName, opts.Agent)
	}
	msg += "\n\nRemoved-by: ww agent backend remove\n"

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

	sig := signatureFromGitConfig()
	hash, err := wt.Commit(msg, &gogit.CommitOptions{Author: sig, Committer: sig})
	if err != nil {
		return fmt.Errorf("commit: %w", err)
	}
	fmt.Fprintf(opts.Out, "Committed %s: %s\n", shortSHA(hash), strings.Split(msg, "\n")[0])
	return pushBranch(ctx, repo, scope.branch, auth, opts.Out)
}

// findInlineBackendYAML scans spec.config[] for the entry that mounts
// backend.yaml into the harness. Returns the index + raw entry when
// found; (-1, nil) when missing. Callers use this to distinguish
// ww-owned inline config from a user-supplied one.
func findInlineBackendYAML(cr *unstructured.Unstructured) (int, map[string]interface{}) {
	raw, found, err := unstructured.NestedSlice(cr.Object, "spec", "config")
	if err != nil || !found {
		return -1, nil
	}
	for i, entry := range raw {
		m, ok := entry.(map[string]interface{})
		if !ok {
			continue
		}
		mount, _ := m["mountPath"].(string)
		if mount == "/home/agent/.witwave/backend.yaml" {
			return i, m
		}
	}
	return -1, nil
}

// harnessBackendYAMLGitMounted returns true when any harness-level
// gitMapping targets the .witwave/ directory (which overlays the
// inline backend.yaml). When true, the repo's file is the source of
// truth and remove shouldn't touch spec.config.
func harnessBackendYAMLGitMounted(cr *unstructured.Unstructured) bool {
	maps, err := readHarnessGitMappings(cr)
	if err != nil {
		return false
	}
	for _, m := range maps {
		dest, _ := m["dest"].(string)
		// Any mapping whose dest encompasses /home/agent/.witwave/ wins —
		// covers both directory mounts (dest trailing `/`) and exact
		// backend.yaml overlays.
		if dest == "/home/agent/.witwave/" ||
			dest == "/home/agent/.witwave" ||
			dest == "/home/agent/.witwave/backend.yaml" {
			return true
		}
	}
	return false
}

// rewriteInlineBackendYAML replaces the content of the inline
// backend.yaml config entry with a freshly-rendered version that
// enumerates the `kept` backends and routes every concern to the new
// primary (kept[0]). Preserves every other field on the config entry
// (name, mountPath, any future keys).
func rewriteInlineBackendYAML(cr *unstructured.Unstructured, idx int, entry map[string]interface{}, kept []map[string]interface{}) error {
	cfg, found, err := unstructured.NestedSlice(cr.Object, "spec", "config")
	if err != nil {
		return fmt.Errorf("read spec.config: %w", err)
	}
	if !found {
		return nil
	}

	// Reconstruct BackendSpecs from the remaining backends so the
	// shared renderer can produce the right shape. Ports are read
	// back from the CR — users may have assigned custom ports we'd
	// otherwise clobber.
	specs := make([]BackendSpec, 0, len(kept))
	for _, b := range kept {
		name, _ := b["name"].(string)
		port, _ := b["port"].(int64)
		specs = append(specs, BackendSpec{Name: name, Port: int32(port)}) //nolint:gosec // G115: port bounded by CRD schema [DefaultBackendBasePort=8001, DefaultBackendMaxPort=8050]; cannot exceed int32.
	}

	// Shallow-copy the map so the caller's reference isn't mutated
	// out from under them before we write it back.
	updated := make(map[string]interface{}, len(entry))
	for k, v := range entry {
		updated[k] = v
	}
	updated["content"] = renderBackendYAML(specs)
	cfg[idx] = updated
	return unstructured.SetNestedSlice(cr.Object, cfg, "spec", "config")
}
