package cmd

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
	"github.com/witwave-ai/witwave/clients/ww/internal/operator"
)

// operatorFlags are inherited by every `ww operator *` subcommand so
// the namespace and confirmation behaviour stays uniform across
// install / upgrade / status / uninstall.
//
// Cluster-identity flags (--kubeconfig, --context) live on the root
// command per DESIGN.md KC-5 and reach us via K8sFromCtx. --namespace
// is persistent here rather than on root per DESIGN.md KC-6: the
// operator subtree's namespace defaults to witwave-system, whereas
// future tenant-scoped subtrees (agent, prompt) will default to the
// context's namespace — one global default would be wrong for at least
// one subtree.
type operatorFlags struct {
	namespace string
	// --yes — skip preflight confirmation on production-looking targets.
	// Also honoured via WW_ASSUME_YES=true.
	assumeYes bool
	// --dry-run — print the plan banner and exit without mutating. Read
	// by install/upgrade/uninstall; ignored by status.
	dryRun bool
}

func bindOperatorFlags(cmd *cobra.Command, f *operatorFlags) {
	cmd.PersistentFlags().StringVarP(&f.namespace, "namespace", "n", operator.DefaultNamespace,
		"Namespace the operator is installed in")
}

// bindMutatingFlags adds --yes and --dry-run to mutating subcommands
// (install/upgrade/uninstall). Keeps read-only subcommands free of them.
func bindMutatingFlags(cmd *cobra.Command, f *operatorFlags) {
	cmd.Flags().BoolVarP(&f.assumeYes, "yes", "y", false,
		"Skip the preflight confirmation prompt (or set WW_ASSUME_YES=true)")
	cmd.Flags().BoolVar(&f.dryRun, "dry-run", false,
		"Print the plan and exit without applying any changes")
}

// valuesFlags carries the Helm value-overlay flags shared by the
// chart-rendering mutating subcommands (install + upgrade). Flag names
// and semantics mirror `helm` exactly (see DESIGN.md OV-1) so muscle
// memory transfers and `ww operator install -f x.yaml --set k=v` behaves
// like the equivalent `helm install`. uninstall/status take no values —
// they don't render the chart.
type valuesFlags struct {
	valueFiles      []string
	setValues       []string
	setStringValues []string
}

func bindValuesFlags(cmd *cobra.Command, v *valuesFlags) {
	// Backtick spans set cobra's value placeholder, so the first pair in
	// each usage string is the arg name shown in --help (file / key=val).
	cmd.Flags().StringArrayVarP(&v.valueFiles, "values", "f", nil,
		"Path to a values YAML `file` (repeatable; later files win), as with helm -f")
	cmd.Flags().StringArrayVar(&v.setValues, "set", nil,
		"Set a chart value `key=val` (repeatable; overrides --values), as with helm --set")
	cmd.Flags().StringArrayVar(&v.setStringValues, "set-string", nil,
		"Force a STRING chart value `key=val` (repeatable), as with helm --set-string")
}

// merged turns the flag inputs into the values map the install/upgrade
// internals feed to the Helm SDK. nil/empty in → empty (non-nil) map.
func (v *valuesFlags) merged() (map[string]interface{}, error) {
	return operator.MergeValues(v.valueFiles, v.setValues, v.setStringValues)
}

// resolveTarget runs the kubeconfig loader and returns the populated
// Target + REST config. Surfaces the friendly "no kubeconfig / no context"
// error when applicable. Cluster-identity flags come from the root
// command via K8sFromCtx (DESIGN.md KC-5); namespace is the operator
// subtree's own persistent flag (KC-6).
func (f *operatorFlags) resolveTarget(ctx context.Context) (*k8s.Target, *k8s.Resolver, error) {
	kc := K8sFromCtx(ctx)
	r, err := k8s.NewResolver(k8s.Options{
		KubeconfigPath: kc.Kubeconfig,
		Context:        kc.Context,
		Namespace:      f.namespace,
	})
	if err != nil {
		return nil, nil, err
	}
	return r.Target(), r, nil
}

// newOperatorCmd is the parent command for `ww operator *`. Has no
// direct behaviour; cobra will print help if no subcommand runs.
func newOperatorCmd() *cobra.Command {
	f := &operatorFlags{}
	cmd := &cobra.Command{
		Use:   "operator",
		Short: "Manage the witwave-operator install (CRD controller) on a Kubernetes cluster",
		Long: "Install, upgrade, inspect, or uninstall the witwave-operator Helm release.\n\n" +
			"The operator is a cluster-scoped singleton — one install per cluster. These\n" +
			"commands use the ambient kubeconfig (--kubeconfig / KUBECONFIG / ~/.kube/config)\n" +
			"and the current-context by default; override with the root's --context flag.\n" +
			"Use --namespace / -n to target a non-default operator namespace (default: " + operator.DefaultNamespace + ").",
	}
	bindOperatorFlags(cmd, f)

	cmd.AddCommand(newOperatorStatusCmd(f))
	cmd.AddCommand(newOperatorInstallCmd(f))
	cmd.AddCommand(newOperatorUpgradeCmd(f))
	cmd.AddCommand(newOperatorUninstallCmd(f))
	cmd.AddCommand(newOperatorLogsCmd(f))
	cmd.AddCommand(newOperatorEventsCmd(f))
	return cmd
}

// ---------------------------------------------------------------------------
// events — read-only; lists Kubernetes events related to the operator.
// ---------------------------------------------------------------------------

func newOperatorEventsCmd(f *operatorFlags) *cobra.Command {
	var (
		watch    bool
		warnings bool
		since    time.Duration
	)
	cmd := &cobra.Command{
		Use:   "events",
		Short: "Show Kubernetes events related to the witwave operator",
		Long: "Lists events the operator emits (on WitwaveAgent / WitwavePrompt CRs\n" +
			"and their owned resources) plus events on the operator's own pods\n" +
			"in witwave-system. --watch streams new events until Ctrl-C.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runOperatorEvents(cmd.Context(), f, operator.EventsOptions{
				// CRs live in user namespaces; list cluster-wide by default.
				// --namespace on the parent command overrides, but unlike
				// install/upgrade/status where --namespace targets the
				// operator's own ns, for events the semantic is "only CRs
				// in this namespace." Pass through verbatim — empty string
				// means cluster-wide.
				Namespace:         f.namespace,
				OperatorNamespace: operator.DefaultNamespace,
				Watch:             watch,
				WarningsOnly:      warnings,
				Since:             since,
				Out:               os.Stdout,
			})
		},
	}
	cmd.Flags().BoolVarP(&watch, "watch", "w", false,
		"Stream new events until interrupted")
	cmd.Flags().BoolVar(&warnings, "warnings", false,
		"Only show events of type Warning")
	cmd.Flags().DurationVar(&since, "since", time.Hour,
		"Lookback window for the initial listing, e.g. 10m or 6h")
	return cmd
}

func runOperatorEvents(ctx context.Context, f *operatorFlags, opts operator.EventsOptions) error {
	_, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	// The parent --namespace defaults to witwave-system which is perfect
	// for install/upgrade/status/logs. For events, witwave-system would
	// silently hide every CR event. Detect the default and broaden to
	// cluster-wide so `ww operator events` DTRT without requiring
	// `-n ""` gymnastics.
	if opts.Namespace == operator.DefaultNamespace {
		opts.Namespace = ""
	}
	return operator.Events(ctx, cfg, opts)
}

// ---------------------------------------------------------------------------
// logs — read-only; tails operator pod logs.
// ---------------------------------------------------------------------------

func newOperatorLogsCmd(f *operatorFlags) *cobra.Command {
	var (
		tail     int64
		since    time.Duration
		noFollow bool
		pod      string
	)
	cmd := &cobra.Command{
		Use:   "logs",
		Short: "Tail witwave-operator pod logs",
		Long: "Streams pod logs from every pod matching\n" +
			"app.kubernetes.io/name=witwave-operator in the target namespace.\n" +
			"When multiple pods are present, each line is prefixed with\n" +
			"[pod-name] so sources can be distinguished. Ctrl-C exits cleanly.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runOperatorLogs(cmd.Context(), f, operator.LogsOptions{
				Namespace: f.namespace,
				Follow:    !noFollow,
				TailLines: tail,
				Since:     since,
				Pod:       pod,
				Out:       os.Stdout,
			})
		},
	}
	cmd.Flags().Int64Var(&tail, "tail", 100,
		"Number of recent log lines to emit before following (0 = full history)")
	cmd.Flags().DurationVar(&since, "since", 0,
		"Lookback duration, e.g. 1h or 30m (empty = no limit)")
	cmd.Flags().BoolVar(&noFollow, "no-follow", false,
		"Print current log contents and exit without streaming")
	cmd.Flags().StringVar(&pod, "pod", "",
		"Target a specific pod by name instead of all operator pods")
	return cmd
}

func runOperatorLogs(ctx context.Context, f *operatorFlags, opts operator.LogsOptions) error {
	_, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	return operator.Logs(ctx, cfg, opts)
}

// ---------------------------------------------------------------------------
// status — read-only; ships now.
// ---------------------------------------------------------------------------

func newOperatorStatusCmd(f *operatorFlags) *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Show the witwave-operator install state on the target cluster",
		Long: "Reads (no cluster mutations) the witwave-operator Helm release, its\n" +
			"pod(s), the CRDs it owns, and the live count of WitwaveAgent and\n" +
			"WitwavePrompt CRs. Prints an \"operator not installed\" block when\n" +
			"no release is found in the target namespace.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runOperatorStatus(cmd.Context(), f)
		},
	}
}

func runOperatorStatus(ctx context.Context, f *operatorFlags) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	// status is read-only — no confirmation, but still print the header
	// so users see which cluster they're looking at.
	fmt.Printf("Target cluster: %s  (context: %s)\n",
		cmpDisplay(target.Cluster, target.Server), target.Context)
	fmt.Println()

	cfg, err := resolver.REST()
	if err != nil {
		return err
	}

	s, err := operator.GatherStatus(ctx, cfg, f.namespace, Version)
	if err != nil {
		// Render whatever we got before returning the error — users
		// still see partial info.
		if s != nil {
			s.Render(os.Stdout)
			fmt.Fprintln(os.Stderr)
		}
		return fmt.Errorf("gather status: %w", err)
	}
	s.Render(os.Stdout)
	return nil
}

// cmpDisplay prefers the cluster nickname over the raw server URL — EKS /
// GKE ARNs can be long, and when both are present the nickname is what
// users actually identify with.
func cmpDisplay(cluster, server string) string {
	if cluster != "" {
		return cluster
	}
	return server
}

// ---------------------------------------------------------------------------
// install / upgrade / uninstall — mutating; each runs the shared preflight
// banner + confirmation (k8s.Confirm) before any API mutation per DESIGN.md
// KC-4. Implementation lives in internal/operator/{install,upgrade,uninstall}.go.
// ---------------------------------------------------------------------------

func newOperatorInstallCmd(f *operatorFlags) *cobra.Command {
	var (
		adopt           bool
		ifMissing       bool
		createNamespace bool
		vals            valuesFlags
	)
	cmd := &cobra.Command{
		Use:   "install",
		Short: "Install the witwave-operator Helm release (embedded chart)",
		Long: "Installs the embedded witwave-operator chart into the target cluster.\n\n" +
			"Runs singleton detection (refuses when another release is already\n" +
			"installed cluster-wide), preflight banner + confirmation on\n" +
			"production-looking contexts, RBAC SelfSubjectAccessReview, namespace\n" +
			"check, and the Helm install. Use --adopt to take over a\n" +
			"cluster whose CRDs were installed manually (via `kubectl apply`).\n\n" +
			"Pass --create-namespace to provision the target namespace if it\n" +
			"doesn't already exist. Without the flag, install refuses on a\n" +
			"missing namespace so a typo in --namespace can't silently create\n" +
			"a junk namespace (matches `ww workspace create` and\n" +
			"`ww agent create`).\n\n" +
			"--if-missing makes the install idempotent: when the operator is\n" +
			"already installed, the command logs a no-op line and returns\n" +
			"success instead of refusing. Useful for scripts that want to\n" +
			"\"ensure the operator is present\" without caring whether this run\n" +
			"is the one that installed it.\n\n" +
			"-f/--values, --set, and --set-string overlay chart values exactly\n" +
			"like `helm`. Pass an env-specific values file to enable optional\n" +
			"chart features at install time, e.g.:\n" +
			"  ww operator install -f .agents/self/operator-values.yaml\n" +
			"`ww operator upgrade` reuses previously-applied values, so a value\n" +
			"set here survives later upgrades without re-passing -f.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runOperatorInstall(cmd.Context(), f, &vals, adopt, ifMissing, createNamespace)
		},
	}
	bindMutatingFlags(cmd, f)
	bindValuesFlags(cmd, &vals)
	cmd.Flags().BoolVar(&adopt, "adopt", false,
		"Proceed when operator CRDs already exist on the cluster without a Helm release "+
			"(ww will take over management via Helm)")
	cmd.Flags().BoolVar(&ifMissing, "if-missing", false,
		"Return success without acting when the operator is already installed (idempotent mode)")
	cmd.Flags().BoolVar(&createNamespace, "create-namespace", false,
		"Create the target namespace if it doesn't already exist (no-op otherwise)")
	return cmd
}

func runOperatorInstall(ctx context.Context, f *operatorFlags, vals *valuesFlags, adopt, ifMissing, createNamespace bool) error {
	values, err := vals.merged()
	if err != nil {
		return err
	}
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return operator.Install(ctx, target, cfg, resolver.ConfigFlags(), operator.InstallOptions{
		Namespace:       f.namespace,
		Adopt:           adopt,
		IfMissing:       ifMissing,
		CreateNamespace: createNamespace,
		AssumeYes:       assumeYes,
		DryRun:          f.dryRun,
		Values:          values,
		Out:             os.Stdout,
		In:              os.Stdin,
	})
}

func newOperatorUpgradeCmd(f *operatorFlags) *cobra.Command {
	var (
		force bool
		vals  valuesFlags
	)
	cmd := &cobra.Command{
		Use:   "upgrade",
		Short: "Upgrade the witwave-operator Helm release to the embedded chart version",
		Long: "Upgrades in place. The embedded chart's CRDs are server-side applied\n" +
			"first (this works around Helm's 'crds/ is install-only' semantics so\n" +
			"new CRD fields land on the apiserver before the Deployment rolls),\n" +
			"then the Helm upgrade runs. Refuses when no release exists — use\n" +
			"`ww operator install` first.\n\n" +
			"Values previously applied to the release are reused (Helm\n" +
			"reset-then-reuse): a plain `ww operator upgrade` keeps any optional\n" +
			"chart feature you enabled earlier while still adopting the embedded\n" +
			"chart's new defaults (so the operator image tracks the embedded\n" +
			"version). Pass -f/--values, --set, or --set-string to add or change\n" +
			"overrides on top — e.g. the first time you enable a feature:\n" +
			"  ww operator upgrade -f .agents/self/operator-values.yaml",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runOperatorUpgrade(cmd.Context(), f, &vals, force)
		},
	}
	bindMutatingFlags(cmd, f)
	bindValuesFlags(cmd, &vals)
	cmd.Flags().BoolVar(&force, "force", false,
		"Reserved for future skew-policy overrides; accepted today for flag compatibility")
	return cmd
}

func runOperatorUpgrade(ctx context.Context, f *operatorFlags, vals *valuesFlags, force bool) error {
	values, err := vals.merged()
	if err != nil {
		return err
	}
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return operator.Upgrade(ctx, target, cfg, resolver.ConfigFlags(), operator.UpgradeOptions{
		Namespace: f.namespace,
		AssumeYes: assumeYes,
		DryRun:    f.dryRun,
		Force:     force,
		Values:    values,
		Out:       os.Stdout,
		In:        os.Stdin,
	})
}

func newOperatorUninstallCmd(f *operatorFlags) *cobra.Command {
	var deleteCRDs, force bool
	cmd := &cobra.Command{
		Use:   "uninstall",
		Short: "Uninstall the witwave-operator Helm release",
		Long: "Removes the operator's Helm release. By default CRDs and the CRs\n" +
			"they own are preserved — use --delete-crds to remove them too\n" +
			"(refuses when any CRs exist; --force overrides with a warning).",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runOperatorUninstall(cmd.Context(), f, deleteCRDs, force)
		},
	}
	bindMutatingFlags(cmd, f)
	cmd.Flags().BoolVar(&deleteCRDs, "delete-crds", false,
		"Also delete the WitwaveAgent / WitwavePrompt CRDs (refuses when live CRs exist; --force overrides)")
	cmd.Flags().BoolVar(&force, "force", false,
		"Override safety gates (cascade-delete CRs when --delete-crds + live CRs; dangerous)")
	return cmd
}

func runOperatorUninstall(ctx context.Context, f *operatorFlags, deleteCRDs, force bool) error {
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	assumeYes := f.assumeYes || os.Getenv("WW_ASSUME_YES") == "true"
	return operator.Uninstall(ctx, target, cfg, resolver.ConfigFlags(), operator.UninstallOptions{
		Namespace:  f.namespace,
		DeleteCRDs: deleteCRDs,
		Force:      force,
		AssumeYes:  assumeYes,
		DryRun:     f.dryRun,
		Out:        os.Stdout,
		In:         os.Stdin,
	})
}
