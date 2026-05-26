package agent

import (
	"context"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// MetricsOptions controls `ww agent metrics`.
type MetricsOptions struct {
	Agent     string
	Namespace string

	// Pod, when non-empty, targets one specific pod instead of every
	// non-terminal pod matching the agent label. This is mostly useful
	// during rollouts when old and new replicas briefly overlap.
	Pod string

	// Timeout bounds the full multi-endpoint scrape. Zero uses a safe
	// default so a wedged metrics listener does not hang the CLI.
	Timeout time.Duration

	Out io.Writer
}

// ScrapeMetricsOptions controls direct metric scraping for callers that need
// structured access to each endpoint instead of the concatenated CLI output.
type ScrapeMetricsOptions struct {
	Agent     string
	Namespace string

	// Pod, when non-empty, targets one specific pod instead of every
	// non-terminal pod matching the agent label.
	Pod string

	// Timeout bounds the full multi-endpoint scrape. Zero uses the same safe
	// default as Metrics.
	Timeout time.Duration
}

// ScrapedMetrics is one raw /metrics response from one container endpoint.
type ScrapedMetrics struct {
	Pod       string
	Container string
	PortName  string
	Port      int32
	Body      []byte
}

type metricsEndpoint struct {
	Pod       string
	Container string
	PortName  string
	Port      int32
}

// Metrics scrapes every named metrics port owned by the selected
// WitwaveAgent pod(s). Each application container exposes its own
// /metrics listener (harness on metrics-harness, backends on
// metrics-<backend>), so the command discovers pod container ports and
// reaches each one through the apiserver pod proxy.
func Metrics(ctx context.Context, cfg *rest.Config, opts MetricsOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("MetricsOptions.Out is required")
	}
	if opts.Agent == "" {
		return fmt.Errorf("MetricsOptions.Agent is required")
	}
	if opts.Namespace == "" {
		return fmt.Errorf("MetricsOptions.Namespace is required")
	}
	scrapes, err := ScrapeMetrics(ctx, cfg, ScrapeMetricsOptions{
		Agent:     opts.Agent,
		Namespace: opts.Namespace,
		Pod:       opts.Pod,
		Timeout:   opts.Timeout,
	})
	if err != nil {
		return err
	}

	multiPod := scrapedMetricsPodCount(scrapes) > 1
	for i, scrape := range scrapes {
		ep := metricsEndpoint{
			Pod:       scrape.Pod,
			Container: scrape.Container,
			PortName:  scrape.PortName,
			Port:      scrape.Port,
		}
		if err := writeMetricsSection(opts.Out, opts.Agent, ep, scrape.Body, multiPod, i > 0); err != nil {
			return err
		}
	}
	return nil
}

// ScrapeMetrics scrapes every named metrics port owned by the selected
// WitwaveAgent pod(s) and returns one raw response per container endpoint.
func ScrapeMetrics(ctx context.Context, cfg *rest.Config, opts ScrapeMetricsOptions) ([]ScrapedMetrics, error) {
	if opts.Agent == "" {
		return nil, fmt.Errorf("ScrapeMetricsOptions.Agent is required")
	}
	if opts.Namespace == "" {
		return nil, fmt.Errorf("ScrapeMetricsOptions.Namespace is required")
	}

	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 30 * time.Second
	}

	k8s, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("build kubernetes client: %w", err)
	}

	scrapeCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	pods, err := selectAgentMetricPods(scrapeCtx, k8s, opts.Namespace, opts.Agent, opts.Pod)
	if err != nil {
		return nil, err
	}
	if len(pods) == 0 {
		return nil, fmt.Errorf(
			"no running pods found for WitwaveAgent %q in namespace %s — is the agent Ready? Run `ww agent status %s`",
			opts.Agent, opts.Namespace, opts.Agent,
		)
	}

	var endpoints []metricsEndpoint
	for _, pod := range pods {
		endpoints = append(endpoints, metricsEndpointsForPod(pod)...)
	}
	if len(endpoints) == 0 {
		return nil, fmt.Errorf("no metrics ports found for agent %s/%s; is spec.metrics.enabled=false?", opts.Namespace, opts.Agent)
	}

	scrapes := make([]ScrapedMetrics, 0, len(endpoints))
	for _, ep := range endpoints {
		raw, err := scrapePodMetrics(scrapeCtx, k8s, opts.Namespace, ep)
		if err != nil {
			return nil, err
		}
		scrapes = append(scrapes, ScrapedMetrics{
			Pod:       ep.Pod,
			Container: ep.Container,
			PortName:  ep.PortName,
			Port:      ep.Port,
			Body:      raw,
		})
	}
	return scrapes, nil
}

func scrapedMetricsPodCount(scrapes []ScrapedMetrics) int {
	pods := map[string]bool{}
	for _, scrape := range scrapes {
		pods[scrape.Pod] = true
	}
	return len(pods)
}

func selectAgentMetricPods(ctx context.Context, k8s kubernetes.Interface, ns, agentName, explicitPod string) ([]corev1.Pod, error) {
	if explicitPod != "" {
		pod, err := k8s.CoreV1().Pods(ns).Get(ctx, explicitPod, metav1.GetOptions{})
		if err != nil {
			return nil, fmt.Errorf("get pod %s/%s: %w", ns, explicitPod, err)
		}
		if isTerminalPodPhase(pod.Status.Phase) {
			return nil, fmt.Errorf("pod %s/%s is %s and has no active metrics endpoint", ns, explicitPod, pod.Status.Phase)
		}
		return []corev1.Pod{*pod}, nil
	}

	sel := labels.SelectorFromSet(labels.Set{
		"app.kubernetes.io/name": agentName,
	})
	list, err := k8s.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{LabelSelector: sel.String()})
	if err != nil {
		return nil, fmt.Errorf("list pods for agent %s in %s: %w", agentName, ns, err)
	}

	pods := make([]corev1.Pod, 0, len(list.Items))
	for i := range list.Items {
		pod := list.Items[i]
		if isTerminalPodPhase(pod.Status.Phase) {
			continue
		}
		pods = append(pods, pod)
	}
	sort.Slice(pods, func(i, j int) bool {
		return pods[i].Name < pods[j].Name
	})
	return pods, nil
}

func isTerminalPodPhase(phase corev1.PodPhase) bool {
	return phase == corev1.PodSucceeded || phase == corev1.PodFailed
}

func metricsEndpointsForPod(pod corev1.Pod) []metricsEndpoint {
	var endpoints []metricsEndpoint
	for _, container := range pod.Spec.Containers {
		for _, port := range container.Ports {
			if !strings.HasPrefix(port.Name, "metrics") {
				continue
			}
			endpoints = append(endpoints, metricsEndpoint{
				Pod:       pod.Name,
				Container: container.Name,
				PortName:  port.Name,
				Port:      port.ContainerPort,
			})
		}
	}
	return endpoints
}

func scrapePodMetrics(ctx context.Context, k8s kubernetes.Interface, ns string, ep metricsEndpoint) ([]byte, error) {
	proxyPath := podMetricsProxyPath(ns, ep.Pod, ep.Port)
	raw, err := k8s.CoreV1().RESTClient().Get().AbsPath(proxyPath).Do(ctx).Raw()
	if err != nil {
		return nil, fmt.Errorf("GET /metrics for pod %s/%s container %s port %s:%d: %w",
			ns, ep.Pod, ep.Container, ep.PortName, ep.Port, err)
	}
	return raw, nil
}

func podMetricsProxyPath(ns, pod string, port int32) string {
	return fmt.Sprintf("/api/v1/namespaces/%s/pods/http:%s:%d/proxy/metrics", ns, pod, port)
}

func writeMetricsSection(w io.Writer, agentName string, ep metricsEndpoint, raw []byte, multiPod bool, leadingBlank bool) error {
	if leadingBlank {
		if _, err := fmt.Fprintln(w); err != nil {
			return err
		}
	}

	if multiPod {
		if _, err := fmt.Fprintf(w, "# ww metrics: agent=%s pod=%s container=%s port=%s:%d\n",
			agentName, ep.Pod, ep.Container, ep.PortName, ep.Port); err != nil {
			return err
		}
	} else {
		if _, err := fmt.Fprintf(w, "# ww metrics: agent=%s container=%s port=%s:%d\n",
			agentName, ep.Container, ep.PortName, ep.Port); err != nil {
			return err
		}
	}
	if _, err := w.Write(raw); err != nil {
		return err
	}
	if len(raw) == 0 || raw[len(raw)-1] != '\n' {
		if _, err := fmt.Fprintln(w); err != nil {
			return err
		}
	}
	return nil
}
