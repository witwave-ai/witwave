package cmd

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/client"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
)

func TestJobsRunPostsAdhocEndpoint(t *testing.T) {
	cmd := newJobsRunCmd()
	gotPath, gotAuth, gotCSRF, stdout, stderr := runSchedulerRunTest(t, cmd, "daily report",
		`{"delivery_id":"delivery-1","kind":"job","name":"daily report"}`)

	if gotPath != "/jobs/daily%20report/run" {
		t.Errorf("path = %q, want escaped job run path", gotPath)
	}
	if gotAuth != "Bearer run-token" {
		t.Errorf("auth = %q, want run token", gotAuth)
	}
	if gotCSRF != "1" {
		t.Errorf("X-Ad-Hoc-Run = %q, want 1", gotCSRF)
	}
	if !strings.Contains(stdout, "delivery-1") || !strings.Contains(stdout, "daily report") {
		t.Errorf("stdout = %q, want delivery id and job name", stdout)
	}
	if stderr != "" {
		t.Errorf("stderr = %q, want empty", stderr)
	}
}

func TestTasksRunPostsAdhocEndpoint(t *testing.T) {
	cmd := newTasksRunCmd()
	gotPath, gotAuth, gotCSRF, stdout, stderr := runSchedulerRunTest(t, cmd, "status sweep",
		`{"delivery_id":"delivery-2","kind":"task","name":"status sweep"}`)

	if gotPath != "/tasks/status%20sweep/run" {
		t.Errorf("path = %q, want escaped task run path", gotPath)
	}
	if gotAuth != "Bearer run-token" {
		t.Errorf("auth = %q, want run token", gotAuth)
	}
	if gotCSRF != "1" {
		t.Errorf("X-Ad-Hoc-Run = %q, want 1", gotCSRF)
	}
	if !strings.Contains(stdout, "delivery-2") || !strings.Contains(stdout, "status sweep") {
		t.Errorf("stdout = %q, want delivery id and task name", stdout)
	}
	if stderr != "" {
		t.Errorf("stderr = %q, want empty", stderr)
	}
}

func runSchedulerRunTest(t *testing.T, cmd *cobra.Command, name string, response string) (string, string, string, string, string) {
	t.Helper()

	var gotPath, gotAuth, gotCSRF string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		gotAuth = r.Header.Get("Authorization")
		gotCSRF = r.Header.Get("X-Ad-Hoc-Run")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(response))
	}))
	defer srv.Close()

	var stdout, stderr bytes.Buffer
	ctx := context.WithValue(context.Background(), ctxKeyClient, client.New(client.Config{
		BaseURL:  srv.URL,
		Token:    "conversation-token",
		RunToken: "run-token",
	}))
	ctx = context.WithValue(ctx, ctxKeyOut, output.New(&stdout, &stderr, false, false, false))
	cmd.SetContext(ctx)
	cmd.SetArgs([]string{name})

	if err := cmd.Execute(); err != nil {
		t.Fatalf("%s returned error: %v", cmd.Use, err)
	}
	return gotPath, gotAuth, gotCSRF, stdout.String(), stderr.String()
}
