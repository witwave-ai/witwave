package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"text/tabwriter"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"
)

// ListOptions controls which WitwaveAgent CRs are returned.
//
// AllNamespaces is the default shape for `ww agent list` (see DESIGN.md
// NS-3 — list spans the cluster unless the user explicitly narrows it).
// Setting Namespace scopes to that namespace; leaving both empty +
// AllNamespaces=false is a caller bug and produces a namespace-less
// list call (which the apiserver rejects).
//
// JSON switches the renderer from the human table to a machine-readable
// array (stable field names; includes per-container image versions). The
// upgrade-orchestration path and observer skills read this instead of
// shelling out to `kubectl get wwa -o jsonpath`.
type ListOptions struct {
	Namespace     string
	AllNamespaces bool
	JSON          bool
	Out           io.Writer
}

// AgentImage is the repository / tag / digest of one container image
// (the harness, or a backend). Mirrors the CRD's ImageSpec shape so
// callers can read the running version without re-parsing the raw CR.
type AgentImage struct {
	Repository string `json:"repository,omitempty"`
	Tag        string `json:"tag,omitempty"`
	Digest     string `json:"digest,omitempty"`
}

// Ref renders the image as repository:tag (or repository@digest when
// pinned by digest, or just repository). Empty repository renders "-".
func (i AgentImage) Ref() string {
	switch {
	case i.Repository == "":
		return "-"
	case i.Digest != "":
		return i.Repository + "@" + i.Digest
	case i.Tag != "":
		return i.Repository + ":" + i.Tag
	default:
		return i.Repository
	}
}

// Version is the human-facing version of an image: its tag, or the
// digest when pinned by digest, or "-" when neither is set. This is the
// value the VERSION column shows and the upgrade path compares against.
func (i AgentImage) Version() string {
	switch {
	case i.Tag != "":
		return i.Tag
	case i.Digest != "":
		return i.Digest
	default:
		return "-"
	}
}

// BackendSummary pairs a backend name with its image.
type BackendSummary struct {
	Name  string     `json:"name"`
	Image AgentImage `json:"image"`
}

// AgentSummary is a render-ready view of one WitwaveAgent. Flat enough
// for a tview.Table cell or a tabwriter row; retains a pointer to the
// raw unstructured object so callers that need more fields (TUI drill-
// down, JSON emitters) don't have to re-fetch.
type AgentSummary struct {
	Namespace string
	Name      string
	// Team is the value of the witwave.ai/team label, or empty string
	// when the agent is ungrouped (lands in the namespace-wide manifest).
	Team string
	// Phase is .status.phase or "Pending" when the CR hasn't been
	// reconciled yet.
	Phase string
	// Ready is .status.readyReplicas (0 when unset).
	Ready int64
	// Disabled is true when spec.enabled is explicitly false. Disabled
	// agents may retain stale status from the last active Deployment.
	Disabled bool
	// Backends is the ordered list of spec.backends[*].name.
	Backends []string
	// Harness is the harness container image (spec.image). Its tag is the
	// agent's canonical version shown in the VERSION column.
	Harness AgentImage
	// BackendImages pairs each backend name with its image
	// (spec.backends[*].{name,image}). Parallel to Backends — carried
	// separately so the text table can stay names-only while --json and
	// the upgrade path see every container's version.
	BackendImages []BackendSummary
	// Created is the CR's creation timestamp, raw. Callers format it.
	Created time.Time
	// Raw is the underlying CR so drill-down views can render extra
	// fields without a second round-trip. Callers MUST NOT mutate.
	Raw *unstructured.Unstructured
}

// ListAgents returns summaries for the agents in scope. Shared data
// path between `ww agent list` (CLI) and the TUI — both format the
// same shape, neither re-derives status fields. Namespace/AllNamespaces
// resolution matches the List wrapper below.
func ListAgents(ctx context.Context, cfg *rest.Config, opts ListOptions) ([]AgentSummary, error) {
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("build dynamic client: %w", err)
	}
	var items *unstructured.UnstructuredList
	if opts.AllNamespaces {
		items, err = dyn.Resource(GVR()).List(ctx, metav1.ListOptions{})
	} else {
		items, err = dyn.Resource(GVR()).Namespace(opts.Namespace).List(ctx, metav1.ListOptions{})
	}
	if err != nil {
		return nil, fmt.Errorf("list agents: %w", err)
	}
	out := make([]AgentSummary, 0, len(items.Items))
	for i := range items.Items {
		cr := &items.Items[i]
		out = append(out, agentSummary(cr))
	}
	return out, nil
}

func agentSummary(cr *unstructured.Unstructured) AgentSummary {
	s := AgentSummary{
		Namespace: cr.GetNamespace(),
		Name:      cr.GetName(),
		Team:      cr.GetLabels()[TeamLabel],
		Phase:     readPhase(cr),
		Created:   cr.GetCreationTimestamp().Time,
		Harness:   imageFromCR(cr, "spec", "image"),
		Raw:       cr,
	}
	if s.Phase == "" {
		s.Phase = "Pending"
	}
	if v, found, err := unstructured.NestedInt64(cr.Object, "status", "readyReplicas"); err == nil && found {
		s.Ready = v
	}
	if enabled, found, err := unstructured.NestedBool(cr.Object, "spec", "enabled"); err == nil && found && !enabled {
		s.Disabled = true
	}
	if backends, found, err := unstructured.NestedSlice(cr.Object, "spec", "backends"); err == nil && found {
		for _, b := range backends {
			m, ok := b.(map[string]any)
			if !ok {
				continue
			}
			n, _ := m["name"].(string)
			if n != "" {
				s.Backends = append(s.Backends, n)
			}
			s.BackendImages = append(s.BackendImages, BackendSummary{
				Name:  n,
				Image: imageFromMap(m["image"]),
			})
		}
	}
	return s
}

// imageFromCR reads an ImageSpec ({repository, tag, digest}) at the given
// field path in the CR into an AgentImage. Missing path → zero value.
func imageFromCR(cr *unstructured.Unstructured, fields ...string) AgentImage {
	m, found, err := unstructured.NestedMap(cr.Object, fields...)
	if err != nil || !found {
		return AgentImage{}
	}
	return imageFromMap(m)
}

// imageFromMap reads an ImageSpec from an already-extracted map value
// (e.g. a backend's "image" subfield). Non-map input → zero value.
func imageFromMap(v any) AgentImage {
	m, ok := v.(map[string]any)
	if !ok {
		return AgentImage{}
	}
	repo, _ := m["repository"].(string)
	tag, _ := m["tag"].(string)
	digest, _ := m["digest"].(string)
	return AgentImage{Repository: repo, Tag: tag, Digest: digest}
}

// List renders a table of WitwaveAgent CRs to opts.Out, or a JSON array
// when opts.JSON is set. Columns match the CRD's additionalPrinterColumns
// plus a VERSION column (harness tag) so operators see at a glance which
// release each agent is on. Thin formatter over ListAgents so the TUI
// shares the same data path.
func List(ctx context.Context, cfg *rest.Config, opts ListOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("ListOptions.Out is required")
	}
	summaries, err := ListAgents(ctx, cfg, opts)
	if err != nil {
		return err
	}
	if opts.JSON {
		return renderListJSON(opts.Out, summaries)
	}
	if len(summaries) == 0 {
		if opts.AllNamespaces {
			fmt.Fprintln(opts.Out, "No WitwaveAgents found in any namespace.")
		} else {
			fmt.Fprintf(opts.Out, "No WitwaveAgents found in namespace %q.\n", opts.Namespace)
		}
		return nil
	}
	return renderList(opts.Out, summaries)
}

func renderList(out io.Writer, summaries []AgentSummary) error {
	tw := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(tw, "NAMESPACE\tNAME\tENABLED\tPHASE\tREADY\tVERSION\tBACKENDS\tAGE")
	for _, s := range summaries {
		backends := strings.Join(s.Backends, ",")
		if backends == "" {
			backends = "-"
		}
		fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%d\t%s\t%s\t%s\n",
			s.Namespace, s.Name, enabledDisplay(s), s.Phase, s.Ready, s.Harness.Version(), backends, FormatAge(s.Created),
		)
	}
	return tw.Flush()
}

// AgentJSON is the machine-readable view emitted by `ww agent list
// --json`. Stable field names — scripts and agent skills depend on
// them (e.g. `ww agent list -n witwave-self --json | jq -r '.[].name'`,
// or `.harness.tag` / `.backends[].image.tag` for version reads).
type AgentJSON struct {
	Namespace string           `json:"namespace"`
	Name      string           `json:"name"`
	Team      string           `json:"team,omitempty"`
	Enabled   bool             `json:"enabled"`
	Phase     string           `json:"phase"`
	Ready     int64            `json:"ready"`
	Age       string           `json:"age"`
	Harness   AgentImage       `json:"harness"`
	Backends  []BackendSummary `json:"backends"`
}

func (s AgentSummary) toJSON() AgentJSON {
	backends := s.BackendImages
	if backends == nil {
		backends = []BackendSummary{}
	}
	return AgentJSON{
		Namespace: s.Namespace,
		Name:      s.Name,
		Team:      s.Team,
		Enabled:   !s.Disabled,
		Phase:     s.Phase,
		Ready:     s.Ready,
		Age:       FormatAge(s.Created),
		Harness:   s.Harness,
		Backends:  backends,
	}
}

func renderListJSON(out io.Writer, summaries []AgentSummary) error {
	items := make([]AgentJSON, 0, len(summaries))
	for _, s := range summaries {
		items = append(items, s.toJSON())
	}
	enc := json.NewEncoder(out)
	enc.SetIndent("", "  ")
	return enc.Encode(items)
}

func enabledDisplay(s AgentSummary) string {
	if s.Disabled {
		return "false"
	}
	return "true"
}

// FormatAge mirrors kubectl's age column: 10s, 5m, 2h, 3d. Intentionally
// lossy — the precise timestamp is available via `ww agent status`.
// Exported so the TUI can render ages the same way as the CLI table.
func FormatAge(t time.Time) string {
	if t.IsZero() {
		return "-"
	}
	d := time.Since(t)
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%ds", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	}
}
