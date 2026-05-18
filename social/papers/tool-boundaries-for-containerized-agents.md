# Tool Boundaries for Containerized Agents

> Draft working paper on native tools, MCP tools, and project-specific toolchains for long-running AI agents.

---

## Executive summary

Containerized AI agents need tools. That sounds obvious until the agent is expected to maintain real software across
many repositories, languages, clouds, and operating environments.

A desktop agent inherits the developer workstation. If the human has `git`, `go`, Python, Rust, Node, `kubectl`, Helm,
cloud CLIs, browser access, local credentials, and package caches, the agent can often use them directly. That is
convenient, but ambient. The agent gets whatever the host happens to provide.

A containerized agent has no such default. Every capability must be deliberately supplied. The platform has to decide
where compilers live, where CLIs live, where credentials live, where source code is mounted, how commands are audited,
and how one model backend can work on projects that need very different development environments.

WitWave currently runs three real model backends: Claude, Codex, and Gemini. Each backend is its own image and its own
A2A server. The platform also has a shared `backend-base` image that gives those backends a common set of useful command
line tools: Go, Node, `kubectl`, `ww`, `gh`, Helm, ruff, shellcheck, hadolint, gitleaks, trivy, and related analysis and
test tooling. That shared base solved one problem: the three backend images no longer have to build the same baseline
utilities independently.

It did not solve the larger problem. A shared backend base is still a backend image. If every project-specific language
runtime, linter, build system, cloud integration, and operational tool keeps moving into that layer, the backend will
become a universal developer workstation image. That does not scale. The next Rust project, Node project, Java project,
Solana project, Foundry project, or Terraform-heavy platform repo should not force changes across Claude, Codex, and
Gemini.

The better boundary is:

- **Backend containers** run model-specific agent runtimes and protocol surfaces.
- **Toolchain containers** provide project-specific local execution environments.
- **MCP and other gateways** expose standardized tool calls into those environments or into external systems.

MCP is not the thing to avoid. MCP is a good protocol for this. A toolchain sidecar can expose an MCP server over
localhost, and the backend can call that server using the MCP support it already has. The important boundary is not "MCP
versus native." The important boundary is whether the language-specific runtime lives inside the backend image or inside
a dedicated execution container.

This paper argues for a hybrid model: keep backends small and stable, use toolchain containers for local project
execution, and use MCP or similar gateways to mediate both toolchain calls and external authority. The core goal is not
to remove native tools or avoid MCP. The core goal is to make tool placement explicit.

---

## The immediate question

The design question is:

> How should a containerized AI backend execute project-specific tools without baking every possible language and
> workflow into every backend image?

This is not hypothetical. WitWave itself is already a mixed Go and Python codebase with Helm charts, Kubernetes operator
code, static-site content, Dockerfiles, SOPS-encrypted secrets, release automation, and agent configuration. The current
backend images carry enough tooling to work on this repository. That is reasonable for WitWave today.

But WitWave is meant to run agents against more than this repository. Another workspace may need Rust. Another may need
Node. Another may need Java. Another may need AWS account tooling, Terraform, Foundry, Solana, mobile tooling, or a
private compiler. If the answer is always "install it in the backend image," then every new project capability creates
backend-image churn.

The cleaner question is not "MCP or native tools?" It is:

> Which capabilities belong in the model backend, which belong in a project execution environment, and which belong
> behind an external authority gateway?

That gives us a more useful design vocabulary.

---

## Terminology

### Backend

A backend is the model execution container. In WitWave today, the production backends are Claude, Codex, and Gemini.
Each backend is a standalone A2A server. Each owns its model SDK integration, session handling, conversation logs,
memory, metrics, protected inspection endpoints, and provider-specific runtime behavior.

The backend receives identity and behavior through mounted files:

- `CLAUDE.md` for Claude.
- `AGENTS.md` for Codex.
- `GEMINI.md` for Gemini.

The backend may expose provider-native tools. For example, Claude can use Claude Code-style tools such as read/search,
Bash, edit/write, and MCP depending on configured permissions. Codex can expose a local shell tool through its Agents
SDK integration. Gemini participates in the same backend layout and MCP configuration posture, though some lower-level
tool-loop interposition remains less mature than Claude and Codex.

The backend should know how to call tools. It should not be required to contain every possible project tool.

### Native tool

A native tool is directly available inside the container where the model backend is running. For a desktop agent, native
tools are host tools. For a containerized agent, native tools are binaries installed in the backend image or mounted
into that backend container.

Examples:

- `git`
- `gh`
- `ww`
- `kubectl`
- `helm`
- `go`
- `python`
- `pytest`
- `ruff`
- `cargo`
- `rustfmt`
- `terraform`
- `make`

Native tools are direct and familiar. They also couple the backend image to the project toolchain.

### MCP-mediated tool

An MCP-mediated tool is exposed by an MCP server and called through the Model Context Protocol. The server may run as a
cluster-shared service, a same-pod sidecar, or an external endpoint. The key feature is not where it runs; the key
feature is that the model sees a structured tool surface instead of arbitrary ambient shell access.

Examples:

- Kubernetes inspection or remediation tools.
- Helm release-management tools.
- Prometheus query tools.
- GitHub issue, pull request, or discussion tools.
- AWS account inspection tools.
- PagerDuty or incident-management tools.
- Toolchain execution tools such as `rust.cargo_test` or `python.pytest`.

MCP is strongest when the tool surface is intentionally described, bounded, observable, and policy-aware. That applies
to external systems, and it can also apply to local toolchain containers.

### Toolchain container

A toolchain container is a project-specific or language-specific execution environment mounted beside the backend. It
contains compilers, interpreters, package managers, linters, test runners, project utilities, caches, and related local
execution dependencies.

Examples:

- `toolchain-go-python`
- `toolchain-rust`
- `toolchain-node`
- `toolchain-terraform`
- `toolchain-witwave`

The backend cannot run a process inside a sibling container by default. Kubernetes containers in one pod share network
and volumes, but they do not share process environments. Therefore the toolchain container needs a deliberate execution
surface: usually an HTTP API, an MCP server, gRPC, or a small local daemon.

### External authority gateway

An external authority gateway is a service that holds or uses credentials for systems outside the workspace: Kubernetes,
AWS, GCP, GitHub, observability systems, ticketing systems, incident-management systems, or secrets systems.

A gateway may also expose MCP. The distinction is not the protocol. The distinction is the authority boundary.

A Rust toolchain and an AWS gateway can both expose MCP tools. The Rust toolchain boundary is the containerized project
execution environment. The AWS gateway boundary is credentials, account scope, audit, and blast radius.

---

## What we have today

WitWave already has several tool surfaces. They are useful, but they are not yet a first-class toolchain layer.

### One deployable agent, multiple containers

A named WitWave agent is deployed as a pod-shaped unit:

- A **harness** container receives A2A traffic, schedules heartbeats, runs jobs/tasks/triggers/continuations, and routes
  work to a backend according to `.witwave/backend.yaml`.
- One or more **backend** containers run model-specific A2A servers. The common production shape is Claude, Codex, and
  Gemini sidecars.
- Optional **git-sync** sidecars materialize repository content into the pod.
- Optional **MCP tools** run as separate deployments/services today, with chart and operator support for MCP tool
  rendering.
- Workspace volumes can be mounted into participating backend containers through `WitwaveWorkspace` references.

The repo remains the source of truth. Agent identity, routing, prompts, schedules, backend-specific instructions,
settings, MCP configuration, skills, docs, and website content all live in git. At runtime, git-sync and workspace
mounts make those files visible to containers at stable paths. This is an important design point: the agent is not
configured by hand inside the pod. The pod is a runtime projection of repo-managed state.

### Three backend images plus one shared backend base

WitWave currently maintains separate backend images for:

- Claude.
- Codex.
- Gemini.

Those images share `images/backend-base/`, published as `ghcr.io/witwave-ai/images/backend-base:<version>`. The base
image includes common CLIs, runtimes, and analyzers such as Go, Node, `kubectl`, `ww`, `gh`, Helm, ruff, shellcheck,
hadolint, gitleaks, trivy, and test tooling.

That shared base is useful. It removed duplicated installation work across the three backend Dockerfiles and keeps
common tool versions pinned in one place.

But it is still part of the backend-image family. It is not a separate project execution environment. If a new language
or linter goes into `backend-base`, that capability still lands in every backend that inherits from it. That can be the
right short-term move for common platform tools. It should not become the default answer for every project-specific
runtime.

The base image also does not grant authority. Installing `kubectl` does not grant cluster access. In-cluster Kubernetes
authority is controlled separately through `WitwaveAgent.spec.kubernetesApiAccess` or explicit ServiceAccount/RBAC
configuration.

### Claude tool execution

The Claude backend uses Claude Code-style tool configuration. Its default posture is conservative: read/search tools are
allowed by default, while Bash, Write/Edit, and WebFetch require explicit enablement through `ALLOWED_TOOLS` or
`.claude/settings.json` `permissions.allow`.

Claude also reads `.claude/mcp.json` through `MCP_CONFIG_PATH`. The backend validates and hot-reloads MCP configuration.
For stdio MCP entries, commands are passed through the shared MCP command allowlist before they can spawn inside the
backend container.

So Claude already has two relevant execution surfaces:

- Provider-native tools such as Bash/read/edit when enabled.
- MCP tools described in `.claude/mcp.json`.

Both execute from the Claude backend's point of view. Neither creates a separate project execution environment by
itself.

### Codex tool execution

The Codex backend reads `.codex/config.toml` for built-in tool flags and `.codex/mcp.json` for MCP servers.

Its local shell tool is implemented by a `LocalShellTool` executor that runs `subprocess.run(...)` inside the Codex
backend container. The shell path has baseline denial rules, audit logging, timeouts, environment sanitization, and
trace instrumentation. The important part for this paper is simple: when Codex runs a shell command today, the process
runs in the Codex backend container.

Codex also loads MCP configuration, validates MCP config paths, and applies the same shared stdio MCP command allowlist
and argument safety checks used by the other backends.

So Codex also has two relevant execution surfaces:

- A direct local shell inside the backend image.
- MCP tools described in `.codex/mcp.json`.

Again, neither is a separate toolchain layer yet.

### Gemini tool execution

The Gemini backend follows the same high-level backend pattern: mounted identity document, memory/log layout, MCP
configuration shape, metrics, and protected backend endpoints. Its MCP and hook surfaces are being kept aligned with the
other backends where possible.

Gemini matters for this design even where its local tool loop is less mature, because any toolchain architecture should
not require each backend to reinvent project execution. If a toolchain sidecar exposes MCP tools, Gemini can eventually
use the same described tool surface as Claude and Codex rather than needing Gemini-specific Rust, Go, Node, or Terraform
images.

### MCP components

WitWave currently ships MCP components under `tools/`:

- `mcp-kubernetes` for Kubernetes API access.
- `mcp-helm` for Helm release management.
- `mcp-prometheus` for Prometheus queries.

These run long-lived FastMCP HTTP servers on port `8000`, expose `/health`, enforce bearer-token auth through shared
middleware unless explicitly disabled, and are consumed by backend `mcp.json` entries. Chart-rendered MCP tools are
cluster services such as `http://<release>-mcp-kubernetes:8000`, not binaries inside a backend container.

WitWave also protects stdio MCP entries. A malicious or misreviewed `mcp.json` should not be able to spawn arbitrary
binaries inside a backend pod. The shared `mcp_command_allowlist` limits accepted commands and rejects unsafe
interpreter argument shapes such as inline code, stdin scripts, unsafe `uv`/`uvx` patterns, or positional scripts
outside explicitly allowed paths.

This posture is useful for a future toolchain design: if toolchains expose MCP, the platform already has concepts for
MCP config, reload, authentication, body caps, command allowlists, and metrics. But the current MCP tools are mostly
external gateways. They do not yet solve local project execution.

### Shared filesystem and repo-as-source

WitWave relies heavily on repo-managed files and stable mounted paths:

- `.witwave/` contains runtime harness configuration such as `backend.yaml`, `HEARTBEAT.md`, jobs, tasks, triggers,
  continuations, webhooks, and the public agent card.
- `.claude/`, `.codex/`, and `.gemini/` contain backend-specific identity and tool configuration.
- `.agents/` stores self and test team definitions, including per-agent behavior files and SOPS-encrypted secret
  mirrors.
- `WitwaveWorkspace` can provision shared volumes and stamp shared config files and existing Secrets onto participating
  agents.
- Runtime memory, logs, and conversation state are persisted through backend/harness storage paths rather than being
  baked into images.

This matters because a toolchain container does not need a separate idea of the project. It needs the same source tree
and workspace files mounted at the same path as the backend. The repo remains the contract. The toolchain is just a
better place to execute project-local tools against that contract.

### Kubernetes API access is separate from tools

WitWave now has `spec.kubernetesApiAccess` for agents. When enabled, the operator creates a per-agent ServiceAccount,
namespace-scoped Role, and RoleBinding. The current presets distinguish read-only inspection from bounded
namespace-write remediation.

This is a useful precedent: image composition and authority are separate. The backend image can contain `kubectl`, but
`kubectl` only works if the pod has a Kubernetes identity with matching RBAC. The same principle should apply to future
toolchains. A toolchain image may contain a powerful binary, but credentials and external authority should be separate,
explicit, and preferably mediated.

### What is missing

The missing layer is not MCP support. The missing layer is a first-class execution environment abstraction.

Today there is no:

- `toolchains:` block on `WitwaveAgent` or `WitwaveWorkspace`.
- Operator or chart renderer for toolchain sidecars.
- Standard toolchain image contract.
- Generated backend MCP config pointing to local toolchain sidecars.
- Common `toolchaind` or structured MCP server for project-local execution.
- Policy model for command, cwd, timeout, output, environment, network, and secrets at the toolchain boundary.
- Trace model that distinguishes local project execution from external authority calls.
- Routing model that explains why `cargo test` ran in the Rust toolchain and `pytest` ran in the Python toolchain.

That is the actual gap this paper is about.

---

## Why backend images should not become universal toolboxes

The backend images have a clear job: run model backends reliably.

Claude needs the Claude runtime. Codex needs the OpenAI Agents SDK runtime. Gemini needs the Google Gemini runtime. Each
backend already has provider-specific dependencies, provider-specific configuration, provider-specific tool behavior,
provider-specific metrics, and provider-specific failure modes.

If every tool lives inside those backends, every new capability becomes a three-image maintenance problem. Supporting a
new language, adding a linter suite, introducing a build system, or wiring a new external integration means updating and
releasing Claude, Codex, and Gemini images.

The shared `backend-base` image helps with common baseline tools, but it does not remove the matrix. It just moves the
shared part of the matrix into a common parent. For truly common platform utilities, that is useful. For
project-specific toolchains, it is still the wrong center of gravity.

For example, if a project needs Rust support and the project can run on all three backends, the naive backend-image
strategy creates this product surface:

```text
claude + rust
codex + rust
gemini + rust
```

If the next project needs Node and Terraform:

```text
claude + node + terraform
codex + node + terraform
gemini + node + terraform
```

If another project needs Go, Python, Rust, Foundry, and Helm:

```text
claude + go + python + rust + foundry + helm
codex + go + python + rust + foundry + helm
gemini + go + python + rust + foundry + helm
```

That turns tool support into this matrix:

```text
backend type x project toolchain x version x security posture
```

The failure modes are predictable:

- Backend images grow large.
- Build times increase.
- CVE surface expands.
- Toolchain version drift becomes harder to reason about.
- Provider runtime changes are coupled to project language changes.
- Project portability suffers because each repo wants a custom backend image.
- Ownership becomes unclear: should the Claude backend image own Rust versioning?

Dedicated toolchain containers break that multiplier. The backend images stay small, generic, and stable. Adding Rust
support means creating or updating one Rust toolchain container, not rebuilding Claude, Codex, and Gemini. Adding a new
linter suite means updating the relevant project toolchain. Adding a new external integration means creating a gateway
or toolchain container with its own policy and release cadence.

The backend should know how to call tools. It should not need to contain every tool.

---

## The sidecar toolchain model

The proposed model is to place project execution environments beside the backend, not inside it.

```text
agent pod
├── harness
├── claude-backend
├── codex-backend
├── gemini-backend
├── toolchain-go-python
├── toolchain-rust
└── shared workspace volume
```

Each toolchain container has:

- A project-specific or language-specific image.
- The shared workspace mounted at a known path.
- A small local execution service.
- A localhost port inside the pod.
- Structured tools or tightly controlled command execution.
- Command, cwd, timeout, output, and environment policy.
- Audit logs and metrics.
- Resource requests and limits independent of the backend.

The backend calls the toolchain service. It does not directly spawn toolchain processes.

For example, a Rust toolchain sidecar might expose an MCP tool equivalent to:

```json
{
  "tool": "rust.cargo_test",
  "arguments": {
    "cwd": "/workspaces/witwave/source",
    "package": "operator",
    "timeoutSeconds": 300
  }
}
```

Or it might expose a more generic but constrained execution tool:

```json
{
  "tool": "toolchain.exec_allowed",
  "arguments": {
    "command": ["cargo", "test"],
    "cwd": "/workspaces/witwave/source",
    "timeoutSeconds": 300
  }
}
```

The toolchain container runs the command inside the Rust environment and returns structured output:

```json
{
  "exitCode": 0,
  "stdout": "...",
  "stderr": "",
  "durationMs": 18420,
  "truncated": false
}
```

This keeps the Rust compiler in the Rust environment, keeps the LLM runtime in the backend environment, and keeps the
source tree as the shared contract between them.

---

## How the backend would execute toolchain work

There are several possible integration paths. They can coexist, but they should not be treated as equally desirable.

### Option 1: Toolchain sidecars expose MCP

This is the most practical first implementation.

The backends already know how to consume MCP configuration. Claude, Codex, and Gemini use the same broad `mcp.json`
shape. A toolchain sidecar can run an MCP server over HTTP on localhost, and the chart/operator layer can render backend
MCP entries that point at those local sidecars.

Example generated backend MCP configuration:

```json
{
  "mcpServers": {
    "toolchain-go-python": {
      "url": "http://localhost:8701/mcp"
    },
    "toolchain-rust": {
      "url": "http://localhost:8702/mcp"
    }
  }
}
```

The Rust toolchain MCP server might expose:

```text
rust.cargo_check
rust.cargo_test
rust.rustfmt_check
rust.clippy
toolchain.exec_allowed
```

The Go/Python toolchain MCP server might expose:

```text
go.test
go.vet
python.pytest
python.ruff_check
python.ruff_format_check
toolchain.exec_allowed
```

This is not a compromise or a misuse of MCP. MCP can be the clean, standardized communication protocol between the
backend and the toolchain container. The important boundary is the container boundary: language-specific tools live in a
dedicated execution environment rather than in the backend image.

Benefits:

- Reuses existing backend MCP support.
- Avoids immediate per-backend native tool implementation.
- Gives each toolchain a described model-facing tool surface.
- Allows multiple named toolchains in one pod.
- Makes tool availability explicit to the model.
- Fits existing MCP auth, body-cap, metrics, audit, and reload patterns.

Risks and controls:

- Generic `exec` remains powerful and needs strict policy.
- Tool descriptions must be precise and safe.
- CWD must be restricted to approved workspace paths.
- Commands and arguments need allowlists or structured wrappers.
- Timeouts and output caps are mandatory.
- Toolchain names and descriptions must be clear enough that the model can choose correctly.

This is the best MVP path.

### Option 2: Backend-native `run_toolchain` tool

The backend could expose a provider-native tool named `run_toolchain` and call a local toolchain HTTP API directly.

Example:

```json
{
  "toolchain": "rust",
  "command": ["cargo", "test"],
  "cwd": "/workspaces/witwave/source",
  "timeoutSeconds": 300
}
```

Benefits:

- Clear product abstraction.
- Backend traces can represent toolchain calls consistently.
- A central backend policy layer can mediate calls.
- The toolchain contract is not dependent on MCP.

Costs:

- Requires implementation in each backend.
- Claude, Codex, and Gemini have different native tool APIs.
- Slower to ship.
- Adds another protocol surface to maintain.

This may become attractive later, but it is not the simplest first implementation.

### Option 3: Backend uses `kubectl exec` into a sibling container

This is the tempting shortcut:

```bash
kubectl exec <pod> -c toolchain-rust -- cargo test
```

It should remain a debugging technique, not a platform primitive.

It requires the backend to have `pods/exec` permission. It turns Kubernetes into the command-execution API. It is harder
to audit as an agent tool action. It couples local project execution to Kubernetes API authority even though the
containers already share a pod. It also encourages granting broad Kubernetes capabilities to a model-facing backend.

### Option 4: Project-specific backend images

A project can still build a custom backend image:

```dockerfile
FROM ghcr.io/witwave-ai/images/claude:0.27.1

RUN install-rust-toolchain
RUN install-project-tools
```

This is simple and sometimes useful. It should remain an escape hatch, not the default design. It repeats work across
backend types, bloats backend images, and couples project toolchains to provider runtimes.

### Option 5: Ephemeral runner jobs

Heavy or risky execution can move into short-lived Kubernetes Jobs:

```text
run cargo test in image ghcr.io/org/project-rust-toolchain:sha
mount workspace snapshot
return logs and exit code
```

This is useful for expensive builds, matrix tests, integration tests, or untrusted workloads. It gives stronger
isolation and cleaner resource lifecycle. It is also slower and more complex than a sidecar, so it should complement the
sidecar model rather than replace it at first.

---

## MCP as protocol, toolchain as boundary

MCP is a protocol. Toolchain is an architectural role.

It is reasonable for a Rust toolchain, a Go/Python toolchain, a Prometheus gateway, and an AWS gateway to all expose MCP
tools. The question is not whether local toolchains are allowed to use MCP. They are. The question is whether the
platform preserves the right boundary behind the protocol.

For toolchains, the boundary is local execution:

- Workspace-mounted source code.
- Language runtimes.
- Compilers.
- Linters.
- Test runners.
- Package caches.
- Local build scripts.

For external systems, the boundary is authority:

- Credentials.
- Account scope.
- Cluster scope.
- Read/write posture.
- Audit.
- Blast radius.

The same protocol can serve both layers. The layer still matters.

| Capability              | Primary concern          | Better conceptual bucket      |
| ----------------------- | ------------------------ | ----------------------------- |
| `cargo test`            | Local project execution  | Toolchain                     |
| `go test ./...`         | Local project execution  | Toolchain                     |
| `ruff format`           | Local project mutation   | Toolchain                     |
| `make test`             | Local project execution  | Toolchain                     |
| `kubectl get pods`      | Cluster authority        | Native tool or MCP gateway    |
| `helm template`         | Local chart rendering    | Toolchain                     |
| `helm upgrade`          | Cluster mutation         | MCP gateway or controlled CLI |
| `aws sts assume-role`   | Cloud authority          | MCP/gateway                   |
| `aws s3 ls prod-bucket` | Cloud authority/data     | MCP/gateway                   |
| `gh issue create`       | Source-control authority | MCP/gateway or native CLI     |
| `prometheus query`      | Observability access     | MCP/gateway                   |

The important claim is modest: use MCP where it helps, but do not let the protocol hide whether a tool is local
execution or external authority.

---

## Recommended first design

The first design should be deliberately small:

> Add named toolchain sidecars that expose structured MCP tools over localhost.

The platform primitive should be `toolchains`. The first transport can be MCP.

Example future agent spec:

```yaml
spec:
  toolchains:
    - name: go-python
      image:
        repository: ghcr.io/witwave-ai/toolchains/go-python
        tag: "0.1.0"
      port: 8701
      mountPath: /workspaces/witwave/source
      tools:
        mode: structured
      allowedCommands:
        - go
        - python
        - pytest
        - ruff
        - make

    - name: rust
      image:
        repository: ghcr.io/witwave-ai/toolchains/rust
        tag: "0.1.0"
      port: 8702
      mountPath: /workspaces/witwave/source
      tools:
        mode: structured
      allowedCommands:
        - cargo
        - rustc
        - rustfmt
        - clippy
```

The operator would render:

- One sidecar per toolchain.
- Workspace mounts into each toolchain sidecar.
- Backend MCP config entries for local toolchain URLs.
- Per-toolchain resource requests and limits.
- Per-toolchain security context.
- Optional environment variables describing toolchain names and URLs.
- Optional network-policy controls.

The sidecar would provide:

- `/health`.
- `/metrics`.
- An MCP endpoint.
- Structured tools.
- Optional constrained `exec_allowed`.
- CWD restriction.
- Timeout enforcement.
- Output caps.
- Audit logs.

The backend would not need to know that Rust is installed in its own filesystem. It would only need to know that a
`toolchain-rust` MCP server exists and exposes safe Rust tools.

---

## Multiple execution environments

Multiple toolchains should be supported, but bounded.

It is reasonable for an agent pod to have a small number of named toolchain containers:

```text
toolchain-go-python
toolchain-rust
toolchain-node
toolchain-terraform
```

It is not reasonable for an agent pod to have hundreds. Each toolchain has image pulls, resource requests, patch
cadence, security posture, logs, metrics, and operational overhead.

The design should optimize for:

- One default project toolchain.
- Optional language-specific toolchains for heavy runtimes.
- Optional high-risk toolchains with tighter network or secret posture.
- Optional runner jobs for expensive matrix work.

Toolchain selection should start explicit:

```text
Use the Rust toolchain to run cargo test.
Use the Go/Python toolchain to run ruff and pytest.
Use the Terraform toolchain to run terraform fmt and validate.
```

Later, the platform can add routing hints:

```yaml
handles:
  files:
    - Cargo.toml
    - "*.rs"
  commands:
    - cargo
    - rustc
    - rustfmt
    - clippy
```

Automatic selection should be explainable and traceable. If an agent runs `cargo test`, the trace should show:

```text
toolchain selected: rust
selection reason: command cargo matched toolchain rust handles.commands
```

Hidden routing should wait until traces show the common patterns. If the model asks for `make test`, the platform should
not silently guess between Go/Python, Rust, Node, or project-default toolchains without leaving an explanation.

---

## Security posture

Toolchain execution is powerful. It is local code execution against a shared workspace. That should be treated as a
high-trust but bounded capability.

Minimum controls:

- **No cluster credentials by default.** Toolchain containers should not automatically receive Kubernetes service
  account tokens.
- **No cloud credentials by default.** AWS, GCP, Azure, GitHub, and other external authority should be mediated
  separately unless explicitly required.
- **CWD restriction.** Commands should run only inside approved workspace paths.
- **Structured tools first.** Prefer `rust.cargo_test` or `python.pytest` over a generic shell when possible.
- **Command allowlist.** If generic exec exists, restrict approved binaries.
- **Argument policy.** Interpreters and package runners need special handling because `python -c`, `node -e`, `bash -c`,
  `uvx`, `npx`, and similar forms can defeat naive command allowlists.
- **Timeouts.** Every execution needs a bounded timeout.
- **Output caps.** Large output should be truncated with clear metadata.
- **Audit logs.** Record toolchain name, backend, session hash, command or structured tool, cwd, exit code, duration,
  truncation, and policy decisions.
- **Resource limits.** Toolchains should have CPU and memory requests/limits independent of the backend.
- **Network posture.** Some toolchains need package download access; others should be egress-restricted.
- **Secret posture.** Secrets required for external systems should not be casually mounted into local build toolchains.

The platform should assume prompt injection can cause a backend to call available tools. The answer is not to provide no
tools. The answer is to place tools behind boundaries that match their risk.

---

## Relationship to dev containers

The Dev Container ecosystem is relevant because many repositories already describe their development environment in
`.devcontainer/devcontainer.json`.

A future WitWave toolchain design could support:

```yaml
toolchains:
  - name: default
    devcontainer:
      path: .devcontainer/devcontainer.json
```

This would let projects reuse an existing development-container definition instead of writing a WitWave-specific
execution image from scratch.

Dev containers are not a complete solution by themselves. The platform still needs:

- A backend-to-toolchain call path.
- A model-facing tool surface.
- Audit logs.
- Timeouts and output caps.
- Secret and network policy.
- Support for more than one execution environment.

Dev containers can help define the image. They do not define the agent execution contract.

---

## Relationship to MCP gateways

Toolchains should sit beside MCP gateways, not replace them.

A possible deployment could look like this:

```text
Local project execution:
  toolchain-go-python
  toolchain-rust
  toolchain-node

External authority:
  mcp-kubernetes
  mcp-helm
  mcp-prometheus
  mcp-aws-prod-readonly
  mcp-github-discussions
```

Some tools have both local and authority-bearing modes.

Kubernetes:

- `kubectl` in a backend or toolchain container is useful for diagnostics and parity with human workflows.
- Production cluster access is safer behind scoped identity, RBAC, and often an MCP gateway.

Helm:

- `helm template` and `helm lint` are local chart work and fit a toolchain.
- `helm upgrade` against a live cluster is external authority and may belong behind a gateway or a tightly controlled
  native path.

AWS:

- Native `aws` CLI can work for one account with one scoped identity.
- Multi-account role switching is easier to audit when it is represented as a gateway surface scoped by account,
  environment, and permission tier.

Prometheus:

- Prometheus is mostly observability access, so MCP is a natural fit.
- The value comes less from where the client binary lives and more from scoping query access, limiting response size,
  and auditing what the agent asked.

---

## Draft implementation stages

### Stage 1: Keep the paper as a design record

Name the boundaries clearly:

- Backend runtime.
- Toolchain execution.
- External authority gateway.

Use this paper to avoid re-litigating the same boundary every time a new language or CLI appears.

### Stage 2: Build one local toolchain sidecar

Build a simple `toolchain-go-python` sidecar for the current repo.

It should:

- Run as a sidecar.
- Mount the same source/workspace volume as the backend.
- Expose `/health` and `/metrics`.
- Expose an MCP server over localhost.
- Provide structured tools for the current repo's common checks.
- Record audit logs.
- Enforce timeouts and output caps.

Initial tools could be:

```text
go_test
go_test_package
python_pytest
python_ruff_check
python_ruff_format_check
repo_command_allowed
```

Avoid starting with a wide-open shell.

### Stage 3: Add chart/operator support

Add a `toolchains` block to the chart and operator.

Minimum fields:

- name
- image
- port
- workspace mount path
- resources
- env
- allowed commands or structured mode
- enabled flag

The operator should render sidecars and backend MCP config entries.

### Stage 4: Add a second language toolchain

Add `toolchain-rust`.

This proves the design is not hardcoded to the current Go/Python repo.

Initial tools:

```text
cargo_check
cargo_test
rustfmt_check
clippy
```

### Stage 5: Add routing hints

Add optional metadata:

```yaml
handles:
  commands: ["cargo", "rustc", "rustfmt", "clippy"]
  files: ["Cargo.toml", "*.rs"]
```

Expose hints to the model and traces first. Add automatic routing only after usage patterns are clear.

### Stage 6: Add runner jobs for heavy execution

For expensive builds, integration tests, matrix runs, or untrusted code, use short-lived runner pods or jobs rather than
long-running sidecars.

---

## Open questions

### Should toolchains be per-agent or per-workspace?

Per-agent is easier to render because agents already own their pods.

Per-workspace is conceptually cleaner because toolchains are project-specific, not personality-specific. Multiple agents
bound to the same workspace probably want the same execution environments.

A practical path is to support per-agent toolchains first and leave room for workspace-level defaults later.

### Should package caches be shared?

Probably, eventually. Language package caches are expensive to rebuild.

But shared caches introduce state, invalidation, poisoning, and storage concerns. The first version can tolerate slower
execution in exchange for simpler semantics.

### Should toolchains have network access?

Some must. Package managers need network access unless dependencies are vendored or cached.

But network access changes the risk model. A toolchain running project scripts with broad egress is a larger attack
surface than one limited to local compilation. Network policy should become part of the toolchain spec.

### How generic should the execution tool be?

The safest first tools are structured: `cargo_test`, `go_test`, `python_pytest`, `ruff_check`.

A generic `exec_allowed` tool may still be necessary, but it should be treated as a controlled escape hatch with strict
command, cwd, argument, timeout, output, and audit policy.

### How much should the model choose automatically?

Not much at first.

The model can choose from clearly named tools. The platform can later add routing hints and defaults. Hidden automatic
routing should wait until traces show common patterns.

---

## Conclusion

Containerized agents need a tool model that respects three different responsibilities: model runtime, project-local
execution, and external authority.

WitWave already has the pieces that make this problem visible. It has three real backends. It has a shared backend base
with useful native tools. It has MCP gateways. It has git-backed agent configuration. It has shared workspaces. It has a
repo-as-source operating model. Those choices are coherent, but they also reveal the next boundary: project-specific
execution should not keep accumulating inside backend images.

The next architectural step is to make toolchains first-class. A toolchain container gives an agent the right compiler,
interpreter, linter, test runner, package manager, and local execution environment for the workspace it is maintaining.
MCP can be the standard way the backend calls into that container. The backend stays focused on model execution. The
repo remains the source of truth. External systems remain behind explicit gateways and credentials.

That boundary keeps the system understandable as it grows. Claude, Codex, and Gemini should not each become every
possible developer workstation. They should be stable model runtimes that can reach the right execution environment for
the job in front of them.
