package agent

import (
	"bytes"
	"context"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

func TestMetricsEndpointsForPod(t *testing.T) {
	pod := corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "mira-abc", Namespace: "witwave-self"},
		Spec: corev1.PodSpec{
			Containers: []corev1.Container{
				{
					Name: "harness",
					Ports: []corev1.ContainerPort{
						{Name: "http", ContainerPort: 8000},
						{Name: "metrics-harness", ContainerPort: 9000},
					},
				},
				{Name: "git-sync"},
				{
					Name: "codex",
					Ports: []corev1.ContainerPort{
						{Name: "http", ContainerPort: 8001},
						{Name: "metrics-codex", ContainerPort: 9001},
					},
				},
			},
		},
	}

	got := metricsEndpointsForPod(pod)
	want := []metricsEndpoint{
		{Pod: "mira-abc", Container: "harness", PortName: "metrics-harness", Port: 9000},
		{Pod: "mira-abc", Container: "codex", PortName: "metrics-codex", Port: 9001},
	}
	if len(got) != len(want) {
		t.Fatalf("metricsEndpointsForPod len=%d, want %d (got=%+v)", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("metricsEndpointsForPod[%d]=%+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestSelectAgentMetricPods(t *testing.T) {
	pod := func(name string, phase corev1.PodPhase) *corev1.Pod {
		return &corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "witwave-self",
				Labels: map[string]string{
					"app.kubernetes.io/name": "mira",
				},
			},
			Status: corev1.PodStatus{Phase: phase},
		}
	}
	client := fake.NewClientset(
		pod("mira-b", corev1.PodRunning),
		pod("mira-a", corev1.PodPending),
		pod("mira-old", corev1.PodSucceeded),
		&corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "zora-a",
				Namespace: "witwave-self",
				Labels: map[string]string{
					"app.kubernetes.io/name": "zora",
				},
			},
			Status: corev1.PodStatus{Phase: corev1.PodRunning},
		},
	)

	got, err := selectAgentMetricPods(context.Background(), client, "witwave-self", "mira", "")
	if err != nil {
		t.Fatalf("selectAgentMetricPods unexpected error: %v", err)
	}
	wantNames := []string{"mira-a", "mira-b"}
	if len(got) != len(wantNames) {
		t.Fatalf("selectAgentMetricPods len=%d, want %d (got=%v)", len(got), len(wantNames), got)
	}
	for i, want := range wantNames {
		if got[i].Name != want {
			t.Errorf("selectAgentMetricPods[%d]=%q, want %q", i, got[i].Name, want)
		}
	}
}

func TestSelectAgentMetricPodsExplicitTerminalPod(t *testing.T) {
	client := fake.NewClientset(&corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "mira-old", Namespace: "witwave-self"},
		Status:     corev1.PodStatus{Phase: corev1.PodFailed},
	})

	_, err := selectAgentMetricPods(context.Background(), client, "witwave-self", "mira", "mira-old")
	if err == nil {
		t.Fatal("expected terminal pod error, got nil")
	}
	if !strings.Contains(err.Error(), "Failed") {
		t.Fatalf("error %q does not mention terminal phase", err.Error())
	}
}

func TestPodMetricsProxyPath(t *testing.T) {
	got := podMetricsProxyPath("witwave-self", "mira-abc", 9001)
	want := "/api/v1/namespaces/witwave-self/pods/http:mira-abc:9001/proxy/metrics"
	if got != want {
		t.Errorf("podMetricsProxyPath = %q, want %q", got, want)
	}
}

func TestWriteMetricsSection(t *testing.T) {
	var buf bytes.Buffer
	ep := metricsEndpoint{
		Pod:       "mira-abc",
		Container: "codex",
		PortName:  "metrics-codex",
		Port:      9001,
	}

	err := writeMetricsSection(&buf, "mira", ep, []byte("backend_requests_total 1"), false, false)
	if err != nil {
		t.Fatalf("writeMetricsSection unexpected error: %v", err)
	}

	got := buf.String()
	for _, want := range []string{
		"# ww metrics: agent=mira container=codex port=metrics-codex:9001\n",
		"backend_requests_total 1\n",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("writeMetricsSection output %q missing %q", got, want)
		}
	}
}
