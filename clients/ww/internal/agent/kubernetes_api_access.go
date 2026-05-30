package agent

import (
	"context"
	"fmt"
	"io"
	"strings"
	"time"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"

	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
)

const (
	// KubernetesApiAccessModeReadOnly matches the CRD's conservative
	// diagnostics preset.
	KubernetesApiAccessModeReadOnly = "readOnly"
	// KubernetesApiAccessModeNamespaceWrite matches the CRD's bounded
	// namespace-local remediation preset.
	KubernetesApiAccessModeNamespaceWrite = "namespaceWrite"
	// KubernetesApiAccessModeAgentLifecycle matches the CRD's preset that
	// adds patch on witwaveagents to namespaceWrite, so an Agent-Resources
	// agent can run `ww agent upgrade` against its peers.
	KubernetesApiAccessModeAgentLifecycle = "agentLifecycle"
)

// KubernetesApiAccessSpec mirrors spec.kubernetesApiAccess on the
// WitwaveAgent CR without importing the operator API into the CLI.
type KubernetesApiAccessSpec struct {
	Enabled bool
	Name    string
	Mode    string
}

// NewKubernetesApiAccessSpec returns an enabled access spec using the
// supplied mode, defaulting to readOnly when mode is blank.
func NewKubernetesApiAccessSpec(mode string) (*KubernetesApiAccessSpec, error) {
	normalized, err := NormalizeKubernetesApiAccessMode(mode)
	if err != nil {
		return nil, err
	}
	return &KubernetesApiAccessSpec{Enabled: true, Mode: normalized}, nil
}

// NormalizeKubernetesApiAccessMode accepts the canonical CRD values plus a
// few human shortcuts that are common at the CLI.
func NormalizeKubernetesApiAccessMode(mode string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "", "readonly", "read-only", "read_only", "ro", "r/o":
		return KubernetesApiAccessModeReadOnly, nil
	case "namespacewrite", "namespace-write", "namespace_write", "write", "rw":
		return KubernetesApiAccessModeNamespaceWrite, nil
	case "agentlifecycle", "agent-lifecycle", "agent_lifecycle", "lifecycle":
		return KubernetesApiAccessModeAgentLifecycle, nil
	default:
		return "", fmt.Errorf(
			"unsupported Kubernetes API access mode %q; valid modes: %s, %s, %s",
			mode,
			KubernetesApiAccessModeReadOnly,
			KubernetesApiAccessModeNamespaceWrite,
			KubernetesApiAccessModeAgentLifecycle,
		)
	}
}

// KubernetesApiAccessOptions controls `ww agent kubernetes-api-access *`.
type KubernetesApiAccessOptions struct {
	Name      string
	Namespace string
	Mode      string

	Wait    bool
	Timeout time.Duration

	AssumeYes bool
	DryRun    bool
	Out       io.Writer
	In        io.Reader
}

// KubernetesApiAccessEnable enables or updates operator-managed Kubernetes
// API access on an existing WitwaveAgent CR.
func KubernetesApiAccessEnable(
	ctx context.Context,
	target *k8s.Target,
	cfg *rest.Config,
	opts KubernetesApiAccessOptions,
) error {
	if opts.Out == nil {
		return fmt.Errorf("KubernetesApiAccessOptions.Out is required")
	}
	if opts.Name == "" {
		return fmt.Errorf("KubernetesApiAccessOptions.Name is required")
	}
	if opts.Namespace == "" {
		return fmt.Errorf("KubernetesApiAccessOptions.Namespace is required")
	}

	access, err := NewKubernetesApiAccessSpec(opts.Mode)
	if err != nil {
		return err
	}
	dyn, err := newDynamicClient(cfg)
	if err != nil {
		return err
	}
	cr, err := fetchAgentCR(ctx, dyn, opts.Namespace, opts.Name)
	if err != nil {
		return err
	}
	working := cr.DeepCopy()
	changed, err := applyKubernetesApiAccessInPlace(working, access)
	if err != nil {
		return err
	}
	if !changed {
		fmt.Fprintf(opts.Out, "WitwaveAgent %s/%s already has Kubernetes API access configured as %s.\n",
			opts.Namespace, opts.Name, access.Mode)
		return nil
	}

	plan := []k8s.PlanLine{
		{Key: "Action", Value: fmt.Sprintf("set Kubernetes API access on WitwaveAgent %q", opts.Name)},
		{Key: "Mode", Value: kubernetesApiAccessPlanValue(access.Mode)},
		{Key: "Identity", Value: "operator-managed ServiceAccount/Role/RoleBinding"},
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

	if _, err := updateAgentCR(ctx, dyn, working); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "Updated WitwaveAgent %s/%s.\n", opts.Namespace, opts.Name)
	if err := waitForKubernetesApiAccessRollout(ctx, cfg, dyn, opts, "Kubernetes API access updated"); err != nil {
		return err
	}
	return nil
}

// KubernetesApiAccessDisable removes operator-managed Kubernetes API access
// from an existing WitwaveAgent CR, returning it to the no-token default.
func KubernetesApiAccessDisable(
	ctx context.Context,
	target *k8s.Target,
	cfg *rest.Config,
	opts KubernetesApiAccessOptions,
) error {
	if opts.Out == nil {
		return fmt.Errorf("KubernetesApiAccessOptions.Out is required")
	}
	if opts.Name == "" {
		return fmt.Errorf("KubernetesApiAccessOptions.Name is required")
	}
	if opts.Namespace == "" {
		return fmt.Errorf("KubernetesApiAccessOptions.Namespace is required")
	}

	dyn, err := newDynamicClient(cfg)
	if err != nil {
		return err
	}
	cr, err := fetchAgentCR(ctx, dyn, opts.Namespace, opts.Name)
	if err != nil {
		return err
	}
	working := cr.DeepCopy()
	changed, err := removeKubernetesApiAccessInPlace(working)
	if err != nil {
		return err
	}
	if !changed {
		fmt.Fprintf(opts.Out, "WitwaveAgent %s/%s already has Kubernetes API access disabled.\n",
			opts.Namespace, opts.Name)
		return nil
	}

	plan := []k8s.PlanLine{
		{Key: "Action", Value: fmt.Sprintf("disable Kubernetes API access on WitwaveAgent %q", opts.Name)},
		{Key: "Identity", Value: "remove managed ServiceAccount/Role/RoleBinding"},
		{Key: "Pod token", Value: "return to the operator's no-token default"},
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

	if _, err := updateAgentCR(ctx, dyn, working); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "Updated WitwaveAgent %s/%s.\n", opts.Namespace, opts.Name)
	if err := waitForKubernetesApiAccessRollout(ctx, cfg, dyn, opts, "Kubernetes API access disabled"); err != nil {
		return err
	}
	return nil
}

func waitForKubernetesApiAccessRollout(
	ctx context.Context,
	cfg *rest.Config,
	dyn dynamic.Interface,
	opts KubernetesApiAccessOptions,
	done string,
) error {
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
	if err := waitForDeploymentRollout(ctx, k8sClient, opts.Namespace, opts.Name, timeout, opts.Out); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "\n%s for agent %s.\n", done, opts.Name)
	return nil
}

func applyKubernetesApiAccessInPlace(cr *unstructured.Unstructured, access *KubernetesApiAccessSpec) (bool, error) {
	if access == nil || !access.Enabled {
		return removeKubernetesApiAccessInPlace(cr)
	}
	mode, err := NormalizeKubernetesApiAccessMode(access.Mode)
	if err != nil {
		return false, err
	}
	if access.Name != "" {
		if err := ValidateName(access.Name); err != nil {
			return false, fmt.Errorf("kubernetesApiAccess.name %q: %w", access.Name, err)
		}
	}

	current, found, err := unstructured.NestedMap(cr.Object, "spec", "kubernetesApiAccess")
	if err != nil {
		return false, fmt.Errorf("read spec.kubernetesApiAccess: %w", err)
	}
	if !found {
		current = map[string]interface{}{}
	}
	changed := !found
	if enabled, found, err := unstructured.NestedBool(current, "enabled"); err != nil {
		return false, fmt.Errorf("read spec.kubernetesApiAccess.enabled: %w", err)
	} else if !found || !enabled {
		current["enabled"] = true
		changed = true
	}
	if got, _, err := unstructured.NestedString(current, "mode"); err != nil {
		return false, fmt.Errorf("read spec.kubernetesApiAccess.mode: %w", err)
	} else if got != mode {
		current["mode"] = mode
		changed = true
	}
	if access.Name != "" {
		if got, _, err := unstructured.NestedString(current, "name"); err != nil {
			return false, fmt.Errorf("read spec.kubernetesApiAccess.name: %w", err)
		} else if got != access.Name {
			current["name"] = access.Name
			changed = true
		}
	}
	if !changed {
		return false, nil
	}
	if err := unstructured.SetNestedMap(cr.Object, current, "spec", "kubernetesApiAccess"); err != nil {
		return false, fmt.Errorf("set spec.kubernetesApiAccess: %w", err)
	}
	return true, nil
}

func removeKubernetesApiAccessInPlace(cr *unstructured.Unstructured) (bool, error) {
	if _, found, err := unstructured.NestedMap(cr.Object, "spec", "kubernetesApiAccess"); err != nil {
		return false, fmt.Errorf("read spec.kubernetesApiAccess: %w", err)
	} else if !found {
		return false, nil
	}
	unstructured.RemoveNestedField(cr.Object, "spec", "kubernetesApiAccess")
	return true, nil
}

func kubernetesApiAccessPlanValue(mode string) string {
	switch mode {
	case KubernetesApiAccessModeNamespaceWrite:
		return "namespaceWrite (bounded namespace-local remediation; no secrets/RBAC/cluster resources)"
	case KubernetesApiAccessModeAgentLifecycle:
		return "agentLifecycle (namespaceWrite + patch on witwaveagents for ww agent upgrade)"
	default:
		return "readOnly (get/list/watch + pod logs; no mutating verbs)"
	}
}
