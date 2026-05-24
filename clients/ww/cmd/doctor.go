package cmd

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/agent"
	"github.com/witwave-ai/witwave/clients/ww/internal/client"
	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
	"github.com/witwave-ai/witwave/clients/ww/internal/operator"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

type doctorFlags struct {
	operatorNamespace  string
	skipCluster        bool
	skipHarness        bool
	agents             []string
	requireAgentsReady bool
	strictAgentTags    bool
}

type doctorStatus string

const (
	doctorPass doctorStatus = "PASS"
	doctorWarn doctorStatus = "WARN"
	doctorFail doctorStatus = "FAIL"
	doctorSkip doctorStatus = "SKIP"
)

type doctorCheck struct {
	Name    string       `json:"name"`
	Status  doctorStatus `json:"status"`
	Details string       `json:"details,omitempty"`
}

type doctorReport struct {
	Kind      string        `json:"kind"`
	WWVersion string        `json:"ww_version"`
	Commit    string        `json:"commit,omitempty"`
	BuildDate string        `json:"build_date,omitempty"`
	CheckedAt string        `json:"checked_at"`
	Checks    []doctorCheck `json:"checks"`
}

func newDoctorCmd() *cobra.Command {
	f := &doctorFlags{}
	cmd := &cobra.Command{
		Use:   "doctor",
		Short: "Run opinionated Witwave diagnostics",
		Long: "Run opinionated diagnostics that stitch together harness, operator,\n" +
			"and agent checks. Start with `ww doctor release` after a release,\n" +
			"operator upgrade, or backend rollout.",
	}
	cmd.AddCommand(newDoctorReleaseCmd(f))
	return cmd
}

func newDoctorReleaseCmd(f *doctorFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "release",
		Short: "Check post-release health across ww, harness, operator, and agents",
		Long: "Runs a read-only post-release diagnostic pass. It checks the local ww\n" +
			"build metadata, the configured harness, the operator Helm release,\n" +
			"operator pods, CRDs, and WitwaveAgent CRs. Agent readiness and image\n" +
			"tag skew are warnings by default because agents may be intentionally\n" +
			"scaled down; use --agent or --require-agents-ready when this should\n" +
			"act as a rollout gate.",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runDoctorRelease(cmd.Context(), *f)
		},
	}
	cmd.Flags().StringVar(&f.operatorNamespace, "operator-namespace", operator.DefaultNamespace,
		"Namespace where the witwave-operator Helm release is installed")
	cmd.Flags().BoolVar(&f.skipCluster, "skip-cluster", false,
		"Skip Kubernetes/operator/agent CR checks")
	cmd.Flags().BoolVar(&f.skipHarness, "skip-harness", false,
		"Skip configured harness checks")
	cmd.Flags().StringArrayVar(&f.agents, "agent", nil,
		"Require the named WitwaveAgent to be present and Ready; repeatable")
	cmd.Flags().BoolVar(&f.requireAgentsReady, "require-agents-ready", false,
		"Fail when any inspected WitwaveAgent is not Ready")
	cmd.Flags().BoolVar(&f.strictAgentTags, "strict-agent-tags", false,
		"Fail when inspected agent harness/backend image tags differ from the operator appVersion")
	return cmd
}

func runDoctorRelease(ctx context.Context, f doctorFlags) error {
	out := OutFromCtx(ctx)
	c := ClientFromCtx(ctx)
	report := doctorReport{
		Kind:      "release",
		WWVersion: Version,
		Commit:    Commit,
		BuildDate: BuildDate,
		CheckedAt: time.Now().UTC().Format(time.RFC3339),
	}

	report.Checks = append(report.Checks, doctorCheck{
		Name:    "ww binary",
		Status:  doctorPass,
		Details: fmt.Sprintf("version=%s commit=%s built=%s", Version, Commit, BuildDate),
	})
	if f.skipHarness {
		report.Checks = append(report.Checks, doctorCheck{Name: "harness", Status: doctorSkip, Details: "--skip-harness"})
	} else {
		report.Checks = append(report.Checks, runHarnessReleaseChecks(ctx, c)...)
	}
	if f.skipCluster {
		report.Checks = append(report.Checks, doctorCheck{Name: "cluster", Status: doctorSkip, Details: "--skip-cluster"})
	} else {
		report.Checks = append(report.Checks, runClusterReleaseChecks(ctx, f)...)
	}

	if out.IsJSON() {
		if err := out.EmitJSON(report); err != nil {
			return err
		}
	} else if out.IsYAML() {
		if err := out.EmitYAML(report); err != nil {
			return err
		}
	} else {
		renderDoctorReport(out, report)
	}

	if doctorHasFailure(report.Checks) {
		return logicalErr(fmt.Errorf("release doctor found failing checks"))
	}
	return nil
}

func runHarnessReleaseChecks(ctx context.Context, c *client.Client) []doctorCheck {
	checks := []doctorCheck{}
	var agents []agentEntry
	if err := c.DoJSON(ctx, http.MethodGet, "/agents", nil, &agents, false); err != nil {
		return append(checks, doctorCheck{
			Name:    "harness /agents",
			Status:  doctorFail,
			Details: err.Error(),
		})
	}
	checks = append(checks, doctorCheck{
		Name:    "harness /agents",
		Status:  doctorPass,
		Details: fmt.Sprintf("%d advertised agent(s)", len(agents)),
	})
	probeTargets, skippedBackends := releaseDoctorHealthProbeTargets(agents)
	if len(probeTargets) > 0 {
		rows := probeAll(ctx, c, probeTargets)
		down := []string{}
		for _, row := range rows {
			if !row.Healthy {
				detail := row.Name
				if row.Error != "" {
					detail += " (" + row.Error + ")"
				} else if row.Status != "" {
					detail += " (" + row.Status + ")"
				}
				down = append(down, detail)
			}
		}
		if len(down) == 0 {
			checks = append(checks, doctorCheck{Name: "advertised agent health", Status: doctorPass, Details: "all advertised agents responded"})
		} else {
			checks = append(checks, doctorCheck{Name: "advertised agent health", Status: doctorFail, Details: strings.Join(down, "; ")})
		}
	} else if len(agents) > 0 {
		checks = append(checks, doctorCheck{Name: "advertised agent health", Status: doctorSkip, Details: "only backend sidecar entries advertised"})
	}
	if skippedBackends > 0 {
		checks = append(checks, doctorCheck{
			Name:    "backend sidecar health",
			Status:  doctorSkip,
			Details: fmt.Sprintf("%d backend entr%s skipped; sidecar URLs are pod-local", skippedBackends, pluralY(skippedBackends)),
		})
	}

	entry, err := fetchSnapshotSingle(ctx, c, "/heartbeat")
	if err != nil {
		checks = append(checks, doctorCheck{Name: "heartbeat config", Status: doctorFail, Details: err.Error()})
	} else if entry == nil || !heartbeatEnabled(entry) {
		checks = append(checks, doctorCheck{Name: "heartbeat config", Status: doctorWarn, Details: "no enabled heartbeat configured"})
	} else {
		schedule := entry.pickField("schedule")
		if schedule == "" {
			schedule = "enabled"
		}
		checks = append(checks, doctorCheck{Name: "heartbeat config", Status: doctorPass, Details: schedule})
	}
	return checks
}

func releaseDoctorHealthProbeTargets(agents []agentEntry) ([]agentEntry, int) {
	targets := make([]agentEntry, 0, len(agents))
	skippedBackends := 0
	for _, entry := range agents {
		if strings.EqualFold(entry.Role, "backend") {
			skippedBackends++
			continue
		}
		targets = append(targets, entry)
	}
	return targets, skippedBackends
}

func pluralY(n int) string {
	if n == 1 {
		return "y"
	}
	return "ies"
}

func runClusterReleaseChecks(ctx context.Context, f doctorFlags) []doctorCheck {
	kc := K8sFromCtx(ctx)
	resolver, err := k8s.NewResolver(k8s.Options{
		KubeconfigPath: kc.Kubeconfig,
		Context:        kc.Context,
		Namespace:      f.operatorNamespace,
	})
	if err != nil {
		return []doctorCheck{{Name: "cluster config", Status: doctorFail, Details: err.Error()}}
	}
	cfg, err := resolver.REST()
	if err != nil {
		return []doctorCheck{{Name: "cluster REST config", Status: doctorFail, Details: err.Error()}}
	}

	checks := []doctorCheck{{
		Name:    "target cluster",
		Status:  doctorPass,
		Details: fmt.Sprintf("%s (context: %s)", cmpDisplay(resolver.Target().Cluster, resolver.Target().Server), resolver.Target().Context),
	}}
	status, err := operator.GatherStatus(ctx, cfg, f.operatorNamespace, Version)
	if err != nil {
		checks = append(checks, doctorCheck{Name: "operator status probe", Status: doctorFail, Details: err.Error()})
	}
	if status == nil {
		return checks
	}
	checks = append(checks, evaluateOperatorStatus(status)...)

	summaries, err := agent.ListAgents(ctx, cfg, agent.ListOptions{AllNamespaces: true})
	if err != nil {
		checks = append(checks, doctorCheck{Name: "WitwaveAgent list", Status: doctorFail, Details: err.Error()})
		return checks
	}
	checks = append(checks, evaluateAgentSummaries(summaries, f, expectedOperatorVersion(status))...)
	return checks
}

func evaluateOperatorStatus(status *operator.Status) []doctorCheck {
	checks := []doctorCheck{}
	if status.Release == nil {
		checks = append(checks, doctorCheck{Name: "operator release", Status: doctorFail, Details: "not installed"})
	} else {
		checkStatus := doctorPass
		if status.Release.Status != "deployed" {
			checkStatus = doctorFail
		}
		checks = append(checks, doctorCheck{
			Name:    "operator release",
			Status:  checkStatus,
			Details: fmt.Sprintf("%s appVersion=%s chart=%s", status.Release.Status, status.Release.AppVersion, status.Release.ChartVersion),
		})
	}

	if len(status.Pods) == 0 {
		checks = append(checks, doctorCheck{Name: "operator pods", Status: doctorFail, Details: "no operator pods found"})
	} else {
		notRunning := []string{}
		running := 0
		for _, pod := range status.Pods {
			if pod.Phase == "Running" {
				running++
			} else {
				notRunning = append(notRunning, pod.Name+"="+pod.Phase)
			}
		}
		switch {
		case running == 0:
			checks = append(checks, doctorCheck{Name: "operator pods", Status: doctorFail, Details: strings.Join(notRunning, "; ")})
		case len(notRunning) > 0:
			checks = append(checks, doctorCheck{Name: "operator pods", Status: doctorWarn, Details: strings.Join(notRunning, "; ")})
		default:
			checks = append(checks, doctorCheck{Name: "operator pods", Status: doctorPass, Details: fmt.Sprintf("%d running", running)})
		}
	}

	missing := []string{}
	for _, crd := range status.CRDs {
		if !crd.Found {
			missing = append(missing, crd.Name)
		}
	}
	if len(missing) > 0 {
		checks = append(checks, doctorCheck{Name: "operator CRDs", Status: doctorFail, Details: strings.Join(missing, ", ")})
	} else {
		checks = append(checks, doctorCheck{Name: "operator CRDs", Status: doctorPass, Details: fmt.Sprintf("%d present", len(status.CRDs))})
	}
	return checks
}

func evaluateAgentSummaries(summaries []agent.AgentSummary, f doctorFlags, expectedTag string) []doctorCheck {
	checks := []doctorCheck{}
	filtered, missing := filterAgentSummaries(summaries, f.agents)
	if len(f.agents) > 0 && len(missing) > 0 {
		checks = append(checks, doctorCheck{Name: "required agents", Status: doctorFail, Details: "missing: " + strings.Join(missing, ", ")})
	}
	if len(filtered) == 0 {
		status := doctorWarn
		details := "no WitwaveAgents found"
		if len(f.agents) > 0 {
			status = doctorFail
			details = "no requested WitwaveAgents found"
		}
		return append(checks, doctorCheck{Name: "WitwaveAgent readiness", Status: status, Details: details})
	}

	notReady := []string{}
	for _, summary := range filtered {
		if !agentReady(summary) {
			notReady = append(notReady, fmt.Sprintf("%s/%s phase=%s ready=%d", summary.Namespace, summary.Name, summary.Phase, summary.Ready))
		}
	}
	if len(notReady) > 0 {
		status := doctorWarn
		if f.requireAgentsReady || len(f.agents) > 0 {
			status = doctorFail
		}
		checks = append(checks, doctorCheck{Name: "WitwaveAgent readiness", Status: status, Details: strings.Join(notReady, "; ")})
	} else {
		checks = append(checks, doctorCheck{Name: "WitwaveAgent readiness", Status: doctorPass, Details: fmt.Sprintf("%d Ready", len(filtered))})
	}

	if expectedTag != "" {
		mismatches := agentImageTagMismatches(filtered, expectedTag)
		if len(mismatches) > 0 {
			status := doctorWarn
			if f.strictAgentTags {
				status = doctorFail
			}
			checks = append(checks, doctorCheck{Name: "agent image tags", Status: status, Details: strings.Join(mismatches, "; ")})
		} else {
			checks = append(checks, doctorCheck{Name: "agent image tags", Status: doctorPass, Details: "match operator appVersion " + expectedTag})
		}
	}
	return checks
}

func filterAgentSummaries(summaries []agent.AgentSummary, names []string) ([]agent.AgentSummary, []string) {
	if len(names) == 0 {
		return summaries, nil
	}
	wanted := map[string]bool{}
	for _, name := range names {
		wanted[name] = false
	}
	filtered := []agent.AgentSummary{}
	for _, summary := range summaries {
		keys := []string{summary.Name}
		if summary.Namespace != "" {
			keys = append(keys, summary.Namespace+"/"+summary.Name)
		}
		for _, key := range keys {
			if _, ok := wanted[key]; !ok {
				continue
			}
			filtered = append(filtered, summary)
			wanted[key] = true
			break
		}
	}
	missing := []string{}
	for name, found := range wanted {
		if !found {
			missing = append(missing, name)
		}
	}
	sort.Strings(missing)
	return filtered, missing
}

func agentReady(summary agent.AgentSummary) bool {
	return summary.Phase == "Ready" && summary.Ready > 0
}

func expectedOperatorVersion(status *operator.Status) string {
	if status == nil || status.Release == nil {
		return ""
	}
	return status.Release.AppVersion
}

func agentImageTagMismatches(summaries []agent.AgentSummary, expectedTag string) []string {
	mismatches := []string{}
	for _, summary := range summaries {
		if summary.Raw == nil {
			continue
		}
		if tag := imageTagFromMap(summary.Raw.Object, "spec", "image"); tag != "" && tag != expectedTag {
			mismatches = append(mismatches, fmt.Sprintf("%s/%s harness=%s", summary.Namespace, summary.Name, tag))
		}
		backends, found, err := unstructured.NestedSlice(summary.Raw.Object, "spec", "backends")
		if err != nil || !found {
			continue
		}
		for _, raw := range backends {
			m, ok := raw.(map[string]interface{})
			if !ok {
				continue
			}
			name, _ := m["name"].(string)
			tag := imageTagFromMap(m, "image")
			if tag != "" && tag != expectedTag {
				if name == "" {
					name = "backend"
				}
				mismatches = append(mismatches, fmt.Sprintf("%s/%s %s=%s", summary.Namespace, summary.Name, name, tag))
			}
		}
	}
	sort.Strings(mismatches)
	return mismatches
}

func imageTagFromMap(obj map[string]interface{}, fields ...string) string {
	img, found, err := unstructured.NestedMap(obj, fields...)
	if err != nil || !found {
		return ""
	}
	tag, _ := img["tag"].(string)
	return tag
}

func renderDoctorReport(out *output.Writer, report doctorReport) {
	out.Headerf("Release Doctor\n")
	rows := make([][]string, 0, len(report.Checks))
	for _, check := range report.Checks {
		rows = append(rows, []string{string(check.Status), check.Name, check.Details})
	}
	output.Table(out.Out, []string{"STATUS", "CHECK", "DETAILS"}, rows)
	fmt.Fprintf(out.Out, "\nSummary: %s\n", doctorSummary(report.Checks))
}

func doctorSummary(checks []doctorCheck) string {
	counts := map[doctorStatus]int{}
	for _, check := range checks {
		counts[check.Status]++
	}
	return fmt.Sprintf("%d pass, %d warn, %d fail, %d skip",
		counts[doctorPass], counts[doctorWarn], counts[doctorFail], counts[doctorSkip])
}

func doctorHasFailure(checks []doctorCheck) bool {
	for _, check := range checks {
		if check.Status == doctorFail {
			return true
		}
	}
	return false
}
