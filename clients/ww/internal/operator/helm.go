package operator

import (
	"context"
	"fmt"
	"io"
	"log"
	"strings"
	"time"

	"helm.sh/helm/v3/pkg/action"
	"helm.sh/helm/v3/pkg/chart"
	"helm.sh/helm/v3/pkg/cli"
	"helm.sh/helm/v3/pkg/release"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/cli-runtime/pkg/genericclioptions"
	"k8s.io/client-go/kubernetes"
)

// HelmClient wraps the Helm action.Configuration and the parameters ww
// cares about for managing the operator release. One HelmClient per
// command invocation — don't reuse across namespaces.
type HelmClient struct {
	cfg         *action.Configuration
	settings    *cli.EnvSettings
	namespace   string
	releaseName string
}

// NewHelmClient builds an action.Configuration against the caller-
// supplied kubeconfig + namespace. The Helm SDK insists on taking its
// own genericclioptions.ConfigFlags; we pass the one the k8s.Resolver
// produced so kubeconfig path + context + namespace are consistent
// across ww's read-only probes and the Helm actions here.
//
// logFn receives Helm's verbose progress messages. Pass a closure that
// writes to ww's output writer, or Noop to discard.
func NewHelmClient(flags *genericclioptions.ConfigFlags, namespace, releaseName string, logFn func(string, ...interface{})) (*HelmClient, error) {
	if logFn == nil {
		logFn = Noop
	}
	cfg := new(action.Configuration)
	// Driver "secret" matches Helm 3's default release storage — one
	// Secret per release revision. "configmap" and "sql" exist but
	// nobody should use them for a first-party install.
	if err := cfg.Init(flags, namespace, "secret", func(format string, v ...interface{}) {
		logFn(format, v...)
	}); err != nil {
		return nil, fmt.Errorf("init helm config: %w", err)
	}
	return &HelmClient{
		cfg:         cfg,
		settings:    cli.New(),
		namespace:   namespace,
		releaseName: releaseName,
	}, nil
}

// Noop is a no-op log sink suitable for callers that want quiet Helm
// actions (e.g. status probes).
func Noop(string, ...interface{}) {}

// helmReleaseStatus reads release.Info.Status safely. The Helm SDK has
// shipped releases where Install/Upgrade returned a *release.Release
// whose Info pointer was nil (early-failure paths, or drivers mid-
// migration between schema versions). Dereferencing blindly turns a
// successful install into a panic-level exit (#1550). When Info is nil,
// return "(unknown)" so the success banner still renders.
func helmReleaseStatus(rel *release.Release) string {
	if rel == nil || rel.Info == nil {
		return "(unknown)"
	}
	return string(rel.Info.Status)
}

// StderrLog returns a log function that writes Helm's progress output
// to the given writer. Each message gets a "helm: " prefix so users
// can separate it from ww's own output.
func StderrLog(w io.Writer) func(string, ...interface{}) {
	logger := log.New(w, "helm: ", 0)
	return logger.Printf
}

// Install runs `helm install <release> <chart>` using the embedded
// chart. Values is the rendered user-supplied values map (typically
// empty for ww operator install — the chart defaults are sensible).
// Returns the deployed release on success.
//
// The caller is responsible for having run the preflight checks
// (singleton detection, RBAC preflight, namespace creation).
func (c *HelmClient) Install(ctx context.Context, ch *chart.Chart, values map[string]interface{}) (*release.Release, error) {
	act := action.NewInstall(c.cfg)
	act.ReleaseName = c.releaseName
	act.Namespace = c.namespace
	act.CreateNamespace = true
	act.Wait = false
	// We install the CRDs explicitly via SSA in the upgrade path and
	// via helm.DisableCRDInstall=false here so first-install carries
	// the CRDs as part of the chart. Operators running ww twice in a
	// row will hit the SSA path on the second invocation.
	act.SkipCRDs = false
	// RunWithContext honours context cancellation mid-apply.
	rel, err := act.RunWithContext(ctx, ch, values)
	if err != nil {
		return nil, fmt.Errorf("helm install: %w", err)
	}
	return rel, nil
}

// Upgrade runs `helm upgrade <release> <chart>` against an existing
// release. Caller should have applied any CRD changes via a separate
// server-side-apply step first so Helm's upgrade just touches the
// regular Kubernetes objects.
func (c *HelmClient) Upgrade(ctx context.Context, ch *chart.Chart, values map[string]interface{}) (*release.Release, error) {
	act := action.NewUpgrade(c.cfg)
	act.Namespace = c.namespace
	act.Wait = false
	act.SkipCRDs = true // CRDs are applied separately (see upgrade.go)
	act.MaxHistory = 10 // keep recent history for rollback; drop ancient
	// ResetThenReuseValues makes a plain `ww operator upgrade` durable:
	// start from the embedded chart's NEW defaults (so a value the user
	// never set — notably image.tag, which defaults to the embedded
	// chart's appVersion — tracks the version this ww binary ships),
	// re-apply the previous release's user overrides, then layer the
	// values passed on THIS invocation on top. The alternative — Helm's
	// default reset-to-chart-defaults — silently drops any value the
	// operator enabled earlier (e.g. agentImagePatchPolicy) on every
	// upgrade that doesn't re-pass it, which is exactly the durability
	// gap this command must not have. Plain ReuseValues would instead
	// pin the OLD chart's defaults and miss new ones, so reset-then-reuse
	// is the correct middle ground for an in-place upgrade.
	act.ResetThenReuseValues = true
	rel, err := act.RunWithContext(ctx, c.releaseName, ch, values)
	if err != nil {
		return nil, fmt.Errorf("helm upgrade: %w", err)
	}
	return rel, nil
}

// Uninstall removes the Helm release. CRD deletion is NOT part of
// Helm's uninstall and MUST be handled separately — callers decide
// whether to delete CRDs based on --delete-crds + CR-existence safety.
//
// Helm's action.Uninstall does not expose a RunWithContext entry point,
// so ctx is honoured two ways: (1) when ctx has a deadline, the
// remaining time is copied into act.Timeout so the SDK's own wait
// mechanisms cut over; (2) the Run call executes in a goroutine and
// ctx.Done() wins the race so the caller returns immediately on Ctrl-C,
// letting the helm call finish in the background rather than blocking
// the command (#1548).
func (c *HelmClient) Uninstall(ctx context.Context) (*release.UninstallReleaseResponse, error) {
	act := action.NewUninstall(c.cfg)
	// Propagate any caller deadline into Helm's own timeout so the SDK
	// has a chance to abort cleanly rather than relying purely on the
	// goroutine shim below.
	if deadline, ok := ctx.Deadline(); ok {
		if remaining := time.Until(deadline); remaining > 0 {
			act.Timeout = remaining
		}
	}
	// Keep history off so a re-install lands cleanly.
	act.KeepHistory = false

	type result struct {
		resp *release.UninstallReleaseResponse
		err  error
	}
	done := make(chan result, 1)
	go func() {
		resp, err := act.Run(c.releaseName)
		done <- result{resp: resp, err: err}
	}()

	select {
	case <-ctx.Done():
		// Release control back to the caller immediately; the in-flight
		// Run will finish (or fail) asynchronously. Wrap ctx.Err so
		// callers can detect cancellation via errors.Is.
		//
		// #1655: the Helm SDK doesn't accept a context, so we can't
		// propagate cancellation into act.Run — the goroutine keeps
		// executing after we return. Spawn a bounded background waiter
		// that surfaces a WARN log if the in-flight call hasn't settled
		// within a generous window, so operators know dangling Helm work
		// continues past the command exit. This is observability only;
		// it does NOT block the function return.
		go func() {
			const danglingWarnAfter = 60 * time.Second
			t := time.NewTimer(danglingWarnAfter)
			defer t.Stop()
			select {
			case <-done:
				// Background Run completed; nothing to flag.
			case <-t.C:
				log.Printf("WARNING: helm uninstall for release %q in namespace %q is still running %s after caller cancellation; release state may be inconsistent until it settles (#1655)",
					c.releaseName, c.namespace, danglingWarnAfter)
			}
		}()
		return nil, fmt.Errorf("helm uninstall: %w", ctx.Err())
	case r := <-done:
		if r.err != nil {
			return nil, fmt.Errorf("helm uninstall: %w", r.err)
		}
		return r.resp, nil
	}
}

// Get returns the current release info or nil if not installed.
func (c *HelmClient) Get() (*release.Release, error) {
	act := action.NewGet(c.cfg)
	rel, err := act.Run(c.releaseName)
	if err != nil {
		// Helm returns a specific error for "release not found"; string-
		// match is gross but the SDK doesn't export a typed sentinel.
		if strings.Contains(err.Error(), "release: not found") {
			return nil, nil
		}
		return nil, fmt.Errorf("helm get: %w", err)
	}
	return rel, nil
}

// EnsureNamespace creates the target namespace if it doesn't exist.
// Idempotent — calling against an existing namespace is a no-op.
// Helm's own CreateNamespace flag covers install, but uninstall and
// manual namespace seeding (e.g. for dry-run output) need their own
// path.
func EnsureNamespace(ctx context.Context, k8s kubernetes.Interface, ns string) error {
	_, err := k8s.CoreV1().Namespaces().Get(ctx, ns, metav1.GetOptions{})
	if err == nil {
		return nil
	}
	if !apierrors.IsNotFound(err) {
		return fmt.Errorf("probe namespace %s: %w", ns, err)
	}
	_, err = k8s.CoreV1().Namespaces().Create(ctx, &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{Name: ns},
	}, metav1.CreateOptions{})
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("create namespace %s: %w", ns, err)
	}
	return nil
}
