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
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/tools/record"
	yaml "sigs.k8s.io/yaml"

	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
)

// Labels and annotations stamped on WitwavePrompt-owned ConfigMaps so the
// cleanup pass and the agent-side mount builder can find them without
// re-querying the CRs.
const (
	// labelWitwavePromptName identifies the WitwavePrompt a ConfigMap was rendered
	// for. Unique per ConfigMap (one CM per (WitwavePrompt, agent) pair).
	labelWitwavePromptName = "witwave.ai/witwaveprompt"

	// labelWitwavePromptTargetAgent identifies which WitwaveAgent the ConfigMap is
	// rendered for. Lets the WitwaveAgent reconciler list matching CMs with a
	// single label selector instead of joining across WitwavePrompt specs.
	labelWitwavePromptTargetAgent = "witwave.ai/witwaveprompt-target-agent"

	// labelWitwavePromptKind is set to the spec.kind value. The agent-side
	// mount builder uses this to pick the right container directory
	// (.witwave/jobs, .witwave/tasks, …, or HEARTBEAT.md for kind=heartbeat).
	labelWitwavePromptKind = "witwave.ai/witwaveprompt-kind"

	// componentWitwavePrompt distinguishes WitwavePrompt-owned ConfigMaps from
	// the inline-config + manifest ConfigMaps the WitwaveAgent reconciler
	// owns. Prevents the WitwaveAgent CM cleanup pass from treating a
	// WitwavePrompt CM as stale.
	componentWitwavePrompt = "witwaveprompt"

	// annotationWitwavePromptFilename records the filename the ConfigMap
	// materialises into inside the pod. The agent reconciler reads this
	// when building the per-file subPath mount, so filename drift is
	// authoritative from the CM (the WitwavePrompt spec can change).
	annotationWitwavePromptFilename = "witwave.ai/witwaveprompt-filename"
)

// witwavePromptFinalizer guarantees the operator observes WitwavePrompt deletion
// so it can drain the per-CR Prometheus gauges before the apiserver
// removes the object (#1559). Without a finalizer the "gauge reset" in the
// IsNotFound branch is racy: if the cached informer lags, a Reconcile for
// a just-deleted object may never fire, and ready/desired series for
// (namespace, name) labels persist across operator restarts.
const witwavePromptFinalizer = "witwaveprompt.witwave.ai/finalizer"

// WitwavePromptReconciler reconciles a WitwavePrompt object.
//
// One reconcile produces one ConfigMap per (WitwavePrompt, target agent) pair.
// The WitwaveAgent reconciler watches these ConfigMaps via a label selector
// and renders them into the pod's .witwave/ tree so the harness scheduler
// treats WitwavePrompt-sourced prompts indistinguishably from gitSync-
// materialised ones.
type WitwavePromptReconciler struct {
	client.Client
	// APIReader is a cache-bypassing reader wired from mgr.GetAPIReader()
	// (#1738). Cleanup paths use ``paginatedList(r.APIReader, …)`` so the
	// apiserver actually paginates; the cached client.Client silently
	// ignores client.Limit / client.Continue and would return all
	// matching items in one allocation.
	APIReader client.Reader
	Scheme    *runtime.Scheme
	Recorder  record.EventRecorder
}

// +kubebuilder:rbac:groups=witwave.ai,resources=witwaveprompts,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=witwave.ai,resources=witwaveprompts/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=witwave.ai,resources=witwaveprompts/finalizers,verbs=update

// Reconcile is the control loop entry point. See the type doc for what it does.
func (r *WitwavePromptReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	prompt := &witwavev1alpha1.WitwavePrompt{}
	if err := r.Get(ctx, req.NamespacedName, prompt); err != nil {
		if apierrors.IsNotFound(err) {
			// Belt-and-suspenders: with the finalizer (#1559) the
			// deletion path below already drains gauges before the
			// object disappears. Still drain here so a prompt deleted
			// by an operator that removed the finalizer externally
			// doesn't leak stale (namespace, name) series.
			witwavepromptReadyAgents.DeleteLabelValues(req.Namespace, req.Name)
			witwavepromptDesiredAgents.DeleteLabelValues(req.Namespace, req.Name)
			witwavepromptStatusPatchConflictsTotal.DeleteLabelValues(req.Namespace, req.Name)
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	// Finalizer lifecycle (#1559). Mirrors the WitwaveAgent pattern: drain
	// per-CR metric series on deletion, then remove the finalizer so the
	// apiserver can GC the object. Finalizer mutations go through
	// client.Patch(MergeFrom) so concurrent spec writers don't race the
	// metadata-only update.
	if !prompt.DeletionTimestamp.IsZero() {
		if controllerutil.ContainsFinalizer(prompt, witwavePromptFinalizer) {
			witwavepromptReadyAgents.DeleteLabelValues(prompt.Namespace, prompt.Name)
			witwavepromptDesiredAgents.DeleteLabelValues(prompt.Namespace, prompt.Name)
			witwavepromptStatusPatchConflictsTotal.DeleteLabelValues(prompt.Namespace, prompt.Name)
			before := prompt.DeepCopy()
			controllerutil.RemoveFinalizer(prompt, witwavePromptFinalizer)
			if err := r.Patch(ctx, prompt, client.MergeFrom(before)); err != nil {
				return ctrl.Result{}, fmt.Errorf("remove WitwavePrompt finalizer: %w", err)
			}
		}
		return ctrl.Result{}, nil
	}
	if !controllerutil.ContainsFinalizer(prompt, witwavePromptFinalizer) {
		before := prompt.DeepCopy()
		if controllerutil.AddFinalizer(prompt, witwavePromptFinalizer) {
			if err := r.Patch(ctx, prompt, client.MergeFrom(before)); err != nil {
				return ctrl.Result{}, fmt.Errorf("add WitwavePrompt finalizer: %w", err)
			}
			return ctrl.Result{}, nil
		}
	}
	// Compute the desired ConfigMap set.
	desired := map[string]*corev1.ConfigMap{}
	bindings := make([]witwavev1alpha1.WitwavePromptBinding, 0, len(prompt.Spec.AgentRefs))
	var reconcileErrs []error
	// Deferred outcome tracking (#906). Previously outcomes were
	// incremented inline and the apply-failure path incremented again
	// for bindings already counted 'ready', so sum(outcomes) > bindings
	// and 'percent ready' dashboards computed wrong denominators.
	// Collect the final outcome per agent and flush once at the end so
	// each binding contributes exactly one outcome.
	finalOutcome := make(map[string]string, len(prompt.Spec.AgentRefs))

	for _, ref := range prompt.Spec.AgentRefs {
		binding := witwavev1alpha1.WitwavePromptBinding{AgentName: ref.Name}

		// Resolve the target agent. When absent we keep going — the
		// watch on WitwaveAgent re-enqueues this WitwavePrompt once the agent
		// shows up, so there is no need to return an error.
		agent := &witwavev1alpha1.WitwaveAgent{}
		if err := r.Get(ctx, types.NamespacedName{Namespace: prompt.Namespace, Name: ref.Name}, agent); err != nil {
			if apierrors.IsNotFound(err) {
				binding.Message = "target WitwaveAgent not found (will retry when it appears)"
				bindings = append(bindings, binding)
				finalOutcome[ref.Name] = "agent_missing"
				continue
			}
			reconcileErrs = append(reconcileErrs, fmt.Errorf("get WitwaveAgent %q: %w", ref.Name, err))
			binding.Message = err.Error()
			bindings = append(bindings, binding)
			finalOutcome[ref.Name] = "agent_missing"
			continue
		}

		cm, err := buildWitwavePromptConfigMap(prompt, ref)
		if err != nil {
			reconcileErrs = append(reconcileErrs, fmt.Errorf("build ConfigMap for %q: %w", ref.Name, err))
			binding.Message = err.Error()
			bindings = append(bindings, binding)
			finalOutcome[ref.Name] = "build_error"
			continue
		}
		if err := controllerutil.SetControllerReference(prompt, cm, r.Scheme); err != nil {
			reconcileErrs = append(reconcileErrs, fmt.Errorf("set owner on ConfigMap %s: %w", cm.Name, err))
			binding.Message = err.Error()
			bindings = append(bindings, binding)
			finalOutcome[ref.Name] = "owner_error"
			continue
		}

		desired[cm.Name] = cm
		binding.ConfigMapName = cm.Name
		binding.Filename = cm.Annotations[annotationWitwavePromptFilename]
		binding.Ready = true
		bindings = append(bindings, binding)
		finalOutcome[ref.Name] = "ready"
	}

	// Apply each desired ConfigMap.
	for _, cm := range desired {
		if err := r.applyWitwavePromptConfigMap(ctx, cm); err != nil {
			reconcileErrs = append(reconcileErrs, err)
			// Flip the matching binding to Ready=false so status reflects
			// the write failure instead of the build success. Under a CM
			// name collision multiple bindings may match — each flips
			// independently. finalOutcome is overwritten (not double-
			// incremented) so the counter stays per-binding.
			for i := range bindings {
				if bindings[i].ConfigMapName == cm.Name {
					bindings[i].Ready = false
					bindings[i].Message = err.Error()
					finalOutcome[bindings[i].AgentName] = "apply_error"
				}
			}
		}
	}

	// #1228: outcomes counter flush is deferred until AFTER the status
	// patch succeeds — mirrors the gauge pattern below so a 409-exhausted
	// patch does not leave dashboards advertising apply_success counts
	// for a reconcile whose status write never landed.

	// GC any WitwavePrompt-owned ConfigMaps whose (prompt, agent) pair is no
	// longer in the spec. Controller-runtime's OwnerReferences GC covers
	// the delete-whole-prompt case; this pass handles the remove-agent-
	// from-spec case.
	//
	// #1726: paginate the List so a WitwavePrompt with hundreds of bound
	// agents (or a namespace holding many WitwavePrompts with large
	// agentRefs lists) cannot trip apiserver chunk-fetch limits. Mirrors
	// the #1656 sweep applied to the WitwaveAgent reconciler's cleanup
	// paths.
	existing := &corev1.ConfigMapList{}
	if err := paginatedList(ctx, r.APIReader, existing, func() error {
		for i := range existing.Items {
			cm := &existing.Items[i]
			if _, ok := desired[cm.Name]; ok {
				continue
			}
			if !metav1.IsControlledBy(cm, prompt) {
				continue
			}
			if err := r.Delete(ctx, cm); err != nil && !apierrors.IsNotFound(err) {
				reconcileErrs = append(reconcileErrs, fmt.Errorf("delete stale ConfigMap %s: %w", cm.Name, err))
			}
		}
		return nil
	},
		client.InNamespace(prompt.Namespace),
		client.MatchingLabels{
			labelManagedBy:         managedBy,
			labelWitwavePromptName: prompt.Name,
		},
	); err != nil {
		reconcileErrs = append(reconcileErrs, fmt.Errorf("list owned ConfigMaps: %w", err))
	}

	// Update status.
	var readyCount int32
	for _, b := range bindings {
		if b.Ready {
			readyCount++
		}
	}
	desiredCount := int32(len(prompt.Spec.AgentRefs)) //nolint:gosec // G115: AgentRefs slice length is bounded by K8s API list-size constraints + etcd 1.5MB object-size cap; cannot exceed int32 range. K8s status counters use int32 by wire-type convention.

	// Status().Patch with MergeFrom (#757) so concurrent writers on the
	// status subresource do not contend over the full object version;
	// only contested fields raise a conflict.
	//
	// 409 retry policy (#905): a conflict used to return to controller-
	// runtime, which requeued the WHOLE reconcile — including the apply
	// loop above — even though the CM Updates already succeeded. Under
	// contention a single WitwavePrompt could land the same Update dozens of
	// times in the apiserver audit log. Retry the status patch inline
	// with a bounded re-Get + re-apply-status loop so the apply work is
	// not duplicated. Non-conflict errors still propagate to controller-
	// runtime so its rate limiter can back off.
	hasErr := len(reconcileErrs) > 0
	statusErr := r.patchStatusWithConflictRetry(ctx, prompt, bindings, readyCount, hasErr)
	if statusErr != nil {
		reconcileErrs = append(reconcileErrs, fmt.Errorf("update status: %w", statusErr))
	}

	// #1177: publish ready/desired gauges only AFTER a successful status
	// patch. Previously the gauges fired before the patch, so a 409
	// exhaustion would leave dashboards advertising a post-reconcile
	// state that never actually landed on the CR. Firing after the
	// commit keeps the metric and the status subresource in lockstep —
	// on patch failure the gauges are left at their prior values and
	// the next reconcile (triggered by the resource version watch) will
	// re-try the whole update.
	if statusErr == nil {
		witwavepromptReadyAgents.WithLabelValues(prompt.Namespace, prompt.Name).Set(float64(readyCount))
		witwavepromptDesiredAgents.WithLabelValues(prompt.Namespace, prompt.Name).Set(float64(desiredCount))
		// #1228: flush outcomes exactly once per binding AFTER status
		// patch success. The per-outcome counter carries no agent label
		// (#1070) to avoid unbounded cardinality from malformed specs.
		for _, outcome := range finalOutcome {
			witwavepromptBindingOutcomesTotal.WithLabelValues(outcome).Inc()
		}
	}

	if len(reconcileErrs) > 0 {
		// Join all errors so controller-runtime backoff applies.
		msg := make([]string, 0, len(reconcileErrs))
		for _, e := range reconcileErrs {
			msg = append(msg, e.Error())
		}
		log.Error(fmt.Errorf("%s", strings.Join(msg, "; ")), "WitwavePrompt reconcile encountered errors")
		// #1227: return a joined error so controller-runtime's backoff
		// path sees every failure, not just the first one. Collapsing
		// to reconcileErrs[0] previously hid late-loop errors from
		// operators triaging reconciles.
		return ctrl.Result{}, errors.Join(reconcileErrs...)
	}

	return ctrl.Result{}, nil
}

// applyWitwavePromptConfigMap is the create-or-update helper for a WitwavePrompt-
// owned ConfigMap. Mirrors the simpler of the two patterns in the WitwaveAgent
// reconciler (no content-hash short-circuit yet — prompts change infrequently
// and are small).
//
// #949: Labels/Annotations are merged rather than full-overwritten. Previously
// a bare `existing.Annotations = desired.Annotations` clobbered annotations
// written by unrelated controllers (ArgoCD tracking, cost labellers, CSI
// snapshotters) between our Get and Update, producing annotation flap and
// noisy reconciles. We now only mutate the keys this controller owns
// (see witwavePromptOwnedAnnotationKeys / witwavePromptOwnedLabelKeys) and preserve
// any foreign keys already present on the object.
func (r *WitwavePromptReconciler) applyWitwavePromptConfigMap(ctx context.Context, desired *corev1.ConfigMap) error {
	existing := &corev1.ConfigMap{}
	err := r.Get(ctx, client.ObjectKeyFromObject(desired), existing)
	switch {
	case apierrors.IsNotFound(err):
		return r.Create(ctx, desired)
	case err != nil:
		return err
	}
	existing.Data = desired.Data
	existing.Labels = mergeOwnedStringMap(existing.Labels, desired.Labels, witwavePromptOwnedLabelKeys)
	existing.Annotations = mergeOwnedStringMap(existing.Annotations, desired.Annotations, witwavePromptOwnedAnnotationKeys)
	existing.OwnerReferences = desired.OwnerReferences
	return r.Update(ctx, existing)
}

// witwavePromptOwnedLabelKeys enumerates the metadata.labels keys this
// controller stamps onto prompt ConfigMaps. Any label key outside this set
// is considered owned by another actor and is preserved on Update (#949).
var witwavePromptOwnedLabelKeys = []string{
	labelName,
	labelComponent,
	labelPartOf,
	labelManagedBy,
	labelWitwavePromptName,
	labelWitwavePromptTargetAgent,
	labelWitwavePromptKind,
}

// witwavePromptOwnedAnnotationKeys enumerates the metadata.annotations keys
// this controller stamps onto prompt ConfigMaps. Only these keys are
// (re)written on Update; foreign annotations pass through (#949).
var witwavePromptOwnedAnnotationKeys = []string{
	annotationWitwavePromptFilename,
}

// mergeOwnedStringMap returns a map that preserves every key in `existing`
// except those the caller declares as owned. For owned keys, the value from
// `desired` wins (or the key is removed if desired drops it). This is the
// Go-map equivalent of a field-manager filter: we rewrite only what we own
// and leave other controllers' writes untouched.
func mergeOwnedStringMap(existing, desired map[string]string, ownedKeys []string) map[string]string {
	owned := make(map[string]struct{}, len(ownedKeys))
	for _, k := range ownedKeys {
		owned[k] = struct{}{}
	}
	out := make(map[string]string, len(existing)+len(desired))
	for k, v := range existing {
		if _, isOwned := owned[k]; isOwned {
			continue
		}
		out[k] = v
	}
	for k, v := range desired {
		out[k] = v
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// patchStatusWithConflictRetry writes WitwavePrompt status via Status().Patch
// and re-Gets + re-applies the status fields on 409 conflicts, without
// re-running the apply loop (#905). Bounded retries (5) avoid wedging the
// reconciler on sustained contention — after the limit the error is
// returned to controller-runtime so its rate limiter can space retries.
func (r *WitwavePromptReconciler) patchStatusWithConflictRetry(
	ctx context.Context,
	prompt *witwavev1alpha1.WitwavePrompt,
	bindings []witwavev1alpha1.WitwavePromptBinding,
	readyCount int32,
	reconcileHadErrors bool,
) error {
	const maxAttempts = 5
	// The bindings / readyCount values passed in were computed against the
	// spec generation we observed at the top of Reconcile. If a 409 retry
	// re-Gets the object and finds a newer spec generation, we must NOT
	// stamp ObservedGeneration with the fresh generation — that would
	// falsely advertise "fresh spec reconciled" while bindings still
	// reflect the old spec (#1012). In that case we preserve the prior
	// ObservedGeneration and the Ready condition's ObservedGeneration so
	// the next reconcile (triggered by the spec-generation watch) recomputes
	// bindings against the current spec.
	reconciledGeneration := prompt.Generation
	// The ready condition content is recomputed fresh on each retry so
	// its LastTransitionTime tracks the successful write, and so the
	// bindings message stays in sync with the re-Get'd object.
	apply := func(target *witwavev1alpha1.WitwavePrompt) {
		// Only stamp ObservedGeneration when the spec we reconciled
		// against still matches the object we're patching. Otherwise
		// keep whatever was there so status doesn't lie about which
		// generation produced these bindings.
		stampGen := reconciledGeneration
		if target.Generation != reconciledGeneration {
			// Preserve prior ObservedGeneration; do not overwrite.
			stampGen = target.Status.ObservedGeneration
		} else {
			target.Status.ObservedGeneration = reconciledGeneration
		}
		target.Status.Bindings = bindings
		target.Status.ReadyCount = readyCount
		cond := metav1.Condition{
			Type:               witwavev1alpha1.WitwavePromptConditionReady,
			LastTransitionTime: metav1.Now(),
			ObservedGeneration: stampGen,
		}
		// #1312: compare against len(bindings) when generation moved so
		// we don't falsely declare AllBound against a new spec's refs.
		var allBoundRefs int
		if target.Generation == reconciledGeneration {
			allBoundRefs = len(target.Spec.AgentRefs)
		} else {
			allBoundRefs = len(bindings)
		}
		if !reconcileHadErrors && readyCount == int32(allBoundRefs) && allBoundRefs > 0 { //nolint:gosec // G115: allBoundRefs is len(target.Spec.AgentRefs) or len(bindings); bounded by K8s API list-size constraints + etcd 1.5MB object-size cap.
			cond.Status = metav1.ConditionTrue
			cond.Reason = "AllBound"
		} else {
			cond.Status = metav1.ConditionFalse
			cond.Reason = "PartialBinding"
		}
		// #1312: use the same generation-basis for BOTH numerator and
		// denominator so the message doesn't show "readyCount(old_spec) /
		// desired(new_spec)" when a 409 retry observes a newer spec.
		// When we preserve prior ObservedGeneration (spec moved), report
		// against the bindings we actually wrote (they reflect the old
		// spec) — so also use len(bindings) as the denominator instead
		// of len(target.Spec.AgentRefs).
		var desiredCount int
		if target.Generation == reconciledGeneration {
			desiredCount = len(target.Spec.AgentRefs)
		} else {
			desiredCount = len(bindings)
		}
		cond.Message = fmt.Sprintf("%d/%d agents bound.", readyCount, desiredCount)
		setCondition(&target.Status.Conditions, cond)
	}

	// First attempt uses the object we already have.
	before := prompt.DeepCopy()
	apply(prompt)
	err := r.Status().Patch(ctx, prompt, client.MergeFrom(before))
	if err == nil || !apierrors.IsConflict(err) {
		return err
	}
	// Count the initial 409 so dashboards can alert on sustained
	// contention rather than bucketing it into the generic reconcile
	// error rate (#950).
	witwavepromptStatusPatchConflictsTotal.WithLabelValues(prompt.Namespace, prompt.Name).Inc()

	// Conflict path: re-Get and re-apply status (only), bounded.
	for attempt := 2; attempt <= maxAttempts; attempt++ {
		fresh := &witwavev1alpha1.WitwavePrompt{}
		if gErr := r.Get(ctx, client.ObjectKeyFromObject(prompt), fresh); gErr != nil {
			return fmt.Errorf("refetch after conflict: %w", gErr)
		}
		// #1677: do NOT refresh reconciledGeneration here. The
		// bindings/readyCount values we're about to patch were computed
		// against the spec captured at the top of Reconcile (line 416).
		// If a 409 retry observes a newer fresh.Generation, advancing
		// reconciledGeneration to it would falsely claim
		// "ObservedGeneration=N reconciled" while Bindings still reflect
		// generation N-1. The apply() mismatch branch (target.Generation
		// != reconciledGeneration) preserves the prior ObservedGeneration
		// in that case so status remains internally consistent; the next
		// watch-fired reconcile will recompute bindings against the
		// current spec and stamp the new ObservedGeneration honestly.
		// The earlier #1636 attempt to "track the freshest spec" inverted
		// this invariant and is reverted here.
		freshBefore := fresh.DeepCopy()
		apply(fresh)
		err = r.Status().Patch(ctx, fresh, client.MergeFrom(freshBefore))
		if err == nil {
			// Echo the written status back onto the caller's copy so
			// the rest of the reconcile (logs, metrics already flushed
			// above) observes the committed state.
			prompt.Status = fresh.Status
			return nil
		}
		if !apierrors.IsConflict(err) {
			return err
		}
		witwavepromptStatusPatchConflictsTotal.WithLabelValues(fresh.Namespace, fresh.Name).Inc()
	}
	return err
}

// witwavePromptConfigMapName is the per-(prompt, agent) ConfigMap name.
//
// #1676: the previous scheme `witwaveprompt-{prompt}-{agent}` was non-
// injective. DNS-1123 names allow internal hyphens, so distinct pairs
// could collapse to the same CM name (e.g. (foo-bar, baz) and (foo,
// bar-baz) both rendered `witwaveprompt-foo-bar-baz`). Two coexisting
// WitwavePrompts could then write to the same ConfigMap, silently
// shadowing one binding.
//
// The fix appends a 12-hex-char SHA-256-based suffix derived from
// (promptName + 0x00 + agentName). The 0x00 separator cannot appear in
// either input (DNS-1123 only allows alphanumerics and hyphens), so the
// hash is collision-resistant on input pairs in addition to the
// general 48-bit collision resistance of the truncated digest. The
// prompt name remains in the CM name for `kubectl get cm` readability;
// the suffix is what guarantees uniqueness.
//
// GC is label-driven (controller_witwaveprompt.go reconcile loop) so old
// CMs created under the prior scheme are auto-cleaned on first reconcile
// after upgrade — no migration step required.
func witwavePromptConfigMapName(promptName, agentName string) string {
	sum := sha256.Sum256([]byte(promptName + "\x00" + agentName))
	return fmt.Sprintf("witwaveprompt-%s-%s", promptName, hex.EncodeToString(sum[:6]))
}

// witwavePromptFilename is the filename materialised inside the pod under the
// kind's directory. Heartbeat is a special case: the harness watches for a
// file named exactly HEARTBEAT.md.
func witwavePromptFilename(prompt *witwavev1alpha1.WitwavePrompt, ref witwavev1alpha1.WitwavePromptAgentRef) string {
	if prompt.Spec.Kind == witwavev1alpha1.WitwavePromptKindHeartbeat {
		return "HEARTBEAT.md"
	}
	base := fmt.Sprintf("witwaveprompt-%s", prompt.Name)
	if ref.FilenameSuffix != "" {
		base = fmt.Sprintf("%s-%s", base, ref.FilenameSuffix)
	}
	return base + ".md"
}

// witwavePromptMountDir is the absolute path inside the pod the prompt file
// materialises at. For heartbeat this is the file path itself; for
// directory-watched kinds it is the parent directory.
func witwavePromptMountDir(kind witwavev1alpha1.WitwavePromptKind) string {
	switch kind {
	case witwavev1alpha1.WitwavePromptKindJob:
		return "/home/agent/.witwave/jobs"
	case witwavev1alpha1.WitwavePromptKindTask:
		return "/home/agent/.witwave/tasks"
	case witwavev1alpha1.WitwavePromptKindTrigger:
		return "/home/agent/.witwave/triggers"
	case witwavev1alpha1.WitwavePromptKindContinuation:
		return "/home/agent/.witwave/continuations"
	case witwavev1alpha1.WitwavePromptKindWebhook:
		return "/home/agent/.witwave/webhooks"
	case witwavev1alpha1.WitwavePromptKindHeartbeat:
		return "/home/agent/.witwave"
	}
	return ""
}

// buildWitwavePromptConfigMap renders the ConfigMap for one (WitwavePrompt, agent)
// binding. The single data key is the filename the pod will see; the value
// is the fully-assembled YAML-frontmatter-plus-body .md document.
func buildWitwavePromptConfigMap(prompt *witwavev1alpha1.WitwavePrompt, ref witwavev1alpha1.WitwavePromptAgentRef) (*corev1.ConfigMap, error) {
	body, err := renderWitwavePromptBody(prompt)
	if err != nil {
		return nil, err
	}
	filename := witwavePromptFilename(prompt, ref)
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      witwavePromptConfigMapName(prompt.Name, ref.Name),
			Namespace: prompt.Namespace,
			Labels: map[string]string{
				labelName:                     ref.Name,
				labelComponent:                componentWitwavePrompt,
				labelPartOf:                   partOf,
				labelManagedBy:                managedBy,
				labelWitwavePromptName:        prompt.Name,
				labelWitwavePromptTargetAgent: ref.Name,
				labelWitwavePromptKind:        string(prompt.Spec.Kind),
			},
			Annotations: map[string]string{
				annotationWitwavePromptFilename: filename,
			},
		},
		Data: map[string]string{
			filename: body,
		},
	}
	return cm, nil
}

// renderWitwavePromptBody assembles the final .md document: optional YAML
// frontmatter between "---" fences, then the body. Frontmatter keys are
// sorted so the output is deterministic across reconciles (kubelet
// compares ConfigMap bytes, and unstable ordering would churn the mount).
func renderWitwavePromptBody(prompt *witwavev1alpha1.WitwavePrompt) (string, error) {
	var buf strings.Builder
	if prompt.Spec.Frontmatter != nil && len(prompt.Spec.Frontmatter.Raw) > 0 {
		var m map[string]interface{}
		if err := json.Unmarshal(prompt.Spec.Frontmatter.Raw, &m); err != nil {
			return "", fmt.Errorf("frontmatter is not a JSON object: %w", err)
		}
		// sigs.k8s.io/yaml emits keys in sorted order via its internal
		// json→yaml path, so the rendered frontmatter is stable.
		fm, err := yaml.Marshal(sortMap(m))
		if err != nil {
			return "", fmt.Errorf("marshal frontmatter: %w", err)
		}
		buf.WriteString("---\n")
		buf.Write(fm)
		buf.WriteString("---\n\n")
	}
	buf.WriteString(prompt.Spec.Body)
	// Ensure a trailing newline so POSIX tools that stream the file line-
	// by-line don't drop the last line when Body omits one.
	if !strings.HasSuffix(prompt.Spec.Body, "\n") {
		buf.WriteString("\n")
	}
	return buf.String(), nil
}

// sortMap returns the input with keys in a deterministic order at every
// level of the map tree (#1167). json.Unmarshal into `interface{}` only
// produces `map[string]interface{}`, but we also handle
// `map[interface{}]interface{}` (what a YAML decoder would emit) and
// walk slices so any nested maps inside list entries are normalised too.
// sigs.k8s.io/yaml's sorted-key emission only applies to the top level
// of each map it serialises; nested untyped maps came through in Go's
// random iteration order, so frontmatter with nested structures churned
// the rendered CM bytes across reconciles.
func sortMap(in map[string]interface{}) map[string]interface{} {
	if len(in) == 0 {
		return in
	}
	keys := make([]string, 0, len(in))
	for k := range in {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make(map[string]interface{}, len(in))
	for _, k := range keys {
		out[k] = sortValue(in[k])
	}
	return out
}

// sortValue recursively normalises nested maps and slices so the
// rendered YAML is byte-stable regardless of map iteration order.
func sortValue(v interface{}) interface{} {
	switch x := v.(type) {
	case map[string]interface{}:
		return sortMap(x)
	case map[interface{}]interface{}:
		// Convert to map[string]interface{} when every key is a string
		// so downstream YAML emission remains identical to the plain
		// sortMap path. Non-string keys are left under the original
		// type (rare in JSON-sourced frontmatter, but defensive).
		out := make(map[string]interface{}, len(x))
		allStringKeys := true
		for k := range x {
			if _, ok := k.(string); !ok {
				allStringKeys = false
				break
			}
		}
		if !allStringKeys {
			// Sort by fmt-stringified key for determinism even when
			// keys aren't strings; still normalise values.
			converted := make(map[string]interface{}, len(x))
			for k, v := range x {
				converted[fmt.Sprintf("%v", k)] = v
			}
			return sortMap(converted)
		}
		for k, v := range x {
			out[k.(string)] = v
		}
		return sortMap(out)
	case []interface{}:
		for i, item := range x {
			x[i] = sortValue(item)
		}
		return x
	default:
		return v
	}
}

// SetupWithManager wires the reconciler. WitwavePrompt owns its ConfigMaps;
// changes to a WitwaveAgent that is referenced (or was previously referenced)
// by a WitwavePrompt re-enqueue every WitwavePrompt in the namespace so the
// binding list in status catches up.
func (r *WitwavePromptReconciler) SetupWithManager(mgr ctrl.Manager) error {
	enqueueAllPrompts := handler.EnqueueRequestsFromMapFunc(func(ctx context.Context, obj client.Object) []reconcile.Request {
		prompts := &witwavev1alpha1.WitwavePromptList{}
		if err := mgr.GetClient().List(ctx, prompts, client.InNamespace(obj.GetNamespace())); err != nil {
			return nil
		}
		reqs := make([]reconcile.Request, 0, len(prompts.Items))
		for i := range prompts.Items {
			p := &prompts.Items[i]
			// Re-enqueue every prompt whose spec references this agent,
			// whether the agent exists or not — the "not found" case
			// above writes a status message we want to clear promptly.
			for _, ref := range p.Spec.AgentRefs {
				if ref.Name == obj.GetName() {
					reqs = append(reqs, reconcile.Request{
						NamespacedName: types.NamespacedName{Namespace: p.Namespace, Name: p.Name},
					})
					break
				}
			}
		}
		return reqs
	})

	// #1684: only fan out to prompts on Create/Delete events (where the
	// agent's existence changes) and on Updates where binding-relevant
	// fields change. spec.Body / annotation / label churn on a
	// WitwaveAgent does not affect any prompt binding — the binding
	// resolves on `ref.Name == agent.Name`, plus the
	// not-found-status-message clear path. Forwarding every Update
	// produced an O(N×M) reconcile storm under GitOps churn (Argo/Flux
	// re-applying agent specs on every sync). The Update gate below
	// catches: name change (impossible for a single object), deletion-
	// timestamp set (not-found-equivalent), and Generation bump (the
	// "spec or labels changed in a way that may matter" signal). We use
	// a permissive Update gate to stay safe on edge cases — the win is
	// shedding the high-volume status-only updates that controller-
	// runtime emits as part of its own reconcile loop.
	agentBindingPredicate := predicate.Funcs{
		CreateFunc:  func(e event.CreateEvent) bool { return true },
		DeleteFunc:  func(e event.DeleteEvent) bool { return true },
		GenericFunc: func(e event.GenericEvent) bool { return false },
		UpdateFunc: func(e event.UpdateEvent) bool {
			if e.ObjectOld == nil || e.ObjectNew == nil {
				return true
			}
			// Status-only updates bump ResourceVersion but not
			// Generation. The prompt binding is structurally
			// independent of agent.Status, so suppress those.
			if e.ObjectOld.GetGeneration() == e.ObjectNew.GetGeneration() {
				// One exception: deletionTimestamp transitioning to
				// non-zero is a status-shape change but matters for
				// binding (the prompt status should clear references
				// to a deleting agent). Keep that path live.
				oldDel := !e.ObjectOld.GetDeletionTimestamp().IsZero()
				newDel := !e.ObjectNew.GetDeletionTimestamp().IsZero()
				if oldDel != newDel {
					return true
				}
				return false
			}
			return true
		},
	}

	return ctrl.NewControllerManagedBy(mgr).
		For(&witwavev1alpha1.WitwavePrompt{}).
		Owns(&corev1.ConfigMap{}).
		Watches(&witwavev1alpha1.WitwaveAgent{}, enqueueAllPrompts, builder.WithPredicates(agentBindingPredicate)).
		Named("witwaveprompt").
		Complete(r)
}
