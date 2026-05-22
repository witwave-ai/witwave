package agent

import (
	"fmt"
	"path/filepath"
	"strings"
)

// skeletonFile is one file the scaffolder materialises. Paths are
// repo-relative; content is the exact byte string to write.
type skeletonFile struct {
	Path    string
	Content string
}

// buildSkeleton returns the minimum-viable file set for a freshly
// scaffolded agent. Shape (multi-backend example):
//
//	.agents/[<group>/]<name>/
//	├── README.md
//	├── .witwave/
//	│   ├── backend.yaml          # routing — every backend listed
//	│   └── HEARTBEAT.md          # hourly heartbeat, unless skel.NoHeartbeat
//	├── .<backend-0>/
//	│   ├── agent-card.md         # A2A identity skeleton
//	│   └── <BEHAVIOR>.md         # LLM-backed types only
//	├── .<backend-1>/
//	│   ├── agent-card.md
//	│   └── <BEHAVIOR>.md
//	└── ...
//
// Deliberately omits: `jobs/`, `tasks/`, `triggers/`, `continuations/`,
// `webhooks/`. Per DESIGN.md SUB-1..4 their absence is how an agent
// expresses "I don't use this feature yet" — we don't pre-create
// dormant subsystems for those.
//
// HEARTBEAT.md is a documented exception to that rule: we scaffold it
// on by default because a running agent that reports "HEARTBEAT_OK"
// on a schedule is the cheapest possible proof that the dispatch path,
// the backend sidecars, and the routing config all actually work.
// Users who genuinely want a heartbeat-free agent pass
// skel.NoHeartbeat = true (cobra: --no-heartbeat), which keeps the
// dormant-default posture for that subsystem.
func buildSkeleton(skel skeletonOpts) []skeletonFile {
	root := filepath.ToSlash(agentRepoRoot(skel.Name, skel.Group))

	// Fall back to the hello-world default (single echo) when the
	// caller didn't specify any backends. Keeps backward compat with
	// tests and any programmatic callers that don't populate the new
	// slice.
	backends := skel.Backends
	if len(backends) == 0 {
		backends = []BackendSpec{{Name: DefaultBackend, Type: DefaultBackend, Port: BackendPort(0)}}
	}

	files := []skeletonFile{
		{
			Path:    root + "/README.md",
			Content: renderAgentReadme(skel.Name, skel.Group, backends, !skel.NoHeartbeat),
		},
		{
			Path:    root + "/.witwave/backend.yaml",
			Content: renderBackendYAML(backends),
		},
	}

	if !skel.NoHeartbeat {
		files = append(files, skeletonFile{
			Path:    root + "/.witwave/HEARTBEAT.md",
			Content: renderHeartbeat(),
		})
	}

	// One folder per declared backend, each with an agent-card.md and
	// (for LLM-backed types) a behavioural-instructions file. Name-vs-
	// type matters here: folder name tracks the backend NAME (so the
	// operator's gitMapping .<name>/ path resolves), while the
	// behavioural filename (CLAUDE.md / AGENTS.md / GEMINI.md) tracks
	// the backend TYPE. Two backends of the same type with different
	// names both get the same behavioural filename inside their own
	// folder.
	for _, b := range backends {
		files = append(files, skeletonFile{
			Path:    root + "/." + b.Name + "/agent-card.md",
			Content: renderAgentCard(skel.Name, b.Name, b.Type),
		})
		if behaviorName, ok := behaviorFileName(b.Type); ok {
			files = append(files, skeletonFile{
				Path:    root + "/." + b.Name + "/" + behaviorName,
				Content: renderBehaviorStub(skel.Name, b.Name, b.Type),
			})
		}
	}

	return files
}

// skeletonOpts is the narrow slice of ScaffoldOptions buildSkeleton
// cares about. Passing a struct (rather than positional args) keeps
// call sites compact and lets new fields land without rippling
// through every helper.
type skeletonOpts struct {
	Name        string
	Group       string
	Backends    []BackendSpec
	CLIVersion  string
	NoHeartbeat bool
}

// agentRepoRoot returns the repo-relative directory for an agent,
// honouring the optional group segment. `group == ""` produces the
// flat `.agents/<name>/` layout (the default, per the product
// discussion); a group name nests one level deeper.
func agentRepoRoot(name, group string) string {
	if group == "" {
		return filepath.Join(".agents", name)
	}
	return filepath.Join(".agents", group, name)
}

// behaviorFileName returns the backend-specific behavioural-instructions
// filename, mirroring how the existing test agents are laid out:
// CLAUDE.md for claude, AGENTS.md for openai/codex, GEMINI.md for gemini. The
// `ok` return is false for backends that don't carry such a file
// (today: only echo).
func behaviorFileName(backend string) (string, bool) {
	switch backend {
	case BackendClaude:
		return "CLAUDE.md", true
	case BackendOpenAI:
		return "AGENTS.md", true
	case BackendCodex:
		return "AGENTS.md", true
	case BackendGemini:
		return "GEMINI.md", true
	case BackendEcho:
		return "", false
	}
	return "", false
}

// renderAgentReadme produces a one-screen README.md explaining what
// this agent is and what the file layout means. Multi-backend aware:
// enumerates every declared backend's folder + its behavioural-
// instructions file (if any). Intentionally terse — someone reading
// this while clicking around the repo should grasp the shape without
// any witwave context.
func renderAgentReadme(name, group string, backends []BackendSpec, hasHeartbeat bool) string {
	var groupLine string
	if group != "" {
		groupLine = fmt.Sprintf("Group:   `%s`\n", group)
	}

	// Backend summary line for the header: "echo-1 (echo), echo-2 (echo)"
	// or just "claude" when name == type (common case).
	backendLine := ""
	parts := make([]string, 0, len(backends))
	for _, b := range backends {
		if b.Name == b.Type {
			parts = append(parts, fmt.Sprintf("`%s`", b.Name))
		} else {
			parts = append(parts, fmt.Sprintf("`%s` (`%s`)", b.Name, b.Type))
		}
	}
	if len(parts) == 1 {
		backendLine = "Backend: " + parts[0]
	} else {
		backendLine = "Backends: " + strings.Join(parts, ", ")
	}

	// Layout section: one line per backend, plus behaviour-file line
	// for LLM backends.
	var layout strings.Builder
	fmt.Fprintln(&layout, ".witwave/backend.yaml    Harness routing config. Every declared backend")
	fmt.Fprintln(&layout, "                         is listed under `agents:`; every concern routes")
	fmt.Fprintln(&layout, "                         to the first backend by default — edit this file")
	fmt.Fprintln(&layout, "                         to redistribute across backends.")
	if hasHeartbeat {
		fmt.Fprintln(&layout, ".witwave/HEARTBEAT.md    Hourly heartbeat. Fires a prompt at the agent every hour")
		fmt.Fprintln(&layout, "                         at minute 0. Edit the schedule (cron) or body, or delete")
		fmt.Fprintln(&layout, "                         the file entirely to go silent.")
	}
	for _, b := range backends {
		fmt.Fprintf(&layout, ".%s/agent-card.md    A2A identity card for the `%s` sidecar (port %d).\n",
			b.Name, b.Name, b.Port)
		if behaviorName, ok := behaviorFileName(b.Type); ok {
			fmt.Fprintf(&layout, ".%s/%s        Behavioural instructions mounted at /home/agent/.%s/%s.\n",
				b.Name, behaviorName, b.Name, behaviorName)
		}
	}

	heartbeatBullet := "- `HEARTBEAT.md` — recurring prompt the agent fires at itself on a schedule.\n"
	if hasHeartbeat {
		heartbeatBullet = "- `HEARTBEAT.md` — recurring prompt the agent fires at itself on a schedule. *(Scaffold seeds one hourly; edit or delete as you like.)*\n"
	}

	return fmt.Sprintf(
		`# %s

WitwaveAgent configuration for `+"`%s`"+`, scaffolded by `+"`ww agent scaffold`"+`.

%s%s

This directory is gitSync-mounted into the running agent pod. When files
here change on the configured branch, the agent picks them up on the
next sync interval without a restart.

## Layout

`+"```"+`
%s`+"```"+`

## Next steps

- `+"`ww agent create %s`"+` — deploy on the cluster (if not already done).
- `+"`ww agent git add %s --repo <this-repo>`"+` — wire the running agent to pull from this directory.
- `+"`ww agent send %s \"hello\"`"+` — round-trip a test prompt.

Add scheduled work by dropping files under `+"`.witwave/`"+`:

%s- `+"`jobs/*.md`"+` — one-shot jobs fired when the agent boots.
- `+"`tasks/*.md`"+` — calendar-scheduled tasks (days, time window, date range).
- `+"`triggers/*.md`"+` — inbound HTTP trigger endpoints.
- `+"`continuations/*.md`"+` — follow-up prompts on upstream completion.
- `+"`webhooks/*.md`"+` — outbound webhook subscriptions.

Absence of any of these files means the agent does not use that feature.
The harness is quiet about dormant subsystems — file presence is the
enablement signal.
`,
		name, name, groupLine, backendLine, layout.String(), name, name, name, heartbeatBullet,
	)
}

// renderHeartbeat returns an hourly "HEARTBEAT_OK" heartbeat. Body is
// deliberately minimal — proving the dispatch path is cheaper than any
// prompt the scaffold could guess at. Users customise by editing; users
// who want no heartbeat at all scaffold with --no-heartbeat.
//
// Cron `0 * * * *` fires at the top of every hour. Matches what most
// users reach for when asked "schedule hourly" — easier to reason about
// than "every 3600 seconds from boot."
func renderHeartbeat() string {
	return `---
description: |
  Hourly heartbeat. Fires a prompt at the agent every hour at minute 0
  to exercise the dispatch path end-to-end and prove the backend sidecar
  is answering. Edit the schedule below, edit the body, or delete this
  file entirely to disable heartbeats.
schedule: "0 * * * *"
enabled: true
---

Respond HEARTBEAT_OK.
`
}

// renderAgentCard returns the A2A agent-card skeleton for a specific
// backend sidecar. Takes both the backend's name (for display + the
// "foo-1 vs foo-2" disambiguation multi-backend agents care about)
// and its type (what kind of backend it is). Format mirrors the text
// the backends' load_agent_description helper already reads — a plain
// markdown document whose first paragraph(s) become the A2A card
// description.
func renderAgentCard(agentName, backendName, backendType string) string {
	descriptor := backendType
	if backendName != backendType {
		descriptor = fmt.Sprintf("%s (%s)", backendName, backendType)
	}
	return fmt.Sprintf(
		`An autonomous agent named `+"`%s`"+`, running on the %s backend.

Edit this file to customise the A2A identity card that callers see at
`+"`/.well-known/agent-card.json`"+`. The full contents are surfaced verbatim as
the agent-card description.
`,
		agentName, descriptor,
	)
}

// renderBehaviorStub returns the behavioural-instructions stub for an
// LLM backend. Takes name + type so multi-backend agents get correctly
// labelled stubs — two claude backends named claude-primary and
// claude-fallback both get a CLAUDE.md but with distinct headers so
// git log readers can tell them apart.
//
// Deliberately minimal — we don't want to bias the agent's personality,
// just give the user a clearly-marked place to add their own prompt.
func renderBehaviorStub(agentName, backendName, backendType string) string {
	behaviorName, _ := behaviorFileName(backendType)
	return fmt.Sprintf(
		`# %s — %s

Behavioural instructions for the `+"`%s`"+` backend (type `+"`%s`"+`) on `+"`%s`"+`.
This file is loaded at container startup and injected as the agent's
system prompt.

Edit freely. The harness reloads `+"`%s`"+` on change (via gitSync pulls)
without restarting the pod.

## Identity

You are %s, an autonomous agent. Replace this section with the persona,
tone, and goals you want the agent to inhabit.

## Capabilities

List the tools, MCP servers, and scheduled routines this agent has access
to. Keep this section honest — hallucinated capabilities are a common
failure mode when the agent reasons about what it can do.

## Constraints

Note any hard rules (e.g. "never write to /etc", "only answer questions
about this codebase") the agent should enforce before acting.
`,
		strings.ToUpper(backendName[:1])+backendName[1:],
		strings.ToUpper(backendType[:1])+backendType[1:],
		backendName, backendType, agentName, behaviorName, agentName,
	)
}
