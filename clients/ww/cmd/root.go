// Package cmd wires the ww CLI's cobra command tree.
package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/spf13/cobra"
	"github.com/witwave-ai/witwave/clients/ww/internal/client"
	"github.com/witwave-ai/witwave/clients/ww/internal/config"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
	"github.com/witwave-ai/witwave/clients/ww/internal/update"
)

// Version info — overwritten at build time via -ldflags.
var (
	Version   = "dev"
	Commit    = "unknown"
	BuildDate = "unknown"
)

// Globals populated by PersistentPreRunE so subcommands can reach them
// without re-parsing flags.
type contextKey struct{ name string }

var (
	ctxKeyClient = &contextKey{"client"}
	ctxKeyOut    = &contextKey{"out"}
	ctxKeyCfg    = &contextKey{"cfg"}
	ctxKeyUpdate = &contextKey{"update"}
	ctxKeyK8s    = &contextKey{"k8s"}
)

// K8sFlags carries the cluster-identity flags from the root command down
// to cluster-touching subtrees (today: `ww operator`). Per DESIGN.md KC-5,
// --kubeconfig and --context are persistent on the root command; harness
// subcommands inherit them harmlessly and ignore them. Namespace is
// deliberately absent — per KC-6 it lives on each cluster-touching subtree
// with subtree-specific defaults.
type K8sFlags struct {
	Kubeconfig string
	Context    string
}

// K8sFromCtx retrieves the root's cluster-identity flags. Returns the
// zero value when called from a command invocation that did not run
// PersistentPreRunE — callers MUST treat empty strings as "fall through
// to client-go defaults," which is the same contract as k8s.Options.
func K8sFromCtx(ctx context.Context) K8sFlags {
	k, _ := ctx.Value(ctxKeyK8s).(K8sFlags)
	return k
}

// pendingUpdateCheck is stashed in the per-command context by
// PersistentPreRunE (when the update check is enabled) and consumed by
// PersistentPostRunE after the user's command has finished. The check
// itself runs asynchronously in a goroutine so it never adds latency
// to the happy path: if the command completes before the check does,
// PostRunE waits a short additional window and then gives up silently.
type pendingUpdateCheck struct {
	mode     update.Mode
	resultCh chan *update.Notice
	cancel   context.CancelFunc
}

// ClientFromCtx retrieves the configured HTTP client or panics.
func ClientFromCtx(ctx context.Context) *client.Client {
	c, _ := ctx.Value(ctxKeyClient).(*client.Client)
	if c == nil {
		panic("no client in context")
	}
	return c
}

// OutFromCtx retrieves the configured output writer.
func OutFromCtx(ctx context.Context) *output.Writer {
	o, _ := ctx.Value(ctxKeyOut).(*output.Writer)
	if o == nil {
		panic("no output writer in context")
	}
	return o
}

// CfgFromCtx retrieves the resolved config.
func CfgFromCtx(ctx context.Context) config.Resolved {
	c, _ := ctx.Value(ctxKeyCfg).(config.Resolved)
	return c
}

// persistent flag values
type rootFlags struct {
	configPath string
	profile    string
	baseURL    string
	token      string
	runToken   string
	timeout    time.Duration
	verbose    int
	jsonOut    bool
	compact    bool
	yamlOut    bool

	// Cluster-identity flags — see DESIGN.md KC-5. Populated on every
	// invocation regardless of subcommand; consumed only by cluster-touching
	// subtrees (today: `ww operator`) via K8sFromCtx.
	kubeconfig string
	kubectx    string

	// pendingUpdate carries the result of the async update check from
	// PersistentPreRunE to Execute(). Stored on rootFlags rather than
	// the per-command context because root.Context() doesn't see the
	// context that subcommand PreRunE sets on the invoked subcommand
	// via cc.SetContext — stashing here side-steps the lookup mismatch
	// and lets Execute() consume the check regardless of which
	// subcommand ran or whether it succeeded.
	pendingUpdate *pendingUpdateCheck
}

// Execute runs the root command. Returns the intended exit code.
func Execute() int {
	root, rf := newRoot()
	root.AddCommand(newVersionCmd())
	root.AddCommand(newStatusCmd())
	root.AddCommand(newTailCmd())
	root.AddCommand(newSendCmd())
	root.AddCommand(newJobsCmd())
	root.AddCommand(newTasksCmd())
	root.AddCommand(newHeartbeatCmd())
	root.AddCommand(newTriggersCmd())
	root.AddCommand(newContinuationsCmd())
	root.AddCommand(newValidateCmd())
	root.AddCommand(newConfigCmd())
	root.AddCommand(newUpdateCmd())
	root.AddCommand(newDoctorCmd())
	root.AddCommand(newOperatorCmd())
	root.AddCommand(newAgentCmd())
	root.AddCommand(newWorkspaceCmd())
	root.AddCommand(newConversationCmd())
	root.AddCommand(newTeamCmd())
	root.AddCommand(newTuiCmd())

	err := root.Execute()
	// Finish the async update check regardless of command outcome.
	// Cobra's PersistentPostRunE is skipped when RunE fails, which
	// would hide the "you're out of date" hint in exactly the cases
	// where a stale client might be contributing to the failure.
	// Running it here ensures the banner fires whether the command
	// succeeded or failed. finishUpdateCheck is safe to call with a
	// nil pending (the version subcommand opts out of PreRunE and
	// never kicks off a check, so rf.pendingUpdate stays nil there).
	finishUpdateCheck(rf.pendingUpdate)

	if err != nil {
		// Errors already printed by runWithExit helper; fall back here
		// if cobra reports a flag parse error or similar.
		var ce *commandErr
		if ok := extract(err, &ce); ok {
			return ce.code
		}
		fmt.Fprintln(os.Stderr, "error:", err)
		return client.ExitLogical
	}
	_ = rf // quiet unused warning for release builds with small command sets
	return 0
}

func newRoot() (*cobra.Command, *rootFlags) {
	rf := &rootFlags{}
	cmd := &cobra.Command{
		Use:   "ww",
		Short: "witwave CLI — operate the Witwave agent platform",
		Long: "ww is the command-line companion for the Witwave / witwave agent platform.\n" +
			"It talks to a harness over the shared event + REST surface: tail the live\n" +
			"event stream, send A2A prompts, inspect jobs / tasks / triggers, and\n" +
			"validate scheduler files — all without a browser.",
		SilenceUsage:  true,
		SilenceErrors: true,
		PersistentPreRunE: func(cc *cobra.Command, args []string) error {
			resolved, err := config.Load(rf.configPath, config.FlagOverrides{
				Profile:  rf.profile,
				BaseURL:  rf.baseURL,
				Token:    rf.token,
				RunToken: rf.runToken,
				Timeout:  rf.timeout,
			}, os.Getenv)
			if err != nil {
				return transportErr(err)
			}
			var logger io.Writer
			if rf.verbose > 0 {
				logger = os.Stderr
			}
			hc := client.New(client.Config{
				BaseURL:  resolved.BaseURL,
				Token:    resolved.Token,
				RunToken: resolved.RunToken,
				Timeout:  resolved.Timeout,
				Verbose:  rf.verbose,
				Logger:   logger,
			})
			out := output.New(os.Stdout, os.Stderr, rf.jsonOut, rf.compact, rf.yamlOut)
			if rf.verbose > 0 {
				resolved.Dump(os.Stderr)
			}
			ctx := cc.Context()
			if ctx == nil {
				ctx = context.Background()
			}
			ctx = context.WithValue(ctx, ctxKeyClient, hc)
			ctx = context.WithValue(ctx, ctxKeyOut, out)
			ctx = context.WithValue(ctx, ctxKeyCfg, resolved)
			ctx = context.WithValue(ctx, ctxKeyK8s, K8sFlags{
				Kubeconfig: rf.kubeconfig,
				Context:    rf.kubectx,
			})

			// Kick off the version check asynchronously so the user's
			// command never waits on it. Execute() consumes the result
			// with a short deadline after root.Execute() returns.
			// Stashed on rootFlags (not ctx) so Execute() can reach it
			// regardless of whether the subcommand succeeded or failed.
			rf.pendingUpdate = startUpdateCheck(resolved)

			cc.SetContext(ctx)
			return nil
		},
		// PersistentPostRunE is intentionally NOT set here: cobra skips
		// it on command failure, and we want the "newer version
		// available" banner to fire whether the command succeeded or
		// failed. Execute() calls finishUpdateCheck() after
		// root.Execute() returns, covering both paths uniformly.
	}
	p := cmd.PersistentFlags()
	p.StringVar(&rf.configPath, "config", "", "path to ww config (default: $XDG_CONFIG_HOME/ww/config.toml)")
	p.StringVar(&rf.profile, "profile", "", "config profile to use (default: default)")
	p.StringVar(&rf.baseURL, "base-url", "", "harness base URL (overrides config)")
	p.StringVar(&rf.token, "token", "", "bearer token for conversations/events endpoints")
	p.StringVar(&rf.runToken, "run-token", "", "bearer token for ad-hoc run endpoints")
	p.DurationVar(&rf.timeout, "timeout", 0, "per-request timeout (stream commands ignore)")
	p.CountVarP(&rf.verbose, "verbose", "v", "increase verbosity (-v: requests, -vv: bodies)")
	p.BoolVar(&rf.jsonOut, "json", false, "emit JSON (pretty for snapshots, line-delimited for streams)")
	p.BoolVar(&rf.compact, "compact", false, "with --json, emit compact single-line JSON for snapshots")
	p.BoolVar(&rf.yamlOut, "yaml", false, "emit YAML for snapshot commands (#1707)")
	// Cluster-identity flags (DESIGN.md KC-5). Harmless on harness-only
	// subcommands; consumed by cluster-touching subtrees such as `ww operator`.
	p.StringVar(&rf.kubeconfig, "kubeconfig", "",
		"path to kubeconfig (overrides KUBECONFIG env var and ~/.kube/config)")
	p.StringVar(&rf.kubectx, "context", "",
		"kubeconfig context to use (defaults to current-context)")
	return cmd, rf
}

// commandErr is a sentinel returned by subcommand RunE when we want to
// carry a specific exit code up through cobra.
type commandErr struct {
	code int
	msg  string
}

func (e *commandErr) Error() string { return e.msg }

// extract returns true if err is (or wraps) *commandErr and stashes it.
// #1552: delegate to errors.As so multi-error wrappers (errors.Join,
// any custom multi-unwrap) are traversed correctly — the previous
// manual single-Unwrap loop missed *commandErr values buried inside a
// joined error tree and returned wrong exit codes.
func extract(err error, out **commandErr) bool {
	return errors.As(err, out)
}

func transportErr(err error) error {
	return &commandErr{code: client.ExitTransport, msg: err.Error()}
}

func logicalErr(err error) error {
	return &commandErr{code: client.ExitLogical, msg: err.Error()}
}

// handleErr renders err via out and returns a wrapped commandErr cobra
// will propagate to Execute. Transport errors (network, timeout) get
// exit 2; HTTP 4xx and logic errors get exit 1.
func handleErr(out *output.Writer, err error) error {
	if err == nil {
		return nil
	}
	if he, ok := client.IsHTTPError(err); ok {
		out.Errorf("%s", he.Error())
		if he.StatusCode >= 500 {
			return transportErr(err)
		}
		return logicalErr(err)
	}
	out.Errorf("%v", err)
	return transportErr(err)
}

// startUpdateCheck parses the [update] config, applies runtime
// guardrails (CI, non-TTY, WW_NO_UPDATE_CHECK), and if still enabled
// kicks off an asynchronous GitHub Releases API check. Returns nil
// when the check is disabled — callers MUST treat nil as "nothing to
// consume in PostRunE" without panicking.
//
// Running async keeps the version check off the happy path: even on a
// cache miss (once per 24h per user) the user's command starts
// executing immediately while the HTTP request is in flight. PostRunE
// collects the result with a short wait-budget and silently gives up
// if the check hasn't finished.
//
// Config-parse errors (e.g. "mode = wubwub" in config.toml) degrade
// to a disabled check rather than failing the command — we warn once
// on stderr so the misconfiguration is visible but do not block.
func startUpdateCheck(resolved config.Resolved) *pendingUpdateCheck {
	mode, err := update.ParseMode(resolved.Update.Mode)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ww: warning: %v (update check disabled)\n", err)
		return nil
	}
	mode = update.EffectiveMode(mode, os.Getenv, nil)
	if mode == update.ModeOff {
		return nil
	}

	channel, err := update.ParseChannel(resolved.Update.Channel)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ww: warning: %v (update check disabled)\n", err)
		return nil
	}

	var interval time.Duration
	if s := resolved.Update.Interval; s != "" {
		if d, err := time.ParseDuration(s); err == nil && d > 0 {
			interval = d
		}
		// Bad interval value silently falls through to the package default.
	}

	checker := update.NewChecker(Version, channel, interval)
	resultCh := make(chan *update.Notice, 1)
	// Independent context so the check survives its own deadline rather
	// than being tied to the user's command deadline. 3s is generous
	// vs the HTTPClient's 2s timeout — leaves room for DNS + TLS setup.
	checkCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	go func() {
		defer cancel()
		resultCh <- checker.Check(checkCtx)
	}()

	return &pendingUpdateCheck{
		mode:     mode,
		resultCh: resultCh,
		cancel:   cancel,
	}
}

// finishUpdateCheck waits briefly for the async check to complete, then
// (if a notice was produced) dispatches to the update package's Notify
// to print the banner and optionally run the upgrade delegate. Safe to
// call with a nil pendingUpdateCheck (e.g. when the check was disabled
// or the subcommand opted out of PersistentPreRunE).
func finishUpdateCheck(pending *pendingUpdateCheck) {
	if pending == nil {
		return
	}
	// Short grace window after the command completed. The check
	// usually finishes in a few hundred ms (the HTTP timeout is 2s);
	// we give it a touch longer so a fresh cache write can finish too.
	var notice *update.Notice
	select {
	case notice = <-pending.resultCh:
	case <-time.After(750 * time.Millisecond):
		// Check still running; cancel it and give up. The user's
		// command already completed — we don't delay their prompt.
		pending.cancel()
		return
	}
	if notice == nil {
		return
	}
	method := update.DetectInstallMethod(nil, nil, nil)
	// Notify errors are deliberately swallowed: the version-check
	// system must not turn a successful command into a failed one.
	// Independent context so a cancelled command context doesn't
	// abort the brew-upgrade shell-out.
	_ = update.Notify(context.Background(), pending.mode, notice, method, os.Stdout, os.Stderr, os.Stdin)
}
