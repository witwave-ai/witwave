package cmd

import (
	"strings"
	"testing"

	"github.com/witwave-ai/witwave/clients/ww/internal/agent"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func TestDoctorHasFailure(t *testing.T) {
	if doctorHasFailure([]doctorCheck{{Status: doctorPass}, {Status: doctorWarn}}) {
		t.Fatal("doctorHasFailure returned true without a FAIL check")
	}
	if !doctorHasFailure([]doctorCheck{{Status: doctorPass}, {Status: doctorFail}}) {
		t.Fatal("doctorHasFailure returned false with a FAIL check")
	}
}

func TestDoctorSummaryCountsStatuses(t *testing.T) {
	got := doctorSummary([]doctorCheck{
		{Status: doctorPass},
		{Status: doctorPass},
		{Status: doctorWarn},
		{Status: doctorFail},
		{Status: doctorSkip},
	})
	want := "2 pass, 1 warn, 1 fail, 1 skip"
	if got != want {
		t.Errorf("doctorSummary = %q, want %q", got, want)
	}
}

func TestFilterAgentSummariesFindsRequestedAndMissing(t *testing.T) {
	in := []agent.AgentSummary{
		{Name: "mira", Namespace: "witwave-self"},
		{Name: "zora", Namespace: "witwave-self"},
	}
	got, missing := filterAgentSummaries(in, []string{"zora", "piper"})
	if len(got) != 1 || got[0].Name != "zora" {
		t.Fatalf("filtered = %+v, want only zora", got)
	}
	if len(missing) != 1 || missing[0] != "piper" {
		t.Fatalf("missing = %+v, want piper", missing)
	}
}

func TestFilterAgentSummariesAcceptsNamespaceQualifiedNames(t *testing.T) {
	in := []agent.AgentSummary{
		{Name: "mira", Namespace: "witwave-self"},
		{Name: "mira", Namespace: "witwave-test"},
	}
	got, missing := filterAgentSummaries(in, []string{"witwave-test/mira"})
	if len(missing) != 0 {
		t.Fatalf("missing = %+v, want none", missing)
	}
	if len(got) != 1 || got[0].Namespace != "witwave-test" {
		t.Fatalf("filtered = %+v, want witwave-test/mira", got)
	}
}

func TestEvaluateAgentSummariesTreatsNamedNotReadyAsFailure(t *testing.T) {
	checks := evaluateAgentSummaries([]agent.AgentSummary{
		{Name: "mira", Namespace: "witwave-self", Phase: "Reconciling", Ready: 0, Raw: agentWithImages("0.30.0", nil)},
	}, doctorFlags{agents: []string{"mira"}}, "0.30.0")

	found := false
	for _, check := range checks {
		if check.Name == "WitwaveAgent readiness" {
			found = true
			if check.Status != doctorFail {
				t.Fatalf("readiness status = %s, want FAIL", check.Status)
			}
			if !strings.Contains(check.Details, "mira") {
				t.Fatalf("readiness details = %q, want agent name", check.Details)
			}
		}
	}
	if !found {
		t.Fatal("missing WitwaveAgent readiness check")
	}
}

func TestEvaluateAgentSummariesSkipsDisabledByDefault(t *testing.T) {
	checks := evaluateAgentSummaries([]agent.AgentSummary{
		{Name: "evan", Namespace: "witwave-self", Phase: "Ready", Ready: 1, Disabled: true, Raw: agentWithImages("0.30.0", nil)},
		{Name: "zora", Namespace: "witwave-self", Phase: "Ready", Ready: 1, Raw: agentWithImages("0.30.0", nil)},
	}, doctorFlags{}, "0.30.0")

	foundDisabled := false
	foundReadiness := false
	for _, check := range checks {
		switch check.Name {
		case "WitwaveAgent disabled":
			foundDisabled = true
			if check.Status != doctorSkip {
				t.Fatalf("disabled status = %s, want SKIP", check.Status)
			}
			if !strings.Contains(check.Details, "evan") {
				t.Fatalf("disabled details = %q, want agent name", check.Details)
			}
		case "WitwaveAgent readiness":
			foundReadiness = true
			if check.Status != doctorPass || check.Details != "1 enabled Ready" {
				t.Fatalf("readiness check = %+v, want one enabled ready pass", check)
			}
		}
	}
	if !foundDisabled || !foundReadiness {
		t.Fatalf("checks = %+v, want disabled and readiness checks", checks)
	}
}

func TestEvaluateAgentSummariesTreatsRequiredDisabledAsFailure(t *testing.T) {
	checks := evaluateAgentSummaries([]agent.AgentSummary{
		{Name: "evan", Namespace: "witwave-self", Phase: "Ready", Ready: 1, Disabled: true, Raw: agentWithImages("0.30.0", nil)},
	}, doctorFlags{agents: []string{"evan"}}, "0.30.0")

	for _, check := range checks {
		if check.Name == "WitwaveAgent disabled" && check.Status == doctorFail {
			return
		}
	}
	t.Fatalf("checks = %+v, want required disabled agent to fail", checks)
}

func TestEvaluateAgentSummariesRequiresNamedBackend(t *testing.T) {
	checks := evaluateAgentSummaries([]agent.AgentSummary{
		{
			Name:      "mira",
			Namespace: "witwave-self",
			Phase:     "Ready",
			Ready:     1,
			Backends:  []string{"codex"},
			Raw:       agentWithImages("0.30.0", []map[string]string{{"name": "codex", "tag": "0.30.0"}}),
		},
	}, doctorFlags{agents: []string{"mira"}, requiredBackends: []string{"CODEX"}}, "0.30.0")

	for _, check := range checks {
		if check.Name == "required backends" {
			if check.Status != doctorPass {
				t.Fatalf("required backend status = %s, want PASS", check.Status)
			}
			if check.Details != "codex" {
				t.Fatalf("required backend details = %q, want codex", check.Details)
			}
			return
		}
	}
	t.Fatalf("checks = %+v, want required backends check", checks)
}

func TestEvaluateAgentSummariesFailsWhenRequiredBackendMissing(t *testing.T) {
	checks := evaluateAgentSummaries([]agent.AgentSummary{
		{
			Name:      "zora",
			Namespace: "witwave-self",
			Phase:     "Ready",
			Ready:     1,
			Backends:  []string{"claude"},
			Raw:       agentWithImages("0.30.0", []map[string]string{{"name": "claude", "tag": "0.30.0"}}),
		},
	}, doctorFlags{agents: []string{"zora"}, requiredBackends: []string{"codex"}}, "0.30.0")

	for _, check := range checks {
		if check.Name == "required backends" {
			if check.Status != doctorFail {
				t.Fatalf("required backend status = %s, want FAIL", check.Status)
			}
			if !strings.Contains(check.Details, "witwave-self/zora missing codex") {
				t.Fatalf("required backend details = %q, want missing zora/codex", check.Details)
			}
			return
		}
	}
	t.Fatalf("checks = %+v, want required backends check", checks)
}

func TestEvaluateRuntimeMetricSamplesPassesCodexPosture(t *testing.T) {
	expect := buildRuntimeExpectations(doctorFlags{
		runtimeModel:             "gpt-5.5",
		runtimeReasoningEffort:   "xhigh",
		runtimeDefaultMaxTokens:  "30000",
		runtimeMaxToolIterations: "10",
		runtimeStreaming:         "true",
		runtimeStubMode:          "false",
	})
	scrapes := []agent.ScrapedMetrics{{
		Container: "codex",
		PortName:  "metrics-codex",
		Port:      9001,
		Body: []byte(strings.Join([]string{
			`backend_runtime_config_info{agent="mira",backend="codex",model="gpt-5.5",reasoning_effort="xhigh"} 1`,
			`backend_runtime_default_max_tokens{agent="mira",backend="codex"} 30000`,
			`backend_runtime_max_tool_iterations{agent="mira",backend="codex"} 10`,
			`backend_runtime_responses_streaming_enabled{agent="mira",backend="codex"} 1`,
			`backend_runtime_stub_mode_enabled{agent="mira",backend="codex"} 0`,
		}, "\n")),
	}}

	if got := evaluateRuntimeMetricSamples(scrapes, expect, []string{"codex"}); len(got) != 0 {
		t.Fatalf("runtime mismatches = %+v, want none", got)
	}
}

func TestEvaluateRuntimeMetricSamplesFailsMissingRuntimeMetrics(t *testing.T) {
	expect := buildRuntimeExpectations(doctorFlags{runtimeModel: "gpt-5.5"})
	scrapes := []agent.ScrapedMetrics{{
		Container: "harness",
		PortName:  "metrics-harness",
		Port:      9000,
		Body:      []byte(`harness_up 1`),
	}}

	got := evaluateRuntimeMetricSamples(scrapes, expect, []string{"codex"})
	if len(got) != 1 || !strings.Contains(got[0], "no backend_runtime_* metrics found") {
		t.Fatalf("runtime mismatches = %+v, want missing runtime metrics", got)
	}
}

func TestEvaluateRuntimeMetricSamplesFailsMismatchedPosture(t *testing.T) {
	expect := buildRuntimeExpectations(doctorFlags{
		runtimeModel:             "gpt-5.5",
		runtimeDefaultMaxTokens:  "30000",
		runtimeMaxToolIterations: "10",
		runtimeStreaming:         "true",
		runtimeStubMode:          "false",
	})
	scrapes := []agent.ScrapedMetrics{{
		Container: "codex",
		PortName:  "metrics-codex",
		Port:      9001,
		Body: []byte(strings.Join([]string{
			`backend_runtime_config_info{agent="mira",backend="codex",model="gpt-5.4",reasoning_effort="xhigh"} 1`,
			`backend_runtime_default_max_tokens{agent="mira",backend="codex"} 12000`,
			`backend_runtime_max_tool_iterations{agent="mira",backend="codex"} 10`,
			`backend_runtime_responses_streaming_enabled{agent="mira",backend="codex"} 0`,
			`backend_runtime_stub_mode_enabled{agent="mira",backend="codex"} 1`,
		}, "\n")),
	}}

	got := strings.Join(evaluateRuntimeMetricSamples(scrapes, expect, []string{"codex"}), "; ")
	for _, want := range []string{
		`model on codex is "gpt-5.4", want "gpt-5.5"`,
		`backend_runtime_default_max_tokens on codex is 12000, want 30000`,
		`backend_runtime_responses_streaming_enabled on codex is 0, want 1`,
		`backend_runtime_stub_mode_enabled on codex is 1, want 0`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("runtime mismatches = %q, want %q", got, want)
		}
	}
}

func TestValidateDoctorFlagsRejectsRuntimeExpectationsWithSkipCluster(t *testing.T) {
	err := validateDoctorFlags(doctorFlags{skipCluster: true, runtimeModel: "gpt-5.5"})
	if err == nil || !strings.Contains(err.Error(), "require cluster checks") {
		t.Fatalf("validateDoctorFlags error = %v, want skip-cluster/runtime rejection", err)
	}
}

func TestValidateDoctorFlagsRejectsInvalidRuntimeValues(t *testing.T) {
	for name, flags := range map[string]doctorFlags{
		"streaming":  {runtimeStreaming: "maybe"},
		"stub":       {runtimeStubMode: "nope"},
		"tokens":     {runtimeDefaultMaxTokens: "-1"},
		"iterations": {runtimeMaxToolIterations: "many"},
	} {
		t.Run(name, func(t *testing.T) {
			if err := validateDoctorFlags(flags); err == nil {
				t.Fatal("validateDoctorFlags returned nil, want error")
			}
		})
	}
}

func TestReleaseDoctorHealthProbeTargetsSkipsBackendSidecars(t *testing.T) {
	targets, skipped := releaseDoctorHealthProbeTargets([]agentEntry{
		{ID: "zora", Role: "witwave", URL: "http://localhost:8000"},
		{ID: "claude", Role: "backend", URL: "http://localhost:8001"},
		{ID: "codex", Role: "BACKEND", URL: "http://localhost:8002"},
	})

	if skipped != 2 {
		t.Fatalf("skipped = %d, want 2", skipped)
	}
	if len(targets) != 1 || targets[0].ID != "zora" {
		t.Fatalf("targets = %+v, want only zora", targets)
	}
}

func TestAgentImageTagMismatchesIncludesHarnessAndBackends(t *testing.T) {
	summaries := []agent.AgentSummary{{
		Name:      "mira",
		Namespace: "witwave-self",
		Raw: agentWithImages("0.29.0", []map[string]string{
			{"name": "codex", "tag": "0.29.0"},
			{"name": "echo", "tag": "0.30.0"},
		}),
	}}

	got := agentImageTagMismatches(summaries, "0.30.0")
	if len(got) != 2 {
		t.Fatalf("mismatches = %+v, want harness + codex", got)
	}
	joined := strings.Join(got, "\n")
	for _, want := range []string{"harness=0.29.0", "codex=0.29.0"} {
		if !strings.Contains(joined, want) {
			t.Errorf("mismatches = %q, want %q", joined, want)
		}
	}
	if strings.Contains(joined, "echo=0.30.0") {
		t.Errorf("mismatches = %q, did not expect matching echo backend", joined)
	}
}

func TestAgentImageTagMismatchesSkipsNilRaw(t *testing.T) {
	got := agentImageTagMismatches([]agent.AgentSummary{{Name: "mira", Namespace: "witwave-self"}}, "0.30.0")
	if len(got) != 0 {
		t.Fatalf("mismatches = %+v, want none", got)
	}
}

func agentWithImages(harnessTag string, backendTags []map[string]string) *unstructured.Unstructured {
	obj := map[string]interface{}{
		"spec": map[string]interface{}{
			"image": map[string]interface{}{"tag": harnessTag},
		},
	}
	backends := make([]interface{}, 0, len(backendTags))
	for _, backend := range backendTags {
		backends = append(backends, map[string]interface{}{
			"name":  backend["name"],
			"image": map[string]interface{}{"tag": backend["tag"]},
		})
	}
	if len(backends) > 0 {
		obj["spec"].(map[string]interface{})["backends"] = backends
	}
	return &unstructured.Unstructured{Object: obj}
}
