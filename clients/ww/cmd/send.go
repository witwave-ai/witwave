package cmd

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/client"
)

type sendFlags struct {
	promptFile string
	backendID  string
	contextID  string
	namespace  string
	token      string
	async      bool
	asyncWait  time.Duration
}

func newSendCmd() *cobra.Command {
	sf := &sendFlags{}
	cmd := &cobra.Command{
		Use:   "send <agent> [prompt]",
		Short: "Send an A2A prompt to an agent via the harness",
		Long: "Sends a message to the named agent through the harness A2A endpoint\n" +
			"(POST / with a JSON-RPC message/send payload). If no prompt is provided\n" +
			"inline, reads from --prompt-file or stdin.",
		Args: cobra.RangeArgs(1, 2),
		RunE: func(cc *cobra.Command, args []string) error {
			ctx := cc.Context()
			out := OutFromCtx(ctx)

			agent := args[0]
			prompt, err := readPrompt(args, sf.promptFile)
			if err != nil {
				return logicalErr(err)
			}
			prompt = strings.TrimSpace(prompt)
			if prompt == "" {
				return logicalErr(fmt.Errorf("empty prompt"))
			}

			// Pick the HTTP client. If the user passed --base-url
			// (overriding the root default), respect that — they're
			// pointing at a manual port-forward or in-cluster URL.
			// Otherwise auto-port-forward to the named agent's harness
			// using the same helper `ww conversation` uses.
			c := ClientFromCtx(ctx)
			if !explicitBaseURLSet(cc) {
				ac, cleanup, err := openAgentClient(ctx, K8sFromCtx(ctx), sf.namespace, agent, sf.token)
				if err != nil {
					return handleErr(out, err)
				}
				defer cleanup()
				c = ac
			}

			// When auto-port-forwarded the harness IS the agent — POST
			// directly to "/". When --base-url points at a directory-
			// style harness (e.g. dashboard proxy), look up /agents and
			// route to the per-agent path.
			var targetURL string
			if !explicitBaseURLSet(cc) {
				targetURL = "/"
			} else {
				targetURL, err = resolveSendURL(ctx, c, agent)
				if err != nil {
					return handleErr(out, err)
				}
			}

			msgID := randomHex(8)
			ctxID := sf.contextID
			if ctxID == "" {
				ctxID = randomHex(8)
			}
			metadata := map[string]any{}
			if sf.backendID != "" {
				metadata["backend_id"] = sf.backendID
			}
			message := map[string]any{
				"messageId": msgID,
				"contextId": ctxID,
				"role":      "user",
				"parts":     []any{map[string]any{"kind": "text", "text": prompt}},
			}
			if len(metadata) > 0 {
				message["metadata"] = metadata
			}
			body := map[string]any{
				"jsonrpc": "2.0",
				"id":      1,
				"method":  "message/send",
				"params":  map[string]any{"message": message},
			}

			// A2A send uses the conversations/session token, not the
			// ad-hoc run token. The run token is reserved for dedicated
			// /run endpoints.
			if c.PreferredToken() == "" {
				out.Warnf("no token configured; request will be unauthenticated")
			}

			// Async dispatch: fire the request with a brief deadline.
			// Empirically the harness routes the prompt to its backend
			// inside a few hundred ms (harness logs show the executor's
			// "Session ... (new)" message well before the upstream LLM
			// call finishes). Once the harness has the prompt, the work
			// proceeds independently of the client connection — so we
			// can hang up early and return the contextId for tailing.
			//
			// If a deadline doesn't fire (server returns inside the
			// window), we just print the response like sync mode.
			// Non-deadline errors propagate as real failures so a wedged
			// connection / 401 / 502 doesn't get masked as "dispatched".
			if sf.async {
				asyncCtx, cancel := context.WithTimeout(ctx, sf.asyncWait)
				defer cancel()
				var resp a2aResponse
				err := c.DoJSON(asyncCtx, http.MethodPost, targetURL, body, &resp, false)
				if err == nil && resp.Error == nil {
					if out.IsJSON() {
						return out.EmitJSON(resp)
					}
					if text := extractText(resp); text != "" {
						fmt.Fprintln(out.Out, text)
					}
					return nil
				}
				// Deadline-exceeded → harness has the prompt, work
				// continues server-side. Note: the A2A contextId we
				// generated client-side is NOT the same as the
				// backend's session_id (the backend assigns its own
				// UUID when the prompt lands). `ww conversation show
				// <contextId>` would 404 — so we point the user at
				// `ww conversation list --agent <name> --follow`,
				// which surfaces every session for that agent and
				// live-tails new ones as they arrive (the newest
				// session in the list will be this dispatch).
				if errors.Is(err, context.DeadlineExceeded) {
					tailNS := sf.namespace
					if tailNS == "" {
						tailNS = "<namespace>"
					}
					fmt.Fprintf(out.Out, "Dispatched to %s (contextId=%s, messageId=%s).\n", agent, ctxID, msgID)
					fmt.Fprintf(out.Out, "Tail with: ww conversation list -n %s --agent %s --follow\n", tailNS, agent)
					return nil
				}
				// Real transport error or server-side JSON-RPC error:
				// don't pretend we dispatched.
				if err != nil {
					return handleErr(out, err)
				}
				out.Errorf("%s", resp.Error.Message)
				return logicalErr(fmt.Errorf("%s", resp.Error.Message))
			}

			var resp a2aResponse
			if err := c.DoJSON(ctx, http.MethodPost, targetURL, body, &resp, false); err != nil {
				return handleErr(out, err)
			}
			if resp.Error != nil {
				out.Errorf("%s", resp.Error.Message)
				return logicalErr(fmt.Errorf("%s", resp.Error.Message))
			}
			if out.IsJSON() {
				return out.EmitJSON(resp)
			}
			text := extractText(resp)
			if text == "" {
				out.Warnf("empty response")
				return nil
			}
			fmt.Fprintln(out.Out, text)
			return nil
		},
	}
	cmd.Flags().StringVar(&sf.promptFile, "prompt-file", "", "read prompt text from this file (- for stdin)")
	cmd.Flags().StringVar(&sf.backendID, "backend", "", "route to a specific backend (claude|openai|gemini)")
	cmd.Flags().StringVar(&sf.contextID, "context", "", "reuse this A2A contextId (default: random)")
	cmd.Flags().StringVarP(&sf.namespace, "namespace", "n", "",
		"Agent's namespace (default: kubeconfig context's namespace, falling back to 'witwave')")
	cmd.Flags().StringVar(&sf.token, "send-token", "",
		"Override the agent's CONVERSATIONS_AUTH_TOKEN (default: read from <agent>-claude Secret)")
	cmd.Flags().BoolVar(&sf.async, "async", false,
		"fire-and-forget: return as soon as the harness has the prompt, surface the contextId for tailing instead of blocking on the LLM response")
	cmd.Flags().DurationVar(&sf.asyncWait, "async-wait", 3*time.Second,
		"with --async, how long to wait for the harness to receive the request before treating it as dispatched")
	return cmd
}

// explicitBaseURLSet reports whether the user passed --base-url on
// this invocation (as opposed to inheriting it from config / leaving
// it unset). When true we keep the existing manual-port-forward flow;
// when false we auto-port-forward via openAgentClient.
func explicitBaseURLSet(cmd *cobra.Command) bool {
	f := cmd.Root().PersistentFlags().Lookup("base-url")
	if f == nil {
		f = cmd.InheritedFlags().Lookup("base-url")
	}
	return f != nil && f.Changed
}

func resolveSendURL(ctx context.Context, c any, agent string) (string, error) {
	// A dashboard-style harness proxies /agents/<name>/ to the A2A root
	// of that agent's harness. Pure harness / single-agent deployments
	// accept POST "/" directly. We look up /agents; if we find a member
	// whose id matches, we return its absolute URL with a trailing
	// slash. Otherwise fall back to "/agents/<name>/" for dashboard
	// compatibility.
	type entry struct {
		ID  string `json:"id"`
		URL string `json:"url"`
	}
	var agents []entry
	hc, ok := c.(interface {
		DoJSON(ctx context.Context, method, path string, body, out any, useRun bool) error
	})
	if !ok {
		return "", fmt.Errorf("client type assertion failed")
	}
	if err := hc.DoJSON(ctx, http.MethodGet, "/agents", nil, &agents, false); err != nil {
		// #1555: only fall back to the dashboard-style proxy path for
		// errors that indicate "/agents isn't exposed here" — a 404
		// from a plain harness, or a transport error where the
		// directory endpoint simply can't be reached. Auth failures
		// (401/403), server errors (5xx), and other 4xx responses
		// propagate so the user sees a real diagnostic instead of a
		// masked token/scope problem dressed up as a silent fallback.
		if he, ok := client.IsHTTPError(err); ok {
			if he.StatusCode != http.StatusNotFound {
				return "", err
			}
			// 404 → fall through to proxy path below.
		}
		return "/agents/" + agent + "/", nil
	}
	for _, a := range agents {
		if a.ID == agent && a.URL != "" {
			u := strings.TrimRight(a.URL, "/")
			return u + "/", nil
		}
	}
	// Not in directory — still try the proxy path for forward-compat.
	return "/agents/" + agent + "/", nil
}

func readPrompt(args []string, file string) (string, error) {
	if len(args) >= 2 {
		return args[1], nil
	}
	if file == "" {
		return "", fmt.Errorf("no prompt provided: pass an argument or --prompt-file")
	}
	if file == "-" {
		b, err := io.ReadAll(os.Stdin)
		return string(b), err
	}
	b, err := os.ReadFile(file)
	return string(b), err
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		// #1397: cryto/rand failure is extremely rare but not impossible
		// (e.g. seccomp stripping getrandom). Previous fallback returned
		// the literal "fallbackid" for every caller — parallel invocations
		// collided on contextId and merged distinct A2A conversations
		// on the backend. Use PID + high-res time so collisions require
		// same-pid-same-nanosecond.
		now := time.Now().UnixNano()
		return fmt.Sprintf("ww-%d-%d", os.Getpid(), now)
	}
	return hex.EncodeToString(b)
}

type a2aError struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
}

type a2aPart struct {
	Kind string `json:"kind"`
	Text string `json:"text,omitempty"`
}

type a2aResponse struct {
	JSONRPC string    `json:"jsonrpc"`
	ID      any       `json:"id"`
	Error   *a2aError `json:"error,omitempty"`
	Result  *struct {
		Parts  []a2aPart `json:"parts,omitempty"`
		Status *struct {
			Message *struct {
				Parts []a2aPart `json:"parts,omitempty"`
			} `json:"message,omitempty"`
		} `json:"status,omitempty"`
	} `json:"result,omitempty"`
}

func extractText(r a2aResponse) string {
	if r.Result == nil {
		return ""
	}
	parts := r.Result.Parts
	if len(parts) == 0 && r.Result.Status != nil && r.Result.Status.Message != nil {
		parts = r.Result.Status.Message.Parts
	}
	var b strings.Builder
	for _, p := range parts {
		if p.Kind == "text" {
			b.WriteString(p.Text)
		}
	}
	return b.String()
}
