package agent

import (
	"context"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ---------------------------------------------------------------------------
// ParseBackendAuth — flag surface
// ---------------------------------------------------------------------------

func TestParseBackendAuth_ThreeFlagsComposed(t *testing.T) {
	t.Parallel()
	got, err := ParseBackendAuth(
		[]string{"claude=oauth"},
		[]string{"openai=OPENAI_API_KEY"},
		[]string{"gemini=my-gemini-secret"},
		nil,
	)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("expected 3 resolvers, got %d", len(got))
	}
	if got[0].Backend != "claude" || got[0].Mode != BackendAuthProfile || got[0].Profile != "oauth" {
		t.Errorf("claude resolver = %+v; want profile=oauth", got[0])
	}
	if got[1].Backend != "openai" || got[1].Mode != BackendAuthFromEnv || got[1].EnvVars[0] != "OPENAI_API_KEY" {
		t.Errorf("openai resolver = %+v; want from-env OPENAI_API_KEY", got[1])
	}
	if got[2].Backend != "gemini" || got[2].Mode != BackendAuthExistingSecret || got[2].ExistingSecret != "my-gemini-secret" {
		t.Errorf("gemini resolver = %+v; want existing-secret my-gemini-secret", got[2])
	}
}

func TestParseBackendAuth_RejectsDoubleClaim(t *testing.T) {
	t.Parallel()
	_, err := ParseBackendAuth(
		[]string{"claude=oauth"},
		[]string{"claude=ANTHROPIC_API_KEY"},
		nil,
		nil,
	)
	if err == nil {
		t.Fatal("expected an error when the same backend appears across flags")
	}
	if !strings.Contains(err.Error(), "already has credentials from") {
		t.Errorf("error = %q; want double-claim message", err)
	}
}

func TestParseBackendAuth_RejectsBadShape(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		args [4][]string
		want string
	}{
		{"missing equals", [4][]string{{"claude-oauth"}, nil, nil, nil}, "<backend>=<value>"},
		{"empty backend", [4][]string{{"=oauth"}, nil, nil, nil}, "backend name is empty"},
		{"empty value", [4][]string{{"claude="}, nil, nil, nil}, "value is empty"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := ParseBackendAuth(tc.args[0], tc.args[1], tc.args[2], tc.args[3])
			if err == nil {
				t.Fatalf("expected an error, got nil")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error = %q; want substring %q", err, tc.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// --auth-set parser + resolver coverage
// ---------------------------------------------------------------------------

func TestParseBackendAuth_AuthSet_AccumulatesPerBackend(t *testing.T) {
	t.Parallel()
	got, err := ParseBackendAuth(
		nil, nil, nil,
		[]string{
			"claude:ANTHROPIC_API_KEY=sk-ant-xxx",
			"claude:ALT_TOKEN=ghp_yyy",
			"openai:OPENAI_API_KEY=sk-xxx",
		},
	)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Two resolvers — one per backend — each carrying the inline map.
	if len(got) != 2 {
		t.Fatalf("expected 2 resolvers, got %d", len(got))
	}
	byBackend := make(map[string]BackendAuthResolver)
	for _, r := range got {
		byBackend[r.Backend] = r
	}
	if r, ok := byBackend["claude"]; !ok ||
		r.Mode != BackendAuthInline ||
		r.Inline["ANTHROPIC_API_KEY"] != "sk-ant-xxx" ||
		r.Inline["ALT_TOKEN"] != "ghp_yyy" {
		t.Errorf("claude resolver = %+v; want inline with both keys", r)
	}
	if r, ok := byBackend["openai"]; !ok ||
		r.Mode != BackendAuthInline ||
		r.Inline["OPENAI_API_KEY"] != "sk-xxx" {
		t.Errorf("openai resolver = %+v; want inline OPENAI_API_KEY", r)
	}
}

func TestParseBackendAuth_AuthSet_RejectsDupKey(t *testing.T) {
	t.Parallel()
	_, err := ParseBackendAuth(nil, nil, nil, []string{
		"claude:KEY=first",
		"claude:KEY=second",
	})
	if err == nil {
		t.Fatal("expected an error for duplicate KEY in same backend")
	}
	if !strings.Contains(err.Error(), "given twice") {
		t.Errorf("error = %q; want 'given twice' substring", err)
	}
}

func TestParseBackendAuth_AuthSet_ConflictsWithOtherFlags(t *testing.T) {
	t.Parallel()
	_, err := ParseBackendAuth(
		[]string{"claude=oauth"},
		nil, nil,
		[]string{"claude:KEY=value"},
	)
	if err == nil {
		t.Fatal("expected an error when --auth and --auth-set both target the same backend")
	}
	if !strings.Contains(err.Error(), "already has credentials") {
		t.Errorf("error = %q; want double-claim message", err)
	}
}

func TestParseBackendAuth_AuthSet_BadShape(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		raw  string
		want string
	}{
		{"missing colon", "claudeKEY=value", "<backend>:<KEY>=<VALUE>"},
		{"empty backend", ":KEY=value", "backend name is empty"},
		{"missing equals", "claude:KEYvalue", "KEY=VALUE form"},
		{"empty key", "claude:=value", "KEY is empty"},
		{"empty value", "claude:KEY=", "VALUE is empty"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := ParseBackendAuth(nil, nil, nil, []string{tc.raw})
			if err == nil {
				t.Fatalf("expected an error, got nil")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error = %q; want substring %q", err, tc.want)
			}
		})
	}
}

func TestResolve_Inline_MintsSecretWithKVPairs(t *testing.T) {
	k8sClient := makeFakeK8s()
	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthInline,
		Inline: map[string]string{
			"ANTHROPIC_API_KEY": "sk-ant-abc123",
			"ALT_TOKEN":         "ghp_xyz",
		},
	}
	secretName, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if secretName != "iris-claude" {
		t.Errorf("secretName = %q; want iris-claude", secretName)
	}
	sec, err := k8sClient.CoreV1().Secrets("witwave").Get(
		context.Background(), "iris-claude", metav1.GetOptions{},
	)
	if err != nil {
		t.Fatalf("Secret not minted: %v", err)
	}
	if sec.StringData["ANTHROPIC_API_KEY"] != "sk-ant-abc123" {
		t.Error("ANTHROPIC_API_KEY value didn't land")
	}
	if sec.StringData["ALT_TOKEN"] != "ghp_xyz" {
		t.Error("ALT_TOKEN value didn't land")
	}
	// Annotation must NOT echo values (only key names) — values
	// would leak into `kubectl get secret -o yaml` metadata.
	createdBy, _ := sec.Annotations["witwave.ai/created-by"]
	if strings.Contains(createdBy, "sk-ant-abc123") || strings.Contains(createdBy, "ghp_xyz") {
		t.Errorf("created-by annotation leaks values: %q", createdBy)
	}
}

func TestResolve_Inline_RejectsEmptyValue(t *testing.T) {
	k8sClient := makeFakeK8s()
	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthInline,
		Inline:  map[string]string{"KEY": ""},
	}
	_, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err == nil {
		t.Fatal("expected error for empty value")
	}
	if !strings.Contains(err.Error(), "empty value") {
		t.Errorf("error = %q; want 'empty value' substring", err)
	}
}

// ---------------------------------------------------------------------------
// resolve() — the three modes
// ---------------------------------------------------------------------------

func TestResolve_Profile_MintsSecretWithConventionalKey(t *testing.T) {
	// Can't t.Parallel — t.Setenv mutates process env.
	t.Setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth-fake-123")
	k8sClient := makeFakeK8s()

	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthProfile,
		Profile: "oauth",
	}
	secretName, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if secretName != "iris-claude" {
		t.Errorf("secret name = %q; want iris-claude", secretName)
	}

	sec, err := k8sClient.CoreV1().Secrets("witwave").Get(context.Background(), "iris-claude", metav1.GetOptions{})
	if err != nil {
		t.Fatalf("Secret wasn't minted: %v", err)
	}
	if sec.Labels[LabelManagedBy] != LabelManagedByWW {
		t.Errorf("managed-by label = %q; want %q", sec.Labels[LabelManagedBy], LabelManagedByWW)
	}
	if sec.Labels["witwave.ai/credential-type"] != "backend" {
		t.Errorf("credential-type label = %q; want 'backend'", sec.Labels["witwave.ai/credential-type"])
	}
	if sec.StringData["CLAUDE_CODE_OAUTH_TOKEN"] != "sk-oauth-fake-123" {
		t.Error("Secret key CLAUDE_CODE_OAUTH_TOKEN not set to env value")
	}
}

func TestResolve_Profile_ApiKey_ReadsAnthropicVar(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "sk-api-key-fake-456")
	k8sClient := makeFakeK8s()

	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthProfile,
		Profile: "api-key",
	}
	_, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	sec, _ := k8sClient.CoreV1().Secrets("witwave").Get(context.Background(), "iris-claude", metav1.GetOptions{})
	if sec.StringData["ANTHROPIC_API_KEY"] != "sk-api-key-fake-456" {
		t.Error("Secret key ANTHROPIC_API_KEY not set to env value")
	}
	// Should NOT carry CLAUDE_CODE_OAUTH_TOKEN — different profile.
	if _, ok := sec.StringData["CLAUDE_CODE_OAUTH_TOKEN"]; ok {
		t.Error("api-key profile should NOT include CLAUDE_CODE_OAUTH_TOKEN key")
	}
}

func TestResolve_Profile_UnknownProfile(t *testing.T) {
	k8sClient := makeFakeK8s()
	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthProfile,
		Profile: "bogus",
	}
	_, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err == nil {
		t.Fatal("expected an error for unknown profile")
	}
	if !strings.Contains(err.Error(), "unknown profile") {
		t.Errorf("error = %q; want 'unknown profile' substring", err)
	}
}

func TestResolve_Profile_MissingEnvVar_ReturnsActionableError(t *testing.T) {
	// Deliberately don't set the env var.
	t.Setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
	k8sClient := makeFakeK8s()
	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthProfile,
		Profile: "oauth",
	}
	_, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err == nil {
		t.Fatal("expected an error when the env var is unset")
	}
	if !strings.Contains(err.Error(), "CLAUDE_CODE_OAUTH_TOKEN") {
		t.Errorf("error = %q; want env var name in message", err)
	}
}

func TestResolve_FromEnv_MintsSecretWithNamedKeys(t *testing.T) {
	t.Setenv("MY_CUSTOM_TOKEN", "custom-value")
	k8sClient := makeFakeK8s()

	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthFromEnv,
		EnvVars: []string{"MY_CUSTOM_TOKEN"},
	}
	secretName, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	sec, _ := k8sClient.CoreV1().Secrets("witwave").Get(context.Background(), secretName, metav1.GetOptions{})
	if sec.StringData["MY_CUSTOM_TOKEN"] != "custom-value" {
		t.Error("from-env key MY_CUSTOM_TOKEN not minted")
	}
}

func TestResolve_ExistingSecret_VerifiesPresence(t *testing.T) {
	// Pre-create the Secret.
	preExisting := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "my-anthropic-pat",
			Namespace: "witwave",
		},
	}
	k8sClient := makeFakeK8s(preExisting)

	resolver := BackendAuthResolver{
		Backend:        "claude",
		Mode:           BackendAuthExistingSecret,
		ExistingSecret: "my-anthropic-pat",
	}
	secretName, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if secretName != "my-anthropic-pat" {
		t.Errorf("secret name = %q; want my-anthropic-pat", secretName)
	}
	// Ensure the user's Secret is untouched (no managed-by label added).
	got, _ := k8sClient.CoreV1().Secrets("witwave").Get(context.Background(), "my-anthropic-pat", metav1.GetOptions{})
	if _, labelled := got.Labels[LabelManagedBy]; labelled {
		t.Error("user-managed Secret should not gain a managed-by label")
	}
}

func TestResolve_ExistingSecret_MissingIsActionable(t *testing.T) {
	k8sClient := makeFakeK8s()
	resolver := BackendAuthResolver{
		Backend:        "claude",
		Mode:           BackendAuthExistingSecret,
		ExistingSecret: "does-not-exist",
	}
	_, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err == nil {
		t.Fatal("expected an error for missing Secret")
	}
	if !strings.Contains(err.Error(), "not found") || !strings.Contains(err.Error(), "kubectl") {
		t.Errorf("error = %q; want 'not found' + kubectl recipe", err)
	}
}

// ---------------------------------------------------------------------------
// validateBackendAuthTargets
// ---------------------------------------------------------------------------

func TestValidateBackendAuthTargets_CatchesTypo(t *testing.T) {
	t.Parallel()
	backends := []BackendSpec{{Name: "claude", Type: "claude"}}
	resolvers := []BackendAuthResolver{{Backend: "clade"}} // typo
	err := validateBackendAuthTargets(resolvers, backends)
	if err == nil {
		t.Fatal("expected an error for typo'd backend name")
	}
	if !strings.Contains(err.Error(), "clade") {
		t.Errorf("error = %q; want typo'd name in message", err)
	}
}

// ---------------------------------------------------------------------------
// Secret update path — ww-managed only
// ---------------------------------------------------------------------------

func TestUpsert_RefusesToClobberUserManagedSecret(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "new-value")
	// Pre-create a user-managed Secret at the name ww would mint into.
	preExisting := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "iris-claude",
			Namespace: "witwave",
			// No managed-by label → user-owned.
		},
	}
	k8sClient := makeFakeK8s(preExisting)

	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthProfile,
		Profile: "api-key",
	}
	_, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude")
	if err == nil {
		t.Fatal("expected refusal when a user Secret lives at ww's minting name")
	}
	if !strings.Contains(err.Error(), "not ww-managed") {
		t.Errorf("error = %q; want 'not ww-managed' message", err)
	}
}

func TestUpsert_UpdatesWWManagedSecret(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "rotated-value")
	// Pre-create a ww-managed Secret to simulate a re-run / rotation.
	preExisting := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "iris-claude",
			Namespace: "witwave",
			Labels:    map[string]string{LabelManagedBy: LabelManagedByWW},
		},
		StringData: map[string]string{"ANTHROPIC_API_KEY": "old-value"},
	}
	k8sClient := makeFakeK8s(preExisting)

	resolver := BackendAuthResolver{
		Backend: "claude",
		Mode:    BackendAuthProfile,
		Profile: "api-key",
	}
	if _, err := resolver.resolve(context.Background(), k8sClient, "witwave", "iris", "claude"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got, _ := k8sClient.CoreV1().Secrets("witwave").Get(context.Background(), "iris-claude", metav1.GetOptions{})
	if got.StringData["ANTHROPIC_API_KEY"] != "rotated-value" {
		t.Error("ww-managed Secret update didn't rotate the value")
	}
}
