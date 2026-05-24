package agent

import (
	"context"
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
type StatusOptions struct {
	Name      string
	Namespace string
	Out       io.Writer
}

// Status fetches the WitwaveAgent CR and prints a compact, curated view
// of its current state: metadata, backends, last-reconcile history.
// This is the ww equivalent of `kubectl describe wwa <name>` minus the
// yaml noise.
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
		m, ok := b.(map[string]interface{})
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
		image := renderImageField(m)
		fmt.Fprintf(tw, "  %s\t%s\t%s\t%s\n", name, image, port, model)
	}
	_ = tw.Flush()
	fmt.Fprintln(out)
}

// renderImageField assembles a display string for the `image` subfield
// of a backend or harness spec. CRD shape: `{repository, tag?}`.
func renderImageField(m map[string]interface{}) string {
	img, _ := m["image"].(map[string]interface{})
	if img == nil {
		return "-"
	}
	repo, _ := img["repository"].(string)
	tag, _ := img["tag"].(string)
	if repo == "" {
		return "-"
	}
	if tag == "" {
		return repo
	}
	return repo + ":" + tag
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
	start := len(history) - cap
	if start < 0 {
		start = 0
	}
	fmt.Fprintf(out, "Reconcile history (last %d of %d):\n", len(history)-start, len(history))
	tw := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(tw, "  TIME\tPHASE\tREASON")
	for _, h := range history[start:] {
		m, ok := h.(map[string]interface{})
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
