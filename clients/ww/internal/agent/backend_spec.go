package agent

import (
	"fmt"
	"strings"
)

// BackendSpec describes one declared backend on an agent — the (name,
// type) pair plus the port the operator will assign. Names are the
// identifier that everything else derives from:
//
//   - container name in the pod      (→ spec.backends[].name)
//   - folder in the repo             (→ .agents/<agent>/.<name>/)
//   - mount path inside the pod      (→ /home/agent/.<name>/)
//   - agent id in backend.yaml       (→ routing entries reference this)
//
// Types (echo / claude / openai / gemini) determine which image the
// operator pulls and which behavioural-instructions file the scaffold
// seeds (CLAUDE.md / AGENTS.md / GEMINI.md). A single type can appear
// on an agent under multiple names — that's the "two echo backends"
// pattern: distinct names + distinct ports + same image.
type BackendSpec struct {
	// Name is the unique-per-agent identifier. Must be DNS-1123 and
	// distinct across the agent's backends.
	Name string

	// Type is the backend implementation. One of the values returned
	// by KnownBackends(). Defaults to the same value as Name when the
	// user supplies only a name (i.e. "--backend echo" implies
	// type echo).
	Type string

	// Port is derived from the backend's index in the list —
	// BackendPort(0) = 8001, BackendPort(1) = 8002, … up to the CRD's
	// 50-backend cap. Kept as a distinct field so callers don't have
	// to recompute it in every codepath that writes it into the CR
	// or the inline backend.yaml.
	Port int32

	// CredentialSecret, when non-empty, stamps
	// `spec.backends[].credentials.existingSecret` on the generated CR.
	// The operator wires this Secret into the backend container's
	// envFrom at reconcile time, so whatever keys live in the Secret
	// become env vars visible to the backend. Populated by Create's
	// auth-resolution phase (see BackendAuthResolver.resolve).
	CredentialSecret string

	// Storage, when non-nil, stamps `spec.backends[].storage` on the
	// generated CR. The operator reconciles a per-backend PVC named
	// `<agent>-<backend>-data` and projects it via subPath into the
	// container at each Mount.MountPath. Populated by Create's
	// `--persist` flag-resolution phase (see ApplyBackendPersist).
	Storage *BackendStorageSpec

	// Env, when non-empty, stamps `spec.backends[].env[]` on the
	// generated CR — one {name, value} entry per (key, value) pair.
	// Use for plain (non-secret) env vars that need to override the
	// container's compiled-in defaults: TASK_TIMEOUT_SECONDS,
	// LOG_LEVEL, STREAM_CHUNK_TIMEOUT_SECONDS, etc. Populated by
	// Create's `--backend-env` flag-resolution phase (see
	// ApplyBackendEnvs). Secrets belong on CredentialSecret instead.
	Env map[string]string
}

// ParseBackendSpecs converts the repeatable `--backend` flag values
// into structured entries. Accepts two shapes per entry:
//
//   - `type`              — name = type (the single-backend shortcut)
//   - `name:type`         — explicit name + type pair (for multi-
//     backend configurations, especially when
//     the same type appears twice)
//
// Returns the default single-echo spec when `raw` is empty so callers
// that don't care about backends at all (most single-backend users)
// don't have to construct one themselves.
//
// Validates each entry: DNS-1123 compliance on names, type is in the
// known-backend set, and no two entries share a name. Errors here are
// usage-level and get surfaced to cobra's RunE — the CLI prints them
// prefixed with "error: ".
func ParseBackendSpecs(raw []string) ([]BackendSpec, error) {
	if len(raw) == 0 {
		return []BackendSpec{{
			Name: DefaultBackend,
			Type: DefaultBackend,
			Port: BackendPort(0),
		}}, nil
	}

	out := make([]BackendSpec, 0, len(raw))
	seen := make(map[string]bool, len(raw))
	for i, r := range raw {
		entry := strings.TrimSpace(r)
		if entry == "" {
			return nil, fmt.Errorf("--backend[%d]: empty value", i)
		}
		var name, typ string
		if idx := strings.IndexByte(entry, ':'); idx >= 0 {
			name = strings.TrimSpace(entry[:idx])
			typ = strings.TrimSpace(entry[idx+1:])
		} else {
			name = entry
			typ = entry
		}
		if name == "" || typ == "" {
			return nil, fmt.Errorf("--backend[%d] %q: both name and type required (use `type` or `name:type`)", i, entry)
		}
		if err := ValidateName(name); err != nil {
			return nil, fmt.Errorf("--backend[%d] name: %w", i, err)
		}
		if !IsKnownBackend(typ) {
			return nil, fmt.Errorf(
				"--backend[%d] unknown type %q; valid types: %v",
				i, typ, KnownBackends(),
			)
		}
		if seen[name] {
			return nil, fmt.Errorf(
				"--backend[%d]: duplicate name %q (two backends can share a type but not a name; "+
					"use `name:type` shapes like echo-1:echo / echo-2:echo)",
				i, name,
			)
		}
		seen[name] = true
		out = append(out, BackendSpec{
			Name: name,
			Type: typ,
			Port: BackendPort(i),
		})
	}
	return out, nil
}

// PrimaryBackend returns the first BackendSpec in the list — the
// conventional "default routing target" used by scaffold when
// generating backend.yaml. `ww agent scaffold` hands this to the
// template so every concern initially routes to the first backend;
// the user edits the file later to redistribute.
func PrimaryBackend(specs []BackendSpec) BackendSpec {
	return specs[0]
}
