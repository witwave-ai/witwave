package cmd

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/witwave-ai/witwave/clients/ww/internal/agent"
	"github.com/witwave-ai/witwave/clients/ww/internal/config"
	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
)

// agentFlags carries the namespace flag shared across every `ww agent *`
// subcommand. Per DESIGN.md KC-6 (namespace-per-subtree) + NS-1 (default
// to context's namespace) + NS-2 (always print resolved ns).
//
// Cluster-identity flags (--kubeconfig, --context) live on the root
// command per DESIGN.md KC-5 and reach us via K8sFromCtx.
type agentFlags struct {
	namespace     string
	allNamespaces bool

	// Mutating-command flags. Not every subcommand wires both — see
	// bindMutatingAgentFlags below.
	assumeYes bool
	dryRun    bool
}

func bindAgentFlags(cmd *cobra.Command, f *agentFlags) {
	cmd.PersistentFlags().StringVarP(&f.namespace, "namespace", "n", "",
		fmt.Sprintf("Namespace for the agent (defaults to the kubeconfig context's namespace, then %q)", agent.DefaultAgentNamespace))
}

func bindAgentMutatingFlags(cmd *cobra.Command, f *agentFlags) {
	cmd.Flags().BoolVarP(&f.assumeYes, "yes", "y", false,
		"Skip the preflight confirmation prompt (or set WW_ASSUME_YES=true)")
	cmd.Flags().BoolVar(&f.dryRun, "dry-run", false,
		"Print the plan and exit without applying any changes")
}

// resolveTarget runs the kubeconfig loader and returns the populated
// Target + REST config. Cluster-identity flags come from the root
// command via K8sFromCtx (DESIGN.md KC-5); namespace is the agent
// subtree's own persistent flag (KC-6), defaulted via
// agent.ResolveNamespace when the caller left -n empty.
func (f *agentFlags) resolveTarget(ctx context.Context) (*k8s.Target, *k8s.Resolver, error) {
	kc := K8sFromCtx(ctx)
	r, err := k8s.NewResolver(k8s.Options{
		KubeconfigPath: kc.Kubeconfig,
		Context:        kc.Context,
		// Pass the raw flag value; the resolver hydrates the context
		// namespace for us when this is empty. We then re-resolve for
		// display via agent.ResolveNamespace so the user sees which
		// namespace we actually picked.
		Namespace: f.namespace,
	})
	if err != nil {
		return nil, nil, err
	}
	return r.Target(), r, nil
}

// logAndResolveNamespace resolves the namespace from flag / context /
// ww default, prints a one-line notice to stdout when the flag was
// omitted (so the user sees where the CR actually landed), and returns
// the resolved value. The note distinguishes the source so
// "(from kubeconfig context)" never misrepresents a ww-default fallback.
// Keeps DESIGN.md NS-2 (always echo the resolved namespace) consistent
// across every `ww agent *` verb.
func logAndResolveNamespace(flagValue, contextNS string) string {
	ns, source := agent.ResolveNamespaceWithSource(flagValue, contextNS)
	if source == agent.NamespaceFromFlag {
		return ns
	}
	var why string
	switch source {
	case agent.NamespaceFromContext:
		why = "from kubeconfig context"
	case agent.NamespaceFromDefault:
		why = "ww default"
	}
	fmt.Fprintf(os.Stdout, "Using namespace: %s (%s)\n", ns, why)
	return ns
}

// newAgentCmd is the parent command for `ww agent *`.
func newAgentCmd() *cobra.Command {
	f := &agentFlags{}
	cmd := &cobra.Command{
		Use:   "agent",
		Short: "Manage WitwaveAgent custom resources on a Kubernetes cluster",
		Long: "Create, list, inspect, and delete WitwaveAgent CRs. The witwave-operator\n" +
			"reconciles each CR into a running agent pod with harness + backend sidecars.\n\n" +
			"Prerequisite: the operator must already be installed on the target cluster\n" +
			"(see `ww operator install`). Every `ww agent *` command honours the ambient\n" +
			"kubeconfig and current-context (override via the root --kubeconfig / --context\n" +
			"flags). Use --namespace / -n to target a specific namespace; omit to use the\n" +
			"kubeconfig context's namespace (falling back to \"" + agent.DefaultAgentNamespace + "\").\n\n" +
			"Use `ww agent create --create-namespace` to provision the namespace on first use.",
	}
	bindAgentFlags(cmd, f)

	cmd.AddCommand(newAgentCreateCmd(f))
	cmd.AddCommand(newAgentUpgradeCmd(f))
	cmd.AddCommand(newAgentListCmd(f))
	cmd.AddCommand(newAgentStatusCmd(f))
	cmd.AddCommand(newAgentDeleteCmd(f))
	cmd.AddCommand(newAgentSendCmd(f))
	cmd.AddCommand(newAgentMetricsCmd(f))
	cmd.AddCommand(newAgentLogsCmd(f))
	cmd.AddCommand(newAgentEventsCmd(f))
	cmd.AddCommand(newAgentScaffoldCmd())
	cmd.AddCommand(newAgentStorageCmd(f))
	cmd.AddCommand(newAgentKubernetesApiAccessCmd(f))
	cmd.AddCommand(newAgentGitCmd(f))
	cmd.AddCommand(newAgentBackendCmd(f))
	cmd.AddCommand(newAgentTeamCmd(f))
	return cmd
}

// ---------------------------------------------------------------------------
// team (team membership via the witwave.ai/team label)
// ---------------------------------------------------------------------------

func newAgentTeamCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "team",
		Short: "Join, leave, list, or show agent team membership",
		Long: "Manages the `witwave.ai/team` label on WitwaveAgent CRs. The\n" +
			"operator reconciles one `witwave-manifest-<team>` ConfigMap per\n" +
			"team and mounts it into every member's pod at\n" +
			"`/home/agent/manifest.json`, so harnesses discover their teammates'\n" +
			"URLs at runtime. Agents without the team label share one\n" +
			"namespace-wide manifest.\n\n" +
			"Team membership is a pure label patch — no CRD schema change, no\n" +
			"pod restart. Members see a new peer within one operator reconcile\n" +
			"cycle (seconds).",
	}
	cmd.AddCommand(newAgentTeamJoinCmd(f))
	cmd.AddCommand(newAgentTeamLeaveCmd(f))
	cmd.AddCommand(newAgentTeamListCmd(f))
	cmd.AddCommand(newAgentTeamShowCmd(f))
	return cmd
}

func newAgentTeamJoinCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "join <agent> <team>",
		Short: "Add an agent to a team (sets witwave.ai/team=<team>)",
		Long: "Sets the `witwave.ai/team` label on the named WitwaveAgent. The\n" +
			"operator re-reconciles the per-team manifest ConfigMap on the next\n" +
			"cycle; every member (including the newcomer) picks up the updated\n" +
			"peer list within seconds.\n\n" +
			"Idempotent when the agent is already in the target team.\n" +
			"Allowed when the agent is already in a *different* team — the\n" +
			"banner shows the transition explicitly (was → now) so the user\n" +
			"can see they're moving, not joining.",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentTeamJoin(cmd.Context(), f, args[0], args[1])
		},
	}
	bindAgentMutatingFlags(cmd, f)
	return cmd
}

func runAgentTeamJoin(ctx context.Context, f *agentFlags, name, team string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	_ = target
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.TeamJoin(ctx, cfg, agent.TeamJoinOptions{
		Agent:     name,
		Namespace: ns,
		Team:      team,
		AssumeYes: assumeYes,
		DryRun:    f.dryRun,
		Out:       os.Stdout,
		In:        os.Stdin,
	})
}

func newAgentTeamLeaveCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "leave <agent>",
		Short: "Remove an agent from its team (drops witwave.ai/team label)",
		Long: "Removes the `witwave.ai/team` label from the agent. The operator\n" +
			"reconciles the agent back into the namespace-wide manifest (the\n" +
			"bucket every label-less agent shares). No-ops cleanly when the\n" +
			"agent wasn't in a team to begin with.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentTeamLeave(cmd.Context(), f, args[0])
		},
	}
	bindAgentMutatingFlags(cmd, f)
	return cmd
}

func runAgentTeamLeave(ctx context.Context, f *agentFlags, name string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.TeamLeave(ctx, cfg, agent.TeamLeaveOptions{
		Agent:     name,
		Namespace: ns,
		AssumeYes: assumeYes,
		DryRun:    f.dryRun,
		Out:       os.Stdout,
		In:        os.Stdin,
	})
}

func newAgentTeamListCmd(f *agentFlags) *cobra.Command {
	var team string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List teams in a namespace (all teams by default, one team with --team)",
		Long: "Enumerates every team label in the namespace and the WitwaveAgent\n" +
			"members in each. Without --team, prints one section per team, sorted\n" +
			"by team name. With --team <name>, narrows to a single team and prints\n" +
			"just its members. Untagged agents (no team label) are excluded from\n" +
			"both modes — they're surfaced by `ww agent list`, not by this verb.\n\n" +
			"Namespace resolution follows DESIGN.md NS-2: --namespace > kubeconfig\n" +
			"context > ww default. The resolved namespace is always echoed first.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentTeamList(cmd.Context(), f, team)
		},
	}
	cmd.Flags().StringVar(&team, "team", "",
		"Only list members of this team (default: list every team in the namespace)")
	return cmd
}

func runAgentTeamList(ctx context.Context, f *agentFlags, team string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	return agent.TeamList(ctx, cfg, agent.TeamListOptions{
		Namespace: ns,
		Team:      team,
		Out:       os.Stdout,
	})
}

func newAgentTeamShowCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "show <agent>",
		Short: "Show which team an agent is in + its teammates",
		Long: "Looks up the WitwaveAgent <agent> in the resolved namespace, reads\n" +
			"its team label, and prints the team name plus every other agent in\n" +
			"that team. Errors clearly when the agent isn't in any team (no\n" +
			"label) — distinct from the more general `ww agent show <agent>` which\n" +
			"prints full spec/status regardless of team membership.\n\n" +
			"Namespace resolution follows DESIGN.md NS-2: --namespace > kubeconfig\n" +
			"context > ww default. The resolved namespace is always echoed first.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentTeamShow(cmd.Context(), f, args[0])
		},
	}
	return cmd
}

func runAgentTeamShow(ctx context.Context, f *agentFlags, name string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	return agent.TeamShow(ctx, cfg, agent.TeamShowOptions{
		Agent:     name,
		Namespace: ns,
		Out:       os.Stdout,
	})
}

// ---------------------------------------------------------------------------
// backend (attach / detach individual backends on an existing agent)
// ---------------------------------------------------------------------------

func newAgentBackendCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "backend",
		Short: "Add, remove, or rename backends on an existing WitwaveAgent",
		Long: "Manipulates the `spec.backends[]` array on a running agent's CR.\n" +
			"Covers the full lifecycle:\n\n" +
			"  ww agent backend add    <agent> <name>[:<type>]   append a backend\n" +
			"  ww agent backend remove <agent> <name>            drop a backend\n" +
			"  ww agent backend rename <agent> <old> <new>       rename a backend\n\n" +
			"Each verb updates the CR, regenerates the inline backend.yaml when\n" +
			"ww owns it, and — for agents with a single gitSync wired — optionally\n" +
			"mirrors the change into the repo's `.agents/<…>/.<name>/` folder.",
	}
	cmd.AddCommand(newAgentBackendAddCmd(f))
	cmd.AddCommand(newAgentBackendRemoveCmd(f))
	cmd.AddCommand(newAgentBackendRenameCmd(f))
	return cmd
}

func newAgentBackendAddCmd(f *agentFlags) *cobra.Command {
	var (
		authProfile   string
		secretFromEnv string
		authSecret    string
		authSet       []string
		noRepoFolder  bool
		commitMessage string
	)
	cmd := &cobra.Command{
		Use:   "add <agent> <name>[:<type>]",
		Short: "Add a backend to an existing WitwaveAgent",
		Long: "Appends a backend to an existing agent's `spec.backends[]`. Port\n" +
			"is auto-assigned to the first free slot in the 8001..8050 range.\n" +
			"When the agent has a single gitSync wired, also scaffolds\n" +
			"`.agents/<…>/.<name>/agent-card.md` (and the behavioural-\n" +
			"instructions stub for LLM backends) to the repo — same layout\n" +
			"`ww agent scaffold` produces. Pass --no-repo-folder to skip the\n" +
			"repo side and leave the CR change in-cluster only.\n\n" +
			"Backend spec follows the same shape as `ww agent create --backend`:\n" +
			"  <type>         — name = type (single-backend shortcut)\n" +
			"  <name>:<type>  — explicit name + type pair (for multiple of a type)\n\n" +
			"Credentials: pick one of --auth / --backend-secret-from-env / --auth-secret.\n" +
			"Omit all three when the backend type needs no credentials (echo).\n" +
			"LLM backends added without credentials will start the pod but error\n" +
			"on first request — a yellow warning appears in the preflight banner.",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			specs, err := agent.ParseBackendSpecs([]string{args[1]})
			if err != nil {
				return err
			}
			// ParseBackendSpecs stamps port 8001 for index 0 (right
			// for `create` where ports come from scratch, wrong for
			// `backend add` where the agent already owns backends).
			// Clear it so BackendAdd's nextFreeBackendPort picks the
			// first slot the existing CR isn't using.
			specs[0].Port = 0
			// Build the auth resolver — flags here drop the <backend>=
			// prefix (and --auth-set drops the <backend>: prefix)
			// because the backend is already named positionally.
			auth, err := resolveSingleBackendAuth(specs[0].Name, authProfile, secretFromEnv, authSecret, authSet)
			if err != nil {
				return err
			}
			return runAgentBackendAdd(cmd.Context(), f, args[0], specs[0], auth, !noRepoFolder, commitMessage)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringVar(&authProfile, "auth", "",
		fmt.Sprintf("Named auth profile (e.g. `oauth`, `api-key`). Known: %s", agent.KnownCredentialProfiles()))
	cmd.Flags().StringVar(&secretFromEnv, "backend-secret-from-env", "",
		"Mint a K8s Secret from arbitrary env vars. Form: <VAR1>[,VAR2,...]. Secret keys match names verbatim.")
	cmd.Flags().StringVar(&authSecret, "auth-secret", "",
		"Reference an existing K8s Secret (verified, not modified)")
	cmd.Flags().StringArrayVar(&authSet, "auth-set", nil,
		"Mint a Secret with literal KEY=VALUE pairs. Repeatable. Form: <KEY>=<VALUE>. "+
			"SECURITY: values land in shell history + ps output — for production tokens "+
			"prefer --auth-secret or --backend-secret-from-env.")
	cmd.Flags().BoolVar(&noRepoFolder, "no-repo-folder", false,
		"Skip the repo-side `.agents/<…>/.<name>/` scaffold (CR-only change)")
	cmd.Flags().StringVar(&commitMessage, "commit-message", "",
		"Custom commit message for the repo-side scaffold (default: \"Add backend <name> for agent <agent>\")")
	return cmd
}

// resolveSingleBackendAuth converts the four flat auth flags on
// `backend add` into a single BackendAuthResolver. At most one mode
// may be set — they're mutually exclusive (with the exception of
// --auth-set, which is repeatable but counts as one mode regardless
// of how many KEY=VALUE pairs were passed). All-empty is the
// legitimate "no credentials needed" case for echo backends.
func resolveSingleBackendAuth(backend, profile, fromEnv, secret string, set []string) (agent.BackendAuthResolver, error) {
	modes := 0
	if profile != "" {
		modes++
	}
	if fromEnv != "" {
		modes++
	}
	if secret != "" {
		modes++
	}
	if len(set) > 0 {
		modes++
	}
	if modes > 1 {
		return agent.BackendAuthResolver{}, fmt.Errorf(
			"pick at most one of --auth / --backend-secret-from-env / --auth-secret / --auth-set")
	}
	switch {
	case profile != "":
		return agent.BackendAuthResolver{Backend: backend, Mode: agent.BackendAuthProfile, Profile: profile}, nil
	case fromEnv != "":
		return agent.BackendAuthResolver{Backend: backend, Mode: agent.BackendAuthFromEnv, EnvVars: strings.Split(fromEnv, ",")}, nil
	case secret != "":
		return agent.BackendAuthResolver{Backend: backend, Mode: agent.BackendAuthExistingSecret, ExistingSecret: secret}, nil
	case len(set) > 0:
		// --auth-set on `backend add` drops the <backend>: prefix —
		// the backend's already named positionally — so each entry
		// is just KEY=VALUE. Reuse the inner-half of the create
		// parser so the error messages stay uniform across verbs.
		inline := make(map[string]string, len(set))
		for _, raw := range set {
			key, value, err := agent.SplitInlineKV(raw, "--auth-set")
			if err != nil {
				return agent.BackendAuthResolver{}, err
			}
			if existing, dup := inline[key]; dup {
				return agent.BackendAuthResolver{}, fmt.Errorf(
					"--auth-set: key %q given twice (first=%q, second=%q) — pick one",
					key, existing, value,
				)
			}
			inline[key] = value
		}
		return agent.BackendAuthResolver{Backend: backend, Mode: agent.BackendAuthInline, Inline: inline}, nil
	}
	return agent.BackendAuthResolver{Backend: backend, Mode: agent.BackendAuthNone}, nil
}

func runAgentBackendAdd(ctx context.Context, f *agentFlags, name string, spec agent.BackendSpec, auth agent.BackendAuthResolver, repoFolder bool, commitMessage string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	_ = target
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.BackendAdd(ctx, cfg, agent.BackendAddOptions{
		Agent:         name,
		Namespace:     ns,
		Backend:       spec,
		Auth:          auth,
		RepoFolder:    repoFolder,
		CommitMessage: commitMessage,
		CLIVersion:    Version,
		AssumeYes:     assumeYes,
		DryRun:        f.dryRun,
		Out:           os.Stdout,
		In:            os.Stdin,
	})
}

func newAgentBackendRenameCmd(f *agentFlags) *cobra.Command {
	var (
		noRepoRename  bool
		commitMessage string
	)
	cmd := &cobra.Command{
		Use:   "rename <agent> <old-name> <new-name>",
		Short: "Rename a backend on a WitwaveAgent (both the CR and the repo folder)",
		Long: "Renames a backend in three places, atomically:\n\n" +
			"1. `spec.backends[<N>].name` on the CR.\n" +
			"2. Every harness + per-backend `gitMappings[]` entry whose `src` or\n" +
			"   `dest` path references the old name.\n" +
			"3. When ww owns the inline backend.yaml (spec.config[0], scaffolded\n" +
			"   at create time), regenerates it so `agents:` lists the new name\n" +
			"   and all routing entries targeting the old name repoint at the new.\n\n" +
			"Repo-side rename: when exactly one gitSync is wired AND --no-repo-\n" +
			"rename isn't set, clones the repo, `git mv`s `.agents/<…>/.<old>/`\n" +
			"to `.agents/<…>/.<new>/`, commits + pushes. Credentials inherit from\n" +
			"the system the same way scaffold does (env token → gh auth → git\n" +
			"credential helper → ssh agent).\n\n" +
			"Refuses when:\n" +
			"- old and new names match (nothing to do),\n" +
			"- new name already exists on the agent (would overwrite),\n" +
			"- new name isn't DNS-1123 compliant.\n\n" +
			"Repo-side failure (clone / push) is non-fatal: the CR rename is\n" +
			"preserved and a recovery recipe is printed.",
		Args: cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentBackendRename(cmd.Context(), f, args[0], args[1], args[2],
				!noRepoRename, commitMessage)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().BoolVar(&noRepoRename, "no-repo-rename", false,
		"Rename the CR only; leave the repo folder alone for manual editing")
	cmd.Flags().StringVar(&commitMessage, "commit-message", "",
		"Custom commit message for the repo rename "+
			"(default: \"Rename backend <old> → <new> for agent <name>\")")
	return cmd
}

func runAgentBackendRename(ctx context.Context, f *agentFlags, name, oldName, newName string, repoRename bool, commitMessage string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.BackendRename(ctx, cfg, agent.BackendRenameOptions{
		Agent:         name,
		Namespace:     ns,
		OldName:       oldName,
		NewName:       newName,
		RepoRename:    repoRename,
		CommitMessage: commitMessage,
		AssumeYes:     assumeYes,
		DryRun:        f.dryRun,
		Out:           os.Stdout,
		In:            os.Stdin,
	})
}

func newAgentBackendRemoveCmd(f *agentFlags) *cobra.Command {
	var (
		removeRepoFolder bool
		commitMessage    string
	)
	cmd := &cobra.Command{
		Use:   "remove <agent> <backend-name>",
		Short: "Remove a backend from a WitwaveAgent (and optionally its repo folder)",
		Long: "Drops the named backend from the agent's `spec.backends[]` array.\n\n" +
			"When backend.yaml is inline (ww-managed via spec.config[0], the default\n" +
			"for agents created via `ww agent create`), it is regenerated to exclude\n" +
			"the removed backend and route every concern to the new primary (first\n" +
			"remaining backend). Any user-customised routing in the inline file is\n" +
			"lost; re-edit after the remove lands.\n\n" +
			"When backend.yaml is gitSync-managed (a harness-level gitMapping mounts\n" +
			".witwave/ from the repo), spec.config is left alone. Pass --remove-repo-\n" +
			"folder to automate the repo-side cleanup: clone, git rm -r the\n" +
			"`.agents/<…>/.<backend>/` folder, rewrite backend.yaml to exclude the\n" +
			"removed backend, commit + push. Without the flag, the repo's copy is\n" +
			"preserved and the user is responsible for editing it.\n\n" +
			"Refuses to remove the last backend — the CRD requires at least one.\n" +
			"Operator reconciles the pod to drop the sidecar on next reconcile.",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentBackendRemove(cmd.Context(), f, args[0], args[1],
				removeRepoFolder, commitMessage)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().BoolVar(&removeRepoFolder, "remove-repo-folder", false,
		"Also remove the `.agents/<…>/.<backend>/` folder from the gitSync repo "+
			"and rewrite backend.yaml to drop references to the backend")
	cmd.Flags().StringVar(&commitMessage, "commit-message", "",
		"Custom commit message for the repo removal "+
			"(default: \"Remove backend <name> for agent <agent>\")")
	return cmd
}

func runAgentBackendRemove(ctx context.Context, f *agentFlags, name, backendName string, removeRepoFolder bool, commitMessage string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.BackendRemove(ctx, cfg, agent.BackendRemoveOptions{
		Agent:            name,
		Namespace:        ns,
		BackendName:      backendName,
		RemoveRepoFolder: removeRepoFolder,
		CommitMessage:    commitMessage,
		AssumeYes:        assumeYes,
		DryRun:           f.dryRun,
		Out:              os.Stdout,
		In:               os.Stdin,
	})
}

// ---------------------------------------------------------------------------
// git (attach / detach / list gitSync on a WitwaveAgent)
// ---------------------------------------------------------------------------

func newAgentGitCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "git",
		Short: "Attach, detach, or list gitSync repos on a WitwaveAgent",
		Long: "Wires a running WitwaveAgent to a git repository via the operator's\n" +
			"gitSync sidecar. Content under `.agents/<name>/.witwave/` and\n" +
			"`.agents/<name>/.<backend>/` lands in the agent pod on each sync\n" +
			"interval — typically the repo produced by `ww agent scaffold`.\n\n" +
			"Auth posture — three ways to provide a credential Secret:\n\n" +
			"  --auth-secret <name>     reference an existing K8s Secret (production)\n" +
			"  --auth-from-gh           mint one from `gh auth token` (dev laptops)\n" +
			"  --secret-from-env <VAR>  mint one from the named env var (CI/CD / secret manager)\n\n" +
			"Public repos need no auth flag. Secrets minted by ww carry an\n" +
			"`app.kubernetes.io/managed-by: ww` label so detach + delete can\n" +
			"distinguish ww-created Secrets from hand-authored ones.",
	}
	cmd.AddCommand(newAgentGitAddCmd(f))
	cmd.AddCommand(newAgentGitListCmd(f))
	cmd.AddCommand(newAgentGitRemoveCmd(f))
	return cmd
}

func newAgentGitAddCmd(f *agentFlags) *cobra.Command {
	var (
		repo           string
		repoPath       string
		group          string
		branch         string
		period         string
		syncName       string
		authSecret     string
		authFromGH     bool
		secretFromEnv  string
		authSecretName string
	)
	cmd := &cobra.Command{
		Use:   "add <agent>",
		Short: "Attach a gitSync to a WitwaveAgent (repo content → agent pod)",
		Long: "Patches the existing WitwaveAgent CR to add (or replace) a gitSync\n" +
			"entry plus the conventional harness + per-backend gitMappings.\n" +
			"Idempotent: re-running with the same --sync-name updates every\n" +
			"field ww owns and leaves unrelated mappings untouched.\n\n" +
			"--repo-path defaults to `.agents/<agent>/` (or `.agents/<group>/<agent>/`\n" +
			"when --group is set), matching the layout produced by `ww agent scaffold`.\n" +
			"Override explicitly for non-standard repo layouts.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentGitAdd(cmd.Context(), f, args[0], agent.GitAddOptions{
				Repo:      repo,
				RepoPath:  repoPath,
				Group:     group,
				Branch:    branch,
				Period:    period,
				SyncName:  syncName,
				AssumeYes: f.assumeYes,
				DryRun:    f.dryRun,
				Out:       os.Stdout,
				In:        os.Stdin,
				Auth: agent.GitAuthResolver{
					Mode:           chooseAuthMode(authSecret, authFromGH, secretFromEnv),
					ExistingSecret: authSecret,
					EnvVar:         secretFromEnv,
					SecretName:     authSecretName,
				},
			})
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringVar(&repo, "repo", "",
		"Remote repo (owner/repo, host/owner/repo, full URL, or git@host:owner/repo) — required")
	_ = cmd.MarkFlagRequired("repo")
	cmd.Flags().StringVar(&repoPath, "repo-path", "",
		"Path within the repo (default: `.agents/<agent>/` or `.agents/<group>/<agent>/`)")
	cmd.Flags().StringVar(&group, "group", "",
		"Group segment used to derive --repo-path when it's unset (mirrors scaffold's --group)")
	cmd.Flags().StringVar(&branch, "branch", "",
		"Branch / tag / commit to sync (default: remote HEAD)")
	cmd.Flags().StringVar(&period, "period", agent.DefaultGitPeriod,
		"Sync interval (e.g. 30s, 1m, 5m)")
	cmd.Flags().StringVar(&syncName, "sync-name", "",
		"Name for the gitSyncs[] entry (default: sanitised basename of --repo, e.g. "+
			"`owner/my.repo` → `my-repo`). Pick explicitly when wiring two gitSyncs "+
			"with the same repo basename or two branches of the same repo")
	cmd.Flags().StringVar(&authSecret, "auth-secret", "",
		"Reference an existing K8s Secret with GITSYNC_USERNAME / GITSYNC_PASSWORD")
	cmd.Flags().BoolVar(&authFromGH, "auth-from-gh", false,
		"Mint a K8s Secret from `gh auth token` (reads gh's current session)")
	cmd.Flags().StringVar(&secretFromEnv, "secret-from-env", "",
		"Mint a K8s Secret from a named env var (e.g. GITHUB_TOKEN)")
	cmd.Flags().StringVar(&authSecretName, "auth-secret-name", "",
		"Name to use when minting a Secret (default: <agent>-git-credentials)")
	return cmd
}

// chooseAuthMode collapses the three mutually-exclusive auth flags into
// a single GitAuthMode. Exactly one may be set; more than one is a
// usage error surfaced at validation time.
func chooseAuthMode(secret string, fromGH bool, env string) agent.GitAuthMode {
	switch {
	case secret != "":
		return agent.GitAuthExistingSecret
	case fromGH:
		return agent.GitAuthFromGH
	case env != "":
		return agent.GitAuthFromEnv
	}
	return agent.GitAuthNone
}

func runAgentGitAdd(ctx context.Context, f *agentFlags, name string, opts agent.GitAddOptions) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	// Validate mutual exclusivity up-front so users get a crisp error
	// rather than having the auth resolver pick one silently.
	if err := assertOneAuthMode(opts.Auth); err != nil {
		return err
	}
	opts.Agent = name
	opts.Namespace = ns
	return agent.GitAdd(ctx, cfg, opts)
}

func assertOneAuthMode(auth agent.GitAuthResolver) error {
	set := 0
	if auth.ExistingSecret != "" {
		set++
	}
	if auth.Mode == agent.GitAuthFromGH {
		set++
	}
	if auth.EnvVar != "" {
		set++
	}
	if set > 1 {
		return fmt.Errorf("pick at most one of --auth-secret / --auth-from-gh / --secret-from-env")
	}
	return nil
}

func newAgentGitListCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "list <agent>",
		Short: "Show the gitSyncs + mappings configured on a WitwaveAgent",
		Long: "Prints the agent's `spec.gitSyncs[]` array as a flat table: each\n" +
			"row is one git repo subscription, its destination mount path, and\n" +
			"the agent-folder mapping (if any) that controls where the agent's\n" +
			"backends + identity files render inside the cloned tree. Reads\n" +
			"the CR straight from the apiserver — no harness round-trip — so\n" +
			"works even when the agent pod is offline.\n\n" +
			"Namespace resolution follows DESIGN.md NS-2: --namespace > kubeconfig\n" +
			"context > ww default. The resolved namespace is always echoed first.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentGitList(cmd.Context(), f, args[0])
		},
	}
	return cmd
}

func runAgentGitList(ctx context.Context, f *agentFlags, name string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	if f.namespace == "" {
		fmt.Fprintln(os.Stdout)
	}
	return agent.GitList(ctx, cfg, agent.GitListOptions{
		Agent:     name,
		Namespace: ns,
		Out:       os.Stdout,
	})
}

func newAgentGitRemoveCmd(f *agentFlags) *cobra.Command {
	var (
		syncName     string
		deleteSecret bool
	)
	cmd := &cobra.Command{
		Use:   "remove <agent>",
		Short: "Detach a gitSync from a WitwaveAgent",
		Long: "Removes the named gitSyncs[] entry and every harness + per-backend\n" +
			"gitMapping tied to it. Mappings tied to other gitSyncs are preserved.\n\n" +
			"By default the ww-managed credential Secret is kept so a later\n" +
			"`ww agent git add` can re-attach without re-resolving auth. Pass\n" +
			"--delete-secret to remove it (user-created Secrets under the same\n" +
			"name are always preserved — the managed-by label gates deletion).",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentGitRemove(cmd.Context(), f, args[0], syncName, deleteSecret)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringVar(&syncName, "sync-name", "",
		"Name of the gitSyncs[] entry to detach (default: the agent's only "+
			"gitSync when exactly one is configured; required when 2+)")
	cmd.Flags().BoolVar(&deleteSecret, "delete-secret", false,
		"Also delete the ww-managed credential Secret for this sync")
	return cmd
}

func runAgentGitRemove(ctx context.Context, f *agentFlags, name, syncName string, deleteSecret bool) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	return agent.GitRemove(ctx, cfg, agent.GitRemoveOptions{
		Agent:        name,
		Namespace:    ns,
		SyncName:     syncName,
		DeleteSecret: deleteSecret,
		AssumeYes:    f.assumeYes,
		DryRun:       f.dryRun,
		Out:          os.Stdout,
		In:           os.Stdin,
	})
}

// ---------------------------------------------------------------------------
// scaffold
// ---------------------------------------------------------------------------
//
// scaffold is deliberately *not* a cluster-touching verb — it materialises
// a ww-conformant agent directory structure on a remote git repo so a
// future `ww agent git add` can wire a deployed agent to that directory.
// It therefore doesn't share the agentFlags parent (which carries -n +
// cluster preflight); it owns its own flag set.

func newAgentScaffoldCmd() *cobra.Command {
	var (
		repo          string
		group         string
		backends      []string
		branch        string
		commitMessage string
		cloneTo       string
		noPush        bool
		dryRun        bool
		force         bool
		noHeartbeat   bool
	)
	cmd := &cobra.Command{
		Use:   "scaffold <name>",
		Short: "Create a ww-conformant agent directory on a remote git repo",
		Long: "Scaffolds the directory structure for a new agent on a remote git repo so it\n" +
			"can later be wired up via gitSync. Default layout:\n\n" +
			"  <repo>/.agents/<name>/\n" +
			"    ├── README.md\n" +
			"    ├── .witwave/backend.yaml\n" +
			"    └── .<backend>/\n" +
			"        ├── agent-card.md\n" +
			"        └── <CLAUDE|AGENTS|GEMINI>.md   (LLM backends only)\n\n" +
			"Pass --group to nest under `.agents/<group>/<name>/`. The scaffolder uses\n" +
			"your machine's git credentials (credential helper, SSH agent, or\n" +
			"GITHUB_TOKEN env) so whatever `git push` against this remote already\n" +
			"works — `ww agent scaffold` works too. Empty remote repos are\n" +
			"supported: the scaffolder initialises the first commit and pushes\n" +
			"with --set-upstream semantics.\n\n" +
			"Dormant subsystems (heartbeat, jobs, tasks, triggers, continuations,\n" +
			"webhooks) are NOT pre-created — per DESIGN.md SUB-1..4, the absence of\n" +
			"their content IS how you express \"this agent doesn't use that feature.\"\n" +
			"Future `ww agent add-job` / `add-task` verbs will materialise them on\n" +
			"demand.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			specs, err := agent.ParseBackendSpecs(backends)
			if err != nil {
				return err
			}
			return runAgentScaffold(cmd.Context(), args[0], agent.ScaffoldOptions{
				Repo:          repo,
				Group:         group,
				Backends:      specs,
				Branch:        branch,
				CommitMessage: commitMessage,
				CloneTo:       cloneTo,
				NoPush:        noPush,
				DryRun:        dryRun,
				Force:         force,
				NoHeartbeat:   noHeartbeat,
				CLIVersion:    Version,
				Out:           os.Stdout,
			})
		},
	}
	cmd.Flags().StringVar(&repo, "repo", "",
		"Remote repo (owner/repo, github.com/owner/repo, full URL, or git@host:owner/repo) — required")
	_ = cmd.MarkFlagRequired("repo")
	cmd.Flags().StringVar(&group, "group", "",
		"Optional group segment — `.agents/<group>/<name>/` when set; flat `.agents/<name>/` otherwise")
	cmd.Flags().StringArrayVar(&backends, "backend", nil,
		fmt.Sprintf(
			"Backend to scaffold. Repeatable. Two shapes accepted:\n"+
				"  `<type>`        — name = type (single-backend shortcut)\n"+
				"  `<name>:<type>` — explicit name + type pair (for multi-backend agents)\n"+
				"Valid types: %s. Default when omitted: one %s backend.\n"+
				"Example: --backend claude --backend openai  (multi-model consensus)\n"+
				"Example: --backend echo-1:echo --backend echo-2:echo  (two echo backends)",
			strings.Join(agent.KnownBackends(), ", "), agent.DefaultBackend,
		))
	cmd.Flags().StringVar(&branch, "branch", "",
		"Git branch to push to. Unspecified: detects the remote's default "+
			"(via HEAD symref) and falls back to \"main\" on empty repos")
	cmd.Flags().StringVar(&commitMessage, "commit-message", "",
		"Commit message (default: \"Scaffold agent <name>\")")
	cmd.Flags().StringVar(&cloneTo, "clone-to", "",
		"Persist the clone at this path instead of using a temp dir; directory must be empty")
	cmd.Flags().BoolVar(&noPush, "no-push", false,
		"Stop after the commit is created; don't push to origin")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false,
		"Print the plan + file list and exit without touching the remote or disk")
	cmd.Flags().BoolVar(&force, "force", false,
		"Overwrite scaffold files that have drifted from the template. "+
			"Never touches files outside the skeleton list (user-added jobs/tasks/etc. are safe either way)")
	cmd.Flags().BoolVar(&noHeartbeat, "no-heartbeat", false,
		"Skip writing .witwave/HEARTBEAT.md (scaffold defaults to an hourly heartbeat)")
	return cmd
}

func runAgentScaffold(ctx context.Context, name string, opts agent.ScaffoldOptions) error {
	opts.Name = name
	return agent.Scaffold(ctx, opts)
}

// ---------------------------------------------------------------------------
// create
// ---------------------------------------------------------------------------

func newAgentCreateCmd(f *agentFlags) *cobra.Command {
	var (
		backends        []string
		noWait          bool
		timeout         time.Duration
		createNamespace bool
		team            string
		workspaces      []string
		gitOps          string
		gitSyncs        []string
		gitMaps         []string
		gitSyncSecrets  []string
		gitSyncFromEnv  string
		persist         []string
		persistMounts   []string
		withPersistence bool
		authProfiles    []string
		secretFromEnv   []string
		authSecrets     []string
		authSet         []string
		backendEnvs     []string
		harnessEnvs     []string
		noMetrics       bool
		kubernetesAPI   string
	)
	cmd := &cobra.Command{
		Use:   "create <name>",
		Short: "Create a WitwaveAgent CR (defaults to the echo backend)",
		Long: "Creates a WitwaveAgent with one or more backend sidecars. With no flags,\n" +
			"deploys a single echo backend — a zero-dependency stub that requires no API\n" +
			"keys — so you can exercise an agent end-to-end with \"access to a Kubernetes\n" +
			"cluster and the CLI\" as the only prerequisites.\n\n" +
			"Pass --backend repeatedly to declare multiple backends:\n\n" +
			"  ww agent create consensus-agent --backend claude --backend openai\n" +
			"  ww agent create hello --backend echo-1:echo --backend echo-2:echo\n\n" +
			"Each backend's folder in the gitOps repo (and the /home/agent/.<name>/ mount\n" +
			"in the pod) is named after the backend's NAME, not its type — so two backends\n" +
			"of the same type must use the `<name>:<type>` shape to differentiate them.\n\n" +
			"Pass --workspace repeatedly to bind the agent to one or more existing\n" +
			"WitwaveWorkspaces at creation time, equivalent to a follow-up\n" +
			"`ww workspace bind`. v1alpha1 only supports same-namespace binding;\n" +
			"each workspace must already exist in the agent's namespace.\n\n" +
			"After the CR is applied, waits up to --timeout for the operator to report the\n" +
			"agent as Ready. Pass --no-wait to skip the readiness wait.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			specs, err := agent.ParseBackendSpecs(backends)
			if err != nil {
				return err
			}
			auth, err := agent.ParseBackendAuth(authProfiles, secretFromEnv, authSecrets, authSet)
			if err != nil {
				return err
			}
			envs, err := agent.ParseBackendEnvs(backendEnvs)
			if err != nil {
				return err
			}
			specs, err = agent.ApplyBackendEnvs(specs, envs)
			if err != nil {
				return err
			}
			harnessEnv, err := agent.ParseHarnessEnvs(harnessEnvs)
			if err != nil {
				return err
			}
			syncs, err := agent.ParseGitSyncs(gitSyncs)
			if err != nil {
				return err
			}
			secrets, err := agent.ParseGitSyncSecrets(gitSyncSecrets)
			if err != nil {
				return err
			}
			syncs, err = agent.ApplyGitSyncSecrets(syncs, secrets)
			if err != nil {
				return err
			}
			maps, err := agent.ParseGitMappings(gitMaps)
			if err != nil {
				return err
			}
			// --gitsync-bundle is convention-driven sugar over --gitsync + N+1
			// --gitsync-map. Expand it AFTER explicit flags are parsed and
			// PREPEND its synthesised entries so the explicit ones
			// appear later in ValidateGitFlags' duplicate-check pass —
			// duplicate (container, dest) pairs are flagged as a clear
			// "already set by --gitsync-map[N]" error against the explicit
			// override, not against the implicit convention default.
			gitOpsSpec, err := agent.ParseGitOps(gitOps)
			if err != nil {
				return err
			}
			expandSyncs, expandMaps := agent.ExpandGitOps(gitOpsSpec, specs)
			syncs = append(expandSyncs, syncs...)
			maps = append(expandMaps, maps...)
			var gitsyncEnvSpec *agent.GitSyncFromEnvSpec
			if strings.TrimSpace(gitSyncFromEnv) != "" {
				parsed, err := agent.ParseGitSyncFromEnv(gitSyncFromEnv)
				if err != nil {
					return err
				}
				gitsyncEnvSpec = &parsed
			}
			persistMap, err := agent.ParseBackendPersist(persist)
			if err != nil {
				return err
			}
			persistMountMap, err := agent.ParseBackendPersistMounts(persistMounts)
			if err != nil {
				return err
			}
			// --with-persistence fans out to every declared --backend
			// using the (config → code) type-default chain. Explicit
			// --persist entries always win — they take ownership of
			// the named backend's spec; --with-persistence only fills
			// in backends that weren't named explicitly.
			if withPersistence {
				cfgDefaults, err := loadPersistDefaults(f)
				if err != nil {
					return fmt.Errorf("load persist defaults: %w", err)
				}
				resolved := agent.ResolvePersistDefaults(cfgDefaults)
				persistMap, err = agent.ExpandWithPersistence(specs, resolved, persistMap)
				if err != nil {
					return err
				}
			}
			specs, err = agent.ApplyBackendPersist(specs, persistMap, persistMountMap)
			if err != nil {
				return err
			}
			var runtimeStorage *agent.RuntimeStorageSpec
			if withPersistence {
				runtimeStorage = agent.DefaultRuntimeStorageSpec()
				harnessEnv = agent.ApplyHarnessTaskStoreDefault(harnessEnv)
			}
			specs = agent.ApplyBackendTaskStoreDefaults(specs)
			var kubernetesAccess *agent.KubernetesApiAccessSpec
			if cmd.Flags().Changed("kubernetes-api-access") {
				kubernetesAccess, err = agent.NewKubernetesApiAccessSpec(kubernetesAPI)
				if err != nil {
					return err
				}
			}
			return runAgentCreate(cmd.Context(), f, args[0], specs, !noWait, timeout, createNamespace, team, workspaces, syncs, maps, auth, gitsyncEnvSpec, noMetrics, harnessEnv, runtimeStorage, kubernetesAccess)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringArrayVar(&backends, "backend", nil,
		fmt.Sprintf(
			"Backend to deploy. Repeatable. Two shapes accepted:\n"+
				"  `<type>`        — name = type (single-backend shortcut)\n"+
				"  `<name>:<type>` — explicit name + type pair (for multi-backend agents)\n"+
				"Valid types: %s. Default when omitted: one %s backend",
			strings.Join(agent.KnownBackends(), ", "), agent.DefaultBackend,
		))
	cmd.Flags().BoolVar(&noWait, "no-wait", false,
		"Return as soon as the CR is accepted; skip the readiness wait")
	cmd.Flags().BoolVar(&noMetrics, "no-metrics", false,
		"Disable Prometheus metrics for this agent. By default the operator "+
			"stamps every agent with metrics on (spec.metrics.enabled=true) so "+
			"the harness's /metrics endpoint and the internal /internal/events/* "+
			"routes use the dedicated metrics listener. Pass --no-metrics to "+
			"opt out — the routes fall back to the app listener and no "+
			"ServiceMonitor / scrape annotations are rendered.")
	cmd.Flags().DurationVar(&timeout, "timeout", 5*time.Minute,
		"Maximum time to wait for the agent to report Ready (ignored with --no-wait). "+
			"On timeout, recent CR + pod events are dumped so you can tell "+
			"\"still pulling images\" from \"container crashlooping\" without a follow-up `ww agent events`.")
	cmd.Flags().BoolVar(&createNamespace, "create-namespace", false,
		"Create the target namespace if it doesn't already exist (no-op otherwise)")
	cmd.Flags().StringVar(&team, "team", "",
		"Stamp witwave.ai/team=<team> at creation (avoids a follow-up `ww agent team join`). "+
			"Omit to leave the agent in the namespace-wide manifest.")
	cmd.Flags().StringArrayVar(&workspaces, "workspace", nil,
		"Bind the agent to a WitwaveWorkspace at creation time (repeatable). "+
			"Equivalent to a follow-up `ww workspace bind <agent> <workspace>`. "+
			"Each named workspace must already exist in the agent's namespace; "+
			"v1alpha1 only supports same-namespace binding.")
	cmd.Flags().StringVar(&gitOps, "gitsync-bundle", "",
		"Convention-driven shortcut for the agent's identity-from-git wiring. "+
			"Form: <url>[@<branch>]:<repo-path>. Expands at parse time to one --gitsync "+
			"entry (sync name derived from the URL's basename) plus one --gitsync-map per "+
			"declared --backend (mapping <repo-path>/.<backend-name>/ → "+
			"/home/agent/.<backend-name>/) plus one harness mapping "+
			"(<repo-path>/.witwave/ → /home/agent/.witwave/). Composes with --gitsync "+
			"and --gitsync-map — additional explicit entries are merged in alongside the "+
			"convention defaults; duplicate (container, dest) pairs reject at parse "+
			"time so a typo can't silently shadow the convention.")
	cmd.Flags().StringArrayVar(&gitSyncs, "gitsync", nil,
		"Declare a gitSync entry on the agent (repeatable). Form: <name>=<url>[@<branch>]. "+
			"Populates spec.gitSyncs[] — the operator runs an init+sidecar pair that clones "+
			"the named repo into /git/<name>. The CLI never accepts inline credentials; pair "+
			"with --gitsync-secret to wire a pre-created Kubernetes Secret for private repos.")
	cmd.Flags().StringArrayVar(&gitMaps, "gitsync-map", nil,
		"Declare a gitMapping (repeatable). Form: [<container>=]<gitsync>:<src>:<dest>. "+
			"<container> defaults to `harness`; pass a backend name from --backend to land "+
			"the mapping on that backend container instead. <gitsync> must reference a "+
			"--gitsync entry. Duplicate (container, dest) pairs are rejected at parse time.")
	cmd.Flags().StringArrayVar(&gitSyncSecrets, "gitsync-secret", nil,
		"Reference a pre-created Kubernetes Secret as gitSync credentials (repeatable). "+
			"Form: <gitsync-name>=<k8s-secret>. The Secret should carry the gitSync env "+
			"variables (typically GITSYNC_USERNAME/GITSYNC_PASSWORD or GITSYNC_SSH_KEY_FILE). "+
			"<gitsync-name> must reference a --gitsync entry; CLI never accepts inline tokens.")
	cmd.Flags().StringVar(&gitSyncFromEnv, "gitsync-secret-from-env", "",
		"Lift two shell vars into a per-agent gitSync credential Secret. "+
			"Form: <USER_VAR>:<PASS_VAR>. The CLI reads $USER_VAR and $PASS_VAR from "+
			"the shell, mints a Secret named <agent>-gitsync with keys "+
			"GITSYNC_USERNAME/GITSYNC_PASSWORD, and stamps it onto every gitSyncs[] "+
			"entry that doesn't already carry an explicit --gitsync-secret. "+
			"Per-entry --gitsync-secret values win on collision (precedence: explicit "+
			"per-entry > agent-wide --gitsync-secret-from-env).")
	cmd.Flags().StringArrayVar(&persist, "persist", nil,
		"Provision a per-backend PVC for session/memory/log/state persistence (repeatable). "+
			"Form: <backend-name>=<size>[@<storage-class>]. Operator creates a PVC named "+
			"<agent>-<backend>-data and projects it into the container at default mount "+
			"paths derived from the backend's TYPE: claude → projects/sessions/backups/memory/logs/state, "+
			"openai → memory/sessions/logs/state, gemini → memory/logs/state, echo → memory (symbolic). Pair with "+
			"--persist-mount to override the default mount list with an explicit one.")
	cmd.Flags().StringArrayVar(&persistMounts, "persist-mount", nil,
		"Override the default mount list on a backend's PVC (repeatable). Form: "+
			"<backend-name>=<subpath>:<mountpath>. Replace-on-presence: any --persist-mount "+
			"for a backend takes ownership of its FULL mount list — type-derived defaults "+
			"are skipped. Each entry adds one (subPath, mountPath) pair on the backend's PVC. "+
			"Requires a matching --persist <backend-name>=<size> to provision the PVC.")
	cmd.Flags().BoolVar(&withPersistence, "with-persistence", false,
		"Provision a per-backend PVC for every declared --backend using type-derived "+
			"defaults (size + mount layout). Echo → 1Gi/memory; claude → 10Gi with "+
			"projects/sessions/backups/memory/logs/state; openai → 5Gi with memory/sessions/logs/state; gemini → "+
			"5Gi/memory/logs/state. Also provisions a 1Gi agent runtime PVC for harness logs/state. "+
			"Override per-type defaults in ~/.config/ww/config.toml under "+
			"[persist.defaults.<type>] (size, storageClassName, and mounts). Explicit "+
			"--persist <name>=<size> takes precedence — --with-persistence only fills in "+
			"backends that weren't named explicitly.")
	cmd.Flags().StringVar(&kubernetesAPI, "kubernetes-api-access", "",
		"Enable operator-managed Kubernetes API access for the agent. Optional value: readOnly, namespaceWrite, or "+
			"agentLifecycle. Omit the value for readOnly.")
	cmd.Flags().Lookup("kubernetes-api-access").NoOptDefVal = agent.KubernetesApiAccessModeReadOnly
	cmd.Flags().StringArrayVar(&authProfiles, "auth", nil,
		fmt.Sprintf(
			"Per-backend auth profile. Repeatable. Form: <backend>=<profile>.\n"+
				"Profile reads conventional env var(s) from the shell + mints a K8s Secret.\n"+
				"Known profiles: %s",
			agent.KnownCredentialProfiles(),
		))
	cmd.Flags().StringArrayVar(&secretFromEnv, "backend-secret-from-env", nil,
		"Mint a K8s Secret from arbitrary env vars and wire it as `envFrom` on a "+
			"backend container. Repeatable. Form: <backend>=<VAR1>[,VAR2,...]. Each "+
			"VAR is either bare `<NAME>` (read $NAME, store under Secret key NAME) "+
			"or a rename `<SRC>:<DEST>` (read $SRC, store under Secret key DEST). "+
			"Rename form lets agent-suffixed shell vars land as stable in-container "+
			"names (e.g. GITHUB_TOKEN_IRIS:GITHUB_TOKEN reads $GITHUB_TOKEN_IRIS and "+
			"injects it as $GITHUB_TOKEN inside the container).")
	cmd.Flags().StringArrayVar(&authSecrets, "auth-secret", nil,
		"Reference an existing K8s Secret (verified, not modified). Repeatable. "+
			"Form: <backend>=<secret-name>.")
	cmd.Flags().StringArrayVar(&authSet, "auth-set", nil,
		"Mint a Secret with literal KEY=VALUE pairs. Repeatable per (backend, KEY). "+
			"Form: <backend>:<KEY>=<VALUE>. SECURITY: values land in shell history + ps "+
			"output — for production tokens prefer --auth-secret or --backend-secret-from-env.")
	cmd.Flags().StringArrayVar(&backendEnvs, "backend-env", nil,
		"Set plain (non-secret) env vars on a backend container. Repeatable per "+
			"(backend, KEY). Form: <backend>:<KEY>=<VALUE>. Lands as literal "+
			"spec.backends[].env[] entries on the CR — values are visible in "+
			"`kubectl get -o yaml`, so use --auth-set / --backend-secret-from-env / "+
			"--auth-secret for anything sensitive. Use this for tunables like "+
			"TASK_TIMEOUT_SECONDS, LOG_LEVEL, STREAM_CHUNK_TIMEOUT_SECONDS, etc. "+
			"that need to override the container's compiled-in defaults.")
	cmd.Flags().StringArrayVar(&harnessEnvs, "harness-env", nil,
		"Set plain (non-secret) env vars on the harness container. Repeatable. "+
			"Form: <KEY>=<VALUE> (no <backend>: prefix — harness is the implicit "+
			"target). Lands as spec.env[] on the CR. Use for harness-side tunables "+
			"like TASK_TIMEOUT_SECONDS (the harness's A2A relay read-timeout is "+
			"derived from this), A2A_RETRY_POLICY, A2A_RETRY_FAST_ONLY_MS, "+
			"HARNESS_PROXY_MAX_RESPONSE_BYTES, etc. Backend-targeted env vars use "+
			"--backend-env instead (they go on a different field).")
	return cmd
}

func runAgentCreate(ctx context.Context, f *agentFlags, name string, backends []agent.BackendSpec, wait bool, timeout time.Duration, createNamespace bool, team string, workspaces []string, gitSyncs []agent.GitSyncFlagSpec, gitMappings []agent.GitMappingFlagSpec, backendAuth []agent.BackendAuthResolver, gitsyncFromEnv *agent.GitSyncFromEnvSpec, noMetrics bool, harnessEnv map[string]string, runtimeStorage *agent.RuntimeStorageSpec, kubernetesAccess *agent.KubernetesApiAccessSpec) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)

	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.Create(ctx, target, cfg, resolver.ConfigFlags(), agent.CreateOptions{
		Name:                name,
		Namespace:           ns,
		Backends:            backends,
		CLIVersion:          Version,
		CreatedBy:           fmt.Sprintf("ww agent create %s", name),
		AssumeYes:           assumeYes,
		DryRun:              f.dryRun,
		Wait:                wait,
		Timeout:             timeout,
		CreateNamespace:     createNamespace,
		Team:                team,
		WorkspaceRefs:       workspaces,
		GitSyncs:            gitSyncs,
		GitMappings:         gitMappings,
		GitSyncFromEnv:      gitsyncFromEnv,
		NoMetrics:           noMetrics,
		BackendAuth:         backendAuth,
		HarnessEnv:          harnessEnv,
		RuntimeStorage:      runtimeStorage,
		KubernetesApiAccess: kubernetesAccess,
		Out:                 os.Stdout,
		In:                  os.Stdin,
	})
}

// loadPersistDefaults bridges the on-disk [persist.defaults.<type>]
// config block (typed via internal/config) into the agent package's
// PersistDefaults shape. The conversion is mechanical — the two
// types mirror each other field-for-field; we keep them separate so
// the agent package doesn't import internal/config.
//
// Empty config or absent block returns (nil, nil) — caller falls
// back to the code-level BackendStorageSizeDefaults +
// BackendStoragePresets maps without further plumbing.
//
// Errors only on a hard failure path (currently none — the focused
// LoadPersistDefaults swallows read/parse errors and returns
// ok=false). The error return is reserved for future config-source
// expansions that could fail loudly.
func loadPersistDefaults(_ *agentFlags) (map[string]agent.PersistDefaults, error) {
	cfg, ok := config.LoadPersistDefaults(os.Getenv)
	if !ok || len(cfg) == 0 {
		return nil, nil
	}
	out := make(map[string]agent.PersistDefaults, len(cfg))
	for typ, d := range cfg {
		mounts := make([]agent.BackendStorageMount, 0, len(d.Mounts))
		for _, m := range d.Mounts {
			mounts = append(mounts, agent.BackendStorageMount{
				SubPath:   m.SubPath,
				MountPath: m.MountPath,
			})
		}
		out[typ] = agent.PersistDefaults{
			Size:             d.Size,
			StorageClassName: d.StorageClassName,
			Mounts:           mounts,
		}
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

func newAgentListCmd(f *agentFlags) *cobra.Command {
	var jsonOut bool
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List WitwaveAgent CRs across every namespace (narrow with --namespace)",
		Long: "Lists WitwaveAgent CRs. The default scope is EVERY namespace the caller\n" +
			"can read — matches the `kubectl get pods -A` muscle memory that most\n" +
			"operators reach for anyway. Narrow to a single namespace with --namespace.\n\n" +
			"The NAMESPACE column is always shown regardless of scope so sort / grep\n" +
			"pipelines work the same across modes.\n\n" +
			"DESIGN.md NS-3: list is a read verb — the cluster-wide default never\n" +
			"applies to mutating verbs (create, delete, …), which still honour the\n" +
			"context-ns-first resolution.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentList(cmd.Context(), f, jsonOut)
		},
	}
	cmd.Flags().BoolVarP(&f.allNamespaces, "all-namespaces", "A", false,
		"Explicit all-namespaces mode. Redundant — this is already the default — "+
			"but accepted for kubectl parity so muscle-memory flags don't error.")
	cmd.Flags().BoolVar(&jsonOut, "json", false,
		"Emit a machine-readable JSON array (includes harness + backend image versions) instead of the table.")
	return cmd
}

func runAgentList(ctx context.Context, f *agentFlags, jsonOut bool) error {
	_, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	// New default scope (DESIGN.md NS-3): list spans every namespace
	// unless the user explicitly narrows it with -n. -A is preserved
	// for kubectl parity — functionally redundant now since the
	// default IS all-namespaces, but harmless to keep.
	allNamespaces := f.allNamespaces || f.namespace == ""
	var ns string
	if !allNamespaces {
		ns = f.namespace
	}
	return agent.List(ctx, cfg, agent.ListOptions{
		Namespace:     ns,
		AllNamespaces: allNamespaces,
		JSON:          jsonOut,
		Out:           os.Stdout,
	})
}

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

func newAgentStatusCmd(f *agentFlags) *cobra.Command {
	var jsonOut bool
	cmd := &cobra.Command{
		Use:   "status <name>",
		Short: "Show phase, backends, and reconcile history for a WitwaveAgent",
		Long: "Reads the WitwaveAgent CR's `.status` subresource and prints the\n" +
			"current phase (e.g. Pending / Reconciling / Ready / Failed), the\n" +
			"per-backend status block (image, port, ready flag, last error),\n" +
			"and the most recent reconcile conditions with timestamps. Useful\n" +
			"as a first-pass diagnostic when an agent isn't responding — the\n" +
			"phase + last condition usually pinpoints whether the issue is\n" +
			"backend startup, image pull, or controller reconcile failure.\n\n" +
			"Namespace resolution follows DESIGN.md NS-2: --namespace > kubeconfig\n" +
			"context > ww default. The resolved namespace is always echoed first.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentStatus(cmd.Context(), f, args[0], jsonOut)
		},
	}
	cmd.Flags().BoolVar(&jsonOut, "json", false,
		"Emit a machine-readable JSON object (harness + backend versions, generation/observedGeneration) instead of the text view.")
	return cmd
}

func runAgentStatus(ctx context.Context, f *agentFlags, name string, jsonOut bool) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	// In --json mode keep stdout pure JSON: resolve the namespace without
	// the human "Using namespace: …" echo (and no spacer line).
	var ns string
	if jsonOut {
		ns, _ = agent.ResolveNamespaceWithSource(f.namespace, target.Namespace)
	} else {
		ns = logAndResolveNamespace(f.namespace, target.Namespace)
		if f.namespace == "" {
			fmt.Fprintln(os.Stdout)
		}
	}
	return agent.Status(ctx, cfg, agent.StatusOptions{
		Name:      name,
		Namespace: ns,
		JSON:      jsonOut,
		Out:       os.Stdout,
	})
}

// ---------------------------------------------------------------------------
// upgrade
// ---------------------------------------------------------------------------

func newAgentUpgradeCmd(f *agentFlags) *cobra.Command {
	var (
		tag         string
		harnessTag  string
		backendTags []string
		noWait      bool
		timeout     time.Duration
		force       bool
	)
	cmd := &cobra.Command{
		Use:   "upgrade <name>",
		Short: "Upgrade a WitwaveAgent's container image tags in place",
		Long: "Patches spec.image.tag (harness) and each spec.backends[].image.tag\n" +
			"on the named WitwaveAgent CR. The operator reconciles the change and\n" +
			"rolls the Deployment via the standard kubelet rollout — pod-local PVC\n" +
			"state survives, no agent recreation needed.\n\n" +
			"Tag resolution priority:\n\n" +
			"  --tag <X>                 pin every container to <X>\n" +
			"  --harness-tag <X>         override harness only\n" +
			"  --backend-tag <name>=<X>  override one backend (repeatable)\n" +
			"  (none of the above)       default to the brewed `ww` binary's own version\n\n" +
			"--tag is mutually exclusive with --harness-tag / --backend-tag.\n\n" +
			"Idempotent fast path: when every container already at the desired tag,\n" +
			"the command exits 0 without rolling. Pass --force to roll anyway (cache\n" +
			"bust on dev :latest images, etc.).\n\n" +
			"After patching, waits up to --timeout for the operator to report Ready.\n" +
			"Pass --no-wait to return as soon as the patch lands.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if tag != "" && (harnessTag != "" || len(backendTags) > 0) {
				return fmt.Errorf(
					"--tag is mutually exclusive with --harness-tag / --backend-tag — pick one form")
			}
			parsedBackend, err := parseBackendTagFlags(backendTags)
			if err != nil {
				return err
			}
			return runAgentUpgrade(cmd.Context(), f, args[0], tag, harnessTag, parsedBackend, !noWait, timeout, force)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringVar(&tag, "tag", "",
		"Pin every container (harness + all backends) to this image tag. "+
			"Mutually exclusive with --harness-tag / --backend-tag. "+
			"Leading `v` is stripped — `0.11.14` and `v0.11.14` are equivalent.")
	cmd.Flags().StringVar(&harnessTag, "harness-tag", "",
		"Override the harness container's image tag only. Backends keep their "+
			"current tag unless a per-backend --backend-tag is also set.")
	cmd.Flags().StringArrayVar(&backendTags, "backend-tag", nil,
		"Override one backend's image tag (repeatable). Form: <backend-name>=<tag>. "+
			"<backend-name> must match a backend declared on the agent's CR; unknown "+
			"names are rejected so a typo can't silently no-op.")
	cmd.Flags().BoolVar(&noWait, "no-wait", false,
		"Return as soon as the patch lands; skip the rollout-to-Ready wait.")
	cmd.Flags().DurationVar(&timeout, "timeout", 5*time.Minute,
		"Maximum time to wait for the rollout to report Ready (ignored with --no-wait). "+
			"On timeout, recent CR + pod events are dumped — same UX as `ww agent create`.")
	cmd.Flags().BoolVar(&force, "force", false,
		"Roll the deployment even when every target tag already matches the current "+
			"value. Use to cache-bust on dev :latest images.")
	return cmd
}

// parseBackendTagFlags converts repeatable --backend-tag <name>=<tag>
// flag values into a (name → tag) map. Empty names or empty tags are
// rejected so a typo doesn't drop silently into an opaque downstream
// error.
func parseBackendTagFlags(raw []string) (map[string]string, error) {
	out := make(map[string]string, len(raw))
	for i, r := range raw {
		entry := strings.TrimSpace(r)
		if entry == "" {
			return nil, fmt.Errorf("--backend-tag[%d]: empty value", i)
		}
		eq := strings.IndexByte(entry, '=')
		if eq < 1 {
			return nil, fmt.Errorf("--backend-tag[%d] %q: form is <backend-name>=<tag>", i, entry)
		}
		name := strings.TrimSpace(entry[:eq])
		tag := strings.TrimSpace(entry[eq+1:])
		if name == "" || tag == "" {
			return nil, fmt.Errorf("--backend-tag[%d] %q: both name and tag required", i, entry)
		}
		if _, dup := out[name]; dup {
			return nil, fmt.Errorf("--backend-tag[%d]: backend %q already given", i, name)
		}
		out[name] = tag
	}
	return out, nil
}

func runAgentUpgrade(ctx context.Context, f *agentFlags, name, tag, harnessTag string, backendTags map[string]string, wait bool, timeout time.Duration, force bool) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.Upgrade(ctx, target, cfg, agent.UpgradeOptions{
		Name:        name,
		Namespace:   ns,
		Tag:         tag,
		HarnessTag:  harnessTag,
		BackendTags: backendTags,
		CLIVersion:  Version,
		Force:       force,
		Wait:        wait,
		Timeout:     timeout,
		AssumeYes:   assumeYes,
		DryRun:      f.dryRun,
		Out:         os.Stdout,
		In:          os.Stdin,
	})
}

// ---------------------------------------------------------------------------
// storage
// ---------------------------------------------------------------------------

func newAgentStorageCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "storage",
		Short: "Manage persistent storage on an existing WitwaveAgent",
		Long: "Manage storage-related fields on an existing WitwaveAgent CR.\n\n" +
			"These commands patch the CR in place and let the operator roll the\n" +
			"Deployment. Use them when an agent already exists and you need to\n" +
			"turn on persistence features that `ww agent create --with-persistence`\n" +
			"would have stamped at creation time.",
	}
	cmd.AddCommand(newAgentStorageEnableCmd(f))
	return cmd
}

func newAgentStorageEnableCmd(f *agentFlags) *cobra.Command {
	var (
		size           string
		storageClass   string
		noBackendState bool
		noWait         bool
		timeout        time.Duration
	)
	cmd := &cobra.Command{
		Use:   "enable <name>",
		Short: "Enable durable runtime storage on an existing agent",
		Long: "Enables the default runtime storage layout on an existing\n" +
			"WitwaveAgent without recreating it. The command adds\n" +
			"spec.runtimeStorage for the harness runtime PVC, then adds\n" +
			"/home/agent/state to each backend that already has persistent\n" +
			"backend storage. The operator renders TASK_STORE_PATH when the\n" +
			"state mount is present.\n\n" +
			"This is the existing-agent counterpart to `ww agent create\n" +
			"--with-persistence` for the runtime state portion.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentStorageEnable(cmd.Context(), f, args[0], size, storageClass, !noBackendState, !noWait, timeout)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringVar(&size, "size", "1Gi",
		"Runtime PVC size to use when spec.runtimeStorage does not already set a size")
	cmd.Flags().StringVar(&storageClass, "storage-class", "",
		"StorageClassName for the runtime PVC (default: cluster default). "+
			"When runtimeStorage already exists, this updates storageClassName only when supplied.")
	cmd.Flags().BoolVar(&noBackendState, "no-backend-state", false,
		"Only enable harness runtimeStorage; do not add /home/agent/state to existing backend PVC mounts.")
	cmd.Flags().BoolVar(&noWait, "no-wait", false,
		"Return as soon as the patch lands; skip the rollout-to-Ready wait.")
	cmd.Flags().DurationVar(&timeout, "timeout", 5*time.Minute,
		"Maximum time to wait for the rollout to finish (ignored with --no-wait). "+
			"On timeout, recent CR + pod events are dumped.")
	return cmd
}

func runAgentStorageEnable(ctx context.Context, f *agentFlags, name, size, storageClass string, backendState, wait bool, timeout time.Duration) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.StorageEnable(ctx, target, cfg, agent.StorageEnableOptions{
		Name:                    name,
		Namespace:               ns,
		RuntimeSize:             size,
		RuntimeStorageClassName: storageClass,
		BackendState:            backendState,
		Wait:                    wait,
		Timeout:                 timeout,
		AssumeYes:               assumeYes,
		DryRun:                  f.dryRun,
		Out:                     os.Stdout,
		In:                      os.Stdin,
	})
}

// ---------------------------------------------------------------------------
// kubernetes-api-access
// ---------------------------------------------------------------------------

func newAgentKubernetesApiAccessCmd(f *agentFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "kubernetes-api-access",
		Aliases: []string{"k8s-access"},
		Short:   "Manage operator-rendered Kubernetes API access on an agent",
		Long: "Manage spec.kubernetesApiAccess on an existing WitwaveAgent CR.\n\n" +
			"When enabled, the operator creates a per-agent ServiceAccount,\n" +
			"namespace-scoped Role, and RoleBinding, then rolls the pod so\n" +
			"kubectl and client-go can authenticate from the agent containers.\n" +
			"The default mode is readOnly; namespaceWrite must be requested\n" +
			"explicitly for bounded namespace-local remediation; agentLifecycle\n" +
			"adds patch on witwaveagents for ww agent upgrade.",
	}
	cmd.AddCommand(newAgentKubernetesApiAccessEnableCmd(f))
	cmd.AddCommand(newAgentKubernetesApiAccessDisableCmd(f))
	return cmd
}

func newAgentKubernetesApiAccessEnableCmd(f *agentFlags) *cobra.Command {
	var (
		mode    string
		noWait  bool
		timeout time.Duration
	)
	cmd := &cobra.Command{
		Use:   "enable <name>",
		Short: "Enable or update Kubernetes API access on an existing agent",
		Long: "Enables or updates operator-managed Kubernetes API access on\n" +
			"an existing WitwaveAgent. readOnly grants diagnostics-only access\n" +
			"(get/list/watch plus pod logs). namespaceWrite adds bounded\n" +
			"namespace-local remediation while still excluding secrets, RBAC,\n" +
			"raw pod creation, and cluster-scoped resources. agentLifecycle adds\n" +
			"patch on witwaveagents so the agent can run ww agent upgrade.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentKubernetesApiAccessEnable(cmd.Context(), f, args[0], mode, !noWait, timeout)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().StringVar(&mode, "mode", agent.KubernetesApiAccessModeReadOnly,
		"Kubernetes API access preset: readOnly, namespaceWrite, or agentLifecycle")
	cmd.Flags().BoolVar(&noWait, "no-wait", false,
		"Return as soon as the patch lands; skip the rollout-to-Ready wait.")
	cmd.Flags().DurationVar(&timeout, "timeout", 5*time.Minute,
		"Maximum time to wait for the rollout to finish (ignored with --no-wait). "+
			"On timeout, recent CR + pod events are dumped.")
	return cmd
}

func newAgentKubernetesApiAccessDisableCmd(f *agentFlags) *cobra.Command {
	var (
		noWait  bool
		timeout time.Duration
	)
	cmd := &cobra.Command{
		Use:   "disable <name>",
		Short: "Disable Kubernetes API access on an existing agent",
		Long: "Removes spec.kubernetesApiAccess from the WitwaveAgent. The\n" +
			"operator cleans up its managed ServiceAccount, Role, and\n" +
			"RoleBinding, then rolls the pod back to the default no-token\n" +
			"service-account posture.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentKubernetesApiAccessDisable(cmd.Context(), f, args[0], !noWait, timeout)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().BoolVar(&noWait, "no-wait", false,
		"Return as soon as the patch lands; skip the rollout-to-Ready wait.")
	cmd.Flags().DurationVar(&timeout, "timeout", 5*time.Minute,
		"Maximum time to wait for the rollout to finish (ignored with --no-wait). "+
			"On timeout, recent CR + pod events are dumped.")
	return cmd
}

func runAgentKubernetesApiAccessEnable(ctx context.Context, f *agentFlags, name, mode string, wait bool, timeout time.Duration) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.KubernetesApiAccessEnable(ctx, target, cfg, agent.KubernetesApiAccessOptions{
		Name:      name,
		Namespace: ns,
		Mode:      mode,
		Wait:      wait,
		Timeout:   timeout,
		AssumeYes: assumeYes,
		DryRun:    f.dryRun,
		Out:       os.Stdout,
		In:        os.Stdin,
	})
}

func runAgentKubernetesApiAccessDisable(ctx context.Context, f *agentFlags, name string, wait bool, timeout time.Duration) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.KubernetesApiAccessDisable(ctx, target, cfg, agent.KubernetesApiAccessOptions{
		Name:      name,
		Namespace: ns,
		Wait:      wait,
		Timeout:   timeout,
		AssumeYes: assumeYes,
		DryRun:    f.dryRun,
		Out:       os.Stdout,
		In:        os.Stdin,
	})
}

// ---------------------------------------------------------------------------
// delete
// ---------------------------------------------------------------------------

func newAgentDeleteCmd(f *agentFlags) *cobra.Command {
	var (
		removeRepoFolder   bool
		deleteGitSecret    bool
		keepBackendSecrets bool
		purge              bool
		commitMessage      string
	)
	cmd := &cobra.Command{
		Use:   "delete <name>",
		Short: "Delete a WitwaveAgent CR (operator cascades pod cleanup)",
		Long: "Deletes the WitwaveAgent CR. The operator cascades pod + Service\n" +
			"teardown via owner references — no manual cleanup needed on the\n" +
			"cluster side. By default ww-managed backend credential Secrets\n" +
			"(named <agent>-<backend>, minted by --backend-secret-from-env /\n" +
			"--auth / --auth-set on `ww agent create`) are also reaped — the\n" +
			"per-agent naming makes them unambiguous orphans, and the\n" +
			"per-backend PVCs already cascade-delete via owner refs. Pass\n" +
			"--keep-backend-secrets to preserve them.\n\n" +
			"Optional repo-side + extra credential cleanup:\n\n" +
			"  --remove-repo-folder      Also wipe the agent's `.agents/<…>/`\n" +
			"                            directory from the wired gitSync repo\n" +
			"                            (clone → git rm -r → commit → push).\n" +
			"                            Requires exactly one gitSync configured;\n" +
			"                            multiple syncs refuse as ambiguous.\n" +
			"                            Runs BEFORE the CR delete so a repo-\n" +
			"                            side failure leaves cluster state intact\n" +
			"                            and the user can retry.\n\n" +
			"  --delete-git-secret       Also delete every ww-managed credential\n" +
			"                            Secret referenced by the CR's\n" +
			"                            gitSyncs[]. Opt-in (not default) because\n" +
			"                            gitSync Secrets are sometimes shared\n" +
			"                            across agents — default-deleting one\n" +
			"                            could break peer agents on the same\n" +
			"                            shared credential.\n\n" +
			"  --keep-backend-secrets    Preserve the ww-managed backend Secrets\n" +
			"                            (opt-out — by default they're deleted\n" +
			"                            alongside the agent).\n\n" +
			"  --purge                   Convenience: --remove-repo-folder +\n" +
			"                            --delete-git-secret. Backend Secrets are\n" +
			"                            already deleted by default; --purge does\n" +
			"                            not enable --keep-backend-secrets.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if purge {
				removeRepoFolder = true
				deleteGitSecret = true
			}
			return runAgentDelete(cmd.Context(), f, args[0], removeRepoFolder, deleteGitSecret, keepBackendSecrets, commitMessage)
		},
	}
	bindAgentMutatingFlags(cmd, f)
	cmd.Flags().BoolVar(&removeRepoFolder, "remove-repo-folder", false,
		"Also wipe the agent's `.agents/<…>/` directory from the wired gitSync repo "+
			"(refuses when multiple gitSyncs are configured)")
	cmd.Flags().BoolVar(&deleteGitSecret, "delete-git-secret", false,
		"Also delete every ww-managed credential Secret referenced by the CR's "+
			"gitSyncs[]. Opt-in because gitSync Secrets may be shared across agents.")
	cmd.Flags().BoolVar(&keepBackendSecrets, "keep-backend-secrets", false,
		"Preserve the ww-managed backend credential Secrets (per-agent, named "+
			"<agent>-<backend>). Default: delete them alongside the "+
			"agent. Secrets without app.kubernetes.io/managed-by=ww are never "+
			"touched regardless.")
	cmd.Flags().BoolVar(&purge, "purge", false,
		"Convenience flag: enables --remove-repo-folder + --delete-git-secret. "+
			"Backend Secrets are already deleted by default.")
	cmd.Flags().StringVar(&commitMessage, "commit-message", "",
		"Custom commit message for the repo wipe (default: \"Remove agent <name>\")")
	return cmd
}

func runAgentDelete(ctx context.Context, f *agentFlags, name string, removeRepoFolder, deleteGitSecret, keepBackendSecrets bool, commitMessage string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return agent.Delete(ctx, target, cfg, agent.DeleteOptions{
		Name:               name,
		Namespace:          ns,
		RemoveRepoFolder:   removeRepoFolder,
		DeleteGitSecret:    deleteGitSecret,
		KeepBackendSecrets: keepBackendSecrets,
		CommitMessage:      commitMessage,
		AssumeYes:          assumeYes,
		DryRun:             f.dryRun,
		Out:                os.Stdout,
		In:                 os.Stdin,
	})
}

// ---------------------------------------------------------------------------
// send
// ---------------------------------------------------------------------------

func newAgentSendCmd(f *agentFlags) *cobra.Command {
	var (
		messageID string
		timeout   time.Duration
		rawJSON   bool
		backend   string
	)
	cmd := &cobra.Command{
		Use:   "send <name> <prompt>",
		Short: "Send an A2A prompt to an agent via the Kubernetes apiserver Service proxy",
		Long: "Makes a single A2A message/send round-trip against the agent's harness\n" +
			"Service. Uses the apiserver's built-in Service proxy so no local port-forward\n" +
			"or external LoadBalancer is required — any ClusterIP Service works.\n\n" +
			"By default the harness routes the prompt to whichever backend backend.yaml\n" +
			"names as the primary for the `a2a` concern. Pass --backend <name> to bypass\n" +
			"that routing and target a specific backend container directly — the harness\n" +
			"honours the metadata.backend_id hint and dispatches to the named sidecar.\n\n" +
			"Not suited for streaming or very large payloads (apiserver proxy has size\n" +
			"caps); ww agent logs -f is the right tool for live observation. Use --raw\n" +
			"to print the full JSON-RPC envelope for debugging.",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentSend(cmd.Context(), f, args[0], args[1], messageID, timeout, rawJSON, backend)
		},
	}
	cmd.Flags().StringVar(&messageID, "message-id", "",
		"Explicit A2A messageId (default: ww-send-<timestamp>)")
	cmd.Flags().DurationVar(&timeout, "timeout", 30*time.Second,
		"Round-trip timeout through the apiserver Service proxy")
	cmd.Flags().BoolVar(&rawJSON, "raw", false,
		"Print the raw JSON-RPC response envelope instead of extracting the agent text")
	cmd.Flags().StringVar(&backend, "backend", "",
		"Target a specific backend by name (stamps metadata.backend_id; harness "+
			"dispatches directly to the named sidecar instead of routing per backend.yaml)")
	return cmd
}

func runAgentSend(ctx context.Context, f *agentFlags, name, prompt, messageID string, timeout time.Duration, rawJSON bool, backend string) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	return agent.Send(ctx, cfg, agent.SendOptions{
		Agent:     name,
		Namespace: ns,
		Prompt:    prompt,
		MessageID: messageID,
		BackendID: backend,
		Timeout:   timeout,
		RawJSON:   rawJSON,
		Out:       os.Stdout,
	})
}

// ---------------------------------------------------------------------------
// metrics
// ---------------------------------------------------------------------------

func newAgentMetricsCmd(f *agentFlags) *cobra.Command {
	var (
		timeout time.Duration
		pod     string
	)
	cmd := &cobra.Command{
		Use:   "metrics <name>",
		Short: "Scrape every Prometheus metrics endpoint for a WitwaveAgent",
		Long: "Scrapes every /metrics endpoint owned by the selected WitwaveAgent pod.\n" +
			"Each application container exposes its own Prometheus listener: the harness\n" +
			"uses metrics-harness and each backend uses metrics-<backend>. ww discovers\n" +
			"those pod ports and reads them through the Kubernetes apiserver pod proxy,\n" +
			"so no local port-forward is required.\n\n" +
			"The output is Prometheus text grouped with comment headers per container.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentMetrics(cmd.Context(), f, args[0], agent.MetricsOptions{
				Timeout: timeout,
				Pod:     pod,
				Out:     os.Stdout,
			})
		},
	}
	cmd.Flags().DurationVar(&timeout, "timeout", 30*time.Second,
		"Maximum time for the full multi-container metrics scrape")
	cmd.Flags().StringVar(&pod, "pod", "",
		"Target a specific pod by name instead of all pods matching the agent label")
	return cmd
}

func runAgentMetrics(ctx context.Context, f *agentFlags, name string, opts agent.MetricsOptions) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	opts.Agent = name
	opts.Namespace = ns
	return agent.Metrics(ctx, cfg, opts)
}

// ---------------------------------------------------------------------------
// logs
// ---------------------------------------------------------------------------

func newAgentLogsCmd(f *agentFlags) *cobra.Command {
	var (
		container string
		tail      int64
		since     time.Duration
		noFollow  bool
		pod       string
	)
	cmd := &cobra.Command{
		Use:   "logs <name>",
		Short: "Tail logs from a WitwaveAgent's pod(s)",
		Long: "Streams logs from every pod matching the agent's label selector. By default\n" +
			"every container in the pod (harness + backend(s) + git-sync) is tailed and each\n" +
			"line is prefixed with `[<container>]` so you can tell streams apart. Pass\n" +
			"-c <name> to filter to a single container; the prefix is still emitted for\n" +
			"consistency with `kubectl logs --prefix` semantics.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentLogs(cmd.Context(), f, args[0], agent.LogsOptions{
				Container: container,
				Follow:    !noFollow,
				TailLines: tail,
				Since:     since,
				Pod:       pod,
				Out:       os.Stdout,
			})
		},
	}
	cmd.Flags().StringVarP(&container, "container", "c", "",
		"Filter to a single container (default: tail every container in the pod, prefixed)")
	cmd.Flags().Int64Var(&tail, "tail", 100,
		"Number of recent log lines to emit before following (0 = full history)")
	cmd.Flags().DurationVar(&since, "since", 0,
		"Lookback duration, e.g. 1h or 30m (empty = no limit)")
	cmd.Flags().BoolVar(&noFollow, "no-follow", false,
		"Print current log contents and exit without streaming")
	cmd.Flags().StringVar(&pod, "pod", "",
		"Target a specific pod by name instead of all pods matching the agent label")
	return cmd
}

func runAgentLogs(ctx context.Context, f *agentFlags, name string, opts agent.LogsOptions) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	opts.Agent = name
	opts.Namespace = ns
	return agent.Logs(ctx, cfg, opts)
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------

func newAgentEventsCmd(f *agentFlags) *cobra.Command {
	var (
		warnings bool
		since    time.Duration
	)
	cmd := &cobra.Command{
		Use:   "events <name>",
		Short: "Show Kubernetes events for a WitwaveAgent and its owned pods",
		Long: "One-shot event snapshot scoped to a single agent: CR-level events (reconcile\n" +
			"actions, validation failures) plus pod-level events (scheduling, pulls, crash\n" +
			"loops). For live streaming, `ww agent logs -f` is usually the better signal.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAgentEvents(cmd.Context(), f, args[0], agent.EventsOptions{
				WarningsOnly: warnings,
				Since:        since,
				Out:          os.Stdout,
			})
		},
	}
	cmd.Flags().BoolVar(&warnings, "warnings", false,
		"Only show events of type Warning")
	cmd.Flags().DurationVar(&since, "since", time.Hour,
		"Lookback window for the initial listing, e.g. 10m or 6h")
	return cmd
}

func runAgentEvents(ctx context.Context, f *agentFlags, name string, opts agent.EventsOptions) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	ns := logAndResolveNamespace(f.namespace, target.Namespace)
	opts.Agent = name
	opts.Namespace = ns
	return agent.Events(ctx, cfg, opts)
}
