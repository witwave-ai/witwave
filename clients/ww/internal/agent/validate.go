package agent

import (
	"fmt"
	"regexp"
)

// Name length cap. Kubernetes limits most object names to 253 chars, but
// container names (which the operator derives from the agent's name +
// backend name) cap at 63. A 50-char agent-name ceiling leaves 13 chars
// for the backend suffix — enough for `-claude` / `-openai` / `-codex` /
// `-gemini` / `-echo` plus a trailing disambiguator without tripping the
// container-name limit.
const maxAgentNameLen = 50

// DNS-1123 label pattern: lowercase alphanumerics and '-', must start and
// end with alphanumeric. Matches Kubernetes' own validation.
var dns1123LabelRE = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)

// ValidateName enforces the naming rules for WitwaveAgent CRs. The rules
// are deliberately the intersection of:
//
//   - Kubernetes DNS-1123 label (required for any downstream Service /
//     label-selector matching)
//   - A 50-char ceiling so derived container names stay under the 63-char
//     Kubernetes limit after the operator appends a backend suffix
//
// Keep the error message actionable — the most common user mistake is
// including an underscore or uppercase character.
func ValidateName(name string) error {
	if name == "" {
		return fmt.Errorf("agent name must not be empty")
	}
	if len(name) > maxAgentNameLen {
		return fmt.Errorf(
			"agent name %q is %d characters; maximum is %d (leaves room for the operator to derive container names under Kubernetes' 63-char limit)",
			name, len(name), maxAgentNameLen,
		)
	}
	if !dns1123LabelRE.MatchString(name) {
		return fmt.Errorf(
			"agent name %q is invalid: must be lowercase alphanumerics and '-' only, start and end with alphanumeric (DNS-1123 label)",
			name,
		)
	}
	return nil
}
