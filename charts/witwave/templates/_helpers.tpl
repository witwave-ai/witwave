{{/*
witwave.serviceMeshPodAnnotations — emit the service-mesh sidecar-injection
annotations uniform across every pod template in the chart (#1121).
Pre-#1121 operators hand-assembled per-mesh annotations in each of
agents[].podAnnotations, mcpTools.<name>.podAnnotations,
dashboard.podAnnotations, and witwave-operator.podAnnotations.

Supported meshes:
  linkerd — `linkerd.io/inject: enabled`
  istio   — `sidecar.istio.io/inject: "true"`
  none    — no annotations (explicit opt-out shape)

Caller:
  .root — top-level .Values handle

Emits nothing when serviceMesh.enabled is falsey or type is missing/none.
Output lines include trailing newlines so the caller can nindent the
result into an `annotations:` block directly.

Usage:
  {{- include "witwave.serviceMeshPodAnnotations" (dict "root" $) | nindent 8 }}
*/}}
{{- define "witwave.serviceMeshPodAnnotations" -}}
{{- $root := .root -}}
{{- $sm := ($root.Values.serviceMesh | default dict) -}}
{{- if $sm.enabled -}}
{{- $type := ($sm.type | default "none" | toString | lower) -}}
{{- if eq $type "linkerd" -}}
linkerd.io/inject: enabled
{{ end -}}
{{- if eq $type "istio" -}}
sidecar.istio.io/inject: "true"
{{ end -}}
{{- end -}}
{{- end }}

{{/*
witwave.hardenedContainerSecurityContext — PSS-restricted-compliant container
securityContext applied to every container and initContainer across the
chart (#1073). PSS-restricted evaluates per-container: missing fields
(runAsNonRoot, seccompProfile, allowPrivilegeEscalation:false,
capabilities.drop:ALL) are a per-container reject even when the
pod-level securityContext sets them. This helper makes the full set
explicit on the container so the chart installs cleanly under
restricted enforcement.

readOnlyRootFilesystem toggles off (.Values.podSecurity.readOnlyRootFilesystem)
which defaults to true under #1073. Write-happy paths that need rw
access (e.g. helm's cache dir) get an emptyDir volume from the caller.

Caller passes:
  .root               — top-level .Values (for .Values.podSecurity lookup)

Usage:
  {{- include "witwave.hardenedContainerSecurityContext" (dict "root" $) | nindent 12 }}
*/}}
{{- define "witwave.hardenedContainerSecurityContext" -}}
{{- $root := .root -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- if (($root.Values).podSecurity).readOnlyRootFilesystem }}
readOnlyRootFilesystem: true
{{- end }}
{{- end }}

{{/*
witwave.assertSecretRefSafe — refuse to render when a Secret reference uses a
name that matches the REPLACE_ME / test-artifact denylist (#1072). Used
wherever secret material lands in a pod (envFrom.secretRef.name,
valueFrom.secretKeyRef.name, credentials.existingSecret). Bypass vectors
closed: valueFrom / envFrom / existingSecret pointing at Secrets named
`witwave-test-credentials`, `bob-claude-test-secrets`, or anything still
stamped with a `REPLACE_ME` marker.

Patterns refused (case-insensitive):
  - contains "REPLACE_ME"
  - contains "test" (word-ish — "-test-", "test-", "-test", equals "test")
  - equals "witwave-test-credentials"

Caller passes:
  .name     — the Secret reference name (empty string is allowed and ignored)
  .context  — string used in the failure message (e.g. "agents[bob].envFrom[0].secretRef")

Usage:
  {{- include "witwave.assertSecretRefSafe" (dict "name" $refName "context" $ctx) }}
*/}}
{{- define "witwave.assertSecretRefSafe" -}}
{{- $name := (.name | default "" | toString) -}}
{{- $ctx := .context | default "(unknown)" -}}
{{- if $name -}}
{{- $lc := lower $name -}}
{{- if contains "replace_me" $lc -}}
{{- fail (printf "charts/witwave: %s references Secret %q which matches the REPLACE_ME denylist (#1072). Rename the Secret to a real production name (not a placeholder) before deploying." $ctx $name) -}}
{{- end -}}
{{- if eq $lc "witwave-test-credentials" -}}
{{- fail (printf "charts/witwave: %s references Secret %q which is reserved for smoke tests (#1072). Provision a per-deployment Secret instead." $ctx $name) -}}
{{- end -}}
{{/* Word-ish match on "test": standalone, leading, trailing, or hyphen-wrapped. */}}
{{- if or (eq $lc "test") (hasPrefix "test-" $lc) (hasSuffix "-test" $lc) (contains "-test-" $lc) -}}
{{- fail (printf "charts/witwave: %s references Secret %q which matches the *test* denylist (#1072). Rename the Secret to a real production name before deploying. Set the Secret name to something without leading/trailing/interior `-test-` segments." $ctx $name) -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Default git-sync image, resolved as repository:tag where tag falls back to
.Chart.AppVersion (matching every other image in this chart). Per-entry
.gitSyncs[].image overrides still take precedence and accept any string.
Backwards-compat: if a user still has the legacy `gitSync.image: <string>`
shape, the string is returned verbatim.
Usage: {{ include "witwave.gitSyncImage" . }}
*/}}
{{- define "witwave.gitSyncImage" -}}
{{- if kindIs "string" .Values.gitSync.image -}}
{{ .Values.gitSync.image }}
{{- else -}}
{{ .Values.gitSync.image.repository }}:{{ .Values.gitSync.image.tag | default .Chart.AppVersion }}
{{- end -}}
{{- end }}

{{/*
Chart label value (used by helm.sh/chart).
Usage: {{ include "witwave.chart" . }}
*/}}
{{- define "witwave.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Agent component labels (harness).
Emits the full Kubernetes Recommended Labels set. NOT suitable for use in
selector.matchLabels (includes `app.kubernetes.io/version` and `helm.sh/chart`,
which change on chart upgrade). For selectors, use `witwave.agentSelectorLabels`.
Usage: {{- include "witwave.agentLabels" (dict "name" .name "root" $) | nindent 4 }}
Legacy (no recommended labels): {{- include "witwave.agentLabels" .name | nindent 4 }}
*/}}
{{- define "witwave.agentLabels" -}}
{{- if kindIs "map" . -}}
{{- $root := .root -}}
helm.sh/chart: {{ include "witwave.chart" $root }}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: harness
app.kubernetes.io/part-of: witwave
app.kubernetes.io/managed-by: {{ $root.Release.Service }}
{{- if $root.Chart.AppVersion }}
app.kubernetes.io/version: {{ $root.Chart.AppVersion | quote }}
{{- end }}
{{- else -}}
app.kubernetes.io/name: {{ . }}
app.kubernetes.io/component: harness
app.kubernetes.io/part-of: witwave
app.kubernetes.io/managed-by: helm
{{- end -}}
{{- end }}

{{/*
Agent selector labels — stable across upgrades (safe for selector.matchLabels).
Intentionally omits `app.kubernetes.io/version` and `helm.sh/chart`.
Usage: {{- include "witwave.agentSelectorLabels" .name | nindent 6 }}
*/}}
{{- define "witwave.agentSelectorLabels" -}}
app.kubernetes.io/name: {{ . }}
app.kubernetes.io/component: harness
app.kubernetes.io/part-of: witwave
{{- end }}

{{/*
Runtime-storage PVC labels.
*/}}
{{- define "witwave.runtimeStorageLabels" -}}
helm.sh/chart: {{ include "witwave.chart" .root }}
app.kubernetes.io/name: {{ .agentName }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: runtime-storage
app.kubernetes.io/part-of: witwave
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- if .root.Chart.AppVersion }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Backend component labels.
Emits the full Kubernetes Recommended Labels set. NOT suitable for
selector.matchLabels. For selectors, use `witwave.backendSelectorLabels`.
Usage: {{- include "witwave.backendLabels" (dict "agentName" .name "backendName" .backendName "root" $) | nindent 4 }}
Legacy (no recommended labels): {{- include "witwave.backendLabels" (dict "agentName" .name "backendName" .backendName) | nindent 4 }}
*/}}
{{- define "witwave.backendLabels" -}}
{{- $root := .root -}}
{{- if $root -}}
helm.sh/chart: {{ include "witwave.chart" $root }}
app.kubernetes.io/name: {{ .agentName }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ .backendName }}-backend
app.kubernetes.io/part-of: witwave
app.kubernetes.io/managed-by: {{ $root.Release.Service }}
{{- if $root.Chart.AppVersion }}
app.kubernetes.io/version: {{ $root.Chart.AppVersion | quote }}
{{- end }}
{{- else -}}
app.kubernetes.io/name: {{ .agentName }}
app.kubernetes.io/component: {{ .backendName }}-backend
app.kubernetes.io/part-of: witwave
app.kubernetes.io/managed-by: helm
{{- end -}}
{{- end }}

{{/*
Backend selector labels — stable across upgrades (safe for selector.matchLabels).
Usage: {{- include "witwave.backendSelectorLabels" (dict "agentName" .name "backendName" .backendName) | nindent 6 }}
*/}}
{{- define "witwave.backendSelectorLabels" -}}
app.kubernetes.io/name: {{ .agentName }}
app.kubernetes.io/component: {{ .backendName }}-backend
app.kubernetes.io/part-of: witwave
{{- end }}

{{/*
Generate a git-mapping emptyDir volume name from agent, context, and dest path.
The dest is hashed (sha1sum, truncated to 10 chars) rather than slug-translated
so that paths differing only by `/` vs `-` vs `.` produce distinct names (#573).
The final name is capped to 63 chars to satisfy Kubernetes' DNS-1123 label limit.
Usage: {{- include "witwave.gmVolumeName" (dict "agentName" $agentName "context" "agent" "dest" .dest) }}
*/}}
{{- define "witwave.gmVolumeName" -}}
{{- $hash := printf "%s" .dest | sha1sum | trunc 10 -}}
{{- printf "gm-%s-%s-%s" .agentName .context $hash | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Returns true if an agent has any git mappings (agent-level or backend-level).
Usage: {{- if include "witwave.hasMappings" . }}
*/}}
{{- define "witwave.hasMappings" -}}
{{- $has := false -}}
{{- if .gitMappings }}{{- $has = true }}{{- end -}}
{{- range .backends }}
{{- if eq (include "witwave.enabled" .) "true" }}
{{- if .gitMappings }}{{- $has = true }}{{- end }}
{{- end }}
{{- end -}}
{{- if $has }}true{{- end -}}
{{- end }}

{{/*
Git-mapping volume mounts for a given agent — mounts script, mappings ConfigMaps,
and emptyDir destinations. Rendered into git-sync sidecar and git-map-init containers.
Usage: {{- include "witwave.gitMappingMounts" (dict "agent" $agent "release" .Release.Name) | nindent 12 }}
*/}}
{{- define "witwave.gitMappingMounts" -}}
{{- $agentName := .agent.name -}}
{{- $release := .release -}}
- name: {{ $release }}-git-sync-script
  mountPath: /witwave-scripts
{{- if .agent.gitMappings }}
- name: {{ $release }}-{{ $agentName }}-git-mappings
  mountPath: /witwave-mappings/agent
{{- range .agent.gitMappings }}
- name: {{ include "witwave.gmVolumeName" (dict "agentName" $agentName "context" "agent" "dest" .dest) }}
  mountPath: {{ .dest }}
{{- end }}
{{- end }}
{{- range .agent.backends }}
{{- if eq (include "witwave.enabled" .) "true" }}
{{- if .gitMappings }}
- name: {{ $release }}-{{ $agentName }}-{{ .name }}-git-mappings
  mountPath: /witwave-mappings/{{ .name }}
{{- $backendName := .name }}
{{- range .gitMappings }}
- name: {{ include "witwave.gmVolumeName" (dict "agentName" $agentName "context" $backendName "dest" .dest) }}
  mountPath: {{ .dest }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Git-mapping emptyDir volumes for an agent — one per unique mapped destination.
Usage: {{- include "witwave.gitMappingVolumes" (dict "agent" . "release" $.Release.Name) | nindent 8 }}
*/}}
{{- define "witwave.gitMappingVolumes" -}}
{{- $agentName := .agent.name -}}
{{- $release := .release -}}
- name: {{ $release }}-git-sync-script
  configMap:
    name: {{ $release }}-git-sync-script
    defaultMode: 0755
{{- if .agent.gitMappings }}
- name: {{ $release }}-{{ $agentName }}-git-mappings
  configMap:
    name: {{ $release }}-{{ $agentName }}-git-mappings
{{- range .agent.gitMappings }}
- name: {{ include "witwave.gmVolumeName" (dict "agentName" $agentName "context" "agent" "dest" .dest) }}
  emptyDir: {}
{{- end }}
{{- end }}
{{- range .agent.backends }}
{{- if eq (include "witwave.enabled" .) "true" }}
{{- if .gitMappings }}
- name: {{ $release }}-{{ $agentName }}-{{ .name }}-git-mappings
  configMap:
    name: {{ $release }}-{{ $agentName }}-{{ .name }}-git-mappings
{{- $backendName := .name }}
{{- range .gitMappings }}
- name: {{ include "witwave.gmVolumeName" (dict "agentName" $agentName "context" $backendName "dest" .dest) }}
  emptyDir: {}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Resolve resources for the harness container (#553). Order of precedence:
  1. agent.resources (per-agent override in values.yaml)
  2. .Values.defaults.resources.harness (chart-shipped default)
Returns the empty string when neither is set, so the caller can branch on it.
Usage:
  {{- $res := include "witwave.harnessResources" (dict "agent" . "Values" $.Values) }}
  {{- if $res }}
  resources:
    {{- $res | nindent 12 }}
  {{- end }}
*/}}
{{- define "witwave.harnessResources" -}}
{{- $agent := .agent -}}
{{- $Values := .Values -}}
{{- if $agent.resources -}}
{{ toYaml $agent.resources }}
{{- else if and $Values.defaults $Values.defaults.resources $Values.defaults.resources.harness -}}
{{ toYaml $Values.defaults.resources.harness }}
{{- end -}}
{{- end }}

{{/*
Resolve resources for a backend sidecar (#553). Order of precedence:
  1. backend.resources (per-backend override in values.yaml)
  2. .Values.defaults.resources.backends[<backend-name>] (per-backend-type default,
     keyed by backend.name — e.g. "claude", "openai", "gemini")
  3. .Values.defaults.resources.backend (shared fallback for any unknown backend)
Returns the empty string when none of these are set.
Usage:
  {{- $res := include "witwave.backendResources" (dict "backend" . "Values" $.Values) }}
  {{- if $res }}
  resources:
    {{- $res | nindent 12 }}
  {{- end }}
*/}}
{{- define "witwave.backendResources" -}}
{{- $backend := .backend -}}
{{- $Values := .Values -}}
{{- $defaults := dict -}}
{{- if and $Values.defaults $Values.defaults.resources -}}{{- $defaults = $Values.defaults.resources -}}{{- end -}}
{{- if $backend.resources -}}
{{ toYaml $backend.resources }}
{{- else if and $defaults.backends (index $defaults.backends $backend.name) -}}
{{ toYaml (index $defaults.backends $backend.name) }}
{{- else if $defaults.backend -}}
{{ toYaml $defaults.backend }}
{{- end -}}
{{- end }}

{{/*
witwave.otelEnv — emit OTEL_* env list entries for a container when
observability.tracing is enabled (#634). Renders nothing when tracing is
disabled, so the caller can unconditionally include it.

This chart does NOT deploy a collector — the endpoint must be a
user-provided OTLP target (opentelemetry-operator-managed collector,
direct Jaeger/Tempo/cloud backend, etc.). Matches the idiomatic operator
pattern across Strimzi, cert-manager, Istio, Knative, Argo, Crossplane
and others.

Wiring:
  - OTEL_ENABLED              master toggle read by shared/otel.py +
                              operator/internal/tracing/otel.go
  - OTEL_EXPORTER_OTLP_ENDPOINT  read by the OTel SDK directly; only
                                 emitted when observability.tracing.endpoint
                                 is set
  - OTEL_TRACES_SAMPLER[_ARG] forwarded verbatim when set
  - OTEL_SERVICE_NAME         per-container service name; omitted when
                              the caller doesn't supply one (each
                              backend main.py already derives a sensible
                              default from AGENT_OWNER)

Usage:
  {{- include "witwave.otelEnv" (dict "root" $ "serviceName" (printf "harness-%s" .name)) | nindent 12 }}
*/}}
{{- define "witwave.otelEnv" -}}
{{- $root := .root -}}
{{- $serviceName := .serviceName -}}
{{- $tracing := (((($root.Values).observability)).tracing) -}}
{{- if and $tracing $tracing.enabled -}}
{{- $endpoint := $tracing.endpoint | default "" -}}
- name: OTEL_ENABLED
  value: "true"
{{- if $endpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ $endpoint | quote }}
{{- end }}
{{- if $tracing.sampler }}
- name: OTEL_TRACES_SAMPLER
  value: {{ $tracing.sampler | quote }}
{{- end }}
{{- if $tracing.samplerArg }}
- name: OTEL_TRACES_SAMPLER_ARG
  value: {{ $tracing.samplerArg | quote }}
{{- end }}
{{- if $serviceName }}
- name: OTEL_SERVICE_NAME
  value: {{ $serviceName | quote }}
{{- end }}
{{- end -}}
{{- end }}

{{/*
witwave.enabled — returns "true" or "false" for a scope's `enabled` field,
defaulting to "true" when the key is absent. Use via `eq (include
"witwave.enabled" .) "true"`. This exists because `default true .enabled`
returns "true" even when .enabled is literally false (sprig's `default`
treats the boolean false as an "empty" value). Added in beta.32 for the
per-agent and per-backend enabled flags.
*/}}
{{- define "witwave.enabled" -}}
{{- if hasKey . "enabled" -}}{{- .enabled -}}{{- else -}}true{{- end -}}
{{- end -}}

{{/*
witwave.resolveCredentials — unified dev-friendly / production-friendly
credentials resolver used by gitSync (GITSYNC_USERNAME / GITSYNC_PASSWORD)
and by each backend (CLAUDE_CODE_OAUTH_TOKEN, OPENAI_API_KEY, …).

Call site passes a dict with:
  .creds        — the per-entry credentials block (may be empty)
  .default      — the chart-global fallback credentials block (may be empty)
  .secretName   — the name the chart-rendered Secret should use in inline mode
                  (e.g. "bob-claude-credentials", "bob-witwave-gitsync-credentials")
  .context      — string used in error messages to identify the caller
                  (e.g. "agents[0].backends[0] (bob/claude)")

The helper fails render with {{- fail ... }} when:
  - inline values (username/token or secrets map) are populated without
    acknowledgeInsecureInline=true
  - nothing at all resolves (no existingSecret, no inline, no default)
    AND .required is true

Returns the NAME of the Secret the caller should envFrom (either the
existingSecret or the chart-rendered one). Empty string when no
credentials are needed. Callers use the return to wire envFrom.
*/}}
{{- define "witwave.resolveCredentials" -}}
{{- $creds := .creds | default dict -}}
{{- $def := .default | default dict -}}
{{- $ctx := .context | default "(unknown)" -}}
{{- $existing := "" -}}
{{- if $creds.existingSecret -}}
  {{- $existing = $creds.existingSecret -}}
{{- else if $def.existingSecret -}}
  {{- $existing = $def.existingSecret -}}
{{- end -}}
{{- $inlineUser := or $creds.username $def.username -}}
{{- $inlineTok  := or $creds.token $def.token -}}
{{- $inlineSecs := $creds.secrets | default $def.secrets | default dict -}}
{{- $hasInline  := or (or $inlineUser $inlineTok) (gt (len $inlineSecs) 0) -}}
{{- $ack := or $creds.acknowledgeInsecureInline $def.acknowledgeInsecureInline -}}
{{- if and $existing $hasInline -}}
  {{- /* existingSecret wins; inline is ignored. Emit a NOTES.txt-side warning via .Warnings could go here. */ -}}
{{- end -}}
{{- if $existing -}}
{{- $existing -}}
{{- else if $hasInline -}}
  {{- if not $ack -}}
    {{- fail (printf "charts/witwave: %s has inline credential values set but acknowledgeInsecureInline is false. Inline tokens land in helm release history, `helm get values`, and etcd. Set acknowledgeInsecureInline: true to confirm the risk (dev/smoke only) OR use existingSecret to reference a pre-created Secret (production)." $ctx) -}}
  {{- end -}}
{{- .secretName -}}
{{- else -}}
{{- /* no credentials resolved — empty return means "don't render envFrom" */ -}}
{{- end -}}
{{- end -}}

{{/*
witwave.inlineCredentialData — returns the stringData map for a chart-rendered
Secret in inline mode. Caller handles Secret metadata + passes result into
stringData:. Merges default + entry maps so either/or chart-global vs
per-entry works. gitSync credentials map into GITSYNC_USERNAME /
GITSYNC_PASSWORD keys; backend credentials map open-ended env-var names.
*/}}
{{- define "witwave.inlineCredentialData" -}}
{{- $creds := .creds | default dict -}}
{{- $def := .default | default dict -}}
{{- $kind := .kind -}} {{/* "gitsync" or "backend" */}}
{{- $user := or $creds.username $def.username -}}
{{- $tok := or $creds.token $def.token -}}
{{- $secs := $creds.secrets | default $def.secrets | default dict -}}
{{- $out := dict -}}
{{- if eq $kind "gitsync" -}}
  {{- if $user -}}{{- $_ := set $out "GITSYNC_USERNAME" $user -}}{{- end -}}
  {{- if $tok  -}}{{- $_ := set $out "GITSYNC_PASSWORD" $tok  -}}{{- end -}}
{{- else if eq $kind "backend" -}}
  {{- range $k, $v := $secs -}}
    {{- $_ := set $out $k $v -}}
  {{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/*
witwave.renderGitSyncEnvFrom — emits the envFrom block for a gitSync entry,
resolving credentials via witwave.resolveCredentials. Falls back to the
entry's legacy `envFrom:` list when no credentials/default resolve, so
deployments that haven't adopted the new shape keep working verbatim.

Caller passes:
  .gs        — the single gitSyncs[] entry
  .agent     — the enclosing agent dict (for name)
  .default   — the chart-global gitSync.credentials fallback
  .release   — $.Release.Name

Writes a full `envFrom:` block including the leading key + indentation
when there's anything to emit; nothing otherwise. Caller should NOT
wrap this in `if` — the helper decides internally.
*/}}
{{- define "witwave.renderGitSyncEnvFrom" -}}
{{- $gs := .gs -}}
{{- $agent := .agent -}}
{{- $def := .default | default dict -}}
{{- $creds := $gs.credentials | default dict -}}
{{- $ctx := printf "agent %q gitSync %q" $agent.name $gs.name -}}
{{- $secretName := printf "%s-%s-%s-gitsync-credentials" .release $agent.name $gs.name | trunc 253 -}}
{{- $resolved := include "witwave.resolveCredentials" (dict "creds" $creds "default" $def "secretName" $secretName "context" $ctx) -}}
{{- $legacy := $gs.envFrom -}}
{{- if $resolved }}
envFrom:
  - secretRef:
      name: {{ $resolved | quote }}
{{- else if $legacy }}
envFrom:
{{ toYaml $legacy | indent 2 }}
{{- end -}}
{{- end -}}

{{/*
witwave.renderBackendEnvFrom — same pattern for agents[].backends[] entries.
Inline "secrets:" map (vs gitSync's username/password) is the dev path.
*/}}
{{- define "witwave.renderBackendEnvFrom" -}}
{{- $b := .backend -}}
{{- $agent := .agent -}}
{{- $def := .default | default dict -}}
{{- $creds := $b.credentials | default dict -}}
{{- $ctx := printf "agent %q backend %q" $agent.name $b.name -}}
{{- $secretName := printf "%s-%s-%s-backend-credentials" .release $agent.name $b.name | trunc 253 -}}
{{- $resolved := include "witwave.resolveCredentials" (dict "creds" $creds "default" $def "secretName" $secretName "context" $ctx) -}}
{{- $legacy := $b.envFrom -}}
{{- if $resolved }}
envFrom:
  - secretRef:
      name: {{ $resolved | quote }}
{{- else if $legacy }}
envFrom:
{{ toYaml $legacy | indent 2 }}
{{- end -}}
{{- end -}}

{{/*
witwave.hasInlineCredentials — true when the creds-or-default combo has any
inline secret material set. Used by the credential-secret templates to
decide whether to render a Secret at all.
*/}}
{{- define "witwave.hasInlineCredentials" -}}
{{- $creds := .creds | default dict -}}
{{- $def := .default | default dict -}}
{{- $kind := .kind -}}
{{- if eq $kind "gitsync" -}}
  {{- if or (or $creds.username $def.username) (or $creds.token $def.token) -}}true{{- end -}}
{{- else if eq $kind "backend" -}}
  {{- $secs := $creds.secrets | default $def.secrets | default dict -}}
  {{- if gt (len $secs) 0 -}}true{{- end -}}
{{- end -}}
{{- end -}}
