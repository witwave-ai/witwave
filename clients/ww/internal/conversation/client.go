// Package conversation talks to a harness's /conversations REST endpoint
// — the same surface the dashboard hits for its conversation views.
//
// v1 covers list (cross-agent and per-agent) and show (one session by
// id). Live-tail (watch) is deferred to v2 because it lives on the
// backend container's port :8001, not the harness's :8000, and adds a
// second port-forward to the implementation. List + show are both
// harness-only and ship as one cohesive piece.
package conversation

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Entry is one conversation log line. Mirrors the dashboard's
// ConversationEntry shape (clients/dashboard/src/types/chat.ts) so the
// JSON unmarshal lines up byte-for-byte with what the harness emits.
type Entry struct {
	TS        string  `json:"ts"`
	Timestamp string  `json:"timestamp,omitempty"`
	Agent     string  `json:"agent"`
	SessionID string  `json:"session_id,omitempty"`
	Role      string  `json:"role"`
	Model     *string `json:"model,omitempty"`
	Tokens    *int    `json:"tokens,omitempty"`
	Text      *string `json:"text,omitempty"`
	TraceID   *string `json:"trace_id,omitempty"`
}

// Client is a thin HTTP client over a harness's /conversations
// endpoint. Take one per (agent, base URL) pair — base URL comes from
// the port-forward helper, token from the agent's credentials secret
// (or the user's --token override).
type Client struct {
	BaseURL string
	Token   string
	HTTP    *http.Client
}

// NewClient returns a Client with sensible defaults for the CLI use
// case (60-second per-request timeout). Callers can override Client.HTTP
// for tests.
func NewClient(baseURL, token string) *Client {
	return &Client{
		BaseURL: baseURL,
		Token:   token,
		HTTP:    &http.Client{Timeout: 60 * time.Second},
	}
}

// ListOptions filters the /conversations list. Both fields are
// optional; empty/zero values mean "no filter on the server side."
type ListOptions struct {
	// Since is an ISO-8601 timestamp; the harness returns entries
	// strictly newer than this. Empty = no time filter.
	Since string
	// Limit caps the number of entries the harness returns. 0 = no
	// limit (server picks).
	Limit int
}

// List fetches conversation entries from the harness, oldest-first
// (the harness's emission order). Caller can re-sort or filter
// downstream as needed.
func (c *Client) List(ctx context.Context, opts ListOptions) ([]Entry, error) {
	url := c.BaseURL + "/conversations"
	q := ""
	if opts.Since != "" {
		q = "since=" + opts.Since
	}
	if opts.Limit > 0 {
		if q != "" {
			q += "&"
		}
		q += fmt.Sprintf("limit=%d", opts.Limit)
	}
	if q != "" {
		url += "?" + q
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("conversations request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	switch resp.StatusCode {
	case http.StatusOK:
		var entries []Entry
		if err := json.Unmarshal(body, &entries); err != nil {
			return nil, fmt.Errorf("decode conversations response: %w (body: %.200s)", err, string(body))
		}
		return entries, nil
	case http.StatusUnauthorized:
		return nil, fmt.Errorf("unauthorized: harness rejected the bearer token (set CONVERSATIONS_AUTH_TOKEN on the agent and pass --token, or set CONVERSATIONS_AUTH_DISABLED=true for local dev)")
	case http.StatusServiceUnavailable:
		return nil, fmt.Errorf("conversations endpoint not configured on the agent: harness has CONVERSATIONS_AUTH_TOKEN unset and CONVERSATIONS_AUTH_DISABLED not set; the endpoint fails closed by design")
	default:
		return nil, fmt.Errorf("conversations request: HTTP %d (%.200s)", resp.StatusCode, string(body))
	}
}

// FilterSession returns only entries whose SessionID equals sessionID.
// Used by `ww conversation show` to filter a list call's results down
// to one session — the harness doesn't expose a per-session endpoint,
// so we fetch the broader log and filter client-side. Cheap on
// bounded-size logs (the harness's default truncation keeps each agent's
// conversation log under a few thousand entries).
func FilterSession(entries []Entry, sessionID string) []Entry {
	out := make([]Entry, 0, len(entries))
	for i := range entries {
		if entries[i].SessionID == sessionID {
			out = append(out, entries[i])
		}
	}
	return out
}

// SessionSummary is one row in the `ww conversation list` output —
// distilled from the raw entries belonging to one session. Used for
// the table display.
type SessionSummary struct {
	SessionID    string
	Agent        string
	Namespace    string
	Started      string
	LastActivity string
	Turns        int
	// Source is a heuristic — first-message role/metadata determines
	// whether this looks like a heartbeat tick, an A2A dispatch, a job,
	// etc. Empty when we can't tell.
	Source string
}

// Summarize collapses a flat list of entries into one row per
// (agent, session). Sorted by LastActivity descending so the most-
// recent sessions float to the top of the table. Stable when timestamps
// tie (rare).
func Summarize(entries []Entry, namespace string) []SessionSummary {
	type key struct{ agent, session string }
	seen := make(map[key]*SessionSummary)
	for i := range entries {
		e := &entries[i]
		if e.SessionID == "" {
			continue // shouldn't happen for real session entries; skip defensively
		}
		k := key{agent: e.Agent, session: e.SessionID}
		s, ok := seen[k]
		if !ok {
			s = &SessionSummary{
				SessionID: e.SessionID,
				Agent:     e.Agent,
				Namespace: namespace,
				Started:   e.TS,
				Source:    inferSource(e),
			}
			seen[k] = s
		}
		s.Turns++
		s.LastActivity = e.TS // entries arrive oldest-first
		if e.TS < s.Started {
			s.Started = e.TS
		}
	}
	out := make([]SessionSummary, 0, len(seen))
	for _, s := range seen {
		out = append(out, *s)
	}
	// Newest-last-activity first.
	for i := 0; i < len(out); i++ {
		for j := i + 1; j < len(out); j++ {
			if out[j].LastActivity > out[i].LastActivity {
				out[i], out[j] = out[j], out[i]
			}
		}
	}
	return out
}

// inferSource picks a coarse "where did this session come from?"
// label from the first entry's role and (if present) text shape. Real
// answers come from looking at the harness's own `decision_log` or
// the dispatch source, but those aren't in /conversations output —
// this is the best we can do with what's locally visible.
func inferSource(first *Entry) string {
	if first.Text != nil {
		t := *first.Text
		if len(t) > 30 && (containsAny(t, "Heartbeat check", "Run your", "dispatch-team", "team-tidy")) {
			return "heartbeat"
		}
	}
	if first.Role == "user" {
		return "a2a"
	}
	return ""
}

func containsAny(haystack string, needles ...string) bool {
	for _, n := range needles {
		if len(haystack) >= len(n) {
			for i := 0; i+len(n) <= len(haystack); i++ {
				if haystack[i:i+len(n)] == n {
					return true
				}
			}
		}
	}
	return false
}
