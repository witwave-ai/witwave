package client

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestClient_DoJSON_Auth(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "secret"})
	var out struct{ OK bool }
	if err := c.DoJSON(context.Background(), http.MethodGet, "/ping", nil, &out, false); err != nil {
		t.Fatal(err)
	}
	if gotAuth != "Bearer secret" {
		t.Errorf("auth header: got %q", gotAuth)
	}
	if !out.OK {
		t.Errorf("decode failed: %+v", out)
	}
}

func TestClient_DoJSON_RunTokenPreferred(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "conv", RunToken: "run"})
	_ = c.DoJSON(context.Background(), http.MethodPost, "/x", map[string]string{"a": "b"}, nil, true)
	if gotAuth != "Bearer run" {
		t.Errorf("want run token, got %q", gotAuth)
	}
}

func TestClient_DoJSON_RunTokenAddsAdhocCSRFHeader(t *testing.T) {
	var gotCSRF string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotCSRF = r.Header.Get("X-Ad-Hoc-Run")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "conv", RunToken: "run"})
	_ = c.DoJSON(context.Background(), http.MethodPost, "/x", nil, nil, true)
	if gotCSRF != "1" {
		t.Errorf("want X-Ad-Hoc-Run=1, got %q", gotCSRF)
	}
}

func TestClient_DoJSON_RetryOn5xx(t *testing.T) {
	var count int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&count, 1)
		if n < 3 {
			http.Error(w, "oops", http.StatusBadGateway)
			return
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "t"})
	var out struct{ OK bool }
	err := c.DoJSON(context.Background(), http.MethodGet, "/x", nil, &out, false)
	if err != nil {
		t.Fatalf("expected success after retry, got %v", err)
	}
	if atomic.LoadInt32(&count) != 3 {
		t.Errorf("want 3 attempts, got %d", count)
	}
}

func TestClient_DoJSON_No4xxRetry(t *testing.T) {
	var count int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&count, 1)
		http.Error(w, "forbidden", http.StatusForbidden)
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "t"})
	err := c.DoJSON(context.Background(), http.MethodGet, "/x", nil, nil, false)
	he, ok := IsHTTPError(err)
	if !ok {
		t.Fatalf("want HTTPError, got %v", err)
	}
	if he.StatusCode != http.StatusForbidden {
		t.Errorf("status: %d", he.StatusCode)
	}
	if got := atomic.LoadInt32(&count); got != 1 {
		t.Errorf("want 1 attempt, got %d", got)
	}
}

func TestClient_Resolve_Absolute(t *testing.T) {
	c := New(Config{BaseURL: "http://base.example/"})
	u, err := c.Resolve("http://direct.example/agents")
	if err != nil || u != "http://direct.example/agents" {
		t.Fatalf("got (%q, %v)", u, err)
	}
}

func TestClient_OpenStream_SSE(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		fl, ok := w.(http.Flusher)
		if !ok {
			t.Errorf("want Flusher")
			return
		}
		_, _ = io.WriteString(w, "event:ping\ndata:{\"n\":1}\nid:1\n\n")
		fl.Flush()
		_, _ = io.WriteString(w, ": keepalive\n\n")
		fl.Flush()
		_, _ = io.WriteString(w, "event:pong\ndata:{\"n\":2}\nid:2\n\n")
		fl.Flush()
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "t"})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	resp, err := c.OpenStream(ctx, http.MethodGet, "/events/stream", nil, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	p := NewSSEParser(resp.Body)
	ev, err := p.Next(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if ev.Type != "ping" {
		t.Errorf("type: %q", ev.Type)
	}
	var payload struct{ N int }
	if err := json.Unmarshal([]byte(ev.Data), &payload); err != nil || payload.N != 1 {
		t.Errorf("payload: %+v err=%v", payload, err)
	}

	ev, err = p.Next(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !ev.IsKeepalive() {
		t.Errorf("want keepalive, got %+v", ev)
	}

	ev, err = p.Next(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if ev.Type != "pong" || ev.ID != "2" {
		t.Errorf("pong: %+v", ev)
	}

	_, err = p.Next(ctx)
	if !errors.Is(err, io.EOF) {
		t.Errorf("want EOF, got %v", err)
	}
}

func TestClient_Timeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "t", Timeout: 20 * time.Millisecond})
	err := c.DoJSON(context.Background(), http.MethodGet, "/x", nil, nil, false)
	if err == nil {
		t.Fatal("want timeout error")
	}
	if !strings.Contains(err.Error(), "transport") {
		t.Errorf("err: %v", err)
	}
}

// TestClient_OpenStream_SurvivesPastSnapshotTimeout pins #1733: snapshot
// callers (DoJSON / GetBytes) must keep their per-request Timeout, but
// stream callers (OpenStream) must not be severed when that same
// timeout elapses. The handler below emits an event, then sleeps
// 4*Timeout, then emits another event; under the regression we'd get
// the first event but the body Read after the timeout would return
// "context deadline exceeded" / "stream error".
func TestClient_OpenStream_SurvivesPastSnapshotTimeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		fl, ok := w.(http.Flusher)
		if !ok {
			t.Errorf("want Flusher")
			return
		}
		_, _ = io.WriteString(w, "event:first\ndata:1\nid:1\n\n")
		fl.Flush()
		// Sleep past the snapshot Timeout window. If OpenStream
		// reuses the snapshot client (with Timeout set), this
		// sleep tears the body down before the second event flushes.
		time.Sleep(120 * time.Millisecond)
		_, _ = io.WriteString(w, "event:second\ndata:2\nid:2\n\n")
		fl.Flush()
	}))
	defer srv.Close()

	// Snapshot Timeout deliberately tight so the regression would
	// fire well within the test budget.
	c := New(Config{BaseURL: srv.URL, Token: "t", Timeout: 30 * time.Millisecond})
	// Caller's context provides the only stream-side cancellation.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	resp, err := c.OpenStream(ctx, http.MethodGet, "/events/stream", nil, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	p := NewSSEParser(resp.Body)
	first, err := p.Next(ctx)
	if err != nil {
		t.Fatalf("first event: %v", err)
	}
	if first.Type != "first" {
		t.Fatalf("first.Type = %q", first.Type)
	}

	// This is the regression-blocking read: it must succeed past the
	// snapshot Timeout boundary.
	second, err := p.Next(ctx)
	if err != nil {
		t.Fatalf("second event after past-Timeout sleep: %v", err)
	}
	if second.Type != "second" {
		t.Fatalf("second.Type = %q", second.Type)
	}
}

// TestClient_OpenStream_RespectsContextCancellation pins the inverse —
// the caller's context.Context is the legitimate cancellation path
// for OpenStream, so an explicit cancel() must still tear the stream
// down promptly.
func TestClient_OpenStream_RespectsContextCancellation(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		fl, _ := w.(http.Flusher)
		_, _ = io.WriteString(w, "event:hello\ndata:1\nid:1\n\n")
		if fl != nil {
			fl.Flush()
		}
		// Block until the connection drops (ctx-cancel).
		<-r.Context().Done()
	}))
	defer srv.Close()

	c := New(Config{BaseURL: srv.URL, Token: "t", Timeout: 30 * time.Millisecond})
	ctx, cancel := context.WithCancel(context.Background())
	resp, err := c.OpenStream(ctx, http.MethodGet, "/events/stream", nil, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	p := NewSSEParser(resp.Body)
	if _, err := p.Next(ctx); err != nil {
		t.Fatalf("first read: %v", err)
	}
	cancel()
	// Subsequent read must fail (context cancelled), not hang.
	done := make(chan error, 1)
	go func() { _, e := p.Next(ctx); done <- e }()
	select {
	case e := <-done:
		if e == nil {
			t.Fatal("expected error after cancel, got nil")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("read did not unblock after ctx cancel")
	}
}
