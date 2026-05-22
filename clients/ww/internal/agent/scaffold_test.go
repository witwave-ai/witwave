package agent

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseRepoRef(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in, wantURL, wantDisplay, wantScheme string
		wantErr                              string
	}{
		{
			in:          "witwave-ai/witwave-test",
			wantURL:     "https://github.com/witwave-ai/witwave-test.git",
			wantDisplay: "witwave-ai/witwave-test",
			wantScheme:  "https",
		},
		{
			in:          "github.com/witwave-ai/witwave-test",
			wantURL:     "https://github.com/witwave-ai/witwave-test.git",
			wantDisplay: "witwave-ai/witwave-test",
			wantScheme:  "https",
		},
		{
			in:          "https://github.com/witwave-ai/witwave-test",
			wantURL:     "https://github.com/witwave-ai/witwave-test.git",
			wantDisplay: "witwave-ai/witwave-test",
			wantScheme:  "https",
		},
		{
			in:          "https://github.com/witwave-ai/witwave-test.git",
			wantURL:     "https://github.com/witwave-ai/witwave-test.git",
			wantDisplay: "witwave-ai/witwave-test",
			wantScheme:  "https",
		},
		{
			// Credentials in URL are stripped — we never keep them
			// anywhere that could leak to logs or config.
			in:          "https://ignored:secret@github.com/witwave-ai/witwave-test",
			wantURL:     "https://github.com/witwave-ai/witwave-test.git",
			wantDisplay: "witwave-ai/witwave-test",
			wantScheme:  "https",
		},
		{
			in:          "git@github.com:witwave-ai/witwave-test.git",
			wantURL:     "git@github.com:witwave-ai/witwave-test.git",
			wantDisplay: "witwave-ai/witwave-test",
			wantScheme:  "ssh",
		},
		{
			in:          "git@gitlab.example.com:team/repo",
			wantURL:     "git@gitlab.example.com:team/repo.git",
			wantDisplay: "team/repo",
			wantScheme:  "ssh",
		},
		// Error paths
		{in: "", wantErr: "repo is required"},
		{in: "ftp://github.com/foo/bar", wantErr: "scheme"},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.in, func(t *testing.T) {
			t.Parallel()
			got, err := parseRepoRef(tc.in)
			if tc.wantErr != "" {
				if err == nil {
					t.Fatalf("parseRepoRef(%q) = nil error; want substring %q", tc.in, tc.wantErr)
				}
				if !strings.Contains(err.Error(), tc.wantErr) {
					t.Fatalf("parseRepoRef(%q) err = %q; want substring %q", tc.in, err, tc.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatalf("parseRepoRef(%q) unexpected err: %v", tc.in, err)
			}
			if got.CloneURL != tc.wantURL {
				t.Errorf("CloneURL = %q; want %q", got.CloneURL, tc.wantURL)
			}
			if got.Display != tc.wantDisplay {
				t.Errorf("Display = %q; want %q", got.Display, tc.wantDisplay)
			}
			if got.Scheme != tc.wantScheme {
				t.Errorf("Scheme = %q; want %q", got.Scheme, tc.wantScheme)
			}
		})
	}
}

func TestAgentRepoRoot(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, group, want string
	}{
		{"hello", "", ".agents/hello"},
		{"hello", "dev", ".agents/dev/hello"},
		{"iris", "test", ".agents/test/iris"},
	}
	for _, tc := range cases {
		if got := agentRepoRoot(tc.name, tc.group); got != tc.want {
			t.Errorf("agentRepoRoot(%q, %q) = %q; want %q", tc.name, tc.group, got, tc.want)
		}
	}
}

func TestBuildSkeleton_EchoBackend(t *testing.T) {
	t.Parallel()
	files := buildSkeleton(skeletonOpts{Name: "hello", Backends: []BackendSpec{{Name: "echo", Type: "echo", Port: BackendPort(0)}}, CLIVersion: "0.7.2"})

	paths := collectPaths(files)

	must := []string{
		".agents/hello/README.md",
		".agents/hello/.witwave/backend.yaml",
		".agents/hello/.echo/agent-card.md",
		// HEARTBEAT is a documented exception to SUB-4 — scaffolded on
		// by default so a freshly-created agent has a self-exercising
		// proof-of-life signal out of the box.
		".agents/hello/.witwave/HEARTBEAT.md",
	}
	for _, m := range must {
		if !contains(paths, m) {
			t.Errorf("missing required path %q. got: %v", m, paths)
		}
	}

	// Echo has no behaviour stub — that's an LLM-backend-only concept.
	// Other subsystems (jobs/tasks/triggers/continuations/webhooks) stay
	// dormant per SUB-1..4.
	mustNot := []string{
		".agents/hello/.echo/CLAUDE.md",
		".agents/hello/.echo/AGENTS.md",
		".agents/hello/.echo/GEMINI.md",
		".agents/hello/.witwave/jobs",
		".agents/hello/.witwave/tasks",
		".agents/hello/.witwave/triggers",
		".agents/hello/.witwave/continuations",
		".agents/hello/.witwave/webhooks",
	}
	for _, m := range mustNot {
		for _, p := range paths {
			if strings.HasPrefix(p, m) {
				t.Errorf("unexpected pre-created path %q (dormant SUB-1..4)", p)
			}
		}
	}
}

// TestBuildSkeleton_NoHeartbeat verifies the opt-out path for users
// who genuinely want a silent agent — `--no-heartbeat` must produce
// a skeleton without HEARTBEAT.md.
func TestBuildSkeleton_NoHeartbeat(t *testing.T) {
	t.Parallel()
	files := buildSkeleton(skeletonOpts{
		Name: "silent", Backends: []BackendSpec{{Name: "echo", Type: "echo", Port: BackendPort(0)}}, CLIVersion: "0.7.2", NoHeartbeat: true,
	})
	paths := collectPaths(files)
	for _, p := range paths {
		if strings.Contains(p, "HEARTBEAT.md") {
			t.Errorf("--no-heartbeat was set but scaffold produced %q", p)
		}
	}
}

// TestRenderHeartbeat_ShapeValidates sanity-checks the emitted
// HEARTBEAT.md against the frontmatter shape the harness expects
// (`schedule`, `enabled`, plus a prompt body). If the harness's
// accepted shape changes, this test catches the drift before a user
// hits it.
func TestRenderHeartbeat_ShapeValidates(t *testing.T) {
	t.Parallel()
	out := renderHeartbeat()
	for _, want := range []string{
		"schedule: \"0 * * * *\"", // hourly at minute 0
		"enabled: true",
		"HEARTBEAT_OK",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("renderHeartbeat output missing %q. got:\n%s", want, out)
		}
	}
}

func TestBuildSkeleton_LLMBackends(t *testing.T) {
	t.Parallel()
	cases := []struct {
		backend, behaviorFile string
	}{
		{"claude", "CLAUDE.md"},
		{"openai", "AGENTS.md"},
		{"gemini", "GEMINI.md"},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.backend, func(t *testing.T) {
			t.Parallel()
			files := buildSkeleton(skeletonOpts{
				Name:       "hello",
				Backends:   []BackendSpec{{Name: tc.backend, Type: tc.backend, Port: BackendPort(0)}},
				CLIVersion: "0.7.2",
			})
			paths := collectPaths(files)
			want := ".agents/hello/." + tc.backend + "/" + tc.behaviorFile
			if !contains(paths, want) {
				t.Errorf("missing %q for backend %q. got: %v", want, tc.backend, paths)
			}
		})
	}
}

func TestBuildSkeleton_WithGroup(t *testing.T) {
	t.Parallel()
	files := buildSkeleton(skeletonOpts{
		Name:       "hello",
		Group:      "prod",
		Backends:   []BackendSpec{{Name: "echo", Type: "echo", Port: BackendPort(0)}},
		CLIVersion: "0.7.2",
	})
	paths := collectPaths(files)
	for _, p := range paths {
		if !strings.HasPrefix(p, ".agents/prod/hello/") {
			t.Errorf("expected all paths under .agents/prod/hello/, got %q", p)
		}
	}
}

func TestWriteSkeletonMerge(t *testing.T) {
	t.Parallel()

	t.Run("fresh dir — everything is Added", func(t *testing.T) {
		t.Parallel()
		root := t.TempDir()
		files := []skeletonFile{
			{Path: "README.md", Content: "hello"},
			{Path: ".witwave/backend.yaml", Content: "backend: {}"},
		}
		outcome, err := writeSkeletonMerge(root, files, false)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(outcome.Added) != 2 {
			t.Errorf("expected 2 Added, got %d (%v)", len(outcome.Added), outcome.Added)
		}
		if len(outcome.Preserved) != 0 || len(outcome.Overwrote) != 0 || len(outcome.Identical) != 0 {
			t.Errorf("unexpected non-Added buckets: %+v", outcome)
		}
	})

	t.Run("exact match — everything is Identical", func(t *testing.T) {
		t.Parallel()
		root := t.TempDir()
		files := []skeletonFile{{Path: "README.md", Content: "hello"}}
		_, _ = writeSkeletonMerge(root, files, false) // prime
		outcome, err := writeSkeletonMerge(root, files, false)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(outcome.Identical) != 1 {
			t.Errorf("expected 1 Identical, got %+v", outcome)
		}
		if len(outcome.Added)+len(outcome.Preserved)+len(outcome.Overwrote) != 0 {
			t.Errorf("unexpected writes/overwrites: %+v", outcome)
		}
	})

	t.Run("drift without --force — Preserved", func(t *testing.T) {
		t.Parallel()
		root := t.TempDir()
		// Prime disk with a user-edited version.
		userContent := "# My own README\nCustom prose here.\n"
		if err := writeFile(root, "README.md", userContent); err != nil {
			t.Fatalf("prime: %v", err)
		}
		outcome, err := writeSkeletonMerge(root, []skeletonFile{
			{Path: "README.md", Content: "scaffold template content"},
		}, false)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(outcome.Preserved) != 1 {
			t.Errorf("expected 1 Preserved, got %+v", outcome)
		}
		// File on disk should still have the user's edits.
		got := readFile(t, root, "README.md")
		if got != userContent {
			t.Errorf("user edits not preserved.\n got: %q\nwant: %q", got, userContent)
		}
	})

	t.Run("drift with --force — Overwrote", func(t *testing.T) {
		t.Parallel()
		root := t.TempDir()
		if err := writeFile(root, "README.md", "user edits"); err != nil {
			t.Fatalf("prime: %v", err)
		}
		outcome, err := writeSkeletonMerge(root, []skeletonFile{
			{Path: "README.md", Content: "scaffold template"},
		}, true)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(outcome.Overwrote) != 1 {
			t.Errorf("expected 1 Overwrote, got %+v", outcome)
		}
		got := readFile(t, root, "README.md")
		if got != "scaffold template" {
			t.Errorf("file not overwritten: %q", got)
		}
	})

	t.Run("partial drift — mix of buckets", func(t *testing.T) {
		t.Parallel()
		root := t.TempDir()
		// Prime one file with user edits, one with identical content,
		// leave one missing.
		_ = writeFile(root, "edited.md", "user's version")
		_ = writeFile(root, "matching.md", "same")
		outcome, err := writeSkeletonMerge(root, []skeletonFile{
			{Path: "edited.md", Content: "template"},
			{Path: "matching.md", Content: "same"},
			{Path: "new.md", Content: "freshly added"},
		}, false)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(outcome.Added) != 1 || outcome.Added[0] != "new.md" {
			t.Errorf("Added = %v; want [new.md]", outcome.Added)
		}
		if len(outcome.Identical) != 1 || outcome.Identical[0] != "matching.md" {
			t.Errorf("Identical = %v; want [matching.md]", outcome.Identical)
		}
		if len(outcome.Preserved) != 1 || outcome.Preserved[0] != "edited.md" {
			t.Errorf("Preserved = %v; want [edited.md]", outcome.Preserved)
		}
	})

	t.Run("user-added files never touched", func(t *testing.T) {
		t.Parallel()
		root := t.TempDir()
		_ = writeFile(root, ".witwave/jobs/custom-job.md", "user job content")
		_, err := writeSkeletonMerge(root, []skeletonFile{
			{Path: "README.md", Content: "scaffold"},
		}, true)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		// User-added file must survive --force untouched, since it's
		// not in the skeleton list.
		got := readFile(t, root, ".witwave/jobs/custom-job.md")
		if got != "user job content" {
			t.Errorf("user-added file was modified: %q", got)
		}
	})
}

func TestBehaviorFileName(t *testing.T) {
	t.Parallel()
	cases := []struct {
		backend string
		want    string
		ok      bool
	}{
		{"claude", "CLAUDE.md", true},
		{"openai", "AGENTS.md", true},
		{"gemini", "GEMINI.md", true},
		{"echo", "", false},
		{"unknown", "", false},
	}
	for _, tc := range cases {
		got, ok := behaviorFileName(tc.backend)
		if got != tc.want || ok != tc.ok {
			t.Errorf("behaviorFileName(%q) = (%q, %v); want (%q, %v)",
				tc.backend, got, ok, tc.want, tc.ok)
		}
	}
}

func TestValidateScaffoldOptions(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name    string
		opts    ScaffoldOptions
		wantErr string
	}{
		{
			name:    "missing name",
			opts:    ScaffoldOptions{Repo: "a/b"},
			wantErr: "empty",
		},
		{
			name:    "missing repo",
			opts:    ScaffoldOptions{Name: "hello"},
			wantErr: "repo is required",
		},
		{
			name:    "bad agent name",
			opts:    ScaffoldOptions{Name: "Hello", Repo: "a/b"},
			wantErr: "DNS-1123",
		},
		{
			name:    "bad group name",
			opts:    ScaffoldOptions{Name: "hello", Group: "Prod", Repo: "a/b"},
			wantErr: "group name",
		},
		{
			name:    "unknown backend",
			opts:    ScaffoldOptions{Name: "hello", Repo: "a/b", Backends: []BackendSpec{{Name: "mistral", Type: "mistral"}}},
			wantErr: "unknown type",
		},
		{
			name: "happy path — echo",
			opts: ScaffoldOptions{Name: "hello", Repo: "a/b"},
		},
		{
			name: "happy path — claude with group",
			opts: ScaffoldOptions{Name: "iris", Group: "prod", Repo: "a/b", Backends: []BackendSpec{{Name: "claude", Type: "claude", Port: BackendPort(0)}}},
		},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			err := validateScaffoldOptions(&tc.opts)
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatalf("expected error containing %q; got nil", tc.wantErr)
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("error = %q; want substring %q", err, tc.wantErr)
			}
		})
	}
}

func TestEnvTokenPrecedence(t *testing.T) {
	// Stub environment so the GH_TOKEN / GITHUB_TOKEN / GIT_TOKEN
	// precedence is verifiable without touching the real env.
	original := envLookup
	defer func() { envLookup = original }()

	t.Run("GITHUB_TOKEN wins over GH_TOKEN", func(t *testing.T) {
		envLookup = func(k string) string {
			switch k {
			case "GITHUB_TOKEN":
				return "github-token"
			case "GH_TOKEN":
				return "gh-token"
			}
			return ""
		}
		if got, want := envToken(), "github-token"; got != want {
			t.Errorf("envToken = %q; want %q", got, want)
		}
	})

	t.Run("falls through to GH_TOKEN when GITHUB_TOKEN empty", func(t *testing.T) {
		envLookup = func(k string) string {
			if k == "GH_TOKEN" {
				return "gh-token"
			}
			return ""
		}
		if got, want := envToken(), "gh-token"; got != want {
			t.Errorf("envToken = %q; want %q", got, want)
		}
	})

	t.Run("returns empty when all unset", func(t *testing.T) {
		envLookup = func(string) string { return "" }
		if got := envToken(); got != "" {
			t.Errorf("envToken = %q; want empty", got)
		}
	})
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func collectPaths(files []skeletonFile) []string {
	out := make([]string, 0, len(files))
	for _, f := range files {
		out = append(out, f.Path)
	}
	return out
}

func contains(haystack []string, needle string) bool {
	for _, h := range haystack {
		if h == needle {
			return true
		}
	}
	return false
}

// writeFile creates parents-as-needed and writes contents at root/rel.
// Helper for TestWriteSkeletonMerge to prime the tempdir with user-like
// content.
func writeFile(root, rel, contents string) error {
	full := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		return err
	}
	return os.WriteFile(full, []byte(contents), 0o644)
}

// readFile reads root/rel and returns its contents as a string, or
// fails the test with a clear error. Deliberately test-only so the
// happy-path asserts stay terse.
func readFile(t *testing.T, root, rel string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatalf("read %s: %v", rel, err)
	}
	return string(b)
}
