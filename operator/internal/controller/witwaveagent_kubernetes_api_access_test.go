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
	"fmt"
	"testing"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

var _ = Describe("WitwaveAgent Kubernetes API access", func() {
	It("creates namespace-scoped read-only identity and wires the pod", func() {
		name := fmt.Sprintf("k8s-access-%s", rand5())
		key := types.NamespacedName{Name: name, Namespace: "default"}
		r := newReconciler()
		agent := newTestAgent(name)
		agent.Spec.KubernetesApiAccess = &witwavev1alpha1.KubernetesApiAccessSpec{Enabled: true}
		Expect(k8sClient.Create(ctx, agent)).To(Succeed())
		defer cleanupKubernetesApiAccessTestAgent(key)

		Expect(reconcileUntilStable(r, key, 5)).To(Succeed())

		sa := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, key, sa)).To(Succeed())
		Expect(sa.Labels).To(HaveKeyWithValue(labelComponent, componentKubernetesApiAccess))
		Expect(sa.OwnerReferences).To(HaveLen(1))

		role := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, key, role)).To(Succeed())
		Expect(role.Labels).To(HaveKeyWithValue(labelComponent, componentKubernetesApiAccess))
		Expect(role.OwnerReferences).To(HaveLen(1))
		Expect(policyRulesContainResource(role.Rules, "pods")).To(BeTrue())
		Expect(policyRulesContainResource(role.Rules, "pods/log")).To(BeTrue())
		Expect(policyRulesContainResource(role.Rules, "witwaveagents")).To(BeTrue())
		Expect(policyRulesContainResource(role.Rules, "nodes")).To(BeFalse())
		Expect(policyRulesContainResource(role.Rules, "persistentvolumes")).To(BeFalse())
		Expect(policyRulesContainResource(role.Rules, "namespaces")).To(BeFalse())
		Expect(policyRulesContainResource(role.Rules, "secrets")).To(BeFalse())
		Expect(policyRulesContainVerb(role.Rules, "create")).To(BeFalse())
		Expect(policyRulesContainVerb(role.Rules, "update")).To(BeFalse())
		Expect(policyRulesContainVerb(role.Rules, "patch")).To(BeFalse())
		Expect(policyRulesContainVerb(role.Rules, "delete")).To(BeFalse())

		roleBinding := &rbacv1.RoleBinding{}
		Expect(k8sClient.Get(ctx, key, roleBinding)).To(Succeed())
		Expect(roleBinding.RoleRef.Kind).To(Equal("Role"))
		Expect(roleBinding.RoleRef.Name).To(Equal(name))
		Expect(roleBinding.Subjects).To(HaveLen(1))
		Expect(roleBinding.Subjects[0].Kind).To(Equal(rbacv1.ServiceAccountKind))
		Expect(roleBinding.Subjects[0].Name).To(Equal(name))
		Expect(roleBinding.Subjects[0].Namespace).To(Equal("default"))
		Expect(roleBinding.OwnerReferences).To(HaveLen(1))

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, key, dep)).To(Succeed())
		Expect(dep.Spec.Template.Spec.ServiceAccountName).To(Equal(name))
		Expect(dep.Spec.Template.Spec.AutomountServiceAccountToken).ToNot(BeNil())
		Expect(*dep.Spec.Template.Spec.AutomountServiceAccountToken).To(BeTrue())
	})

	It("removes the managed identity and returns the pod to no-token mode when disabled", func() {
		name := fmt.Sprintf("k8s-access-toggle-%s", rand5())
		key := types.NamespacedName{Name: name, Namespace: "default"}
		r := newReconciler()
		agent := newTestAgent(name)
		agent.Spec.KubernetesApiAccess = &witwavev1alpha1.KubernetesApiAccessSpec{Enabled: true}
		Expect(k8sClient.Create(ctx, agent)).To(Succeed())
		defer cleanupKubernetesApiAccessTestAgent(key)

		Expect(reconcileUntilStable(r, key, 5)).To(Succeed())
		Expect(k8sClient.Get(ctx, key, &corev1.ServiceAccount{})).To(Succeed())
		Expect(k8sClient.Get(ctx, key, &rbacv1.Role{})).To(Succeed())
		Expect(k8sClient.Get(ctx, key, &rbacv1.RoleBinding{})).To(Succeed())

		current := &witwavev1alpha1.WitwaveAgent{}
		Expect(k8sClient.Get(ctx, key, current)).To(Succeed())
		current.Spec.KubernetesApiAccess = nil
		Expect(k8sClient.Update(ctx, current)).To(Succeed())
		Expect(k8sClient.Get(ctx, key, current)).To(Succeed())
		Expect(current.Spec.KubernetesApiAccess).To(BeNil())
		Expect(current.Spec.ServiceAccountName).To(BeEmpty())

		Expect(reconcileUntilStable(r, key, 5)).To(Succeed())
		Expect(apierrors.IsNotFound(k8sClient.Get(ctx, key, &corev1.ServiceAccount{}))).To(BeTrue())
		Expect(apierrors.IsNotFound(k8sClient.Get(ctx, key, &rbacv1.Role{}))).To(BeTrue())
		Expect(apierrors.IsNotFound(k8sClient.Get(ctx, key, &rbacv1.RoleBinding{}))).To(BeTrue())

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, key, dep)).To(Succeed())
		Expect(dep.Spec.Template.Spec.ServiceAccountName).To(Equal("default"))
		Expect(dep.Spec.Template.Spec.AutomountServiceAccountToken).ToNot(BeNil())
		Expect(*dep.Spec.Template.Spec.AutomountServiceAccountToken).To(BeFalse())
	})

	It("creates bounded namespace-write identity without secrets or RBAC mutation", func() {
		name := fmt.Sprintf("k8s-access-write-%s", rand5())
		key := types.NamespacedName{Name: name, Namespace: "default"}
		r := newReconciler()
		agent := newTestAgent(name)
		agent.Spec.KubernetesApiAccess = &witwavev1alpha1.KubernetesApiAccessSpec{
			Enabled: true,
			Mode:    witwavev1alpha1.KubernetesApiAccessModeNamespaceWrite,
		}
		Expect(k8sClient.Create(ctx, agent)).To(Succeed())
		defer cleanupKubernetesApiAccessTestAgent(key)

		Expect(reconcileUntilStable(r, key, 5)).To(Succeed())

		role := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, key, role)).To(Succeed())
		Expect(policyRulesContainResourceVerb(role.Rules, "pods", "delete")).To(BeTrue())
		Expect(policyRulesContainResourceVerb(role.Rules, "pods/eviction", "create")).To(BeTrue())
		Expect(policyRulesContainResourceVerb(role.Rules, "deployments", "patch")).To(BeTrue())
		Expect(policyRulesContainResourceVerb(role.Rules, "jobs", "create")).To(BeTrue())
		Expect(policyRulesContainResourceVerb(role.Rules, "configmaps", "update")).To(BeTrue())
		Expect(policyRulesContainResourceVerb(role.Rules, "services", "delete")).To(BeTrue())

		Expect(policyRulesContainResource(role.Rules, "secrets")).To(BeFalse())
		Expect(policyRulesContainResource(role.Rules, "nodes")).To(BeFalse())
		Expect(policyRulesContainResource(role.Rules, "namespaces")).To(BeFalse())
		Expect(policyRulesContainResourceVerb(role.Rules, "pods", "create")).To(BeFalse())
		Expect(policyRulesContainResourceVerb(role.Rules, "pods", "patch")).To(BeFalse())
		Expect(policyRulesContainResourceVerb(role.Rules, "roles", "patch")).To(BeFalse())
		Expect(policyRulesContainResourceVerb(role.Rules, "rolebindings", "delete")).To(BeFalse())
	})
})

func cleanupKubernetesApiAccessTestAgent(key types.NamespacedName) {
	agent := &witwavev1alpha1.WitwaveAgent{}
	if err := k8sClient.Get(ctx, key, agent); err == nil {
		controllerutil.RemoveFinalizer(agent, witwaveAgentFinalizer)
		_ = k8sClient.Update(ctx, agent)
		_ = k8sClient.Delete(ctx, agent)
	}
}

func policyRulesContainResource(rules []rbacv1.PolicyRule, resource string) bool {
	for _, rule := range rules {
		for _, got := range rule.Resources {
			if got == resource {
				return true
			}
		}
	}
	return false
}

func policyRulesContainVerb(rules []rbacv1.PolicyRule, verb string) bool {
	for _, rule := range rules {
		for _, got := range rule.Verbs {
			if got == verb {
				return true
			}
		}
	}
	return false
}

func policyRulesContainResourceVerb(rules []rbacv1.PolicyRule, resource, verb string) bool {
	for _, rule := range rules {
		hasResource := false
		for _, got := range rule.Resources {
			if got == resource {
				hasResource = true
				break
			}
		}
		if !hasResource {
			continue
		}
		for _, got := range rule.Verbs {
			if got == verb {
				return true
			}
		}
	}
	return false
}

// TestKubernetesApiAccessAgentLifecycleRules pins the agentLifecycle preset:
// the namespaceWrite surface PLUS patch on witwaveagents, and nothing more
// dangerous. This is what lets an Agent-Resources agent (milo) drive
// `ww agent upgrade` against its peers. Pure unit test — no envtest.
func TestKubernetesApiAccessAgentLifecycleRules(t *testing.T) {
	rules, err := kubernetesApiAccessRulesForMode(witwavev1alpha1.KubernetesApiAccessModeAgentLifecycle)
	if err != nil {
		t.Fatalf("rules for agentLifecycle: %v", err)
	}

	// The defining addition: patch on witwaveagents.
	if !policyRulesContainResourceVerb(rules, "witwaveagents", "patch") {
		t.Error("agentLifecycle must grant patch on witwaveagents (for ww agent upgrade)")
	}
	// Inherits the namespaceWrite remediation surface.
	if !policyRulesContainResourceVerb(rules, "pods", "delete") {
		t.Error("agentLifecycle should retain namespaceWrite pods/delete")
	}
	if !policyRulesContainResourceVerb(rules, "deployments", "patch") {
		t.Error("agentLifecycle should retain namespaceWrite deployments/patch")
	}
	// Inherits the readOnly read on witwaveagents.
	if !policyRulesContainResourceVerb(rules, "witwaveagents", "get") {
		t.Error("agentLifecycle should retain read on witwaveagents")
	}

	// Withholds everything namespaceWrite withholds, and grants no broader
	// witwaveagents mutation than patch.
	if policyRulesContainResource(rules, "secrets") {
		t.Error("agentLifecycle must NOT grant secrets")
	}
	if policyRulesContainResourceVerb(rules, "roles", "patch") {
		t.Error("agentLifecycle must NOT grant RBAC mutation")
	}
	if policyRulesContainResourceVerb(rules, "witwaveagents", "delete") {
		t.Error("agentLifecycle must grant patch only on witwaveagents, not delete")
	}
}
