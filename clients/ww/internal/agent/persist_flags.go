package agent

import (
	"fmt"
	"strings"
)

// BackendStorageMount is the CLI-side representation of one backend
// PVC subPath → mountPath pair. Mirrors the chart's
// `agents[i].backends[j].storage.mounts[k]` entry shape and the
// operator CRD's BackendStorageMount, so a CR built by ww renders
// byte-for-byte identical to a Helm-rendered pod with the same
// values.
type BackendStorageMount struct {
	// SubPath is the path inside the PVC (no leading "/").
	SubPath string

	// MountPath is the absolute path inside the backend container.
	MountPath string
}

// BackendStorageSpec describes the per-backend PVC declaration parsed
// from one `--persist` flag entry. One PVC per backend (named
// `<agent>-<backend>-data` by the operator), subPath-fanned into the
// container at one or more mount paths.
type BackendStorageSpec struct {
	// Size is the PVC storage request (e.g. "10Gi"). The CRD's
	// validation pattern is the resource.Quantity syntax — we let the
	// operator's admission webhook reject malformed values rather than
	// duplicating the regex on the CLI side.
	Size string

	// StorageClassName, when non-empty, overrides the cluster's
	// default storage class for the operator-created PVC.
	StorageClassName string

	// Mounts is the subPath → mountPath fan-out. Populated by the
	// flag parser using a per-backend-type default convention; future
	// `--persist-mount` flags would append/override here.
	Mounts []BackendStorageMount
}

// BackendStorageSizeDefaults enumerates the default PVC size for each
// known backend type. Used when --persist's <size> is omitted (bare
// `--persist <name>`) and --with-persistence's per-backend fan-out.
// Override via `[persist.defaults.<type>].size` in config.toml — the
// resolver in ApplyBackendPersist consults config first, then this
// map.
//
// Defaults reflect typical disk pressure per backend type:
//
//   - echo:   1Gi  — symbolic mount; echo writes nothing real
//   - claude: 10Gi — projects/sessions/backups/memory/logs accumulate
//   - openai: 5Gi  — memory + sessions + logs, lighter footprint
//   - codex:  5Gi  — Codex instructions, session state, memory, and logs
//   - gemini: 5Gi  — JSON session store + logs grow with conversation length
var BackendStorageSizeDefaults = map[string]string{
	"echo":   "1Gi",
	"claude": "10Gi",
	"openai": "5Gi",
	"codex":  "5Gi",
	"gemini": "5Gi",
}

// BackendStoragePresets enumerates the default subPath → mountPath
// fan-out for each known backend type. Mirrors the chart's example
// values (charts/witwave/values.yaml ~line 1488) and the dirs each
// backend's image expects to find under `/home/agent/.<type>/`, plus
// `/home/agent/logs` for Witwave conversation/trace JSONL durability and
// `/home/agent/state` for durable backend machine state.
//
// Backend types not listed here have no defaults — the CLI rejects
// `--persist <name>=<size>` for them with an actionable error rather
// than rendering an unused PVC.
var BackendStoragePresets = map[string][]BackendStorageMount{
	"claude": {
		{SubPath: "projects", MountPath: "/home/agent/.claude/projects"},
		{SubPath: "sessions", MountPath: "/home/agent/.claude/sessions"},
		{SubPath: "backups", MountPath: "/home/agent/.claude/backups"},
		{SubPath: "memory", MountPath: "/home/agent/.claude/memory"},
		{SubPath: "logs", MountPath: "/home/agent/logs"},
		{SubPath: "state", MountPath: "/home/agent/state"},
	},
	"codex": {
		{SubPath: "memory", MountPath: "/home/agent/.codex/memory"},
		{SubPath: "sessions", MountPath: "/home/agent/.codex/sessions"},
		{SubPath: "logs", MountPath: "/home/agent/logs"},
		{SubPath: "state", MountPath: "/home/agent/state"},
	},
	"openai": {
		{SubPath: "memory", MountPath: "/home/agent/.openai/memory"},
		{SubPath: "sessions", MountPath: "/home/agent/.openai/sessions"},
		{SubPath: "logs", MountPath: "/home/agent/logs"},
		{SubPath: "state", MountPath: "/home/agent/state"},
	},
	"gemini": {
		// gemini stores conversation JSON under memory/sessions/ per
		// SESSION_STORE_DIR's default, and persisted memory files at
		// memory/. One PVC, two subPaths.
		{SubPath: "memory", MountPath: "/home/agent/.gemini/memory"},
		{SubPath: "logs", MountPath: "/home/agent/logs"},
		{SubPath: "state", MountPath: "/home/agent/state"},
	},
	"echo": {
		// Echo's intentional non-scope (backends/echo/README.md) means
		// it has no real session state. The single `memory/` subPath
		// here is a symbolic convention so `--persist` exercises the
		// mechanic uniformly across backend types — useful for
		// bootstrap walkthroughs that want to verify the per-backend
		// PVC story without dragging in claude/openai/codex/gemini API keys.
		// Path is type-keyed (`/home/agent/.echo/memory`) so two
		// echo backends in the same agent (echo-1 + echo-2) each get
		// their own PVC mounted at the same in-container path,
		// distinct from any name-based gitMapping at .echo-1/ /
		// .echo-2/.
		{SubPath: "memory", MountPath: "/home/agent/.echo/memory"},
	},
}

// ParseBackendPersistMounts converts repeatable `--persist-mount`
// flag values to (backend-name → []BackendStorageMount) entries.
// Form:
//
//	<backend-name>=<subpath>:<mountpath>
//
// Each backend can be referenced multiple times to declare more
// than one mount. Cross-flag validation (does the backend-name
// have a matching --persist entry? duplicate within a backend?)
// happens in ApplyBackendPersist — the parser stays a pure
// string→struct conversion.
func ParseBackendPersistMounts(raw []string) (map[string][]BackendStorageMount, error) {
	out := map[string][]BackendStorageMount{}
	for i, r := range raw {
		entry := strings.TrimSpace(r)
		if entry == "" {
			return nil, fmt.Errorf("--persist-mount[%d]: empty value", i)
		}
		eq := strings.IndexByte(entry, '=')
		if eq < 1 {
			return nil, fmt.Errorf("--persist-mount[%d] %q: form is <backend-name>=<subpath>:<mountpath>", i, entry)
		}
		name := strings.TrimSpace(entry[:eq])
		body := strings.TrimSpace(entry[eq+1:])
		if err := ValidateName(name); err != nil {
			return nil, fmt.Errorf("--persist-mount[%d] backend %q: %w", i, name, err)
		}
		// Split body into <subpath>:<mountpath>. mountPath must be
		// absolute (starts with `/`) so the colon split is unambiguous —
		// any `:` before the first `/` is the subPath separator.
		colon := strings.IndexByte(body, ':')
		if colon < 1 {
			return nil, fmt.Errorf("--persist-mount[%d] %q: form is <backend-name>=<subpath>:<mountpath>", i, entry)
		}
		subPath := strings.TrimSpace(body[:colon])
		mountPath := strings.TrimSpace(body[colon+1:])
		if subPath == "" || mountPath == "" {
			return nil, fmt.Errorf("--persist-mount[%d] %q: subpath and mountpath are both required", i, entry)
		}
		if !strings.HasPrefix(mountPath, "/") {
			return nil, fmt.Errorf("--persist-mount[%d] %q: mountpath %q must be absolute (start with '/')", i, entry, mountPath)
		}
		// kubelet rejects subPath with `..`, leading `/`, or absolute
		// segments — catch it client-side for a clearer error.
		if strings.HasPrefix(subPath, "/") {
			return nil, fmt.Errorf("--persist-mount[%d] %q: subpath %q must be relative (no leading '/')", i, entry, subPath)
		}
		if strings.Contains(subPath, "..") {
			return nil, fmt.Errorf("--persist-mount[%d] %q: subpath %q must not contain '..'", i, entry, subPath)
		}
		out[name] = append(out[name], BackendStorageMount{
			SubPath:   subPath,
			MountPath: mountPath,
		})
	}
	return out, nil
}

// ParseBackendPersist converts repeatable `--persist` flag values to
// (backend-name → BackendStorageSpec) entries. Two shapes accepted:
//
//	<backend-name>                            — type-default size + class
//	<backend-name>=<size>[@<storage-class>]   — explicit size (and optional class)
//
// Bare `<name>` form (size omitted) leaves Size empty in the parsed
// spec; ApplyBackendPersist fills it from the (config → code) defaults
// chain.
//
// Cross-flag validation (does the backend-name reference an actual
// --backend? does the backend's type have a default mount preset?)
// happens in ApplyBackendPersist — the parser stays a pure
// string→struct conversion.
func ParseBackendPersist(raw []string) (map[string]BackendStorageSpec, error) {
	out := map[string]BackendStorageSpec{}
	for i, r := range raw {
		entry := strings.TrimSpace(r)
		if entry == "" {
			return nil, fmt.Errorf("--persist[%d]: empty value", i)
		}
		// Two parse shapes:
		//   <name>=<size>[@<class>]   — explicit
		//   <name>                    — bare, size resolved from defaults
		var name, rest string
		if eq := strings.IndexByte(entry, '='); eq >= 0 {
			name = strings.TrimSpace(entry[:eq])
			rest = strings.TrimSpace(entry[eq+1:])
			if rest == "" {
				return nil, fmt.Errorf("--persist[%d] %q: empty size after '=' (drop the '=' to use the type-default)", i, entry)
			}
		} else {
			name = strings.TrimSpace(entry)
		}
		if err := ValidateName(name); err != nil {
			return nil, fmt.Errorf("--persist[%d] backend %q: %w", i, name, err)
		}
		if _, dup := out[name]; dup {
			return nil, fmt.Errorf("--persist[%d]: duplicate backend %q", i, name)
		}

		var size, class string
		if rest != "" {
			if at := strings.IndexByte(rest, '@'); at >= 0 {
				size = strings.TrimSpace(rest[:at])
				class = strings.TrimSpace(rest[at+1:])
				if size == "" {
					return nil, fmt.Errorf("--persist[%d] %q: empty size before '@'", i, entry)
				}
				if class == "" {
					return nil, fmt.Errorf("--persist[%d] %q: empty storage class after '@'", i, entry)
				}
			} else {
				size = rest
			}
		}

		out[name] = BackendStorageSpec{
			Size:             size,
			StorageClassName: class,
		}
	}
	return out, nil
}

// PersistDefaults captures the per-backend-type overrides supplied by
// the user's config.toml (or any other layered source above the
// code-level BackendStorageSizeDefaults / BackendStoragePresets
// constants). The CLI marshals its config-side struct into this
// shape before handing it to ApplyBackendPersist; the call-site is
// agnostic to where the defaults came from.
type PersistDefaults struct {
	// Size, when non-empty, replaces the code default for this
	// backend type. Same resource.Quantity syntax as --persist's
	// <size>.
	Size string

	// StorageClassName, when non-empty, replaces the cluster default
	// for this backend type.
	StorageClassName string

	// Mounts, when non-empty, replaces the BackendStoragePresets
	// entry for this backend type. Empty falls back to the code
	// default.
	Mounts []BackendStorageMount
}

// ResolvePersistDefaults builds the (type → spec) map used by
// ApplyBackendPersist when --persist's <size> is omitted (bare
// `--persist <name>`) or when `--with-persistence` fans out across
// every backend. Layered resolution per type:
//
//   - Size:   configDefaults[<type>].Size                 ?? BackendStorageSizeDefaults[<type>]
//   - Class:  configDefaults[<type>].StorageClassName    ?? "" (cluster default)
//   - Mounts: configDefaults[<type>].Mounts              ?? BackendStoragePresets[<type>]
//
// Returns the resolved per-type defaults plus a list of backend types
// for which neither the config nor the code has any default — those
// names show up in cross-flag validation errors so users can diagnose
// "I asked for --with-persistence but my custom backend type X had no
// default — add a [persist.defaults.X] block."
func ResolvePersistDefaults(configDefaults map[string]PersistDefaults) map[string]BackendStorageSpec {
	known := map[string]struct{}{}
	for t := range BackendStorageSizeDefaults {
		known[t] = struct{}{}
	}
	for t := range BackendStoragePresets {
		known[t] = struct{}{}
	}
	for t := range configDefaults {
		known[t] = struct{}{}
	}

	out := make(map[string]BackendStorageSpec, len(known))
	for t := range known {
		spec := BackendStorageSpec{}

		size := BackendStorageSizeDefaults[t]
		if c, ok := configDefaults[t]; ok && c.Size != "" {
			size = c.Size
		}
		spec.Size = size

		if c, ok := configDefaults[t]; ok && c.StorageClassName != "" {
			spec.StorageClassName = c.StorageClassName
		}

		if c, ok := configDefaults[t]; ok && len(c.Mounts) > 0 {
			spec.Mounts = append([]BackendStorageMount(nil), c.Mounts...)
		} else if preset, ok := BackendStoragePresets[t]; ok {
			spec.Mounts = append([]BackendStorageMount(nil), preset...)
		}

		out[t] = spec
	}
	return out
}

// ExpandWithPersistence fans out --with-persistence across every
// declared --backend, producing one BackendStorageSpec per backend
// keyed by NAME (not type). Backends already named in `explicit` are
// skipped — explicit --persist entries always win. Errors when a
// declared backend's TYPE has no resolved defaults (BackendStorage*
// maps and the config block both lack an entry).
func ExpandWithPersistence(
	backends []BackendSpec,
	defaults map[string]BackendStorageSpec,
	explicit map[string]BackendStorageSpec,
) (map[string]BackendStorageSpec, error) {
	out := map[string]BackendStorageSpec{}
	for k, v := range explicit {
		out[k] = v
	}
	for _, b := range backends {
		if _, already := out[b.Name]; already {
			continue
		}
		def, ok := defaults[b.Type]
		if !ok || def.Size == "" {
			return nil, fmt.Errorf(
				"--with-persistence: backend %q (type %q) has no default size — "+
					"add a [persist.defaults.%s] block to your config.toml or pass --persist %s=<size>",
				b.Name, b.Type, b.Type, b.Name)
		}
		out[b.Name] = def
	}
	return out, nil
}

// ApplyBackendPersist resolves --persist + --persist-mount entries
// against the declared --backend list and stamps the storage spec
// onto the matching BackendSpec.
//
// Mount-list resolution is replace-on-presence: when `mounts` carries
// any entries for a backend, those entries are the FULL mount list
// for that backend's PVC — type-derived presets are skipped entirely.
// This avoids "your custom mount got accidentally extended by a
// surprise preset" footguns. To extend the defaults, the caller
// must spell them out alongside the new entry.
//
// Errors when:
//   - a --persist entry references a backend name that doesn't appear
//     in --backend.
//   - a --persist-mount entry references a backend name with no
//     matching --persist (no PVC = nowhere to mount onto).
//   - in default-mount mode, the matched backend's type has no
//     preset in BackendStoragePresets.
//   - a backend's resolved mount list contains duplicate (subPath,
//     mountPath) or duplicate mountPath entries.
func ApplyBackendPersist(backends []BackendSpec, persist map[string]BackendStorageSpec, mounts map[string][]BackendStorageMount) ([]BackendSpec, error) {
	if len(persist) == 0 && len(mounts) == 0 {
		return backends, nil
	}
	idx := make(map[string]int, len(backends))
	for i, b := range backends {
		idx[b.Name] = i
	}

	// Validate every --persist-mount has a matching --persist before
	// touching backends — produces a single clean error rather than
	// stamping some backends and erroring midway.
	for name := range mounts {
		if _, ok := persist[name]; !ok {
			return nil, fmt.Errorf(
				"--persist-mount %q: no matching --persist entry with that name "+
					"(declare --persist %s=<size> first to provision the PVC)",
				name, name)
		}
	}

	for name, spec := range persist {
		bi, ok := idx[name]
		if !ok {
			return nil, fmt.Errorf("--persist %q: no matching --backend entry with that name", name)
		}

		// Size resolution: explicit on flag > type-default from
		// BackendStorageSizeDefaults. The bare `--persist <name>`
		// form leaves Size empty in the parsed spec; fill it from
		// the type-default map here so callers don't have to.
		if spec.Size == "" {
			def, ok := BackendStorageSizeDefaults[backends[bi].Type]
			if !ok {
				return nil, fmt.Errorf(
					"--persist %q: backend type %q has no default size "+
						"(known: %s) — provide an explicit size with "+
						"--persist %s=<size>, or add a [persist.defaults.%s] "+
						"config block",
					name, backends[bi].Type, knownPresetTypes(),
					name, backends[bi].Type)
			}
			spec.Size = def
		}

		// Mount-list resolution. Three sources, most-specific wins:
		//
		//   1. --persist-mount entries for this backend (explicit on
		//      the command line) — replace-on-presence.
		//   2. spec.Mounts already populated upstream (e.g. by
		//      --with-persistence's ExpandWithPersistence using the
		//      config-or-code defaults chain) — don't re-append, the
		//      caller already chose them.
		//   3. BackendStoragePresets[<type>] code default — last
		//      resort fallback.
		switch {
		case len(mounts[name]) > 0:
			spec.Mounts = append(mounts[name][:0:0], mounts[name]...)
		case len(spec.Mounts) > 0:
			// Already filled in by an upstream defaults resolver;
			// leave as-is.
		default:
			preset, ok := BackendStoragePresets[backends[bi].Type]
			if !ok {
				return nil, fmt.Errorf(
					"--persist %q: backend type %q has no default persistent mount paths "+
						"(known: %s) — pass --persist-mount to declare them explicitly",
					name, backends[bi].Type, knownPresetTypes())
			}
			spec.Mounts = append(spec.Mounts, preset...)
		}

		// Defense-in-depth: reject duplicate destinations within the
		// resolved list (kubelet would reject the pod silently
		// otherwise; this surfaces a clean CLI error instead).
		seenSub := map[string]bool{}
		seenPath := map[string]bool{}
		for _, m := range spec.Mounts {
			if seenSub[m.SubPath] {
				return nil, fmt.Errorf("--persist %q: duplicate subPath %q in resolved mount list", name, m.SubPath)
			}
			if seenPath[m.MountPath] {
				return nil, fmt.Errorf("--persist %q: duplicate mountPath %q in resolved mount list", name, m.MountPath)
			}
			seenSub[m.SubPath] = true
			seenPath[m.MountPath] = true
		}

		backends[bi].Storage = &spec
	}
	return backends, nil
}

// knownPresetTypes returns a comma-separated list of backend types
// that have default mount presets, for use in error messages.
func knownPresetTypes() string {
	names := make([]string, 0, len(BackendStoragePresets))
	for k := range BackendStoragePresets {
		names = append(names, k)
	}
	// Stable order for predictable error text.
	for i := 1; i < len(names); i++ {
		for j := i; j > 0 && names[j-1] > names[j]; j-- {
			names[j-1], names[j] = names[j], names[j-1]
		}
	}
	return strings.Join(names, ", ")
}
