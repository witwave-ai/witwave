package cmd

import (
	"context"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/witwave-ai/witwave/clients/ww/internal/agent"
	"github.com/witwave-ai/witwave/clients/ww/internal/conversation"
	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
)

type teamFlags struct {
	namespace     string
	allNamespaces bool
}

func newTeamCmd() *cobra.Command {
	f := &teamFlags{}
	cmd := &cobra.Command{
		Use:   "team",
		Short: "Inspect team-level agent activity",
		Long: "Inspect activity across the WitwaveAgents in scope. A team status view\n" +
			"treats each named agent as the primary teammate and aggregates all of\n" +
			"that agent's configured backends into one row. Use --all-namespaces to\n" +
			"fan out cluster-wide, or --namespace to stay within one namespace.",
	}
	cmd.PersistentFlags().StringVarP(&f.namespace, "namespace", "n", "",
		fmt.Sprintf("Namespace for team reads (defaults to the kubeconfig context's namespace, then %q)", agent.DefaultAgentNamespace))
	cmd.PersistentFlags().BoolVarP(&f.allNamespaces, "all-namespaces", "A", false,
		"Fan out across every namespace the current kubeconfig can read")
	cmd.AddCommand(newTeamStatusCmd(f))
	return cmd
}

func (f *teamFlags) resolveTarget(ctx context.Context) (*k8s.Target, *k8s.Resolver, error) {
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

func newTeamStatusCmd(f *teamFlags) *cobra.Command {
	var (
		sinceExpr  string
		teamFilter string
		token      string
		quiet      bool
		watch      bool
		interval   time.Duration
	)
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show aggregated recent activity for every agent in scope",
		Long: "Shows one row per WitwaveAgent in scope, aggregating recent conversation\n" +
			"activity across all of that agent's configured backends. Phase one is\n" +
			"conversation-backed: it reports RECENT / QUIET / IDLE / OFFLINE / UNKNOWN\n" +
			"from conversation history and agent readiness. Live RUNNING/BUSY detection\n" +
			"will come from backend metrics in a later phase.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if watch {
				return runTeamStatusWatch(cmd.Context(), f, sinceExpr, teamFilter, token, quiet, interval)
			}
			return runTeamStatus(cmd.Context(), f, sinceExpr, teamFilter, token, quiet)
		},
	}
	cmd.Flags().StringVar(&sinceExpr, "since", "1h",
		"Lookback window for activity (examples: 1h, 4h, 12h, 24h, 1d)")
	cmd.Flags().StringVar(&teamFilter, "team", "",
		"Only include agents labelled witwave.ai/team=<team>")
	cmd.Flags().StringVar(&token, "token", "",
		"Bearer token override for conversation endpoints")
	cmd.Flags().BoolVarP(&quiet, "quiet", "q", false,
		"Suppress scope/window banners and unreachable-agent footer")
	cmd.Flags().BoolVarP(&watch, "watch", "w", false,
		"Refresh the human status table until Ctrl-C")
	cmd.Flags().DurationVar(&interval, "interval", 10*time.Second,
		"Refresh interval for --watch")
	return cmd
}

func runTeamStatusWatch(
	ctx context.Context,
	f *teamFlags,
	sinceExpr string,
	teamFilter string,
	token string,
	quiet bool,
	interval time.Duration,
) error {
	out := OutFromCtx(ctx)
	if out.IsJSON() || out.IsYAML() {
		return fmt.Errorf("--watch only supports human output; rerun without --json/--yaml")
	}
	if interval <= 0 {
		return fmt.Errorf("--interval must be positive")
	}
	if _, err := parseTeamStatusSince(sinceExpr); err != nil {
		return err
	}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		clearTeamStatusWatchFrame(out.Out)
		if !quiet {
			fmt.Fprintf(out.Out, "Updated: %s  Refresh: %s  Stop: Ctrl-C\n\n",
				time.Now().UTC().Format(time.RFC3339), interval)
		}
		if err := runTeamStatus(ctx, f, sinceExpr, teamFilter, token, quiet); err != nil {
			fmt.Fprintf(out.Err, "ww team status --watch: %v\n", err)
		}

		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func clearTeamStatusWatchFrame(w io.Writer) {
	fmt.Fprint(w, "\033[2J\033[H")
}

func runTeamStatus(
	ctx context.Context,
	f *teamFlags,
	sinceExpr string,
	teamFilter string,
	token string,
	quiet bool,
) error {
	out := OutFromCtx(ctx)
	target, resolver, err := f.resolveTarget(ctx)
	if err != nil {
		return err
	}
	cfg, err := resolver.REST()
	if err != nil {
		return err
	}
	window, err := parseTeamStatusSince(sinceExpr)
	if err != nil {
		return err
	}

	now := time.Now().UTC()
	ns := ""
	nsSource := agent.NamespaceFromFlag
	if !f.allNamespaces {
		ns, nsSource = agent.ResolveNamespaceWithSource(f.namespace, target.Namespace)
	}

	summaries, err := agent.ListAgents(ctx, cfg, agent.ListOptions{
		Namespace:     ns,
		AllNamespaces: f.allNamespaces,
	})
	if err != nil {
		return err
	}
	if teamFilter != "" {
		summaries = filterTeamStatusAgents(summaries, teamFilter)
	}
	sortAgentSummaries(summaries)

	targets := teamStatusTargets(summaries)
	results := []conversation.FanOutResult{}
	if len(targets) > 0 {
		tokenFn := makeTokenLookup(ctx, cfg, token)
		results = conversation.FanOutList(ctx, cfg, targets, conversation.ListOptions{
			Since: now.Add(-window).UTC().Format(time.RFC3339),
		}, tokenFn)
	}
	rows, unreachable := buildTeamStatusRows(summaries, results, teamStatusBuildOptions{
		Now:    now,
		Window: window,
	})

	doc := teamStatusDocument{
		Scope:         teamStatusScope(f.allNamespaces, ns, nsSource),
		Namespace:     ns,
		AllNamespaces: f.allNamespaces,
		Team:          teamFilter,
		Since:         formatTeamStatusDuration(window),
		GeneratedAt:   now.Format(time.RFC3339),
		Rows:          rows,
		Unreachable:   unreachable,
	}
	if out.IsJSON() {
		return out.EmitJSON(doc)
	}
	if out.IsYAML() {
		return out.EmitYAML(doc)
	}

	if !quiet {
		fmt.Fprintf(out.Out, "Scope: %s\n", doc.Scope)
		if teamFilter != "" {
			fmt.Fprintf(out.Out, "Team: %s\n", teamFilter)
		}
		fmt.Fprintf(out.Out, "Window: last %s\n\n", doc.Since)
	}
	renderTeamStatusTable(out.Out, rows, f.allNamespaces)
	if !quiet {
		renderTeamStatusUnreachableFooter(out.Out, unreachable)
	}
	return nil
}

type teamStatusDocument struct {
	Scope         string                  `json:"scope" yaml:"scope"`
	Namespace     string                  `json:"namespace,omitempty" yaml:"namespace,omitempty"`
	AllNamespaces bool                    `json:"all_namespaces" yaml:"allNamespaces"`
	Team          string                  `json:"team,omitempty" yaml:"team,omitempty"`
	Since         string                  `json:"since" yaml:"since"`
	GeneratedAt   string                  `json:"generated_at" yaml:"generatedAt"`
	Rows          []teamStatusRow         `json:"rows" yaml:"rows"`
	Unreachable   []teamStatusUnreachable `json:"unreachable,omitempty" yaml:"unreachable,omitempty"`
}

type teamStatusRow struct {
	Namespace    string   `json:"namespace,omitempty" yaml:"namespace,omitempty"`
	Agent        string   `json:"agent" yaml:"agent"`
	State        string   `json:"state" yaml:"state"`
	Backends     []string `json:"backends" yaml:"backends"`
	LastActivity string   `json:"last_activity,omitempty" yaml:"lastActivity,omitempty"`
	LastTurn     string   `json:"last_turn" yaml:"lastTurn"`
	Sessions     int      `json:"sessions" yaml:"sessions"`
	Turns        int      `json:"turns" yaml:"turns"`
	Tokens       int      `json:"tokens" yaml:"tokens"`
	Activity     string   `json:"activity" yaml:"activity"`
	Note         string   `json:"note,omitempty" yaml:"note,omitempty"`
}

type teamStatusUnreachable struct {
	Namespace string `json:"namespace" yaml:"namespace"`
	Agent     string `json:"agent" yaml:"agent"`
	Error     string `json:"error" yaml:"error"`
}

type teamStatusBuildOptions struct {
	Now    time.Time
	Window time.Duration
}

type teamStatusKey struct {
	namespace string
	agent     string
}

type teamStatusAccumulator struct {
	row         teamStatusRow
	ready       bool
	fetchFailed bool
	sessions    map[string]struct{}
	entryTimes  []time.Time
	last        time.Time
}

func buildTeamStatusRows(
	agents []agent.AgentSummary,
	results []conversation.FanOutResult,
	opts teamStatusBuildOptions,
) ([]teamStatusRow, []teamStatusUnreachable) {
	if opts.Now.IsZero() {
		opts.Now = time.Now().UTC()
	}
	accByKey := make(map[teamStatusKey]*teamStatusAccumulator, len(agents))
	keys := make([]teamStatusKey, 0, len(agents))
	for _, s := range agents {
		key := teamStatusKey{namespace: s.Namespace, agent: s.Name}
		ready := teamStatusAgentReady(s)
		state := "IDLE"
		note := ""
		if !ready {
			state = "OFFLINE"
			note = teamStatusReadinessNote(s)
		}
		accByKey[key] = &teamStatusAccumulator{
			row: teamStatusRow{
				Namespace: s.Namespace,
				Agent:     s.Name,
				State:     state,
				Backends:  append([]string(nil), s.Backends...),
				LastTurn:  "-",
				Activity:  renderTeamActivity(nil, opts.Now, opts.Window),
				Note:      note,
			},
			ready:    ready,
			sessions: make(map[string]struct{}),
		}
		keys = append(keys, key)
	}

	unreachable := make([]teamStatusUnreachable, 0)
	for _, r := range results {
		key := teamStatusKey{namespace: r.Target.Namespace, agent: r.Target.Agent}
		acc, ok := accByKey[key]
		if !ok {
			continue
		}
		if r.Err != nil {
			acc.fetchFailed = true
			if acc.ready {
				acc.row.State = "UNKNOWN"
				acc.row.Note = "conversation read failed"
			}
			unreachable = append(unreachable, teamStatusUnreachable{
				Namespace: r.Target.Namespace,
				Agent:     r.Target.Agent,
				Error:     r.Err.Error(),
			})
			continue
		}
		for _, e := range r.Entries {
			if e.SessionID == "" {
				continue
			}
			acc.row.Turns++
			acc.sessions[e.SessionID] = struct{}{}
			if e.Tokens != nil && *e.Tokens > 0 {
				acc.row.Tokens += *e.Tokens
			}
			if ts, ok := parseTeamEntryTime(teamEntryTimestamp(e)); ok {
				acc.entryTimes = append(acc.entryTimes, ts)
				if acc.last.IsZero() || ts.After(acc.last) {
					acc.last = ts
				}
			}
		}
	}

	rows := make([]teamStatusRow, 0, len(keys))
	for _, key := range keys {
		acc := accByKey[key]
		acc.row.Sessions = len(acc.sessions)
		if !acc.last.IsZero() {
			acc.row.LastActivity = acc.last.UTC().Format(time.RFC3339)
			acc.row.LastTurn = formatTeamStatusAgo(opts.Now, acc.last)
		}
		acc.row.Activity = renderTeamActivity(acc.entryTimes, opts.Now, opts.Window)
		if acc.ready && !acc.fetchFailed {
			acc.row.State = deriveTeamStatusState(opts.Now, acc.last, acc.row.Turns)
		}
		rows = append(rows, acc.row)
	}
	return rows, unreachable
}

func teamStatusAgentReady(s agent.AgentSummary) bool {
	return strings.EqualFold(s.Phase, "Ready") && s.Ready > 0
}

func teamStatusReadinessNote(s agent.AgentSummary) string {
	phase := s.Phase
	if phase == "" {
		phase = "Pending"
	}
	if s.Ready <= 0 {
		return fmt.Sprintf("phase=%s ready=0", phase)
	}
	return fmt.Sprintf("phase=%s ready=%d", phase, s.Ready)
}

func deriveTeamStatusState(now, last time.Time, turns int) string {
	if turns == 0 {
		return "IDLE"
	}
	if !last.IsZero() && now.Sub(last) <= 10*time.Minute {
		return "RECENT"
	}
	return "QUIET"
}

func filterTeamStatusAgents(in []agent.AgentSummary, team string) []agent.AgentSummary {
	out := make([]agent.AgentSummary, 0, len(in))
	for _, s := range in {
		if s.Team == team {
			out = append(out, s)
		}
	}
	return out
}

func sortAgentSummaries(in []agent.AgentSummary) {
	sort.SliceStable(in, func(i, j int) bool {
		if in[i].Namespace != in[j].Namespace {
			return in[i].Namespace < in[j].Namespace
		}
		return in[i].Name < in[j].Name
	})
}

func teamStatusTargets(summaries []agent.AgentSummary) []conversation.AgentTarget {
	targets := make([]conversation.AgentTarget, 0, len(summaries))
	for _, s := range summaries {
		targets = append(targets, conversation.AgentTarget{
			Namespace: s.Namespace,
			Agent:     s.Name,
		})
	}
	return targets
}

func renderTeamStatusTable(w io.Writer, rows []teamStatusRow, showNamespace bool) {
	if len(rows) == 0 {
		fmt.Fprintln(w, "No WitwaveAgents found in scope.")
		return
	}
	headers := []string{"AGENT", "STATE", "BACKENDS", "LAST TURN", "SESSIONS", "TURNS", "TOKENS", "ACTIVITY"}
	if showNamespace {
		headers = append([]string{"NAMESPACE"}, headers...)
	}
	data := make([][]string, 0, len(rows))
	for _, r := range rows {
		row := []string{
			r.Agent,
			r.State,
			formatTeamStatusBackends(r.Backends),
			r.LastTurn,
			strconv.Itoa(r.Sessions),
			strconv.Itoa(r.Turns),
			formatTeamStatusTokens(r.Tokens),
			r.Activity,
		}
		if showNamespace {
			row = append([]string{r.Namespace}, row...)
		}
		data = append(data, row)
	}
	output.Table(w, headers, data)
}

func renderTeamStatusUnreachableFooter(w io.Writer, unreachable []teamStatusUnreachable) {
	if len(unreachable) == 0 {
		return
	}
	names := make([]string, 0, len(unreachable))
	for _, r := range unreachable {
		names = append(names, fmt.Sprintf("%s/%s", r.Namespace, r.Agent))
	}
	fmt.Fprintf(w, "\n(%d agent(s) had conversation read errors: %s)\n",
		len(unreachable), strings.Join(names, ", "))
	for _, r := range unreachable {
		fmt.Fprintf(w, "  %s/%s: %s\n", r.Namespace, r.Agent, r.Error)
	}
}

func parseTeamStatusSince(expr string) (time.Duration, error) {
	raw := strings.TrimSpace(strings.ToLower(expr))
	if raw == "" {
		return 0, fmt.Errorf("--since must not be empty")
	}
	if raw == "day" || raw == "today" {
		return 24 * time.Hour, nil
	}
	for _, suffix := range []string{"days", "day", "d"} {
		if strings.HasSuffix(raw, suffix) {
			n := strings.TrimSpace(strings.TrimSuffix(raw, suffix))
			if n == "" {
				n = "1"
			}
			days, err := strconv.ParseFloat(n, 64)
			if err != nil || days <= 0 {
				return 0, fmt.Errorf("invalid --since %q", expr)
			}
			return time.Duration(days * float64(24*time.Hour)), nil
		}
	}
	d, err := time.ParseDuration(raw)
	if err != nil || d <= 0 {
		return 0, fmt.Errorf("invalid --since %q (examples: 1h, 4h, 12h, 24h, 1d)", expr)
	}
	return d, nil
}

func parseTeamEntryTime(ts string) (time.Time, bool) {
	if ts == "" {
		return time.Time{}, false
	}
	if t, err := time.Parse(time.RFC3339Nano, ts); err == nil {
		return t.UTC(), true
	}
	if epoch, err := strconv.ParseFloat(ts, 64); err == nil {
		sec := int64(epoch)
		nsec := int64((epoch - float64(sec)) * 1_000_000_000)
		return time.Unix(sec, nsec).UTC(), true
	}
	return time.Time{}, false
}

func teamEntryTimestamp(e conversation.Entry) string {
	if e.TS != "" {
		return e.TS
	}
	return e.Timestamp
}

func renderTeamActivity(times []time.Time, now time.Time, window time.Duration) string {
	const buckets = 12
	if window <= 0 {
		return "[" + strings.Repeat("-", buckets) + "]"
	}
	counts := make([]int, buckets)
	start := now.Add(-window)
	bucketWidth := window / buckets
	if bucketWidth <= 0 {
		bucketWidth = window
	}
	for _, ts := range times {
		if ts.Before(start) || ts.After(now) {
			continue
		}
		idx := int(ts.Sub(start) / bucketWidth)
		if idx >= buckets {
			idx = buckets - 1
		}
		if idx < 0 {
			idx = 0
		}
		counts[idx]++
	}
	var b strings.Builder
	b.WriteByte('[')
	for _, count := range counts {
		if count > 0 {
			b.WriteByte('#')
		} else {
			b.WriteByte('-')
		}
	}
	b.WriteByte(']')
	return b.String()
}

func formatTeamStatusAgo(now, t time.Time) string {
	if t.IsZero() {
		return "-"
	}
	d := now.Sub(t)
	if d < 0 {
		d = 0
	}
	return formatTeamStatusDuration(d) + " ago"
}

func formatTeamStatusDuration(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%ds", int(d.Seconds()))
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	if d < 24*time.Hour {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	if d%(24*time.Hour) == 0 {
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	}
	return fmt.Sprintf("%dh", int(d.Hours()))
}

func formatTeamStatusBackends(backends []string) string {
	if len(backends) == 0 {
		return "-"
	}
	return strings.Join(backends, ",")
}

func formatTeamStatusTokens(tokens int) string {
	if tokens <= 0 {
		return "-"
	}
	if tokens >= 1_000_000 {
		return trimTeamStatusFloat(float64(tokens)/1_000_000) + "m"
	}
	if tokens >= 1_000 {
		return trimTeamStatusFloat(float64(tokens)/1_000) + "k"
	}
	return strconv.Itoa(tokens)
}

func trimTeamStatusFloat(v float64) string {
	s := fmt.Sprintf("%.1f", v)
	return strings.TrimSuffix(s, ".0")
}

func teamStatusScope(allNamespaces bool, namespace string, source agent.NamespaceSource) string {
	if allNamespaces {
		return "cluster-wide (-A)"
	}
	switch source {
	case agent.NamespaceFromContext:
		return fmt.Sprintf("namespace/%s (from kubeconfig context)", namespace)
	case agent.NamespaceFromDefault:
		return fmt.Sprintf("namespace/%s (ww default)", namespace)
	default:
		return "namespace/" + namespace
	}
}
