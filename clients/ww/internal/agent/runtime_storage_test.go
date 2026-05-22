package agent

import "testing"

func TestDefaultRuntimeStorageSpec(t *testing.T) {
	got := DefaultRuntimeStorageSpec()
	if got == nil {
		t.Fatal("DefaultRuntimeStorageSpec returned nil")
	}
	if got.Size != "1Gi" {
		t.Errorf("Size = %q, want 1Gi", got.Size)
	}
	if len(got.Mounts) != 2 {
		t.Fatalf("mounts = %d, want 2", len(got.Mounts))
	}
	if got.Mounts[0].SubPath != "logs" || got.Mounts[0].MountPath != "/home/agent/logs" {
		t.Errorf("logs mount = %+v", got.Mounts[0])
	}
	if got.Mounts[1].SubPath != "state" || got.Mounts[1].MountPath != "/home/agent/state" {
		t.Errorf("state mount = %+v", got.Mounts[1])
	}
}

func TestApplyHarnessTaskStoreDefaultPreservesExplicitValue(t *testing.T) {
	got := ApplyHarnessTaskStoreDefault(map[string]string{"TASK_STORE_PATH": "/custom/tasks.db"})
	if got["TASK_STORE_PATH"] != "/custom/tasks.db" {
		t.Errorf("TASK_STORE_PATH = %q, want explicit value preserved", got["TASK_STORE_PATH"])
	}
}

func TestApplyBackendTaskStoreDefaultsOnlyWhenStateMounted(t *testing.T) {
	backends := []BackendSpec{
		{
			Name: "claude",
			Type: "claude",
			Storage: &BackendStorageSpec{Mounts: []BackendStorageMount{
				{SubPath: "state", MountPath: "/home/agent/state"},
			}},
		},
		{
			Name: "openai",
			Type: "openai",
			Storage: &BackendStorageSpec{Mounts: []BackendStorageMount{
				{SubPath: "logs", MountPath: "/home/agent/logs"},
			}},
		},
	}
	got := ApplyBackendTaskStoreDefaults(backends)
	if got[0].Env["TASK_STORE_PATH"] != RuntimeTaskStorePath {
		t.Errorf("claude TASK_STORE_PATH = %q, want %q", got[0].Env["TASK_STORE_PATH"], RuntimeTaskStorePath)
	}
	if got[1].Env != nil {
		t.Errorf("openai env = %+v, want nil without state mount", got[1].Env)
	}
}

func TestApplyBackendTaskStoreDefaultsPreservesExplicitValue(t *testing.T) {
	backends := []BackendSpec{{
		Name: "claude",
		Type: "claude",
		Env:  map[string]string{"TASK_STORE_PATH": "/custom/tasks.db"},
		Storage: &BackendStorageSpec{Mounts: []BackendStorageMount{
			{SubPath: "state", MountPath: "/home/agent/state"},
		}},
	}}
	got := ApplyBackendTaskStoreDefaults(backends)
	if got[0].Env["TASK_STORE_PATH"] != "/custom/tasks.db" {
		t.Errorf("TASK_STORE_PATH = %q, want explicit value preserved", got[0].Env["TASK_STORE_PATH"])
	}
}
