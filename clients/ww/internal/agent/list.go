package agent

import (
	"context"
	"fmt"
	"io"
	"strings"
	"text/tabwriter"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"
)

// ListOptions controls which WitwaveAgent CRs are returned.
//
// AllNamespaces is the default shape for `ww agent list` (see DESIGN.md
// NS-3 — list spans the cluster unless the user explicitly narrows it).
// Setting Namespace scopes to that namespace; leaving both empty +
// AllNamespaces=false is a caller bug and produces a namespace-less
// list call (which the apiserver rejects).
type ListOptions struct {
	Namespace     string
	AllNamespaces bool
	Out           io.Writer
}

// AgentSummary is a render-ready view of one WitwaveAgent. Flat enough
// for a tview.Table cell or a tabwriter row; retains a pointer to the
// raw unstructured object so callers that need more fields (TUI drill-
// down, JSON emitters) don't have to re-fetch.
type AgentSummary struct {
	Namespace string
	Name      string
	// Team is the value of the witwave.ai/team label, or empty string
	// when the agent is ungrouped (lands in the namespace-wide manifest).
	Team string
	// Phase is .status.phase or "Pending" when the CR hasn't been
	// reconciled yet.
	Phase string
	// Ready is .status.readyReplicas (0 when unset).
	Ready int64
	// Disabled is true when spec.enabled is explicitly false. Disabled
	// agents may retain stale status from the last active Deployment.
	Disabled bool
	// Backends is the ordered list of spec.backends[*].name.
	Backends []string
	// Created is the CR's creation timestamp, raw. Callers format it.
	Created time.Time
	// Raw is the underlying CR so drill-down views can render extra
	// fields without a second round-trip. Callers MUST NOT mutate.
	Raw *unstructured.Unstructured
}

// ListAgents returns summaries for the agents in scope. Shared data
// path between `ww agent list` (CLI) and the TUI — both format the
// same shape, neither re-derives status fields. Namespace/AllNamespaces
// resolution matches the List wrapper below.
func ListAgents(ctx context.Context, cfg *rest.Config, opts ListOptions) ([]AgentSummary, error) {
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("build dynamic client: %w", err)
	}
	var items *unstructured.UnstructuredList
	if opts.AllNamespaces {
		items, err = dyn.Resource(GVR()).List(ctx, metav1.ListOptions{})
	} else {
		items, err = dyn.Resource(GVR()).Namespace(opts.Namespace).List(ctx, metav1.ListOptions{})
	}
	if err != nil {
		return nil, fmt.Errorf("list agents: %w", err)
	}
	out := make([]AgentSummary, 0, len(items.Items))
	for i := range items.Items {
		cr := &items.Items[i]
		out = append(out, agentSummary(cr))
	}
	return out, nil
}

func agentSummary(cr *unstructured.Unstructured) AgentSummary {
	s := AgentSummary{
		Namespace: cr.GetNamespace(),
		Name:      cr.GetName(),
		Team:      cr.GetLabels()[TeamLabel],
		Phase:     readPhase(cr),
		Created:   cr.GetCreationTimestamp().Time,
		Raw:       cr,
	}
	if s.Phase == "" {
		s.Phase = "Pending"
	}
	if v, found, err := unstructured.NestedInt64(cr.Object, "status", "readyReplicas"); err == nil && found {
		s.Ready = v
	}
	if enabled, found, err := unstructured.NestedBool(cr.Object, "spec", "enabled"); err == nil && found && !enabled {
		s.Disabled = true
	}
	if backends, found, err := unstructured.NestedSlice(cr.Object, "spec", "backends"); err == nil && found {
		for _, b := range backends {
			m, ok := b.(map[string]interface{})
			if !ok {
				continue
			}
			if n, ok := m["name"].(string); ok {
				s.Backends = append(s.Backends, n)
			}
		}
	}
	return s
}

// List renders a table of WitwaveAgent CRs to opts.Out. Columns match
// the CRD's additionalPrinterColumns so operators see the same fields
// they'd see from `kubectl get wwa`. The NAMESPACE column is always
// shown — kept uniform regardless of scope so users can grep/sort by
// namespace without worrying about which mode they ran in. Thin
// formatter over ListAgents so the TUI shares the same data path.
func List(ctx context.Context, cfg *rest.Config, opts ListOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("ListOptions.Out is required")
	}
	summaries, err := ListAgents(ctx, cfg, opts)
	if err != nil {
		return err
	}
	if len(summaries) == 0 {
		if opts.AllNamespaces {
			fmt.Fprintln(opts.Out, "No WitwaveAgents found in any namespace.")
		} else {
			fmt.Fprintf(opts.Out, "No WitwaveAgents found in namespace %q.\n", opts.Namespace)
		}
		return nil
	}
	return renderList(opts.Out, summaries)
}

func renderList(out io.Writer, summaries []AgentSummary) error {
	tw := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(tw, "NAMESPACE\tNAME\tENABLED\tPHASE\tREADY\tBACKENDS\tAGE")
	for _, s := range summaries {
		backends := strings.Join(s.Backends, ",")
		if backends == "" {
			backends = "-"
		}
		fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%d\t%s\t%s\n",
			s.Namespace, s.Name, enabledDisplay(s), s.Phase, s.Ready, backends, FormatAge(s.Created),
		)
	}
	return tw.Flush()
}

func enabledDisplay(s AgentSummary) string {
	if s.Disabled {
		return "false"
	}
	return "true"
}

// FormatAge mirrors kubectl's age column: 10s, 5m, 2h, 3d. Intentionally
// lossy — the precise timestamp is available via `ww agent status`.
// Exported so the TUI can render ages the same way as the CLI table.
func FormatAge(t time.Time) string {
	if t.IsZero() {
		return "-"
	}
	d := time.Since(t)
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%ds", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	}
}
