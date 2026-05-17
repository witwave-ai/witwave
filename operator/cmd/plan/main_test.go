/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package main

import (
	"bytes"
	"strings"
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

// TestRenderAgentHappyPath exercises the public renderAgent entry-point
// on a minimal WitwaveAgent and pins the plan-header line (#1111). The
// header at the top of renderAgent ("# === plan for WitwaveAgent
// <ns>/<name> ===") is the one Fprintf in renderAgent that is not
// wrapped in an error-check today (the per-resource writes inside
// `emit` and the manifest-CM error return path all check). This test
// is the regression guard a follow-up fix needs so the header's
// substitution semantics and its presence-exactly-once invariant
// don't silently regress when the unchecked Fprintf is wrapped or
// refactored.
//
// Sibling pattern: operator/cmd/main_test.go's
// TestValidateLeaderElectionFlags shape (table-light test on a public
// surface in the same cmd-package shape).
func TestRenderAgentHappyPath(t *testing.T) {
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "plan-smoke",
			Namespace: "team-finn",
		},
	}

	var buf bytes.Buffer
	if err := renderAgent(agent, &buf); err != nil {
		t.Fatalf("renderAgent on minimal WitwaveAgent returned error: %v", err)
	}

	out := buf.String()
	if out == "" {
		t.Fatal("renderAgent produced no output on minimal WitwaveAgent; expected at least the plan header")
	}

	// The plan-header line is the load-bearing assertion — it is the
	// one Fprintf in renderAgent not currently error-checked, and the
	// reason this test file exists. Anchor on the exact format string
	// from main.go (`# === plan for WitwaveAgent <ns>/<name> ===`) so
	// a future refactor that drops the header or changes its
	// substitution wiring trips this guard.
	wantHeader := "# === plan for WitwaveAgent team-finn/plan-smoke ==="
	if !strings.Contains(out, wantHeader) {
		t.Fatalf("renderAgent output missing plan header.\nwant substring: %q\ngot:\n%s",
			wantHeader, out)
	}

	// Header must appear exactly once per renderAgent invocation. A
	// duplicate would mean a refactor mistakenly emitted it inside the
	// resource loop; a zero count past the wantHeader check above is
	// impossible (the Contains check would have fired), but pin
	// count==1 explicitly to defend against a future refactor that
	// might emit it twice with different forms.
	if got := strings.Count(out, "# === plan for WitwaveAgent "); got != 1 {
		t.Fatalf("expected exactly 1 plan header in renderAgent output, got %d.\noutput:\n%s",
			got, out)
	}
}
