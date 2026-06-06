package operator

import (
	"context"
	"errors"
	"fmt"
	"strings"

	authv1 "k8s.io/api/authorization/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
)

// InstallPreflightResult is the outcome of the singleton-detection +
// CRD-presence probe that gates `ww operator install`. The four cells
// of the matrix in #1477 map to this struct.
type InstallPreflightResult struct {
	// ReleaseNamespace is the namespace where an existing Helm release
	// was found (cluster-wide scan). Empty means no release exists.
	ReleaseNamespace string
	// CRDsPresent is true when BOTH operator CRDs exist on the cluster.
	CRDsPresent bool

	// Decision the caller MUST honour.
	Action PreflightAction
	// Reason is a human-readable summary for logs + banner.
	Reason string
}

// PreflightAction enumerates the four matrix outcomes.
type PreflightAction string

const (
	// ActionCleanInstall means no existing Helm release and no operator
	// CRDs were found on the cluster, so `ww operator install` may
	// proceed without further user input.
	ActionCleanInstall PreflightAction = "clean-install"
	// ActionAdoptCRDs means the operator CRDs already exist on the
	// cluster but no Helm release was found — someone installed via
	// `kubectl apply` or hand-crafted manifests. The caller must require
	// an explicit `--adopt` flag before proceeding so ww takes over CRD
	// management via Helm rather than colliding with the prior
	// installer.
	ActionAdoptCRDs PreflightAction = "adopt-crds"
	// ActionRefuseExists means both a Helm release and the operator CRDs
	// are present: the operator is already installed. Callers MUST
	// refuse to re-install and direct the user to `ww operator upgrade`
	// or `ww operator uninstall` instead.
	ActionRefuseExists PreflightAction = "refuse-already-installed"
	// ActionRefuseCorrupt means a Helm release exists but the operator
	// CRDs are missing — an inconsistent state ww refuses to recover
	// from automatically. The caller surfaces the namespace of the
	// dangling release and asks the user to investigate manually.
	ActionRefuseCorrupt PreflightAction = "refuse-corrupt-state"
)

// CheckInstall scans the cluster for the singleton pre-conditions.
// Never mutates state. Returns a structured result; callers render the
// matching message and decide whether to proceed (caller may choose to
// proceed on ActionAdoptCRDs if the user passed --adopt; otherwise it
// stops).
func CheckInstall(ctx context.Context, k8s kubernetes.Interface, dyn dynamic.Interface) (*InstallPreflightResult, error) {
	ns, err := FindReleaseClusterWide(ctx, k8s, ReleaseName)
	if err != nil {
		return nil, err
	}
	crds, err := InspectCRDs(ctx, dyn)
	if err != nil {
		return nil, err
	}

	crdsPresent := true
	for _, c := range crds {
		if !c.Found {
			crdsPresent = false
			break
		}
	}

	res := &InstallPreflightResult{
		ReleaseNamespace: ns,
		CRDsPresent:      crdsPresent,
	}

	switch {
	case ns == "" && !crdsPresent:
		res.Action = ActionCleanInstall
		res.Reason = "no existing Helm release; CRDs absent"
	case ns == "" && crdsPresent:
		res.Action = ActionAdoptCRDs
		res.Reason = "operator CRDs exist on the cluster but no Helm release found — " +
			"someone installed via kubectl apply or hand-crafted manifests. Pass " +
			"--adopt to let ww take over management via Helm."
	case ns != "" && crdsPresent:
		res.Action = ActionRefuseExists
		res.Reason = fmt.Sprintf("witwave-operator is already installed in namespace %q. "+
			"Use `ww operator upgrade` to change versions, or `ww operator uninstall` first.", ns)
	case ns != "" && !crdsPresent:
		res.Action = ActionRefuseCorrupt
		res.Reason = fmt.Sprintf("found Helm release in namespace %q but operator CRDs are missing. "+
			"This is corrupt state — investigate manually before re-installing.", ns)
	}
	return res, nil
}

// RBACCheck represents a single permission ww needs at install-time.
// Group + Resource + Verb match Kubernetes' SelfSubjectAccessReview
// vocabulary. Namespace may be empty for cluster-scoped resources.
type RBACCheck struct {
	Group     string
	Resource  string
	Verb      string
	Namespace string // "" for cluster-scoped
}

// InstallRBACRequirements lists the minimum permissions ww needs to
// install the operator. Derived from the chart's template set —
// Deployment, Service, ServiceAccount, ClusterRole, ClusterRoleBinding,
// Role, RoleBinding, CRDs.
func InstallRBACRequirements(namespace string) []RBACCheck {
	return []RBACCheck{
		{Group: "", Resource: "namespaces", Verb: "create"},
		{Group: "", Resource: "namespaces", Verb: "get"},
		{Group: "", Resource: "secrets", Verb: "create", Namespace: namespace},
		{Group: "", Resource: "services", Verb: "create", Namespace: namespace},
		{Group: "", Resource: "serviceaccounts", Verb: "create", Namespace: namespace},
		{Group: "apps", Resource: "deployments", Verb: "create", Namespace: namespace},
		{Group: "rbac.authorization.k8s.io", Resource: "clusterroles", Verb: "create"},
		{Group: "rbac.authorization.k8s.io", Resource: "clusterrolebindings", Verb: "create"},
		{Group: "rbac.authorization.k8s.io", Resource: "roles", Verb: "create", Namespace: namespace},
		{Group: "rbac.authorization.k8s.io", Resource: "rolebindings", Verb: "create", Namespace: namespace},
		{Group: "apiextensions.k8s.io", Resource: "customresourcedefinitions", Verb: "create"},
		{Group: "apiextensions.k8s.io", Resource: "customresourcedefinitions", Verb: "update"},
	}
}

// CheckRBAC runs a SelfSubjectAccessReview for each required permission
// and returns the list of missing verbs. Empty slice means "all good".
//
// Caveat called out in #1477: SAR tells you what you CAN do at the
// moment of the call — it doesn't guarantee success later. Admission
// webhooks, quotas, and finalizers can still reject. Error messages in
// the calling command MUST distinguish "preflight said yes but install
// failed at step X" from "preflight said no, stopping here."
func CheckRBAC(ctx context.Context, k8s kubernetes.Interface, checks []RBACCheck) ([]RBACCheck, error) {
	var missing []RBACCheck
	for _, c := range checks {
		ra := &authv1.SelfSubjectAccessReview{
			Spec: authv1.SelfSubjectAccessReviewSpec{
				ResourceAttributes: &authv1.ResourceAttributes{
					Namespace: c.Namespace,
					Verb:      c.Verb,
					Group:     c.Group,
					Resource:  c.Resource,
				},
			},
		}
		resp, err := k8s.AuthorizationV1().SelfSubjectAccessReviews().Create(ctx, ra, metav1.CreateOptions{})
		if err != nil {
			return nil, fmt.Errorf("SAR for %s/%s %s: %w", c.Group, c.Resource, c.Verb, err)
		}
		if !resp.Status.Allowed {
			missing = append(missing, c)
		}
	}
	return missing, nil
}

// FormatMissingRBAC turns the CheckRBAC output into a readable block.
// Placed in calling command's error so users see exactly which verbs
// they're missing.
func FormatMissingRBAC(missing []RBACCheck) string {
	if len(missing) == 0 {
		return ""
	}
	var lines []string
	for _, c := range missing {
		scope := "cluster-wide"
		if c.Namespace != "" {
			scope = fmt.Sprintf("namespace=%s", c.Namespace)
		}
		g := c.Group
		if g == "" {
			g = "core"
		}
		lines = append(lines, fmt.Sprintf("  - %s/%s: %s (%s)", g, c.Resource, c.Verb, scope))
	}
	return "missing Kubernetes permissions:\n" + strings.Join(lines, "\n")
}

// CheckUninstallSafety returns a non-nil error when uninstalling the
// operator would orphan live CRs. Returns nil when it is safe to
// uninstall. When `deleteCRDs` is true the check also refuses if any
// CRs exist at all (since --delete-crds would cascade-delete them).
func CheckUninstallSafety(ctx context.Context, dyn dynamic.Interface, deleteCRDs bool) error {
	counts, err := CountCRs(ctx, dyn)
	if err != nil {
		return err
	}
	total := 0
	for _, n := range counts {
		total += n
	}
	if total == 0 {
		return nil
	}
	if deleteCRDs {
		return fmt.Errorf("refusing to delete CRDs: %d managed custom resources still exist "+
			"(%d WitwaveAgent + %d WitwavePrompt). Delete them first with "+
			"`kubectl delete witwaveagent --all --all-namespaces` etc., or re-run "+
			"with --force", total, counts["WitwaveAgent"], counts["WitwavePrompt"])
	}
	// Soft case: release goes, but CRs + CRDs stay. That's the default
	// and safe — surface it as a notice, not an error.
	return nil
}

// ErrPreflightRefused is the sentinel callers return when singleton
// detection says stop. Cobra surfaces the message cleanly to the user.
var ErrPreflightRefused = errors.New("preflight refused the requested action")
