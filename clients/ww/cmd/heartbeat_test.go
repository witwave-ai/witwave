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

func TestRunHeartbeatRunPostsAdhocEndpoint(t *testing.T) {
	var gotMethod, gotPath, gotAuth, gotCSRF string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		gotAuth = r.Header.Get("Authorization")
		gotCSRF = r.Header.Get("X-Ad-Hoc-Run")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"delivery_id":"delivery-1","session_id":"heartbeat","kind":"heartbeat"}`))
	}))
	defer srv.Close()

	var stdout, stderr bytes.Buffer
	cc := &cobra.Command{}
	ctx := context.WithValue(context.Background(), ctxKeyClient, client.New(client.Config{
		BaseURL:  srv.URL,
		Token:    "conversation-token",
		RunToken: "run-token",
	}))
	ctx = context.WithValue(ctx, ctxKeyOut, output.New(&stdout, &stderr, false, false, false))
	cc.SetContext(ctx)

	if err := runHeartbeatRun(cc); err != nil {
		t.Fatalf("runHeartbeatRun returned error: %v", err)
	}
	if gotMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if gotPath != "/heartbeat/run" {
		t.Errorf("path = %q, want /heartbeat/run", gotPath)
	}
	if gotAuth != "Bearer run-token" {
		t.Errorf("auth = %q, want run token", gotAuth)
	}
	if gotCSRF != "1" {
		t.Errorf("X-Ad-Hoc-Run = %q, want 1", gotCSRF)
	}
	rendered := stdout.String()
	for _, want := range []string{"delivery_id", "delivery-1", "session_id", "heartbeat"} {
		if !strings.Contains(rendered, want) {
			t.Errorf("output %q does not contain %q", rendered, want)
		}
	}
	if stderr.Len() != 0 {
		t.Errorf("stderr = %q, want empty", stderr.String())
	}
}
