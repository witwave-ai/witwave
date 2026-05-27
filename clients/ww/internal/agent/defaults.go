package agent

import (
	"fmt"
	"slices"
	"strings"
)

// Default image registry + repo prefix. Mirrors every other image this
// project publishes.
const imageRepoPrefix = "ghcr.io/witwave-ai/images/"

// Port convention for ww-minted WitwaveAgent CRs. Documented so
// NetworkPolicy rules, Service definitions, and operators adding a
// backend by hand can rely on a predictable range:
//
//	Harness:              8000
//	Backend sidecars:     8001-8050 (8001 + index, 0-based)
//	Metrics listener:     9000 (every container, on a dedicated port —
//	                      see shared/metrics_server.py)
//
// The CRD caps `.spec.backends` at 50 entries (maxItems: 50), so the
// range 8001-8050 is an exact fit: one port per possible backend slot
// with no overflow into the metrics port. The harness + every backend
// share a pod network namespace, so ports MUST be distinct.
//
// Callers that need a port outside this range (e.g. to match a legacy
// backend container's listener) can set `spec.backends[].port`
// explicitly in the CR; ww doesn't enforce the convention at CR-apply
// time, only at Build() time from this package.
const (
	DefaultHarnessPort     int32 = 8000
	DefaultBackendBasePort int32 = 8001
	DefaultBackendMaxPort  int32 = 8050
	DefaultMetricsPort     int32 = 9000
)

// BackendPort returns the port the Nth backend (0-indexed) should listen
// on. Offset from DefaultBackendBasePort so concurrent backends never
// collide with the harness or each other. Panics when `index` would
// push the port above DefaultBackendMaxPort — i.e. past the CRD's
// 50-backend cap — because that state is unreachable from a
// schema-valid CR.
func BackendPort(index int) int32 {
	p := DefaultBackendBasePort + int32(index) //nolint:gosec // G115: index is bounded by CRD's 50-backend cap (maxItems: 50); the panic guard on the next line enforces this defensively for unschematised callers.
	if p > DefaultBackendMaxPort {
		panic(fmt.Sprintf(
			"agent: backend index %d exceeds CRD's 50-backend cap (port %d > max %d)",
			index, p, DefaultBackendMaxPort,
		))
	}
	return p
}

// DefaultPort is kept as an alias for DefaultHarnessPort so existing
// references (tests, docs) continue to resolve. New code should prefer
// the explicit DefaultHarnessPort / BackendPort pair.
const DefaultPort = DefaultHarnessPort

// Backend-type identifiers the CLI knows about today. Keep this list
// aligned with `backends/` subdirectories and the dashboard's
// BackendType union — adding a backend type without updating this list
// would leave `ww agent create --backend <new>` silently unresolvable.
const (
	BackendEcho   = "echo"
	BackendClaude = "claude"
	BackendOpenAI = "openai"
	// BackendCodex is the Codex-native coding-agent backend.
	BackendCodex  = "codex"
	BackendGemini = "gemini"
)

// DefaultBackend is the backend name used when `ww agent create` is
// invoked without `--backend`. Echo is chosen because it requires no API
// keys and no external services, which is the "access to a Kubernetes
// cluster and the CLI is all you need" promise (see
// backends/echo/README.md).
const DefaultBackend = BackendEcho

// KnownBackends returns the backends `ww agent create --backend` accepts.
// Order is display-order (echo first for onboarding visibility).
func KnownBackends() []string {
	return []string{BackendEcho, BackendClaude, BackendOpenAI, BackendCodex, BackendGemini}
}

// IsKnownBackend reports whether name matches a backend this CLI knows
// about. Echo + the three LLM backends only; future backends must be
// added both here and in the dashboard's BackendType union.
func IsKnownBackend(name string) bool {
	return slices.Contains(KnownBackends(), name)
}

// HarnessImage returns the default harness image reference for the given
// CLI version. An empty or "dev" version falls back to :latest with an
// explicit marker so a dev build never silently points at a stale
// production tag.
func HarnessImage(cliVersion string) string {
	return imageRef("harness", cliVersion)
}

// BackendImage returns the default image reference for a named backend
// at the given CLI version.
func BackendImage(backend, cliVersion string) string {
	return imageRef(backend, cliVersion)
}

// imageRef assembles `ghcr.io/witwave-ai/images/<name>:<tag>`. A blank,
// "dev", or "unknown" cliVersion falls back to :latest — this happens
// only on unreleased builds and is loud by virtue of the tag itself.
func imageRef(name, cliVersion string) string {
	tag := strings.TrimSpace(cliVersion)
	switch tag {
	case "", "dev", "unknown":
		tag = "latest"
	}
	// Strip any leading `v` on release tags (v0.6.0 → 0.6.0) to match the
	// GHCR publishing pattern used by .github/workflows/release.yaml.
	tag = strings.TrimPrefix(tag, "v")
	return imageRepoPrefix + name + ":" + tag
}

// IsDevVersion reports whether the CLI was built without a concrete
// release version (ldflags not set). Callers surface this as a warning
// in the preflight banner so operators know they're about to deploy
// floating-tag images.
func IsDevVersion(cliVersion string) bool {
	t := strings.TrimSpace(cliVersion)
	return t == "" || t == "dev" || t == "unknown"
}

// DefaultAgentNamespace is the ww-specific fallback namespace for every
// `ww agent *` operation when neither --namespace nor the kubeconfig
// context pin one. Chosen to keep ww-managed resources out of the `default`
// namespace by default — `default` is a shared free-for-all and ww agents
// benefit from a dedicated blast radius.
const DefaultAgentNamespace = "witwave"

// ResolveNamespace picks the namespace for an `ww agent` operation.
// Precedence: explicit flag → context's configured namespace →
// DefaultAgentNamespace. Callers MUST log the resolved value so the
// user sees where an unnamed invocation landed (DESIGN.md NS-2).
func ResolveNamespace(flagValue, contextNS string) string {
	ns, _ := ResolveNamespaceWithSource(flagValue, contextNS)
	return ns
}

// NamespaceSource identifies where a resolved namespace came from. Used
// by callers that log the resolution so the message can distinguish
// "we picked this from your kubeconfig context" from "we fell back to
// the ww default because nothing else was configured."
type NamespaceSource int

const (
	// NamespaceFromFlag means the user passed --namespace / -n.
	NamespaceFromFlag NamespaceSource = iota
	// NamespaceFromContext means the kubeconfig context pinned a namespace.
	NamespaceFromContext
	// NamespaceFromDefault means nothing was pinned; ww fell back to
	// DefaultAgentNamespace.
	NamespaceFromDefault
)

// ResolveNamespaceWithSource mirrors ResolveNamespace but additionally
// returns the source of the resolved value — so log lines can read
// "(from kubeconfig context)" vs "(ww default)" accurately.
func ResolveNamespaceWithSource(flagValue, contextNS string) (string, NamespaceSource) {
	if flagValue != "" {
		return flagValue, NamespaceFromFlag
	}
	if contextNS != "" {
		return contextNS, NamespaceFromContext
	}
	return DefaultAgentNamespace, NamespaceFromDefault
}
