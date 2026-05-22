package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// SendOptions — inputs to `ww agent send`.
type SendOptions struct {
	Agent     string
	Namespace string
	Prompt    string

	// MessageID is stamped on the A2A `message/send` payload. Optional;
	// when blank, a timestamp-based value is generated so back-to-back
	// calls don't collide on the harness's dedup key.
	MessageID string

	// BackendID, when non-empty, is stamped onto the A2A message's
	// `metadata.backend_id` field. The harness honours this to route
	// the prompt to a specific backend container instead of the
	// default-routed one (per the agent's backend.yaml). Empty value
	// = harness picks per its routing config (the common case).
	BackendID string

	// Timeout bounds the round-trip through the apiserver proxy. The
	// default (30s) leaves room for cold-start delays on freshly
	// reconciled agents; scripts can tighten this.
	Timeout time.Duration

	// RawJSON prints the raw JSON-RPC response body instead of
	// extracting the agent text. Useful for debugging wire shapes.
	RawJSON bool

	Out io.Writer
}

// a2aRequest / a2aResponse mirror the narrow subset of the A2A JSON-RPC
// surface that `message/send` uses today. We don't build a full A2A
// client here — the echo smoke test and claude/openai/gemini are served
// by the A2A SDK, so there's a canonical wire format we can depend on.
type a2aRequest struct {
	JSONRPC string            `json:"jsonrpc"`
	ID      string            `json:"id"`
	Method  string            `json:"method"`
	Params  a2aRequest2params `json:"params"`
}

type a2aRequest2params struct {
	Message a2aMessage `json:"message"`
}

type a2aMessage struct {
	Role      string         `json:"role"`
	Parts     []a2aTextPart  `json:"parts"`
	MessageID string         `json:"messageId"`
	Kind      string         `json:"kind"`
	Metadata  map[string]any `json:"metadata,omitempty"`
}

type a2aTextPart struct {
	Kind string `json:"kind"`
	Text string `json:"text"`
}

type a2aResponse struct {
	JSONRPC string           `json:"jsonrpc"`
	ID      string           `json:"id"`
	Result  *a2aResult       `json:"result,omitempty"`
	Error   *a2aErrorPayload `json:"error,omitempty"`
}

type a2aResult struct {
	Kind  string        `json:"kind"`
	Parts []a2aTextPart `json:"parts"`
	Role  string        `json:"role"`
}

type a2aErrorPayload struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Send makes a single A2A `message/send` round-trip to the agent's
// harness Service via the apiserver's built-in Service proxy. This
// avoids the lifecycle complexity of port-forwarding and works with
// ClusterIP Services out of the box.
//
// Caveat: the apiserver proxy is not suited for streaming or very
// large payloads (size caps apply). The harness's JSON-RPC request/
// response shape fits comfortably.
func Send(ctx context.Context, cfg *rest.Config, opts SendOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("SendOptions.Out is required")
	}
	if opts.Agent == "" {
		return fmt.Errorf("SendOptions.Agent is required")
	}
	if opts.Namespace == "" {
		return fmt.Errorf("SendOptions.Namespace is required")
	}
	if opts.Prompt == "" {
		return fmt.Errorf("prompt is required; pass a string after the agent name")
	}

	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 30 * time.Second
	}

	k8s, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build kubernetes client: %w", err)
	}

	// #1720: bound the entire round-trip with --timeout, including the
	// prerequisite Service Get. Previously the Get used the outer ctx and
	// a hanging apiserver could block past the user-supplied timeout.
	sendCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Sanity-check the Service exists before we build a proxy URL —
	// otherwise the apiserver returns a generic 503/404 that isn't
	// always obvious.
	svc, err := k8s.CoreV1().Services(opts.Namespace).Get(sendCtx, opts.Agent, metav1.GetOptions{})
	if err != nil {
		if apierrors.IsNotFound(err) {
			return fmt.Errorf(
				"no Service named %q in namespace %s — is the agent Ready? Run `ww agent status %s`",
				opts.Agent, opts.Namespace, opts.Agent,
			)
		}
		return fmt.Errorf("get Service %s/%s: %w", opts.Namespace, opts.Agent, err)
	}

	port, err := pickServicePort(svc)
	if err != nil {
		return err
	}

	messageID := opts.MessageID
	if messageID == "" {
		messageID = fmt.Sprintf("ww-send-%d", time.Now().UnixNano())
	}

	msg := a2aMessage{
		Role:      "user",
		Parts:     []a2aTextPart{{Kind: "text", Text: opts.Prompt}},
		MessageID: messageID,
		Kind:      "message",
	}
	// Stamp metadata.backend_id when the caller wants a specific
	// backend. Harness routes per spec.config backend.yaml when this
	// is unset; setting it bypasses the default-routing primary and
	// hits the named sidecar directly.
	if opts.BackendID != "" {
		msg.Metadata = map[string]any{"backend_id": opts.BackendID}
	}
	reqBody := a2aRequest{
		JSONRPC: "2.0",
		ID:      "1",
		Method:  "message/send",
		Params:  a2aRequest2params{Message: msg},
	}
	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return fmt.Errorf("marshal A2A request: %w", err)
	}

	// sendCtx is bounded above so the proxy POST shares the same
	// timeout budget as the Service Get (#1720).

	// Apiserver service-proxy path:
	//   /api/v1/namespaces/<ns>/services/<scheme>:<svc>:<port>/proxy/
	// Trailing slash matters — the harness mounts A2A at `/`.
	proxyPath := fmt.Sprintf(
		"/api/v1/namespaces/%s/services/http:%s:%d/proxy/",
		opts.Namespace, opts.Agent, port,
	)
	result := k8s.CoreV1().RESTClient().Post().
		AbsPath(proxyPath).
		SetHeader("Content-Type", "application/json").
		Body(bodyBytes).
		Do(sendCtx)

	raw, err := result.Raw()
	if err != nil {
		return fmt.Errorf("POST A2A request via Service proxy: %w", err)
	}

	if opts.RawJSON {
		fmt.Fprintln(opts.Out, string(raw))
		return nil
	}

	var resp a2aResponse
	if err := json.Unmarshal(raw, &resp); err != nil {
		return fmt.Errorf("parse A2A response (raw=%q): %w", string(raw), err)
	}
	if resp.Error != nil {
		return fmt.Errorf("A2A error %d: %s", resp.Error.Code, resp.Error.Message)
	}
	if resp.Result == nil || len(resp.Result.Parts) == 0 {
		return fmt.Errorf("A2A response contained no parts — wire format may have drifted; rerun with --raw to inspect")
	}
	for _, part := range resp.Result.Parts {
		if part.Kind != "text" {
			// Unknown part kind — emit a marker so callers see it in
			// scripted output rather than a silent skip.
			fmt.Fprintf(opts.Out, "[non-text part kind=%q skipped]\n", part.Kind)
			continue
		}
		fmt.Fprintln(opts.Out, part.Text)
	}
	return nil
}

// pickServicePort picks the port this Service expects the A2A JSON-RPC
// endpoint to listen on. The harness convention is a port named "http";
// we fall back to the first port if no name matches. Metrics ports
// (typically name=metrics, targetPort=metrics-harness) are deliberately
// skipped — A2A traffic never lands on them.
func pickServicePort(svc *corev1.Service) (int32, error) {
	if len(svc.Spec.Ports) == 0 {
		return 0, fmt.Errorf("Service %s/%s has no ports", svc.Namespace, svc.Name)
	}
	// Prefer the named http port — this matches the operator's
	// convention (witwaveagent_resources.go: {Name: "http", ...}).
	for _, p := range svc.Spec.Ports {
		if p.Name == "http" {
			return p.Port, nil
		}
	}
	// Fall back to the first non-metrics port.
	for _, p := range svc.Spec.Ports {
		if p.Name == "metrics" {
			continue
		}
		return p.Port, nil
	}
	// Only metrics ports? Something's off; return the first and let the
	// apiserver proxy's 404 / error surface naturally.
	return svc.Spec.Ports[0].Port, nil
}
