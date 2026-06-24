// Package operator implements the `ww operator install/upgrade/status/uninstall`
// commands. This file contains the read-only plumbing — locating the Helm
// release, inspecting pods, listing CRDs — so `ww operator status` can ship
// before the full Helm-SDK-backed install/upgrade paths.
package operator

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
)

// ReleaseName is the Helm release name ww uses when installing the
// operator chart. Hardcoded so `status` can look it up without a config
// file; when we later support `--release-name`, this becomes the default.
const ReleaseName = "witwave-operator"

// DefaultNamespace is ww's install-time default for the operator. Users
// can override with --namespace.
const DefaultNamespace = "witwave-system"

// crdNames enumerates the CRDs the operator owns. Used by status (presence
// check) and uninstall (CR-existence safety gate). Keep in sync with the
// chart's crds/ directory and the operator's api/v1alpha1 types.
var crdNames = []string{
	"witwaveagents.witwave.ai",
	"witwaveprompts.witwave.ai",
	"witwaveworkspaces.witwave.ai",
}

// ReleaseInfo is the decoded subset of a Helm release Secret relevant to
// ww. Helm stores each release revision as a Secret named
// `sh.helm.release.v1.<name>.v<revision>` with a gzipped+base64 payload in
// data["release"]. We decode it directly so `ww` doesn't pull in the full
// Helm SDK just to read status.
type ReleaseInfo struct {
	Name         string
	Namespace    string
	Revision     int
	Status       string // "deployed", "failed", ...
	ChartName    string
	ChartVersion string
	AppVersion   string
}

// LookupRelease returns the latest revision of a Helm release by name, or
// (nil, nil) if no release is found in the namespace. Errors other than
// NotFound propagate.
//
// Scans all Secrets in the namespace filtered by Helm's canonical labels
// (owner=helm, name=<release>) and returns the highest-revision hit.
func LookupRelease(ctx context.Context, k8s kubernetes.Interface, ns, name string) (*ReleaseInfo, error) {
	sel := labels.SelectorFromSet(labels.Set{
		"owner": "helm",
		"name":  name,
	})
	list, err := k8s.CoreV1().Secrets(ns).List(ctx, metav1.ListOptions{LabelSelector: sel.String()})
	if err != nil {
		return nil, fmt.Errorf("list helm release secrets in %s: %w", ns, err)
	}
	if len(list.Items) == 0 {
		return nil, nil
	}

	// Pick the highest revision. Helm stores the revision in the "version"
	// label as a decimal integer; sort numerically so revision 10 beats 9
	// (lexicographic sort returns "9" > "10", stranding ww on an old
	// release after 10+ revisions).
	latest := &list.Items[0]
	latestRev := parseRevisionLabel(latest.Labels["version"])
	for i := 1; i < len(list.Items); i++ {
		rev := parseRevisionLabel(list.Items[i].Labels["version"])
		if rev > latestRev {
			latest = &list.Items[i]
			latestRev = rev
		}
	}
	return decodeReleaseSecret(latest)
}

// parseRevisionLabel returns the int value of Helm's "version" label, or -1
// if the label is missing / unparsable. Unparsable labels are ordered below
// any valid revision so a stray Secret can't displace a real release.
func parseRevisionLabel(v string) int {
	if v == "" {
		return -1
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return -1
	}
	return n
}

// FindReleaseClusterWide returns the namespace in which a release of the
// given name is installed, or "" if none. Used by the singleton-detection
// preflight — the caller can then refuse install when a release already
// exists in some other namespace.
func FindReleaseClusterWide(ctx context.Context, k8s kubernetes.Interface, name string) (string, error) {
	sel := labels.SelectorFromSet(labels.Set{
		"owner": "helm",
		"name":  name,
	})
	list, err := k8s.CoreV1().Secrets("").List(ctx, metav1.ListOptions{LabelSelector: sel.String()})
	if err != nil {
		return "", fmt.Errorf("cluster-wide helm release search for %q: %w", name, err)
	}
	if len(list.Items) == 0 {
		return "", nil
	}
	// Return the namespace of any matching secret — Helm disallows two
	// releases with the same name across namespaces in practice.
	return list.Items[0].Namespace, nil
}

// decodeReleaseSecret unwraps Helm's release-v1 payload. The format is:
// base64 of gzip of JSON (as of Helm 3.x). Any decode failure yields a
// diagnosable error rather than a partial result.
func decodeReleaseSecret(s *corev1.Secret) (*ReleaseInfo, error) {
	raw, ok := s.Data["release"]
	if !ok {
		return nil, fmt.Errorf("secret %s/%s missing data[\"release\"]", s.Namespace, s.Name)
	}

	// First hop: base64 decode.
	decoded, err := base64.StdEncoding.DecodeString(string(raw))
	if err != nil {
		// Newer Helm versions may already have the raw bytes in Data
		// (Kubernetes applies its own base64 layer over `data`). If
		// gzip works on the raw value, use that; otherwise propagate
		// the decode error.
		decoded = raw
	}

	// Second hop: gzip.
	gz, err := gzip.NewReader(bytes.NewReader(decoded))
	if err != nil {
		return nil, fmt.Errorf("decompress helm release %s/%s: %w", s.Namespace, s.Name, err)
	}
	jsonBytes, err := io.ReadAll(gz)
	if err != nil {
		return nil, fmt.Errorf("read helm release %s/%s: %w", s.Namespace, s.Name, err)
	}
	_ = gz.Close()

	// Third hop: JSON decode into just the fields we care about. Helm's
	// full release struct is enormous; this tolerates unknown fields.
	var shallow struct {
		Name      string `json:"name"`
		Namespace string `json:"namespace"`
		Version   int    `json:"version"`
		Info      struct {
			Status string `json:"status"`
		} `json:"info"`
		Chart struct {
			Metadata struct {
				Name       string `json:"name"`
				Version    string `json:"version"`
				AppVersion string `json:"appVersion"`
			} `json:"metadata"`
		} `json:"chart"`
	}
	if err := json.Unmarshal(jsonBytes, &shallow); err != nil {
		return nil, fmt.Errorf("parse helm release %s/%s json: %w", s.Namespace, s.Name, err)
	}

	return &ReleaseInfo{
		Name:         shallow.Name,
		Namespace:    shallow.Namespace,
		Revision:     shallow.Version,
		Status:       shallow.Info.Status,
		ChartName:    shallow.Chart.Metadata.Name,
		ChartVersion: shallow.Chart.Metadata.Version,
		AppVersion:   shallow.Chart.Metadata.AppVersion,
	}, nil
}

// PodSummary is a compact row for the status output's Pods block.
type PodSummary struct {
	Name     string
	Phase    string
	IsLeader bool // best-effort — populated when a leader lease exists
}

// ListOperatorPods returns the operator's pods in the given namespace,
// matched by the standard `app.kubernetes.io/name=witwave-operator` label.
// Empty list + nil error means "operator not running here."
func ListOperatorPods(ctx context.Context, k8s kubernetes.Interface, ns string) ([]PodSummary, error) {
	sel := labels.SelectorFromSet(labels.Set{
		"app.kubernetes.io/name": "witwave-operator",
	})
	list, err := k8s.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{LabelSelector: sel.String()})
	if err != nil {
		return nil, fmt.Errorf("list operator pods in %s: %w", ns, err)
	}
	out := make([]PodSummary, 0, len(list.Items))
	for i := range list.Items {
		p := &list.Items[i]
		out = append(out, PodSummary{
			Name:  p.Name,
			Phase: string(p.Status.Phase),
		})
	}
	return out, nil
}

// CRDInfo is a compact description of a CustomResourceDefinition for the
// status block. We access CRDs through the dynamic client so ww doesn't
// take a compile-time dependency on the operator's Go types.
type CRDInfo struct {
	Name     string
	Versions []string
	Found    bool
}

var crdGVR = schema.GroupVersionResource{
	Group:    "apiextensions.k8s.io",
	Version:  "v1",
	Resource: "customresourcedefinitions",
}

// InspectCRDs looks up each CRD ww cares about and reports which versions
// are served + stored. Missing CRDs return Found=false without error.
func InspectCRDs(ctx context.Context, dyn dynamic.Interface) ([]CRDInfo, error) {
	out := make([]CRDInfo, 0, len(crdNames))
	for _, name := range crdNames {
		u, err := dyn.Resource(crdGVR).Get(ctx, name, metav1.GetOptions{})
		if apierrors.IsNotFound(err) {
			out = append(out, CRDInfo{Name: name, Found: false})
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("get CRD %s: %w", name, err)
		}
		versions := []string{}
		if vs, found, _ := unstructuredSlice(u.Object, "spec", "versions"); found {
			for _, v := range vs {
				if vm, ok := v.(map[string]interface{}); ok {
					if s, ok := vm["name"].(string); ok {
						versions = append(versions, s)
					}
				}
			}
		}
		out = append(out, CRDInfo{Name: name, Versions: versions, Found: true})
	}
	return out, nil
}

// CountCRs returns the count of each kind ww cares about. Missing CRDs
// are reported as 0 without error — callers typically render both
// InspectCRDs + CountCRs to show "CRD absent" vs "CRD present, zero CRs".
func CountCRs(ctx context.Context, dyn dynamic.Interface) (map[string]int, error) {
	counts := map[string]int{}
	for _, spec := range []struct {
		kindLabel string
		gvr       schema.GroupVersionResource
	}{
		{"WitwaveAgent", schema.GroupVersionResource{Group: "witwave.ai", Version: "v1alpha1", Resource: "witwaveagents"}},
		{"WitwavePrompt", schema.GroupVersionResource{Group: "witwave.ai", Version: "v1alpha1", Resource: "witwaveprompts"}},
		{"WitwaveWorkspace", schema.GroupVersionResource{Group: "witwave.ai", Version: "v1alpha1", Resource: "witwaveworkspaces"}},
	} {
		l, err := dyn.Resource(spec.gvr).List(ctx, metav1.ListOptions{})
		switch {
		case err == nil:
			counts[spec.kindLabel] = len(l.Items)
		case apierrors.IsNotFound(err), isCRDNotFound(err):
			// CRD absent — report 0, not an error.
			counts[spec.kindLabel] = 0
		default:
			return nil, fmt.Errorf("list %s: %w", spec.kindLabel, err)
		}
	}
	return counts, nil
}

// isCRDNotFound detects the "no matches for kind" error returned by the
// discovery client when the CRD is absent. This is distinct from a
// NotFound on a named resource and isn't always wrapped with IsNotFound.
func isCRDNotFound(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.Contains(s, "no matches for kind") || strings.Contains(s, "could not find the requested resource")
}

// unstructuredSlice walks an arbitrarily-nested unstructured map and
// returns the slice at the given path, if any.
func unstructuredSlice(obj map[string]interface{}, path ...string) ([]interface{}, bool, error) {
	if len(path) == 0 {
		return nil, false, errors.New("empty path")
	}
	var cur interface{} = obj
	for _, p := range path {
		m, ok := cur.(map[string]interface{})
		if !ok {
			return nil, false, nil
		}
		cur, ok = m[p]
		if !ok {
			return nil, false, nil
		}
	}
	s, ok := cur.([]interface{})
	if !ok {
		return nil, false, nil
	}
	return s, true, nil
}
