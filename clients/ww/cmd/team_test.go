package cmd

import (
	"bytes"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/witwave-ai/witwave/clients/ww/internal/agent"
	"github.com/witwave-ai/witwave/clients/ww/internal/conversation"
)

func TestParseTeamStatusSince(t *testing.T) {
	tests := []struct {
		in   string
		want time.Duration
	}{
		{"1h", time.Hour},
		{"4h", 4 * time.Hour},
		{"12h", 12 * time.Hour},
		{"24h", 24 * time.Hour},
		{"1d", 24 * time.Hour},
		{"day", 24 * time.Hour},
		{"2days", 48 * time.Hour},
	}
	for _, tc := range tests {
		got, err := parseTeamStatusSince(tc.in)
		if err != nil {
			t.Fatalf("parseTeamStatusSince(%q) returned error: %v", tc.in, err)
		}
		if got != tc.want {
			t.Errorf("parseTeamStatusSince(%q) = %s, want %s", tc.in, got, tc.want)
		}
	}
}

func TestTeamStatusWatchFlagsRegistered(t *testing.T) {
	cmd := newTeamStatusCmd(&teamFlags{})
	watch := cmd.Flags().Lookup("watch")
	if watch == nil {
		t.Fatal("--watch flag is not registered")
	}
	if watch.Shorthand != "w" {
		t.Fatalf("--watch shorthand = %q, want w", watch.Shorthand)
	}
	interval := cmd.Flags().Lookup("interval")
	if interval == nil {
		t.Fatal("--interval flag is not registered")
	}
	if interval.DefValue != "10s" {
		t.Fatalf("--interval default = %q, want 10s", interval.DefValue)
	}
}

func TestBuildTeamStatusRowsAggregatesPerAgent(t *testing.T) {
	now := time.Date(2026, 5, 15, 12, 0, 0, 0, time.UTC)
	tok1000 := 1000
	tok2500 := 2500
	agents := []agent.AgentSummary{
		{Namespace: "witwave", Name: "zora", Phase: "Ready", Ready: 1, Backends: []string{"claude", "openai"}},
		{Namespace: "witwave", Name: "iris", Phase: "Ready", Ready: 1, Backends: []string{"openai"}},
		{Namespace: "witwave", Name: "piper", Phase: "Ready", Ready: 1, Backends: []string{"claude", "openai"}},
		{Namespace: "witwave", Name: "fred", Phase: "Ready", Ready: 1, Backends: []string{"echo"}},
		{Namespace: "witwave", Name: "nova", Phase: "Reconciling", Ready: 0, Backends: []string{"claude"}},
	}
	results := []conversation.FanOutResult{
		{
			Target: conversation.AgentTarget{Namespace: "witwave", Agent: "zora"},
			Entries: []conversation.Entry{
				{TS: now.Add(-30 * time.Minute).Format(time.RFC3339), Agent: "zora", SessionID: "s1", Role: "user", Tokens: &tok1000},
				{TS: now.Add(-20 * time.Minute).Format(time.RFC3339), Agent: "zora", SessionID: "s1", Role: "assistant"},
				{TS: now.Add(-2 * time.Minute).Format(time.RFC3339), Agent: "zora", SessionID: "s2", Role: "assistant", Tokens: &tok2500},
			},
		},
		{
			Target: conversation.AgentTarget{Namespace: "witwave", Agent: "iris"},
			Entries: []conversation.Entry{
				{TS: now.Add(-45 * time.Minute).Format(time.RFC3339), Agent: "iris", SessionID: "s3", Role: "assistant"},
			},
		},
		{Target: conversation.AgentTarget{Namespace: "witwave", Agent: "piper"}},
		{
			Target: conversation.AgentTarget{Namespace: "witwave", Agent: "fred"},
			Err:    errors.New("port-forward failed"),
		},
	}

	rows, unreachable := buildTeamStatusRows(agents, results, teamStatusBuildOptions{
		Now:    now,
		Window: time.Hour,
	})

	if len(rows) != len(agents) {
		t.Fatalf("got %d rows, want %d", len(rows), len(agents))
	}
	assertRow := func(idx int, agentName, state string) teamStatusRow {
		t.Helper()
		row := rows[idx]
		if row.Agent != agentName || row.State != state {
			t.Fatalf("rows[%d] = %s/%s, want %s/%s", idx, row.Agent, row.State, agentName, state)
		}
		return row
	}
	zora := assertRow(0, "zora", "RECENT")
	if zora.Sessions != 2 || zora.Turns != 3 || zora.Tokens != 3500 {
		t.Fatalf("zora aggregate = sessions %d turns %d tokens %d, want 2/3/3500",
			zora.Sessions, zora.Turns, zora.Tokens)
	}
	if zora.LastTurn != "2m ago" {
		t.Fatalf("zora LastTurn = %q, want 2m ago", zora.LastTurn)
	}
	if zora.Activity == "[------------]" {
		t.Fatalf("zora Activity should show at least one active bucket")
	}
	assertRow(1, "iris", "QUIET")
	assertRow(2, "piper", "IDLE")
	fred := assertRow(3, "fred", "UNKNOWN")
	if fred.Note != "conversation read failed" {
		t.Fatalf("fred Note = %q, want conversation read failed", fred.Note)
	}
	nova := assertRow(4, "nova", "OFFLINE")
	if !strings.Contains(nova.Note, "phase=Reconciling") {
		t.Fatalf("nova Note = %q, want phase detail", nova.Note)
	}
	if len(unreachable) != 1 || unreachable[0].Agent != "fred" {
		t.Fatalf("unreachable = %#v, want fred only", unreachable)
	}
}

func TestBuildTeamStatusRowsUsesLegacyTimestampFallback(t *testing.T) {
	now := time.Date(2026, 5, 15, 12, 0, 0, 0, time.UTC)
	agents := []agent.AgentSummary{
		{Namespace: "witwave", Name: "mira", Phase: "Ready", Ready: 1, Backends: []string{"codex"}},
	}
	results := []conversation.FanOutResult{
		{
			Target: conversation.AgentTarget{Namespace: "witwave", Agent: "mira"},
			Entries: []conversation.Entry{
				{
					Timestamp: now.Add(-3 * time.Minute).Format(time.RFC3339),
					Agent:     "mira",
					SessionID: "legacy-codex",
					Role:      "agent",
				},
			},
		},
	}

	rows, _ := buildTeamStatusRows(agents, results, teamStatusBuildOptions{Now: now, Window: time.Hour})

	if len(rows) != 1 {
		t.Fatalf("got %d rows, want 1", len(rows))
	}
	if rows[0].LastTurn != "3m ago" || rows[0].State != "RECENT" {
		t.Fatalf("legacy timestamp fallback row = LastTurn %q State %q, want 3m ago/RECENT",
			rows[0].LastTurn, rows[0].State)
	}
}

func TestRenderTeamStatusTable(t *testing.T) {
	rows := []teamStatusRow{
		{
			Namespace: "witwave",
			Agent:     "zora",
			State:     "RECENT",
			Backends:  []string{"claude", "openai"},
			LastTurn:  "2m ago",
			Sessions:  2,
			Turns:     3,
			Tokens:    3500,
			Activity:  "[-----#-#]",
		},
	}
	var buf bytes.Buffer
	renderTeamStatusTable(&buf, rows, true)
	got := buf.String()
	for _, want := range []string{
		"NAMESPACE",
		"AGENT",
		"witwave",
		"zora",
		"claude,openai",
		"3.5k",
		"[-----#-#]",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("rendered table missing %q:\n%s", want, got)
		}
	}
}

func TestDeriveTeamStatusState(t *testing.T) {
	now := time.Date(2026, 5, 15, 12, 0, 0, 0, time.UTC)
	tests := []struct {
		name  string
		last  time.Time
		turns int
		want  string
	}{
		{"zero turns -> IDLE", time.Time{}, 0, "IDLE"},
		{"zero turns with timestamp -> IDLE", now.Add(-time.Minute), 0, "IDLE"},
		{"recent edge: exactly 10 min", now.Add(-10 * time.Minute), 1, "RECENT"},
		{"recent: 2 min", now.Add(-2 * time.Minute), 3, "RECENT"},
		{"quiet: 11 min", now.Add(-11 * time.Minute), 1, "QUIET"},
		{"quiet: 1 hour", now.Add(-time.Hour), 5, "QUIET"},
		{"quiet with zero last time but turns>0", time.Time{}, 4, "QUIET"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := deriveTeamStatusState(now, tc.last, tc.turns)
			if got != tc.want {
				t.Errorf("deriveTeamStatusState(now, %v, %d) = %q, want %q", tc.last, tc.turns, got, tc.want)
			}
		})
	}
}

func TestParseTeamEntryTime(t *testing.T) {
	want := time.Date(2026, 5, 15, 12, 30, 45, 0, time.UTC)
	tests := []struct {
		name   string
		in     string
		ok     bool
		expect time.Time
	}{
		{"empty -> false", "", false, time.Time{}},
		{"garbage -> false", "not-a-timestamp", false, time.Time{}},
		{"RFC3339", "2026-05-15T12:30:45Z", true, want},
		{"RFC3339 with tz", "2026-05-15T05:30:45-07:00", true, want},
		{"RFC3339Nano", "2026-05-15T12:30:45.000000000Z", true, want},
		{"epoch integer seconds", "1778848245", true, want},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := parseTeamEntryTime(tc.in)
			if ok != tc.ok {
				t.Fatalf("parseTeamEntryTime(%q) ok=%v, want %v (got=%v)", tc.in, ok, tc.ok, got)
			}
			if ok && !got.Equal(tc.expect) {
				t.Errorf("parseTeamEntryTime(%q) = %v, want %v", tc.in, got, tc.expect)
			}
			if ok && got.Location() != time.UTC {
				t.Errorf("parseTeamEntryTime(%q) location = %s, want UTC", tc.in, got.Location())
			}
		})
	}
}

func TestParseTeamEntryTimeEpochFractional(t *testing.T) {
	// Epoch with sub-second fraction: 1778848245.5 -> +500ms.
	got, ok := parseTeamEntryTime("1778848245.5")
	if !ok {
		t.Fatalf("parseTeamEntryTime fractional epoch returned ok=false")
	}
	want := time.Date(2026, 5, 15, 12, 30, 45, 500_000_000, time.UTC)
	// Allow a 1µs slack for float→nsec conversion rounding.
	if delta := got.Sub(want); delta < -time.Microsecond || delta > time.Microsecond {
		t.Errorf("parseTeamEntryTime fractional epoch = %v, want %v (delta %v)", got, want, delta)
	}
}

func TestRenderTeamActivity(t *testing.T) {
	now := time.Date(2026, 5, 15, 12, 0, 0, 0, time.UTC)
	window := time.Hour
	tests := []struct {
		name   string
		times  []time.Time
		window time.Duration
		want   string
	}{
		{"empty -> all dashes", nil, window, "[------------]"},
		{"window<=0 -> all dashes", []time.Time{now}, 0, "[------------]"},
		{"single recent in last 5min bucket", []time.Time{now.Add(-2 * time.Minute)}, window, "[-----------#]"},
		{"single at window start", []time.Time{now.Add(-59 * time.Minute)}, window, "[#-----------]"},
		{"out-of-window before drops", []time.Time{now.Add(-2 * time.Hour)}, window, "[------------]"},
		{"out-of-window after drops", []time.Time{now.Add(time.Hour)}, window, "[------------]"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := renderTeamActivity(tc.times, now, tc.window)
			if got != tc.want {
				t.Errorf("renderTeamActivity = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestFormatTeamStatusDuration(t *testing.T) {
	tests := []struct {
		in   time.Duration
		want string
	}{
		{0, "0s"},
		{15 * time.Second, "15s"},
		{59 * time.Second, "59s"},
		{time.Minute, "1m"},
		{59 * time.Minute, "59m"},
		{time.Hour, "1h"},
		{23 * time.Hour, "23h"},
		{24 * time.Hour, "1d"},
		{48 * time.Hour, "2d"},
		{25 * time.Hour, "25h"},
	}
	for _, tc := range tests {
		got := formatTeamStatusDuration(tc.in)
		if got != tc.want {
			t.Errorf("formatTeamStatusDuration(%s) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestFormatTeamStatusAgo(t *testing.T) {
	now := time.Date(2026, 5, 15, 12, 0, 0, 0, time.UTC)
	if got := formatTeamStatusAgo(now, time.Time{}); got != "-" {
		t.Errorf("zero time -> %q, want -", got)
	}
	if got := formatTeamStatusAgo(now, now.Add(-2*time.Minute)); got != "2m ago" {
		t.Errorf("2m past -> %q, want %q", got, "2m ago")
	}
	if got := formatTeamStatusAgo(now, now.Add(time.Minute)); got != "0s ago" {
		t.Errorf("future timestamp -> %q, want %q (negative clamped to 0)", got, "0s ago")
	}
}

func TestFormatTeamStatusBackends(t *testing.T) {
	tests := []struct {
		in   []string
		want string
	}{
		{nil, "-"},
		{[]string{}, "-"},
		{[]string{"claude"}, "claude"},
		{[]string{"claude", "openai"}, "claude,openai"},
	}
	for _, tc := range tests {
		got := formatTeamStatusBackends(tc.in)
		if got != tc.want {
			t.Errorf("formatTeamStatusBackends(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestFormatTeamStatusTokens(t *testing.T) {
	tests := []struct {
		in   int
		want string
	}{
		{0, "-"},
		{-1, "-"},
		{1, "1"},
		{999, "999"},
		{1000, "1k"},
		{3500, "3.5k"},
		{999_999, "1000k"},
		{1_000_000, "1m"},
		{2_500_000, "2.5m"},
	}
	for _, tc := range tests {
		got := formatTeamStatusTokens(tc.in)
		if got != tc.want {
			t.Errorf("formatTeamStatusTokens(%d) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestTrimTeamStatusFloat(t *testing.T) {
	tests := []struct {
		in   float64
		want string
	}{
		{1.0, "1"},
		{1.5, "1.5"},
		{3.14, "3.1"},
		{10.0, "10"},
		{0.5, "0.5"},
		{2.0, "2"},
	}
	for _, tc := range tests {
		got := trimTeamStatusFloat(tc.in)
		if got != tc.want {
			t.Errorf("trimTeamStatusFloat(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestTeamStatusScope(t *testing.T) {
	tests := []struct {
		name          string
		allNamespaces bool
		namespace     string
		source        agent.NamespaceSource
		want          string
	}{
		{"-A wins over namespace", true, "witwave", agent.NamespaceFromFlag, "cluster-wide (-A)"},
		{"flag source -> bare namespace", false, "witwave", agent.NamespaceFromFlag, "namespace/witwave"},
		{"context source", false, "ops", agent.NamespaceFromContext, "namespace/ops (from kubeconfig context)"},
		{"default source", false, "witwave", agent.NamespaceFromDefault, "namespace/witwave (ww default)"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := teamStatusScope(tc.allNamespaces, tc.namespace, tc.source)
			if got != tc.want {
				t.Errorf("teamStatusScope(%v, %q, %v) = %q, want %q",
					tc.allNamespaces, tc.namespace, tc.source, got, tc.want)
			}
		})
	}
}

func TestFilterTeamStatusAgents(t *testing.T) {
	in := []agent.AgentSummary{
		{Namespace: "witwave", Name: "zora", Team: "ops"},
		{Namespace: "witwave", Name: "iris", Team: "ops"},
		{Namespace: "witwave", Name: "fred", Team: "demo"},
		{Namespace: "witwave", Name: "ungrouped", Team: ""},
	}
	got := filterTeamStatusAgents(in, "ops")
	if len(got) != 2 || got[0].Name != "zora" || got[1].Name != "iris" {
		t.Errorf("filter ops = %+v, want zora+iris", got)
	}
	got = filterTeamStatusAgents(in, "demo")
	if len(got) != 1 || got[0].Name != "fred" {
		t.Errorf("filter demo = %+v, want fred", got)
	}
	got = filterTeamStatusAgents(in, "")
	if len(got) != 1 || got[0].Name != "ungrouped" {
		t.Errorf("filter empty = %+v, want ungrouped", got)
	}
	if got := filterTeamStatusAgents(nil, "ops"); len(got) != 0 {
		t.Errorf("filter nil input = %+v, want empty", got)
	}
}

func TestSortAgentSummaries(t *testing.T) {
	in := []agent.AgentSummary{
		{Namespace: "witwave", Name: "zora"},
		{Namespace: "ops", Name: "iris"},
		{Namespace: "witwave", Name: "iris"},
		{Namespace: "ops", Name: "zora"},
	}
	sortAgentSummaries(in)
	wantOrder := []string{"ops/iris", "ops/zora", "witwave/iris", "witwave/zora"}
	for i, w := range wantOrder {
		got := in[i].Namespace + "/" + in[i].Name
		if got != w {
			t.Errorf("sorted[%d] = %s, want %s", i, got, w)
		}
	}
}

func TestTeamStatusAgentReady(t *testing.T) {
	tests := []struct {
		name string
		s    agent.AgentSummary
		want bool
	}{
		{"Ready+replica", agent.AgentSummary{Phase: "Ready", Ready: 1}, true},
		{"ready lowercase ok", agent.AgentSummary{Phase: "ready", Ready: 2}, true},
		{"Ready but 0 replicas", agent.AgentSummary{Phase: "Ready", Ready: 0}, false},
		{"Reconciling phase", agent.AgentSummary{Phase: "Reconciling", Ready: 1}, false},
		{"empty phase", agent.AgentSummary{Phase: "", Ready: 1}, false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := teamStatusAgentReady(tc.s)
			if got != tc.want {
				t.Errorf("teamStatusAgentReady(%+v) = %v, want %v", tc.s, got, tc.want)
			}
		})
	}
}

func TestTeamStatusReadinessNote(t *testing.T) {
	tests := []struct {
		name string
		s    agent.AgentSummary
		want string
	}{
		{"empty phase -> Pending", agent.AgentSummary{Phase: "", Ready: 0}, "phase=Pending ready=0"},
		{"Reconciling+0", agent.AgentSummary{Phase: "Reconciling", Ready: 0}, "phase=Reconciling ready=0"},
		{"Ready+1", agent.AgentSummary{Phase: "Ready", Ready: 1}, "phase=Ready ready=1"},
		{"Ready+3 replicas", agent.AgentSummary{Phase: "Ready", Ready: 3}, "phase=Ready ready=3"},
		{"phase Pending+0", agent.AgentSummary{Phase: "Pending", Ready: 0}, "phase=Pending ready=0"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := teamStatusReadinessNote(tc.s)
			if got != tc.want {
				t.Errorf("teamStatusReadinessNote(%+v) = %q, want %q", tc.s, got, tc.want)
			}
		})
	}
}

func TestClearTeamStatusWatchFrame(t *testing.T) {
	var buf bytes.Buffer
	clearTeamStatusWatchFrame(&buf)
	if buf.String() != "\033[2J\033[H" {
		t.Errorf("clearTeamStatusWatchFrame wrote %q, want %q", buf.String(), "\033[2J\033[H")
	}
}

func TestRenderTeamStatusUnreachableFooter(t *testing.T) {
	var buf bytes.Buffer
	renderTeamStatusUnreachableFooter(&buf, nil)
	if buf.String() != "" {
		t.Errorf("empty input wrote %q, want empty", buf.String())
	}
	buf.Reset()
	renderTeamStatusUnreachableFooter(&buf, []teamStatusUnreachable{
		{Namespace: "witwave", Agent: "fred", Error: "port-forward failed"},
		{Namespace: "ops", Agent: "barney", Error: "401 unauthorized"},
	})
	got := buf.String()
	for _, want := range []string{
		"2 agent(s) had conversation read errors",
		"witwave/fred",
		"ops/barney",
		"port-forward failed",
		"401 unauthorized",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("footer missing %q:\n%s", want, got)
		}
	}
}

func TestRenderTeamStatusTableEmpty(t *testing.T) {
	var buf bytes.Buffer
	renderTeamStatusTable(&buf, nil, true)
	if !strings.Contains(buf.String(), "No WitwaveAgents found in scope.") {
		t.Errorf("empty table = %q, want guidance message", buf.String())
	}
}
