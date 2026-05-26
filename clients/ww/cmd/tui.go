package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
	"github.com/witwave-ai/witwave/clients/ww/internal/tui"
	"k8s.io/client-go/rest"
)

// newTuiCmd wires `ww tui` — the interactive terminal surface tracked
// in #1450. It is intentionally lighter than the full web dashboard,
// but it already provides a live agent list plus common per-agent
// actions; only the Enter-driven details page remains a stub.
func newTuiCmd() *cobra.Command {
	var kubeconfig, contextName, namespace string
	cmd := &cobra.Command{
		Use:   "tui",
		Short: "Open the interactive ww terminal UI",
		Long: "Launches a tview-based terminal UI for ww. The current\n" +
			"surface shows a live WitwaveAgent list, refreshes from the\n" +
			"Kubernetes API, and supports common row actions: add, delete,\n" +
			"send, and logs. Enter is reserved for a future per-agent\n" +
			"details page and currently displays a short hint.\n\n" +
			"Keybindings: ↑/↓ move, a add, d delete, s send, l logs,\n" +
			"r refresh, q/Esc/Ctrl-C quit.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runTui(kubeconfig, contextName, namespace, Version)
		},
	}
	cmd.Flags().StringVar(&kubeconfig, "kubeconfig", "",
		"Path to kubeconfig (overrides KUBECONFIG env var and ~/.kube/config)")
	cmd.Flags().StringVar(&contextName, "context", "",
		"Kubeconfig context to use (defaults to current-context)")
	cmd.Flags().StringVarP(&namespace, "namespace", "n", "",
		"Namespace to display and operate in (defaults to the context's namespace)")
	return cmd
}

// runTui resolves the requested kubeconfig context (best-effort —
// a failure does NOT block launch) and hands the resulting Target
// + REST config (or the diagnostic string) to the tview application.
func runTui(kubeconfig, contextName, namespace, version string) error {
	var target *k8s.Target
	var cfg *rest.Config
	var contextErr string

	r, err := k8s.NewResolver(k8s.Options{
		KubeconfigPath: kubeconfig,
		Context:        contextName,
		Namespace:      namespace,
	})
	if err != nil {
		// Soft-fail: the TUI still launches + renders "No cluster
		// configured" in place of the context block. Stays useful
		// for first-time users who haven't wired a kubeconfig yet.
		contextErr = fmt.Sprintf("No cluster configured: %s", err)
	} else {
		target = r.Target()
		cfg, err = r.REST()
		if err != nil {
			contextErr = fmt.Sprintf("Could not build REST client: %s", err)
		}
	}

	return tui.Run(version, target, cfg, contextErr)
}
