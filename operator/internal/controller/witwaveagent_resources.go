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

package controller

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	autoscalingv2 "k8s.io/api/autoscaling/v2"
	corev1 "k8s.io/api/core/v1"
	policyv1 "k8s.io/api/policy/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/intstr"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

// Label keys used for every resource the operator creates.
const (
	labelName      = "app.kubernetes.io/name"
	labelComponent = "app.kubernetes.io/component"
	labelPartOf    = "app.kubernetes.io/part-of"
	labelManagedBy = "app.kubernetes.io/managed-by"

	// componentAgent matches the chart's witwave.agentLabels component value
	// ("harness") so Prometheus rules, ServiceMonitors, and Grafana
	// panels that select on `app.kubernetes.io/component=harness`
	// match operator-rendered agents the same way they match Helm-rendered
	// agents (#575). managedBy stays "witwave-operator" (vs the chart's "helm")
	// on purpose — it's the one label that is semantically different
	// between the two install paths and consumers should be able to tell
	// the rendering path apart.
	componentAgent   = "harness"
	componentBackend = "backend"
	partOf           = "witwave"
	managedBy        = "witwave-operator"

	// sharedStorageVolume is the pod-level volume name used when the agent
	// mounts a shared volume (PVC or hostPath). All containers mount it.
	sharedStorageVolume = "shared-storage"

	// runtimeStorageVolume is the pod-level volume name used when the agent
	// mounts durable harness runtime state.
	runtimeStorageVolume = "runtime-storage"

	// componentSharedStorage labels the operator-managed shared-storage PVC
	// (#481) so its cleanup path stays cleanly separated from the backend
	// PVC cleanup path — both sweep by labelName+labelManagedBy and would
	// otherwise reciprocally delete each other.
	componentSharedStorage = "shared-storage"

	// componentRuntimeStorage labels the operator-managed runtime-storage PVC
	// so backend/shared PVC cleanup paths do not sweep it.
	componentRuntimeStorage = "runtime-storage"

	// defaultSharedStorageMountPath mirrors charts/witwave/values.yaml's
	// `sharedStorage.mountPath` default so operator-rendered pods mount
	// the shared volume at the same path Helm-rendered pods do.
	defaultSharedStorageMountPath = "/data/shared"

	// defaultSharedStorageSize mirrors the chart default PVC storage
	// request for `sharedStorage.size` when the operator creates the PVC
	// itself (#481).
	defaultSharedStorageSize = "1Gi"

	// Runtime storage defaults mirror charts/witwave/values.yaml. The state
	// mount holds durable machine state such as the A2A SqliteTaskStore; logs
	// remain separate append-only history.
	defaultRuntimeStorageSize = "1Gi"
	runtimeLogsSubPath        = "logs"
	runtimeLogsMountPath      = "/home/agent/logs"
	runtimeStateSubPath       = "state"
	runtimeStateMountPath     = "/home/agent/state"
	defaultTaskStorePath      = "/home/agent/state/a2a-tasks.db"

	// agentConfigVolumePrefix is the prefix for per-agent/backend inline
	// config ConfigMap volume names.
	agentConfigVolumePrefix = "agent-config-"

	// teamLabel opts a WitwaveAgent into a named team. Agents sharing the same
	// value are listed together in the team manifest ConfigMap so each
	// harness can address its peers by name (#474). When unset, the agent
	// is grouped with every other label-less WitwaveAgent in its namespace.
	teamLabel = "witwave.ai/team"

	// componentManifest labels the per-team manifest ConfigMap so it can
	// be identified independently of the per-agent owned ConfigMaps
	// (inline config, dashboard nginx template, etc.).
	componentManifest = "witwave-manifest"

	// manifestVolumeName / manifestMountPath mirror the chart's harness
	// volume wiring so operator-rendered pods behave identically to
	// Helm-rendered pods (#474). MANIFEST_PATH env override still wins
	// at runtime if an operator needs to remap it.
	manifestVolumeName = "manifest"
	manifestMountPath  = "/home/agent/manifest.json"
	manifestSubPath    = "manifest.json"
)

// agentLabels returns the canonical label set for resources owned by an agent.
func agentLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	return map[string]string{
		labelName:      agent.Name,
		labelComponent: componentAgent,
		labelPartOf:    partOf,
		labelManagedBy: managedBy,
	}
}

// selectorLabels returns the minimal label set used as a Deployment/Service
// selector. This intentionally omits managed-by/part-of so selectors stay
// forward-compatible when those values change.
func selectorLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	return map[string]string{
		labelName: agent.Name,
	}
}

// imageRef assembles a container image reference from an ImageSpec, falling
// back to the provided default tag when the spec omits one.
func imageRef(img witwavev1alpha1.ImageSpec, fallbackTag string) string {
	// #1352: digest pinning takes precedence over tag so production
	// deployments protect against registry re-tagging / supply-chain
	// compromise.
	if img.Digest != "" {
		return fmt.Sprintf("%s@%s", img.Repository, img.Digest)
	}
	tag := img.Tag
	if tag == "" {
		tag = fallbackTag
	}
	return fmt.Sprintf("%s:%s", img.Repository, tag)
}

// imagePullPolicy returns the effective container ImagePullPolicy for an
// ImageSpec, defaulting to IfNotPresent when the spec leaves it unset. This
// mirrors the Helm chart's `| default "IfNotPresent"` behaviour so the
// operator and the chart render identical pod specs for the same CR (#578).
// When the user explicitly sets PullPolicy (including "Always" for :latest
// during local dev), the spec value is honoured verbatim.
func imagePullPolicy(img witwavev1alpha1.ImageSpec) corev1.PullPolicy {
	if img.PullPolicy == "" {
		return corev1.PullIfNotPresent
	}
	return img.PullPolicy
}

// probeDefaults returns an effective ProbeSpec merging optional overrides with
// the Helm-chart defaults.
func probeDefaults(override *witwavev1alpha1.ProbeSpec, liveness bool) witwavev1alpha1.ProbeSpec {
	// Defaults match charts/witwave/values.yaml probes.*.
	var def witwavev1alpha1.ProbeSpec
	if liveness {
		def = witwavev1alpha1.ProbeSpec{
			InitialDelaySeconds: 10,
			PeriodSeconds:       30,
			TimeoutSeconds:      5,
			FailureThreshold:    3,
		}
	} else {
		def = witwavev1alpha1.ProbeSpec{
			InitialDelaySeconds: 5,
			PeriodSeconds:       10,
			TimeoutSeconds:      5,
			FailureThreshold:    3,
		}
	}
	if override == nil {
		return def
	}
	if override.InitialDelaySeconds > 0 {
		def.InitialDelaySeconds = override.InitialDelaySeconds
	}
	if override.PeriodSeconds > 0 {
		def.PeriodSeconds = override.PeriodSeconds
	}
	if override.TimeoutSeconds > 0 {
		def.TimeoutSeconds = override.TimeoutSeconds
	}
	if override.FailureThreshold > 0 {
		def.FailureThreshold = override.FailureThreshold
	}
	return def
}

// startupProbeDefaults returns the effective startupProbe ProbeSpec. Defaults
// match charts/witwave/values.yaml probes.startup.* (initialDelaySeconds=0,
// periodSeconds=10, timeoutSeconds=5, failureThreshold=30 → ~5min cold-start
// budget before liveness/readiness take over).
func startupProbeDefaults(override *witwavev1alpha1.ProbeSpec) witwavev1alpha1.ProbeSpec {
	def := witwavev1alpha1.ProbeSpec{
		InitialDelaySeconds: 0,
		PeriodSeconds:       10,
		TimeoutSeconds:      5,
		FailureThreshold:    30,
	}
	if override == nil {
		return def
	}
	if override.InitialDelaySeconds > 0 {
		def.InitialDelaySeconds = override.InitialDelaySeconds
	}
	if override.PeriodSeconds > 0 {
		def.PeriodSeconds = override.PeriodSeconds
	}
	if override.TimeoutSeconds > 0 {
		def.TimeoutSeconds = override.TimeoutSeconds
	}
	if override.FailureThreshold > 0 {
		def.FailureThreshold = override.FailureThreshold
	}
	return def
}

// backendUsesThreeProbes reports whether a backend implements the three-probe
// health model (/health/start, /health, /health/ready). All current backends
// except echo do — echo is the deliberately minimal hello-world default and
// only exposes /health (see backends/echo/README.md intentional-non-scope
// list).
//
// We key on the IMAGE repository basename, not BackendSpec.Name, because the
// CLI's `<name>:<type>` shape decouples the two: `--backend echo-1:echo`
// produces Name=echo-1 with the echo image. A name-based check breaks the
// moment a user picks any non-default backend name (which is mandatory
// for multi-backend agents — two echos can't both be named "echo"). The
// image repo basename ("ghcr.io/witwave-ai/images/echo" → "echo") tracks
// the actual implementation regardless of how the user named the
// container.
func backendUsesThreeProbes(b witwavev1alpha1.BackendSpec) bool {
	return imageBackendType(b.Image.Repository) != "echo"
}

// imageBackendType extracts the implementation type from a backend's image
// repository — the path's last segment after the final "/". Examples:
//
//	ghcr.io/witwave-ai/images/echo       → echo
//	ghcr.io/witwave-ai/images/claude     → claude
//	ghcr.io/witwave-ai/images/openai      → openai
//
// Empty string when the repository is empty (which the CRD's required
// validation prevents, but defense-in-depth).
func imageBackendType(repository string) string {
	if repository == "" {
		return ""
	}
	if i := strings.LastIndexByte(repository, '/'); i >= 0 {
		return repository[i+1:]
	}
	return repository
}

// backendProbePaths bundles the readiness probe path for a backend; liveness
// always points at /health regardless of three-probe support.
type backendProbePaths struct {
	ready string
}

// livenessReadinessPaths returns the readiness path for a backend. Three-probe
// backends use /health/ready so the kubelet can distinguish "process up" from
// "ready to serve" (#1608, #1672, #1686). Echo retains /health since it does
// not implement the readiness split.
func livenessReadinessPaths(b witwavev1alpha1.BackendSpec) backendProbePaths {
	if backendUsesThreeProbes(b) {
		return backendProbePaths{ready: "/health/ready"}
	}
	return backendProbePaths{ready: "/health"}
}

// maybeStartupProbe returns a startupProbe targeting /health/start for
// three-probe backends, and nil for echo.
func maybeStartupProbe(b witwavev1alpha1.BackendSpec, port int32, spec witwavev1alpha1.ProbeSpec) *corev1.Probe {
	if !backendUsesThreeProbes(b) {
		return nil
	}
	return httpProbe(port, "/health/start", spec)
}

// httpProbe builds an HTTP GET probe against the given port and path using
// the timing from spec.
func httpProbe(port int32, path string, spec witwavev1alpha1.ProbeSpec) *corev1.Probe {
	return &corev1.Probe{
		ProbeHandler: corev1.ProbeHandler{
			HTTPGet: &corev1.HTTPGetAction{
				Path: path,
				Port: intstr.FromInt(int(port)),
			},
		},
		InitialDelaySeconds: spec.InitialDelaySeconds,
		PeriodSeconds:       spec.PeriodSeconds,
		TimeoutSeconds:      spec.TimeoutSeconds,
		FailureThreshold:    spec.FailureThreshold,
	}
}

// agentConfigMapName returns the ConfigMap name for an agent's or backend's
// inline config files. The backend name may be empty for the agent itself.
func agentConfigMapName(agent *witwavev1alpha1.WitwaveAgent, backendName string) string {
	if backendName == "" {
		return fmt.Sprintf("%s-config", agent.Name)
	}
	return fmt.Sprintf("%s-%s-config", agent.Name, backendName)
}

// buildConfigMap renders one ConfigMap containing the given inline files, or
// nil if files is empty.
func buildConfigMap(agent *witwavev1alpha1.WitwaveAgent, name string, files []witwavev1alpha1.ConfigFile) *corev1.ConfigMap {
	if len(files) == 0 {
		return nil
	}
	data := make(map[string]string, len(files))
	for _, f := range files {
		data[f.Name] = f.Content
	}
	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: agent.Namespace,
			Labels:    agentLabels(agent),
		},
		Data: data,
	}
}

// configVolumesAndMounts converts a list of ConfigFiles (backed by the named
// ConfigMap) into the pod Volume + container VolumeMount pairs needed to
// surface each file at its declared mount path.
//
// Each file becomes its own subPath mount so the existing container filesystem
// is not masked (the common K8s pattern for injecting individual files).
func configVolumesAndMounts(cmName string, volumeKey string, files []witwavev1alpha1.ConfigFile) ([]corev1.Volume, []corev1.VolumeMount) {
	if len(files) == 0 {
		return nil, nil
	}
	vol := corev1.Volume{
		Name: volumeKey,
		VolumeSource: corev1.VolumeSource{
			ConfigMap: &corev1.ConfigMapVolumeSource{
				LocalObjectReference: corev1.LocalObjectReference{Name: cmName},
			},
		},
	}
	mounts := make([]corev1.VolumeMount, 0, len(files))
	for _, f := range files {
		mounts = append(mounts, corev1.VolumeMount{
			Name:      volumeKey,
			MountPath: f.MountPath,
			SubPath:   f.Name,
			ReadOnly:  true,
		})
	}
	return []corev1.Volume{vol}, mounts
}

// ── Shared storage (#481, #611) ──────────────────────────────────────────────
//
// sharedStorageEnabled returns whether the agent has any form of shared
// volume configured. Backward-compat: an older CR that only sets
// `claimName` (the deprecated SharedStorageRef shape) is treated as
// enabled=true even when `enabled` is unset.
func sharedStorageEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	s := agent.Spec.SharedStorage
	if s == nil {
		return false
	}
	if s.Enabled {
		return true
	}
	// Back-compat path: a CR authored against SharedStorageRef set only
	// claimName. Treat that as enabled=true, storageType=pvc,
	// existingClaim=claimName so such CRs render identically to before.
	return s.ClaimName != "" && s.ExistingClaim == "" && s.StorageType == ""
}

// sharedStorageType returns the effective storage type for the shared
// volume, defaulting to pvc.
func sharedStorageType(agent *witwavev1alpha1.WitwaveAgent) witwavev1alpha1.SharedStorageType {
	s := agent.Spec.SharedStorage
	if s == nil || s.StorageType == "" {
		return witwavev1alpha1.SharedStorageTypePVC
	}
	return s.StorageType
}

// sharedStorageExistingClaim returns the claim name the pod should
// reference when StorageType=pvc. When the user supplied neither
// ExistingClaim nor the deprecated ClaimName, the operator falls back to
// the claim it reconciles itself: `<agent>-shared`.
func sharedStorageExistingClaim(agent *witwavev1alpha1.WitwaveAgent) string {
	s := agent.Spec.SharedStorage
	if s == nil {
		return ""
	}
	if s.ExistingClaim != "" {
		return s.ExistingClaim
	}
	if s.ClaimName != "" {
		return s.ClaimName
	}
	return sharedStoragePVCName(agent)
}

// sharedStorageMountPath returns the effective mount path, defaulting
// to the chart's default `/data/shared`.
func sharedStorageMountPath(agent *witwavev1alpha1.WitwaveAgent) string {
	s := agent.Spec.SharedStorage
	if s != nil && s.MountPath != "" {
		return s.MountPath
	}
	return defaultSharedStorageMountPath
}

// sharedStorageOperatorManaged reports whether the operator is the one
// reconciling the shared PVC (vs. referencing one supplied by the user).
// Matches the chart's `.enabled && storageType==pvc && !existingClaim`
// rule — when ExistingClaim and the deprecated ClaimName are both empty
// the operator creates the PVC (#481).
func sharedStorageOperatorManaged(agent *witwavev1alpha1.WitwaveAgent) bool {
	if !sharedStorageEnabled(agent) {
		return false
	}
	if sharedStorageType(agent) != witwavev1alpha1.SharedStorageTypePVC {
		return false
	}
	s := agent.Spec.SharedStorage
	return s != nil && s.ExistingClaim == "" && s.ClaimName == ""
}

// sharedStoragePVCName returns the conventional name for the
// operator-reconciled shared PVC (#481).
func sharedStoragePVCName(agent *witwavev1alpha1.WitwaveAgent) string {
	return fmt.Sprintf("%s-shared", agent.Name)
}

// sharedStorageLabels returns the label set stamped on the operator-managed
// shared PVC. The distinct `componentSharedStorage` value keeps backend
// PVC cleanup from sweeping the shared PVC (and vice versa) even though
// both share the same name+managed-by labels (#481).
func sharedStorageLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	l := agentLabels(agent)
	l[labelComponent] = componentSharedStorage
	return l
}

// sharedStorageVolumeForPod returns the pod Volume for the shared
// storage mount, or nil when shared storage is disabled. When
// StorageType=hostPath and HostPath is empty the function also returns
// nil — the pod cannot legally reference an unset hostPath, so the
// container mount is skipped to keep the pod schedulable; the user-
// facing validation lives at the CRD layer plus a future webhook check.
func sharedStorageVolumeForPod(agent *witwavev1alpha1.WitwaveAgent) *corev1.Volume {
	if !sharedStorageEnabled(agent) {
		return nil
	}
	switch sharedStorageType(agent) {
	case witwavev1alpha1.SharedStorageTypeHostPath:
		s := agent.Spec.SharedStorage
		if s == nil || s.HostPath == "" {
			// #1221: log a WARNING when hostPath mode is requested but
			// HostPath is empty. Recorder is not plumbed through at this
			// call site (renderer, not reconciler) — emit a loud log so
			// operators notice. CRD validation + webhook should catch
			// this upstream; this is the renderer's last line of defence.
			logf.Log.WithName("witwaveagent-shared-storage").Info(
				"SharedStorageMisconfig: hostPath mode requested but HostPath is empty — skipping volume",
				"agent", agent.Name,
				"namespace", agent.Namespace,
			)
			return nil
		}
		hpType := corev1.HostPathDirectoryOrCreate
		if s.HostPathType != nil {
			hpType = *s.HostPathType
		}
		return &corev1.Volume{
			Name: sharedStorageVolume,
			VolumeSource: corev1.VolumeSource{
				HostPath: &corev1.HostPathVolumeSource{
					Path: s.HostPath,
					Type: &hpType,
				},
			},
		}
	default:
		// PVC mode.
		claim := sharedStorageExistingClaim(agent)
		if claim == "" {
			return nil
		}
		return &corev1.Volume{
			Name: sharedStorageVolume,
			VolumeSource: corev1.VolumeSource{
				PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
					ClaimName: claim,
				},
			},
		}
	}
}

// buildSharedStoragePVC returns the PVC the operator should create for
// shared storage, or nil when no PVC is required (user supplied an
// existing claim, storage is disabled, or StorageType=hostPath). Mirrors
// the chart's pvc.yaml branch gated on
// `.enabled && storageType==pvc && !existingClaim` (#481). When the
// caller cannot parse Size the returned error is surfaced as an event
// rather than failing the whole reconcile.
func buildSharedStoragePVC(agent *witwavev1alpha1.WitwaveAgent) (*corev1.PersistentVolumeClaim, error) {
	if !sharedStorageOperatorManaged(agent) {
		return nil, nil
	}
	s := agent.Spec.SharedStorage
	size := s.Size
	if size == "" {
		size = defaultSharedStorageSize
	}
	qty, err := resource.ParseQuantity(size)
	if err != nil {
		return nil, fmt.Errorf("sharedStorage.size %q: %w", size, err)
	}
	accessModes := s.AccessModes
	if len(accessModes) == 0 {
		accessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany}
	}
	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      sharedStoragePVCName(agent),
			Namespace: agent.Namespace,
			Labels:    sharedStorageLabels(agent),
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: accessModes,
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{corev1.ResourceStorage: qty},
			},
		},
	}
	if s.StorageClassName != "" {
		pvc.Spec.StorageClassName = &s.StorageClassName
	}
	return pvc, nil
}

// ── Runtime storage ─────────────────────────────────────────────────────────
//
// runtimeStorage is agent-level durable state for the harness container. It is
// separate from sharedStorage (mounted into every container for team/project
// files) and backend storage (per-backend native sessions/memory/logs).

func runtimeStorageEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	s := agent.Spec.RuntimeStorage
	return s != nil && s.Enabled
}

func runtimeStorageExistingClaim(agent *witwavev1alpha1.WitwaveAgent) string {
	s := agent.Spec.RuntimeStorage
	if s == nil {
		return ""
	}
	if s.ExistingClaim != "" {
		return s.ExistingClaim
	}
	return runtimeStoragePVCName(agent)
}

func runtimeStorageOperatorManaged(agent *witwavev1alpha1.WitwaveAgent) bool {
	s := agent.Spec.RuntimeStorage
	return runtimeStorageEnabled(agent) && s != nil && s.ExistingClaim == ""
}

func runtimeStoragePVCName(agent *witwavev1alpha1.WitwaveAgent) string {
	return fmt.Sprintf("%s-runtime-data", agent.Name)
}

func runtimeStorageLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	l := agentLabels(agent)
	l[labelComponent] = componentRuntimeStorage
	return l
}

func runtimeStorageMounts(agent *witwavev1alpha1.WitwaveAgent) []corev1.VolumeMount {
	if !runtimeStorageEnabled(agent) {
		return nil
	}
	s := agent.Spec.RuntimeStorage
	if s == nil || len(s.Mounts) == 0 {
		return []corev1.VolumeMount{
			{Name: runtimeStorageVolume, MountPath: runtimeLogsMountPath, SubPath: runtimeLogsSubPath},
			{Name: runtimeStorageVolume, MountPath: runtimeStateMountPath, SubPath: runtimeStateSubPath},
		}
	}
	mounts := make([]corev1.VolumeMount, 0, len(s.Mounts))
	for _, m := range s.Mounts {
		mounts = append(mounts, corev1.VolumeMount{
			Name:      runtimeStorageVolume,
			MountPath: m.MountPath,
			SubPath:   m.SubPath,
		})
	}
	return mounts
}

func runtimeStorageMountsPath(agent *witwavev1alpha1.WitwaveAgent, mountPath string) bool {
	for _, m := range runtimeStorageMounts(agent) {
		if m.MountPath == mountPath {
			return true
		}
	}
	return false
}

func runtimeStorageVolumeForPod(agent *witwavev1alpha1.WitwaveAgent) *corev1.Volume {
	if !runtimeStorageEnabled(agent) {
		return nil
	}
	claim := runtimeStorageExistingClaim(agent)
	if claim == "" {
		return nil
	}
	return &corev1.Volume{
		Name: runtimeStorageVolume,
		VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
				ClaimName: claim,
			},
		},
	}
}

func buildRuntimeStoragePVC(agent *witwavev1alpha1.WitwaveAgent) (*corev1.PersistentVolumeClaim, error) {
	if !runtimeStorageOperatorManaged(agent) {
		return nil, nil
	}
	s := agent.Spec.RuntimeStorage
	size := s.Size
	if size == "" {
		size = defaultRuntimeStorageSize
	}
	qty, err := resource.ParseQuantity(size)
	if err != nil {
		return nil, fmt.Errorf("runtimeStorage.size %q: %w", size, err)
	}
	accessModes := s.AccessModes
	if len(accessModes) == 0 {
		accessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}
	}
	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      runtimeStoragePVCName(agent),
			Namespace: agent.Namespace,
			Labels:    runtimeStorageLabels(agent),
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: accessModes,
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{corev1.ResourceStorage: qty},
			},
		},
	}
	if s.StorageClassName != "" {
		pvc.Spec.StorageClassName = &s.StorageClassName
	}
	return pvc, nil
}

// backendStorageVolumeAndMounts returns the pod Volume and container
// VolumeMounts for a backend's persistent storage, or (nil, nil) when storage
// is disabled.
func backendStorageVolumeAndMounts(agent *witwavev1alpha1.WitwaveAgent, b witwavev1alpha1.BackendSpec) (*corev1.Volume, []corev1.VolumeMount) {
	if b.Storage == nil || !b.Storage.Enabled {
		return nil, nil
	}
	claimName := b.Storage.ExistingClaim
	if claimName == "" {
		claimName = fmt.Sprintf("%s-%s-data", agent.Name, b.Name)
	}
	volName := fmt.Sprintf("%s-data", b.Name)
	vol := &corev1.Volume{
		Name: volName,
		VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
				ClaimName: claimName,
			},
		},
	}
	mounts := make([]corev1.VolumeMount, 0, len(b.Storage.Mounts))
	if len(b.Storage.Mounts) == 0 {
		// Default to mounting the root of the PVC at /data when no sub-mounts are listed.
		mounts = append(mounts, corev1.VolumeMount{Name: volName, MountPath: "/data"})
	} else {
		for _, m := range b.Storage.Mounts {
			mounts = append(mounts, corev1.VolumeMount{
				Name:      volName,
				MountPath: m.MountPath,
				SubPath:   m.SubPath,
			})
		}
	}
	return vol, mounts
}

// buildDeployment assembles the agent Deployment: one harness container
// plus one container per backend. AppVersion is the chart/operator app version
// used as a default image tag when an ImageSpec omits Tag. Prompts lists the
// WitwavePrompt CRs bound to this agent; one pod-level Volume + harness-level
// subPath VolumeMount is rendered per prompt so the harness scheduler sees
// the WitwavePrompt-sourced .md files alongside anything gitSync dropped into
// the same `.witwave/<kind>/` directory.
func buildDeployment(agent *witwavev1alpha1.WitwaveAgent, appVersion string, prompts []witwavev1alpha1.WitwavePrompt) *appsv1.Deployment {
	labels := agentLabels(agent)
	selector := selectorLabels(agent)

	// Build volumes and container mount references for agent + each backend.
	var volumes []corev1.Volume
	var harnessMounts []corev1.VolumeMount

	// Agent inline config.
	if len(agent.Spec.Config) > 0 {
		vols, mounts := configVolumesAndMounts(
			agentConfigMapName(agent, ""),
			agentConfigVolumePrefix+"harness",
			agent.Spec.Config,
		)
		volumes = append(volumes, vols...)
		harnessMounts = append(harnessMounts, mounts...)
	}

	// Shared storage (#481, #611). Supports two VolumeSource shapes:
	//   pvc:      reference existingClaim, or the operator-reconciled
	//             `<agent>-shared` PVC when none is supplied.
	//   hostPath: bind-mount a node-local directory (single-node only).
	// Backward-compat: older CRs that set only `claimName` are treated as
	// {enabled: true, storageType: pvc, existingClaim: claimName}.
	if vol := sharedStorageVolumeForPod(agent); vol != nil {
		volumes = append(volumes, *vol)
		harnessMounts = append(harnessMounts, corev1.VolumeMount{
			Name:      sharedStorageVolume,
			MountPath: sharedStorageMountPath(agent),
		})
	}

	if vol := runtimeStorageVolumeForPod(agent); vol != nil {
		volumes = append(volumes, *vol)
		harnessMounts = append(harnessMounts, runtimeStorageMounts(agent)...)
	}

	// Team manifest — mounted at the same path the chart uses
	// (/home/agent/manifest.json) so the harness's /team and /proxy/{name}
	// endpoints work identically across rendering paths (#474). The CM
	// itself is reconciled separately; if it doesn't exist yet, the pod
	// will stay in ContainerCreating until the first reconcile writes it.
	{
		cmName := manifestConfigMapName(agent)
		vol, mount := manifestVolumeAndMount(cmName)
		volumes = append(volumes, vol)
		harnessMounts = append(harnessMounts, mount)
	}

	// Git-sync plumbing (#475). Mirrors the chart's shared /git emptyDir
	// + per-mapping emptyDirs + script/mapping ConfigMap volumes. Adding
	// these to the pod spec is always safe — when GitSyncs is empty the
	// helper returns nil, keeping pod specs identical to the no-git-sync
	// case. The init/sidecar containers are appended below once the
	// harness container is built so order stays deterministic.
	volumes = append(volumes, gitSyncVolumes(agent)...)
	harnessMounts = append(harnessMounts, agentGitMappingMounts(agent)...)

	// WitwavePrompt-owned prompt files. One pod-level Volume per CR (so a
	// removed CR drops its Volume cleanly) and one subPath file mount
	// per CR at the kind's directory. The harness is the only container
	// that needs these — backends never read `.witwave/`.
	pv, pm := witwavePromptVolumesAndMounts(agent, prompts)
	volumes = append(volumes, pv...)
	harnessMounts = append(harnessMounts, pm...)

	// harness container.
	harnessPort := agent.Spec.Port
	if harnessPort == 0 {
		harnessPort = 8000
	}
	livenessProbe := probeDefaults(nil, true)
	readinessProbe := probeDefaults(nil, false)
	startupProbe := startupProbeDefaults(nil)
	if agent.Spec.Probes != nil {
		livenessProbe = probeDefaults(agent.Spec.Probes.Liveness, true)
		readinessProbe = probeDefaults(agent.Spec.Probes.Readiness, false)
		startupProbe = startupProbeDefaults(agent.Spec.Probes.Startup)
	}

	// Per-container metrics port (#836 / chart #687): each container's
	// metrics listener is `appPort + 1000` so two containers in the same
	// pod don't collide on a single fixed port. agent.Spec.MetricsPort
	// remains a legacy override — when non-zero, every container falls
	// back to it. containerMetricsPort encapsulates the rule so harness
	// and backend rendering stay aligned.
	harnessMetricsPort := containerMetricsPort(agent.Spec.MetricsPort, harnessPort)
	metricsOn := metricsEnabled(agent)
	harnessPorts := []corev1.ContainerPort{{Name: "http", ContainerPort: harnessPort}}
	if metricsOn {
		// Unique container-port names per pod (#1249): "metrics" collided
		// between harness and every backend container. Keep harness on
		// "metrics-harness" and each backend on "metrics-<backend>" so the
		// Service / PodMonitor can target a specific container without
		// the kubelet rejecting the Pod on duplicate port-name validation.
		harnessPorts = append(harnessPorts, corev1.ContainerPort{
			Name:          "metrics-harness",
			ContainerPort: harnessMetricsPort,
		})
	}

	harnessEnv := append(append([]corev1.EnvVar{
		{Name: "AGENT_NAME", Value: agent.Name},
		{Name: "HARNESS_PORT", Value: fmt.Sprintf("%d", harnessPort)},
		{Name: "METRICS_ENABLED", Value: metricsEnabledValue(agent)},
		{Name: "METRICS_PORT", Value: fmt.Sprintf("%d", harnessMetricsPort)},
	}, otelEnv(agent, fmt.Sprintf("harness-%s", agent.Name))...),
		corsEnv(agent)...)
	if runtimeStorageMountsPath(agent, runtimeStateMountPath) && !envListHasName(agent.Spec.Env, "TASK_STORE_PATH") {
		harnessEnv = append(harnessEnv, corev1.EnvVar{Name: "TASK_STORE_PATH", Value: defaultTaskStorePath})
	}
	harnessEnv = append(harnessEnv, agent.Spec.Env...)

	harness := corev1.Container{
		Name:            "harness",
		Image:           imageRef(agent.Spec.Image, appVersion),
		ImagePullPolicy: imagePullPolicy(agent.Spec.Image),
		Ports:           harnessPorts,
		// #829: chart parity for observability.tracing.* — stamp OTEL_*
		// env vars derived from spec.tracing onto every pod container so
		// operator-managed agents don't require hand-crafted spec.env per
		// container. serviceName mirrors the chart helper
		// ("harness-<agent>" / "<agent>-<backend>").
		// #1748: CORS env mirrors the chart's cors.* block — stamped
		// before agent.Spec.Env so explicit per-container overrides
		// still win on key collision (chart parity, late-merge wins).
		Env:       harnessEnv,
		EnvFrom:   harnessEnvFromWithInternal(agent),
		Resources: agent.Spec.Resources,
		SecurityContext: &corev1.SecurityContext{
			AllowPrivilegeEscalation: boolPtr(false),
			Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
		},
		LivenessProbe:  httpProbe(harnessPort, "/health/live", livenessProbe),
		ReadinessProbe: httpProbe(harnessPort, "/health/ready", readinessProbe),
		VolumeMounts:   harnessMounts,
		Lifecycle:      preStopLifecycle(agent),
	}

	// Backend containers.
	containers := []corev1.Container{harness}
	// Sort backends by name for a deterministic rendering. Skip per-backend
	// disabled entries (#chart beta.32 / #466 mirror) so the container,
	// per-backend volume mounts, and any inline configs aren't materialised.
	backends := append([]witwavev1alpha1.BackendSpec(nil), agent.Spec.Backends...)
	sort.Slice(backends, func(i, j int) bool { return backends[i].Name < backends[j].Name })
	for _, b := range backends {
		if !backendEnabled(b) {
			continue
		}
		bPort := b.Port
		if bPort == 0 {
			bPort = 8000
		}
		var bMounts []corev1.VolumeMount

		// Backend inline config.
		if len(b.Config) > 0 {
			vols, mounts := configVolumesAndMounts(
				agentConfigMapName(agent, b.Name),
				agentConfigVolumePrefix+b.Name,
				b.Config,
			)
			volumes = append(volumes, vols...)
			bMounts = append(bMounts, mounts...)
		}

		// Backend storage PVC.
		if vol, mounts := backendStorageVolumeAndMounts(agent, b); vol != nil {
			volumes = append(volumes, *vol)
			bMounts = append(bMounts, mounts...)
		}

		// Shared storage (same volume as harness). The pod-level
		// Volume is appended once up-top by sharedStorageVolumeForPod;
		// here we just mount it into each backend container so the
		// volume graph stays consistent whether the source is a PVC or
		// a hostPath directory (#481, #611).
		if sharedStorageEnabled(agent) && sharedStorageVolumeForPod(agent) != nil {
			bMounts = append(bMounts, corev1.VolumeMount{
				Name:      sharedStorageVolume,
				MountPath: sharedStorageMountPath(agent),
			})
		}

		// Git-sync mounts for this backend (#475): the shared /git
		// volume plus one emptyDir per backend-scoped GitMapping at
		// its declared dest. When the agent has no GitSyncs the
		// helper returns nil.
		bMounts = append(bMounts, backendGitMappingMounts(agent, b)...)

		// Per-container metrics port (#836): backend listener is
		// `bPort + 1000` unless MetricsPort is explicitly set on the CR.
		bMetricsPort := containerMetricsPort(agent.Spec.MetricsPort, bPort)
		bPorts := []corev1.ContainerPort{{Name: "http", ContainerPort: bPort}}
		if metricsOn {
			// Dedicated Prometheus metrics listener (#643). Each backend
			// uses a container-port name of "metrics-<backend>" so the
			// pod's Port list is unique across containers (#1249). The
			// PodMonitor scrapes every container via targetPort-by-number
			// or per-name endpoints; the Service only routes harness.
			bPorts = append(bPorts, corev1.ContainerPort{
				Name:          fmt.Sprintf("metrics-%s", b.Name),
				ContainerPort: bMetricsPort,
			})
		}

		backendEnv := append([]corev1.EnvVar{
			{Name: "AGENT_NAME", Value: fmt.Sprintf("%s-%s", agent.Name, b.Name)},
			{Name: "AGENT_OWNER", Value: agent.Name},
			{Name: "AGENT_ID", Value: b.Name},
			{Name: "AGENT_URL", Value: fmt.Sprintf("http://localhost:%d", bPort)},
			{Name: "BACKEND_PORT", Value: fmt.Sprintf("%d", bPort)},
			{Name: "METRICS_ENABLED", Value: metricsEnabledValue(agent)},
			{Name: "METRICS_PORT", Value: fmt.Sprintf("%d", bMetricsPort)},
			// Backend→harness transport for hook.decision events (#641).
			// Points at the harness running in the same pod; an empty
			// value disables the POST cleanly. HARNESS_EVENTS_AUTH_TOKEN
			// falls back to TRIGGERS_AUTH_TOKEN on the backend side, so
			// operators only need to thread one secret through both
			// harness and backend env.
			//
			// Port selection: when metrics are enabled the harness binds
			// /internal/events/{hook-decision,publish} to the dedicated
			// metrics listener (#924) so a NetworkPolicy can lock the
			// internal channel down to "scraper + same-pod only". When
			// metrics are disabled the routes fall back to the app
			// listener for availability. URL stamping has to track that
			// split or backends 404 silently against a port where no
			// route exists. (#1781)
			{Name: "HARNESS_EVENTS_URL", Value: harnessEventsURL(harnessPort, harnessMetricsPort, metricsOn)},
		}, otelEnv(agent, fmt.Sprintf("%s-%s", agent.Name, b.Name))...)
		if volumeMountsHavePath(bMounts, runtimeStateMountPath) && !envListHasName(b.Env, "TASK_STORE_PATH") {
			backendEnv = append(backendEnv, corev1.EnvVar{Name: "TASK_STORE_PATH", Value: defaultTaskStorePath})
		}
		backendEnv = append(backendEnv, b.Env...)

		bc := corev1.Container{
			Name:            b.Name,
			Image:           imageRef(b.Image, appVersion),
			ImagePullPolicy: imagePullPolicy(b.Image),
			Ports:           bPorts,
			// #829: OTEL_* env parity with chart helper — serviceName
			// "<agent>-<backend>" matches witwave.otelEnv.
			Env:       backendEnv,
			EnvFrom:   backendEnvFromWithInternal(agent, b),
			Resources: b.Resources,
			SecurityContext: &corev1.SecurityContext{
				AllowPrivilegeEscalation: boolPtr(false),
				Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
			},
			// Probe paths use the cycle-1 #1608 + #1672 health split, plus
			// the #1686 three-probe parity (#1719). Backends that implement
			// the three-probe model split startup/liveness/readiness across
			// /health/start, /health, /health/ready. Echo only exposes
			// /health (intentional-non-scope) — keep both probes there and
			// skip startupProbe for it.
			LivenessProbe:  httpProbe(bPort, "/health", livenessProbe),
			ReadinessProbe: httpProbe(bPort, livenessReadinessPaths(b).ready, readinessProbe),
			StartupProbe:   maybeStartupProbe(b, bPort, startupProbe),
			VolumeMounts:   bMounts,
			Lifecycle:      preStopLifecycle(agent),
		}
		if b.Model != "" {
			bc.Env = append(bc.Env, corev1.EnvVar{Name: "BACKEND_MODEL", Value: b.Model})
		}
		containers = append(containers, bc)
	}

	// Replicas: omitted when autoscaling owns the value.
	var replicas *int32
	if agent.Spec.Autoscaling == nil || !agent.Spec.Autoscaling.Enabled {
		replicas = int32Ptr(1)
	}

	// Resolve ServiceAccount + automount per risk #538. Default stays
	// hardened (namespace default SA, token not mounted). Rendering the
	// default SA name explicitly avoids SSA drift when an agent moves from
	// an explicit/managed ServiceAccount back to the no-token posture.
	// When the user opts in via kubernetesApiAccess, the operator-created
	// per-agent SA is used and automount flips to true so kubectl/client-go
	// can authenticate in-cluster. The legacy ServiceAccountName path keeps
	// its inferred true automount behaviour. An explicit
	// AutomountServiceAccountToken on the spec always wins.
	serviceAccountName := "default" //nolint:goconst // Kubernetes' namespace-local default ServiceAccount.
	switch {
	case kubernetesApiAccessEnabled(agent):
		serviceAccountName = kubernetesApiAccessName(agent)
	case agent.Spec.ServiceAccountName != "":
		serviceAccountName = agent.Spec.ServiceAccountName
	}
	var automount *bool
	switch {
	case agent.Spec.AutomountServiceAccountToken != nil:
		automount = agent.Spec.AutomountServiceAccountToken
	case kubernetesApiAccessEnabled(agent):
		automount = boolPtr(true)
	case agent.Spec.ServiceAccountName != "":
		automount = boolPtr(true)
	default:
		automount = boolPtr(false)
	}

	// Git-sync sidecars run alongside the harness + backends so the pod
	// stays in sync with the remote repo for its full lifetime (#475).
	// Prepend them so they appear before the backends in pod spec — the
	// harness container stays first, matching the chart.
	if sidecars := gitSyncSidecarContainers(agent, appVersion); len(sidecars) > 0 {
		withSidecars := make([]corev1.Container, 0, len(containers)+len(sidecars))
		// harness at index 0 is preserved; sidecars appear between
		// harness and backends to match the chart's container order.
		withSidecars = append(withSidecars, containers[0])
		withSidecars = append(withSidecars, sidecars...)
		if len(containers) > 1 {
			withSidecars = append(withSidecars, containers[1:]...)
		}
		containers = withSidecars
	}

	podSpec := corev1.PodSpec{
		TerminationGracePeriodSeconds: func() *int64 {
			if agent.Spec.TerminationGracePeriodSeconds != nil {
				return agent.Spec.TerminationGracePeriodSeconds
			}
			return int64Ptr(60)
		}(),
		ServiceAccountName:           serviceAccountName,
		AutomountServiceAccountToken: automount,
		SecurityContext: &corev1.PodSecurityContext{
			RunAsNonRoot: boolPtr(true),
			RunAsUser:    int64Ptr(1000),
			RunAsGroup:   int64Ptr(1000),
			FSGroup:      int64Ptr(1000),
			SeccompProfile: &corev1.SeccompProfile{
				Type: corev1.SeccompProfileTypeRuntimeDefault,
			},
		},
		ImagePullSecrets: agent.Spec.ImagePullSecrets,
		InitContainers:   gitSyncInitContainers(agent, appVersion),
		Containers:       containers,
		Volumes:          volumes,
		// Scheduling knobs (#605). All passthrough — empty/nil values are
		// no-ops at the PodSpec level, matching the chart's defaults.
		NodeSelector:              agent.Spec.NodeSelector,
		Tolerations:               agent.Spec.Tolerations,
		Affinity:                  agent.Spec.Affinity,
		TopologySpreadConstraints: agent.Spec.TopologySpreadConstraints,
		PriorityClassName:         agent.Spec.PriorityClassName,
	}

	// Pod-level annotations: start from any user-supplied spec.podAnnotations
	// (#477), then overlay operator-managed keys so the operator always wins
	// on conflict. The Prometheus scrape set (#chart beta.35 / #472) is
	// stamped when spec.metrics.enabled and metrics.podAnnotations are on.
	podAnnotations := mergeStringMap(nil, agent.Spec.PodAnnotations)
	if metricsEnabled(agent) && metricsPodAnnotationsEnabled(agent) {
		podAnnotations = mergeStringMap(podAnnotations, map[string]string{
			"prometheus.io/scrape": "true",
			"prometheus.io/port":   fmt.Sprintf("%d", harnessPort),
			"prometheus.io/path":   "/metrics",
		})
	}
	if len(podAnnotations) == 0 {
		podAnnotations = nil
	}

	// Pod-level labels: start from any user-supplied spec.podLabels (#477),
	// drop entries that would clobber operator-managed selector keys, then
	// overlay the canonical agent labels so the selector stays stable.
	podLabels := map[string]string{}
	reservedLabelKeys := map[string]struct{}{
		labelName:      {},
		labelComponent: {},
		labelPartOf:    {},
		labelManagedBy: {},
	}
	for k, v := range agent.Spec.PodLabels {
		if _, reserved := reservedLabelKeys[k]; reserved {
			continue
		}
		podLabels[k] = v
	}
	for k, v := range labels {
		podLabels[k] = v
	}

	return &appsv1.Deployment{
		// #1565: stamp TypeMeta so applySSA never depends on scheme
		// lookup for GVK inference.
		TypeMeta: metav1.TypeMeta{
			APIVersion: "apps/v1",
			Kind:       "Deployment",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name,
			Namespace: agent.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: replicas,
			Selector: &metav1.LabelSelector{MatchLabels: selector},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: podLabels, Annotations: podAnnotations},
				Spec:       podSpec,
			},
		},
	}
}

// witwavePromptVolumesAndMounts returns the pod Volumes and harness container
// VolumeMounts the agent needs to surface every WitwavePrompt bound to it. Each
// prompt becomes one Volume (ConfigMap-backed) and one subPath file mount
// at `<kind-dir>/<filename>` so the prompt file appears next to whatever
// gitSync dropped into the same directory. Prompts is sorted by CR name
// for deterministic pod-spec rendering.
//
// A prompt CR that lists a non-existent agent is silently skipped — the
// WitwavePrompt reconciler already records that in the CR status, and the
// agent pod spec stays valid either way.
func witwavePromptVolumesAndMounts(agent *witwavev1alpha1.WitwaveAgent, prompts []witwavev1alpha1.WitwavePrompt) ([]corev1.Volume, []corev1.VolumeMount) {
	if len(prompts) == 0 {
		return nil, nil
	}
	// Gather the prompt/ref pairs that target this agent, in deterministic
	// order (prompt name, then filename suffix).
	type binding struct {
		prompt *witwavev1alpha1.WitwavePrompt
		ref    witwavev1alpha1.WitwavePromptAgentRef
	}
	var bindings []binding
	for i := range prompts {
		p := &prompts[i]
		for _, ref := range p.Spec.AgentRefs {
			if ref.Name != agent.Name {
				continue
			}
			bindings = append(bindings, binding{prompt: p, ref: ref})
		}
	}
	if len(bindings) == 0 {
		return nil, nil
	}
	sort.Slice(bindings, func(i, j int) bool {
		if bindings[i].prompt.Name != bindings[j].prompt.Name {
			return bindings[i].prompt.Name < bindings[j].prompt.Name
		}
		return bindings[i].ref.FilenameSuffix < bindings[j].ref.FilenameSuffix
	})

	var vols []corev1.Volume
	var mounts []corev1.VolumeMount
	for _, b := range bindings {
		cmName := witwavePromptConfigMapName(b.prompt.Name, b.ref.Name)
		// #1338: include namespace AND a deterministic suffix hash so two
		// WitwavePrompts with the same name in different namespaces, or the
		// same prompt binding twice with different FilenameSuffix values,
		// don't produce duplicate Volume.Name entries (apiserver rejects).
		_vhash := sha256.Sum256([]byte(b.prompt.Namespace + "/" + b.ref.FilenameSuffix))
		// #1346: hash-first layout so DNS-1123-label truncation never
		// severs the hash. Also trim any trailing '-' that could result
		// from a name ending on a hyphen-boundary after clamp.
		volName := fmt.Sprintf("np-%s-%s", hex.EncodeToString(_vhash[:])[:8], b.prompt.Name)
		if len(volName) > 63 {
			volName = volName[:63]
		}
		volName = strings.TrimRight(volName, "-")
		filename := witwavePromptFilename(b.prompt, b.ref)
		dir := witwavePromptMountDir(b.prompt.Spec.Kind)
		if dir == "" {
			// Unknown kind — admission webhook should have rejected
			// this CR, but skip rather than emit a broken pod spec.
			continue
		}
		mountPath := dir + "/" + filename
		vols = append(vols, corev1.Volume{
			Name: volName,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: cmName},
				},
			},
		})
		mounts = append(mounts, corev1.VolumeMount{
			Name:      volName,
			MountPath: mountPath,
			SubPath:   filename,
			ReadOnly:  true,
		})
	}
	return vols, mounts
}

// mergeStringMap returns a new map containing all entries from base with
// entries from overlay applied on top (overlay wins on key collision).
// A nil base is treated as empty. The result is nil only when both inputs
// are empty.
func mergeStringMap(base, overlay map[string]string) map[string]string {
	if len(base) == 0 && len(overlay) == 0 {
		return nil
	}
	out := make(map[string]string, len(base)+len(overlay))
	for k, v := range base {
		out[k] = v
	}
	for k, v := range overlay {
		out[k] = v
	}
	return out
}

// buildService constructs the Service exposing the agent's harness
// HTTP port. Service.spec.type defaults to ClusterIP and is overridable
// via spec.serviceType (#chart beta.31 / #466). Prometheus scrape
// annotations honour spec.metrics.serviceAnnotations (default true)
// when spec.metrics.enabled is true (#chart beta.35 / #472).
func buildService(agent *witwavev1alpha1.WitwaveAgent) *corev1.Service {
	port := agent.Spec.Port
	if port == 0 {
		port = 8000
	}

	// Service port defaults to the container port (#479). When
	// spec.servicePort is set, it overrides the Service `port` only —
	// `targetPort` stays pinned to the container port so probes and
	// pod-to-pod traffic continue to land on the same listener.
	svcPort := port
	if agent.Spec.ServicePort != nil && *agent.Spec.ServicePort > 0 {
		svcPort = *agent.Spec.ServicePort
	}

	// Service prometheus.io/* annotation points at the harness metrics
	// listener (#836 / chart #687): harness app port + 1000 unless
	// spec.metricsPort is explicitly set, in which case it overrides for
	// backward compatibility.
	metricsPort := containerMetricsPort(agent.Spec.MetricsPort, port)

	annotations := map[string]string{}
	if metricsEnabled(agent) && metricsServiceAnnotationsEnabled(agent) {
		annotations["prometheus.io/scrape"] = "true"
		// Scrape the dedicated metrics listener (#643) rather than the
		// app port.
		annotations["prometheus.io/port"] = fmt.Sprintf("%d", metricsPort)
		annotations["prometheus.io/path"] = "/metrics"
	}

	svcType := agent.Spec.ServiceType
	if svcType == "" {
		svcType = corev1.ServiceTypeClusterIP
	}

	servicePorts := []corev1.ServicePort{{
		Name:       "http",
		Port:       svcPort,
		TargetPort: intstr.FromInt(int(port)),
	}}
	if metricsEnabled(agent) {
		// Expose the harness metrics port on the Service so ServiceMonitor
		// can target it by name (#643). Backends keep their own container
		// ports ("metrics-<backend>") and are scraped via PodMonitor rather
		// than fan-out through this Service — routing one named Service
		// port to multiple container-port names is ambiguous. Service
		// targetPort is now "metrics-harness" (#1249) to match the
		// container-port rename that removed the duplicate "metrics" name.
		servicePorts = append(servicePorts, corev1.ServicePort{
			Name:       "metrics",
			Port:       metricsPort,
			TargetPort: intstr.FromString("metrics-harness"),
		})
	}

	return &corev1.Service{
		// #1565: stamp TypeMeta so applySSA never depends on scheme
		// lookup for GVK inference.
		TypeMeta: metav1.TypeMeta{
			APIVersion: "v1",
			Kind:       "Service",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:        agent.Name,
			Namespace:   agent.Namespace,
			Labels:      agentLabels(agent),
			Annotations: annotations,
		},
		Spec: corev1.ServiceSpec{
			Type:     svcType,
			Selector: selectorLabels(agent),
			Ports:    servicePorts,
		},
	}
}

// podMonitorGVK is the monitoring.coreos.com/v1 PodMonitor GroupVersionKind
// used by the Prometheus Operator. Written via an unstructured client so
// the operator build has no hard dependency on prometheus-operator Go
// types (#582, mirrors the ServiceMonitor path).
var podMonitorGVK = schema.GroupVersionKind{
	Group:   "monitoring.coreos.com",
	Version: "v1",
	Kind:    "PodMonitor",
}

// podMonitorEnabled reports whether the agent opted in to PodMonitor
// reconciliation. Creation still additionally requires
// spec.metrics.enabled=true and CRD presence.
func podMonitorEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	return agent.Spec.PodMonitor != nil && agent.Spec.PodMonitor.Enabled
}

// buildPodMonitor assembles the unstructured PodMonitor manifest for an
// agent. One PodMetricsEndpoint per enabled backend, referencing the named
// `<backend>-metrics` container port. Mirrors
// charts/witwave/templates/podmonitor.yaml.
func buildPodMonitor(agent *witwavev1alpha1.WitwaveAgent) *unstructured.Unstructured {
	pm := agent.Spec.PodMonitor
	if pm == nil {
		return nil
	}
	interval := pm.Interval
	if interval == "" {
		interval = "30s"
	}
	scrapeTimeout := pm.ScrapeTimeout
	if scrapeTimeout == "" {
		scrapeTimeout = "10s"
	}

	labels := agentLabels(agent)
	for k, v := range pm.Labels {
		labels[k] = v
	}

	// One endpoint per uniquely-named metrics port (#1249): harness
	// exposes "metrics-harness" and each enabled backend exposes
	// "metrics-<backend>". The former single "metrics" name collided
	// across containers and the kubelet rejected the Pod — see #1249.
	endpoints := []interface{}{
		map[string]interface{}{
			"port":          "metrics-harness",
			"path":          "/metrics",
			"interval":      interval,
			"scrapeTimeout": scrapeTimeout,
		},
	}
	backendNames := append([]witwavev1alpha1.BackendSpec(nil), agent.Spec.Backends...)
	sort.Slice(backendNames, func(i, j int) bool { return backendNames[i].Name < backendNames[j].Name })
	for _, b := range backendNames {
		if !backendEnabled(b) {
			continue
		}
		endpoints = append(endpoints, map[string]interface{}{
			"port":          fmt.Sprintf("metrics-%s", b.Name),
			"path":          "/metrics",
			"interval":      interval,
			"scrapeTimeout": scrapeTimeout,
		})
	}

	obj := &unstructured.Unstructured{}
	obj.SetGroupVersionKind(podMonitorGVK)
	obj.SetName(fmt.Sprintf("%s-backends", agent.Name))
	obj.SetNamespace(agent.Namespace)
	obj.SetLabels(labels)
	obj.Object["spec"] = map[string]interface{}{
		"selector": map[string]interface{}{
			"matchLabels": map[string]interface{}{
				labelName: agent.Name,
			},
		},
		"namespaceSelector": map[string]interface{}{
			"matchNames": []interface{}{agent.Namespace},
		},
		"podMetricsEndpoints": endpoints,
	}
	return obj
}

// serviceMonitorGVK is the monitoring.coreos.com/v1 ServiceMonitor
// GroupVersionKind used by the Prometheus Operator. The operator writes
// ServiceMonitors via an unstructured client so the controller build has
// no hard dependency on prometheus-operator Go types — the CRD may or
// may not be installed on the target cluster (#476).
var serviceMonitorGVK = schema.GroupVersionKind{
	Group:   "monitoring.coreos.com",
	Version: "v1",
	Kind:    "ServiceMonitor",
}

// serviceMonitorEnabled reports whether the agent opted in to
// ServiceMonitor reconciliation. Creation still additionally requires
// spec.metrics.enabled=true (no point scraping a Service that has no
// annotations or metrics endpoint advertised) and CRD presence.
func serviceMonitorEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	return agent.Spec.ServiceMonitor != nil && agent.Spec.ServiceMonitor.Enabled
}

// buildServiceMonitor assembles the unstructured ServiceMonitor manifest
// for an agent. Mirrors charts/witwave/templates/servicemonitor.yaml so the
// two rendering paths produce equivalent ServiceMonitors for the same
// input: one endpoint named `http` (the harness Service port), scraped
// at /metrics with the configured interval + timeout, selected by the
// agent's app.kubernetes.io/name label, and namespace-scoped to the
// agent's namespace.
func buildServiceMonitor(agent *witwavev1alpha1.WitwaveAgent) *unstructured.Unstructured {
	sm := agent.Spec.ServiceMonitor
	if sm == nil {
		return nil
	}
	interval := sm.Interval
	if interval == "" {
		interval = "30s"
	}
	scrapeTimeout := sm.ScrapeTimeout
	if scrapeTimeout == "" {
		scrapeTimeout = "10s"
	}

	// Labels: start from the canonical agent labels so Prometheus's
	// ServiceMonitor selectors that look at the witwave label set still
	// match, then merge any tenancy labels the user supplied (e.g.
	// `release: kube-prometheus-stack`).
	labels := agentLabels(agent)
	for k, v := range sm.Labels {
		labels[k] = v
	}

	obj := &unstructured.Unstructured{}
	obj.SetGroupVersionKind(serviceMonitorGVK)
	obj.SetName(agent.Name)
	obj.SetNamespace(agent.Namespace)
	obj.SetLabels(labels)
	obj.Object["spec"] = map[string]interface{}{
		"selector": map[string]interface{}{
			"matchLabels": map[string]interface{}{
				labelName: agent.Name,
			},
		},
		"namespaceSelector": map[string]interface{}{
			"matchNames": []interface{}{agent.Namespace},
		},
		"endpoints": []interface{}{
			map[string]interface{}{
				"port":          "metrics",
				"path":          "/metrics",
				"interval":      interval,
				"scrapeTimeout": scrapeTimeout,
			},
		},
	}
	return obj
}

// metricsServiceAnnotationsEnabled / metricsPodAnnotationsEnabled resolve
// the per-agent metrics annotation toggles, defaulting to backward-
// compatible chart behaviour when the field is unset (#chart beta.35).
func metricsServiceAnnotationsEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	if agent.Spec.Metrics.ServiceAnnotations != nil {
		return *agent.Spec.Metrics.ServiceAnnotations
	}
	return true // chart default
}

func metricsPodAnnotationsEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	if agent.Spec.Metrics.PodAnnotations != nil {
		return *agent.Spec.Metrics.PodAnnotations
	}
	return false // chart default
}

// metricsEnabledValue renders the METRICS_ENABLED env var value for harness
// and backend containers, mirroring the chart's quoted bool semantics
// (#502). Backends gate their /metrics endpoint on this variable.
// metricsEnabled returns whether metrics are on for this agent. Nil dereferences
// as true so an agent CR submitted without spec.metrics.enabled set picks up the
// default-on posture. Mirrors backendEnabled() for sibling-pattern consistency.
func metricsEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	if agent.Spec.Metrics.Enabled != nil {
		return *agent.Spec.Metrics.Enabled
	}
	return true
}

func metricsEnabledValue(agent *witwavev1alpha1.WitwaveAgent) string {
	if metricsEnabled(agent) {
		return "true"
	}
	return "false"
}

// harnessEventsURL returns the in-pod URL the backend's HARNESS_EVENTS_URL
// env var should point at. The /internal/events/* routes live on the
// metrics listener when metrics are enabled (#924, NetworkPolicy posture)
// and fall back to the app listener when disabled (availability over
// posture). Stamping the wrong port produces silent 404s on every hook-
// decision POST — the failure mode that motivated #1781.
func harnessEventsURL(harnessPort, harnessMetricsPort int32, metricsOn bool) string {
	port := harnessPort
	if metricsOn {
		port = harnessMetricsPort
	}
	return fmt.Sprintf("http://localhost:%d", port)
}

// otelEnv renders the OTEL_* env-var list the operator should stamp onto
// each harness/backend container when spec.tracing.enabled is true. Mirrors
// the chart's witwave.otelEnv helper (#634) so operator-managed and
// chart-managed pods present identical OTLP wiring to the collector.
// Returns nil when tracing is disabled or unconfigured so callers can
// unconditionally `append(..., otelEnv(...)...)`.
func otelEnv(agent *witwavev1alpha1.WitwaveAgent, serviceName string) []corev1.EnvVar {
	t := agent.Spec.Tracing
	if t == nil || !t.Enabled {
		return nil
	}
	env := []corev1.EnvVar{
		{Name: "OTEL_ENABLED", Value: "true"},
	}
	if t.Endpoint != "" {
		env = append(env, corev1.EnvVar{Name: "OTEL_EXPORTER_OTLP_ENDPOINT", Value: t.Endpoint})
	}
	if t.Sampler != "" {
		env = append(env, corev1.EnvVar{Name: "OTEL_TRACES_SAMPLER", Value: t.Sampler})
	}
	if t.SamplerArg != "" {
		env = append(env, corev1.EnvVar{Name: "OTEL_TRACES_SAMPLER_ARG", Value: t.SamplerArg})
	}
	if serviceName != "" {
		env = append(env, corev1.EnvVar{Name: "OTEL_SERVICE_NAME", Value: serviceName})
	}
	return env
}

// corsEnv mirrors charts/witwave/templates/deployment.yaml's CORS env
// stamping (#763, #1748). When spec.cors.allowOrigins is non-empty,
// CORS_ALLOW_ORIGINS is rendered as the comma-joined list. When
// spec.cors.allowWildcard is true, CORS_ALLOW_WILDCARD=true is also
// rendered — matching the chart's #701 explicit-acknowledgement knob
// for wildcard origins. Returns nil when the spec doesn't request
// CORS so callers can unconditionally append.
//
// The wildcard-with-Ingress fail-render guard the chart enforces
// (deployment.yaml:191) is enforced at admission via the validating
// webhook so misconfigurations are caught before they hit the env.
func corsEnv(agent *witwavev1alpha1.WitwaveAgent) []corev1.EnvVar {
	c := agent.Spec.Cors
	if c == nil || len(c.AllowOrigins) == 0 {
		return nil
	}
	env := []corev1.EnvVar{
		{Name: "CORS_ALLOW_ORIGINS", Value: strings.Join(c.AllowOrigins, ",")},
	}
	if c.AllowWildcard {
		env = append(env, corev1.EnvVar{Name: "CORS_ALLOW_WILDCARD", Value: "true"})
	}
	return env
}

// containerMetricsPort resolves the per-container /metrics listener port
// following chart #687 + CRD #836 semantics:
//
//   - When overrideMetricsPort is non-zero (legacy spec.metricsPort is set),
//     every container in the pod falls back to that single value.
//   - Otherwise each container computes its own listener as appPort + 1000
//     so harness (8000 -> 9000) and backends (8010 -> 9010, 8011 -> 9011,
//     ...) don't collide on a fixed port.
//
// Returning a Max-capped int32 keeps us safe if an operator ever wires
// an absurd appPort like 65000; app_port + 1000 would overflow the port
// space, so we cap at 65535 and let the caller surface any downstream
// bind error.
func containerMetricsPort(overrideMetricsPort int32, appPort int32) int32 {
	if overrideMetricsPort > 0 {
		return overrideMetricsPort
	}
	p := appPort + 1000
	if p > 65535 {
		// #1222: clamp-to-65535 is a dangerous fallback — two containers
		// with appPort >= 65000 would both land on 65535 and collide at
		// runtime. We preserve the clamp here to avoid breaking the
		// non-error return signature used by three call sites, but the
		// collision detection in validateContainerMetricsPorts refuses
		// to render a pod that would produce duplicates. The CRD
		// validating webhook should reject appPort >= 64536 upstream;
		// #1222 / #1669 / #1699: webhook now rejects this; kept as
		// defence-in-depth for direct apiserver writes that bypass
		// admission.
		return 65535
	}
	if p <= 0 {
		return 9000
	}
	return p
}

// validateContainerMetricsPorts returns an error when the pod spec the
// renderer is about to produce would contain two containers whose
// metrics listener port is forced into the 65535 clamp region and
// thereby collides. Pre-fix the renderer silently clamped appPort+1000
// to 65535 for any appPort >= 65000, which meant two backends with
// appPort >= 65000 both landed on 65535 and crash-looped at bind. (#1222)
//
// Non-clamp collisions (e.g. harness and a backend both defaulting to
// appPort=8000 → 9000) are pre-existing behaviour: the harness owns the
// pod's single metrics endpoint by convention and backends that need
// their own scrape target set spec.metricsPort on the backend. Moving
// this validation stricter would be a behaviour change, not a bug fix.
// #1222 / #1669 / #1699: the validating webhook now tightens the upper
// bounds; this validator is the runtime defence-in-depth for direct
// apiserver writes that bypass admission.
func validateContainerMetricsPorts(agent *witwavev1alpha1.WitwaveAgent) error {
	_, err := metricsPortClampStatus(agent)
	return err
}

// metricsPortClampStatus reports the list of container names whose metrics
// listener was silently clamped to 65535 (appPort > 64535 with metrics
// enabled) alongside the collision-detection error that
// validateContainerMetricsPorts used to return on its own. Returning the
// names lets the reconciler emit a `MetricsPortClamped` Warning Event
// (#1250) so operators can see the misconfiguration in `kubectl describe`
// without the reconciler itself aborting the rollout when only one
// container is clamped (the single-clamp case is not a port collision).
func metricsPortClampStatus(agent *witwavev1alpha1.WitwaveAgent) (clamped []string, collision error) {
	const clampedValue int32 = 65535
	if !metricsEnabled(agent) {
		return nil, nil
	}
	harnessPort := agent.Spec.Port
	if harnessPort == 0 {
		harnessPort = 8000
	}
	if agent.Spec.MetricsPort == 0 && harnessPort > 64535 {
		clamped = append(clamped, "harness")
	}
	for _, b := range agent.Spec.Backends {
		if !backendEnabled(b) {
			continue
		}
		bPort := b.Port
		if bPort == 0 {
			bPort = 8000
		}
		if agent.Spec.MetricsPort == 0 && bPort > 64535 {
			clamped = append(clamped, b.Name)
		}
	}
	if len(clamped) > 1 {
		return clamped, fmt.Errorf(
			"metrics port collision: containers %v all have appPort>=%d and would clamp to %d; set spec.metricsPort explicitly or lower the app ports",
			clamped, 65535-999, clampedValue,
		)
	}
	return clamped, nil
}

// backendEnabled reports the per-backend enabled flag with default-true
// semantics (#chart beta.32). Returns true when the field is unset.
// preStopLifecycle returns a `lifecycle.preStop` exec sleep hook when the
// agent's PreStop spec is enabled. The sleep duration mirrors the chart
// default (5s) when DelaySeconds is unset, matching charts/witwave
// templates/deployment.yaml (#547, #512). Returns nil when PreStop is
// disabled or the spec is absent.
func preStopLifecycle(agent *witwavev1alpha1.WitwaveAgent) *corev1.Lifecycle {
	if agent.Spec.PreStop == nil || !agent.Spec.PreStop.Enabled {
		return nil
	}
	delay := agent.Spec.PreStop.DelaySeconds
	// #1223: honour an explicit 0 as "no sleep". Only negative values
	// fall back to the 5s chart default — a user who wrote 0 in the CR
	// means "skip the preStop sleep", not "I want the default".
	if delay < 0 {
		delay = 5
	}
	// #1252: `sleep 0` still forks /bin/sh and waits on a no-op; emit no
	// Lifecycle at all when the user asked for zero seconds. The container
	// keeps preStop.enabled=true as a documentation signal without paying
	// for a meaningless exec.
	if delay == 0 {
		return nil
	}
	return &corev1.Lifecycle{
		PreStop: &corev1.LifecycleHandler{
			Exec: &corev1.ExecAction{
				Command: []string{"/bin/sh", "-c", fmt.Sprintf("sleep %d", delay)},
			},
		},
	}
}

func backendEnabled(b witwavev1alpha1.BackendSpec) bool {
	if b.Enabled != nil {
		return *b.Enabled
	}
	return true
}

// buildHPA constructs the optional HorizontalPodAutoscaler, or nil when
// autoscaling is disabled.
func buildHPA(agent *witwavev1alpha1.WitwaveAgent) *autoscalingv2.HorizontalPodAutoscaler {
	a := agent.Spec.Autoscaling
	if a == nil || !a.Enabled {
		return nil
	}
	minR := a.MinReplicas
	if minR == 0 {
		minR = 1
	}
	maxR := a.MaxReplicas
	if maxR == 0 {
		maxR = 3
	}
	// #1411: apiserver rejects HPA with minReplicas > maxReplicas.
	// When a user sets MinReplicas=5 but leaves MaxReplicas unset
	// (defaults to 3), lift the max to the min so the render produces
	// a valid HPA instead of a reconcile loop on invalid-spec.
	if minR > maxR {
		maxR = minR
	}
	var metrics []autoscalingv2.MetricSpec
	if a.TargetCPUUtilizationPercentage != nil {
		metrics = append(metrics, autoscalingv2.MetricSpec{
			Type: autoscalingv2.ResourceMetricSourceType,
			Resource: &autoscalingv2.ResourceMetricSource{
				Name: corev1.ResourceCPU,
				Target: autoscalingv2.MetricTarget{
					Type:               autoscalingv2.UtilizationMetricType,
					AverageUtilization: a.TargetCPUUtilizationPercentage,
				},
			},
		})
	}
	if a.TargetMemoryUtilizationPercentage != nil {
		metrics = append(metrics, autoscalingv2.MetricSpec{
			Type: autoscalingv2.ResourceMetricSourceType,
			Resource: &autoscalingv2.ResourceMetricSource{
				Name: corev1.ResourceMemory,
				Target: autoscalingv2.MetricTarget{
					Type:               autoscalingv2.UtilizationMetricType,
					AverageUtilization: a.TargetMemoryUtilizationPercentage,
				},
			},
		})
	}
	// If neither target is set, default to 80% CPU so the HPA has at least one metric.
	if len(metrics) == 0 {
		def := int32(80)
		metrics = append(metrics, autoscalingv2.MetricSpec{
			Type: autoscalingv2.ResourceMetricSourceType,
			Resource: &autoscalingv2.ResourceMetricSource{
				Name: corev1.ResourceCPU,
				Target: autoscalingv2.MetricTarget{
					Type:               autoscalingv2.UtilizationMetricType,
					AverageUtilization: &def,
				},
			},
		})
	}
	return &autoscalingv2.HorizontalPodAutoscaler{
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name,
			Namespace: agent.Namespace,
			Labels:    agentLabels(agent),
		},
		Spec: autoscalingv2.HorizontalPodAutoscalerSpec{
			ScaleTargetRef: autoscalingv2.CrossVersionObjectReference{
				APIVersion: "apps/v1",
				Kind:       "Deployment",
				Name:       agent.Name,
			},
			MinReplicas: &minR,
			MaxReplicas: maxR,
			Metrics:     metrics,
		},
	}
}

// buildPDB constructs the optional PodDisruptionBudget, or nil when disabled.
// Exactly one of MinAvailable or MaxUnavailable is honoured — MaxUnavailable
// wins when both are set.
func buildPDB(agent *witwavev1alpha1.WitwaveAgent) *policyv1.PodDisruptionBudget {
	p := agent.Spec.PodDisruptionBudget
	if p == nil || !p.Enabled {
		return nil
	}
	pdb := &policyv1.PodDisruptionBudget{
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name,
			Namespace: agent.Namespace,
			Labels:    agentLabels(agent),
		},
		Spec: policyv1.PodDisruptionBudgetSpec{
			Selector: &metav1.LabelSelector{MatchLabels: selectorLabels(agent)},
		},
	}
	// #1220: when both MinAvailable and MaxUnavailable are set the
	// validating webhook should reject the spec, but the renderer also
	// runs in webhook-bypass paths (unit tests, older clusters without
	// the webhook wired in). Log a WARNING so the "picking MaxUnavailable"
	// behaviour does not silently ship with a surprising choice. Recorder
	// is not plumbed through at this call site — threading it would touch
	// every caller; the log suffices until the webhook is universally on.
	if p.MaxUnavailable != nil && p.MinAvailable != nil {
		logf.Log.WithName("witwaveagent-pdb").Info(
			"PDBConflict: both MinAvailable and MaxUnavailable set — picking MaxUnavailable; set exactly one",
			"agent", agent.Name,
			"namespace", agent.Namespace,
			"minAvailable", *p.MinAvailable,
			"maxUnavailable", *p.MaxUnavailable,
		)
	}
	if p.MaxUnavailable != nil {
		v := intstr.FromInt(int(*p.MaxUnavailable))
		pdb.Spec.MaxUnavailable = &v
	} else {
		min := int32(1)
		if p.MinAvailable != nil {
			min = *p.MinAvailable
		}
		v := intstr.FromInt(int(min))
		pdb.Spec.MinAvailable = &v
	}
	return pdb
}

// PVCBuildError describes a backend PVC entry that could not be built.
// Surfaced to the controller so it can log + emit events; previously these
// were silently dropped (#454).
type PVCBuildError struct {
	BackendName string
	Size        string
	Err         error
}

// Error formats the PVCBuildError as
// `backend "<name>": invalid storage.size "<size>": <underlying>` so
// the controller's log + Event surfaces name the offending backend,
// the rejected size value, and the underlying resource.Quantity parse
// failure in a single line.
func (e *PVCBuildError) Error() string {
	return fmt.Sprintf("backend %q: invalid storage.size %q: %v", e.BackendName, e.Size, e.Err)
}

// buildBackendPVCs returns the PVCs the operator should create for each
// backend whose Storage.Enabled is true and whose ExistingClaim is empty.
// The second return value lists per-backend size-parse failures so the
// caller can surface them as logs + events instead of silent drops (#454).
func buildBackendPVCs(agent *witwavev1alpha1.WitwaveAgent) ([]*corev1.PersistentVolumeClaim, []*PVCBuildError) {
	var out []*corev1.PersistentVolumeClaim
	var errs []*PVCBuildError
	for _, b := range agent.Spec.Backends {
		if !backendEnabled(b) {
			continue
		}
		if b.Storage == nil || !b.Storage.Enabled || b.Storage.ExistingClaim != "" {
			continue
		}
		size := b.Storage.Size
		if size == "" {
			size = "1Gi"
		}
		qty, err := resource.ParseQuantity(size)
		if err != nil {
			errs = append(errs, &PVCBuildError{BackendName: b.Name, Size: size, Err: err})
			continue
		}
		// AccessModes default: preserve the historical RWO behaviour for
		// single-replica deployments. Users opt into RWX / RWOP by
		// populating spec.backends[].storage.accessModes explicitly
		// (#614).
		accessModes := b.Storage.AccessModes
		if len(accessModes) == 0 {
			accessModes = []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}
		} else {
			// Copy so a downstream mutation of the PVC spec can't
			// alias back into the WitwaveAgent's AccessModes slice.
			accessModes = append([]corev1.PersistentVolumeAccessMode(nil), accessModes...)
		}
		pvc := &corev1.PersistentVolumeClaim{
			ObjectMeta: metav1.ObjectMeta{
				Name:      fmt.Sprintf("%s-%s-data", agent.Name, b.Name),
				Namespace: agent.Namespace,
				Labels:    agentLabels(agent),
			},
			Spec: corev1.PersistentVolumeClaimSpec{
				AccessModes: accessModes,
				Resources: corev1.VolumeResourceRequirements{
					Requests: corev1.ResourceList{corev1.ResourceStorage: qty},
				},
			},
		}
		if b.Storage.StorageClassName != "" {
			pvc.Spec.StorageClassName = &b.Storage.StorageClassName
		}
		out = append(out, pvc)
	}
	return out, errs
}

// ── small helpers ─────────────────────────────────────────────────────────────

func boolPtr(b bool) *bool    { return &b }
func int32Ptr(i int32) *int32 { return &i }
func int64Ptr(i int64) *int64 { return &i }

// dashboardLabels returns the label set for the per-agent dashboard
// Deployment + Service. The "component=dashboard" label distinguishes
// them from the agent's own pods so selectors stay unambiguous (#470).
func dashboardLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	return map[string]string{
		labelName:      agent.Name + "-dashboard",
		labelComponent: "dashboard",
		labelPartOf:    partOf,
		labelManagedBy: managedBy,
	}
}

func dashboardSelectorLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	return map[string]string{
		labelName: agent.Name + "-dashboard",
	}
}

// buildDashboardConfigMap renders the nginx template the dashboard image
// reads from /etc/nginx/templates/. The template teaches nginx about the
// owned agent's service at /api/agents/<name>/... and serves /api/team
// inline so the dashboard client's two-phase discovery (directory + per-
// agent fan-out) works against a single-CR deployment (#470). Returns nil
// when the dashboard is disabled.
//
// The ConfigMap mirrors the logic in
// charts/witwave/templates/configmap-dashboard-nginx.yaml but scoped to the
// one WitwaveAgent the operator owns — per-CR dashboards can't see other
// agents the operator happens to manage; that's a deliberate boundary.
func buildDashboardConfigMap(agent *witwavev1alpha1.WitwaveAgent) *corev1.ConfigMap {
	d := agent.Spec.Dashboard
	if d == nil || !d.Enabled {
		return nil
	}

	agentPort := agent.Spec.Port
	if agentPort == 0 {
		agentPort = 8000
	}

	// FQDN — nginx's resolver doesn't apply Kubernetes search domains, so
	// short names land as NXDOMAIN. The cluster DNS zone defaults to
	// `cluster.local` but is overridable via spec.dashboard.clusterDomain
	// for clusters bootstrapped with a custom --service-dns-domain
	// (risk #581). The CRD pattern validates the value charset, so it is
	// safe to interpolate into the nginx template verbatim.
	clusterDomain := d.ClusterDomain
	if clusterDomain == "" {
		clusterDomain = "cluster.local"
	}
	upstream := fmt.Sprintf("http://%s.%s.svc.%s:%d", agent.Name, agent.Namespace, clusterDomain, agentPort)
	// #1255: directory entry URL must be the same FQDN the upstream line
	// uses — a short-name `http://<agent>:<port>` entry makes the dashboard
	// client look up the Service at its own namespace search path, which
	// breaks once the dashboard lands in a different namespace than the
	// agent (typical multi-tenant install).
	directoryURL := fmt.Sprintf("http://%s.%s.svc.%s:%d", agent.Name, agent.Namespace, clusterDomain, agentPort)
	directory := fmt.Sprintf(`[{"name":%q,"url":%q}]`, agent.Name, directoryURL)

	tpl := `server {
  listen 3000;
  server_name _;

  resolver ${NGINX_LOCAL_RESOLVERS} valid=30s ipv6=off;

  root /usr/share/nginx/html;
  index index.html;

  location / {
    try_files $uri $uri/ /index.html;
  }

  location = /health {
    access_log off;
    add_header Content-Type application/json;
    return 200 '{"status":"ok","component":"dashboard"}';
  }

  location = /api/team {
    default_type application/json;
    return 200 '` + directory + `';
  }

  location ~ ^/api/agents/` + regexp.QuoteMeta(agent.Name) + `/(.*)$ {
    proxy_pass ` + upstream + `/$1$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 310s;
    client_max_body_size 1m;
  }

  location /api/ {
    return 404;
  }
}
`

	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-dashboard-nginx",
			Namespace: agent.Namespace,
			Labels:    dashboardLabels(agent),
		},
		Data: map[string]string{
			"default.conf.template": tpl,
		},
	}
}

// buildDashboardDeployment returns the Deployment for the per-agent Vue 3
// dashboard, or nil when the dashboard is disabled. The dashboard container
// always listens on 3000; the Service port is controlled separately via
// DashboardSpec.Port. Per-agent nginx routing comes from the sibling
// ConfigMap built by buildDashboardConfigMap — the browser talks to the
// dashboard pod, which proxies to the owned agent's harness directly. No
// HARNESS_URL env var is required (that was the legacy /api/* catch-all
// path retired in beta.46).
func buildDashboardDeployment(agent *witwavev1alpha1.WitwaveAgent, appVersion string) *appsv1.Deployment {
	d := agent.Spec.Dashboard
	if d == nil || !d.Enabled {
		return nil
	}

	replicas := int32(1)
	if d.Replicas != nil {
		replicas = *d.Replicas
	}

	// Image — fall back to the conventional ghcr image when the caller
	// didn't specify one, so the simplest WitwaveAgent with
	// `dashboard.enabled: true` still produces a deployable pod.
	img := witwavev1alpha1.ImageSpec{
		Repository: "ghcr.io/witwave-ai/images/dashboard",
	}
	if d.Image != nil {
		img = *d.Image
	}

	probeSpec := witwavev1alpha1.ProbeSpec{}
	containerPort := int32(3000)

	nginxTemplateVol := agent.Name + "-dashboard-nginx"

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-dashboard",
			Namespace: agent.Namespace,
			Labels:    dashboardLabels(agent),
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: int32Ptr(replicas),
			Selector: &metav1.LabelSelector{
				MatchLabels: dashboardSelectorLabels(agent),
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: dashboardLabels(agent),
				},
				Spec: corev1.PodSpec{
					AutomountServiceAccountToken: boolPtr(false),
					ImagePullSecrets:             agent.Spec.ImagePullSecrets,
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: boolPtr(true),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					Volumes: []corev1.Volume{{
						Name: nginxTemplateVol,
						VolumeSource: corev1.VolumeSource{
							ConfigMap: &corev1.ConfigMapVolumeSource{
								LocalObjectReference: corev1.LocalObjectReference{
									Name: agent.Name + "-dashboard-nginx",
								},
							},
						},
					}},
					Containers: []corev1.Container{{
						Name:            "dashboard",
						Image:           imageRef(img, appVersion),
						ImagePullPolicy: imagePullPolicy(img),
						Ports: []corev1.ContainerPort{{
							Name:          "http",
							ContainerPort: containerPort,
							Protocol:      corev1.ProtocolTCP,
						}},
						Env: []corev1.EnvVar{{
							// Enables the nginx-unprivileged image's local-
							// resolvers envsh hook so NGINX_LOCAL_RESOLVERS
							// gets populated from /etc/resolv.conf at boot
							// — required for the resolver directive above.
							Name:  "NGINX_ENTRYPOINT_LOCAL_RESOLVERS",
							Value: "1",
						}},
						VolumeMounts: []corev1.VolumeMount{{
							Name:      nginxTemplateVol,
							MountPath: "/etc/nginx/templates",
							ReadOnly:  true,
						}},
						SecurityContext: &corev1.SecurityContext{
							AllowPrivilegeEscalation: boolPtr(false),
							Capabilities: &corev1.Capabilities{
								Drop: []corev1.Capability{"ALL"},
							},
							RunAsNonRoot: boolPtr(true),
						},
						LivenessProbe:  httpProbe(containerPort, "/health", probeDefaults(&probeSpec, true)),
						ReadinessProbe: httpProbe(containerPort, "/health", probeDefaults(&probeSpec, false)),
						Resources:      d.Resources,
					}},
				},
			},
		},
	}
}

// buildDashboardService returns the ClusterIP Service for the dashboard,
// or nil when disabled. The Service port defaults to 80 (what users expect
// to hit in-cluster); targetPort is always 3000 to match the nginx listen.
func buildDashboardService(agent *witwavev1alpha1.WitwaveAgent) *corev1.Service {
	d := agent.Spec.Dashboard
	if d == nil || !d.Enabled {
		return nil
	}
	port := d.Port
	if port == 0 {
		port = 80
	}
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-dashboard",
			Namespace: agent.Namespace,
			Labels:    dashboardLabels(agent),
		},
		Spec: corev1.ServiceSpec{
			Type:     corev1.ServiceTypeClusterIP,
			Selector: dashboardSelectorLabels(agent),
			Ports: []corev1.ServicePort{{
				Name:       "http",
				Port:       port,
				TargetPort: intstr.FromInt(3000),
			}},
		},
	}
}

// ── Team manifest ─────────────────────────────────────────────────────────────
//
// The manifest ConfigMap lists every WitwaveAgent that shares a team (or
// namespace when no team label is set) so each harness can route /team
// and /proxy/{name} requests to peers by name. This is the operator's
// equivalent of charts/witwave/templates/configmap-manifest.yaml (#474).
//
// Design note (option a in the gap-approve comment): each reconcile
// recomputes the per-team manifest and writes a CM owned by the agent
// currently being reconciled. A content-hash short-circuit (below)
// avoids churning the CM — and therefore the mounted ConfigMap in every
// pod — when membership is unchanged.

// teamKey returns the value of the team label on an agent, or an empty
// string when the label is absent. Agents with the same team key share
// a manifest; agents without the label are grouped per-namespace.
func teamKey(agent *witwavev1alpha1.WitwaveAgent) string {
	if agent.Labels == nil {
		return ""
	}
	return agent.Labels[teamLabel]
}

// manifestConfigMapName is the name of the per-team manifest CM for a
// given agent. When the team label is set we include its value so
// multiple teams within a namespace each get their own manifest;
// otherwise we fall back to the namespace-level manifest.
func manifestConfigMapName(agent *witwavev1alpha1.WitwaveAgent) string {
	if t := teamKey(agent); t != "" {
		return fmt.Sprintf("witwave-manifest-%s", t)
	}
	return "witwave-manifest"
}

// manifestLabels labels the manifest CM so it can be listed without
// colliding with per-agent owned ConfigMaps. The team key (if any) is
// included so the owning reconcile can short-circuit on a label-
// selector list call.
func manifestLabels(team string) map[string]string {
	l := map[string]string{
		labelComponent: componentManifest,
		labelPartOf:    partOf,
		labelManagedBy: managedBy,
	}
	if team != "" {
		l[teamLabel] = team
	}
	return l
}

// manifestMember is one entry in the rendered manifest.json file. The
// shape matches the chart's manifest exactly (name + URL) so the
// harness parser doesn't care which rendering path produced it.
type manifestMember struct {
	Name string
	Port int32
}

// buildManifestJSON renders the manifest.json payload for a fixed set
// of members. Members are sorted by name for deterministic output,
// which is what the hash short-circuit below relies on. The URL shape
// mirrors the chart: in-cluster service DNS `http://<name>:<port>`.
func buildManifestJSON(members []manifestMember) string {
	sort.Slice(members, func(i, j int) bool { return members[i].Name < members[j].Name })
	var b strings.Builder
	b.WriteString("{\n  \"team\": [")
	for i, m := range members {
		if i == 0 {
			b.WriteString("\n")
		}
		port := m.Port
		if port == 0 {
			port = 8000
		}
		fmt.Fprintf(&b, "    {\n      \"name\": %q,\n      \"url\": \"http://%s:%d\"\n    }",
			m.Name, m.Name, port)
		if i < len(members)-1 {
			b.WriteString(",\n")
		} else {
			b.WriteString("\n  ")
		}
	}
	// Both empty-list and non-empty branches used to write the same
	// "]\n}\n" string; folded to a single write (#904). The loop
	// above already handles the trailing indent so no separate
	// branch is needed. The output for an empty `members` slice is
	//   {
	//     "team": []
	//   }
	// which matches the chart's rendering byte-for-byte.
	b.WriteString("]\n}\n")
	return b.String()
}

// manifestContentHash returns a short stable hash of the manifest JSON
// used as an annotation on the rendered CM. The reconciler uses it to
// skip writes when the desired payload matches what's already in the
// cluster, so a membership-unchanged reconcile doesn't churn the
// mounted volume on every harness pod.
func manifestContentHash(body string) string {
	sum := sha256.Sum256([]byte(body))
	return hex.EncodeToString(sum[:8])
}

// manifestHashAnnotation is the annotation key used to stamp the
// content hash onto the CM. The ConfigMap list in the reconciler
// reads this to short-circuit equal-content reconciles.
const manifestHashAnnotation = "witwave.ai/manifest-hash"

// buildManifestConfigMap assembles the per-team manifest ConfigMap
// covering the given members. `ownerAgent` supplies the namespace and
// team key; `memberAgents` are all CURRENT live WitwaveAgent members of
// that team, which the CM records as **non-controller** OwnerReferences
// so Kubernetes garbage collection only removes the CM when every team
// member has been deleted (#684).
//
// Prior implementations set a single controller OwnerReference pointing
// at whichever agent's reconcile happened to create the CM. Deleting
// that one agent then triggered cascade GC on the shared CM — breaking
// mounts on every surviving pod until another reconcile could rebuild
// it. Switching to multi-owner non-controller refs keeps K8s's GC
// semantics correct: the CM survives until its LAST owner is removed.
//
// The returned hash covers both the JSON body AND the set of owner
// UIDs so the reconciler's short-circuit still fires on membership
// changes that leave the rendered JSON identical (e.g. a rename that
// coincidentally produces the same set of entries).
func buildManifestConfigMap(
	ownerAgent *witwavev1alpha1.WitwaveAgent,
	memberAgents []*witwavev1alpha1.WitwaveAgent,
	members []manifestMember,
) (*corev1.ConfigMap, string) {
	body := buildManifestJSON(members)
	ownerRefs := buildManifestOwnerRefs(memberAgents)
	hash := manifestContentHash(body + manifestOwnerRefsHashInput(ownerRefs))
	team := teamKey(ownerAgent)
	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:            manifestConfigMapName(ownerAgent),
			Namespace:       ownerAgent.Namespace,
			Labels:          manifestLabels(team),
			OwnerReferences: ownerRefs,
			Annotations: map[string]string{
				manifestHashAnnotation: hash,
			},
		},
		Data: map[string]string{
			manifestSubPath: body,
		},
	}, hash
}

// buildManifestOwnerRefs returns one non-controller OwnerReference per
// live team member. Entries are sorted by UID for stable ordering so the
// hash short-circuit is deterministic. Agents without a UID (e.g. not
// yet persisted) are skipped — they'll get their ref on the next
// reconcile after the apiserver assigns the UID.
//
// The skip is the narrow race documented on #1016: APIReader is expected
// to return fully-persisted objects (cache bypass → direct apiserver
// read), so a nil/empty-UID member here means the manifest body lists a
// team peer whose OwnerReference we didn't add to the CM. To keep that
// race observable we increment a metric and emit a warning log per
// skipped member rather than silently dropping the entry.
func buildManifestOwnerRefs(agents []*witwavev1alpha1.WitwaveAgent) []metav1.OwnerReference {
	refs := make([]metav1.OwnerReference, 0, len(agents))
	for _, a := range agents {
		if a == nil {
			continue
		}
		if a.UID == "" {
			witwaveagentManifestOwnerRefSkippedNoUIDTotal.WithLabelValues(a.Namespace).Inc()
			logf.Log.WithName("witwaveagent-manifest").Info(
				"skipping manifest OwnerReference: member has empty UID (APIReader race?)",
				"namespace", a.Namespace,
				"name", a.Name,
			)
			continue
		}
		// #1251: allocate fresh *bool per iteration via boolPtr so each
		// OwnerReference owns its own pointer target. Declaring locals
		// inside the loop body is already per-iteration under Go
		// semantics, but routing through boolPtr — which returns the
		// address of a fresh named parameter — makes the non-aliasing
		// explicit and is robust to future refactors that might hoist
		// the locals out of the loop.
		refs = append(refs, metav1.OwnerReference{
			APIVersion:         witwavev1alpha1.GroupVersion.String(),
			Kind:               "WitwaveAgent",
			Name:               a.Name,
			UID:                a.UID,
			Controller:         boolPtr(false),
			BlockOwnerDeletion: boolPtr(false),
		})
	}
	sort.Slice(refs, func(i, j int) bool { return refs[i].UID < refs[j].UID })
	return refs
}

// manifestOwnerRefsHashInput renders owner refs deterministically so the
// content-hash short-circuit detects membership-only edits that don't
// change the rendered JSON.
func manifestOwnerRefsHashInput(refs []metav1.OwnerReference) string {
	if len(refs) == 0 {
		return "|"
	}
	var b strings.Builder
	b.WriteString("|owners=")
	for i, r := range refs {
		if i > 0 {
			b.WriteString(",")
		}
		b.WriteString(string(r.UID))
	}
	return b.String()
}

// manifestVolumeAndMount returns the pod Volume + harness VolumeMount
// that surface the per-team manifest CM at the path the chart uses.
// Always attached — an empty CM still renders a valid `{"team": []}`
// payload, so the harness's startup parse never breaks.
//
// The ConfigMap volume is marked Optional so a brief absence of the
// team-wide manifest CM (e.g. the moment after a single-member team's
// sole agent is deleted and the CM is GC'd, or during the upgrade from
// the old controller-owner shape to the multi-owner shape (#684))
// doesn't block kubelet from launching the pod. The harness opens the
// manifest file defensively and tolerates a missing path as "empty
// team", so an optional mount is strictly safer than a required one.
func manifestVolumeAndMount(cmName string) (corev1.Volume, corev1.VolumeMount) {
	optional := true
	return corev1.Volume{
			Name: manifestVolumeName,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: cmName},
					Optional:             &optional,
				},
			},
		}, corev1.VolumeMount{
			Name:      manifestVolumeName,
			MountPath: manifestMountPath,
			SubPath:   manifestSubPath,
			ReadOnly:  true,
		}
}

func envListHasName(env []corev1.EnvVar, name string) bool {
	for _, e := range env {
		if e.Name == name {
			return true
		}
	}
	return false
}

func volumeMountsHavePath(mounts []corev1.VolumeMount, mountPath string) bool {
	for _, m := range mounts {
		if m.MountPath == mountPath {
			return true
		}
	}
	return false
}
