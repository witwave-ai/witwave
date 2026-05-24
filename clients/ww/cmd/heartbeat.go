package cmd

import "github.com/spf13/cobra"

func newHeartbeatCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "heartbeat",
		Short: "Inspect or fire the heartbeat",
		Long: "Inspect the configured heartbeat schedule and recent fire metadata,\n" +
			"or fire it immediately through the harness ad-hoc run endpoint.\n" +
			"Run without a subcommand to default to `view`. Each agent has at\n" +
			"most one heartbeat configured; if none is set the command warns\n" +
			"and exits.",
	}
	cmd.AddCommand(newHeartbeatViewCmd(), newHeartbeatRunCmd())
	cmd.RunE = func(cc *cobra.Command, args []string) error { return runHeartbeatView(cc) }
	return cmd
}

func newHeartbeatViewCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "view",
		Short: "Show the configured heartbeat",
		Long: "Fetches /heartbeat from the harness and prints the full heartbeat\n" +
			"record (interval, payload, last-fire metadata). Warns if no\n" +
			"heartbeat is configured for the agent.",
		RunE: func(cc *cobra.Command, args []string) error {
			return runHeartbeatView(cc)
		},
	}
}

func newHeartbeatRunCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "run",
		Short: "Fire the configured heartbeat now",
		Long: "POSTs /heartbeat/run through the harness ad-hoc run endpoint.\n" +
			"Requires an ad-hoc run bearer token via --run-token, WW_RUN_TOKEN,\n" +
			"or profile.default.run_token. The harness accepts the request and\n" +
			"dispatches the heartbeat in the background.",
		RunE: func(cc *cobra.Command, args []string) error {
			return runHeartbeatRun(cc)
		},
	}
}

func runHeartbeatView(cc *cobra.Command) error {
	ctx := cc.Context()
	c := ClientFromCtx(ctx)
	out := OutFromCtx(ctx)
	// /heartbeat returns a flat JSON object (one heartbeat per agent),
	// not the envelope shape used by /jobs|/tasks|/triggers|/continuations.
	// Use the single-entry parser so the body decodes without rejecting
	// at the envelope-key check.
	entry, err := fetchSnapshotSingle(ctx, c, "/heartbeat")
	if err != nil {
		return handleErr(out, err)
	}
	// The harness always returns a populated object — when HEARTBEAT.md
	// is missing or disabled it returns `{"enabled": false, ...}` with
	// nulled fields, not an empty body. Treat both an empty entry and
	// an explicit `enabled: false` as "no heartbeat configured".
	if entry == nil || !heartbeatEnabled(entry) {
		out.Warnf("no heartbeat configured")
		return nil
	}
	return printView(out, entry)
}

func runHeartbeatRun(cc *cobra.Command) error {
	return runAdhocDispatch(cc, "/heartbeat/run", "heartbeat run")
}

// heartbeatEnabled returns the value of entry["enabled"] coerced to
// bool. Missing key returns true (permissive: an older harness or an
// unknown payload shape shouldn't be silently treated as disabled).
func heartbeatEnabled(entry snapshotEntry) bool {
	v, ok := entry["enabled"]
	if !ok {
		return true
	}
	b, ok := v.(bool)
	if !ok {
		return false
	}
	return b
}
