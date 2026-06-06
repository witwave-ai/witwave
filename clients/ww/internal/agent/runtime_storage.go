package agent

const (
	// RuntimeTaskStorePath is the SQLite file path inside the agent
	// runtime PVC that the harness and backends use as their persistent
	// A2A task store. Exported as TASK_STORE_PATH on the harness (via
	// ApplyHarnessTaskStoreDefault) and on every backend whose mount
	// list includes runtimeStatePath (via ApplyBackendTaskStoreDefaults),
	// and surfaced as the "Task store" row in `ww agent status` so
	// operators know which file to back up.
	RuntimeTaskStorePath = "/home/agent/state/a2a-tasks.db"
	runtimeStatePath     = "/home/agent/state"
)

// RuntimeStorageMount is the CLI-side representation of one agent runtime PVC
// subPath → harness mountPath pair.
type RuntimeStorageMount struct {
	SubPath   string
	MountPath string
}

// RuntimeStorageSpec describes the agent-level runtime PVC declaration emitted
// by `ww agent create --with-persistence`.
type RuntimeStorageSpec struct {
	Size             string
	StorageClassName string
	Mounts           []RuntimeStorageMount
}

// DefaultRuntimeStorageSpec returns the durable harness runtime storage layout.
func DefaultRuntimeStorageSpec() *RuntimeStorageSpec {
	return &RuntimeStorageSpec{
		Size: "1Gi",
		Mounts: []RuntimeStorageMount{
			{SubPath: "logs", MountPath: "/home/agent/logs"},
			{SubPath: "state", MountPath: runtimeStatePath},
		},
	}
}

// ApplyHarnessTaskStoreDefault sets TASK_STORE_PATH unless the user already
// provided an explicit harness value.
func ApplyHarnessTaskStoreDefault(env map[string]string) map[string]string {
	if env == nil {
		env = map[string]string{}
	}
	if _, ok := env["TASK_STORE_PATH"]; !ok {
		env["TASK_STORE_PATH"] = RuntimeTaskStorePath
	}
	return env
}

// ApplyBackendTaskStoreDefaults sets TASK_STORE_PATH on backends whose final
// persistent mount list includes /home/agent/state. Explicit backend env wins.
func ApplyBackendTaskStoreDefaults(backends []BackendSpec) []BackendSpec {
	for i := range backends {
		if !backendHasMountPath(backends[i], runtimeStatePath) {
			continue
		}
		if backends[i].Env == nil {
			backends[i].Env = map[string]string{}
		}
		if _, ok := backends[i].Env["TASK_STORE_PATH"]; !ok {
			backends[i].Env["TASK_STORE_PATH"] = RuntimeTaskStorePath
		}
	}
	return backends
}

func backendHasMountPath(backend BackendSpec, mountPath string) bool {
	if backend.Storage == nil {
		return false
	}
	for _, m := range backend.Storage.Mounts {
		if m.MountPath == mountPath {
			return true
		}
	}
	return false
}
