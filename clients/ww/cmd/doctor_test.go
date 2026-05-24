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
