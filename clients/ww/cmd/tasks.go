package cmd

import (
	"fmt"
	"net/url"

	"github.com/spf13/cobra"
)

func newTasksCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "tasks",
		Short: "Inspect scheduled tasks",
		Long: "Fetches /tasks from the harness and lists every time-window task\n" +
			"with its window, days, next fire, last fire, and last outcome.\n" +
			"Run without a subcommand to default to `list`; use `view <name>`\n" +
			"to see the full configuration of a single task or `run <name>`\n" +
			"to fire it immediately.",
	}
	cmd.AddCommand(newTasksListCmd(), newTasksViewCmd(), newTasksRunCmd())
	cmd.RunE = func(cc *cobra.Command, args []string) error { return runTasksList(cc) }
	return cmd
}

func newTasksListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List all tasks",
		Long: "Fetches /tasks from the harness and prints one row per task with\n" +
			"NAME, WINDOW, DAYS, NEXT_FIRE, LAST_FIRE, and OUTCOME.",
		RunE: func(cc *cobra.Command, args []string) error {
			return runTasksList(cc)
		},
	}
}

func runTasksList(cc *cobra.Command) error {
	ctx := cc.Context()
	c := ClientFromCtx(ctx)
	out := OutFromCtx(ctx)
	entries, err := fetchSnapshot(ctx, c, "/tasks")
	if err != nil {
		return handleErr(out, err)
	}
	return printList(out, entries, [][2]string{
		{"NAME", "name"},
		{"WINDOW", "window,time,hours"},
		{"DAYS", "days"},
		{"NEXT_FIRE", "next_fire,next_run,next"},
		{"LAST_FIRE", "last_fire,last_run,last"},
		{"OUTCOME", "last_outcome,outcome"},
	})
}

func newTasksRunCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "run <name>",
		Short: "Fire a task now",
		Long: "POSTs /tasks/<name>/run through the harness ad-hoc run endpoint.\n" +
			"Requires an ad-hoc run bearer token via --run-token, WW_RUN_TOKEN,\n" +
			"or profile.default.run_token. The harness accepts the request and\n" +
			"dispatches the task in the background.",
		Args: cobra.ExactArgs(1),
		RunE: func(cc *cobra.Command, args []string) error {
			return runAdhocDispatch(cc, "/tasks/"+url.PathEscape(args[0])+"/run", "task run")
		},
	}
}

func newTasksViewCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "view <name>",
		Short: "View a single task's full details",
		Long: "Fetches /tasks from the harness and prints the full configuration\n" +
			"for the named task, including its window/days schedule, last-fire\n" +
			"metadata, and any per-task flags.",
		Args: cobra.ExactArgs(1),
		RunE: func(cc *cobra.Command, args []string) error {
			ctx := cc.Context()
			c := ClientFromCtx(ctx)
			out := OutFromCtx(ctx)
			entries, err := fetchSnapshot(ctx, c, "/tasks")
			if err != nil {
				return handleErr(out, err)
			}
			e := findEntryByName(entries, args[0])
			if e == nil {
				return logicalErr(fmt.Errorf("task %q not found", args[0]))
			}
			return printView(out, e)
		},
	}
}
