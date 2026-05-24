package cmd

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

type validateFlags struct {
	kind string
}

func newValidateCmd() *cobra.Command {
	vf := &validateFlags{}
	cmd := &cobra.Command{
		Use:   "validate <file>",
		Short: "Validate a job/task/trigger/continuation/heartbeat markdown file",
		Long: "POSTs the given markdown file to the harness /validate endpoint and\n" +
			"prints the verdict. Kind (job|task|trigger|continuation|heartbeat|\n" +
			"webhook) is inferred from the parent directory name; pass --kind\n" +
			"to override. The ad-hoc run bearer token is used for auth. Human\n" +
			"output prints OK for a clean result and details for validation\n" +
			"failures; JSON/YAML modes emit the full harness response.",
		Args: cobra.ExactArgs(1),
		RunE: func(cc *cobra.Command, args []string) error {
			ctx := cc.Context()
			c := ClientFromCtx(ctx)
			out := OutFromCtx(ctx)

			path := args[0]
			body, err := os.ReadFile(path)
			if err != nil {
				return logicalErr(err)
			}
			kind := vf.kind
			if kind == "" {
				kind = inferKind(path)
			}
			if kind == "" {
				out.Warnf("could not infer kind from path; pass --kind to be explicit")
			}

			resp := snapshotEntry{}
			payload := map[string]string{
				"kind":    kind,
				"content": string(body),
			}
			if err := c.DoJSON(ctx, http.MethodPost, "/validate", payload, &resp, true); err != nil {
				return handleErr(out, err)
			}
			if out.IsJSON() || out.IsYAML() {
				return printView(out, resp)
			}
			if validationOK(resp) {
				fmt.Fprintln(out.Out, "OK")
				return nil
			}
			return printView(out, resp)
		},
	}
	cmd.Flags().StringVar(&vf.kind, "kind", "", "file kind (job|task|trigger|continuation|heartbeat|webhook)")
	return cmd
}

func inferKind(path string) string {
	parent := strings.ToLower(filepath.Base(filepath.Dir(path)))
	switch parent {
	case "jobs":
		return "job"
	case "tasks":
		return "task"
	case "triggers":
		return "trigger"
	case "continuations":
		return "continuation"
	case "webhooks":
		return "webhook"
	}
	base := strings.ToLower(filepath.Base(path))
	if base == "heartbeat.md" {
		return "heartbeat"
	}
	return ""
}

func validationOK(resp snapshotEntry) bool {
	ok, _ := resp["ok"].(bool)
	if !ok {
		return false
	}
	if errors, _ := resp["errors"].([]any); len(errors) > 0 {
		return false
	}
	return true
}
