package tui

import (
	"sort"
	"strings"
)

// defaultExpectedEnvVars maps each backend type to the conventional
// env-var names that backend looks for. Surfaced as autocomplete
// suggestions on the create modal's "Secret #N KEY" InputFields so
// users can pick a known KEY without remembering exact spelling.
//
// Not exhaustive — just the high-signal ones a caller is likely to
// reach for. Users can extend per-backend via the `[tui.expected_env_vars]`
// block in config.toml; their additions merge with the built-in list
// (dedup, sort) rather than replace it, so editing a custom value
// can't accidentally drop the canonical suggestions.
//
// Echo deliberately has no entries — it needs no credentials, so
// the autocomplete popup stays out of the way for hello-world.
var defaultExpectedEnvVars = map[string][]string{
	"claude": {
		"ANTHROPIC_API_KEY",
		"CLAUDE_CODE_OAUTH_TOKEN",
		// AWS Bedrock path — the four together unlock Claude on
		// Bedrock without ANTHROPIC_API_KEY.
		"AWS_ACCESS_KEY_ID",
		"AWS_SECRET_ACCESS_KEY",
		"AWS_SESSION_TOKEN",
		"AWS_REGION",
	},
	"openai": {
		"OPENAI_API_KEY",
		"OPENAI_ORG",
		"OPENAI_PROJECT",
		// Azure OpenAI path.
		"AZURE_OPENAI_API_KEY",
		"AZURE_OPENAI_ENDPOINT",
		"AZURE_OPENAI_API_VERSION",
	},
	"codex": {
		"OPENAI_API_KEY",
		"OPENAI_ORG",
		"OPENAI_PROJECT",
		"AZURE_OPENAI_API_KEY",
		"AZURE_OPENAI_ENDPOINT",
		"AZURE_OPENAI_API_VERSION",
	},
	"gemini": {
		"GEMINI_API_KEY",
		"GOOGLE_API_KEY",
		// Vertex AI path (file-based; the env var holds the path).
		"GOOGLE_APPLICATION_CREDENTIALS",
		"GOOGLE_CLOUD_PROJECT",
	},
	// echo: no entries — autocomplete popup stays hidden.
}

// resolvedExpectedEnvVars returns the merged set of env-var names
// for a backend type: the built-in catalog plus any user-supplied
// additions from `[tui.expected_env_vars]` in config.toml. Dedup
// + sort so the autocomplete UI renders deterministically and
// duplicates from a user typo don't double-show.
//
// MERGE rather than REPLACE: a user's custom entries can never
// accidentally hide the built-in suggestions. Removing a built-in
// you don't want is a future feature (block-list semantics) if
// anyone asks.
func resolvedExpectedEnvVars(backendType string, override map[string][]string) []string {
	seen := make(map[string]bool)
	var out []string
	for _, v := range defaultExpectedEnvVars[backendType] {
		v = strings.TrimSpace(v)
		if v == "" || seen[v] {
			continue
		}
		seen[v] = true
		out = append(out, v)
	}
	for _, v := range override[backendType] {
		v = strings.TrimSpace(v)
		if v == "" || seen[v] {
			continue
		}
		seen[v] = true
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

// filterMatchingEnvVars returns the subset of `catalog` whose names
// contain `prefix` (case-insensitive substring match — substring,
// not just prefix, because credential names share common stems
// like AWS_, AZURE_OPENAI_, GOOGLE_APPLICATION_, and substring
// match makes typing "AWS" or "OPENAI" surface every related
// entry without remembering which prefix order to type).
//
// Returns nil for an empty `prefix` so the autocomplete popup
// stays hidden until the user actually starts typing — popping
// up on every focused field is more annoyance than help. Users
// who want to discover the catalog can type a single relevant
// character (e.g. "A" surfaces ANTHROPIC + AWS + AZURE).
func filterMatchingEnvVars(catalog []string, prefix string) []string {
	prefix = strings.TrimSpace(prefix)
	if prefix == "" {
		return nil
	}
	needle := strings.ToLower(prefix)
	var out []string
	for _, name := range catalog {
		if strings.Contains(strings.ToLower(name), needle) {
			out = append(out, name)
		}
	}
	// If the only "match" is the user's exact typed text, suppress
	// the popup — they've already finished, no point showing a
	// one-entry dropdown of what they just typed.
	if len(out) == 1 && strings.EqualFold(out[0], prefix) {
		return nil
	}
	return out
}
