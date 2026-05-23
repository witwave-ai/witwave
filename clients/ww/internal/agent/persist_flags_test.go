// Tests for the pure-helper functions in persist_flags.go that the
// `ww agent create --persist`, `--persist-mount`, and
// `--with-persistence` flag handlers compose over user input.
// Mirrors the table-driven shape used in backend_spec_test.go
// (TestParseBackendSpecs) — the cluster-side resolution path
// (ApplyBackendPersist) needs BackendSpec fixtures alongside the
// parsed maps so it's left for a higher-tier sweep; this file
// stays at "pure string/map → map/error" coverage.
//
// Drift in any of these parsers would re-shape how every
// `ww agent create --persist*` invocation maps to a CR's PVC
// declarations and mount lists. Every error-branch's substring is
// pinned so users keep getting the targeted message.
package agent

import (
	"strings"
	"testing"
)

// TestParseBackendPersistMounts pins the `--persist-mount` parser.
// Contract:
//   - empty input → empty map, no error.
//   - "<name>=<sub>:<mount>" → one entry keyed by name.
//   - multiple entries for the same name → all appended in order.
//   - empty value, missing `=`, missing `:`, empty halves → error.
//   - mountpath without leading `/` → error (kubelet would reject).
//   - subpath with leading `/` or containing `..` → error.
//   - invalid name → wraps ValidateName's error.
//   - whitespace around tokens is trimmed.
func TestParseBackendPersistMounts(t *testing.T) {
	t.Run("nil input returns empty map", func(t *testing.T) {
		got, err := ParseBackendPersistMounts(nil)
		if err != nil {
			t.Fatalf("err = %v, want nil", err)
		}
		if len(got) != 0 {
			t.Errorf("got = %v, want empty map", got)
		}
	})

	t.Run("single entry keyed by name", func(t *testing.T) {
		got, err := ParseBackendPersistMounts([]string{"claude=projects:/home/agent/.claude/projects"})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		mounts, ok := got["claude"]
		if !ok || len(mounts) != 1 {
			t.Fatalf("got = %v, want one claude entry", got)
		}
		if mounts[0].SubPath != "projects" || mounts[0].MountPath != "/home/agent/.claude/projects" {
			t.Errorf("entry = %+v", mounts[0])
		}
	})

	t.Run("repeated entries for same name append in order", func(t *testing.T) {
		got, err := ParseBackendPersistMounts([]string{
			"claude=projects:/home/agent/.claude/projects",
			"claude=sessions:/home/agent/.claude/sessions",
		})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		mounts := got["claude"]
		if len(mounts) != 2 {
			t.Fatalf("len = %d, want 2", len(mounts))
		}
		if mounts[0].SubPath != "projects" || mounts[1].SubPath != "sessions" {
			t.Errorf("order = %+v", mounts)
		}
	})

	t.Run("whitespace around tokens trimmed", func(t *testing.T) {
		got, err := ParseBackendPersistMounts([]string{"  claude = projects : /home/agent/.claude/projects  "})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["claude"][0].SubPath != "projects" || got["claude"][0].MountPath != "/home/agent/.claude/projects" {
			t.Errorf("trim failed: %+v", got["claude"][0])
		}
	})

	errCases := []struct {
		name    string
		in      string
		wantSub string
	}{
		{"empty value", "", "empty value"},
		{"missing equals", "claude:projects:/home/agent/.claude/projects", "form is <backend-name>=<subpath>:<mountpath>"},
		{"missing colon in body", "claude=projects-only", "form is <backend-name>=<subpath>:<mountpath>"},
		{"empty subpath", "claude=:/home/agent/.claude/projects", "form is <backend-name>=<subpath>:<mountpath>"},
		{"empty mountpath", "claude=projects:", "subpath and mountpath are both required"},
		{"relative mountpath", "claude=projects:home/agent/.claude/projects", "must be absolute"},
		{"absolute subpath", "claude=/abs:/home/agent/.claude/projects", "must be relative"},
		{"subpath with parent traversal", "claude=../escape:/home/agent/.claude/projects", "must not contain"},
		{"invalid name (uppercase)", "Claude=projects:/home/agent/.claude/projects", "backend"},
	}
	for _, tc := range errCases {
		tc := tc
		t.Run("error: "+tc.name, func(t *testing.T) {
			_, err := ParseBackendPersistMounts([]string{tc.in})
			if err == nil {
				t.Fatalf("err = nil, want error containing %q", tc.wantSub)
			}
			if !strings.Contains(err.Error(), tc.wantSub) {
				t.Errorf("err = %q, want substring %q", err.Error(), tc.wantSub)
			}
		})
	}
}

// TestParseBackendPersist pins the `--persist` parser. Contract:
//   - "<name>" → spec with empty Size (defaults resolved later).
//   - "<name>=<size>" → spec with Size set, no class.
//   - "<name>=<size>@<class>" → both set.
//   - empty entry, empty size after `=`, empty halves around `@` → error.
//   - duplicate name → error.
//   - invalid name → error wrapping ValidateName.
//   - whitespace trimmed.
func TestParseBackendPersist(t *testing.T) {
	t.Run("nil input returns empty map", func(t *testing.T) {
		got, err := ParseBackendPersist(nil)
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if len(got) != 0 {
			t.Errorf("got = %v, want empty map", got)
		}
	})

	t.Run("bare name leaves size empty", func(t *testing.T) {
		got, err := ParseBackendPersist([]string{"claude"})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		spec, ok := got["claude"]
		if !ok {
			t.Fatalf("got = %v, want claude entry", got)
		}
		if spec.Size != "" || spec.StorageClassName != "" {
			t.Errorf("spec = %+v, want empty Size+Class", spec)
		}
	})

	t.Run("name=size sets Size only", func(t *testing.T) {
		got, err := ParseBackendPersist([]string{"claude=20Gi"})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["claude"].Size != "20Gi" {
			t.Errorf("Size = %q, want 20Gi", got["claude"].Size)
		}
		if got["claude"].StorageClassName != "" {
			t.Errorf("Class = %q, want empty", got["claude"].StorageClassName)
		}
	})

	t.Run("name=size@class sets both", func(t *testing.T) {
		got, err := ParseBackendPersist([]string{"claude=20Gi@fast-ssd"})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["claude"].Size != "20Gi" || got["claude"].StorageClassName != "fast-ssd" {
			t.Errorf("spec = %+v", got["claude"])
		}
	})

	t.Run("multiple distinct names accumulate", func(t *testing.T) {
		got, err := ParseBackendPersist([]string{"claude=20Gi", "openai=5Gi@slow-hdd"})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if len(got) != 2 || got["claude"].Size != "20Gi" || got["openai"].StorageClassName != "slow-hdd" {
			t.Errorf("got = %+v", got)
		}
	})

	t.Run("whitespace trimmed", func(t *testing.T) {
		got, err := ParseBackendPersist([]string{"  claude = 20Gi @ fast-ssd  "})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["claude"].Size != "20Gi" || got["claude"].StorageClassName != "fast-ssd" {
			t.Errorf("spec = %+v", got["claude"])
		}
	})

	errCases := []struct {
		name    string
		in      []string
		wantSub string
	}{
		{"empty value", []string{""}, "empty value"},
		{"empty size after equals", []string{"claude="}, "empty size after '='"},
		{"empty size before at", []string{"claude=@fast-ssd"}, "empty size before '@'"},
		{"empty class after at", []string{"claude=20Gi@"}, "empty storage class after '@'"},
		{"invalid name (uppercase)", []string{"Claude=20Gi"}, "backend"},
		{"duplicate name", []string{"claude=20Gi", "claude=30Gi"}, "duplicate"},
	}
	for _, tc := range errCases {
		tc := tc
		t.Run("error: "+tc.name, func(t *testing.T) {
			_, err := ParseBackendPersist(tc.in)
			if err == nil {
				t.Fatalf("err = nil, want error containing %q", tc.wantSub)
			}
			if !strings.Contains(err.Error(), tc.wantSub) {
				t.Errorf("err = %q, want substring %q", err.Error(), tc.wantSub)
			}
		})
	}
}

// TestResolvePersistDefaults pins the per-type defaults resolver.
// Contract:
//   - empty config → code-defaults only (every BackendStorageSizeDefaults
//     key shows up with that size + the BackendStoragePresets mount list
//     if any).
//   - config Size override beats code default; same for StorageClassName.
//   - config Mounts override (non-empty slice) beats the preset; an empty
//     Mounts slice falls back to the preset.
//   - a type that's only in configDefaults (not in either package global)
//     still shows up in the output.
//   - returned slices are copies (mutating them does not affect the
//     source maps' state).
func TestResolvePersistDefaults(t *testing.T) {
	t.Run("empty config returns code defaults", func(t *testing.T) {
		got := ResolvePersistDefaults(nil)
		for typeName, wantSize := range BackendStorageSizeDefaults {
			spec, ok := got[typeName]
			if !ok {
				t.Errorf("%q missing from result", typeName)
				continue
			}
			if spec.Size != wantSize {
				t.Errorf("%q size = %q, want %q", typeName, spec.Size, wantSize)
			}
		}
		// Claude's preset includes native state plus Witwave logs and state.
		if mounts := got["claude"].Mounts; len(mounts) != 6 {
			t.Errorf("claude mounts = %d, want 6", len(mounts))
		}
		for _, typ := range []string{"claude", "openai", "codex", "gemini"} {
			if !hasMount(got[typ].Mounts, "logs", "/home/agent/logs") {
				t.Errorf("%s preset missing logs mount: %+v", typ, got[typ].Mounts)
			}
			if !hasMount(got[typ].Mounts, "state", "/home/agent/state") {
				t.Errorf("%s preset missing state mount: %+v", typ, got[typ].Mounts)
			}
		}
	})

	t.Run("config Size wins over code default", func(t *testing.T) {
		got := ResolvePersistDefaults(map[string]PersistDefaults{
			"claude": {Size: "50Gi"},
		})
		if got["claude"].Size != "50Gi" {
			t.Errorf("size = %q, want 50Gi", got["claude"].Size)
		}
	})

	t.Run("config StorageClassName wins over empty default", func(t *testing.T) {
		got := ResolvePersistDefaults(map[string]PersistDefaults{
			"claude": {StorageClassName: "fast-ssd"},
		})
		if got["claude"].StorageClassName != "fast-ssd" {
			t.Errorf("class = %q, want fast-ssd", got["claude"].StorageClassName)
		}
		// Size still falls through to the code default.
		if got["claude"].Size != BackendStorageSizeDefaults["claude"] {
			t.Errorf("size = %q, want %q", got["claude"].Size, BackendStorageSizeDefaults["claude"])
		}
	})

	t.Run("non-empty config Mounts replace preset", func(t *testing.T) {
		got := ResolvePersistDefaults(map[string]PersistDefaults{
			"claude": {Mounts: []BackendStorageMount{{SubPath: "x", MountPath: "/x"}}},
		})
		mounts := got["claude"].Mounts
		if len(mounts) != 1 || mounts[0].SubPath != "x" {
			t.Errorf("mounts = %+v, want single x:/x", mounts)
		}
	})

	t.Run("empty config Mounts fall back to preset", func(t *testing.T) {
		got := ResolvePersistDefaults(map[string]PersistDefaults{
			"claude": {Mounts: nil},
		})
		if len(got["claude"].Mounts) != 6 {
			t.Errorf("mounts = %d, want preset of 6", len(got["claude"].Mounts))
		}
	})

	t.Run("config-only type still appears in result", func(t *testing.T) {
		got := ResolvePersistDefaults(map[string]PersistDefaults{
			"custom": {Size: "1Gi"},
		})
		spec, ok := got["custom"]
		if !ok {
			t.Fatalf("custom missing from result: %v", got)
		}
		if spec.Size != "1Gi" {
			t.Errorf("custom size = %q, want 1Gi", spec.Size)
		}
	})

	t.Run("returned mount slices are copies, not aliases", func(t *testing.T) {
		input := []BackendStorageMount{{SubPath: "x", MountPath: "/x"}}
		got := ResolvePersistDefaults(map[string]PersistDefaults{
			"claude": {Mounts: input},
		})
		// Mutate the result; the input slice's content must not change.
		got["claude"].Mounts[0].SubPath = "MUTATED"
		if input[0].SubPath != "x" {
			t.Errorf("input slice was aliased (got %q, want %q)", input[0].SubPath, "x")
		}
	})
}

func hasMount(mounts []BackendStorageMount, subPath, mountPath string) bool {
	for _, m := range mounts {
		if m.SubPath == subPath && m.MountPath == mountPath {
			return true
		}
	}
	return false
}

// TestExpandWithPersistence pins the --with-persistence fan-out.
// Contract:
//   - explicit entries take priority over the fan-out (same name in
//     both => explicit wins).
//   - backends not in explicit are filled from defaults[type].
//   - backend with type that has no default size → error mentioning
//     the type + suggesting [persist.defaults.<type>] or --persist.
//   - empty backend list with empty explicit map → empty result.
func TestExpandWithPersistence(t *testing.T) {
	defaults := map[string]BackendStorageSpec{
		"claude": {Size: "10Gi"},
		"echo":   {Size: "1Gi"},
	}

	t.Run("backends fan out from defaults when not in explicit", func(t *testing.T) {
		backends := []BackendSpec{{Name: "primary", Type: "claude"}, {Name: "fallback", Type: "echo"}}
		got, err := ExpandWithPersistence(backends, defaults, nil)
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["primary"].Size != "10Gi" || got["fallback"].Size != "1Gi" {
			t.Errorf("got = %+v", got)
		}
	})

	t.Run("explicit entries take priority over fan-out", func(t *testing.T) {
		backends := []BackendSpec{{Name: "primary", Type: "claude"}}
		explicit := map[string]BackendStorageSpec{"primary": {Size: "100Gi"}}
		got, err := ExpandWithPersistence(backends, defaults, explicit)
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["primary"].Size != "100Gi" {
			t.Errorf("primary size = %q, want 100Gi (explicit wins)", got["primary"].Size)
		}
	})

	t.Run("backend with no type-default returns error", func(t *testing.T) {
		backends := []BackendSpec{{Name: "weird", Type: "unknown-type"}}
		_, err := ExpandWithPersistence(backends, defaults, nil)
		if err == nil {
			t.Fatalf("err = nil, want error")
		}
		// Error must mention the offending type and the actionable
		// remedy (config block name + --persist alternative).
		msg := err.Error()
		for _, sub := range []string{"unknown-type", "weird", "persist.defaults"} {
			if !strings.Contains(msg, sub) {
				t.Errorf("err = %q, want substring %q", msg, sub)
			}
		}
	})

	t.Run("empty backend list + empty explicit map → empty result", func(t *testing.T) {
		got, err := ExpandWithPersistence(nil, defaults, nil)
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if len(got) != 0 {
			t.Errorf("got = %+v, want empty", got)
		}
	})

	t.Run("explicit entry for non-declared backend still included", func(t *testing.T) {
		// Backends list empty but explicit carries an entry — the function
		// copies all of explicit into the output even if no backend with
		// that name was passed (cross-flag validation is ApplyBackendPersist's
		// job, not this fan-out helper's).
		got, err := ExpandWithPersistence(nil, defaults, map[string]BackendStorageSpec{"orphan": {Size: "1Gi"}})
		if err != nil {
			t.Fatalf("err = %v", err)
		}
		if got["orphan"].Size != "1Gi" {
			t.Errorf("got = %+v", got)
		}
	})
}
