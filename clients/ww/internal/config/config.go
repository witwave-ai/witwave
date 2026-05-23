// Package config loads ww's layered configuration: defaults → file →
// env vars → command-line flags.
//
// File discovery uses Viper (github.com/spf13/viper) so we get a
// battle-tested multi-path search + a write-back path for free (see
// writer.go). Env-var and flag layering stays hand-rolled on top: Viper
// can't cleanly model our "WW_BASE_URL wins regardless of active
// profile" semantics because it binds env names to nested keys, and
// our env names intentionally omit the profile segment.
package config

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/spf13/viper"
)

// Profile is a single named set of connection settings in the config
// file. Only known fields are honored; unknown keys are ignored so
// future ww versions can add fields without breaking older callers.
type Profile struct {
	BaseURL  string `mapstructure:"base_url"`
	Token    string `mapstructure:"token"`
	RunToken string `mapstructure:"run_token"`
	Timeout  string `mapstructure:"timeout"` // duration string, optional
}

// UpdateConfig is the on-disk [update] block that controls the
// "newer version available" check and optional delegated upgrade. All
// fields are optional; zero values resolve to package defaults in the
// update package.
type UpdateConfig struct {
	// Mode is one of off, notify, prompt, auto. Empty string resolves
	// to the notify default. See the update package for semantics.
	Mode string `mapstructure:"mode"`
	// Interval is the duration string ("24h", "1h", "7d"-style). Empty
	// string resolves to the 24h default. Zero or negative values are
	// rejected by the update package's parser and fall back.
	Interval string `mapstructure:"interval"`
	// Channel selects stable-only vs include-prerelease candidates.
	// One of stable, beta. Empty string resolves to stable.
	Channel string `mapstructure:"channel"`
}

// File is the on-disk TOML shape.
type File struct {
	Profile map[string]Profile `mapstructure:"profile"`
	Update  UpdateConfig       `mapstructure:"update"`
	TUI     TUIConfig          `mapstructure:"tui"`
	Persist PersistConfig      `mapstructure:"persist"`
}

// PersistConfig is the on-disk [persist] block. Currently carries
// per-backend-type defaults consumed by `ww agent create
// --with-persistence` and the `--persist <name>` (size-omitted)
// shortcut.
type PersistConfig struct {
	// Defaults is keyed by backend type (echo, claude, openai, codex, gemini).
	// Each entry overrides the code-level defaults
	// (BackendStorageSizeDefaults / BackendStoragePresets) for that
	// type. Either field can be omitted; resolver falls back to the
	// code default for whichever isn't set.
	Defaults map[string]PersistTypeDefaults `mapstructure:"defaults"`
}

// PersistTypeDefaults is the schema for one
// `[persist.defaults.<type>]` block. Mirrors the shape of the
// operator's BackendStorageSpec at the CLI surface so the override
// path matches the on-CR shape.
type PersistTypeDefaults struct {
	// Size is the PVC storage request for this backend type
	// (e.g. "10Gi"). Empty → fall back to BackendStorageSizeDefaults.
	Size string `mapstructure:"size"`

	// StorageClassName, when non-empty, overrides the cluster's
	// default storage class for this backend type.
	StorageClassName string `mapstructure:"storage_class_name"`

	// Mounts replaces the type-default subPath/mountPath list. When
	// empty (or the field is absent), falls back to
	// BackendStoragePresets for the type.
	Mounts []PersistMountDefault `mapstructure:"mounts"`
}

// PersistMountDefault is one entry in a backend type's mount list.
type PersistMountDefault struct {
	SubPath   string `mapstructure:"subPath"`
	MountPath string `mapstructure:"mountPath"`
}

// TUIConfig is the on-disk [tui] block. Carries:
//
//   - [tui.create_defaults]    — pre-populates the create modal
//   - [tui.expected_env_vars]  — per-backend env-var suggestions
//     surfaced as autocomplete entries
//     on each Secret #N KEY field
//
// Future TUI knobs (theme, poll interval overrides, key remap)
// extend the same struct.
type TUIConfig struct {
	CreateDefaults TUICreateDefaults `mapstructure:"create_defaults"`

	// ExpectedEnvVars is keyed by backend type ("claude", "openai",
	// "codex", "gemini"; echo would be empty). Each value is a list of env-
	// var names the user expects to see suggested when typing a
	// Secret KEY for that backend. Merged with the built-in
	// catalog at modal-open time (dedup + sort); a user's custom
	// entries can never accidentally hide the built-ins.
	ExpectedEnvVars map[string][]string `mapstructure:"expected_env_vars"`
}

// TUICreateDefaults is the schema for `[tui.create_defaults]` —
// values pre-populated into the create-agent modal. Auto-saved by
// the TUI after each successful Create; hand-editable. WW_TUI_DEFAULT_*
// env vars override these at modal-open time (see internal/tui).
//
// Phase 1 secrets redesign: replaced the (auth_mode, auth_value)
// pair with two more focused fields. The old keys are no longer
// read by the loader — users with a saved config from earlier
// versions get fresh defaults (fallback layer) on next launch and
// new state on next successful create. Pre-1.0; the migration
// surface is small.
type TUICreateDefaults struct {
	Namespace       string `mapstructure:"namespace"`
	Backend         string `mapstructure:"backend"`
	Team            string `mapstructure:"team"`
	CreateNamespace bool   `mapstructure:"create_namespace"`

	// ExistingSecret pre-fills the modal's "Existing Secret name
	// (optional)" InputField. When set, the form treats it as the
	// authoritative auth path and ignores Secrets.
	ExistingSecret string `mapstructure:"existing_secret"`

	// Secrets pre-fills the dynamic per-pair section. Each entry
	// is "KEY=VALUE" — values prefixed with `$` are env-lifts at
	// submit time. List shape (rather than a multi-line string)
	// keeps the on-disk TOML hand-editable as
	//
	//   secrets = [
	//     "ANTHROPIC_API_KEY=sk-ant-...",
	//     "GITHUB_TOKEN=$GITHUB_PAT",
	//   ]
	//
	// without quoting / line-continuation gymnastics.
	Secrets []string `mapstructure:"secrets"`

	GitOpsRepo string `mapstructure:"gitops_repo"`
}

// Resolved carries the final effective settings for a single CLI
// invocation.
type Resolved struct {
	Profile  string
	BaseURL  string
	Token    string
	RunToken string
	Timeout  time.Duration
	Update   UpdateConfig // raw strings; parsed by the update package
	// LoadedFrom is the absolute path of the config file that supplied
	// values for this invocation, or "" when no file was found. The
	// config writer uses this to target the right file on save.
	LoadedFrom string
}

// FlagOverrides carries the command-line values. Empty-string means
// "unset" for the layering algorithm.
type FlagOverrides struct {
	Profile  string
	BaseURL  string
	Token    string
	RunToken string
	Timeout  time.Duration // zero means unset
}

// DefaultBaseURL is the compiled-in fallback used when no other source
// provides a base URL.
const DefaultBaseURL = "http://localhost:8000"

// DefaultTimeout is used when no source sets a timeout.
const DefaultTimeout = 30 * time.Second

// Load applies the precedence rules: flag > env > file > default.
// cfgPath may be empty to use the default search path (see
// defaultConfigPaths). The returned Resolved is usable even if the
// config file does not exist.
//
// File discovery order (first existing file wins):
//  1. --config <path> flag (cfgPath argument)
//  2. WW_CONFIG env var
//  3. $HOME/.witwave/config.toml (brand-aligned dotfile dir)
//  4. $XDG_CONFIG_HOME/ww/config.toml
//  5. os.UserConfigDir() / ww / config.toml (platform default)
func Load(cfgPath string, overrides FlagOverrides, getenv func(string) string) (Resolved, error) {
	if getenv == nil {
		getenv = os.Getenv
	}

	profile := overrides.Profile
	if profile == "" {
		profile = getenv("WW_PROFILE")
	}
	if profile == "" {
		profile = "default"
	}

	// WW_CONFIG is the env-var path override. Only consulted when the
	// --config flag wasn't passed — flag still wins.
	if cfgPath == "" {
		cfgPath = getenv("WW_CONFIG")
	}

	// Build a Viper instance for file discovery + parse. We deliberately
	// do NOT use Viper's env binding or flag binding here — the layering
	// logic below covers those with clearer precedence + compat with
	// our pre-Viper env var names (which don't include the "ww" prefix
	// Viper would normally apply nor the profile path segment).
	v := viper.New()
	v.SetConfigType("toml")

	if cfgPath != "" {
		// Explicit path — don't fall back to search paths; respect
		// the user's intent (including "this file doesn't exist yet").
		v.SetConfigFile(cfgPath)
	} else {
		v.SetConfigName("config")
		for _, p := range defaultConfigPaths(getenv) {
			v.AddConfigPath(p)
		}
	}

	var file File
	if err := v.ReadInConfig(); err != nil {
		// A missing file is fine — defaults still apply. Any other
		// error (parse failure, permission denied) surfaces.
		var notFoundErr viper.ConfigFileNotFoundError
		if !errors.As(err, &notFoundErr) && !os.IsNotExist(err) {
			return Resolved{}, fmt.Errorf("read config: %w", err)
		}
	} else {
		// Found + parsed. Apply pre-flight safety checks on the path.
		loaded := v.ConfigFileUsed()
		if loaded != "" {
			if err := validateConfigFile(loaded); err != nil {
				return Resolved{}, err
			}
		}
		if err := v.Unmarshal(&file); err != nil {
			return Resolved{}, fmt.Errorf("decode config: %w", err)
		}
	}

	fileProf, profileFound := file.Profile[profile]

	// Warn on a profile that the user selected but the file doesn't
	// define. The layering still proceeds (env + flags + defaults may
	// supply what the file didn't), but the user almost certainly
	// typoed — silent fallback to a zero-valued Profile can connect
	// to the compiled-in default harness when the user expected prod
	// credentials. The warning gives them a chance to notice before
	// a command actually runs against the wrong target. Skipped for
	// the "default" profile because an absent [profile.default]
	// section is the legitimate zero-config case.
	if !profileFound && profile != "default" && v.ConfigFileUsed() != "" {
		fmt.Fprintf(os.Stderr,
			"ww: warning: profile %q not found in %s — falling back to env/flag/defaults. "+
				"Known profiles: %s\n",
			profile, v.ConfigFileUsed(), knownProfiles(file),
		)
	}

	// Apply layering: flag > env > file > default.
	r := Resolved{
		Profile:    profile,
		BaseURL:    firstNonEmpty(overrides.BaseURL, getenv("WW_BASE_URL"), fileProf.BaseURL, DefaultBaseURL),
		Token:      firstNonEmpty(overrides.Token, getenv("WW_TOKEN"), fileProf.Token),
		RunToken:   firstNonEmpty(overrides.RunToken, getenv("WW_RUN_TOKEN"), fileProf.RunToken),
		LoadedFrom: v.ConfigFileUsed(),
	}

	timeout := overrides.Timeout
	if timeout == 0 {
		if s := getenv("WW_TIMEOUT"); s != "" {
			d, err := time.ParseDuration(s)
			if err != nil {
				return r, fmt.Errorf("WW_TIMEOUT %q: %w", s, err)
			}
			timeout = d
		}
	}
	if timeout == 0 && fileProf.Timeout != "" {
		d, err := time.ParseDuration(fileProf.Timeout)
		if err != nil {
			return r, fmt.Errorf("profile %q timeout %q: %w", profile, fileProf.Timeout, err)
		}
		timeout = d
	}
	if timeout == 0 {
		timeout = DefaultTimeout
	}
	r.Timeout = timeout

	// [update] block: file values feed through; env vars override.
	// The update package parses the strings at use-time and applies
	// its own defaults, so we pass whatever we have through raw.
	r.Update = file.Update
	if v := getenv("WW_UPDATE_MODE"); v != "" {
		r.Update.Mode = v
	}
	if v := getenv("WW_UPDATE_CHANNEL"); v != "" {
		r.Update.Channel = v
	}
	if v := getenv("WW_UPDATE_INTERVAL"); v != "" {
		r.Update.Interval = v
	}
	return r, nil
}

// validateConfigFile applies the safety checks we previously ran inline
// before parsing: refuse non-regular paths, warn on world-readable mode
// because bearer tokens live plaintext in the file.
//
// #1607: the perm check warns to stderr (never stdout — must not corrupt
// JSON-emitting code paths like `ww config get -o json`) and continues
// loading. We deliberately do NOT refuse to start: existing installs
// from pre-fix ww may have landed at 0o644, and a hard failure would
// strand users mid-shell-pipeline. The next `ww config set` Save will
// tighten the perms (see writer.Save). The check is Unix-only;
// Windows uses ACLs and the permission bits are not meaningful.
func validateConfigFile(path string) error {
	st, err := os.Stat(path)
	if err != nil {
		return nil // Viper didn't find it; not our problem
	}
	// #1395: refuse non-regular files (named pipes, devices) so a
	// hostile XDG_CONFIG_HOME pointing at /dev/stdin or a named FIFO
	// can't hang the CLI or exfiltrate via stdout.
	if !st.Mode().IsRegular() {
		return fmt.Errorf("config path %s is not a regular file (mode=%s)", path, st.Mode())
	}
	// #1358 / #1607: warn (but proceed) when config.toml is readable
	// by others — bearer tokens live plaintext inside. Equivalent to
	// "any mode bit > 0600" since the check matches any group/other
	// permission. Unix-only check; Windows permission model differs
	// and this block is a no-op there.
	if runtime.GOOS != "windows" && st.Mode().Perm()&0o077 != 0 {
		fmt.Fprintf(os.Stderr,
			"ww: warning: config %s has permissive mode %04o; "+
				"bearer tokens are readable by other users. "+
				"Run: chmod 600 %s\n",
			path, st.Mode().Perm(), path,
		)
	}
	return nil
}

// defaultConfigPaths returns the search list Viper walks to find the
// config file, in precedence order (first match wins). XDG_CONFIG_HOME
// threads through getenv so tests can exercise every branch.
//
// Note: $HOME/.witwave/ and $XDG_CONFIG_HOME/ww/ are distinct spaces.
// The platform also uses .witwave/ as a per-agent runtime-config dir
// (e.g. .agents/<env>/<agent>/.witwave/) — that's repo-scoped and
// unrelated to this user-level CLI config.
func defaultConfigPaths(getenv func(string) string) []string {
	var paths []string

	// 1. $HOME/.witwave/ — brand-aligned dotfile dir. Preferred first
	//    so a user who chooses this location doesn't need any env
	//    or flag setup.
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		paths = append(paths, filepath.Join(home, ".witwave"))
	}

	// 2. $XDG_CONFIG_HOME/ww/ — XDG Base Directory Specification.
	//    Separate from os.UserConfigDir() because a user may
	//    deliberately set XDG_CONFIG_HOME to something non-default.
	if xdg := getenv("XDG_CONFIG_HOME"); xdg != "" {
		paths = append(paths, filepath.Join(xdg, "ww"))
	}

	// 3. Platform-default user config dir (ww/ under it). On Linux this
	//    typically duplicates #2 (both resolve to ~/.config/ww/) but
	//    Viper dedupes search paths internally; on macOS this is
	//    ~/Library/Application Support/ww/, on Windows %AppData%\ww\.
	if ucd, err := os.UserConfigDir(); err == nil && ucd != "" {
		paths = append(paths, filepath.Join(ucd, "ww"))
	}
	return paths
}

// LoadTUICreateDefaults reads only the `[tui.create_defaults]` block
// from whichever config.toml the standard discovery chain finds first.
// Returns the zero TUICreateDefaults (and ok=false) when no file
// exists or the block isn't present — callers layer their own
// fallbacks on top.
//
// Separate from Load() because the TUI doesn't need the
// flag/env/profile resolution Load runs; it only needs a focused
// read of one TOML sub-tree. This function is deliberately
// side-effect free — no chmod, no mkdir — so opening it from a
// freshly-launched TUI never touches disk except for the read.
func LoadTUICreateDefaults(getenv func(string) string) (TUICreateDefaults, bool) {
	if getenv == nil {
		getenv = os.Getenv
	}
	path := findExistingConfig(getenv)
	if path == "" {
		return TUICreateDefaults{}, false
	}
	v := viper.New()
	v.SetConfigType("toml")
	v.SetConfigFile(path)
	if err := v.ReadInConfig(); err != nil {
		return TUICreateDefaults{}, false
	}
	var f File
	if err := v.Unmarshal(&f); err != nil {
		return TUICreateDefaults{}, false
	}
	d := f.TUI.CreateDefaults
	// "Empty" detection — if every field is at its zero value, the
	// user likely doesn't have the block at all (vs. having
	// explicitly set every value to its zero value). Treat that as
	// "no saved state" so the TUI's fallback layer engages instead
	// of pre-filling the form with all blanks. Slice field forces a
	// manual check (Go can't compare structs containing slices).
	if d.Namespace == "" && d.Backend == "" && d.Team == "" &&
		!d.CreateNamespace && d.ExistingSecret == "" &&
		len(d.Secrets) == 0 && d.GitOpsRepo == "" {
		return d, false
	}
	return d, true
}

// LoadTUIExpectedEnvVars returns the user-supplied env-var override
// map from `[tui.expected_env_vars]`. Returns nil (and ok=false)
// when the block is absent or unreadable so callers can short-
// circuit the merge step. Same focused-read shape as
// LoadTUICreateDefaults — no flag/env layering, just the on-disk
// values.
func LoadTUIExpectedEnvVars(getenv func(string) string) (map[string][]string, bool) {
	if getenv == nil {
		getenv = os.Getenv
	}
	path := findExistingConfig(getenv)
	if path == "" {
		return nil, false
	}
	v := viper.New()
	v.SetConfigType("toml")
	v.SetConfigFile(path)
	if err := v.ReadInConfig(); err != nil {
		return nil, false
	}
	var f File
	if err := v.Unmarshal(&f); err != nil {
		return nil, false
	}
	if len(f.TUI.ExpectedEnvVars) == 0 {
		return nil, false
	}
	return f.TUI.ExpectedEnvVars, true
}

// LoadPersistDefaults reads the `[persist.defaults.<type>]` blocks
// from whichever config.toml the standard discovery chain finds
// first. Returns the per-type override map (and ok=false) when no
// file exists or the block isn't present — callers layer their own
// fallbacks on top.
//
// Same focused-read shape as LoadTUICreateDefaults / LoadTUIExpected
// EnvVars — no flag/env layering, just the on-disk values.
func LoadPersistDefaults(getenv func(string) string) (map[string]PersistTypeDefaults, bool) {
	if getenv == nil {
		getenv = os.Getenv
	}
	path := findExistingConfig(getenv)
	if path == "" {
		return nil, false
	}
	v := viper.New()
	v.SetConfigType("toml")
	v.SetConfigFile(path)
	if err := v.ReadInConfig(); err != nil {
		return nil, false
	}
	var f File
	if err := v.Unmarshal(&f); err != nil {
		return nil, false
	}
	if len(f.Persist.Defaults) == 0 {
		return nil, false
	}
	return f.Persist.Defaults, true
}

// knownProfiles returns a comma-separated list of profiles defined in
// the file, or "(none)" if the Profile map is empty. Purely for the
// "profile not found" warning message — small UX polish that makes
// the "did I typo?" check actionable without a separate subcommand.
func knownProfiles(file File) string {
	if len(file.Profile) == 0 {
		return "(none)"
	}
	names := make([]string, 0, len(file.Profile))
	for name := range file.Profile {
		names = append(names, name)
	}
	sort.Strings(names)
	return strings.Join(names, ", ")
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

// Dump writes a redacted summary of the resolved config for --verbose.
func (r Resolved) Dump(w io.Writer) {
	fmt.Fprintf(w, "profile=%s base_url=%s timeout=%s token=%s run_token=%s loaded_from=%s\n",
		r.Profile, r.BaseURL, r.Timeout,
		redact(r.Token), redact(r.RunToken), displayPath(r.LoadedFrom),
	)
}

func displayPath(p string) string {
	if p == "" {
		return "<none>"
	}
	return p
}

func redact(s string) string {
	if s == "" {
		return "<unset>"
	}
	if len(s) <= 4 {
		return "***"
	}
	return s[:2] + "…" + s[len(s)-2:]
}
