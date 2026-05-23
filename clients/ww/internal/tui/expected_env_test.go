// Starter unit tests for the pure helpers in the TUI package (#1742).
// Begins coverage on the test-friendly subset called out in the gap:
// the expected_env catalog, the env-var filter, and parseBoolOrDefault.
// Larger surfaces (snapshot-swap, goroutine lifecycle) are deferred.

package tui

import (
	"sort"
	"testing"
)

func TestResolvedExpectedEnvVars_BuiltinShape(t *testing.T) {
	for _, backend := range []string{"claude", "openai", "codex", "gemini"} {
		got := resolvedExpectedEnvVars(backend, nil)
		if len(got) == 0 {
			t.Errorf("%s: expected non-empty built-in catalog", backend)
		}
		// Result is sorted.
		s := append([]string(nil), got...)
		sort.Strings(s)
		for i := range got {
			if got[i] != s[i] {
				t.Errorf("%s: result not sorted: %v", backend, got)
				break
			}
		}
	}
}

func TestResolvedExpectedEnvVars_EchoEmpty(t *testing.T) {
	got := resolvedExpectedEnvVars("echo", nil)
	if len(got) != 0 {
		t.Errorf("echo backend should have no entries, got %v", got)
	}
}

func TestResolvedExpectedEnvVars_UnknownBackendIsEmpty(t *testing.T) {
	got := resolvedExpectedEnvVars("does-not-exist", nil)
	if len(got) != 0 {
		t.Errorf("unknown backend should have no entries, got %v", got)
	}
}

func TestResolvedExpectedEnvVars_OverrideMergesAndSorts(t *testing.T) {
	override := map[string][]string{
		"claude": {"MY_CUSTOM_KEY", "ANOTHER_ONE"},
	}
	got := resolvedExpectedEnvVars("claude", override)
	// Built-in entries must still be present.
	if !contains(got, "ANTHROPIC_API_KEY") {
		t.Errorf("expected built-in ANTHROPIC_API_KEY in result, got %v", got)
	}
	// Override entries must be present.
	if !contains(got, "MY_CUSTOM_KEY") {
		t.Errorf("expected override MY_CUSTOM_KEY in result, got %v", got)
	}
	// Output stays sorted.
	s := append([]string(nil), got...)
	sort.Strings(s)
	for i := range got {
		if got[i] != s[i] {
			t.Errorf("merged result not sorted: %v", got)
			break
		}
	}
}

func TestResolvedExpectedEnvVars_OverrideDedupsBuiltin(t *testing.T) {
	override := map[string][]string{
		"claude": {"ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"},
	}
	got := resolvedExpectedEnvVars("claude", override)
	count := 0
	for _, v := range got {
		if v == "ANTHROPIC_API_KEY" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected ANTHROPIC_API_KEY exactly once in dedup result, got %d (%v)", count, got)
	}
}

func TestResolvedExpectedEnvVars_OverrideTrimsEmpty(t *testing.T) {
	override := map[string][]string{
		"claude": {"  ", "", "VALID_KEY"},
	}
	got := resolvedExpectedEnvVars("claude", override)
	if !contains(got, "VALID_KEY") {
		t.Errorf("expected VALID_KEY in result, got %v", got)
	}
	if contains(got, "") || contains(got, "  ") {
		t.Errorf("empty / whitespace entries must be filtered, got %v", got)
	}
}

func TestFilterMatchingEnvVars_EmptyPrefixReturnsNil(t *testing.T) {
	if got := filterMatchingEnvVars([]string{"ANTHROPIC_API_KEY"}, ""); got != nil {
		t.Errorf("empty prefix must return nil, got %v", got)
	}
	if got := filterMatchingEnvVars([]string{"ANTHROPIC_API_KEY"}, "  "); got != nil {
		t.Errorf("whitespace-only prefix must return nil, got %v", got)
	}
}

func TestFilterMatchingEnvVars_SubstringMatch(t *testing.T) {
	catalog := []string{"ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AZURE_OPENAI_API_KEY"}
	got := filterMatchingEnvVars(catalog, "openai")
	if len(got) != 1 || got[0] != "AZURE_OPENAI_API_KEY" {
		t.Errorf("substring 'openai' should match only AZURE_OPENAI_API_KEY, got %v", got)
	}
}

func TestFilterMatchingEnvVars_CaseInsensitive(t *testing.T) {
	catalog := []string{"ANTHROPIC_API_KEY"}
	if got := filterMatchingEnvVars(catalog, "AnTh"); len(got) != 1 {
		t.Errorf("case-insensitive substring match expected, got %v", got)
	}
}

func TestFilterMatchingEnvVars_SingleExactMatchSuppressed(t *testing.T) {
	// When the only "match" is the user's exact typed text, the popup
	// must be suppressed (one-entry dropdown of what they just typed
	// is annoyance, not help).
	got := filterMatchingEnvVars([]string{"ANTHROPIC_API_KEY"}, "ANTHROPIC_API_KEY")
	if got != nil {
		t.Errorf("single exact match must return nil, got %v", got)
	}
}

func TestFilterMatchingEnvVars_NoMatchEmptyResult(t *testing.T) {
	got := filterMatchingEnvVars([]string{"ANTHROPIC_API_KEY"}, "ZZZ")
	if got != nil {
		t.Errorf("no-match must return nil, got %v", got)
	}
}

func TestParseBoolOrDefault(t *testing.T) {
	cases := []struct {
		input    string
		fallback bool
		want     bool
	}{
		{"true", false, true},
		{"True", false, true},
		{"1", false, true},
		{"yes", false, true},
		{"y", false, true},
		{"on", false, true},
		{"false", true, false},
		{"FALSE", true, false},
		{"0", true, false},
		{"no", true, false},
		{"off", true, false},
		// Typos fall back.
		{"ywes", true, true},
		{"  ", false, false},
		{"", true, true},
	}
	for _, tc := range cases {
		t.Run(tc.input, func(t *testing.T) {
			if got := parseBoolOrDefault(tc.input, tc.fallback); got != tc.want {
				t.Errorf("parseBoolOrDefault(%q, %v) = %v, want %v",
					tc.input, tc.fallback, got, tc.want)
			}
		})
	}
}

// helper

func contains(haystack []string, needle string) bool {
	for _, v := range haystack {
		if v == needle {
			return true
		}
	}
	return false
}
