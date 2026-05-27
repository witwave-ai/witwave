package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/client"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
)

type tailFlags struct {
	agent       string
	session     string
	pretty      bool
	lastEventID string
	types       []string
	namespace   string
	token       string
}

func newTailCmd() *cobra.Command {
	tf := &tailFlags{}
	cmd := &cobra.Command{
		Use:   "tail",
		Short: "Stream events from the harness (or a specific agent/session)",
		Long: "Subscribes to the harness /events/stream SSE endpoint. When --agent is set,\n" +
			"connects directly to that agent's harness. With --session AND --agent, switches\n" +
			"to per-session drill-down on the backend. Reconnects automatically with\n" +
			"exponential backoff, sending Last-Event-ID on resume.",
		RunE: func(cc *cobra.Command, args []string) error {
			ctx, stop := signal.NotifyContext(cc.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()

			out := OutFromCtx(ctx)
			if tf.session != "" && tf.agent == "" {
				return logicalErr(fmt.Errorf("--session requires --agent"))
			}

			// Pick the HTTP client. If --base-url is explicitly set,
			// honour it (manual port-forward / in-cluster URL). When
			// --agent is set without --base-url, auto-port-forward to
			// that agent's harness — same shape as `ww send` and
			// `ww conversation`. With neither, fall back to the root's
			// pre-built client (talks to whatever default base URL
			// config has).
			c := ClientFromCtx(ctx)
			if tf.agent != "" && !explicitBaseURLSet(cc) {
				ac, cleanup, err := openAgentClient(ctx, K8sFromCtx(ctx), tf.namespace, tf.agent, tf.token)
				if err != nil {
					return handleErr(out, err)
				}
				defer cleanup()
				c = ac
			}
			typeFilter := make(map[string]struct{})
			for _, t := range tf.types {
				if t != "" {
					typeFilter[t] = struct{}{}
				}
			}

			targetURL, err := resolveStreamURL(ctx, c, tf)
			if err != nil {
				return handleErr(out, err)
			}

			lastID := tf.lastEventID
			const initialBackoff = 100 * time.Millisecond
			const minReconnectFloor = 500 * time.Millisecond
			backoff := initialBackoff
			for {
				if ctx.Err() != nil {
					return nil
				}
				resumed, receivedAny, err := streamOnce(ctx, c, out, targetURL, lastID, tf.pretty, typeFilter)
				if resumed != "" {
					lastID = resumed
				}
				if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
					return nil
				}
				if err == nil || errors.Is(err, io.EOF) {
					// Normal server-side close. Only reset backoff if
					// we actually received something in this attempt —
					// otherwise back off to avoid a tight reconnect
					// loop against a broken endpoint.
					if receivedAny {
						backoff = initialBackoff
						continue
					}
					// No events; enforce a minimum reconnect floor and
					// keep escalating until something comes back.
					wait := backoff
					if wait < minReconnectFloor {
						wait = minReconnectFloor
					}
					if !sleepCtx(ctx, wait) {
						return nil
					}
					backoff = nextBackoff(backoff)
					continue
				}
				out.Warnf("stream error: %v (reconnecting in %v)", err, backoff)
				if !sleepCtx(ctx, backoff) {
					return nil
				}
				backoff = nextBackoff(backoff)
			}
		},
	}
	cmd.Flags().StringVar(&tf.agent, "agent", "", "target a specific agent by name (auto-port-forwards when --base-url isn't set)")
	cmd.Flags().StringVar(&tf.session, "session", "", "session id for per-session drill-down (requires --agent)")
	cmd.Flags().BoolVar(&tf.pretty, "pretty", false, "human-readable event lines (default: JSON lines)")
	cmd.Flags().StringVar(&tf.lastEventID, "last-event-id", "", "resume from this event id")
	cmd.Flags().StringSliceVar(&tf.types, "types", nil, "comma-separated list of event types to emit")
	cmd.Flags().StringVarP(&tf.namespace, "namespace", "n", "",
		"Agent's namespace (default: kubeconfig context's namespace, falling back to 'witwave')")
	cmd.Flags().StringVar(&tf.token, "tail-token", "",
		"Override the agent's CONVERSATIONS_AUTH_TOKEN (default: read from <agent>-claude Secret)")
	return cmd
}

func resolveStreamURL(ctx context.Context, c *client.Client, tf *tailFlags) (string, error) {
	if tf.agent == "" {
		return "/events/stream", nil
	}
	// Look up the agent's URL from /agents.
	var agents []agentEntry
	if err := c.DoJSON(ctx, http.MethodGet, "/agents", nil, &agents, false); err != nil {
		return "", err
	}
	for _, a := range agents {
		if a.ID == tf.agent {
			base := strings.TrimRight(a.URL, "/")
			if tf.session != "" {
				return base + "/api/sessions/" + tf.session + "/stream", nil
			}
			return base + "/events/stream", nil
		}
	}
	return "", fmt.Errorf("agent %q not found in /agents", tf.agent)
}

// streamOnce connects once and returns the last-seen event id so a
// reconnect can resume. The receivedAny bool reports whether any real
// event (not a keepalive / empty block) was consumed during this
// attempt — callers use it to decide whether to reset reconnect
// backoff. err is nil at EOF.
func streamOnce(
	ctx context.Context,
	c *client.Client,
	out *output.Writer,
	target, lastID string,
	pretty bool,
	typeFilter map[string]struct{},
) (string, bool, error) {
	hdr := http.Header{}
	if lastID != "" {
		hdr.Set("Last-Event-ID", lastID)
	}
	resp, err := c.OpenStream(ctx, http.MethodGet, target, nil, hdr, false)
	if err != nil {
		return lastID, false, err
	}
	defer resp.Body.Close()

	p := client.NewSSEParser(resp.Body)
	seenID := lastID
	receivedAny := false
	for {
		ev, err := p.Next(ctx)
		if err != nil {
			return seenID, receivedAny, err
		}
		if ev.IsKeepalive() {
			continue
		}
		receivedAny = true
		if ev.ID != "" {
			seenID = ev.ID
		}
		if len(typeFilter) > 0 {
			if _, ok := typeFilter[ev.Type]; !ok {
				continue
			}
		}
		emitEvent(out, ev, pretty)
	}
}

func emitEvent(w *output.Writer, ev client.Event, pretty bool) {
	// Re-cast via an untyped record to preserve field order across
	// pretty / JSON modes.
	rec := struct {
		Type  string          `json:"type,omitempty"`
		ID    string          `json:"id,omitempty"`
		Data  json.RawMessage `json:"data,omitempty"`
		Retry int64           `json:"retry_ms,omitempty"`
	}{
		Type:  ev.Type,
		ID:    ev.ID,
		Retry: ev.Retry.Milliseconds(),
	}
	dataIsJSON := false
	if ev.Data != "" {
		// Try to emit as JSON; fall back to a string.
		if json.Valid([]byte(ev.Data)) {
			rec.Data = json.RawMessage(ev.Data)
			dataIsJSON = true
		} else {
			b, _ := json.Marshal(ev.Data)
			rec.Data = b
		}
	}
	if !pretty {
		b, err := json.Marshal(rec)
		if err != nil {
			fmt.Fprintln(w.Out, "{\"error\":\"marshal failed\"}")
			return
		}
		fmt.Fprintln(w.Out, string(b))
		return
	}
	// Human pretty line. When the raw data wasn't valid JSON, render it
	// as-is without the JSON quoting/escaping layer so users see the
	// wire text instead of an escaped string literal.
	var rendered string
	if ev.Data == "" {
		rendered = ""
	} else if dataIsJSON {
		rendered = string(rec.Data)
	} else {
		rendered = ev.Data
	}
	fmt.Fprintf(w.Out, "[%s] id=%s  %s\n", ev.Type, ev.ID, rendered)
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func nextBackoff(d time.Duration) time.Duration {
	next := d * 2
	if next > 10*time.Second {
		next = 10 * time.Second
	}
	// Apply jitter: +/-25%.
	jitter := time.Duration(rand.Int63n(int64(next / 2))) //nolint:gosec // G404: non-cryptographic tail-poll backoff jitter; math/rand is the canonical choice for anti-stampede timing.
	return next/2 + jitter
}
