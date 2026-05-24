package cmd

import (
	"net/http"

	"github.com/spf13/cobra"
)

func runAdhocDispatch(cc *cobra.Command, path string, label string) error {
	ctx := cc.Context()
	c := ClientFromCtx(ctx)
	out := OutFromCtx(ctx)

	entry := snapshotEntry{}
	if err := c.DoJSON(ctx, http.MethodPost, path, nil, &entry, true); err != nil {
		return handleErr(out, err)
	}
	if len(entry) == 0 {
		out.Warnf("%s accepted but the harness returned an empty response", label)
		return nil
	}
	return printView(out, entry)
}
