package agent

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"text/tabwriter"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"

	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
	"k8s.io/cli-runtime/pkg/genericclioptions"
	"k8s.io/client-go/rest"
)

// CreateOptions collects the runtime inputs for `ww agent create`.
type CreateOptions struct {
	Name      string
	Namespace string

	// Backends is the list of declared backends. Empty → a single
	// default-echo backend (the hello-world shortcut). Populate via
	// ParseBackendSpecs to turn the repeatable --backend cobra flag
	// into structured entries.
	Backends []BackendSpec

	// CLIVersion from cmd.Version; used to resolve image tags.
	CLIVersion string

	// CreatedBy captures the invoking command (e.g. "ww agent create hello")
	// for the CR's created-by annotation.
	CreatedBy string

	// AssumeYes skips the preflight confirmation. --yes / WW_ASSUME_YES.
	AssumeYes bool
	// DryRun renders the banner and exits without calling the API server.
	DryRun bool

	// CreateNamespace, when true, ensures the target namespace exists
	// before creating the CR (creates it if missing, no-op if it already
	// exists). Mirrors `helm install --create-namespace` semantics.
	CreateNamespace bool

	// Team, when non-empty, stamps the `witwave.ai/team=<team>` label on
	// the CR at creation time. Equivalent to running `ww agent team
	// join` post-create but avoids the race where the agent briefly
	// joins the namespace-wide manifest before the label lands.
	Team string

	// BackendAuth carries the resolved --auth / --backend-secret-from-env /
	// --auth-secret flag values, one per backend the user wants to
	// wire credentials for. Backends not referenced here create
	// without a credentials field (fine for echo, a footgun for
	// claude/openai/gemini — the operator will still start the pod,
	// but the backend will error on first request with missing-key
	// diagnostics).
	BackendAuth []BackendAuthResolver

	// WorkspaceRefs lists WitwaveWorkspace names to bind the agent to at
	// creation time. Each name must resolve to a WitwaveWorkspace in the
	// agent's namespace (v1alpha1 same-namespace binding only). Verified
	// at preflight before the CR is admitted so a typo doesn't silently
	// produce an admitted-but-unbound agent. Empty (the default) is
	// fine — callers can attach with `ww workspace bind` later.
	WorkspaceRefs []string

	// GitSyncs declares git-sync entries the operator should reconcile
	// onto the agent's pod (one cloned repo per entry into /git/<name>).
	// GitMappings (below) reference these by name. Cross-flag
	// validation (gitsync-map.GitSync exists, container exists, no duplicate
	// (container, dest)) runs in Create before Build.
	GitSyncs []GitSyncFlagSpec

	// GitMappings declares per-(container, dest) copy operations the
	// gitSync init script applies after each clone. Container is
	// "harness" or one of the agent's declared backend names; the
	// build path routes each entry into either Spec.GitMappings[] or
	// the matching BackendSpec.GitMappings[].
	GitMappings []GitMappingFlagSpec

	// GitSyncFromEnv, when non-nil, resolves a single per-agent gitSync
	// credential Secret from the named shell vars and stamps it onto
	// every GitSyncs[] entry that doesn't already carry an explicit
	// --gitsync-secret reference. Mirrors --backend-secret-from-env's
	// "lift from shell env, mint a Secret" posture but targets the
	// gitSync sidecar instead of a backend container.
	GitSyncFromEnv *GitSyncFromEnvSpec

	// NoMetrics opts the agent out of the operator's default-on metrics
	// posture. When true the CR is built with spec.metrics.enabled set
	// to a literal false (vs left unset, which the CRD default would
	// resolve to true). Threaded from the --no-metrics CLI flag.
	NoMetrics bool

	// HarnessEnv stamps spec.env[] on the generated CR — plain
	// (non-secret) env vars on the harness container. Threaded from
	// the --harness-env CLI flag via ParseHarnessEnvs. Backend-targeted
	// env vars go on BackendSpec.Env via --backend-env instead.
	HarnessEnv map[string]string

	// RuntimeStorage stamps spec.runtimeStorage for durable harness runtime
	// state. Populated by higher-level persistence sugar such as
	// --with-persistence.
	RuntimeStorage *RuntimeStorageSpec

	// KubernetesApiAccess stamps spec.kubernetesApiAccess so the operator
	// creates and wires a per-agent Kubernetes API identity at creation time.
	KubernetesApiAccess *KubernetesApiAccessSpec

	// Wait controls whether we block after Create until the CR's
	// status.phase flips to Ready. Timeout bounds the wait.
	Wait    bool
	Timeout time.Duration

	// Out + In route UI to/from the caller. Usually os.Stdout / os.Stdin.
	Out io.Writer
	In  io.Reader
}

// Create applies a WitwaveAgent CR to the target cluster. Flow:
//
//  1. Build the unstructured CR from opts + defaults.
//  2. Render preflight banner via k8s.Confirm (honours --yes / --dry-run /
//     local-cluster heuristic per DESIGN.md KC-4).
//  3. Create via dynamic client. AlreadyExists is surfaced cleanly so a
//     user re-running with the same name gets a clear error, not a panic.
//  4. Optionally wait for status.phase == Ready.
func Create(
	ctx context.Context,
	target *k8s.Target,
	cfg *rest.Config,
	flags *genericclioptions.ConfigFlags,
	opts CreateOptions,
) error {
	if opts.Out == nil {
		return fmt.Errorf("CreateOptions.Out is required")
	}
	// Fallback to a single-echo backend when caller didn't supply any —
	// matches the legacy `Backend: ""` behaviour so the hello-world
	// `ww agent create hello` (no flags) stays unchanged.
	backends := opts.Backends
	if len(backends) == 0 {
		backends = []BackendSpec{{Name: DefaultBackend, Type: DefaultBackend, Port: BackendPort(0)}}
	}

	// Validate every --auth* flag targets a real backend before we
	// touch the cluster. Cheap to do up-front; saves the user from
	// "typed `clade` instead of `claude`" confusion after a partial run.
	if err := validateBackendAuthTargets(opts.BackendAuth, backends); err != nil {
		return err
	}

	// Cross-flag gitSync / gitMap validation. Catches dangling
	// references (gitsync-map.GitSync that doesn't match any --gitsync)
	// and bad container targets (typo in <container>= prefix) before
	// we touch the cluster.
	if err := ValidateGitFlags(opts.GitSyncs, opts.GitMappings, backends); err != nil {
		return err
	}

	plan := []k8s.PlanLine{
		{Key: "Action", Value: fmt.Sprintf("create WitwaveAgent %q", opts.Name)},
		{Key: "Backends", Value: summariseBackends(backends)},
		{Key: "Harness image", Value: HarnessImage(opts.CLIVersion)},
	}
	if opts.Team != "" {
		plan = append(plan, k8s.PlanLine{
			Key:   "Team",
			Value: fmt.Sprintf("%q (joins witwave-manifest-%s)", opts.Team, opts.Team),
		})
	}
	if len(opts.WorkspaceRefs) > 0 {
		plan = append(plan, k8s.PlanLine{
			Key:   "Workspaces",
			Value: strings.Join(opts.WorkspaceRefs, ", "),
		})
	}
	if len(opts.GitSyncs) > 0 {
		names := make([]string, 0, len(opts.GitSyncs))
		for _, s := range opts.GitSyncs {
			names = append(names, s.Name)
		}
		plan = append(plan, k8s.PlanLine{
			Key:   "GitSyncs",
			Value: strings.Join(names, ", "),
		})
	}
	if len(opts.GitMappings) > 0 {
		plan = append(plan, k8s.PlanLine{
			Key: "GitMappings",
			Value: fmt.Sprintf("%d (across %d container(s))",
				len(opts.GitMappings), distinctContainers(opts.GitMappings)),
		})
	}
	// One line per backend that has credentials wired — lets the user
	// see exactly what will be referenced (or minted) before they
	// confirm, including which env var(s) the CLI is about to read.
	for _, r := range opts.BackendAuth {
		plan = append(plan, k8s.PlanLine{
			Key:   fmt.Sprintf("Auth (%s)", r.Backend),
			Value: summariseBackendAuth(r, opts.Name),
		})
	}
	if opts.GitSyncFromEnv != nil {
		plan = append(plan, k8s.PlanLine{
			Key: "Auth (gitsync)",
			Value: fmt.Sprintf("from env → mint Secret %q from (%s, %s)",
				gitsyncFromEnvSecretName(opts.Name),
				opts.GitSyncFromEnv.UserVar, opts.GitSyncFromEnv.PassVar),
		})
	}
	if opts.KubernetesApiAccess != nil {
		mode, err := NormalizeKubernetesApiAccessMode(opts.KubernetesApiAccess.Mode)
		if err != nil {
			return err
		}
		plan = append(plan, k8s.PlanLine{
			Key:   "Kubernetes API",
			Value: kubernetesApiAccessPlanValue(mode),
		})
	}
	if IsDevVersion(opts.CLIVersion) {
		plan = append(plan, k8s.PlanLine{
			Key:   "Note",
			Value: "dev build — images will resolve to :latest (floating tag)",
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

	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build dynamic client: %w", err)
	}

	if opts.CreateNamespace {
		if err := ensureNamespace(ctx, cfg, opts.Namespace, opts.Out); err != nil {
			return err
		}
	}

	// Verify every requested WitwaveWorkspace exists in the agent's namespace
	// before admitting the CR. v1alpha1 binds are same-namespace only —
	// catching a typo (or a namespace mismatch) here saves a round-trip
	// through admission/reconcile and a confusing "admitted but unbound"
	// state. Mirrors the verify-then-mutate pattern in `ww workspace bind`.
	if len(opts.WorkspaceRefs) > 0 {
		if err := verifyWorkspaceRefs(ctx, dyn, opts.Namespace, opts.WorkspaceRefs); err != nil {
			return err
		}
	}

	// Resolve per-backend credentials AFTER the namespace exists (so
	// ensureNamespace can precede us) but BEFORE Build(). The CR
	// carries credentials.existingSecret, so we need the Secret names
	// before constructing the object. Minting runs even when no
	// Secret needs creation (existing-secret mode just verifies the
	// Secret is present).
	if len(opts.BackendAuth) > 0 {
		k8sClient, err := newKubernetesClient(cfg)
		if err != nil {
			return err
		}
		for i := range backends {
			resolver := resolveBackendAuthFor(opts.BackendAuth, backends[i].Name)
			if resolver == nil {
				continue
			}
			secretName, err := resolver.resolve(ctx, k8sClient, opts.Namespace, opts.Name, backends[i].Type)
			if err != nil {
				return err
			}
			backends[i].CredentialSecret = secretName
		}
	}

	// Resolve --gitsync-secret-from-env: lift two shell vars into a single
	// per-agent gitSync Secret, then stamp the Secret reference onto
	// every GitSyncs[] entry that doesn't already carry an explicit
	// --gitsync-secret value (per-entry wins by precedence).
	if opts.GitSyncFromEnv != nil {
		k8sClient, err := newKubernetesClient(cfg)
		if err != nil {
			return err
		}
		secretName, err := ResolveGitSyncFromEnv(ctx, k8sClient, opts.Namespace, opts.Name, *opts.GitSyncFromEnv)
		if err != nil {
			return err
		}
		opts.GitSyncs = StampGitSyncSecretOnAll(opts.GitSyncs, secretName)
	}

	obj, err := Build(BuildOptions{
		Name:                opts.Name,
		Namespace:           opts.Namespace,
		Backends:            backends,
		CLIVersion:          opts.CLIVersion,
		CreatedBy:           opts.CreatedBy,
		Team:                opts.Team,
		WorkspaceRefs:       opts.WorkspaceRefs,
		GitSyncs:            opts.GitSyncs,
		GitMappings:         opts.GitMappings,
		NoMetrics:           opts.NoMetrics,
		HarnessEnv:          opts.HarnessEnv,
		RuntimeStorage:      opts.RuntimeStorage,
		KubernetesApiAccess: opts.KubernetesApiAccess,
	})
	if err != nil {
		return fmt.Errorf("build agent CR: %w", err)
	}

	created, err := dyn.Resource(GVR()).Namespace(opts.Namespace).Create(ctx, obj, metav1.CreateOptions{})
	if err != nil {
		if apierrors.IsAlreadyExists(err) {
			return fmt.Errorf(
				"WitwaveAgent %q already exists in namespace %q; delete it with `ww agent delete %s -n %s` or choose a different name",
				opts.Name, opts.Namespace, opts.Name, opts.Namespace,
			)
		}
		return fmt.Errorf("create agent: %w", err)
	}
	fmt.Fprintf(opts.Out, "Created WitwaveAgent %s in namespace %s (uid=%s).\n",
		created.GetName(), created.GetNamespace(), created.GetUID(),
	)

	if !opts.Wait {
		fmt.Fprintln(opts.Out, "Skipping readiness wait (--no-wait). Check with `ww agent status "+opts.Name+"`.")
		return nil
	}

	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 5 * time.Minute
	}
	fmt.Fprintf(opts.Out, "Waiting up to %s for agent to report Ready...\n", timeout)
	k8sClient, err := newKubernetesClient(cfg)
	if err != nil {
		return err
	}
	if err := waitForReady(ctx, dyn, k8sClient, opts.Namespace, opts.Name, timeout, opts.Out); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "\nAgent %s is ready.\n", opts.Name)
	fmt.Fprintln(opts.Out, "Next steps:")
	fmt.Fprintln(opts.Out, "  ww agent status "+opts.Name+"  # see pod + reconcile state")
	fmt.Fprintln(opts.Out, "  ww agent delete "+opts.Name+"  # clean up")
	return nil
}

// verifyWorkspaceRefs probes each requested WitwaveWorkspace by name in
// the agent's namespace. Returns a clean per-name error on the first
// missing one rather than admitting a CR with dangling refs the
// operator silently ignores. Same posture as the verify step in
// `ww workspace bind`.
func verifyWorkspaceRefs(ctx context.Context, dyn dynamic.Interface, namespace string, names []string) error {
	gvr := workspaceGVR()
	for _, name := range names {
		_, err := dyn.Resource(gvr).Namespace(namespace).Get(ctx, name, metav1.GetOptions{})
		if err == nil {
			continue
		}
		if apierrors.IsNotFound(err) {
			return fmt.Errorf(
				"WitwaveWorkspace %q not found in namespace %q — create it first with `ww workspace create %s -n %s` or fix the --workspace value",
				name, namespace, name, namespace,
			)
		}
		return fmt.Errorf("probe WitwaveWorkspace %q in %q: %w", name, namespace, err)
	}
	return nil
}

// workspaceGVR returns the GVR for WitwaveWorkspace. Inlined here (rather
// than imported from internal/workspace) because internal/workspace
// already imports this package — flipping the dependency would create a
// cycle.
func workspaceGVR() schema.GroupVersionResource {
	return schema.GroupVersionResource{Group: "witwave.ai", Version: "v1alpha1", Resource: "witwaveworkspaces"}
}

// distinctContainers counts the unique Container names referenced across
// a set of gitMappings. Used by the preflight banner to surface the
// fan-out shape ("3 (across 2 container(s))") without listing every
// destination path.
func distinctContainers(mappings []GitMappingFlagSpec) int {
	seen := map[string]struct{}{}
	for _, m := range mappings {
		seen[m.Container] = struct{}{}
	}
	return len(seen)
}

// ensureNamespace creates the target namespace if it doesn't already
// exist. Idempotent: a pre-existing namespace is treated as success.
// Respects the clientFactory indirection so tests can swap in a fake
// kubernetes.Interface.
func ensureNamespace(ctx context.Context, cfg *rest.Config, name string, out io.Writer) error {
	k8sClient, err := newKubernetesClient(cfg)
	if err != nil {
		return err
	}
	_, err = k8sClient.CoreV1().Namespaces().Get(ctx, name, metav1.GetOptions{})
	if err == nil {
		return nil // already exists
	}
	if !apierrors.IsNotFound(err) {
		return fmt.Errorf("check namespace %q: %w", name, err)
	}
	ns := &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{
			Name: name,
			Labels: map[string]string{
				LabelManagedBy: LabelManagedByWW,
			},
		},
	}
	if _, err := k8sClient.CoreV1().Namespaces().Create(ctx, ns, metav1.CreateOptions{}); err != nil {
		if apierrors.IsAlreadyExists(err) {
			return nil // race: created between our Get and Create
		}
		return fmt.Errorf("create namespace %q: %w", name, err)
	}
	fmt.Fprintf(out, "Created namespace %s (labelled %s=%s).\n", name, LabelManagedBy, LabelManagedByWW)
	return nil
}

// summariseBackends returns a compact "name:type/port" summary for the
// preflight banner. `echo-1:echo/8001, echo-2:echo/8002` — enough
// information for the user to confirm the shape at a glance.
// summariseBackendAuth renders one line for the preflight banner
// describing how a backend's credentials will be resolved. Deliberately
// non-secret: env var NAMES land in the log, values never.
func summariseBackendAuth(r BackendAuthResolver, agentName string) string {
	switch r.Mode {
	case BackendAuthProfile:
		return fmt.Sprintf(
			"profile %q → mint Secret %q from env (%s)",
			r.Profile,
			backendCredentialSecretName(agentName, r.Backend),
			strings.Join(envVarsForProfile(r), ", "),
		)
	case BackendAuthFromEnv:
		return fmt.Sprintf(
			"from env → mint Secret %q from (%s)",
			backendCredentialSecretName(agentName, r.Backend),
			strings.Join(r.EnvVars, ", "),
		)
	case BackendAuthExistingSecret:
		return fmt.Sprintf("reference existing Secret %q (verified, not modified)", r.ExistingSecret)
	}
	return "(no auth — backend will start without credentials)"
}

// envVarsForProfile looks up the env var list for a resolver in profile
// mode. Split out so summariseBackendAuth can describe the plan without
// triggering the resolver's error-surface for missing env vars — the
// real read happens later in resolve().
func envVarsForProfile(r BackendAuthResolver) []string {
	// The backend-type is not on the resolver (resolvers are keyed by
	// backend NAME, not type), so the banner search scans every known
	// catalog entry for a matching profile name. Collision between two
	// backend types on profile name would produce a stale banner, but
	// the catalog is tiny and profiles are already namespaced by
	// backend in ParseBackendAuth's validation.
	for _, profiles := range credentialProfiles {
		if p, ok := profiles[r.Profile]; ok {
			return p.EnvVars
		}
	}
	return []string{"?"}
}

func summariseBackends(backends []BackendSpec) string {
	if len(backends) == 0 {
		return "<none — default echo>"
	}
	parts := make([]string, 0, len(backends))
	for _, b := range backends {
		if b.Name == b.Type {
			parts = append(parts, fmt.Sprintf("%s/%d", b.Name, b.Port))
		} else {
			parts = append(parts, fmt.Sprintf("%s:%s/%d", b.Name, b.Type, b.Port))
		}
	}
	return strings.Join(parts, ", ")
}

// waitForReady polls the CR's .status.phase until it reads "Ready" or
// the timeout elapses. Poll interval is deliberately modest (2s) —
// operators reconcile on a handful of events, not on a busy-loop. On
// timeout, dumps the most recent CR + pod events so the user can tell
// "still pulling images" from "container crashlooping" without a
// follow-up `ww agent events` round-trip.
func waitForReady(ctx context.Context, dyn dynamic.Interface, k8sClient kubernetes.Interface, ns, name string, timeout time.Duration, out io.Writer) error {
	deadline := time.Now().Add(timeout)
	var lastPhase string
	for {
		if time.Now().After(deadline) {
			fmt.Fprintf(out, "\nTimed out after %s — recent events for %s/%s:\n", timeout, ns, name)
			dumpRecentEvents(ctx, k8sClient, ns, name, out)
			return fmt.Errorf("timed out after %s waiting for agent %q to report Ready (last phase: %q)", timeout, name, lastPhase)
		}

		cr, err := dyn.Resource(GVR()).Namespace(ns).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			// Transient Get errors shouldn't abort the wait; log and retry.
			fmt.Fprintf(out, "  (get failed: %v; retrying)\n", err)
		} else {
			phase := readPhase(cr)
			if phase != lastPhase {
				fmt.Fprintf(out, "  phase: %s\n", phase)
				lastPhase = phase
			}
			if phase == "Ready" {
				return nil
			}
		}

		select {
		case <-ctx.Done():
			return errors.New("wait cancelled")
		case <-time.After(2 * time.Second):
		}
	}
}

// dumpRecentEvents prints the last ~10 events tied to the agent's CR
// and its pods. Best-effort: any list error is logged but doesn't
// shadow the original timeout error the caller is about to return.
func dumpRecentEvents(ctx context.Context, k8sClient kubernetes.Interface, ns, agent string, out io.Writer) {
	crEvents, err := listEventsWithFieldSelector(ctx, k8sClient, ns,
		fields.AndSelectors(
			fields.OneTermEqualSelector("involvedObject.kind", Kind),
			fields.OneTermEqualSelector("involvedObject.name", agent),
		).String(),
	)
	if err != nil {
		fmt.Fprintf(out, "  (could not list CR events: %v)\n", err)
	}

	pods, err := selectAgentPods(ctx, k8sClient, ns, agent, "")
	if err != nil {
		fmt.Fprintf(out, "  (could not list agent pods: %v)\n", err)
	}
	var podEvents []corev1.Event
	for _, pod := range pods {
		evs, err := listEventsWithFieldSelector(ctx, k8sClient, ns,
			fields.AndSelectors(
				fields.OneTermEqualSelector("involvedObject.kind", "Pod"),
				fields.OneTermEqualSelector("involvedObject.name", pod),
			).String(),
		)
		if err != nil {
			continue
		}
		podEvents = append(podEvents, evs...)
	}

	merged := append(crEvents, podEvents...)
	if len(merged) == 0 {
		fmt.Fprintln(out, "  (no events found)")
		return
	}
	sort.SliceStable(merged, func(i, j int) bool {
		return eventTime(merged[i]).Before(eventTime(merged[j]))
	})
	const tail = 10
	if len(merged) > tail {
		merged = merged[len(merged)-tail:]
	}
	tw := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(tw, "  AGE\tTYPE\tOBJECT\tREASON\tMESSAGE")
	for _, e := range merged {
		obj := fmt.Sprintf("%s/%s", e.InvolvedObject.Kind, e.InvolvedObject.Name)
		fmt.Fprintf(tw, "  %s\t%s\t%s\t%s\t%s\n",
			FormatAge(eventTime(e)), e.Type, obj, e.Reason, e.Message,
		)
	}
	_ = tw.Flush()
	fmt.Fprintln(out, "\n  Hint: pod may still be progressing — check `ww agent status "+agent+"` or re-run with --timeout 10m.")
}

// readPhase pulls .status.phase from an unstructured CR. Returns an
// empty string when the field is absent — treated the same as "not yet
// reconciled" by the caller.
func readPhase(cr *unstructured.Unstructured) string {
	phase, found, err := unstructured.NestedString(cr.Object, "status", "phase")
	if err != nil || !found {
		return ""
	}
	return phase
}
