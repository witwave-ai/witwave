// Tests for the pure-helper functions in send.go that the `ww send`
// subcommand composes over the A2A backend response: extractText (the
// message/task flatten contract) and pollTaskResult (the poll-based loop).
package cmd

import (
	"context"
	"errors"
	"testing"
	"time"
)

// TestExtractText pins the A2A-response flatten helper. Contract:
//   - nil Result → empty
//   - top-level Parts → concatenate text parts (Message reply / blocking send)
//   - else artifact Parts → concatenate (completed Task, poll-based send)
//   - else status-message Parts → concatenate (Task with no artifact)
//   - non-text parts ignored; order preserved; top-level wins over the rest
func TestExtractText(t *testing.T) {
	t.Run("nil result returns empty", func(t *testing.T) {
		if got := extractText(a2aResponse{Result: nil}); got != "" {
			t.Errorf("extractText(nil-result) = %q, want empty", got)
		}
	})

	t.Run("top-level parts concatenated in order", func(t *testing.T) {
		r := a2aResponse{Result: &a2aResult{Parts: []a2aPart{
			{Kind: "text", Text: "hello "},
			{Kind: "text", Text: "world"},
		}}}
		if got := extractText(r); got != "hello world" {
			t.Errorf("extractText = %q, want %q", got, "hello world")
		}
	})

	t.Run("non-text parts ignored", func(t *testing.T) {
		r := a2aResponse{Result: &a2aResult{Parts: []a2aPart{
			{Kind: "text", Text: "before "},
			{Kind: "image", Text: "should-not-appear"},
			{Kind: "text", Text: "after"},
		}}}
		if got := extractText(r); got != "before after" {
			t.Errorf("extractText = %q, want %q", got, "before after")
		}
	})

	t.Run("completed task: artifact parts extracted", func(t *testing.T) {
		r := a2aResponse{Result: &a2aResult{
			Kind: "task",
			Artifacts: []a2aArtifact{
				{Parts: []a2aPart{{Kind: "text", Text: "from-"}, {Kind: "text", Text: "artifact"}}},
			},
		}}
		if got := extractText(r); got != "from-artifact" {
			t.Errorf("extractText = %q, want %q", got, "from-artifact")
		}
	})

	t.Run("empty top-level + no artifacts falls back to status-message parts", func(t *testing.T) {
		r := a2aResponse{Result: &a2aResult{Status: &a2aTaskStatus{Message: &a2aStatusMsg{
			Parts: []a2aPart{{Kind: "text", Text: "from-status"}},
		}}}}
		if got := extractText(r); got != "from-status" {
			t.Errorf("extractText = %q, want %q", got, "from-status")
		}
	})

	t.Run("top-level parts win over artifacts and status", func(t *testing.T) {
		r := a2aResponse{Result: &a2aResult{
			Parts:     []a2aPart{{Kind: "text", Text: "top"}},
			Artifacts: []a2aArtifact{{Parts: []a2aPart{{Kind: "text", Text: "artifact"}}}},
			Status:    &a2aTaskStatus{Message: &a2aStatusMsg{Parts: []a2aPart{{Kind: "text", Text: "status"}}}},
		}}
		if got := extractText(r); got != "top" {
			t.Errorf("extractText = %q, want %q", got, "top")
		}
	})

	t.Run("empty everything returns empty", func(t *testing.T) {
		if got := extractText(a2aResponse{Result: &a2aResult{}}); got != "" {
			t.Errorf("extractText = %q, want empty", got)
		}
	})

	t.Run("status with nil message returns empty", func(t *testing.T) {
		r := a2aResponse{Result: &a2aResult{Status: &a2aTaskStatus{Message: nil}}}
		if got := extractText(r); got != "" {
			t.Errorf("extractText = %q, want empty", got)
		}
	})
}

// fakeDoer scripts a sequence of tasks/get responses (one per poll) so the
// poll loop can be exercised without a real backend.
type fakeDoer struct {
	calls    int
	states   []string // state to return on each successive poll (last repeats)
	finalArt string   // artifact text returned with a "completed" state
	err      error    // if set, DoJSON fails (transport error)
}

func (f *fakeDoer) DoJSON(ctx context.Context, method, path string, body, out any, useRunToken bool) error {
	if f.err != nil {
		return f.err
	}
	i := f.calls
	f.calls++
	state := "working"
	if i < len(f.states) {
		state = f.states[i]
	} else if len(f.states) > 0 {
		state = f.states[len(f.states)-1]
	}
	res := &a2aResult{Kind: "task", ID: "t1", Status: &a2aTaskStatus{State: state}}
	if state == "completed" {
		res.Artifacts = []a2aArtifact{{Parts: []a2aPart{{Kind: "text", Text: f.finalArt}}}}
	}
	out.(*a2aResponse).Result = res
	return nil
}

func TestPollTaskResult(t *testing.T) {
	t.Run("polls until completed then extracts artifact", func(t *testing.T) {
		f := &fakeDoer{states: []string{"submitted", "working", "completed"}, finalArt: "poll-result"}
		got, err := pollTaskResult(context.Background(), f, "/", "t1", time.Millisecond)
		if err != nil {
			t.Fatalf("pollTaskResult err = %v", err)
		}
		if txt := extractText(got); txt != "poll-result" {
			t.Errorf("extracted %q, want %q", txt, "poll-result")
		}
		if f.calls != 3 {
			t.Errorf("polled %d times, want 3", f.calls)
		}
	})

	t.Run("failed state returns an error", func(t *testing.T) {
		f := &fakeDoer{states: []string{"failed"}}
		if _, err := pollTaskResult(context.Background(), f, "/", "t1", time.Millisecond); err == nil {
			t.Fatal("expected error for failed task, got nil")
		}
	})

	t.Run("ctx deadline returns an error without hanging", func(t *testing.T) {
		f := &fakeDoer{states: []string{"working"}} // never terminal
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		defer cancel()
		if _, err := pollTaskResult(ctx, f, "/", "t1", 5*time.Millisecond); err == nil {
			t.Fatal("expected timeout error, got nil")
		}
	})

	t.Run("transport error propagates", func(t *testing.T) {
		f := &fakeDoer{err: errors.New("boom")}
		if _, err := pollTaskResult(context.Background(), f, "/", "t1", time.Millisecond); err == nil {
			t.Fatal("expected transport error, got nil")
		}
	})
}
