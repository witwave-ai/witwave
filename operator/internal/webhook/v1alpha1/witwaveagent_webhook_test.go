/*
Copyright 2025.

Licensed under the Apache License, Version 2.0 (the "License");
...
*/

package v1alpha1

import (
	"context"
	"strings"
	"testing"

	authv1 "k8s.io/api/authorization/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

func TestDefaulterPopulatesPortWhenZero(t *testing.T) {
	d := &WitwaveAgentCustomDefaulter{}
	agent := &witwavev1alpha1.WitwaveAgent{}
	if err := d.Default(context.Background(), agent); err != nil {
		t.Fatalf("Default returned err: %v", err)
	}
	if agent.Spec.Port != 8000 {
		t.Fatalf("expected default port 8000, got %d", agent.Spec.Port)
	}
}

func TestDefaulterPreservesExplicitPort(t *testing.T) {
	d := &WitwaveAgentCustomDefaulter{}
	agent := &witwavev1alpha1.WitwaveAgent{Spec: witwavev1alpha1.WitwaveAgentSpec{Port: 9000}}
	if err := d.Default(context.Background(), agent); err != nil {
		t.Fatalf("Default returned err: %v", err)
	}
	if agent.Spec.Port != 9000 {
		t.Fatalf("expected preserved port 9000, got %d", agent.Spec.Port)
	}
}

func TestValidatorRejectsDuplicateBackendNames(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{
				{Name: "claude"},
				{Name: "openai"},
				{Name: "claude"}, // duplicate
			},
		},
	}
	_, err := v.ValidateCreate(context.Background(), agent)
	if err == nil {
		t.Fatal("expected error for duplicate backend name, got nil")
	}
	if !strings.Contains(err.Error(), "duplicates") {
		t.Fatalf("expected error to mention duplicates, got: %v", err)
	}
}

func TestValidatorAllowsUniqueBackendNames(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{
				{Name: "claude"},
				{Name: "openai"},
				{Name: "gemini"},
			},
		},
	}
	if _, err := v.ValidateCreate(context.Background(), agent); err != nil {
		t.Fatalf("ValidateCreate returned err: %v", err)
	}
	if _, err := v.ValidateUpdate(context.Background(), nil, agent); err != nil {
		t.Fatalf("ValidateUpdate returned err: %v", err)
	}
}

// TestValidatorWarnsOnInlineCredentialsRBAC asserts the webhook returns
// (but does not error on) an admission warning when a backend or gitSync
// opts in to inline credentials via AcknowledgeInsecureInline=true. The
// warning tells the operator to verify the chart was installed with
// rbac.secretsWrite=true, since the webhook can't introspect its own RBAC.
// See #1623, #1613.
func TestValidatorWarnsOnInlineCredentialsRBAC(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{
				{
					Name: "claude",
					Credentials: &witwavev1alpha1.BackendCredentialsSpec{
						Secrets:                   map[string]string{"CLAUDE_CODE_OAUTH_TOKEN": "sk-x"},
						AcknowledgeInsecureInline: true,
					},
				},
			},
			GitSyncs: []witwavev1alpha1.GitSyncSpec{
				{
					Name: "config",
					Repo: "https://example.com/x.git",
					Credentials: &witwavev1alpha1.GitSyncCredentialsSpec{
						Username:                  "alice",
						Token:                     "ghp_xxx",
						AcknowledgeInsecureInline: true,
					},
				},
			},
		},
	}
	warnings, err := v.ValidateCreate(context.Background(), agent)
	if err != nil {
		t.Fatalf("ValidateCreate returned err: %v", err)
	}
	if len(warnings) != 2 {
		t.Fatalf("expected 2 warnings (one per inline-creds entry), got %d: %v", len(warnings), warnings)
	}
	joined := strings.Join(warnings, "\n")
	if !strings.Contains(joined, "rbac.secretsWrite=true") {
		t.Fatalf("expected warning to mention rbac.secretsWrite=true, got: %v", warnings)
	}
	if !strings.Contains(joined, "spec.backends[0].credentials") {
		t.Fatalf("expected warning to point at spec.backends[0].credentials, got: %v", warnings)
	}
	if !strings.Contains(joined, "spec.gitSyncs[0].credentials") {
		t.Fatalf("expected warning to point at spec.gitSyncs[0].credentials, got: %v", warnings)
	}

	// ValidateUpdate path emits the same warnings.
	updateWarnings, err := v.ValidateUpdate(context.Background(), nil, agent)
	if err != nil {
		t.Fatalf("ValidateUpdate returned err: %v", err)
	}
	if len(updateWarnings) != 2 {
		t.Fatalf("ValidateUpdate: expected 2 warnings, got %d: %v", len(updateWarnings), updateWarnings)
	}
}

// TestValidatorNoWarningWhenExistingSecret asserts that credentials
// blocks using ExistingSecret (the production path) emit no warnings —
// the operator only needs Secret read verbs in that case, which is the
// default RBAC posture.
func TestValidatorNoWarningWhenExistingSecret(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{
				{
					Name: "claude",
					Credentials: &witwavev1alpha1.BackendCredentialsSpec{
						ExistingSecret: "claude-creds",
					},
				},
			},
		},
	}
	warnings, err := v.ValidateCreate(context.Background(), agent)
	if err != nil {
		t.Fatalf("ValidateCreate returned err: %v", err)
	}
	if len(warnings) != 0 {
		t.Fatalf("expected 0 warnings for existingSecret path, got %d: %v", len(warnings), warnings)
	}
}

// ----- validateLiveCredentials (#1683, #1685) -----

// fakeClientHelper builds a controller-runtime fake client with the
// witwave + corev1 + authv1 schemes wired up so the validator's Get +
// SSAR Create calls land on a real-looking client.
func fakeClientHelper(t *testing.T, ssarAllowed bool, objs ...client.Object) client.Client {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := witwavev1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("add witwave scheme: %v", err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("add corev1 scheme: %v", err)
	}
	if err := authv1.AddToScheme(scheme); err != nil {
		t.Fatalf("add authv1 scheme: %v", err)
	}
	return fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithInterceptorFuncs(interceptor.Funcs{
			// Fake client doesn't run SubjectAccessReview through the
			// authorizer — intercept Create calls on SSARs and stamp
			// Status.Allowed per the test parameter.
			Create: func(ctx context.Context, c client.WithWatch, obj client.Object, opts ...client.CreateOption) error {
				if ssar, ok := obj.(*authv1.SelfSubjectAccessReview); ok {
					ssar.Status = authv1.SubjectAccessReviewStatus{Allowed: ssarAllowed}
					return nil
				}
				return c.Create(ctx, obj, opts...)
			},
		}).
		Build()
}

func TestValidatorAllowsExistingSecretPresent(t *testing.T) {
	c := fakeClientHelper(t, true,
		&corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "git-creds"},
		},
	)
	v := &WitwaveAgentCustomValidator{Client: c}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{{Name: "claude"}},
			GitSyncs: []witwavev1alpha1.GitSyncSpec{
				{
					Name:        "config",
					Repo:        "https://example.com/x.git",
					Credentials: &witwavev1alpha1.GitSyncCredentialsSpec{ExistingSecret: "git-creds"},
				},
			},
		},
	}
	if _, err := v.ValidateCreate(context.Background(), agent); err != nil {
		t.Fatalf("ValidateCreate returned err: %v", err)
	}
}

func TestValidatorRejectsMissingExistingSecret(t *testing.T) {
	c := fakeClientHelper(t, true) // no Secrets in store
	v := &WitwaveAgentCustomValidator{Client: c}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{{
				Name:        "claude",
				Credentials: &witwavev1alpha1.BackendCredentialsSpec{ExistingSecret: "missing-secret"},
			}},
		},
	}
	_, err := v.ValidateCreate(context.Background(), agent)
	if err == nil {
		t.Fatal("expected error for missing existingSecret, got nil")
	}
	if !strings.Contains(err.Error(), "missing-secret") {
		t.Fatalf("expected error to name the missing secret, got: %v", err)
	}
	if !strings.Contains(err.Error(), "#1683") {
		t.Fatalf("expected error to reference #1683, got: %v", err)
	}
}

func TestValidatorRejectsInlineCredentialsWhenSSARDenied(t *testing.T) {
	c := fakeClientHelper(t, false) // SSAR denied
	v := &WitwaveAgentCustomValidator{Client: c}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{{
				Name: "claude",
				Credentials: &witwavev1alpha1.BackendCredentialsSpec{
					Secrets:                   map[string]string{"CLAUDE_CODE_OAUTH_TOKEN": "sk-x"},
					AcknowledgeInsecureInline: true,
				},
			}},
		},
	}
	_, err := v.ValidateCreate(context.Background(), agent)
	if err == nil {
		t.Fatal("expected error when SSAR denies secret create, got nil")
	}
	if !strings.Contains(err.Error(), "#1685") {
		t.Fatalf("expected error to reference #1685, got: %v", err)
	}
	if !strings.Contains(err.Error(), "rbac.secretsWrite") {
		t.Fatalf("expected error to mention rbac.secretsWrite, got: %v", err)
	}
}

func TestValidatorAllowsInlineCredentialsWhenSSARAllowed(t *testing.T) {
	c := fakeClientHelper(t, true) // SSAR allowed
	v := &WitwaveAgentCustomValidator{Client: c}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{{
				Name: "claude",
				Credentials: &witwavev1alpha1.BackendCredentialsSpec{
					Secrets:                   map[string]string{"CLAUDE_CODE_OAUTH_TOKEN": "sk-x"},
					AcknowledgeInsecureInline: true,
				},
			}},
		},
	}
	warnings, err := v.ValidateCreate(context.Background(), agent)
	if err != nil {
		t.Fatalf("ValidateCreate returned err: %v", err)
	}
	if len(warnings) == 0 {
		t.Fatal("expected at least one inline-credentials warning, got none")
	}
}

func TestValidatorNilClientIsBackwardsCompatible(t *testing.T) {
	v := &WitwaveAgentCustomValidator{} // nil Client
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Backends: []witwavev1alpha1.BackendSpec{{
				Name:        "claude",
				Credentials: &witwavev1alpha1.BackendCredentialsSpec{ExistingSecret: "would-be-checked"},
			}},
		},
	}
	if _, err := v.ValidateCreate(context.Background(), agent); err != nil {
		t.Fatalf("nil-client validator must short-circuit; got: %v", err)
	}
}

// ----- CORS validation (#1748) ----------------------------------------

func TestValidatorRejectsWildcardCorsWithoutAck(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Cors: &witwavev1alpha1.CorsSpec{
				AllowOrigins: []string{"*"},
			},
		},
	}
	_, err := v.ValidateCreate(context.Background(), agent)
	if err == nil {
		t.Fatal("expected error for wildcard origin without allowWildcard ack")
	}
	if !strings.Contains(err.Error(), "allowWildcard") {
		t.Errorf("expected error to mention allowWildcard, got: %v", err)
	}
}

func TestValidatorAllowsWildcardCorsWithAck(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Cors: &witwavev1alpha1.CorsSpec{
				AllowOrigins:  []string{"*"},
				AllowWildcard: true,
			},
		},
	}
	if _, err := v.ValidateCreate(context.Background(), agent); err != nil {
		t.Errorf("expected no error with allowWildcard=true, got: %v", err)
	}
}

func TestValidatorAllowsConcreteCorsOrigins(t *testing.T) {
	v := &WitwaveAgentCustomValidator{}
	agent := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "iris"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			Cors: &witwavev1alpha1.CorsSpec{
				AllowOrigins: []string{"https://a.example", "https://b.example"},
			},
		},
	}
	if _, err := v.ValidateCreate(context.Background(), agent); err != nil {
		t.Errorf("expected no error for concrete origins, got: %v", err)
	}
}
