package cmd

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/witwave-ai/witwave/clients/ww/internal/agent"
	"k8s.io/client-go/rest"
)

type runtimeExpectations struct {
	model             string
	reasoningEffort   string
	defaultMaxTokens  *int
	maxToolIterations *int
	streaming         *bool
	stubMode          *bool
}

type prometheusSample struct {
	Name   string
	Labels map[string]string
	Value  float64
	Source string
}

func runtimeExpectationsConfigured(f doctorFlags) bool {
	for _, value := range []string{
		f.runtimeModel,
		f.runtimeReasoningEffort,
		f.runtimeDefaultMaxTokens,
		f.runtimeMaxToolIterations,
		f.runtimeStreaming,
		f.runtimeStubMode,
	} {
		if strings.TrimSpace(value) != "" {
			return true
		}
	}
	return false
}

func buildRuntimeExpectations(f doctorFlags) runtimeExpectations {
	expect := runtimeExpectations{
		model:           strings.TrimSpace(f.runtimeModel),
		reasoningEffort: strings.TrimSpace(f.runtimeReasoningEffort),
	}
	if value := strings.TrimSpace(f.runtimeDefaultMaxTokens); value != "" {
		parsed, _ := strconv.Atoi(value)
		expect.defaultMaxTokens = &parsed
	}
	if value := strings.TrimSpace(f.runtimeMaxToolIterations); value != "" {
		parsed, _ := strconv.Atoi(value)
		expect.maxToolIterations = &parsed
	}
	if value := strings.TrimSpace(f.runtimeStreaming); value != "" {
		parsed, _ := strconv.ParseBool(value)
		expect.streaming = &parsed
	}
	if value := strings.TrimSpace(f.runtimeStubMode); value != "" {
		parsed, _ := strconv.ParseBool(value)
		expect.stubMode = &parsed
	}
	return expect
}

func evaluateRuntimeMetrics(ctx context.Context, cfg *rest.Config, summaries []agent.AgentSummary, f doctorFlags) []doctorCheck {
	if len(summaries) == 0 {
		return []doctorCheck{{
			Name:    "backend runtime posture",
			Status:  doctorFail,
			Details: "runtime expectations were requested, but no enabled WitwaveAgents were inspected",
		}}
	}

	expect := buildRuntimeExpectations(f)
	targetBackends := normalizeRequiredBackends(f.requiredBackends)
	failures := []string{}
	passes := []string{}
	for _, summary := range summaries {
		scrapes, err := agent.ScrapeMetrics(ctx, cfg, agent.ScrapeMetricsOptions{
			Agent:     summary.Name,
			Namespace: summary.Namespace,
			Timeout:   30 * time.Second,
		})
		if err != nil {
			failures = append(failures, fmt.Sprintf("%s/%s: %v", summary.Namespace, summary.Name, err))
			continue
		}
		if mismatches := evaluateRuntimeMetricSamples(scrapes, expect, targetBackends); len(mismatches) > 0 {
			failures = append(failures, fmt.Sprintf("%s/%s: %s", summary.Namespace, summary.Name, strings.Join(mismatches, ", ")))
			continue
		}
		passes = append(passes, fmt.Sprintf("%s/%s %s", summary.Namespace, summary.Name, formatRuntimeExpectations(expect, targetBackends)))
	}
	if len(failures) > 0 {
		return []doctorCheck{{Name: "backend runtime posture", Status: doctorFail, Details: strings.Join(failures, "; ")}}
	}
	return []doctorCheck{{Name: "backend runtime posture", Status: doctorPass, Details: strings.Join(passes, "; ")}}
}

func evaluateRuntimeMetricSamples(scrapes []agent.ScrapedMetrics, expect runtimeExpectations, targetBackends []string) []string {
	samples := collectRuntimeMetricSamples(scrapes, targetBackends)
	if len(samples) == 0 {
		target := "selected backend"
		if len(targetBackends) > 0 {
			target = strings.Join(targetBackends, ",")
		}
		return []string{fmt.Sprintf("no backend_runtime_* metrics found for %s; metrics may be disabled or the backend image may be older than runtime posture metrics", target)}
	}

	mismatches := []string{}
	if expect.model != "" || expect.reasoningEffort != "" {
		configSamples := samples["backend_runtime_config_info"]
		if len(configSamples) == 0 {
			mismatches = append(mismatches, "missing backend_runtime_config_info")
		}
		for _, sample := range configSamples {
			if expect.model != "" && sample.Labels["model"] != expect.model {
				mismatches = append(mismatches, fmt.Sprintf("backend_runtime_config_info model on %s is %q, want %q",
					runtimeSampleSource(sample), sample.Labels["model"], expect.model))
			}
			if expect.reasoningEffort != "" && sample.Labels["reasoning_effort"] != expect.reasoningEffort {
				mismatches = append(mismatches, fmt.Sprintf("backend_runtime_config_info reasoning_effort on %s is %q, want %q",
					runtimeSampleSource(sample), sample.Labels["reasoning_effort"], expect.reasoningEffort))
			}
		}
	}
	if expect.defaultMaxTokens != nil {
		mismatches = append(mismatches, evaluateRuntimeGauge(samples, "backend_runtime_default_max_tokens", float64(*expect.defaultMaxTokens))...)
	}
	if expect.maxToolIterations != nil {
		mismatches = append(mismatches, evaluateRuntimeGauge(samples, "backend_runtime_max_tool_iterations", float64(*expect.maxToolIterations))...)
	}
	if expect.streaming != nil {
		mismatches = append(mismatches, evaluateRuntimeGauge(samples, "backend_runtime_responses_streaming_enabled", boolGauge(*expect.streaming))...)
	}
	if expect.stubMode != nil {
		mismatches = append(mismatches, evaluateRuntimeGauge(samples, "backend_runtime_stub_mode_enabled", boolGauge(*expect.stubMode))...)
	}
	return mismatches
}

func collectRuntimeMetricSamples(scrapes []agent.ScrapedMetrics, targetBackends []string) map[string][]prometheusSample {
	targets := map[string]bool{}
	for _, backend := range targetBackends {
		targets[strings.ToLower(strings.TrimSpace(backend))] = true
	}
	out := map[string][]prometheusSample{}
	for _, scrape := range scrapes {
		for _, line := range strings.Split(string(scrape.Body), "\n") {
			sample, ok := parsePrometheusSample(line)
			if !ok || !strings.HasPrefix(sample.Name, "backend_runtime_") {
				continue
			}
			backend := strings.ToLower(strings.TrimSpace(sample.Labels["backend"]))
			if len(targets) > 0 && !targets[backend] {
				continue
			}
			sample.Source = fmt.Sprintf("%s/%s:%d", scrape.Container, scrape.PortName, scrape.Port)
			if backend != "" {
				sample.Source = backend
			}
			out[sample.Name] = append(out[sample.Name], sample)
		}
	}
	return out
}

func evaluateRuntimeGauge(samples map[string][]prometheusSample, metric string, want float64) []string {
	metricSamples := samples[metric]
	if len(metricSamples) == 0 {
		return []string{"missing " + metric}
	}
	mismatches := []string{}
	for _, sample := range metricSamples {
		if sample.Value != want {
			mismatches = append(mismatches, fmt.Sprintf("%s on %s is %s, want %s",
				metric, runtimeSampleSource(sample), formatRuntimeGaugeValue(sample.Value), formatRuntimeGaugeValue(want)))
		}
	}
	return mismatches
}

func parsePrometheusSample(line string) (prometheusSample, bool) {
	line = strings.TrimSpace(line)
	if line == "" || strings.HasPrefix(line, "#") {
		return prometheusSample{}, false
	}

	metricPart, valuePart, ok := splitPrometheusMetricLine(line)
	if !ok {
		return prometheusSample{}, false
	}
	name := metricPart
	labels := map[string]string{}
	if open := strings.Index(metricPart, "{"); open >= 0 {
		close := strings.LastIndex(metricPart, "}")
		if close <= open {
			return prometheusSample{}, false
		}
		name = metricPart[:open]
		labels = parsePrometheusLabels(metricPart[open+1 : close])
	}
	fields := strings.Fields(valuePart)
	if len(fields) == 0 {
		return prometheusSample{}, false
	}
	value, err := strconv.ParseFloat(fields[0], 64)
	if err != nil {
		return prometheusSample{}, false
	}
	return prometheusSample{Name: name, Labels: labels, Value: value}, true
}

func splitPrometheusMetricLine(line string) (string, string, bool) {
	if close := strings.Index(line, "}"); close >= 0 {
		if close+1 >= len(line) {
			return "", "", false
		}
		return line[:close+1], strings.TrimSpace(line[close+1:]), true
	}
	for i, r := range line {
		if r == ' ' || r == '\t' {
			return line[:i], strings.TrimSpace(line[i:]), true
		}
	}
	return "", "", false
}

func parsePrometheusLabels(raw string) map[string]string {
	labels := map[string]string{}
	for len(raw) > 0 {
		raw = strings.TrimLeft(raw, " \t,")
		if raw == "" {
			break
		}
		eq := strings.Index(raw, "=")
		if eq <= 0 {
			break
		}
		key := strings.TrimSpace(raw[:eq])
		raw = strings.TrimSpace(raw[eq+1:])
		if !strings.HasPrefix(raw, "\"") {
			break
		}
		value, rest := readPrometheusQuotedValue(raw[1:])
		labels[key] = value
		raw = rest
	}
	return labels
}

func readPrometheusQuotedValue(raw string) (string, string) {
	var b strings.Builder
	escaped := false
	for i, r := range raw {
		if escaped {
			switch r {
			case 'n':
				b.WriteRune('\n')
			default:
				b.WriteRune(r)
			}
			escaped = false
			continue
		}
		switch r {
		case '\\':
			escaped = true
		case '"':
			return b.String(), raw[i+1:]
		default:
			b.WriteRune(r)
		}
	}
	return b.String(), ""
}

func boolGauge(value bool) float64 {
	if value {
		return 1
	}
	return 0
}

func runtimeSampleSource(sample prometheusSample) string {
	if sample.Source != "" {
		return sample.Source
	}
	if backend := sample.Labels["backend"]; backend != "" {
		return backend
	}
	return "sample"
}

func formatRuntimeGaugeValue(value float64) string {
	if value == float64(int64(value)) {
		return strconv.FormatInt(int64(value), 10)
	}
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func formatRuntimeExpectations(expect runtimeExpectations, targetBackends []string) string {
	parts := []string{}
	if len(targetBackends) > 0 {
		parts = append(parts, "backend="+strings.Join(targetBackends, ","))
	}
	if expect.model != "" {
		parts = append(parts, "model="+expect.model)
	}
	if expect.reasoningEffort != "" {
		parts = append(parts, "reasoning="+expect.reasoningEffort)
	}
	if expect.defaultMaxTokens != nil {
		parts = append(parts, fmt.Sprintf("default_max_tokens=%d", *expect.defaultMaxTokens))
	}
	if expect.maxToolIterations != nil {
		parts = append(parts, fmt.Sprintf("max_tool_iterations=%d", *expect.maxToolIterations))
	}
	if expect.streaming != nil {
		parts = append(parts, fmt.Sprintf("streaming=%t", *expect.streaming))
	}
	if expect.stubMode != nil {
		parts = append(parts, fmt.Sprintf("stub_mode=%t", *expect.stubMode))
	}
	return strings.Join(parts, " ")
}
