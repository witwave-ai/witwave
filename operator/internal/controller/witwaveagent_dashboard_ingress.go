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

// dashboard_ingress.go implements the operator counterpart to the chart's
// `dashboard.ingress` block (#831, #1741). The fail-closed posture is
// modelled end-to-end:
//
//   1. Schema layer (DashboardAuthSpec.Mode enum): "basic" | "none". Any
//      other value is rejected by the apiserver before it reaches the
//      controller.
//
//   2. authReconcile flag wiring:
//        - Ingress == nil OR Ingress.Enabled == false  -> no-op (delete
//          previously-owned Ingress)
//        - Ingress.Auth == nil OR Ingress.Auth.Mode == "" -> SKIP render,
//          emit a Warning event "DashboardIngressAuthRequired" so the
//          user sees why the Ingress didn't materialise. This is the
//          direct analogue of the chart's fail-render (#528).
//        - Ingress.Auth.Mode == "none" -> render unauthenticated Ingress,
//          emit a Warning event "DashboardIngressUnauthenticated" on
//          every transition so the opt-out stays visible in kubectl
//          describe / dashboards.
//        - Ingress.Auth.Mode == "basic" -> require BasicAuthSecretName to
//          reference an existing basic-auth Secret in the same namespace
//          and stamp the nginx.ingress.kubernetes.io/auth-* annotations.
//          When BasicAuthSecretName is empty we fail-closed (no Secret is
//          synthesised — the chart's htpasswd defaulting requires a
//          plaintext value the operator does not own; users carry that
//          responsibility via existingSecret-style reference).

import (
	"context"
	"encoding/json"
	"fmt"

	networkingv1 "k8s.io/api/networking/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

// dashboardIngressLastEventStateAnnotation tracks the last
// DashboardIngressAuthStatus for which the reconciler emitted an Event
// (#1180). Only transitions emit new Events, so steady-state
// `auth.mode=none` no longer floods the namespace's Event stream every
// reconcile pass.
const dashboardIngressLastEventStateAnnotation = "witwave.ai/dashboard-ingress-last-event-state"

// DashboardIngressAuthStatus describes the fail-closed decision the
// reconciler made this pass. Exported so downstream status-writers (and
// tests) can assert on the distinct states without re-parsing log output.
type DashboardIngressAuthStatus string

const (
	// DashboardIngressAuthStatusDisabled — spec.dashboard is unset, or
	// spec.dashboard.ingress is unset/disabled. The reconciler renders
	// nothing and cleans up any previously-owned Ingress.
	DashboardIngressAuthStatusDisabled DashboardIngressAuthStatus = "disabled"
	// DashboardIngressAuthStatusMissingAuth — Ingress is enabled but
	// spec.dashboard.ingress.auth.mode is empty, or set to an unrecognised
	// value. Fail-closed: the reconciler refuses to render the Ingress and
	// logs each reconcile, emitting an Event only on state transition (#1180).
	DashboardIngressAuthStatusMissingAuth DashboardIngressAuthStatus = "missing-auth"
	// DashboardIngressAuthStatusUnauthenticated — auth.mode="none".
	// Operator explicitly opted out of dashboard auth; the Ingress is
	// rendered without authentication annotations.
	DashboardIngressAuthStatusUnauthenticated DashboardIngressAuthStatus = "unauthenticated"
	// DashboardIngressAuthStatusBasic — auth.mode="basic". The Ingress is
	// rendered with HTTP basic-auth annotations referencing the operator-
	// supplied secret.
	DashboardIngressAuthStatusBasic DashboardIngressAuthStatus = "basic"
)

// EvaluateDashboardIngressAuth returns the fail-closed decision for a
// WitwaveAgent's dashboard ingress configuration. Extracted as a pure helper
// so unit tests can cover every branch without standing up a fake client.
func EvaluateDashboardIngressAuth(agent *witwavev1alpha1.WitwaveAgent) DashboardIngressAuthStatus {
	if agent == nil || agent.Spec.Dashboard == nil {
		return DashboardIngressAuthStatusDisabled
	}
	ing := agent.Spec.Dashboard.Ingress
	if ing == nil || !ing.Enabled {
		return DashboardIngressAuthStatusDisabled
	}
	if ing.Auth == nil || ing.Auth.Mode == "" {
		return DashboardIngressAuthStatusMissingAuth
	}
	switch ing.Auth.Mode {
	case "none":
		return DashboardIngressAuthStatusUnauthenticated
	case "basic":
		return DashboardIngressAuthStatusBasic
	}
	// Any unknown Mode should have been rejected by the CRD enum
	// validation — treat as fail-closed to be safe.
	return DashboardIngressAuthStatusMissingAuth
}

// reconcileDashboardIngress is the entrypoint wired into
// WitwaveAgentReconciler.Reconcile. It walks the fail-closed gate, deletes
// any previously-owned Ingress when the gate denies render, and
// applies a chart-equivalent networking.k8s.io/v1 Ingress when the gate
// allows.
func (r *WitwaveAgentReconciler) reconcileDashboardIngress(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent) error {
	log := logf.FromContext(ctx).WithValues("agent", agent.Name, "namespace", agent.Namespace)
	state := EvaluateDashboardIngressAuth(agent)
	switch state {
	case DashboardIngressAuthStatusDisabled:
		// No-op render path. Clean up any previously-owned Ingress so a
		// disable flip converges (mirrors HPA/NetworkPolicy patterns).
		if err := r.deleteOwnedDashboardIngress(ctx, agent); err != nil {
			return err
		}
		return r.updateDashboardIngressLastEventState(ctx, agent, string(state))
	case DashboardIngressAuthStatusMissingAuth:
		// Fail-closed mirror of chart #528. Log on every reconcile,
		// but only emit an Event on a state transition (#1180) so a
		// steady-state missing-auth configuration doesn't flood the
		// namespace Event stream.
		log.Info("skipping dashboard Ingress render: spec.dashboard.ingress.auth.mode must be set (fail-closed)",
			"component", "dashboard-ingress")
		if r.shouldEmitDashboardIngressEvent(agent, state) && r.Recorder != nil {
			r.Recorder.Event(agent, "Warning", "DashboardIngressAuthRequired",
				"Dashboard ingress skipped: set spec.dashboard.ingress.auth.mode to 'basic' or 'none' to proceed (fail-closed, see #528/#831)")
		}
		// Clean up any previously-owned Ingress so flipping auth.mode
		// from "none"/"basic" back to empty converges to "no Ingress".
		if err := r.deleteOwnedDashboardIngress(ctx, agent); err != nil {
			return err
		}
		return r.updateDashboardIngressLastEventState(ctx, agent, string(state))
	case DashboardIngressAuthStatusUnauthenticated:
		log.Info("rendering dashboard Ingress WITHOUT auth (spec.dashboard.ingress.auth.mode=none)",
			"component", "dashboard-ingress")
		if r.shouldEmitDashboardIngressEvent(agent, state) && r.Recorder != nil {
			r.Recorder.Event(agent, "Warning", "DashboardIngressUnauthenticated",
				"Dashboard ingress rendered without auth (auth.mode=none) — conversation history is reachable via the Ingress host")
		}
		if err := r.applyDashboardIngress(ctx, agent); err != nil {
			return err
		}
		return r.updateDashboardIngressLastEventState(ctx, agent, string(state))
	case DashboardIngressAuthStatusBasic:
		// Basic-auth requires the user to reference an existing Secret;
		// the operator does not synthesise htpasswd content because
		// that would force it to own a plaintext password it has no
		// authority over. Fail-closed when BasicAuthSecretName is empty.
		ingSpec := agent.Spec.Dashboard.Ingress
		if ingSpec.Auth == nil || ingSpec.Auth.BasicAuthSecretName == "" {
			log.Info("skipping dashboard Ingress render: spec.dashboard.ingress.auth.basicAuthSecretName must be set (fail-closed)",
				"component", "dashboard-ingress")
			if r.shouldEmitDashboardIngressEvent(agent, state) && r.Recorder != nil {
				r.Recorder.Event(agent, "Warning", "DashboardIngressBasicAuthSecretRequired",
					"Dashboard ingress skipped: set spec.dashboard.ingress.auth.basicAuthSecretName to reference an existing basic-auth Secret containing an htpasswd-style 'auth' key (fail-closed, see #528/#1741)")
			}
			if err := r.deleteOwnedDashboardIngress(ctx, agent); err != nil {
				return err
			}
			return r.updateDashboardIngressLastEventState(ctx, agent, string(state))
		}
		log.Info("rendering dashboard Ingress with basic-auth",
			"component", "dashboard-ingress",
			"basicAuthSecretName", ingSpec.Auth.BasicAuthSecretName)
		if err := r.applyDashboardIngress(ctx, agent); err != nil {
			return err
		}
		return r.updateDashboardIngressLastEventState(ctx, agent, string(state))
	}
	return nil
}

// applyDashboardIngress renders and applies the per-agent Ingress.
// Caller must have already validated state via EvaluateDashboardIngressAuth.
func (r *WitwaveAgentReconciler) applyDashboardIngress(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent) error {
	desired := buildDashboardIngress(agent)
	if desired == nil {
		// Defensive — shouldn't happen given the caller-side gate.
		return nil
	}
	if err := controllerutil.SetControllerReference(agent, desired, r.Scheme); err != nil {
		return fmt.Errorf("set owner on dashboard Ingress: %w", err)
	}
	return applySSA(ctx, r.Client, desired)
}

// deleteOwnedDashboardIngress removes a previously-applied dashboard
// Ingress when the spec no longer permits render. IsControlledBy gates
// the Delete so we never touch an Ingress hand-authored under our name.
func (r *WitwaveAgentReconciler) deleteOwnedDashboardIngress(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent) error {
	existing := &networkingv1.Ingress{}
	key := client.ObjectKey{Namespace: agent.Namespace, Name: agent.Name + "-dashboard"}
	if err := r.Get(ctx, key, existing); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return fmt.Errorf("get dashboard Ingress for cleanup: %w", err)
	}
	if !metav1.IsControlledBy(existing, agent) {
		return nil
	}
	if err := r.Delete(ctx, existing); err != nil && !apierrors.IsNotFound(err) {
		return fmt.Errorf("delete dashboard Ingress: %w", err)
	}
	return nil
}

// buildDashboardIngress renders the networking.k8s.io/v1 Ingress for the
// per-agent dashboard, mirroring charts/witwave/templates/ingress.yaml.
// Returns nil when the spec doesn't request render. Extracted so unit
// tests can assert annotation/path/host shape without a fake client.
func buildDashboardIngress(agent *witwavev1alpha1.WitwaveAgent) *networkingv1.Ingress {
	if agent == nil || agent.Spec.Dashboard == nil || !agent.Spec.Dashboard.Enabled {
		return nil
	}
	ing := agent.Spec.Dashboard.Ingress
	if ing == nil || !ing.Enabled {
		return nil
	}
	if ing.Auth == nil || ing.Auth.Mode == "" {
		return nil
	}
	if ing.Auth.Mode == "basic" && ing.Auth.BasicAuthSecretName == "" {
		return nil
	}

	port := agent.Spec.Dashboard.Port
	if port == 0 {
		port = 80
	}

	annotations := map[string]string{}
	if ing.Auth.Mode == "basic" {
		annotations["nginx.ingress.kubernetes.io/auth-type"] = "basic"
		annotations["nginx.ingress.kubernetes.io/auth-secret"] = ing.Auth.BasicAuthSecretName
		annotations["nginx.ingress.kubernetes.io/auth-realm"] = "witwave dashboard"
	}

	pathType := networkingv1.PathTypePrefix
	rules := []networkingv1.IngressRule{{
		Host: ing.Host,
		IngressRuleValue: networkingv1.IngressRuleValue{
			HTTP: &networkingv1.HTTPIngressRuleValue{
				Paths: []networkingv1.HTTPIngressPath{{
					Path:     "/",
					PathType: &pathType,
					Backend: networkingv1.IngressBackend{
						Service: &networkingv1.IngressServiceBackend{
							Name: agent.Name + "-dashboard",
							Port: networkingv1.ServiceBackendPort{Number: port},
						},
					},
				}},
			},
		},
	}}

	spec := networkingv1.IngressSpec{
		IngressClassName: ing.ClassName,
		Rules:            rules,
	}
	if ing.TLS != nil && ing.TLS.SecretName != "" {
		spec.TLS = []networkingv1.IngressTLS{{
			SecretName: ing.TLS.SecretName,
			Hosts:      []string{ing.Host},
		}}
	}

	out := &networkingv1.Ingress{
		// #1565: stamp TypeMeta explicitly so applySSA does not depend
		// on scheme lookup to infer the GVK.
		TypeMeta: metav1.TypeMeta{
			APIVersion: "networking.k8s.io/v1",
			Kind:       "Ingress",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-dashboard",
			Namespace: agent.Namespace,
			Labels:    dashboardLabels(agent),
		},
		Spec: spec,
	}
	if len(annotations) > 0 {
		out.Annotations = annotations
	}
	return out
}

// shouldEmitDashboardIngressEvent reports whether the current
// DashboardIngressAuthStatus differs from the last value we stamped on
// the agent's annotations. Callers gate their `Recorder.Event` calls
// on this so reconciler-churn doesn't spam duplicate Events.
func (r *WitwaveAgentReconciler) shouldEmitDashboardIngressEvent(agent *witwavev1alpha1.WitwaveAgent, state DashboardIngressAuthStatus) bool {
	prev := ""
	if agent.Annotations != nil {
		prev = agent.Annotations[dashboardIngressLastEventStateAnnotation]
	}
	return prev != string(state)
}

// updateDashboardIngressLastEventState writes the current state value
// into the annotation via an SSA-safe JSON-merge patch (#1180). The
// patch is scoped to metadata.annotations so it never clobbers
// concurrent writes to unrelated fields. Idempotent: if the annotation
// already holds `value` the patch is a no-op skip.
func (r *WitwaveAgentReconciler) updateDashboardIngressLastEventState(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, value string) error {
	if agent.Annotations != nil && agent.Annotations[dashboardIngressLastEventStateAnnotation] == value {
		return nil
	}
	patch := map[string]interface{}{
		"metadata": map[string]interface{}{
			"annotations": map[string]string{
				dashboardIngressLastEventStateAnnotation: value,
			},
		},
	}
	raw, err := json.Marshal(patch)
	if err != nil {
		return fmt.Errorf("marshal dashboard-ingress-last-event-state patch: %w", err)
	}
	if err := r.Patch(ctx, agent, client.RawPatch(types.MergePatchType, raw)); err != nil {
		return fmt.Errorf("patch dashboard-ingress-last-event-state annotation: %w", err)
	}
	// Mirror the write back into the in-memory copy so downstream
	// reconcile steps see the same annotation value we just landed.
	if agent.Annotations == nil {
		agent.Annotations = map[string]string{}
	}
	agent.Annotations[dashboardIngressLastEventStateAnnotation] = value
	return nil
}
