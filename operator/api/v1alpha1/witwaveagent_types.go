/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ImageSpec describes a container image used by an agent or backend.
type ImageSpec struct {
	// Repository is the image repository without tag or digest.
	// +kubebuilder:validation:MinLength=1
	Repository string `json:"repository"`

	// Tag is the image tag. If empty, the operator may fill in a default.
	// Ignored when Digest is set — digest pinning takes precedence (#1352).
	// +optional
	Tag string `json:"tag,omitempty"`

	// Digest pins the image by sha256:... digest. When set, the rendered
	// image reference is ``<Repository>@<Digest>`` and ``Tag`` is ignored.
	// Production deployments should digest-pin to protect against
	// registry re-tagging or supply-chain compromise (#1352).
	// +kubebuilder:validation:Pattern=`^sha256:[a-f0-9]{64}$`
	// +optional
	Digest string `json:"digest,omitempty"`

	// PullPolicy controls when the image is pulled.
	// +kubebuilder:validation:Enum=Always;IfNotPresent;Never
	// +optional
	PullPolicy corev1.PullPolicy `json:"pullPolicy,omitempty"`
}

// ConfigFile represents a single inline config file mounted into a container.
// The file is materialised as one key in a ConfigMap owned by the WitwaveAgent.
type ConfigFile struct {
	// Name is the ConfigMap key and the filename inside the container.
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`

	// MountPath is the absolute path inside the container.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:Pattern=`^/.*`
	MountPath string `json:"mountPath"`

	// Content is the literal file contents.
	// +kubebuilder:validation:MinLength=1
	Content string `json:"content"`
}

// ProbeSpec describes a single probe's timing parameters.
// Defaults match the Helm chart's probes.* defaults.
type ProbeSpec struct {
	// +kubebuilder:default=10
	// +optional
	InitialDelaySeconds int32 `json:"initialDelaySeconds,omitempty"`

	// +kubebuilder:default=30
	// +optional
	PeriodSeconds int32 `json:"periodSeconds,omitempty"`

	// +kubebuilder:default=5
	// +optional
	TimeoutSeconds int32 `json:"timeoutSeconds,omitempty"`

	// +kubebuilder:default=3
	// +optional
	FailureThreshold int32 `json:"failureThreshold,omitempty"`
}

// ProbesSpec bundles startup, liveness, and readiness probe timings.
type ProbesSpec struct {
	// +optional
	Liveness *ProbeSpec `json:"liveness,omitempty"`

	// +optional
	Readiness *ProbeSpec `json:"readiness,omitempty"`

	// Startup configures the kubelet's startupProbe. The probe targets
	// /health/start on backend containers that implement the three-probe
	// model (claude/openai/gemini, post-#1686). The echo backend retains
	// /health-only behaviour and does not get a startupProbe.
	// +optional
	Startup *ProbeSpec `json:"startup,omitempty"`
}

// PreStopSpec configures a `lifecycle.preStop` sleep hook on every container
// in the agent pod. When enabled, each container sleeps for DelaySeconds
// before SIGTERM arrives, giving in-flight A2A requests, scheduled jobs, and
// webhook deliveries a coordinated drain window (#447). Mirrors the
// `preStop` block in `charts/witwave/values.yaml` (#547, #512). Keep
// DelaySeconds strictly less than Spec.TerminationGracePeriodSeconds so
// SIGTERM has enough remaining grace to complete graceful shutdown before
// SIGKILL.
type PreStopSpec struct {
	// Enabled toggles rendering of the preStop sleep hook. Off by default —
	// opt in for production deployments where graceful shutdown matters.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// DelaySeconds is the preStop sleep duration. Must be strictly less
	// than the pod's terminationGracePeriodSeconds when Enabled=true.
	// +kubebuilder:default=5
	// +kubebuilder:validation:Minimum=0
	// +optional
	DelaySeconds int32 `json:"delaySeconds,omitempty"`
}

// AutoscalingSpec controls optional HorizontalPodAutoscaler creation.
type AutoscalingSpec struct {
	// Enabled creates an HPA for the agent's Deployment.
	// When true, the Deployment's replicas field is omitted so the HPA owns it.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// +kubebuilder:default=1
	// +optional
	MinReplicas int32 `json:"minReplicas,omitempty"`

	// +kubebuilder:default=3
	// +optional
	MaxReplicas int32 `json:"maxReplicas,omitempty"`

	// +optional
	TargetCPUUtilizationPercentage *int32 `json:"targetCPUUtilizationPercentage,omitempty"`

	// +optional
	TargetMemoryUtilizationPercentage *int32 `json:"targetMemoryUtilizationPercentage,omitempty"`
}

// PodDisruptionBudgetSpec controls optional PodDisruptionBudget creation.
// Exactly one of MinAvailable or MaxUnavailable should be set.
type PodDisruptionBudgetSpec struct {
	// Enabled creates a PodDisruptionBudget for the agent's Deployment.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// +optional
	MinAvailable *int32 `json:"minAvailable,omitempty"`

	// +optional
	MaxUnavailable *int32 `json:"maxUnavailable,omitempty"`
}

// BackendStorageMount maps a sub-path of a backend's PVC to a mount path.
type BackendStorageMount struct {
	// +kubebuilder:validation:MinLength=1
	SubPath string `json:"subPath"`
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:Pattern=`^/.*`
	MountPath string `json:"mountPath"`
}

// BackendStorageSpec configures optional persistent storage for a backend.
// Either ExistingClaim (reference a pre-existing PVC) or a new PVC created by
// the operator is used; when Enabled=true and ExistingClaim is empty the
// operator creates a PVC named <agent>-<backend>-data.
type BackendStorageSpec struct {
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Size is a Kubernetes resource.Quantity string (e.g. "10Gi", "500M").
	// The pattern matches the syntax accepted by resource.ParseQuantity so
	// typos like "10 GB" or "10gibs" are rejected at admission rather than
	// surfacing as a PVC `ProvisioningFailed` event on the cluster (#1254).
	// +kubebuilder:validation:Pattern=^([+-]?[0-9.]+)([eEinumkKMGTP]*[-+]?[0-9]*)?$
	// +optional
	Size string `json:"size,omitempty"`

	// +optional
	StorageClassName string `json:"storageClassName,omitempty"`

	// +optional
	ExistingClaim string `json:"existingClaim,omitempty"`

	// AccessModes is the list of access modes for the operator-created
	// backend PVC. Defaults to [ReadWriteOnce] when empty for backward
	// compatibility with single-replica deployments. Set to [ReadWriteMany]
	// when running HPA-scaled backends on RWX-capable storage, or
	// [ReadWriteOncePod] (K8s 1.27+) for stricter single-pod isolation.
	// Ignored when ExistingClaim is set — the pre-existing PVC's access
	// modes are honoured as-is.
	// +optional
	AccessModes []corev1.PersistentVolumeAccessMode `json:"accessModes,omitempty"`

	// +optional
	Mounts []BackendStorageMount `json:"mounts,omitempty"`
}

// BackendSpec defines one backend sidecar container.
type BackendSpec struct {
	// Name identifies the backend (e.g. claude, openai, gemini, echo). Used as the
	// container name and the backend ID in routing. MaxLength caps the
	// name well under the Kubernetes container-name limit (63) so the
	// downstream container name `<agent>-<backend>` still fits comfortably
	// even for long agent names (#1253).
	// +kubebuilder:validation:Pattern=^[a-z0-9][a-z0-9-]*$
	// +kubebuilder:validation:MaxLength=30
	Name string `json:"name"`

	// Enabled toggles whether this backend's container, PVC, and any
	// per-backend ConfigMap get reconciled. Defaults to true. Mirrors
	// the per-backend `enabled` flag in the Helm chart (#chart beta.32).
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Image is the backend container image.
	Image ImageSpec `json:"image"`

	// Port is the HTTP port the backend listens on inside the pod.
	// +kubebuilder:default=8000
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=64535
	// +optional
	Port int32 `json:"port,omitempty"`

	// Model is the default model string passed to the backend.
	// +optional
	Model string `json:"model,omitempty"`

	// Resources are the CPU/memory requests and limits for this backend.
	// +optional
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`

	// Env adds environment variables directly.
	// +optional
	Env []corev1.EnvVar `json:"env,omitempty"`

	// EnvFrom sources env vars from Secrets or ConfigMaps. When
	// Credentials is set to a resolvable value, the resolved Secret is
	// prepended to this list at render time so legacy `envFrom` shapes
	// keep working alongside the resolver.
	// +optional
	EnvFrom []corev1.EnvFromSource `json:"envFrom,omitempty"`

	// Credentials is the unified dev/prod credentials resolver for
	// this backend (#witwave.resolveCredentials parity). Three modes:
	// ExistingSecret (production), inline Secrets map with
	// AcknowledgeInsecureInline=true (dev, operator reconciles a
	// Secret keyed by the map), or empty (legacy EnvFrom passthrough).
	// +optional
	Credentials *BackendCredentialsSpec `json:"credentials,omitempty"`

	// Config mounts inline config files into the backend container.
	// +optional
	// +kubebuilder:validation:MaxItems=50
	Config []ConfigFile `json:"config,omitempty"`

	// Storage provisions or references a PVC for backend persistence.
	// +optional
	Storage *BackendStorageSpec `json:"storage,omitempty"`

	// GitMappings materialise files or directories from a named GitSync
	// repo into this backend container. The referenced GitSync name must
	// exist in WitwaveAgentSpec.GitSyncs. Mirrors the chart's per-backend
	// `gitMappings` block (#475).
	// +optional
	GitMappings []GitMappingSpec `json:"gitMappings,omitempty"`
}

// GitSyncCredentialsSpec is the dev-friendly / production-friendly
// credentials resolver for a GitSync entry, mirroring the chart's
// `witwave.resolveCredentials` helper for gitSync scope.
//
// Three mutually-exclusive modes:
//
//  1. ExistingSecret — reference a pre-created Secret. The operator
//     never writes to it. Production default.
//
//  2. Inline (Username + Token + AcknowledgeInsecureInline=true) —
//     the operator reconciles a per-entry Secret keyed
//     `GITSYNC_USERNAME` / `GITSYNC_PASSWORD` and envFroms it into
//     the git-sync init + sidecar. The ack flag is load-bearing: the
//     admission webhook refuses inline values without it because they
//     land in etcd + CR history and are recoverable via
//     `kubectl get witwaveagent -o yaml`.
//
//  3. Empty — the entry's legacy `EnvFrom` list is used as-is (no
//     operator-managed Secret, no Secret reconciliation).
//
// When both ExistingSecret and inline values are set, ExistingSecret
// wins and the inline values are ignored.
type GitSyncCredentialsSpec struct {
	// ExistingSecret references a pre-created Secret containing the
	// gitSync credentials (typically GITSYNC_USERNAME / GITSYNC_PASSWORD
	// for HTTPS, or an SSH key env for git+ssh).
	// +optional
	ExistingSecret string `json:"existingSecret,omitempty"`

	// Username is the inline git username (dev path only). Must be
	// combined with AcknowledgeInsecureInline=true.
	// +optional
	Username string `json:"username,omitempty"`

	// Token is the inline git token or password (dev path only).
	// Must be combined with AcknowledgeInsecureInline=true.
	// +optional
	Token string `json:"token,omitempty"`

	// AcknowledgeInsecureInline MUST be true for inline Username/Token
	// to be accepted. The admission webhook rejects any WitwaveAgent that
	// sets Username or Token without this flag. The name is intentionally
	// long and awkward — it shows up in CR YAML and is the one place
	// users accept the security tradeoff explicitly.
	// +optional
	AcknowledgeInsecureInline bool `json:"acknowledgeInsecureInline,omitempty"`
}

// BackendCredentialsSpec is the dev-friendly / production-friendly
// credentials resolver for a backend entry, mirroring the chart's
// `witwave.resolveCredentials` helper for backend scope. Same three-mode
// semantics as GitSyncCredentialsSpec; the inline form carries an
// open-ended map of env-var-name → value so each backend can set its
// own token shape (CLAUDE_CODE_OAUTH_TOKEN, OPENAI_API_KEY,
// GOOGLE_API_KEY, …) without the CRD having to enumerate them.
type BackendCredentialsSpec struct {
	// ExistingSecret references a pre-created Secret containing the
	// backend credentials. The Secret's keys become the backend
	// container's env-var names.
	// +optional
	ExistingSecret string `json:"existingSecret,omitempty"`

	// Secrets is the inline env-var → value map (dev path only). Keys
	// become Secret data keys and environment variable names inside
	// the backend container. Must be combined with
	// AcknowledgeInsecureInline=true.
	// +optional
	Secrets map[string]string `json:"secrets,omitempty"`

	// AcknowledgeInsecureInline MUST be true for inline Secrets to be
	// accepted. See GitSyncCredentialsSpec for the rationale.
	// +optional
	AcknowledgeInsecureInline bool `json:"acknowledgeInsecureInline,omitempty"`
}

// GitSyncSpec configures a git-sync sidecar that clones a repo into /git
// on the pod and keeps it in sync. An init container runs the initial
// clone before the agent starts. Auth credentials are typically injected
// via EnvFrom referencing a Secret (e.g. GITSYNC_USERNAME, GITSYNC_PASSWORD,
// GITSYNC_SSH_KEY_FILE). Mirrors the chart's `gitSyncs[]` entry shape
// (charts/witwave/values.yaml).
type GitSyncSpec struct {
	// Name is the unique identifier for this git-sync entry within the
	// agent. Used to name the sidecar container (git-sync-<name>), the
	// init container (git-sync-init-<name>), and as the `gitSync`
	// reference in GitMappingSpec entries.
	// +kubebuilder:validation:Pattern=^[a-z0-9][a-z0-9-]*$
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`

	// Repo is the git repository URL (HTTPS or SSH).
	// +kubebuilder:validation:MinLength=1
	Repo string `json:"repo"`

	// Ref is the branch, tag, or commit SHA to sync. Defaults to HEAD.
	// +optional
	Ref string `json:"ref,omitempty"`

	// Period is the sync interval (e.g. "30s", "1m"). Defaults to "60s".
	// +optional
	Period string `json:"period,omitempty"`

	// Depth is the clone depth. 1 is sufficient for config-only repos.
	// +kubebuilder:validation:Minimum=0
	// +optional
	Depth int32 `json:"depth,omitempty"`

	// Image optionally overrides the default git-sync image for this
	// entry. When unset, the operator's default git-sync image is used
	// (ghcr.io/witwave-ai/images/git-sync:<appVersion>).
	// +optional
	Image *ImageSpec `json:"image,omitempty"`

	// EnvFrom sources env vars from Secrets or ConfigMaps, injected
	// into the git-sync init + sidecar containers for this entry.
	// When Credentials is set to a resolvable value, the resolved
	// Secret is prepended to this list at render time — the legacy
	// `envFrom` shape keeps working alongside the resolver.
	// +optional
	EnvFrom []corev1.EnvFromSource `json:"envFrom,omitempty"`

	// Credentials is the unified dev/prod credentials resolver for this
	// entry (#witwave.resolveCredentials parity). Three modes: ExistingSecret
	// (pre-created Secret, production default), inline Username+Token
	// with AcknowledgeInsecureInline=true (operator reconciles a Secret,
	// dev path), or empty (falls back to the legacy EnvFrom list).
	// +optional
	Credentials *GitSyncCredentialsSpec `json:"credentials,omitempty"`
}

// GitMappingSpec copies a file or directory from a named GitSync repo
// into the target container at a given destination path. Mirrors the
// chart's `gitMappings[]` entry shape so on-cluster behaviour matches
// byte-for-byte (#475).
type GitMappingSpec struct {
	// GitSync is the name of a GitSyncSpec in WitwaveAgentSpec.GitSyncs.
	// +kubebuilder:validation:Pattern=^[a-z0-9][a-z0-9-]*$
	// +kubebuilder:validation:MinLength=1
	GitSync string `json:"gitSync"`

	// Src is the source path within the repo, relative to the repo
	// root. A trailing "/" indicates a directory copy; otherwise a
	// single-file copy is performed.
	// +kubebuilder:validation:MinLength=1
	Src string `json:"src"`

	// Dest is the destination path inside the target container. The
	// mapped path is materialised from an emptyDir volume mounted into
	// the container, populated by the git-map-init container and
	// kept in sync by the git-sync sidecar's exechook.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:Pattern=`^/.*`
	Dest string `json:"dest"`
}

// SharedStorageType selects the VolumeSource the operator emits for the
// agent-wide shared volume. Mirrors the chart's `sharedStorage.storageType`
// enum (`pvc`, `hostPath`) so operator-rendered pods match Helm-rendered
// pods byte-for-byte (#481, #611).
// +kubebuilder:validation:Enum=pvc;hostPath
type SharedStorageType string

const (
	// SharedStorageTypePVC selects a PersistentVolumeClaim volume source.
	// When ExistingClaim is set the operator references it verbatim; when
	// ExistingClaim is empty the operator reconciles a PVC named
	// "<agent>-shared" in the agent's namespace.
	SharedStorageTypePVC SharedStorageType = "pvc"

	// SharedStorageTypeHostPath selects a HostPath volume source. Intended
	// for single-node clusters (kind, Docker Desktop, minikube) that lack
	// a ReadWriteMany storage class; not recommended for production
	// multi-node clusters.
	SharedStorageTypeHostPath SharedStorageType = "hostPath"
)

// SharedStorageSpec configures a volume mounted into every container of the
// agent pod (harness + every backend sidecar). Mirrors the chart's
// `sharedStorage.*` block (charts/witwave/values.yaml) so the operator path has
// feature parity with the chart path (#481, #611).
//
// Backward compatibility: older CRs set only `claimName` and rely on the
// operator to treat that as a reference to a pre-existing PVC. When
// `claimName` is set the operator treats the spec as
// `{enabled: true, storageType: pvc, existingClaim: <claimName>}` — so
// existing CRs keep working without edits. New CRs should set `enabled`
// plus `storageType` plus either `existingClaim`, PVC sizing fields, or
// `hostPath`.
type SharedStorageSpec struct {
	// Enabled toggles rendering of the shared volume. When false (or when
	// the field is omitted), no shared volume is mounted and the operator
	// reconciles away any shared PVC it previously created.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// StorageType selects the VolumeSource: `pvc` (default) or `hostPath`.
	// +kubebuilder:default=pvc
	// +optional
	StorageType SharedStorageType `json:"storageType,omitempty"`

	// MountPath is the absolute path inside each container the shared
	// volume is mounted at. Defaults to `/data/shared` to match the
	// chart.
	// +kubebuilder:default=/data/shared
	// +kubebuilder:validation:Pattern=`^/.*`
	// +optional
	MountPath string `json:"mountPath,omitempty"`

	// ClaimName is a deprecated alias for ExistingClaim retained so CRs
	// authored against the original `SharedStorageRef` shape keep working
	// without edits. When set and ExistingClaim is empty, the operator
	// treats the spec as enabled=true, storageType=pvc,
	// existingClaim=<claimName>.
	// Deprecated: set `existingClaim` instead.
	// +optional
	ClaimName string `json:"claimName,omitempty"`

	// ExistingClaim references a pre-existing PersistentVolumeClaim. Only
	// meaningful when StorageType=pvc. When empty and StorageType=pvc the
	// operator creates a PVC named `<agent>-shared`.
	// +optional
	ExistingClaim string `json:"existingClaim,omitempty"`

	// Size is the PVC storage request (e.g. `1Gi`). Only meaningful when
	// StorageType=pvc and ExistingClaim is empty. Defaults to `1Gi`.
	// +optional
	Size string `json:"size,omitempty"`

	// StorageClassName is the storage class for the created PVC. Only
	// meaningful when StorageType=pvc and ExistingClaim is empty. When
	// empty the cluster default storage class is used.
	// +optional
	StorageClassName string `json:"storageClassName,omitempty"`

	// AccessModes are the PVC access modes. Only meaningful when
	// StorageType=pvc and ExistingClaim is empty. Defaults to
	// `[ReadWriteMany]` matching the chart's default.
	// +optional
	AccessModes []corev1.PersistentVolumeAccessMode `json:"accessModes,omitempty"`

	// HostPath is the node-local directory to bind-mount. Required (and
	// must be absolute) when StorageType=hostPath. Ignored otherwise.
	// Prefer hostPath for single-node local clusters only.
	// +kubebuilder:validation:Pattern=`^(|/.*)$`
	// +optional
	HostPath string `json:"hostPath,omitempty"`

	// HostPathType mirrors corev1.HostPathType. When nil the operator
	// defaults to `DirectoryOrCreate` so pods don't fail to start on
	// fresh nodes that have not yet materialised the directory (matches
	// the chart's fixed `type: DirectoryOrCreate`).
	// +optional
	HostPathType *corev1.HostPathType `json:"hostPathType,omitempty"`
}

// SharedStorageRef is the deprecated alias for SharedStorageSpec retained so
// existing Go consumers don't break. New code should reference
// SharedStorageSpec directly.
// Deprecated: use SharedStorageSpec.
type SharedStorageRef = SharedStorageSpec

// RuntimeStorageMount maps a sub-path of the agent runtime PVC to a harness
// container mount path.
type RuntimeStorageMount struct {
	// +kubebuilder:validation:MinLength=1
	SubPath string `json:"subPath"`
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:Pattern=`^/.*`
	MountPath string `json:"mountPath"`
}

// RuntimeStorageSpec configures optional persistent storage for the harness
// container's runtime state. It is separate from backend storage so harness
// task bookkeeping does not depend on a specific backend being present.
type RuntimeStorageSpec struct {
	// Enabled creates/mounts the runtime storage PVC. When false or omitted,
	// harness runtime state remains container-local.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Size is a Kubernetes resource.Quantity string (e.g. "1Gi", "500M").
	// Only meaningful when ExistingClaim is empty.
	// +kubebuilder:validation:Pattern=^([+-]?[0-9.]+)([eEinumkKMGTP]*[-+]?[0-9]*)?$
	// +optional
	Size string `json:"size,omitempty"`

	// StorageClassName is the storage class for the created PVC. When empty
	// the cluster default storage class is used.
	// +optional
	StorageClassName string `json:"storageClassName,omitempty"`

	// ExistingClaim references a pre-existing PersistentVolumeClaim. When set,
	// the operator mounts it and does not create a runtime PVC.
	// +optional
	ExistingClaim string `json:"existingClaim,omitempty"`

	// AccessModes are the PVC access modes for the operator-created claim.
	// Defaults to [ReadWriteOnce] for the single-pod harness runtime path.
	// +optional
	AccessModes []corev1.PersistentVolumeAccessMode `json:"accessModes,omitempty"`

	// Mounts maps runtime PVC subdirectories into the harness container. When
	// empty, the operator mounts `logs:/home/agent/logs` and
	// `state:/home/agent/state`.
	// +optional
	Mounts []RuntimeStorageMount `json:"mounts,omitempty"`
}

// KubernetesApiAccessMode controls the permission preset rendered for an
// agent's Kubernetes API identity.
type KubernetesApiAccessMode string

const (
	// KubernetesApiAccessModeReadOnly renders a conservative diagnostics
	// surface: get/list/watch on common workload and Witwave resources, plus
	// get on pod logs. It deliberately grants no create/update/patch/delete.
	KubernetesApiAccessModeReadOnly KubernetesApiAccessMode = "readOnly"

	// KubernetesApiAccessModeNamespaceWrite renders a namespace-scoped
	// operational surface for remediation. It can mutate selected namespaced
	// workload/config resources and evict/delete pods, but still excludes
	// secrets, RBAC mutation, raw pod creation, and cluster-scoped resources.
	KubernetesApiAccessModeNamespaceWrite KubernetesApiAccessMode = "namespaceWrite"
)

// KubernetesApiAccessSpec configures an operator-managed Kubernetes API
// identity for an agent pod. When enabled, the operator renders a per-agent
// ServiceAccount plus a per-agent namespace-scoped Role/RoleBinding so tools
// such as kubectl can authenticate from backend containers.
type KubernetesApiAccessSpec struct {
	// Enabled creates and wires the per-agent Kubernetes API identity. When
	// false or omitted, the operator leaves the pod in the hardened default:
	// no service account token mounted.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Name overrides the generated ServiceAccount, Role, and RoleBinding
	// name. Defaults to the WitwaveAgent name, so an agent named "mira" gets
	// ServiceAccount/mira, Role/mira, and RoleBinding/mira in its namespace.
	// +kubebuilder:validation:MaxLength=253
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
	// +optional
	Name string `json:"name,omitempty"`

	// Mode selects the RBAC preset. Defaults to readOnly. namespaceWrite grants
	// a bounded namespace-local remediation surface while still excluding
	// secrets, RBAC mutation, raw pod creation, and cluster-scoped resources.
	// +kubebuilder:validation:Enum=readOnly;namespaceWrite
	// +kubebuilder:default=readOnly
	// +optional
	Mode KubernetesApiAccessMode `json:"mode,omitempty"`
}

// WitwaveAgentSpec defines the desired state of WitwaveAgent.
type WitwaveAgentSpec struct {
	// Enabled toggles whether the entire agent (Deployment, Service, PVCs,
	// ConfigMaps, HPA, PDB, Dashboard) gets reconciled. Defaults to true.
	// Mirrors the per-agent `enabled` flag in the Helm chart (#chart beta.32).
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Port is the HTTP port harness listens on (Service + probe target).
	// +kubebuilder:default=8000
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=64535
	// +optional
	Port int32 `json:"port,omitempty"`

	// ServiceType controls the agent Service.spec.type. Defaults to ClusterIP.
	// Set to NodePort or LoadBalancer to expose the agent outside the
	// cluster without an Ingress (#chart beta.31 / #466).
	// +kubebuilder:validation:Enum=ClusterIP;NodePort;LoadBalancer
	// +kubebuilder:default=ClusterIP
	// +optional
	ServiceType corev1.ServiceType `json:"serviceType,omitempty"`

	// TerminationGracePeriodSeconds overrides the pod's grace window between
	// SIGTERM and SIGKILL. Defaults to 60s, matching the chart-managed
	// agent pods. Increase for workloads with long-running per-request work
	// (multi-minute jobs, slow webhook deliveries) that need more drain
	// time during voluntary disruption (#458).
	// +kubebuilder:validation:Minimum=0
	// +optional
	TerminationGracePeriodSeconds *int64 `json:"terminationGracePeriodSeconds,omitempty"`

	// PreStop adds a `lifecycle.preStop` sleep hook to every container in
	// the agent pod. Mirrors the chart-level `preStop` block (#547, #512).
	// Off by default; when enabled, PreStop.DelaySeconds must be strictly
	// less than TerminationGracePeriodSeconds (defaulting to 60s) so
	// SIGTERM still fires with enough remaining grace for graceful
	// shutdown before SIGKILL.
	// +optional
	PreStop *PreStopSpec `json:"preStop,omitempty"`

	// Image is the harness orchestrator image.
	Image ImageSpec `json:"image"`

	// ImagePullSecrets used by the agent pod.
	// +optional
	ImagePullSecrets []corev1.LocalObjectReference `json:"imagePullSecrets,omitempty"`

	// ServiceAccountName is the name of a pre-existing ServiceAccount to
	// attach to the agent pod. The operator does not create or manage the
	// ServiceAccount or its RBAC bindings — supply an externally-managed SA
	// that already has the permissions your MCP tools require (e.g. a
	// ServiceAccount bound to a Role that lets `mcp-kubernetes` or
	// `mcp-helm` talk to the Kubernetes API in-cluster).
	//
	// When this field is set, AutomountServiceAccountToken flips to true
	// automatically so the SA token is projected into every container in
	// the pod. Note: the token is visible to every sibling container
	// (harness + all backend sidecars + any MCP sidecars) — there is
	// no per-container scoping. When unset, the pod uses the namespace's
	// default ServiceAccount with token automount disabled.
	//
	// See risk #538.
	// +optional
	ServiceAccountName string `json:"serviceAccountName,omitempty"`

	// AutomountServiceAccountToken explicitly controls whether the
	// ServiceAccount token is mounted into the agent pod. When nil (the
	// default), the operator infers the value: false when
	// ServiceAccountName is empty (hardened default ServiceAccount, no
	// token), true when ServiceAccountName is set (so MCP tools can reach
	// the Kubernetes API). Set this field explicitly only when you need to
	// override the inferred value — e.g. to mount the default SA's token
	// without naming a custom SA, or to keep the token out of a pod that
	// does name a custom SA.
	//
	// See risk #538.
	// +optional
	AutomountServiceAccountToken *bool `json:"automountServiceAccountToken,omitempty"`

	// KubernetesApiAccess optionally creates and wires a per-agent
	// Kubernetes API identity. This is the managed replacement for manually
	// creating ServiceAccounts/RBAC and then setting ServiceAccountName.
	// It remains opt-in because the projected service-account token is a
	// powerful credential even when the rendered RBAC is read-only.
	// +optional
	KubernetesApiAccess *KubernetesApiAccessSpec `json:"kubernetesApiAccess,omitempty"`

	// Resources are CPU/memory requests and limits for the harness container.
	// +optional
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`

	// Env adds environment variables to the harness container.
	// +optional
	Env []corev1.EnvVar `json:"env,omitempty"`

	// EnvFrom sources env vars from Secrets or ConfigMaps for the
	// harness container.
	// +optional
	EnvFrom []corev1.EnvFromSource `json:"envFrom,omitempty"`

	// Metrics toggles Prometheus scrape annotations on the Service.
	// +optional
	Metrics MetricsSpec `json:"metrics,omitempty"`

	// Config mounts inline config files into the harness container.
	// +optional
	Config []ConfigFile `json:"config,omitempty"`

	// Probes overrides liveness/readiness probe timing.
	// +optional
	Probes *ProbesSpec `json:"probes,omitempty"`

	// Autoscaling optionally creates an HPA for the agent Deployment.
	// +optional
	Autoscaling *AutoscalingSpec `json:"autoscaling,omitempty"`

	// PodDisruptionBudget optionally creates a PDB for the agent Deployment.
	// +optional
	PodDisruptionBudget *PodDisruptionBudgetSpec `json:"podDisruptionBudget,omitempty"`

	// Backends lists the backend sidecars (claude, openai, gemini, echo, …).
	// +kubebuilder:validation:MinItems=1
	// +kubebuilder:validation:MaxItems=50
	Backends []BackendSpec `json:"backends"`

	// SharedStorage optionally mounts a volume into every container of the
	// agent pod. Supports PVC mode (reference an existing claim or have
	// the operator create one sized per spec) and hostPath mode (for
	// single-node clusters). Mirrors charts/witwave/values.yaml's
	// `sharedStorage.*` block (#481, #611).
	// +optional
	SharedStorage *SharedStorageSpec `json:"sharedStorage,omitempty"`

	// RuntimeStorage optionally mounts durable runtime state into the harness
	// container. It is distinct from SharedStorage (team/project files) and
	// backend storage (LLM backend-native sessions/memory/logs).
	// +optional
	RuntimeStorage *RuntimeStorageSpec `json:"runtimeStorage,omitempty"`

	// GitSyncs declares git-sync sidecar(s) for this agent. Each entry
	// produces one init container (one-time clone) and one long-running
	// sidecar that keeps /git in sync with the remote repo. All
	// containers in the pod share the /git volume. Mirrors the chart's
	// per-agent `gitSyncs` block (#475).
	// +optional
	// +kubebuilder:validation:MaxItems=20
	GitSyncs []GitSyncSpec `json:"gitSyncs,omitempty"`

	// GitMappings materialise files or directories from named GitSyncs
	// into the harness container. A referenced GitSync name must
	// exist in GitSyncs. Per-backend overrides live in
	// BackendSpec.GitMappings. Mirrors the chart's per-agent
	// `gitMappings` block (#475).
	// +optional
	GitMappings []GitMappingSpec `json:"gitMappings,omitempty"`

	// ServiceMonitor optionally creates a monitoring.coreos.com/v1
	// ServiceMonitor for the agent when the Prometheus Operator CRDs
	// are installed (#476). Gated by spec.metrics.enabled and a CRD
	// presence check at reconcile time; no-ops on clusters without
	// prometheus-operator.
	// +optional
	ServiceMonitor *ServiceMonitorSpec `json:"serviceMonitor,omitempty"`

	// PodMonitor optionally creates a monitoring.coreos.com/v1 PodMonitor
	// for the agent's backend containers (#582). Gated by
	// spec.metrics.enabled and a CRD presence check at reconcile time;
	// no-ops on clusters without prometheus-operator. Complementary to
	// ServiceMonitor: use PodMonitor to scrape per-backend /metrics
	// directly (tokens, tool-use, context usage).
	// +optional
	PodMonitor *PodMonitorSpec `json:"podMonitor,omitempty"`

	// PrometheusRule optionally creates a monitoring.coreos.com/v1
	// PrometheusRule per WitwaveAgent that ships the chart's default
	// alert set (#1746). Gated by a CRD presence probe at reconcile
	// time; no-ops on clusters without prometheus-operator.
	// +optional
	PrometheusRule *PrometheusRuleSpec `json:"prometheusRule,omitempty"`

	// Cors mirrors the chart's `cors.*` block (#763, #1748). When set,
	// the operator stamps CORS_ALLOW_ORIGINS (and CORS_ALLOW_WILDCARD
	// when explicitly opted in) onto the harness container. Per-container
	// spec.env still wins on key collision since it's later-merged.
	// +optional
	Cors *CorsSpec `json:"cors,omitempty"`

	// MetricsPort is DEPRECATED (#836). Chart #687 moved harness and backend
	// containers to a per-container metrics port = app_port + 1000 so two
	// containers in the same pod no longer collide on :9000. The operator
	// follows the same rule: when MetricsPort is zero (the default), each
	// container computes its own metrics listener as `port + 1000` (harness
	// port for harness; backend port for each backend container). When
	// MetricsPort is non-zero it overrides the computation for every
	// container in the pod — this is kept for backward compatibility with
	// existing WitwaveAgent manifests and for MCP-tool-style pods that want a
	// single fixed port. Prefer leaving it unset on new deployments.
	//
	// DEPRECATED (#1374): non-zero values in multi-container pods collide
	// (harness:9000 + backend:9000) and mask the intent of the #1249 /
	// #1322 unique-named-port work. New deployments should leave this
	// unset; future v1 may reject non-zero values on multi-container
	// pods via the webhook.
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=65535
	// +optional
	MetricsPort int32 `json:"metricsPort,omitempty"`

	// Dashboard optionally deploys the Vue 3 dashboard (#470) alongside the
	// agent. The operator renders a per-agent dashboard (one per WitwaveAgent),
	// independent of the cluster-wide dashboard the Helm chart deploys.
	// Disabled by default.
	// +optional
	Dashboard *DashboardSpec `json:"dashboard,omitempty"`

	// PodAnnotations are merged onto the agent pod template's
	// `metadata.annotations`. Common uses: ServiceMesh sidecar injection
	// (e.g. `linkerd.io/inject: enabled`), trace sampling overrides,
	// observability-tool discovery hints (#477).
	//
	// Conflict precedence: operator-managed keys always win. User values
	// are applied first, then overwritten by any operator-managed
	// annotation (e.g. the `prometheus.io/*` set stamped when
	// `spec.metrics.podAnnotations` is enabled — #472). This prevents
	// silent loss of operator behaviour when users and the operator pick
	// overlapping keys.
	// +optional
	// +kubebuilder:validation:MaxProperties=100
	PodAnnotations map[string]string `json:"podAnnotations,omitempty"`

	// PodLabels are merged onto the agent pod template's `metadata.labels`.
	// Common uses: custom NetworkPolicy / PodMonitor selectors, team or
	// cost-allocation labels (#477).
	//
	// Conflict precedence: operator-managed keys always win. User labels
	// are applied first, then overwritten by the operator's canonical
	// agent labels. User-supplied entries that collide with the
	// Deployment's selector keys (labelName / labelComponent /
	// labelPartOf / labelManagedBy) are ignored so the selector cannot
	// drift and orphan pods.
	// +optional
	// +kubebuilder:validation:MaxProperties=100
	PodLabels map[string]string `json:"podLabels,omitempty"`

	// NodeSelector constrains the agent pod onto nodes whose labels match
	// every entry. Mirrors the chart-side `nodeSelector` value (#603) so
	// operator- and Helm-rendered pods schedule identically.
	// +optional
	NodeSelector map[string]string `json:"nodeSelector,omitempty"`

	// Tolerations allow the agent pod onto nodes with matching taints.
	// Mirrors the chart-side `tolerations` value (#603).
	// +optional
	Tolerations []corev1.Toleration `json:"tolerations,omitempty"`

	// Affinity expresses node / pod affinity and anti-affinity rules for
	// the agent pod. Mirrors the chart-side `affinity` value (#603).
	// +optional
	Affinity *corev1.Affinity `json:"affinity,omitempty"`

	// TopologySpreadConstraints spreads agent replicas across zones /
	// nodes for HA. Mirrors the chart-side `topologySpreadConstraints`
	// value (#603).
	// +optional
	TopologySpreadConstraints []corev1.TopologySpreadConstraint `json:"topologySpreadConstraints,omitempty"`

	// PriorityClassName sets the pod's scheduling priority. Mirrors the
	// chart-side `priorityClassName` value (#603).
	// +optional
	PriorityClassName string `json:"priorityClassName,omitempty"`

	// ServicePort overrides the agent Service's `port` so it can differ
	// from the container port (#479). When unset, the Service `port` and
	// `targetPort` both resolve to `Spec.Port` (preserving current
	// behaviour). When set, `Service.spec.ports[0].port` becomes
	// `ServicePort` while `targetPort` continues to point at the
	// container port (`Spec.Port`). Mirrors the chart's `service.yaml`
	// which already separates Service port from target port.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=64535
	// +optional
	ServicePort *int32 `json:"servicePort,omitempty"`

	// Tracing controls OTel env injection on harness and backend
	// containers (#829). Mirrors the chart's `observability.tracing.*`
	// block and the `witwave.otelEnv` helper so operator-managed and
	// chart-managed agents emit identical OTEL_* env vars without
	// operators hand-crafting `env:` entries per container.
	// +optional
	Tracing *TracingSpec `json:"tracing,omitempty"`

	// MCPTools configures cluster-wide MCP tool Deployments + Services
	// the operator renders alongside the agent so operator-only installs
	// can expose the same `mcp-kubernetes` / `mcp-helm` surface the Helm
	// chart's `mcpTools` block provides (#830). Scaffold scope: the three
	// most load-bearing knobs (enabled, image, replicas) are wired end-to-
	// end; RBAC/clusterWide/resources are follow-up work.
	// +optional
	MCPTools *MCPToolsSpec `json:"mcpTools,omitempty"`

	// NetworkPolicy mirrors the chart's networkPolicy block (#759, #971).
	// When Enabled=true the operator renders a namespaced
	// networking.k8s.io/v1 NetworkPolicy targeting the agent pod. Scaffold
	// scope: a minimal default-closed ingress policy with opt-in peers
	// (dashboard, same-namespace, metrics scrapers) is wired end-to-end;
	// MCP-tool NetworkPolicies (the `allowWitwaveAgents` knob) are follow-up.
	// +optional
	NetworkPolicy *NetworkPolicySpec `json:"networkPolicy,omitempty"`

	// WorkspaceRefs lists the WitwaveWorkspaces this agent participates in.
	// Each referenced WitwaveWorkspace contributes its volumes, secrets, and
	// configFiles to every backend container of this agent. Membership is
	// agent-owned by design (see tmp/workspace-crd.md "Membership — agent-
	// owned") — the operator maintains WitwaveWorkspace.Status.BoundAgents as an
	// inverted index so the workspace-centric view stays available without
	// duplicating source-of-truth on the WitwaveWorkspace itself.
	// +optional
	// +kubebuilder:validation:MaxItems=20
	// +listType=map
	// +listMapKey=name
	WorkspaceRefs []WitwaveAgentWorkspaceRef `json:"workspaceRefs,omitempty"`
}

// WitwaveAgentWorkspaceRef references one WitwaveWorkspace by name. The referenced
// WitwaveWorkspace must live in the same namespace as the WitwaveAgent in v1alpha1;
// cross-namespace references are not yet supported (the design doc tracks
// `Spec.AgentSelector` membership policy and other knobs as v1.x followups).
type WitwaveAgentWorkspaceRef struct {
	// Name of the WitwaveWorkspace to bind. Must match an existing WitwaveWorkspace's
	// metadata.name in the same namespace as this WitwaveAgent.
	// +kubebuilder:validation:Pattern=^[a-z0-9][a-z0-9-]*$
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`
}

// NetworkPolicySpec mirrors charts/witwave/values.yaml `networkPolicy.*` so
// operator-rendered agents enforce the same pod-level isolation the chart
// provides (#759, #971). When Enabled=true and Ingress is empty, all
// ingress to the agent pod is denied — explicit fail-closed posture.
type NetworkPolicySpec struct {
	// Enabled toggles rendering of the per-agent NetworkPolicy. Off by
	// default — the cluster's historical default-open posture is
	// preserved unless an operator opts in.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Ingress configures inbound peer rules. Empty with Enabled=true is
	// a deliberate deny-all ingress policy; populate the sub-fields to
	// open specific paths.
	// +optional
	Ingress *NetworkPolicyIngressSpec `json:"ingress,omitempty"`

	// EgressOpen keeps the pod's egress unrestricted. Defaults to true,
	// matching the chart (backends need outbound reach to the apiserver,
	// OTel collectors, webhook destinations, DNS, and each other).
	// +kubebuilder:default=true
	// +optional
	EgressOpen *bool `json:"egressOpen,omitempty"`
}

// NetworkPolicyIngressSpec mirrors charts/witwave/values.yaml
// `networkPolicy.ingress.*`. Peers are expressed as raw v1 types so
// operators can mix namespaceSelector / podSelector / ipBlock without
// extra templating.
type NetworkPolicyIngressSpec struct {
	// AllowDashboard authorises the dashboard Service pods to reach the
	// agent pod on its app port. Defaults to true so the in-cluster
	// dashboard -> harness path works out of the box.
	// +kubebuilder:default=true
	// +optional
	AllowDashboard *bool `json:"allowDashboard,omitempty"`

	// AllowSameNamespace authorises every pod in the same namespace as
	// the WitwaveAgent to reach the agent pod on every port. Defaults to
	// false (stricter CIS-style posture).
	// +optional
	AllowSameNamespace bool `json:"allowSameNamespace,omitempty"`

	// MetricsFrom authorises traffic to the metrics port (app_port+1000)
	// from the listed peers. Entries are raw NetworkPolicyPeer objects.
	// +optional
	MetricsFrom []networkingv1.NetworkPolicyPeer `json:"metricsFrom,omitempty"`

	// AdditionalFrom applies to every port on the agent pod. Use
	// sparingly — prefer MetricsFrom for scrape traffic and AllowDashboard
	// for intra-release traffic.
	// +optional
	AdditionalFrom []networkingv1.NetworkPolicyPeer `json:"additionalFrom,omitempty"`
}

// TracingSpec mirrors the chart's observability.tracing.* values (#829).
// When Enabled is true, the operator stamps the OTEL_* env vars produced
// by `witwave.otelEnv` onto every harness and backend container, so chart and
// operator deployments converge on the same OTLP wiring.
type TracingSpec struct {
	// Enabled turns on the OTEL_ENABLED master toggle on every
	// operator-managed container. When false (the default) the operator
	// injects nothing.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Endpoint is forwarded verbatim as OTEL_EXPORTER_OTLP_ENDPOINT.
	// Required when Enabled is true AND the target OTel SDK does not
	// inherit a collector endpoint from the cluster (most deployments).
	// +optional
	Endpoint string `json:"endpoint,omitempty"`

	// Sampler is forwarded verbatim as OTEL_TRACES_SAMPLER.
	// Omitted when empty.
	// +optional
	Sampler string `json:"sampler,omitempty"`

	// SamplerArg is forwarded verbatim as OTEL_TRACES_SAMPLER_ARG.
	// Omitted when empty.
	// +optional
	SamplerArg string `json:"samplerArg,omitempty"`
}

// DashboardSpec configures an optional dashboard Deployment + Service per
// agent. The operator renders one Deployment and one Service scoped to the
// WitwaveAgent, so an agent can have its own dashboard instance independent of
// any release-level dashboard deployed via Helm (#470).
type DashboardSpec struct {
	// Enabled toggles creation of the dashboard Deployment + Service.
	Enabled bool `json:"enabled"`

	// Image is the dashboard container image.
	// +optional
	Image *ImageSpec `json:"image,omitempty"`

	// Replicas for the dashboard Deployment. Defaults to 1.
	// +kubebuilder:validation:Minimum=0
	// +optional
	Replicas *int32 `json:"replicas,omitempty"`

	// Port is the Service port (the container always listens on 3000).
	// +kubebuilder:default=80
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=64535
	// +optional
	Port int32 `json:"port,omitempty"`

	// ClusterDomain is the cluster DNS zone used to build the in-cluster
	// FQDN the dashboard's nginx proxies to (e.g. `cluster.local`,
	// `cluster.example`). nginx's `resolver` directive does not apply the
	// pod's /etc/resolv.conf search list, so the upstream must be an
	// absolute FQDN; this field lets clusters bootstrapped with a
	// non-default `--service-dns-domain` point the dashboard at the right
	// zone (risk #581). Must match `^[a-z0-9][a-z0-9.-]*[a-z0-9]$`.
	// Defaults to `cluster.local` for back-compat.
	// +kubebuilder:default=cluster.local
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=253
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$`
	// +optional
	ClusterDomain string `json:"clusterDomain,omitempty"`

	// HarnessURL is retained for CR backward-compatibility but no longer
	// read by the operator. Since beta.46 the dashboard owns cross-agent
	// routing and talks to each agent's service directly via the per-agent
	// nginx config the operator renders; the legacy /api/* catch-all that
	// this field fed is gone. Drop this field from CRs at your leisure; it
	// is ignored either way.
	// Deprecated: no effect since beta.46; remove on next breaking CRD bump.
	// +optional
	HarnessURL string `json:"harnessUrl,omitempty"`

	// Resources for the dashboard container.
	// +optional
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`

	// Ingress configures an optional networking.k8s.io/v1 Ingress for the
	// dashboard Service (#831). Scaffold scope: schema + fail-closed auth
	// guard. The reconciler in witwaveagent_dashboard_ingress.go renders the
	// Ingress when Enabled=true AND Auth.Mode has been explicitly set —
	// rendering an unauthenticated Ingress for the dashboard would expose
	// sensitive per-agent conversation history and is a fail-render mirror
	// of the chart guard introduced in #528.
	// +optional
	Ingress *DashboardIngressSpec `json:"ingress,omitempty"`
}

// DashboardIngressSpec mirrors the chart's `dashboard.ingress` block and
// is the only operator-managed entrypoint that reaches the dashboard
// Service from outside the cluster. The fail-closed posture is the whole
// point of the CRD gate: the reconciler refuses to render an Ingress
// until Auth.Mode is non-empty.
type DashboardIngressSpec struct {
	// Enabled toggles reconciliation of the Ingress. When false (the
	// default), no Ingress is rendered and any previously-owned Ingress
	// is left to ownerReferences GC on WitwaveAgent deletion.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// ClassName is the ingress class (optional — matches
	// networking.k8s.io/v1 IngressClassName). Examples: "nginx", "traefik".
	// +optional
	ClassName *string `json:"className,omitempty"`

	// Host is the Host header the ingress controller should route on
	// (e.g. "iris.example.com"). Required when Enabled=true.
	// +optional
	Host string `json:"host,omitempty"`

	// TLS wires a TLS block onto the rendered Ingress. When nil the
	// Ingress is rendered without TLS (discouraged; documented).
	// +optional
	TLS *DashboardIngressTLSSpec `json:"tls,omitempty"`

	// Auth is the fail-closed auth configuration. The reconciler refuses
	// to render the Ingress unless Auth.Mode is set — mirroring the
	// chart's fail-render guard (#528). Setting Auth.Mode="none" is an
	// explicit opt-out that the operator still logs loudly on every
	// reconcile.
	// +optional
	Auth *DashboardAuthSpec `json:"auth,omitempty"`
}

// DashboardIngressTLSSpec is the minimal per-host TLS block. SecretName
// refers to a tls.crt / tls.key Secret the cluster operator must create
// out of band (cert-manager, kubectl, etc.).
type DashboardIngressTLSSpec struct {
	// SecretName references a kubernetes.io/tls Secret in the same
	// namespace as the WitwaveAgent. Required when TLS is set.
	// +kubebuilder:validation:MinLength=1
	SecretName string `json:"secretName"`
}

// DashboardAuthSpec mirrors the chart's dashboard.auth guard. Modes:
//
//   - "basic"  — basic-auth Secret is reconciled and stamped into the
//     Ingress via nginx-style annotations (follow-up: richer controller
//     wiring for non-nginx ingress classes).
//   - "none"   — explicit opt-out; Ingress is rendered without auth and
//     the operator logs a warning event on every reconcile.
//
// Any other value is rejected at the CRD schema layer. Leaving Auth nil
// OR leaving Mode empty keeps the fail-closed posture: Ingress render
// is skipped and status surfaces the reason.
type DashboardAuthSpec struct {
	// Mode selects the auth guard.
	// +kubebuilder:validation:Enum=basic;none
	Mode string `json:"mode"`

	// BasicAuthSecretName names an existing basic-auth Secret (the same
	// htpasswd-style key "auth" the nginx ingress controller reads). When
	// empty and Mode="basic", the operator reconciles a scaffold Secret —
	// scaffold scope: the scaffold is documented in the reconciler as a
	// follow-up.
	// +optional
	BasicAuthSecretName string `json:"basicAuthSecretName,omitempty"`
}

// PodMonitorSpec configures an optional monitoring.coreos.com/v1 PodMonitor
// reconciled by the operator when the Prometheus Operator CRDs are present
// (#582). Mirrors the chart's `podMonitor.*` block. Unlike ServiceMonitor
// (which scrapes the harness Service), PodMonitor scrapes each backend
// container's /metrics endpoint directly — useful for per-backend
// telemetry (tokens, tool-use, context-window usage).
//
// Reconciliation is gated by the same CRD-presence probe as ServiceMonitor;
// no hard dependency on prometheus-operator Go types is introduced.
type PodMonitorSpec struct {
	// Enabled toggles creation of the PodMonitor. Requires
	// spec.metrics.enabled=true (no endpoint to scrape otherwise) and the
	// monitoring.coreos.com CRD to be installed.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Interval between scrapes (e.g. "30s"). Defaults to "30s".
	// +kubebuilder:default="30s"
	// +optional
	Interval string `json:"interval,omitempty"`

	// ScrapeTimeout per scrape (e.g. "10s"). Defaults to "10s".
	// +kubebuilder:default="10s"
	// +optional
	ScrapeTimeout string `json:"scrapeTimeout,omitempty"`

	// Labels merged into the PodMonitor's metadata.labels for tenancy-
	// selecting Prometheus deployments (e.g. `release: kube-prometheus-stack`).
	// +optional
	Labels map[string]string `json:"labels,omitempty"`
}

// ServiceMonitorSpec configures an optional monitoring.coreos.com/v1
// ServiceMonitor reconciled by the operator when the Prometheus Operator
// CRDs are present on the cluster (#476). Mirrors the chart's
// `serviceMonitor.*` block in charts/witwave/values.yaml so operator-rendered
// agents are scraped the same way chart-rendered agents are.
//
// The reconciler probes for the `monitoring.coreos.com/v1 ServiceMonitor`
// REST mapping at reconcile time and no-ops when the CRD is absent — no
// hard dependency on prometheus-operator Go types is introduced; the
// ServiceMonitor is created via an unstructured client.
type ServiceMonitorSpec struct {
	// Enabled toggles creation of the ServiceMonitor. Requires
	// spec.metrics.enabled=true (no endpoint to scrape otherwise) and the
	// monitoring.coreos.com CRD to be installed. When the CRD is absent
	// the reconciler logs once and no-ops; reconciliation resumes
	// automatically once the CRD appears.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Interval between scrapes (e.g. "30s"). Defaults to "30s".
	// +kubebuilder:default="30s"
	// +optional
	Interval string `json:"interval,omitempty"`

	// ScrapeTimeout per scrape (e.g. "10s"). Defaults to "10s".
	// +kubebuilder:default="10s"
	// +optional
	ScrapeTimeout string `json:"scrapeTimeout,omitempty"`

	// Labels merged into the ServiceMonitor's metadata.labels. Useful
	// when the cluster's Prometheus selects ServiceMonitors by a
	// tenancy label (e.g. `release: kube-prometheus-stack`).
	// +optional
	Labels map[string]string `json:"labels,omitempty"`
}

// PrometheusRuleSpec configures an optional monitoring.coreos.com/v1
// PrometheusRule reconciled per WitwaveAgent (#1746). Mirrors the chart's
// `prometheusRule.*` block — but ships only a single Enabled toggle to
// keep the CRD surface narrow. The chart's per-alert thresholds are
// rendered verbatim from the chart defaults so an operator-only install
// gets the same alert posture as a chart install. Operators who want a
// different alert posture should hand-author their own PrometheusRule and
// leave this toggle off.
//
// Gated on a monitoring.coreos.com/v1 PrometheusRule CRD-presence probe
// at reconcile time so clusters without prometheus-operator are
// unaffected.
type PrometheusRuleSpec struct {
	// Enabled toggles reconciliation of the PrometheusRule. Off by
	// default — opt in explicitly so an operator install does not
	// suddenly inherit a chart-shaped paging surface.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// AdditionalLabels are merged into the PrometheusRule's
	// metadata.labels. Set this to the label your Prometheus CR uses
	// to select PrometheusRules (e.g. `release: kube-prometheus-stack`).
	// +optional
	AdditionalLabels map[string]string `json:"additionalLabels,omitempty"`
}

// CorsSpec mirrors the chart's `cors.*` block (#763, #1748). The
// operator stamps CORS_ALLOW_ORIGINS on the harness container when
// AllowOrigins is non-empty. AllowWildcard is the chart's explicit
// acknowledgement knob for `*` origins; without it, a `*` entry is
// rejected at admission so an operator-only install does not silently
// reproduce the disclosure-hole the chart's #763 guard prevents.
type CorsSpec struct {
	// AllowOrigins is the comma-joined list rendered as
	// CORS_ALLOW_ORIGINS on harness. Each entry must be either a
	// concrete origin (`https://example.com`) or the literal `*`.
	// `*` is permitted only when AllowWildcard is true.
	// +optional
	AllowOrigins []string `json:"allowOrigins,omitempty"`

	// AllowWildcard is the explicit acknowledgement that wildcard
	// origins (`*`) are intentional. Mirrors chart's
	// CORS_ALLOW_WILDCARD env var (#701). Without this, a `*` entry
	// in AllowOrigins is refused.
	// +optional
	AllowWildcard bool `json:"allowWildcard,omitempty"`
}

// MetricsSpec toggles Prometheus scrape behaviour for the agent.
type MetricsSpec struct {
	// Master toggle. When false, no scrape annotations are emitted on
	// either the Service or the Pod template, regardless of the
	// granular flags below. Also stamps METRICS_ENABLED=false on the
	// pod's containers, which keeps the harness's /internal/events/*
	// routes on the app listener (#1781) and skips the /metrics
	// listener entirely.
	//
	// Defaults to true — every agent the operator provisions has metrics
	// on by default. Disable explicitly via spec.metrics.enabled=false
	// on the CR (or `ww agent create --no-metrics` from the CLI).
	//
	// Pointer type so the kubebuilder default fires at admission when
	// the field is omitted; mirrors the BackendSpec.Enabled / top-level
	// WitwaveAgentSpec.Enabled pattern. Read via metricsEnabled(agent)
	// in the controller — nil dereferences as true.
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// ServiceAnnotations stamps prometheus.io/{scrape,port,path}
	// annotations onto the agent Service for Prometheus configs using
	// `kubernetes_sd_configs.role: service`. Defaults to true (preserves
	// historical chart behaviour).
	// +kubebuilder:default=true
	// +optional
	ServiceAnnotations *bool `json:"serviceAnnotations,omitempty"`

	// PodAnnotations stamps the same annotations onto the Pod template
	// for Prometheus configs using `kubernetes_sd_configs.role: pod`
	// (per-replica metrics). Defaults to false — opt in when needed.
	// Mirrors charts/witwave beta.35 / #472.
	// +kubebuilder:default=false
	// +optional
	PodAnnotations *bool `json:"podAnnotations,omitempty"`
}

// WitwaveAgentPhase is a coarse-grained lifecycle phase for display purposes.
// +kubebuilder:validation:Enum=Pending;Ready;Degraded;Error
type WitwaveAgentPhase string

const (
	WitwaveAgentPhasePending  WitwaveAgentPhase = "Pending"
	WitwaveAgentPhaseReady    WitwaveAgentPhase = "Ready"
	WitwaveAgentPhaseDegraded WitwaveAgentPhase = "Degraded"
	WitwaveAgentPhaseError    WitwaveAgentPhase = "Error"
)

// WitwaveAgentStatus defines the observed state of WitwaveAgent.
type WitwaveAgentStatus struct {
	// Phase is a coarse-grained lifecycle indicator.
	// +optional
	Phase WitwaveAgentPhase `json:"phase,omitempty"`

	// ObservedGeneration is the generation of the spec most recently reconciled.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// ReadyReplicas mirrors the agent Deployment's status.readyReplicas.
	// +optional
	ReadyReplicas int32 `json:"readyReplicas,omitempty"`

	// Conditions follow the standard Kubernetes condition convention.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// ReconcileHistory is a capped ring of the last ReconcileHistoryMax
	// reconcile outcomes (#1112). Newest entry last. Surfaces past phase
	// flaps, reconcile durations, and error reasons to `kubectl describe`
	// without needing Prometheus or a workqueue log tail. Writes happen
	// inside updateStatus on the same Status().Patch that records the
	// phase/conditions, so no extra round-trip to the apiserver.
	// +optional
	// +listType=atomic
	// +kubebuilder:validation:MaxItems=10
	ReconcileHistory []ReconcileHistoryEntry `json:"reconcileHistory,omitempty"`
}

// ReconcileHistoryMax caps the ring of ReconcileHistory entries kept on
// WitwaveAgentStatus. Ten entries is small enough to fit inside a status
// subresource PATCH comfortably (well under the 1 MiB etcd object limit
// even with maximum-length Messages) while still giving operators enough
// scrollback to see a recent flap plus the reconcile that recovered from
// it.
const ReconcileHistoryMax = 10

// ReconcileHistoryMessageMax caps the Message length on each
// ReconcileHistoryEntry. Longer messages are truncated with a trailing
// ellipsis in updateStatus so a pathological multi-paragraph error from a
// downstream apply never inflates status.
const ReconcileHistoryMessageMax = 128

// ReconcileHistoryPhase is the outcome bucket stamped onto a single
// ReconcileHistoryEntry. Distinct from WitwaveAgentPhase (which tracks the
// agent's Deployment readiness): a reconcile can complete Successfully
// while the agent remains Degraded because pods are still rolling.
// +kubebuilder:validation:Enum=Success;Error;Partial
type ReconcileHistoryPhase string

const (
	// ReconcileHistoryPhaseSuccess — reconcile completed with no errors.
	ReconcileHistoryPhaseSuccess ReconcileHistoryPhase = "Success"
	// ReconcileHistoryPhaseError — reconcile returned a fatal error.
	ReconcileHistoryPhaseError ReconcileHistoryPhase = "Error"
	// ReconcileHistoryPhasePartial — reconcile completed but at least one
	// optional sub-resource apply failed (errors.Join aggregated more than
	// one err; reserved for future use). Kept in the enum so the CRD
	// schema is forward-compatible.
	ReconcileHistoryPhasePartial ReconcileHistoryPhase = "Partial"
)

// ReconcileHistoryEntry is a single reconcile outcome persisted on
// WitwaveAgentStatus.ReconcileHistory (#1112). Fields are deliberately
// primitive so the entries serialise cleanly into the status subresource
// and render usefully under `kubectl describe`.
type ReconcileHistoryEntry struct {
	// Time is the apiserver-visible wall-clock at which reconcile
	// finished. Indexed by the ring's natural order; included as a field
	// so `kubectl describe` surfaces it without forcing readers to infer
	// ordering from array position.
	Time metav1.Time `json:"time"`

	// Duration is the reconcile wall-clock duration, serialised as the
	// Go time.Duration string (e.g. "123ms", "2.4s"). Kept as a string
	// rather than int64 nanoseconds so the field is self-describing in
	// `kubectl get -o yaml` without dashboard tooling.
	// +kubebuilder:validation:MinLength=1
	Duration string `json:"duration"`

	// Phase classifies the outcome. See ReconcileHistoryPhase.
	Phase ReconcileHistoryPhase `json:"phase"`

	// Reason is a short machine-readable code — same convention as
	// metav1.Condition.Reason (CamelCase, no punctuation). Typical values:
	// "Reconciled", "ReconcileFailed".
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=64
	Reason string `json:"reason"`

	// Message is a human-readable summary, truncated to
	// ReconcileHistoryMessageMax bytes with a trailing "…" when the full
	// error overflows. Empty on Success outcomes where the Reason alone
	// is enough.
	// +kubebuilder:validation:MaxLength=128
	// +optional
	Message string `json:"message,omitempty"`
}

// Standard condition types for WitwaveAgent.
const (
	ConditionAvailable        = "Available"
	ConditionProgressing      = "Progressing"
	ConditionReconcileSuccess = "ReconcileSuccess"
)

// MCPToolsSpec is the top-level block mirroring the chart's `mcpTools`
// values (#830). Each named tool is rendered as its own Deployment +
// Service in the agent's namespace when Enabled is true. Only a subset
// of the chart's full value surface is modelled here; the remaining
// knobs (resources, nodeSelector, rbac, clusterWide, automountServiceAccountToken,
// image.digest, image.pullPolicy) are follow-up scaffolding — the CRD
// schema was designed so those fields can be added additively without
// a breaking version bump.
type MCPToolsSpec struct {
	// Kubernetes renders the `mcp-kubernetes` tool (Kubernetes API access
	// via the official Python client). Off by default.
	// +optional
	Kubernetes *MCPToolSpec `json:"kubernetes,omitempty"`

	// Helm renders the `mcp-helm` tool (Helm release management via the
	// `helm` CLI). Off by default.
	// +optional
	Helm *MCPToolSpec `json:"helm,omitempty"`

	// Prometheus renders the `mcp-prometheus` tool (PromQL query surface).
	// Off by default. (#1354 stopgap — a future v1 should reshape to
	// map[string]*MCPToolSpec so new tools are additive without CRD bumps.)
	// +optional
	Prometheus *MCPToolSpec `json:"prometheus,omitempty"`
}

// MCPToolSpec is the per-tool knob set. Scaffold scope (#830): Enabled,
// Image, and Replicas are honoured by the operator's renderer; richer
// fields (RBAC, per-tool SA, resources, scheduling) are intentionally
// deferred so the CRD schema can settle before the controller grows a
// full multi-tool renderer.
type MCPToolSpec struct {
	// Enabled toggles rendering of this tool's Deployment + Service.
	// +optional
	Enabled bool `json:"enabled,omitempty"`

	// Image is the tool container image. Repository defaults to the
	// canonical ghcr.io path when the field is omitted on a tool whose
	// Enabled=true (the controller supplies the default).
	// +optional
	Image *ImageSpec `json:"image,omitempty"`

	// Replicas controls the rendered Deployment's replica count. The
	// controller clamps negative values to 1. Defaults to 1 when unset.
	// +kubebuilder:validation:Minimum=0
	// +optional
	Replicas *int32 `json:"replicas,omitempty"`

	// AuthTokenSecretRef references the Secret holding MCP_TOOL_AUTH_TOKEN
	// for this tool. When set, the operator projects the token into the
	// container env so backends can reach the tool with Authorization:
	// Bearer auth. When unset AND AuthDisabled is false, the tool's
	// shared/mcp_auth middleware fails closed on every request (#1331).
	// +optional
	AuthTokenSecretRef *corev1.SecretKeySelector `json:"authTokenSecretRef,omitempty"`

	// #1355 SECURITY NOTE: The cluster-scoped ServiceAccount pattern
	// (when WitwaveAgent uses an SA with apiserver permissions for MCP
	// tool access) mounts the SA token into EVERY pod container —
	// including the backend containers (claude/openai/gemini). A
	// prompt-injection compromise of a backend can read
	// /var/run/secrets/kubernetes.io/serviceaccount/token and make
	// authenticated apiserver calls directly, bypassing every MCP
	// tool allow-list. PREFERRED recipe: deploy MCP tools as their
	// own separately-scoped pods (chart default) and have backends
	// reach them via the cluster-shared Service. Document this in
	// operator/README.md §security-posture.
	// AuthDisabled acknowledges that MCP_TOOL_AUTH_TOKEN is intentionally
	// not set for this tool. Useful for local dev. The mcp_auth middleware
	// logs a loud WARN at startup when this is true. (#1331)
	// +optional
	AuthDisabled bool `json:"authDisabled,omitempty"`

	// Resources sets the container's resource requests/limits. When unset
	// the operator emits BestEffort QoS; noisy-neighbour evictions hit
	// MCP tools first. Chart-rendered MCP tools already expose this via
	// mcpTools.<name>.resources. (#1353)
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`

	// ReadinessProbe overrides the default HTTP GET on /health with no
	// initialDelaySeconds. Large-cluster kube discovery can take >1s at
	// cold start; the default probe times out and CrashLoops the pod
	// before discovery completes. Chart-rendered parity ships tunable
	// probes; operator path now does too. (#1353)
	// +optional
	ReadinessProbe *corev1.Probe `json:"readinessProbe,omitempty"`

	// LivenessProbe overrides the default. Absent by default. (#1353)
	// +optional
	LivenessProbe *corev1.Probe `json:"livenessProbe,omitempty"`

	// ServiceAccountName names a pre-provisioned ServiceAccount to mount
	// into the tool pod. When unset the operator falls back to the
	// namespace's default ServiceAccount (matches Kubernetes default
	// behaviour). Mirrors the chart's mcpTools.<name>.serviceAccountName
	// (#762, #1074, #1737). Use this when the tool needs cluster
	// permissions (e.g. mcp-kubernetes / mcp-helm) — pre-create the SA
	// + Role/ClusterRole + (Role|ClusterRole)Binding out of band, then
	// reference it here.
	// +optional
	ServiceAccountName string `json:"serviceAccountName,omitempty"`

	// AutomountServiceAccountToken is the three-state pod-level flag
	// (mirror chart #856). When nil the operator stamps `true` so the
	// SA token mounts (Kubernetes default). Set false to coexist with
	// IRSA / workload-identity setups that attach the SA for IAM role
	// metadata while wanting the in-pod token mount suppressed.
	// +optional
	AutomountServiceAccountToken *bool `json:"automountServiceAccountToken,omitempty"`

	// ImagePullSecrets attaches one or more Secret references for
	// pulling private registry images. Mirrors the chart's per-tool
	// imagePullSecrets (#1737).
	// +optional
	ImagePullSecrets []corev1.LocalObjectReference `json:"imagePullSecrets,omitempty"`

	// PodSecurityContext sets the pod-level securityContext. When unset
	// the operator stamps a hardened default (RunAsNonRoot=true,
	// RunAsUser/Group=1000, FSGroup=1000, SeccompProfile=RuntimeDefault)
	// matching the chart and PSS-restricted policy (#1737).
	// +optional
	PodSecurityContext *corev1.PodSecurityContext `json:"podSecurityContext,omitempty"`

	// SecurityContext sets the container-level securityContext. When
	// unset the operator stamps a hardened default
	// (AllowPrivilegeEscalation=false, Capabilities.Drop=[ALL],
	// RunAsNonRoot=true, ReadOnlyRootFilesystem=true,
	// SeccompProfile=RuntimeDefault) matching the chart's
	// `witwave.hardenedContainerSecurityContext` helper (#1737).
	// +optional
	SecurityContext *corev1.SecurityContext `json:"securityContext,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=wwa
// The helm.sh/resource-policy=keep annotation below MUST NOT be removed by
// automated regeneration (e.g. `make manifests`). It instructs Helm to retain
// this CRD on `helm uninstall`, preventing accidental deletion of every
// WitwaveAgent CR in the cluster. See #1647 (the bug that prompted adding it)
// and #1614 (the operator install/uninstall lifecycle work it complements).
// controller-gen preserves existing metadata.annotations on regeneration, and
// this marker re-injects the annotation if the CRD file is rebuilt from scratch.
// +kubebuilder:metadata:annotations="helm.sh/resource-policy=keep"
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Ready",type=integer,JSONPath=`.status.readyReplicas`
// +kubebuilder:printcolumn:name="Backends",type=string,JSONPath=`.spec.backends[*].name`,priority=1
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`
// +kubebuilder:printcolumn:name="LastReconcile",type=date,JSONPath=`.status.reconcileHistory[-1:].time`,priority=1
// +kubebuilder:printcolumn:name="LastOutcome",type=string,JSONPath=`.status.reconcileHistory[-1:].phase`,priority=1

// WitwaveAgent is the Schema for the witwaveagents API.
type WitwaveAgent struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   WitwaveAgentSpec   `json:"spec,omitempty"`
	Status WitwaveAgentStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// WitwaveAgentList contains a list of WitwaveAgent.
type WitwaveAgentList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []WitwaveAgent `json:"items"`
}

func init() {
	SchemeBuilder.Register(&WitwaveAgent{}, &WitwaveAgentList{})
}
