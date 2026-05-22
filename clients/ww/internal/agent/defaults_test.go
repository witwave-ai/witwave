package agent

import "testing"

func TestHarnessImage(t *testing.T) {
	t.Parallel()
	cases := []struct {
		version, want string
	}{
		{"0.6.0", "ghcr.io/witwave-ai/images/harness:0.6.0"},
		{"v0.6.0", "ghcr.io/witwave-ai/images/harness:0.6.0"},
		{"", "ghcr.io/witwave-ai/images/harness:latest"},
		{"dev", "ghcr.io/witwave-ai/images/harness:latest"},
		{"unknown", "ghcr.io/witwave-ai/images/harness:latest"},
	}
	for _, tc := range cases {
		if got := HarnessImage(tc.version); got != tc.want {
			t.Errorf("HarnessImage(%q) = %q; want %q", tc.version, got, tc.want)
		}
	}
}

func TestBackendImage(t *testing.T) {
	t.Parallel()
	if got, want := BackendImage("echo", "0.6.0"), "ghcr.io/witwave-ai/images/echo:0.6.0"; got != want {
		t.Errorf("BackendImage(echo, 0.6.0) = %q; want %q", got, want)
	}
	if got, want := BackendImage("claude", "dev"), "ghcr.io/witwave-ai/images/claude:latest"; got != want {
		t.Errorf("BackendImage(claude, dev) = %q; want %q", got, want)
	}
}

func TestIsKnownBackend(t *testing.T) {
	t.Parallel()
	for _, b := range []string{"echo", "claude", "openai", "codex", "gemini"} {
		if !IsKnownBackend(b) {
			t.Errorf("IsKnownBackend(%q) = false; want true", b)
		}
	}
	for _, b := range []string{"", "mistral", "ECHO", "OpenAI"} {
		if IsKnownBackend(b) {
			t.Errorf("IsKnownBackend(%q) = true; want false", b)
		}
	}
}

func TestIsDevVersion(t *testing.T) {
	t.Parallel()
	for _, v := range []string{"", "dev", "unknown", "  dev  "} {
		if !IsDevVersion(v) {
			t.Errorf("IsDevVersion(%q) = false; want true", v)
		}
	}
	for _, v := range []string{"0.6.0", "v0.6.0", "1.0.0-beta.1"} {
		if IsDevVersion(v) {
			t.Errorf("IsDevVersion(%q) = true; want false", v)
		}
	}
}

func TestResolveNamespace(t *testing.T) {
	t.Parallel()
	cases := []struct {
		flag, ctx, want string
	}{
		{"explicit", "ctx-ns", "explicit"},
		{"", "ctx-ns", "ctx-ns"},
		{"", "", DefaultAgentNamespace},
		{"explicit", "", "explicit"},
	}
	for _, tc := range cases {
		if got := ResolveNamespace(tc.flag, tc.ctx); got != tc.want {
			t.Errorf("ResolveNamespace(%q, %q) = %q; want %q", tc.flag, tc.ctx, got, tc.want)
		}
	}
}
