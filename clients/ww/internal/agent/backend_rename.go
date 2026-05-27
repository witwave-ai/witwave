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

// BackendRenameOptions collects the runtime inputs for
// `ww agent backend rename`. Carries both cluster-side fields (Agent,
// Namespace, name pair) and repo-side knobs (RepoRename, CommitMessage)
// because a rename is intrinsically a two-phase operation: CR first,
// then the matching folder in the gitOps repo.
type BackendRenameOptions struct {
	Agent     string
	Namespace string

	OldName string
	NewName string

	// RepoRename controls whether the repo-side folder move happens.
	// Default true. Set to false for "rename the CR only, I'll handle
	// the repo myself" workflows.
	RepoRename bool

	// CommitMessage overrides the auto-generated commit subject on the
	// repo-side move. Empty → "Rename backend <old> → <new> for agent <agent>".
	CommitMessage string

	AssumeYes bool
	DryRun    bool
	Out       io.Writer
	In        io.Reader
}

// BackendRename renames a backend on a WitwaveAgent both in the CR and
// — if gitOps is wired — in the repo. Order of operations:
//
//  1. Fetch CR, validate names, locate the old entry.
//  2. Rename spec.backends[N].name, rewrite every harness + per-
//     backend gitMapping that referenced the old name, regenerate
//     the inline spec.config backend.yaml (if ww owns it).
//  3. Update CR. Repo work happens AFTER this so a failed apiserver
//     write doesn't leave the repo "ahead of" the cluster.
//  4. If RepoRename is true AND spec.gitSyncs[] includes exactly one
//     sync whose harness mapping anchors `.agents/<…>/.witwave/`,
//     clone that repo, `git mv` the backend's folder, commit, push.
//     Multiple gitSyncs → refuse; zero → no-op.
//
// The repo work is best-effort from the user's perspective: if the
// clone or push fails, the CR rename is preserved and the operator
// will keep pulling the old folder content until the user manually
// renames the repo folder. A warning is printed telling them so.
func BackendRename(ctx context.Context, cfg *rest.Config, opts BackendRenameOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("BackendRenameOptions.Out is required")
	}
	if err := ValidateName(opts.Agent); err != nil {
		return err
	}
	if err := ValidateName(opts.NewName); err != nil {
		return fmt.Errorf("new backend name: %w", err)
	}
	if opts.OldName == "" || opts.NewName == "" {
		return fmt.Errorf("both old and new backend names are required")
	}
	if opts.OldName == opts.NewName {
		return fmt.Errorf("new name %q matches old name — nothing to rename", opts.NewName)
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

	// Locate old, refuse if new already exists.
	oldIdx := -1
	for i, b := range backends {
		name, _ := b["name"].(string)
		switch name {
		case opts.OldName:
			oldIdx = i
		case opts.NewName:
			return fmt.Errorf(
				"backend %q already exists on WitwaveAgent %s/%s — refusing to rename %q on top of it",
				opts.NewName, opts.Namespace, opts.Agent, opts.OldName,
			)
		}
	}
	if oldIdx < 0 {
		names := make([]string, 0, len(backends))
		for _, b := range backends {
			if n, _ := b["name"].(string); n != "" {
				names = append(names, n)
			}
		}
		return fmt.Errorf(
			"backend %q not found on WitwaveAgent %s/%s. Current backends: [%s]",
			opts.OldName, opts.Namespace, opts.Agent, strings.Join(names, ", "),
		)
	}

	// Decide whether repo work is in-scope. Needs: RepoRename flag true
	// AND exactly one gitSync whose name the per-backend mapping
	// references. Zero gitSyncs → CR-only rename is correct. Multiple
	// → can't auto-pick without --sync-name; refuse the repo phase
	// (still do the CR rename) and warn.
	repoSync, repoRenameScope := decideRepoRenameScope(cr, opts)

	// Plan + banner.
	fmt.Fprintf(opts.Out, "\nAction:    rename backend %q → %q on WitwaveAgent %q in %s\n",
		opts.OldName, opts.NewName, opts.Agent, opts.Namespace)
	fmt.Fprintf(opts.Out, "  CR:     spec.backends[].name + gitMappings + inline backend.yaml\n")
	switch {
	case !opts.RepoRename:
		fmt.Fprintf(opts.Out, "  Repo:   skipped (--no-repo-rename)\n")
	case repoSync == nil:
		fmt.Fprintf(opts.Out, "  Repo:   skipped (no gitSync configured on this agent)\n")
	case repoRenameScope.srcPath == "":
		fmt.Fprintf(opts.Out, "  Repo:   skipped (ambiguous gitSync configuration)\n")
	default:
		fmt.Fprintf(opts.Out, "  Repo:   %s/%s/ → %s/%s/  on %s (branch %s)\n",
			repoRenameScope.repoDisplay, opts.OldName,
			repoRenameScope.repoDisplay, opts.NewName,
			repoRenameScope.repoURL, repoRenameScope.branch,
		)
	}
	fmt.Fprintln(opts.Out, "")

	if opts.DryRun {
		fmt.Fprintln(opts.Out, "Dry-run mode — no API calls made.")
		return nil
	}

	// --- Phase 1: CR mutation ---

	// Rename the backend entry itself.
	backends[oldIdx]["name"] = opts.NewName

	// Rewrite per-backend gitMappings' dest paths so `.<old>/` →
	// `.<new>/`. Src paths would be updated by the companion repo
	// rename below (not here — gitMappings.src tracks on-disk paths
	// inside the cloned repo, which move when the folder moves).
	if raw, ok := backends[oldIdx]["gitMappings"].([]interface{}); ok {
		for j, entry := range raw {
			m, ok := entry.(map[string]interface{})
			if !ok {
				continue
			}
			if dest, _ := m["dest"].(string); dest == fmt.Sprintf("/home/agent/.%s/", opts.OldName) {
				m["dest"] = fmt.Sprintf("/home/agent/.%s/", opts.NewName)
			}
			if src, _ := m["src"].(string); strings.HasSuffix(src, "/."+opts.OldName+"/") {
				m["src"] = strings.TrimSuffix(src, "/."+opts.OldName+"/") + "/." + opts.NewName + "/"
			}
			raw[j] = m
		}
	}
	if err := writeBackends(cr, backends); err != nil {
		return err
	}

	// Rewrite harness-level gitMappings too — they can in principle
	// include an entry targeting the old backend's folder (rare but
	// valid shape in hand-authored CRs).
	if harnessMaps, err := readHarnessGitMappings(cr); err == nil {
		for i, m := range harnessMaps {
			if src, _ := m["src"].(string); strings.HasSuffix(src, "/."+opts.OldName+"/") {
				m["src"] = strings.TrimSuffix(src, "/."+opts.OldName+"/") + "/." + opts.NewName + "/"
			}
			if dest, _ := m["dest"].(string); dest == fmt.Sprintf("/home/agent/.%s/", opts.OldName) {
				m["dest"] = fmt.Sprintf("/home/agent/.%s/", opts.NewName)
			}
			harnessMaps[i] = m
		}
		if err := writeHarnessGitMappings(cr, harnessMaps); err != nil {
			return err
		}
	}

	// Regenerate inline backend.yaml (ww-owned path only). Routing
	// defaults apply — if the user had customised routing pointing at
	// the old name we'd lose that, but the alternative (parsing + surgery)
	// is much more complex; the cheaper fix is to tell users to re-edit.
	inlineIdx, inlineEntry := findInlineBackendYAML(cr)
	gitMounted := harnessBackendYAMLGitMounted(cr)
	if inlineIdx >= 0 && !gitMounted {
		// Re-read backends now that we've renamed one.
		updated, err := readBackends(cr)
		if err != nil {
			return err
		}
		specs := make([]BackendSpec, 0, len(updated))
		for _, b := range updated {
			name, _ := b["name"].(string)
			port, _ := b["port"].(int64)
			specs = append(specs, BackendSpec{Name: name, Port: int32(port)}) //nolint:gosec // G115: port bounded by CRD schema [DefaultBackendBasePort=8001, DefaultBackendMaxPort=8050]; cannot exceed int32.
		}
		if err := rewriteInlineBackendYAML(cr, inlineIdx, inlineEntry, toMapSlice(specs)); err != nil {
			return err
		}
	}

	if _, err := updateAgentCR(ctx, dyn, cr); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "Renamed backend %q → %q on WitwaveAgent %s/%s.\n",
		opts.OldName, opts.NewName, opts.Namespace, opts.Agent)

	// --- Phase 2: repo folder move (best-effort) ---

	if !opts.RepoRename || repoSync == nil || repoRenameScope.srcPath == "" {
		if gitMounted && !opts.RepoRename {
			fmt.Fprintln(opts.Out,
				"NOTE: --no-repo-rename was set. The repo's folder and backend.yaml still "+
					"use the old name; content will not re-sync until you rename them manually.")
		}
		return nil
	}
	// Build the post-rename backend list so the repo's backend.yaml
	// can be regenerated alongside the folder move.
	postRename, err := readBackends(cr)
	if err != nil {
		return err
	}
	renamedSpecs := make([]BackendSpec, 0, len(postRename))
	for _, b := range postRename {
		name, _ := b["name"].(string)
		port, _ := b["port"].(int64)
		renamedSpecs = append(renamedSpecs, BackendSpec{Name: name, Port: int32(port)}) //nolint:gosec // G115: port bounded by CRD schema [DefaultBackendBasePort=8001, DefaultBackendMaxPort=8050]; cannot exceed int32.
	}

	if err := renameRepoFolder(ctx, opts, repoRenameScope, renamedSpecs); err != nil {
		// Don't fail the whole command — the CR is already updated, and
		// losing the repo rename is recoverable via manual git operations.
		fmt.Fprintf(opts.Out, "WARNING: repo-side rename failed: %v\n", err)
		fmt.Fprintf(opts.Out, "         Rename manually: git -C <clone> mv %s/.%s %s/.%s && push\n",
			repoRenameScope.agentDir, opts.OldName, repoRenameScope.agentDir, opts.NewName)
		return nil
	}
	return nil
}

// toMapSlice wraps a []BackendSpec in the map shape readBackends /
// writeBackends use, so rewriteInlineBackendYAML can treat rename's
// specs + remove's kept-entries identically.
func toMapSlice(specs []BackendSpec) []map[string]interface{} {
	out := make([]map[string]interface{}, 0, len(specs))
	for _, s := range specs {
		out = append(out, map[string]interface{}{
			"name": s.Name,
			"port": int64(s.Port),
		})
	}
	return out
}

// repoRenameScope captures the resolved repo-side rename parameters so
// Phase 2 doesn't have to re-derive them from the CR.
type repoRenameScope struct {
	repoURL     string
	repoDisplay string
	branch      string
	agentDir    string // repo-relative path up to but not including the backend folder, e.g. ".agents/consensus"
	srcPath     string // full old backend path inside the repo (same prefix + .oldName)
	dstPath     string // full new backend path
}

// decideRepoRenameScope inspects the CR and returns the scope of repo
// work plus the gitSync entry to operate against. Returns (nil, zero)
// when repo work isn't applicable.
func decideRepoRenameScope(cr *unstructured.Unstructured, opts BackendRenameOptions) (map[string]interface{}, repoRenameScope) {
	syncs, err := readGitSyncs(cr)
	if err != nil || len(syncs) == 0 {
		return nil, repoRenameScope{}
	}
	if len(syncs) > 1 {
		// Ambiguous — zero srcPath signals "skip the repo phase."
		return syncs[0], repoRenameScope{}
	}
	sync := syncs[0]

	// Find the harness mapping's src to back out the agent dir
	// (".agents/<agent>/" or ".agents/<group>/<agent>/"). Harness
	// mapping's src convention is `<agentDir>/.witwave/`.
	harnessMaps, _ := readHarnessGitMappings(cr)
	var agentDir string
	for _, m := range harnessMaps {
		src, _ := m["src"].(string)
		if strings.HasSuffix(src, "/.witwave/") {
			agentDir = strings.TrimSuffix(src, "/.witwave/")
			break
		}
	}
	if agentDir == "" {
		return sync, repoRenameScope{}
	}

	repoURL, _ := sync["repo"].(string)
	branch, _ := sync["ref"].(string)
	if branch == "" {
		branch = "main"
	}
	ref, refErr := parseRepoRef(repoURL)
	display := repoURL
	if refErr == nil {
		display = ref.Display
	}
	return sync, repoRenameScope{
		repoURL:     repoURL,
		repoDisplay: display,
		branch:      branch,
		agentDir:    agentDir,
		srcPath:     agentDir + "/." + opts.OldName,
		dstPath:     agentDir + "/." + opts.NewName,
	}
}

// renameRepoFolder performs the repo-side move: clone, git mv, commit,
// push. Uses the same auth resolution as scaffold (env tokens → gh auth
// → git credential helper → ssh agent). When the expected old folder
// doesn't exist in the repo, returns a specific "already renamed"
// signal rather than a failure so the caller can log it as informative
// rather than a warning.
func renameRepoFolder(ctx context.Context, opts BackendRenameOptions, scope repoRenameScope, renamedBackends []BackendSpec) error {
	ref, err := parseRepoRef(scope.repoURL)
	if err != nil {
		return fmt.Errorf("parse repo %q: %w", scope.repoURL, err)
	}
	auth, err := resolveGitAuth(ref)
	if err != nil {
		return fmt.Errorf("resolve git auth: %w", err)
	}

	tmp, err := os.MkdirTemp("", fmt.Sprintf("ww-backend-rename-%s-", opts.Agent))
	if err != nil {
		return fmt.Errorf("temp dir: %w", err)
	}
	defer os.RemoveAll(tmp)

	repo, _, err := cloneOrInit(ctx, tmp, ref, scope.branch, auth, opts.Out)
	if err != nil {
		return err
	}

	oldAbs := filepath.Join(tmp, filepath.FromSlash(scope.srcPath))
	newAbs := filepath.Join(tmp, filepath.FromSlash(scope.dstPath))
	if _, err := os.Stat(oldAbs); os.IsNotExist(err) {
		fmt.Fprintf(opts.Out, "Repo folder %q not present — nothing to rename on the repo side.\n", scope.srcPath)
		return nil
	}
	if _, err := os.Stat(newAbs); err == nil {
		return fmt.Errorf("destination %q already exists; refusing to overwrite", scope.dstPath)
	}

	// Use shell `git -C` for the mv so git's own rename-detection
	// drives the on-disk layout + index state. go-git's worktree has
	// no native rename; an os.Rename + wt.Remove + wt.Add gets the
	// same result but lacks git's similarity-index signal in the
	// commit.
	cmd := exec.CommandContext(ctx, "git", "-C", tmp, "mv", scope.srcPath, scope.dstPath)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git mv %s %s: %w (output: %s)", scope.srcPath, scope.dstPath, err, strings.TrimSpace(string(out)))
	}

	// Rewrite the repo's .witwave/backend.yaml so agents: lists the new
	// names. Without this the harness (which reads the gitSync-mounted
	// copy when gitOps is wired) would still try to dial the old
	// backend id. Staged separately from the git mv so the commit
	// captures both operations atomically.
	backendYAMLPath := filepath.Join(tmp, filepath.FromSlash(scope.agentDir), ".witwave", "backend.yaml")
	if _, statErr := os.Stat(backendYAMLPath); statErr == nil {
		updated := renderBackendYAML(renamedBackends)
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
		msg = fmt.Sprintf("Rename backend %s → %s for agent %s", opts.OldName, opts.NewName, opts.Agent)
	}
	msg += "\n\nRenamed-by: ww agent backend rename\n"

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

	if err := pushBranch(ctx, repo, scope.branch, auth, opts.Out); err != nil {
		return err
	}
	return nil
}
