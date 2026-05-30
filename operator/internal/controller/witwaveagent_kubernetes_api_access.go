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
	"context"
	"errors"
	"fmt"
	"reflect"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

const componentKubernetesApiAccess = "kubernetes-api-access"

func kubernetesApiAccessEnabled(agent *witwavev1alpha1.WitwaveAgent) bool {
	return agent.Spec.KubernetesApiAccess != nil && agent.Spec.KubernetesApiAccess.Enabled
}

func kubernetesApiAccessName(agent *witwavev1alpha1.WitwaveAgent) string {
	if agent.Spec.KubernetesApiAccess != nil && agent.Spec.KubernetesApiAccess.Name != "" {
		return agent.Spec.KubernetesApiAccess.Name
	}
	return agent.Name
}

func kubernetesApiAccessMode(agent *witwavev1alpha1.WitwaveAgent) witwavev1alpha1.KubernetesApiAccessMode {
	if agent.Spec.KubernetesApiAccess == nil || agent.Spec.KubernetesApiAccess.Mode == "" {
		return witwavev1alpha1.KubernetesApiAccessModeReadOnly
	}
	return agent.Spec.KubernetesApiAccess.Mode
}

func kubernetesApiAccessLabels(agent *witwavev1alpha1.WitwaveAgent) map[string]string {
	labels := agentLabels(agent)
	labels[labelComponent] = componentKubernetesApiAccess
	return labels
}

func buildKubernetesApiAccessServiceAccount(agent *witwavev1alpha1.WitwaveAgent) *corev1.ServiceAccount {
	return &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      kubernetesApiAccessName(agent),
			Namespace: agent.Namespace,
			Labels:    kubernetesApiAccessLabels(agent),
		},
	}
}

func buildKubernetesApiAccessRole(agent *witwavev1alpha1.WitwaveAgent, rules []rbacv1.PolicyRule) *rbacv1.Role {
	return &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{
			Name:      kubernetesApiAccessName(agent),
			Namespace: agent.Namespace,
			Labels:    kubernetesApiAccessLabels(agent),
		},
		Rules: rules,
	}
}

func buildKubernetesApiAccessRoleBinding(agent *witwavev1alpha1.WitwaveAgent) *rbacv1.RoleBinding {
	name := kubernetesApiAccessName(agent)
	return &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: agent.Namespace,
			Labels:    kubernetesApiAccessLabels(agent),
		},
		Subjects: []rbacv1.Subject{{
			Kind:      rbacv1.ServiceAccountKind,
			Name:      name,
			Namespace: agent.Namespace,
		}},
		RoleRef: rbacv1.RoleRef{
			APIGroup: rbacv1.GroupName,
			Kind:     "Role",
			Name:     name,
		},
	}
}

func kubernetesApiAccessReadOnlyRules() []rbacv1.PolicyRule {
	return []rbacv1.PolicyRule{
		{
			APIGroups: []string{""},
			Resources: []string{
				"configmaps",
				"events",
				"persistentvolumeclaims",
				"pods",
				"services",
			},
			Verbs: []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{""},
			Resources: []string{
				"pods/log",
			},
			Verbs: []string{"get"},
		},
		{
			APIGroups: []string{"apps"},
			Resources: []string{
				"daemonsets",
				"deployments",
				"replicasets",
				"statefulsets",
			},
			Verbs: []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"batch"},
			Resources: []string{
				"cronjobs",
				"jobs",
			},
			Verbs: []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"events.k8s.io"},
			Resources: []string{
				"events",
			},
			Verbs: []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"networking.k8s.io"},
			Resources: []string{
				"ingresses",
				"networkpolicies",
			},
			Verbs: []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"rbac.authorization.k8s.io"},
			Resources: []string{
				"rolebindings",
				"roles",
			},
			Verbs: []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"witwave.ai"},
			Resources: []string{
				"witwaveagents",
				"witwaveprompts",
				"witwaveworkspaces",
			},
			Verbs: []string{"get", "list", "watch"},
		},
	}
}

func kubernetesApiAccessNamespaceWriteRules() []rbacv1.PolicyRule {
	return append(kubernetesApiAccessReadOnlyRules(),
		rbacv1.PolicyRule{
			APIGroups: []string{""},
			Resources: []string{
				"configmaps",
				"services",
			},
			Verbs: []string{"create", "update", "patch", "delete"},
		},
		rbacv1.PolicyRule{
			APIGroups: []string{""},
			Resources: []string{
				"pods",
			},
			// Allow restart-style remediation without allowing raw pod creation
			// or privileged spec mutation.
			Verbs: []string{"delete"},
		},
		rbacv1.PolicyRule{
			APIGroups: []string{""},
			Resources: []string{
				"pods/eviction",
			},
			Verbs: []string{"create"},
		},
		rbacv1.PolicyRule{
			APIGroups: []string{"apps"},
			Resources: []string{
				"deployments",
			},
			Verbs: []string{"create", "update", "patch", "delete"},
		},
		rbacv1.PolicyRule{
			APIGroups: []string{"batch"},
			Resources: []string{
				"cronjobs",
				"jobs",
			},
			Verbs: []string{"create", "update", "patch", "delete"},
		},
	)
}

// kubernetesApiAccessAgentLifecycleRules renders the namespaceWrite surface
// plus patch on witwaveagents, so an Agent-Resources agent (e.g. milo) can
// drive `ww agent upgrade` against its peers. Only the patch verb is added —
// get/list/watch on witwaveagents already come from the readOnly base. The
// grant is resource-scoped (any field of any witwaveagent in the namespace);
// narrow it to image-tag fields with a ValidatingAdmissionPolicy bound to the
// agent's ServiceAccount.
func kubernetesApiAccessAgentLifecycleRules() []rbacv1.PolicyRule {
	return append(kubernetesApiAccessNamespaceWriteRules(),
		rbacv1.PolicyRule{
			APIGroups: []string{"witwave.ai"},
			Resources: []string{
				"witwaveagents",
			},
			Verbs: []string{"patch"},
		},
	)
}

func kubernetesApiAccessRulesForMode(mode witwavev1alpha1.KubernetesApiAccessMode) ([]rbacv1.PolicyRule, error) {
	switch mode {
	case witwavev1alpha1.KubernetesApiAccessModeReadOnly:
		return kubernetesApiAccessReadOnlyRules(), nil
	case witwavev1alpha1.KubernetesApiAccessModeNamespaceWrite:
		return kubernetesApiAccessNamespaceWriteRules(), nil
	case witwavev1alpha1.KubernetesApiAccessModeAgentLifecycle:
		return kubernetesApiAccessAgentLifecycleRules(), nil
	default:
		return nil, fmt.Errorf("unsupported kubernetesApiAccess.mode %q", mode)
	}
}

func (r *WitwaveAgentReconciler) reconcileKubernetesApiAccess(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent) error {
	if !kubernetesApiAccessEnabled(agent) {
		return r.deleteKubernetesApiAccess(ctx, agent)
	}

	rules, err := kubernetesApiAccessRulesForMode(kubernetesApiAccessMode(agent))
	if err != nil {
		return err
	}

	name := kubernetesApiAccessName(agent)
	if err := r.applyKubernetesApiAccessServiceAccount(ctx, agent, buildKubernetesApiAccessServiceAccount(agent)); err != nil {
		return err
	}
	if err := r.applyKubernetesApiAccessRole(ctx, agent, buildKubernetesApiAccessRole(agent, rules)); err != nil {
		return err
	}
	if err := r.applyKubernetesApiAccessRoleBinding(ctx, agent, buildKubernetesApiAccessRoleBinding(agent)); err != nil {
		return err
	}
	return r.cleanupStaleKubernetesApiAccess(ctx, agent, name)
}

func (r *WitwaveAgentReconciler) applyKubernetesApiAccessServiceAccount(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, desired *corev1.ServiceAccount) error {
	if err := controllerutil.SetControllerReference(agent, desired, r.Scheme); err != nil {
		return fmt.Errorf("set owner on Kubernetes API access ServiceAccount: %w", err)
	}

	existing := &corev1.ServiceAccount{}
	err := r.Get(ctx, client.ObjectKeyFromObject(desired), existing)
	switch {
	case apierrors.IsNotFound(err):
		return r.Create(ctx, desired)
	case err != nil:
		return err
	}
	if !metav1.IsControlledBy(existing, agent) {
		return fmt.Errorf("service account %s/%s exists but is not controlled by WitwaveAgent %s/%s",
			existing.Namespace, existing.Name, agent.Namespace, agent.Name)
	}
	if reflect.DeepEqual(existing.Labels, desired.Labels) {
		return nil
	}
	existing.Labels = desired.Labels
	return r.Update(ctx, existing)
}

func (r *WitwaveAgentReconciler) applyKubernetesApiAccessRole(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, desired *rbacv1.Role) error {
	if err := controllerutil.SetControllerReference(agent, desired, r.Scheme); err != nil {
		return fmt.Errorf("set owner on Kubernetes API access Role: %w", err)
	}

	existing := &rbacv1.Role{}
	err := r.Get(ctx, client.ObjectKeyFromObject(desired), existing)
	switch {
	case apierrors.IsNotFound(err):
		return r.Create(ctx, desired)
	case err != nil:
		return err
	}
	if !metav1.IsControlledBy(existing, agent) {
		return fmt.Errorf("role %s/%s exists but is not controlled by WitwaveAgent %s/%s",
			existing.Namespace, existing.Name, agent.Namespace, agent.Name)
	}
	if reflect.DeepEqual(existing.Labels, desired.Labels) && reflect.DeepEqual(existing.Rules, desired.Rules) {
		return nil
	}
	existing.Labels = desired.Labels
	existing.Rules = desired.Rules
	return r.Update(ctx, existing)
}

func (r *WitwaveAgentReconciler) applyKubernetesApiAccessRoleBinding(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, desired *rbacv1.RoleBinding) error {
	if err := controllerutil.SetControllerReference(agent, desired, r.Scheme); err != nil {
		return fmt.Errorf("set owner on Kubernetes API access RoleBinding: %w", err)
	}

	existing := &rbacv1.RoleBinding{}
	err := r.Get(ctx, client.ObjectKeyFromObject(desired), existing)
	switch {
	case apierrors.IsNotFound(err):
		return r.Create(ctx, desired)
	case err != nil:
		return err
	}
	if !metav1.IsControlledBy(existing, agent) {
		return fmt.Errorf("role binding %s/%s exists but is not controlled by WitwaveAgent %s/%s",
			existing.Namespace, existing.Name, agent.Namespace, agent.Name)
	}
	if !reflect.DeepEqual(existing.RoleRef, desired.RoleRef) {
		if err := r.Delete(ctx, existing); err != nil && !apierrors.IsNotFound(err) {
			return err
		}
		return r.Create(ctx, desired)
	}
	if reflect.DeepEqual(existing.Labels, desired.Labels) && reflect.DeepEqual(existing.Subjects, desired.Subjects) {
		return nil
	}
	existing.Labels = desired.Labels
	existing.Subjects = desired.Subjects
	return r.Update(ctx, existing)
}

func (r *WitwaveAgentReconciler) cleanupStaleKubernetesApiAccess(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, desiredName string) error {
	labelSel := client.MatchingLabels{
		labelName:      agent.Name,
		labelComponent: componentKubernetesApiAccess,
		labelManagedBy: managedBy,
	}

	serviceAccounts := &corev1.ServiceAccountList{}
	if err := paginatedList(ctx, r.APIReader, serviceAccounts, func() error {
		for i := range serviceAccounts.Items {
			sa := &serviceAccounts.Items[i]
			if (desiredName != "" && sa.Name == desiredName) || !metav1.IsControlledBy(sa, agent) {
				continue
			}
			if err := r.Delete(ctx, sa); err != nil && !apierrors.IsNotFound(err) {
				return fmt.Errorf("delete stale ServiceAccount %s/%s: %w", sa.Namespace, sa.Name, err)
			}
		}
		return nil
	}, client.InNamespace(agent.Namespace), labelSel); err != nil {
		return fmt.Errorf("list Kubernetes API access ServiceAccounts: %w", err)
	}

	roles := &rbacv1.RoleList{}
	if err := paginatedList(ctx, r.APIReader, roles, func() error {
		for i := range roles.Items {
			role := &roles.Items[i]
			if (desiredName != "" && role.Name == desiredName) || !metav1.IsControlledBy(role, agent) {
				continue
			}
			if err := r.Delete(ctx, role); err != nil && !apierrors.IsNotFound(err) {
				return fmt.Errorf("delete stale Role %s/%s: %w", role.Namespace, role.Name, err)
			}
		}
		return nil
	}, client.InNamespace(agent.Namespace), labelSel); err != nil {
		return fmt.Errorf("list Kubernetes API access Roles: %w", err)
	}

	roleBindings := &rbacv1.RoleBindingList{}
	if err := paginatedList(ctx, r.APIReader, roleBindings, func() error {
		for i := range roleBindings.Items {
			rb := &roleBindings.Items[i]
			if (desiredName != "" && rb.Name == desiredName) || !metav1.IsControlledBy(rb, agent) {
				continue
			}
			if err := r.Delete(ctx, rb); err != nil && !apierrors.IsNotFound(err) {
				return fmt.Errorf("delete stale RoleBinding %s/%s: %w", rb.Namespace, rb.Name, err)
			}
		}
		return nil
	}, client.InNamespace(agent.Namespace), labelSel); err != nil {
		return fmt.Errorf("list Kubernetes API access RoleBindings: %w", err)
	}
	return nil
}

func (r *WitwaveAgentReconciler) deleteKubernetesApiAccess(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent) error {
	name := kubernetesApiAccessName(agent)
	var errs []error
	if err := r.deleteKubernetesApiAccessServiceAccount(ctx, agent, name); err != nil {
		errs = append(errs, err)
	}
	if err := r.deleteKubernetesApiAccessRole(ctx, agent, name); err != nil {
		errs = append(errs, err)
	}
	if err := r.deleteKubernetesApiAccessRoleBinding(ctx, agent, name); err != nil {
		errs = append(errs, err)
	}
	if err := r.cleanupStaleKubernetesApiAccess(ctx, agent, ""); err != nil {
		errs = append(errs, err)
	}
	return errors.Join(errs...)
}

func (r *WitwaveAgentReconciler) deleteKubernetesApiAccessServiceAccount(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, name string) error {
	existing := &corev1.ServiceAccount{}
	err := r.Get(ctx, client.ObjectKey{Namespace: agent.Namespace, Name: name}, existing)
	if apierrors.IsNotFound(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !metav1.IsControlledBy(existing, agent) {
		return nil
	}
	return r.Delete(ctx, existing)
}

func (r *WitwaveAgentReconciler) deleteKubernetesApiAccessRole(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, name string) error {
	existing := &rbacv1.Role{}
	err := r.Get(ctx, client.ObjectKey{Namespace: agent.Namespace, Name: name}, existing)
	if apierrors.IsNotFound(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !metav1.IsControlledBy(existing, agent) {
		return nil
	}
	return r.Delete(ctx, existing)
}

func (r *WitwaveAgentReconciler) deleteKubernetesApiAccessRoleBinding(ctx context.Context, agent *witwavev1alpha1.WitwaveAgent, name string) error {
	existing := &rbacv1.RoleBinding{}
	err := r.Get(ctx, client.ObjectKey{Namespace: agent.Namespace, Name: name}, existing)
	if apierrors.IsNotFound(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !metav1.IsControlledBy(existing, agent) {
		return nil
	}
	return r.Delete(ctx, existing)
}
