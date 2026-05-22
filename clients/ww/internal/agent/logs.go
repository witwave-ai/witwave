package agent

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// LogsOptions — inputs to `ww agent logs`.
type LogsOptions struct {
	Agent     string
	Namespace string
	// Container — single container name to tail. Empty means "tail every
	// container in the agent pod (harness + backend(s) + git-sync)" with
	// each line prefixed by `[<container>]` so the user can tell streams
	// apart. Set to a specific name (echo/claude/openai/gemini/harness/
	// git-sync) to filter to that one container — the prefix is still
	// emitted for consistency.
	Container string
	// Follow — stream until context is cancelled. Default true.
	Follow bool
	// TailLines — emit at most this many lines of log history before
	// starting to follow. 0 = no limit.
	TailLines int64
	// Since — lookback window. Zero means "whatever the server returns".
	Since time.Duration
	// Pod — when non-empty, only tail this specific pod (by name).
	// Otherwise every pod matching the agent label is tailed. Useful
	// when an agent has been scaled or is mid-rollout.
	Pod string
	Out io.Writer
}

// Logs streams container logs for a WitwaveAgent's pods. Pattern mirrors
// internal/operator/logs.go but with a (pod × container) fan-out instead
// of pod-only — an agent pod has multiple containers (harness +
// backend(s) + git-sync) and the user typically wants all of them
// interleaved with a [container]-prefix on every line.
//
// Concurrency: one goroutine per (pod, container) pair, all writing to a
// single Writer under one mutex so line boundaries don't interleave.
// Follow blocks until ctx cancels; no-follow returns when every stream
// EOFs.
func Logs(ctx context.Context, cfg *rest.Config, opts LogsOptions) error {
	if opts.Out == nil {
		return fmt.Errorf("LogsOptions.Out is required")
	}
	if opts.Agent == "" {
		return fmt.Errorf("LogsOptions.Agent is required")
	}

	k8s, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return fmt.Errorf("build kubernetes client: %w", err)
	}

	pods, err := selectAgentPods(ctx, k8s, opts.Namespace, opts.Agent, opts.Pod)
	if err != nil {
		return err
	}
	if len(pods) == 0 {
		return fmt.Errorf(
			"no pods found for WitwaveAgent %q in namespace %s — is the operator installed and the agent Ready? Run `ww agent status %s`",
			opts.Agent, opts.Namespace, opts.Agent,
		)
	}

	// Resolve the (pod, container) pairs to tail. When opts.Container is
	// non-empty we filter to that one; otherwise we list each pod's
	// containers via the API. Doing this up-front rather than failing
	// inside per-stream goroutines lets us return a clean error on a typo
	// like `-c clude`.
	type target struct{ pod, container string }
	var targets []target
	for _, p := range pods {
		containers, err := podContainerNames(ctx, k8s, opts.Namespace, p, opts.Container)
		if err != nil {
			return err
		}
		for _, c := range containers {
			targets = append(targets, target{pod: p, container: c})
		}
	}
	if len(targets) == 0 {
		return fmt.Errorf("no containers to tail for agent %s/%s (filter %q yielded no matches)",
			opts.Namespace, opts.Agent, opts.Container)
	}

	// Prefix shape: `[container]` is the common case (one pod, one or
	// many containers). When more than one pod is in scope (rollout, or
	// an explicit --pod targeting one of several), include a short pod
	// suffix in the prefix — the last `_-_`-separated chunk of the pod
	// name is the unique-per-pod hash, enough to disambiguate without
	// dragging the full deployment name through every log line.
	multiPod := len(pods) > 1
	var writeMu sync.Mutex
	writeLine := func(pod, container, line string) {
		writeMu.Lock()
		defer writeMu.Unlock()
		if multiPod {
			fmt.Fprintf(opts.Out, "[%s/%s] %s\n", shortPodSuffix(pod), container, line)
		} else {
			fmt.Fprintf(opts.Out, "[%s] %s\n", container, line)
		}
	}

	var wg sync.WaitGroup
	errCh := make(chan error, len(targets))
	for _, t := range targets {
		wg.Add(1)
		t := t
		go func() {
			defer wg.Done()
			emit := func(_, line string) { writeLine(t.pod, t.container, line) }
			if err := streamAgentPodLogs(ctx, k8s, opts.Namespace, t.pod, t.container, opts, emit); err != nil {
				errCh <- fmt.Errorf("pod %s container %s: %w", t.pod, t.container, err)
			}
		}()
	}
	wg.Wait()
	close(errCh)

	// Surface the first non-cancellation error. Ctrl-C is the normal
	// exit path and doesn't count as a failure.
	for e := range errCh {
		if ctx.Err() == nil {
			return e
		}
	}
	return nil
}

// podContainerNames returns the containers to tail for one pod. When
// filter is empty, every container in the pod's spec is returned. When
// filter is non-empty, only that container is returned — and an error
// surfaces if the named container doesn't exist on the pod (catches
// typos before we waste a stream).
func podContainerNames(ctx context.Context, k8s kubernetes.Interface, ns, pod, filter string) ([]string, error) {
	p, err := k8s.CoreV1().Pods(ns).Get(ctx, pod, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("get pod %s/%s: %w", ns, pod, err)
	}
	all := make([]string, 0, len(p.Spec.Containers))
	for i := range p.Spec.Containers {
		all = append(all, p.Spec.Containers[i].Name)
	}
	if filter == "" {
		return all, nil
	}
	for _, c := range all {
		if c == filter {
			return []string{c}, nil
		}
	}
	return nil, fmt.Errorf("container %q not found in pod %s/%s; available: %v", filter, ns, pod, all)
}

// shortPodSuffix returns the last hyphen-separated segment of a pod name
// (the per-pod random hash for ReplicaSet-managed pods). Used in the
// log-line prefix when multiple pods are in scope so the user can tell
// the old replica from the new one during a rollout. Falls back to the
// full name when there's no hyphen.
func shortPodSuffix(pod string) string {
	for i := len(pod) - 1; i >= 0; i-- {
		if pod[i] == '-' {
			return pod[i+1:]
		}
	}
	return pod
}

// selectAgentPods returns the pod names to tail for a given agent.
// Pattern mirrors internal/operator/logs.go:selectLogTargets.
func selectAgentPods(ctx context.Context, k8s kubernetes.Interface, ns, agentName, explicitPod string) ([]string, error) {
	if explicitPod != "" {
		// Sanity-check the pod exists; a missing pod surfaces a clear
		// error rather than a silently empty stream.
		if _, err := k8s.CoreV1().Pods(ns).Get(ctx, explicitPod, metav1.GetOptions{}); err != nil {
			return nil, fmt.Errorf("get pod %s/%s: %w", ns, explicitPod, err)
		}
		return []string{explicitPod}, nil
	}
	sel := labels.SelectorFromSet(labels.Set{
		"app.kubernetes.io/name": agentName,
	})
	list, err := k8s.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{LabelSelector: sel.String()})
	if err != nil {
		return nil, fmt.Errorf("list pods for agent %s in %s: %w", agentName, ns, err)
	}
	out := make([]string, 0, len(list.Items))
	for i := range list.Items {
		out = append(out, list.Items[i].Name)
	}
	return out, nil
}

// streamAgentPodLogs opens a log stream for one (pod, container) and
// pumps each line through `emit`.
func streamAgentPodLogs(
	ctx context.Context,
	k8s kubernetes.Interface,
	ns, pod, container string,
	opts LogsOptions,
	emit func(pod, line string),
) error {
	podLogOpts := &corev1.PodLogOptions{
		Container: container,
		Follow:    opts.Follow,
	}
	if opts.TailLines > 0 {
		tail := opts.TailLines
		podLogOpts.TailLines = &tail
	}
	if opts.Since > 0 {
		ts := metav1.NewTime(time.Now().Add(-opts.Since))
		podLogOpts.SinceTime = &ts
	}

	req := k8s.CoreV1().Pods(ns).GetLogs(pod, podLogOpts)
	stream, err := req.Stream(ctx)
	if err != nil {
		return fmt.Errorf("open log stream for container %q: %w", container, err)
	}
	defer stream.Close()

	// bufio buffer bump matches operator/logs.go — harness + backends
	// can emit >64 KiB lines on stack traces or bulky tool-audit entries.
	scanner := bufio.NewScanner(stream)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		emit(pod, scanner.Text())
		if ctx.Err() != nil {
			return nil
		}
	}
	if err := scanner.Err(); err != nil && ctx.Err() == nil {
		return fmt.Errorf("read log stream: %w", err)
	}
	return nil
}
