#!/usr/bin/env bash
# Operator/chart PrometheusRule parity check (#1746).
#
# Renders the chart's PrometheusRule template with the chart defaults
# and the operator's WitwaveAgent default-alerts code-path (via a tiny
# Go program) and confirms that the union of {alert, expr} pairs is
# identical between the two surfaces.
#
# Run from repo root: operator/scripts/check-prometheusrule-parity.sh
#
# Exit codes:
#   0  — surfaces agree
#   1  — alert set or expression mismatch (drift)
#   2  — tooling missing (helm, yq, go)
#
# This script is intentionally simple. It does NOT attempt to compare
# annotations / runbook URLs / labels because the chart leans on Helm
# templating syntax that the operator doesn't reproduce verbatim
# (escaped {{ $labels }} blocks, mulf-printed percentages, etc.). The
# alert NAME + expression is the load-bearing shape — that is what
# Prometheus actually evaluates.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[check-prometheusrule-parity] missing required tool: $1" >&2
    exit 2
  }
}

require helm
require yq
require go

TMPDIR="$(mktemp -d)"
# Place the Go emitter inside the operator module so internal/ imports
# are allowed. We use a parity-only subpath that the operator binary
# does not depend on.
EMITDIR="${REPO_ROOT}/operator/scripts/.parity-emit"
mkdir -p "${EMITDIR}"
trap 'rm -rf "${TMPDIR}" "${EMITDIR}"' EXIT

# 1. Render the chart's PrometheusRule with prometheusRule.enabled=true
# and the chart's default alert thresholds.
helm template witwave-test ./charts/witwave \
  --set prometheusRule.enabled=true \
  --show-only templates/prometheusrule.yaml \
  >"${TMPDIR}/chart.yaml"

# 2. Emit the operator's PrometheusRule for an enabled WitwaveAgent via
# a tiny Go runner that calls buildPrometheusRule.
cat >"${EMITDIR}/main.go" <<'GO'
package main

import (
	"encoding/json"
	"fmt"
	"os"

	witwavev1alpha1 "github.com/witwave-ai/witwave-operator/api/v1alpha1"
	"github.com/witwave-ai/witwave-operator/internal/controller"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func main() {
	a := &witwavev1alpha1.WitwaveAgent{
		ObjectMeta:    metav1.ObjectMeta{Name: "iris", Namespace: "default"},
		Spec: witwavev1alpha1.WitwaveAgentSpec{
			PrometheusRule: &witwavev1alpha1.PrometheusRuleSpec{Enabled: true},
		},
	}
	out := controller.BuildPrometheusRuleForParity(a)
	if out == nil {
		fmt.Fprintln(os.Stderr, "buildPrometheusRule returned nil")
		os.Exit(1)
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out.Object); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
GO

# We need an exported wrapper for buildPrometheusRule; the controller
# package already exports BuildPrometheusRuleForParity (see
# plan_exports.go). Build + run the emitter directly from the operator
# module.
(
  cd operator
  go run "${EMITDIR}/main.go"
) >"${TMPDIR}/operator.json"

# 3. Extract sorted (alert, normalised expr) pairs from each.

# extract_chart — read the helm-templated PrometheusRule from
# ${TMPDIR}/chart.yaml and emit a single JSON array of
# {alert, expr} objects sorted by alert name. yq does the YAML->JSON
# projection (one object per rule); jq -s slurps the stream into a
# single array. Output shape must match extract_operator so the
# downstream diff is apples-to-apples.
extract_chart() {
  yq -o=json '.spec.groups[].rules[] | {"alert": .alert, "expr": .expr}' "${TMPDIR}/chart.yaml" |
    jq -c -s 'sort_by(.alert)'
}

# extract_operator — read the operator-emitted PrometheusRule from
# ${TMPDIR}/operator.json (already a JSON object) and emit the same
# alert-sorted [{alert, expr}, ...] array shape extract_chart
# produces. Single jq invocation because the input is already JSON.
extract_operator() {
  jq -c '[.spec.groups[].rules[] | {alert: .alert, expr: .expr}] | sort_by(.alert)' "${TMPDIR}/operator.json"
}

CHART_JSON="$(extract_chart)"
OPERATOR_JSON="$(extract_operator)"

CHART_ALERTS="$(echo "${CHART_JSON}" | jq -r '.[].alert' | sort)"
OPERATOR_ALERTS="$(echo "${OPERATOR_JSON}" | jq -r '.[].alert' | sort)"

if [[ "${CHART_ALERTS}" != "${OPERATOR_ALERTS}" ]]; then
  echo "[check-prometheusrule-parity] alert-name set drift:" >&2
  diff <(echo "${CHART_ALERTS}") <(echo "${OPERATOR_ALERTS}") || true
  exit 1
fi

# 4. Per-alert expression compare. Whitespace-normalised so chart line
# breaks vs operator-emitted line breaks don't cause spurious drift.
fail=0
while IFS= read -r alert; do
  c_expr="$(echo "${CHART_JSON}" | jq -r --arg a "${alert}" '.[] | select(.alert==$a) | .expr' | tr -s '[:space:]' ' ' | sed 's/^ //;s/ $//')"
  o_expr="$(echo "${OPERATOR_JSON}" | jq -r --arg a "${alert}" '.[] | select(.alert==$a) | .expr' | tr -s '[:space:]' ' ' | sed 's/^ //;s/ $//')"
  if [[ "${c_expr}" != "${o_expr}" ]]; then
    echo "[check-prometheusrule-parity] expr drift on ${alert}:" >&2
    echo "  chart:    ${c_expr}" >&2
    echo "  operator: ${o_expr}" >&2
    fail=1
  fi
done <<<"${OPERATOR_ALERTS}"

if [[ ${fail} -ne 0 ]]; then
  exit 1
fi

echo "[check-prometheusrule-parity] OK — chart and operator alert sets agree (${OPERATOR_ALERTS//$'\n'/, })"
