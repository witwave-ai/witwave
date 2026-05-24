package cmd

import (
	"fmt"
	"net/url"

	"github.com/spf13/cobra"
)

func newJobsCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "jobs",
		Short: "Inspect scheduled jobs",
		Long: "Fetches /jobs from the harness and lists every cron-scheduled job\n" +
			"with its schedule, next fire, last fire, and last outcome. Run\n" +
			"without a subcommand to default to `list`; use `view <name>` to\n" +
			"see the full configuration of a single job or `run <name>` to\n" +
			"fire it immediately.",
	}
	cmd.AddCommand(newJobsListCmd(), newJobsViewCmd(), newJobsRunCmd())
	// Default to list when no subcommand is given.
	cmd.RunE = func(cc *cobra.Command, args []string) error {
		return runJobsList(cc)
	}
	return cmd
}

func newJobsListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List all jobs",
		Long: "Fetches /jobs from the harness and prints one row per job with\n" +
			"NAME, SCHEDULE, NEXT_FIRE, LAST_FIRE, and OUTCOME.",
		RunE: func(cc *cobra.Command, args []string) error {
			return runJobsList(cc)
		},
	}
}

func runJobsList(cc *cobra.Command) error {
	ctx := cc.Context()
	c := ClientFromCtx(ctx)
	out := OutFromCtx(ctx)
	entries, err := fetchSnapshot(ctx, c, "/jobs")
	if err != nil {
		return handleErr(out, err)
	}
	return printList(out, entries, [][2]string{
		{"NAME", "name"},
		{"SCHEDULE", "schedule,cron"},
		{"NEXT_FIRE", "next_fire,next_run,next"},
		{"LAST_FIRE", "last_fire,last_run,last"},
		{"OUTCOME", "last_outcome,outcome"},
	})
}

func newJobsRunCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "run <name>",
		Short: "Fire a job now",
		Long: "POSTs /jobs/<name>/run through the harness ad-hoc run endpoint.\n" +
			"Requires an ad-hoc run bearer token via --run-token, WW_RUN_TOKEN,\n" +
			"or profile.default.run_token. The harness accepts the request and\n" +
			"dispatches the job in the background.",
		Args: cobra.ExactArgs(1),
		RunE: func(cc *cobra.Command, args []string) error {
			return runAdhocDispatch(cc, "/jobs/"+url.PathEscape(args[0])+"/run", "job run")
		},
	}
}

func newJobsViewCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "view <name>",
		Short: "View a single job's full details",
		Long: "Fetches /jobs from the harness and prints the full configuration\n" +
			"for the named job, including its cron schedule, last-fire metadata,\n" +
			"and any per-job flags.",
		Args: cobra.ExactArgs(1),
		RunE: func(cc *cobra.Command, args []string) error {
			ctx := cc.Context()
			c := ClientFromCtx(ctx)
			out := OutFromCtx(ctx)
			entries, err := fetchSnapshot(ctx, c, "/jobs")
			if err != nil {
				return handleErr(out, err)
			}
			e := findEntryByName(entries, args[0])
			if e == nil {
				return logicalErr(fmt.Errorf("job %q not found", args[0]))
			}
			return printView(out, e)
		},
	}
}
