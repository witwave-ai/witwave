package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

// Exit codes aligned with README contract.
const (
	ExitOK        = 0
	ExitLogical   = 1
	ExitTransport = 2
)

// Config holds everything the HTTP client needs to talk to a harness
// (or a direct agent URL).
type Config struct {
	BaseURL    string        // e.g. http://localhost:8000
	Token      string        // CONVERSATIONS_AUTH_TOKEN
	RunToken   string        // ADHOC_RUN_AUTH_TOKEN (optional)
	Timeout    time.Duration // non-stream request timeout
	Verbose    int           // 0 silent, 1 line, 2 dump
	UserAgent  string
	HTTPClient *http.Client // defaults to a fresh client with Timeout
	// Logger receives verbose traces. Defaults to stderr-bound writer
	// when nil.
	Logger io.Writer
}

// Client is a thin HTTP helper with bearer-token auth, retries on 5xx /
// transport errors, and streaming helpers for SSE endpoints.
//
// #1733: stream callers use a sibling client (`streamHC`) whose
// Timeout is zero so a long-lived SSE connection isn't severed every
// cfg.Timeout window. Cancellation comes from the caller's
// context.Context exclusively. Snapshot callers stay on `hc` with
// the configured Timeout so a wedged DoJSON / GetBytes still bounds
// at the per-request budget.
type Client struct {
	cfg      Config
	hc       *http.Client
	streamHC *http.Client
	// runTokenFallbackWarned gates the one-shot warning emitted when a
	// caller asks for RunToken but the client only has Token configured.
	// #1547 — the fallback is an accepted safety net, but silently
	// reusing Token hides auth-split misconfig in CI logs.
	runTokenFallbackWarned sync.Once
}

// New constructs a Client from cfg. Mutations to cfg after New have no
// effect on the returned instance.
func New(cfg Config) *Client {
	if cfg.UserAgent == "" {
		cfg.UserAgent = "ww/0.1"
	}
	if cfg.Timeout == 0 {
		cfg.Timeout = 30 * time.Second
	}
	hc := cfg.HTTPClient
	if hc == nil {
		hc = &http.Client{Timeout: cfg.Timeout}
	}
	// Stream client (#1733). When the caller passes their own
	// HTTPClient we honour it as-is for both paths; otherwise we
	// build a sibling client with Timeout: 0 so streams aren't
	// scythed on the cfg.Timeout boundary. Reuse the snapshot
	// client's Transport so the connection pool / TLS config /
	// proxy resolution stay shared — only the whole-request
	// Timeout differs between the two surfaces.
	streamHC := cfg.HTTPClient
	if streamHC == nil {
		streamHC = &http.Client{Transport: hc.Transport, Jar: hc.Jar}
	}
	return &Client{cfg: cfg, hc: hc, streamHC: streamHC}
}

// BaseURL returns the configured base URL.
func (c *Client) BaseURL() string { return c.cfg.BaseURL }

// Token returns the conversations/session bearer token.
func (c *Client) Token() string { return c.cfg.Token }

// RunToken returns the ad-hoc run bearer token (may be empty).
func (c *Client) RunToken() string { return c.cfg.RunToken }

// PreferredToken returns the token callers should use for A2A / validate
// / other non-ad-hoc-run HTTP calls. This is always the conversations
// token (Token) — RunToken is reserved for the dedicated ad-hoc-run
// endpoints.
func (c *Client) PreferredToken() string { return c.cfg.Token }

// Verbose returns the verbosity level (0/1/2).
func (c *Client) Verbose() int { return c.cfg.Verbose }

// Logger returns the configured logger (nil allowed).
func (c *Client) Logger() io.Writer { return c.cfg.Logger }

// HTTP returns the underlying http.Client so stream helpers can reuse
// the same connection pool.
func (c *Client) HTTP() *http.Client { return c.hc }

// Resolve joins a path onto the configured base URL. If path already
// has a scheme, path is returned as-is (enables targeting a direct
// agent URL pulled from /agents).
func (c *Client) Resolve(path string) (string, error) {
	if strings.HasPrefix(path, "http://") || strings.HasPrefix(path, "https://") {
		return path, nil
	}
	if c.cfg.BaseURL == "" {
		return "", errors.New("base URL is empty; set WW_BASE_URL or --base-url")
	}
	base, err := url.Parse(c.cfg.BaseURL)
	if err != nil {
		return "", fmt.Errorf("invalid base URL %q: %w", c.cfg.BaseURL, err)
	}
	u, err := base.Parse(path)
	if err != nil {
		return "", fmt.Errorf("invalid path %q: %w", path, err)
	}
	return u.String(), nil
}

// DoJSON issues a request with JSON body (nil → no body) and decodes
// the response into out (if non-nil). Retries on 5xx / transport errors
// up to 3 attempts. 4xx is terminal.
func (c *Client) DoJSON(ctx context.Context, method, path string, body, out any, useRunToken bool) error {
	u, err := c.Resolve(path)
	if err != nil {
		return err
	}
	var buf []byte
	if body != nil {
		buf, err = json.Marshal(body)
		if err != nil {
			return fmt.Errorf("encode body: %w", err)
		}
	}

	var lastErr error
	backoff := 200 * time.Millisecond
	// Only retry idempotent methods. Non-GET requests get a single
	// attempt — retrying POST/PUT/DELETE can cause duplicate side
	// effects on the server.
	maxAttempts := 3
	if method != http.MethodGet {
		maxAttempts = 1
	}
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		req, err := http.NewRequestWithContext(ctx, method, u, bytesReader(buf))
		if err != nil {
			return err
		}
		c.applyHeaders(req, useRunToken, buf != nil)
		if c.cfg.Verbose >= 1 {
			c.logLine(fmt.Sprintf("--> %s %s", method, u))
		}
		if c.cfg.Verbose >= 2 && len(buf) > 0 {
			c.logBody(">>", buf)
		}

		resp, err := c.hc.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("transport: %w", err)
			if attempt < maxAttempts && !errors.Is(err, context.Canceled) && !errors.Is(err, context.DeadlineExceeded) {
				c.sleep(ctx, backoff)
				backoff *= 2
				continue
			}
			return lastErr
		}
		// #1384: cap response body so a misconfigured harness returning
		// 500 + 100MB body doesn't blow CLI memory per retry attempt.
		const respBodyCap = 4 * 1024 * 1024 // 4 MiB
		respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, respBodyCap+1))
		_ = resp.Body.Close()
		if int64(len(respBody)) > respBodyCap {
			respBody = respBody[:respBodyCap]
			if c.cfg.Verbose >= 1 {
				c.logLine(fmt.Sprintf("<-- response body truncated at %d bytes", respBodyCap))
			}
		}
		if c.cfg.Verbose >= 1 {
			c.logLine(fmt.Sprintf("<-- %d %s", resp.StatusCode, resp.Status))
		}
		if c.cfg.Verbose >= 2 && len(respBody) > 0 {
			c.logBody("<<", respBody)
		}
		if readErr != nil {
			lastErr = fmt.Errorf("read body: %w", readErr)
			if attempt < maxAttempts {
				c.sleep(ctx, backoff)
				backoff *= 2
				continue
			}
			return lastErr
		}

		if resp.StatusCode >= 500 {
			lastErr = &HTTPError{StatusCode: resp.StatusCode, Status: resp.Status, Body: string(respBody), Method: method, URL: u}
			if attempt < maxAttempts {
				c.sleep(ctx, backoff)
				backoff *= 2
				continue
			}
			return lastErr
		}
		if resp.StatusCode >= 400 {
			return &HTTPError{StatusCode: resp.StatusCode, Status: resp.Status, Body: string(respBody), Method: method, URL: u}
		}

		if out != nil && len(respBody) > 0 {
			if err := json.Unmarshal(respBody, out); err != nil {
				return fmt.Errorf("decode response: %w (body: %s)", err, truncate(string(respBody), 512))
			}
		}
		return nil
	}
	return lastErr
}

// GetBytes does a GET and returns the raw response body on 2xx.
func (c *Client) GetBytes(ctx context.Context, path string) ([]byte, error) {
	u, err := c.Resolve(path)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	c.applyHeaders(req, false, false)
	if c.cfg.Verbose >= 1 {
		c.logLine(fmt.Sprintf("--> GET %s", u))
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("transport: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body: %w", err)
	}
	if c.cfg.Verbose >= 1 {
		c.logLine(fmt.Sprintf("<-- %d %s", resp.StatusCode, resp.Status))
	}
	if resp.StatusCode >= 400 {
		return body, &HTTPError{StatusCode: resp.StatusCode, Status: resp.Status, Body: string(body), Method: "GET", URL: u}
	}
	return body, nil
}

// OpenStream issues a request and hands back the response body as an
// io.ReadCloser. Used by SSE / streaming POST paths. Stream callers are
// responsible for closing the body.
//
// #1733: stream calls go through a dedicated http.Client whose Timeout
// is zero (built in New). Cancellation comes from the caller's
// context.Context — pass a long-lived context (no deadline) for long
// streams. The flag help text on --timeout claims this; before #1733
// it was wrong because the same hc.Timeout that bounded DoJSON /
// GetBytes was tearing SSE bodies down at the same boundary.
func (c *Client) OpenStream(ctx context.Context, method, path string, body any, extraHeaders http.Header, useRunToken bool) (*http.Response, error) {
	u, err := c.Resolve(path)
	if err != nil {
		return nil, err
	}
	var buf []byte
	if body != nil {
		buf, err = json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("encode body: %w", err)
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, u, bytesReader(buf))
	if err != nil {
		return nil, err
	}
	c.applyHeaders(req, useRunToken, buf != nil)
	req.Header.Set("Accept", "text/event-stream")
	for k, vs := range extraHeaders {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	if c.cfg.Verbose >= 1 {
		c.logLine(fmt.Sprintf("--> %s %s [stream]", method, u))
	}
	// #1733: streamHC has Timeout:0 so a long-lived SSE connection
	// isn't severed when cfg.Timeout elapses. Cancellation comes
	// from req.Context() (the caller's context) exclusively.
	resp, err := c.streamHC.Do(req)
	if err != nil {
		return nil, fmt.Errorf("transport: %w", err)
	}
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		return nil, &HTTPError{StatusCode: resp.StatusCode, Status: resp.Status, Body: string(body), Method: method, URL: u}
	}
	return resp, nil
}

func (c *Client) applyHeaders(req *http.Request, useRunToken, hasBody bool) {
	tok := c.cfg.Token
	if useRunToken {
		if c.cfg.RunToken != "" {
			tok = c.cfg.RunToken
		} else if c.cfg.Token != "" {
			// Fallback is accepted (some deployments share a single
			// token across conversations + ad-hoc-run) but should not
			// be silent — log exactly once per Client so CI output
			// surfaces the misconfig without spamming the tail.
			c.runTokenFallbackWarned.Do(func() {
				w := c.cfg.Logger
				if w == nil {
					w = os.Stderr
				}
				fmt.Fprintln(w, "ww: warning: ADHOC_RUN_AUTH_TOKEN is unset; falling back to CONVERSATIONS_AUTH_TOKEN for ad-hoc-run calls")
			})
		}
	}
	if tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	if useRunToken {
		req.Header.Set("X-Ad-Hoc-Run", "1")
	}
	req.Header.Set("User-Agent", c.cfg.UserAgent)
	req.Header.Set("Accept", "application/json")
	if hasBody {
		req.Header.Set("Content-Type", "application/json")
	}
}

func (c *Client) sleep(ctx context.Context, d time.Duration) {
	if d <= 0 {
		return
	}
	// Add +/- 25% jitter so stampedes don't synchronise. Guard against
	// rand.Int63n panicking on tiny durations where d/2 rounds to 0.
	half := d / 2
	var jitter time.Duration
	if half > 0 {
		jitter = time.Duration(rand.Int63n(int64(half)))
	}
	wait := half + jitter
	if wait <= 0 {
		wait = d
	}
	t := time.NewTimer(wait)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}

func (c *Client) logLine(s string) {
	if c.cfg.Logger != nil {
		fmt.Fprintln(c.cfg.Logger, s)
	}
}

func (c *Client) logBody(prefix string, body []byte) {
	if c.cfg.Logger == nil {
		return
	}
	const cap = 4096
	b := body
	truncated := false
	if len(b) > cap {
		b = b[:cap]
		truncated = true
	}
	fmt.Fprintf(c.cfg.Logger, "%s %s", prefix, string(b))
	if truncated {
		fmt.Fprintf(c.cfg.Logger, "… [truncated %d bytes]", len(body)-cap)
	}
	fmt.Fprintln(c.cfg.Logger)
}

// HTTPError wraps a non-2xx HTTP response.
type HTTPError struct {
	Method     string
	URL        string
	StatusCode int
	Status     string
	Body       string
}

// Error formats the HTTPError as "<METHOD> <URL>: <status>" with an
// optional " (<body>)" suffix when the response body was non-empty.
// The body segment is passed through truncate() with a 256-byte cap so
// a pathological upstream payload doesn't blow up log lines that
// embed the formatted error.
func (e *HTTPError) Error() string {
	body := truncate(e.Body, 256)
	return fmt.Sprintf("%s %s: %s%s", e.Method, e.URL, e.Status, func() string {
		if body == "" {
			return ""
		}
		return " (" + body + ")"
	}())
}

// IsHTTPError reports whether err (or any wrapped error) is an HTTPError
// and returns it if so.
func IsHTTPError(err error) (*HTTPError, bool) {
	var he *HTTPError
	if errors.As(err, &he) {
		return he, true
	}
	return nil, false
}

func bytesReader(b []byte) io.Reader {
	if len(b) == 0 {
		return nil
	}
	return bytes.NewReader(b)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
