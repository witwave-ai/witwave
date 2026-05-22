// Package portforward establishes ephemeral kubectl-style port-forwards
// to harness pods so CLI commands that talk to the harness HTTP surface
// (conversations, traces, sessions, events) don't need the user to set
// up `kubectl port-forward` + `--base-url` manually.
//
// The helper resolves a WitwaveAgent name to its backing Pod, opens a
// SPDY-tunnelled forward to the harness container's port, and returns a
// base URL the caller can hit with a normal http.Client. A cleanup
// closure tears the forward down on Ctrl-C / context cancel; nothing
// leaks once the caller's context goes away.
package portforward

import (
	"bytes"
	"context"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/portforward"
	"k8s.io/client-go/transport/spdy"
)

// HarnessHTTPPort is the harness's app-listener port inside the pod
// (the one serving /conversations, /trace, /events/stream, etc). The
// metrics listener on :9000 is intentionally out of scope here — those
// are scraped, not browsed.
const HarnessHTTPPort = 8000

// BackendHTTPPort is the claude/openai/codex/gemini backend container's app
// listener — same pod as the harness, different port. Serves the
// per-session SSE endpoint /api/sessions/{id}/stream that backs
// `ww conversation show --follow` (the harness only fans out aggregate
// /conversations reads, so live-tail of one session has to hit the
// backend directly).
const BackendHTTPPort = 8001

// readyTimeout bounds how long we wait for the SPDY tunnel to declare
// itself ready before giving up on this agent. Most tunnels come up in
// ~100-300ms; 10s leaves ample slack for slow networks without making a
// fan-out hang noticeably on a single dead pod.
const readyTimeout = 10 * time.Second

// Forward is one live port-forward to one (namespace, agent) pair. The
// caller hits Forward.BaseURL via a normal http.Client, then calls
// Close (or just lets the context expire) to tear the tunnel down.
type Forward struct {
	Namespace string
	Agent     string
	Pod       string
	// BaseURL is what HTTP requests should hit, e.g.
	// "http://127.0.0.1:53847". Always loopback; never reachable
	// outside this process.
	BaseURL string

	stopCh chan struct{}
}

// Close stops the port-forward and releases its local listener.
// Idempotent — safe to call multiple times or via defer alongside an
// outer context-cancel-driven teardown.
func (f *Forward) Close() {
	if f == nil || f.stopCh == nil {
		return
	}
	select {
	case <-f.stopCh:
		// already closed
	default:
		close(f.stopCh)
	}
	f.stopCh = nil
}

// Open is shorthand for OpenPort(ctx, cfg, namespace, agent, HarnessHTTPPort)
// — the most common case. Use OpenPort when you need to target a
// different container's port (e.g. the backend's BackendHTTPPort for
// `/api/sessions/<id>/stream`).
func Open(ctx context.Context, cfg *rest.Config, namespace, agent string) (*Forward, error) {
	return OpenPort(ctx, cfg, namespace, agent, HarnessHTTPPort)
}

// OpenPort starts a port-forward to the agent's pod targeting `podPort`.
// Same SPDY-tunnel mechanics as Open, but parameterized so callers can
// reach either the harness (8000) or the backend (8001) container — both
// containers share the pod's network namespace, so port-forward sees
// whichever port a container is bound to. Picks an ephemeral local port
// (`:0`) so concurrent fan-outs don't fight over a fixed-port range.
//
// The returned Forward is ready to receive HTTP traffic by the time
// OpenPort returns. If the tunnel can't be established before
// readyTimeout, returns an error and the caller should treat that agent
// as unreachable on that port.
//
// Caller MUST invoke Forward.Close (typically via defer) when done, or
// pass a cancellable context — the goroutine driving the forward
// observes ctx and shuts down when it cancels.
func OpenPort(ctx context.Context, cfg *rest.Config, namespace, agent string, podPort int) (*Forward, error) {
	k8s, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("build kubernetes client: %w", err)
	}
	pod, err := pickAgentPod(ctx, k8s, namespace, agent)
	if err != nil {
		return nil, err
	}

	// Pick a free local port via :0 listener. The OS hands us a port,
	// we read it, then close the listener and pass the port to the
	// SPDY forwarder. There IS a tiny race window where another
	// process could claim it before we re-bind, but for a single-user
	// CLI hitting localhost it's never been an issue in practice.
	localPort, err := pickFreeLocalPort()
	if err != nil {
		return nil, fmt.Errorf("pick local port: %w", err)
	}

	stopCh := make(chan struct{})
	readyCh := make(chan struct{})

	// Build the SPDY round-tripper for the api-server's pod-portforward
	// subresource. Same construction kubectl itself uses internally.
	roundTripper, upgrader, err := spdy.RoundTripperFor(cfg)
	if err != nil {
		return nil, fmt.Errorf("build SPDY round-tripper: %w", err)
	}
	pfPath := fmt.Sprintf("/api/v1/namespaces/%s/pods/%s/portforward",
		url.PathEscape(namespace), url.PathEscape(pod.Name))
	pfURL := &url.URL{
		Scheme: "https",
		Host:   strings.TrimPrefix(strings.TrimPrefix(cfg.Host, "https://"), "http://"),
		Path:   pfPath,
	}
	dialer := spdy.NewDialer(upgrader, &http.Client{Transport: roundTripper}, http.MethodPost, pfURL)

	// errBuf captures any forwarder error output so we can surface it
	// in the Open error message rather than dumping it to stderr.
	var outBuf, errBuf bytes.Buffer

	pf, err := portforward.NewOnAddresses(
		dialer,
		[]string{"127.0.0.1"},
		[]string{fmt.Sprintf("%d:%d", localPort, podPort)},
		stopCh,
		readyCh,
		&outBuf,
		&errBuf,
	)
	if err != nil {
		return nil, fmt.Errorf("create port-forwarder: %w", err)
	}

	// ForwardPorts blocks until stopCh closes; run it in a goroutine
	// so Open can return as soon as the tunnel reports ready.
	pfDone := make(chan error, 1)
	go func() {
		pfDone <- pf.ForwardPorts()
	}()

	// Wait for ready, ctx-cancel, or a fast-fail from ForwardPorts.
	select {
	case <-readyCh:
		// happy path — tunnel is up
	case <-ctx.Done():
		close(stopCh)
		return nil, ctx.Err()
	case <-time.After(readyTimeout):
		close(stopCh)
		return nil, fmt.Errorf("port-forward to %s/%s timed out after %v: %s",
			namespace, pod.Name, readyTimeout, errBuf.String())
	case err := <-pfDone:
		if err != nil {
			return nil, fmt.Errorf("port-forward to %s/%s failed: %w (%s)",
				namespace, pod.Name, err, errBuf.String())
		}
		return nil, fmt.Errorf("port-forward to %s/%s closed before ready", namespace, pod.Name)
	}

	// Once stopCh closes (via Close() or ctx cancel), drain pfDone so
	// the goroutine doesn't leak.
	go func() {
		select {
		case <-pfDone:
		case <-time.After(2 * time.Second):
			// last-resort timeout; the goroutine will eventually exit
			// when stopCh propagates
		}
	}()

	// Cancel the forward if the caller's ctx dies even without
	// explicit Close. Harmless when Close already fired (the select
	// in Close uses a default branch).
	go func() {
		<-ctx.Done()
		select {
		case <-stopCh:
		default:
			close(stopCh)
		}
	}()

	return &Forward{
		Namespace: namespace,
		Agent:     agent,
		Pod:       pod.Name,
		BaseURL:   fmt.Sprintf("http://127.0.0.1:%d", localPort),
		stopCh:    stopCh,
	}, nil
}

// pickAgentPod selects a Running pod for the agent. Prefers the
// newest-Running pod when multiple match (e.g. mid-rollout), so the
// caller hits the new replica rather than the terminating one.
func pickAgentPod(ctx context.Context, k8s kubernetes.Interface, ns, agent string) (*corev1.Pod, error) {
	sel := labels.SelectorFromSet(labels.Set{
		"app.kubernetes.io/name": agent,
	})
	list, err := k8s.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{LabelSelector: sel.String()})
	if err != nil {
		return nil, fmt.Errorf("list pods for agent %s/%s: %w", ns, agent, err)
	}
	var newest *corev1.Pod
	for i := range list.Items {
		p := &list.Items[i]
		if p.Status.Phase != corev1.PodRunning {
			continue
		}
		// Skip pods being deleted — the SPDY tunnel can flap during
		// terminating-grace and we'd rather hit the new replica.
		if p.DeletionTimestamp != nil {
			continue
		}
		if newest == nil || p.CreationTimestamp.After(newest.CreationTimestamp.Time) {
			newest = p
		}
	}
	if newest == nil {
		return nil, fmt.Errorf("no Running pod for agent %s/%s — operator hasn't reconciled yet, or the agent is mid-roll", ns, agent)
	}
	return newest, nil
}

// pickFreeLocalPort opens a listener on :0, reads back the assigned
// port, and immediately closes the listener so the SPDY forwarder can
// re-bind to it. Standard pattern for "give me an ephemeral port" in
// Go networking code.
func pickFreeLocalPort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}
