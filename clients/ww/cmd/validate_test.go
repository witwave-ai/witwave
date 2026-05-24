// Tests for the inferKind helper that decides which X-Ww-Kind header
// `ww validate` sends to the harness `/validate` endpoint. Pure-helper
// table-driven coverage in the spirit of internal/output/output_test.go;
// the HTTP path is exercised end-to-end against a real harness, here we
// just pin the path-to-kind inference rules so a future rename in the
// `inferKind` switch (or a typo in one of the parent-dir keywords)
// fails loudly rather than silently dropping the X-Ww-Kind header.
package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/witwave-ai/witwave/clients/ww/internal/client"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
)

func TestInferKind(t *testing.T) {
	cases := []struct {
		name string
		path string
		want string
	}{
		{"jobs parent dir", filepath.Join("jobs", "daily-report.md"), "job"},
		{"tasks parent dir", filepath.Join("tasks", "daily-report.md"), "task"},
		{"triggers parent dir", filepath.Join("triggers", "notify.md"), "trigger"},
		{"continuations parent dir", filepath.Join("continuations", "after-deploy.md"), "continuation"},
		{"webhooks parent dir", filepath.Join("webhooks", "github-push.md"), "webhook"},
		{"heartbeat by basename in unrelated dir", filepath.Join(".witwave", "heartbeat.md"), "heartbeat"},
		{"heartbeat by basename at repo root", "heartbeat.md", "heartbeat"},
		{"unknown parent dir falls through", filepath.Join("snippets", "something.md"), ""},
		{"empty path returns empty", "", ""},
		{"parent dir is case-insensitive", filepath.Join("Jobs", "x.md"), "job"},
		{"basename is case-insensitive for heartbeat", filepath.Join("dir", "Heartbeat.md"), "heartbeat"},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			got := inferKind(tc.path)
			if got != tc.want {
				t.Errorf("inferKind(%q) = %q, want %q", tc.path, got, tc.want)
			}
		})
	}
}

func TestValidateUsesRunTokenAndAdhocHeader(t *testing.T) {
	var gotAuth, gotCSRF, gotContentType string
	var gotBody map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		gotCSRF = r.Header.Get("X-Ad-Hoc-Run")
		gotContentType = r.Header.Get("Content-Type")
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &gotBody)
		_, _ = w.Write([]byte(`{"ok":true,"errors":[],"parsed":{"body_len":4}}`))
	}))
	defer srv.Close()

	dir := t.TempDir()
	triggersDir := filepath.Join(dir, "triggers")
	if err := os.MkdirAll(triggersDir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(triggersDir, "notify.md")
	if err := os.WriteFile(path, []byte("---\nendpoint: notify\n---\nbody\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	cmd := newValidateCmd()
	ctx := context.WithValue(context.Background(), ctxKeyClient, client.New(client.Config{
		BaseURL:  srv.URL,
		Token:    "conversation-token",
		RunToken: "run-token",
	}))
	ctx = context.WithValue(ctx, ctxKeyOut, output.New(&stdout, &stderr, false, false, false))
	cmd.SetContext(ctx)
	cmd.SetArgs([]string{path})

	if err := cmd.Execute(); err != nil {
		t.Fatalf("validate returned error: %v", err)
	}
	if gotAuth != "Bearer run-token" {
		t.Errorf("auth = %q, want run token", gotAuth)
	}
	if gotCSRF != "1" {
		t.Errorf("X-Ad-Hoc-Run = %q, want 1", gotCSRF)
	}
	if gotContentType != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", gotContentType)
	}
	if gotBody["kind"] != "trigger" {
		t.Errorf("body kind = %q, want trigger", gotBody["kind"])
	}
	if gotBody["content"] != "---\nendpoint: notify\n---\nbody\n" {
		t.Errorf("body content = %q", gotBody["content"])
	}
	if stderr.Len() != 0 {
		t.Errorf("stderr = %q, want empty", stderr.String())
	}
	if stdout.String() != "OK\n" {
		t.Errorf("stdout = %q, want OK", stdout.String())
	}
}
