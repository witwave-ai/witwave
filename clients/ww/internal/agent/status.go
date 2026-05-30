package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"text/tabwriter"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"
)

// StatusOptions controls the `ww agent status` rendering.
//
// JSON switches from the curated text view to a single machine-readable
// object (harness + per-backend versions, plus generation /
// observedGeneration so an upgrade can confirm the operator has
// reconciled the new image before declaring success).
type StatusOptions struct {
	Name      string
	Namespace string
	JSON      bool
	Out       io.Writer
}

// Status fetches the WitwaveAgent CR and prints a compact, curated view
// of its current state: metadata, backends, last-reconcile history.
// This is the ww equivalent of `kubectl describe wwa <name>` minus the
// yaml noise. With opts.JSON it emits one StatusJSON object instead.
func Status(ctx context.Context, cfg *rest.Config, opts StatusOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("StatusOptions.Out is required")
	}
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build dynamic client: %w", err)
	}

	cr, err := dyn.Resource(GVR()).Namespace(opts.Namespace).Get(ctx, opts.Name, metav1.GetOptions{})
	if err != nil {
		if apierrors.IsNotFound(err) {
			return fmt.Errorf("WitwaveAgent %q not found in namespace %q", opts.Name, opts.Namespace)
		}
		return fmt.Errorf("get agent: %w", err)
	}

	if opts.JSON {
		return renderStatusJSON(opts.Out, cr)
	}
	renderStatus(opts.Out, cr)
	return nil
}

func renderStatus(out io.Writer, cr *unstructured.Unstructured) {
	name := cr.GetName()
	ns := cr.GetNamespace()
	phase := readPhase(cr)
	if phase == "" {
		phase = "Pending"
	}

	fmt.Fprintf(out, "WitwaveAgent: %s\n", name)
	fmt.Fprintf(out, "Namespace:    %s\n", ns)
	if enabled, found, err := unstructured.NestedBool(cr.Object, "spec", "enabled"); err == nil && found {
		fmt.Fprintf(out, "Enabled:      %t\n", enabled)
	} else {
		fmt.Fprintln(out, "Enabled:      true")
	}
	fmt.Fprintf(out, "Phase:        %s\n", phase)
	if harness := imageFromCR(cr, "spec", "image"); harness.Repository != "" {
		fmt.Fprintf(out, "Image:        %s\n", harness.Ref())
	}
	if ts := cr.GetCreationTimestamp(); !ts.IsZero() {
		fmt.Fprintf(out, "Age:          %s\n", FormatAge(ts.Time))
	}

	if ready, found, err := unstructured.NestedInt64(cr.Object, "status", "readyReplicas"); err == nil && found {
		fmt.Fprintf(out, "Ready:        %d\n", ready)
	}

	if msg, found, err := unstructured.NestedString(cr.Object, "status", "message"); err == nil && found && msg != "" {
		fmt.Fprintf(out, "Message:      %s\n", msg)
	}

	fmt.Fprintln(out)
	renderBackends(out, cr)
	renderReconcileHistory(out, cr)
}

func renderBackends(out io.Writer, cr *unstructured.Unstructured) {
	backends, found, err := unstructured.NestedSlice(cr.Object, "spec", "backends")
	if err != nil || !found || len(backends) == 0 {
		return
	}
	fmt.Fprintln(out, "Backends:")
	tw := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(tw, "  NAME\tIMAGE\tPORT\tMODEL")
	for _, b := range backends {
		m, ok := b.(map[string]any)
		if !ok {
			continue
		}
		name, _ := m["name"].(string)
		port := ""
		if v, ok := m["port"].(int64); ok {
			port = fmt.Sprintf("%d", v)
		}
		model, _ := m["model"].(string)
		if model == "" {
			model = "-"
		}
		image := imageFromMap(m["image"]).Ref()
		fmt.Fprintf(tw, "  %s\t%s\t%s\t%s\n", name, image, port, model)
	}
	_ = tw.Flush()
	fmt.Fprintln(out)
}

// StatusJSON is the machine-readable view emitted by `ww agent status
// <name> --json`. Adds generation / observedGeneration on top of the
// list shape so an upgrade can confirm the operator has observed the
// new spec (observedGeneration >= generation) before checking readiness.
type StatusJSON struct {
	Namespace          string           `json:"namespace"`
	Name               string           `json:"name"`
	Enabled            bool             `json:"enabled"`
	Phase              string           `json:"phase"`
	Ready              int64            `json:"ready"`
	Age                string           `json:"age"`
	Message            string           `json:"message,omitempty"`
	Generation         int64            `json:"generation"`
	ObservedGeneration int64            `json:"observedGeneration"`
	Harness            AgentImage       `json:"harness"`
	Backends           []BackendSummary `json:"backends"`
}

func renderStatusJSON(out io.Writer, cr *unstructured.Unstructured) error {
	s := agentSummary(cr)
	sj := StatusJSON{
		Namespace:  s.Namespace,
		Name:       s.Name,
		Enabled:    !s.Disabled,
		Phase:      s.Phase,
		Ready:      s.Ready,
		Age:        FormatAge(s.Created),
		Generation: cr.GetGeneration(),
		Harness:    s.Harness,
		Backends:   s.BackendImages,
	}
	if sj.Backends == nil {
		sj.Backends = []BackendSummary{}
	}
	if msg, found, err := unstructured.NestedString(cr.Object, "status", "message"); err == nil && found {
		sj.Message = msg
	}
	if og, found, err := unstructured.NestedInt64(cr.Object, "status", "observedGeneration"); err == nil && found {
		sj.ObservedGeneration = og
	}
	enc := json.NewEncoder(out)
	enc.SetIndent("", "  ")
	return enc.Encode(sj)
}

func renderReconcileHistory(out io.Writer, cr *unstructured.Unstructured) {
	history, found, err := unstructured.NestedSlice(cr.Object, "status", "reconcileHistory")
	if err != nil || !found || len(history) == 0 {
		fmt.Fprintln(out, "Reconcile history: (none yet)")
		return
	}
	// Cap rendering to the last 5 entries to keep `ww agent status` output
	// scan-able. Full history is always available via `kubectl get wwa -o yaml`.
	const cap = 5
	start := max(len(history)-cap, 0)
	fmt.Fprintf(out, "Reconcile history (last %d of %d):\n", len(history)-start, len(history))
	tw := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(tw, "  TIME\tPHASE\tREASON")
	for _, h := range history[start:] {
		m, ok := h.(map[string]any)
		if !ok {
			continue
		}
		t, _ := m["time"].(string)
		phase, _ := m["phase"].(string)
		reason, _ := m["reason"].(string)
		if reason == "" {
			reason = "-"
		}
		fmt.Fprintf(tw, "  %s\t%s\t%s\n", t, phase, strings.TrimSpace(reason))
	}
	_ = tw.Flush()
}
