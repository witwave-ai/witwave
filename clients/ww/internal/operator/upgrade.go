package operator

import (
	"context"
	"encoding/json"
	"fmt"
	"io"

	"github.com/witwave-ai/witwave/clients/ww/internal/k8s"
	"helm.sh/helm/v3/pkg/chart"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/cli-runtime/pkg/genericclioptions"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/yaml"
)

// UpgradeOptions — inputs to the upgrade flow.
type UpgradeOptions struct {
	Namespace string
	AssumeYes bool
	DryRun    bool
	Values    map[string]interface{}
	// Force — reserved for future skew-policy overrides.
	Force bool
	Out   io.Writer
	In    io.Reader
}

// Upgrade runs:
//
//  1. Verify a release already exists (refuse otherwise — use install).
//  2. Preflight banner + confirmation.
//  3. CRD server-side apply — the two-step pattern from #1477 that
//     works around Helm's "crds/ is install-only, never updated on
//     upgrade" semantics.
//  4. helm upgrade with SkipCRDs=true.
func Upgrade(ctx context.Context, target *k8s.Target, cfg *rest.Config, flags *genericclioptions.ConfigFlags, opts UpgradeOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("UpgradeOptions.Out is required")
	}

	k8sClient, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build kubernetes client: %w", err)
	}
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build dynamic client: %w", err)
	}

	// Step 1 — release must exist.
	rel, err := LookupRelease(ctx, k8sClient, opts.Namespace, ReleaseName)
	if err != nil {
		return fmt.Errorf("look up existing release: %w", err)
	}
	if rel == nil {
		return fmt.Errorf("no %s release found in namespace %s — did you mean `ww operator install`?",
			ReleaseName, opts.Namespace)
	}

	ch, err := LoadEmbeddedChart()
	if err != nil {
		return fmt.Errorf("load embedded chart: %w", err)
	}

	// Step 2 — preflight banner.
	plan := []k8s.PlanLine{
		{Key: "Action", Value: fmt.Sprintf("upgrade %s", ReleaseName)},
		{Key: "Current", Value: fmt.Sprintf("%s (rev %d, %s)", rel.ChartVersion, rel.Revision, rel.Status)},
		{Key: "Target", Value: fmt.Sprintf("%s %s (embedded)", ch.Metadata.Name, ch.Metadata.Version)},
	}
	if len(opts.Values) > 0 {
		plan = append(plan, k8s.PlanLine{Key: "Values", Value: describeValues(opts.Values) + "; merged onto reused values"})
	}
	proceed, err := k8s.Confirm(opts.Out, opts.In, target, plan, k8s.PromptOptions{
		AssumeYes: opts.AssumeYes,
		DryRun:    opts.DryRun,
	})
	if err != nil {
		return err
	}
	if !proceed {
		return nil
	}

	// Step 3 — CRD server-side apply.
	fmt.Fprintln(opts.Out, "Applying CRDs from embedded chart (server-side apply)…")
	if err := ApplyEmbeddedCRDs(ctx, dyn, ch); err != nil {
		return fmt.Errorf("apply CRDs (pre-upgrade): %w", err)
	}

	// Step 4 — Helm upgrade with SkipCRDs=true.
	fmt.Fprintf(opts.Out, "Upgrading Helm release %s to chart %s …\n", ReleaseName, ch.Metadata.Version)
	helm, err := NewHelmClient(flags, opts.Namespace, ReleaseName, StderrLog(opts.Out))
	if err != nil {
		return fmt.Errorf("helm client: %w", err)
	}
	newRel, err := helm.Upgrade(ctx, ch, opts.Values)
	if err != nil {
		return fmt.Errorf("helm upgrade step: %w", err)
	}
	fmt.Fprintf(opts.Out, "\nUpgraded %s to revision %d (%s).\n", newRel.Name, newRel.Version, helmReleaseStatus(newRel))
	return nil
}

// ApplyEmbeddedCRDs server-side applies every CRD in the embedded
// chart's crds/ directory. Idempotent.
//
// We use the dynamic client + raw JSON to avoid taking a compile-time
// dependency on apiextensions typed clients. Helm's *chart.Chart
// already parses the CRDs for us — we just re-serialize each one and
// hand it to the apiserver via Patch(ApplyPatchType).
func ApplyEmbeddedCRDs(ctx context.Context, dyn dynamic.Interface, ch *chart.Chart) error {
	for _, obj := range ch.CRDObjects() {
		// obj.File.Data is the raw YAML from crds/<name>.yaml.
		jsonBytes, err := yaml.YAMLToJSON(obj.File.Data)
		if err != nil {
			return fmt.Errorf("yaml→json for CRD %s: %w", obj.Name, err)
		}
		var u map[string]interface{}
		if err := json.Unmarshal(jsonBytes, &u); err != nil {
			return fmt.Errorf("unmarshal CRD %s: %w", obj.Name, err)
		}
		// Extract the name for the dynamic client's Patch call.
		metaVal, _ := u["metadata"].(map[string]interface{})
		name, _ := metaVal["name"].(string)
		if name == "" {
			return fmt.Errorf("CRD %s has no metadata.name", obj.Name)
		}
		gvr := schema.GroupVersionResource{
			Group:    "apiextensions.k8s.io",
			Version:  "v1",
			Resource: "customresourcedefinitions",
		}
		force := true
		if _, err := dyn.Resource(gvr).Patch(ctx, name, types.ApplyPatchType, jsonBytes, metav1.PatchOptions{
			FieldManager: "ww-operator-cli",
			Force:        &force,
		}); err != nil {
			return fmt.Errorf("server-side apply CRD %s: %w", name, err)
		}
	}
	return nil
}
