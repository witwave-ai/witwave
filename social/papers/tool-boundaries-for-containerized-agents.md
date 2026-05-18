# Tool Boundaries for Containerized Agents

> Draft working paper on native tools, MCP tools, and project-specific toolchains for long-running AI agents.

---

## Executive summary

Containerized AI agents need tools. That statement sounds simple until the agent is expected to maintain real software
over time.

A local desktop agent can often use whatever is already installed on the developer's machine: a shell, a filesystem,
language runtimes, `git`, package managers, cloud CLIs, Kubernetes CLIs, browser access, and local configuration. The
agent inherits the developer workstation as its execution environment. That inheritance is convenient, but it is also
ambient: the agent gets whatever the host happens to have, with whatever credentials and operating assumptions are
present.

A containerized agent has a different problem. Its tools have to be deliberately provided. If the agent needs `go`,
Python, Rust, Node, Java, `kubectl`, `helm`, `gh`, `terraform`, `sops`, `age`, `make`, or a project-specific build
system, those capabilities must exist somewhere in the deployment. Putting all of them into every backend image is not a
durable answer. A backend image that starts as "the Claude runtime" or "the Codex runtime" eventually becomes a
universal development workstation image, and universal development workstation images do not stay small, secure, or
project-neutral for long.

MCP helps with this problem in two related ways. The Model Context Protocol is a good fit for mediated access to
external systems: Kubernetes APIs, cloud accounts, observability platforms, ticketing systems, source-control APIs, and
other authority-bearing services. It is also a practical, acceptable communication protocol between backend containers
and local toolchain containers. It gives the backend a standardized model-facing tool surface without forcing every
language runtime into the backend image.

The distinction is architectural, not anti-MCP. Compiling Rust, running `go test`, formatting Python, or executing a
repository's `make` target are local toolchain problems. They need the right filesystem, dependencies, compilers,
package caches, environment variables, and runtime libraries close to the workspace. MCP can be the clean way to call
into that environment; the important boundary is that the environment lives outside the backend image.

This paper argues for a three-layer model:

- **Backend containers** run the LLM integration and agent protocol.
- **Toolchain containers** provide project-specific local execution environments.
- **MCP and other gateways** mediate access to external systems and sensitive authority boundaries.

The practical bridge can use MCP from the start. A toolchain sidecar can expose an MCP server over localhost, and the
backend can call it using the MCP integration it already has. The product concept should still remain distinct:
toolchains are the project-local execution environments; MCP is one clean protocol for reaching them. Keeping that
distinction clear prevents the platform from collapsing back into a bloated backend image.

---

## The immediate question

The design question is:

> How should a containerized AI backend execute project-specific tools without baking every possible language and
> workflow into the backend image?

The current system already knows how to run model backends. It also knows how to attach shared volumes, mount
workspaces, run git-sync sidecars, expose MCP tools, and grant carefully scoped Kubernetes API access. What it does not
yet have is a first-class "execute this command in a sibling project toolchain container" concept.

That gap matters because the backend is the wrong place to accumulate all development tools.

Today a project might need Go and Python. Tomorrow another project might need Rust, Node, Java, .NET, Terraform,
Ansible, Foundry, Solana tooling, or a private compiler. If every backend image has to include every possible language
toolchain, the backend stops being a portable agent runtime. It becomes a giant mutable build image with an LLM inside
it.

The right boundary is not "MCP versus native tools." The more useful boundary is:

> **Local execution tools** versus **external authority tools**.

Local execution tools operate on the workspace. External authority tools act on systems outside the workspace.

That distinction gives us a cleaner design vocabulary.

---

## Terminology

### Backend

A backend is the model execution container: Claude, Codex, Gemini, or another LLM runtime. It owns the model SDK,
conversation state, memory, tool-call plumbing, session binding, metrics, and agent-facing A2A server behavior.

The backend may expose native model tools such as Bash, file read/write, edit, or MCP client support, depending on the
provider and implementation. It should not be required to contain every project language runtime.

### Native tool

A native tool is a command, API, or capability available directly inside the environment where the backend is executing.
For a desktop agent, native tools are usually host tools. For a containerized agent, native tools are tools installed
inside the backend container image or mounted into that container.

Examples:

- `git`
- `gh`
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

Native tools are fast and natural for development work, but they couple the backend image to the project toolchain.

### MCP-mediated tool

An MCP-mediated tool is exposed by an MCP server and called through the Model Context Protocol. The tool may run in the
same pod, in the same namespace, elsewhere in the cluster, or outside the cluster entirely.

Examples:

- Kubernetes inspection tool
- Helm release-management tool
- Prometheus query tool
- GitHub issue/discussion tool
- AWS account inspection tool
- PagerDuty tool
- ticketing-system tool

MCP is strongest when the tool is not merely "run this local command," but instead "perform a bounded action against a
system with its own identity, policy, and audit requirements."

### Toolchain container

A toolchain container is a project-specific execution environment mounted beside the backend. It contains the compilers,
interpreters, package managers, linters, test runners, and project-local utilities needed to work on a given repository
or class of repositories.

Examples:

- `toolchain-go-python`
- `toolchain-rust`
- `toolchain-node`
- `toolchain-terraform`
- `toolchain-witwave`

The backend does not run commands inside this container directly by magic. Containers in the same Kubernetes pod share a
network namespace and volumes, but they do not share process environments. Therefore the toolchain container needs a
small execution surface: an HTTP API, an MCP server, a gRPC service, or another deliberate bridge.

### External authority gateway

An external authority gateway is a tool service that holds or uses credentials for systems outside the workspace:
Kubernetes, AWS, GCP, GitHub, observability systems, ticketing systems, or incident-management systems. MCP is a strong
candidate for this layer because it gives the model a constrained tool surface rather than handing it raw credentials
and a shell.

The same binary might be available as a native CLI and as a mediated gateway. For example, `kubectl` can be installed in
a toolchain container for local diagnostics, while an MCP Kubernetes gateway can expose a narrower, audited subset of
cluster actions.

---

## What we have today

The current architecture already contains several relevant pieces:

- Backend containers can expose model-native tools.
- Claude can use Claude Code-style tools and MCP entries.
- Codex has a local shell executor that runs subprocesses inside the Codex backend container.
- Backends can read MCP configuration.
- MCP stdio commands are guarded by command allowlists.
- Agent pods already contain multiple containers: harness, backends, git-sync sidecars, and optional MCP/tool-related
  containers.
- Shared storage and workspace references can mount files into backend containers.
- Kubernetes API access can be granted at the agent level with read-only or namespace-write modes.

What is missing is a first-class execution environment abstraction:

- No `toolchains:` block exists on the agent spec.
- No operator rendering exists for toolchain sidecars.
- No generated MCP config points a backend at a local toolchain sidecar.
- No common `toolchaind` execution API exists.
- No routing policy tells a backend which execution environment should handle `cargo test` versus `go test`.
- No audit model separates "the model asked to run a local build" from "the model asked to act against an external
  system."

That does not mean the design is blocked. It means the next layer needs to be made explicit.

---

## Why backend images should not become universal toolboxes

A backend image has a clear job: run the model backend reliably.

For Claude, that means the Claude Agent SDK or Claude Code execution surface, session handling, memory, metrics, hooks,
MCP configuration, auth, and the A2A server. For Codex, it means the OpenAI Agents SDK backend, shell tool integration,
session handling, memory, metrics, and the A2A server. Gemini has its own provider-specific version of the same problem.

WitWave maintains three separate backend images: Claude, Codex, and Gemini. If every tool lives inside those backends,
then every new capability becomes a three-image maintenance problem. Supporting a new programming language, adding a new
set of linters, introducing a new build system, or wiring in a new external integration would require updating and
releasing all three backend images.

That approach becomes unsustainable quickly. Each backend already has provider-specific complexity. Tooling should not
multiply that complexity across every backend type.

For example, if a project needs Rust support and the project can run on Claude, Codex, and Gemini, then the naive
backend-image strategy creates three Rust-capable backend images:

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

If a third project needs Go, Python, Rust, Foundry, and Helm:

```text
claude + go + python + rust + foundry + helm
codex + go + python + rust + foundry + helm
gemini + go + python + rust + foundry + helm
```

This turns the backend matrix into a product problem:

```text
backend type x project toolchain x version x security posture
```

That matrix grows quickly and creates several failure modes:

- Large backend images.
- Slow image builds.
- More CVE surface.
- More cache invalidation.
- More language-specific maintenance in provider-specific images.
- Harder reproducibility.
- Confusing ownership: is the Claude backend team responsible for Rust versioning?
- Harder project portability: each repo needs a custom backend image rather than a custom execution environment.

Dedicated toolchain containers break that multiplier. The backend images can stay small, generic, and stable while the
project-specific capabilities move into one purpose-built execution environment. Adding Rust support should mean
creating or updating one Rust toolchain container, not rebuilding Claude, Codex, and Gemini. Adding a new linter suite
should mean updating the relevant project toolchain, not touching every model backend. Adding a new external integration
should be handled by a dedicated toolchain or gateway container with its own policy and release cadence.

The backend should know how to use tools. It should not need to contain every tool.

---

## The sidecar toolchain model

The proposed model is to place project execution environments beside the backend, not inside it.

```text
agent pod
├── harness
├── claude-backend
├── codex-backend
├── toolchain-go-python
├── toolchain-rust
└── shared workspace volume
```

Each toolchain container has:

- A project-specific or language-specific image.
- The shared workspace mounted at a known path.
- A small local execution service.
- A port on localhost inside the pod.
- Command allowlists or structured tools.
- Output limits.
- Timeouts.
- Audit logs.
- Resource requests and limits.

The backend calls the toolchain service rather than trying to run the toolchain process itself.

For example:

```http
POST http://localhost:8702/run
Content-Type: application/json

{
  "command": ["cargo", "test"],
  "cwd": "/workspaces/witwave/source",
  "timeoutSeconds": 300
}
```

The Rust toolchain sidecar runs the command inside the Rust container and returns:

```json
{
  "exitCode": 0,
  "stdout": "...",
  "stderr": "",
  "durationMs": 18420,
  "truncated": false
}
```

This keeps the Rust compiler in the Rust environment and the LLM runtime in the backend environment.

---

## How the backend would execute toolchain work

There are several possible integration paths. They are not equivalent.

### Option 1: Toolchain sidecars expose MCP

This is the most practical first implementation because the backends already understand MCP.

In this model, each toolchain sidecar runs an MCP server over HTTP. The operator renders the sidecar and also renders
the backend MCP configuration that points at the sidecar's localhost port.

Example generated MCP configuration:

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

The Rust toolchain MCP server might expose structured tools:

```text
rust.cargo_test
rust.cargo_check
rust.rustfmt_check
rust.clippy
rust.exec_allowed
```

The Go/Python toolchain MCP server might expose:

```text
go.test
go.vet
python.pytest
python.ruff_check
python.ruff_format
toolchain.exec_allowed
```

This gives the backend a familiar model-facing tool surface. Claude, Codex, and Gemini can use the same MCP description
surface once their backend MCP handling is wired consistently.

The important conceptual point is that MCP is a good communication protocol here. The toolchain container remains the
architectural boundary, and MCP becomes the standardized way for Claude, Codex, Gemini, or future backends to discover
and call the tools exposed by that boundary. The primary goal is not to avoid MCP; the primary goal is to keep
language-specific tooling out of the backend images.

Benefits:

- Reuses existing backend MCP support.
- Avoids per-backend custom tool implementation at first.
- Gives each toolchain its own described tool surface.
- Allows multiple toolchains in one pod.
- Makes tool availability explicit to the model.
- Can use existing MCP auth/audit/body-cap patterns.

Risks and controls:

- A generic `exec` tool is still powerful and needs clear policy.
- Tool descriptions must be precise and safe.
- MCP servers need strict cwd, command, timeout, output, and environment policy.
- The model may choose the wrong toolchain unless names and descriptions are clear.

This is probably the best MVP path.

### Option 2: Backend-native `run_toolchain` tool

The backend could expose a provider-native tool named something like `run_toolchain`. That tool would call a local
toolchain HTTP API directly.

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
- One policy engine can sit in the backend.
- Toolchain calls can be represented consistently in backend traces.
- The toolchain contract can be expressed without routing through MCP.

Costs:

- Requires implementation in each backend.
- Claude, Codex, and Gemini have different native tool surfaces.
- Harder to ship quickly.
- The platform has to maintain a new tool protocol and SDK integration layer.

This may be the cleaner long-term abstraction, but it is probably not the easiest first step.

### Option 3: Backend uses `kubectl exec` into a sibling container

This is the tempting shortcut.

The backend can run:

```bash
kubectl exec <pod> -c toolchain-rust -- cargo test
```

This should not become the product primitive.

It requires the backend to have `pods/exec` authority. It turns Kubernetes into the execution API. It is harder to audit
as an agent tool action. It is awkward to scope. It encourages broad Kubernetes permissions in a container that is
already exposed to model-driven tool use. It also couples local tool execution to Kubernetes API availability even
though the containers are already in the same pod.

This can be useful for debugging. It should not be the platform design.

### Option 4: Project-specific backend images

The simplest short-term answer is still valid for some cases: build a custom backend image for the project.

```dockerfile
FROM ghcr.io/witwave-ai/images/claude:0.27.1

RUN install-rust-toolchain
RUN install-project-tools
```

Benefits:

- Simple.
- Works with existing backend tool execution.
- No new sidecar bridge required.

Costs:

- Repeats across backend types.
- Bloats backend images.
- Couples project toolchains to provider runtime images.
- Does not scale across languages or projects.

This is acceptable as an escape hatch. It should not be the default architecture.

### Option 5: Ephemeral runner jobs

Another option is to run toolchain work in short-lived Kubernetes Jobs.

The backend asks the harness, operator, or a runner service to launch a job:

```text
run cargo test in image ghcr.io/org/project-rust-toolchain:sha
mount workspace snapshot
return logs and exit code
```

Benefits:

- Strong isolation.
- Good for expensive or risky jobs.
- Clean resource lifecycle.
- Easier to scale for heavy builds.

Costs:

- Slower.
- More Kubernetes machinery.
- Harder to preserve local workspace mutations.
- More complicated for interactive agent loops.

This is a good future path for heavy builds, matrix tests, and untrusted execution. It is likely too heavy for the first
toolchain MVP.

---

## Recommended first design

The first design should be deliberately small:

> Add named toolchain sidecars that expose structured MCP tools over localhost.

The platform-level primitive should be `toolchains`, even if the first transport is MCP.

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
- A shared workspace mount into each toolchain sidecar.
- A backend MCP config entry for each toolchain.
- Optional environment variables describing toolchain names and URLs.
- Per-toolchain resource requests and limits.
- Per-toolchain security context.
- Optional network-policy controls.

The sidecar would provide:

- `/health`
- `/metrics`
- MCP endpoint
- Structured tools
- Command allowlist
- CWD restriction
- Timeout enforcement
- Output cap
- Audit log

The backend would not need to know that Rust is installed anywhere. It would only need to know that a `toolchain-rust`
MCP server exists and exposes safe Rust tools.

---

## MCP as protocol, toolchain as boundary

MCP is a protocol. `toolchain` is an architectural role.

It is perfectly reasonable for a Rust build toolchain, a Go/Python toolchain, a Prometheus gateway, and an AWS gateway
to all expose MCP tools. The issue is not that local toolchains use MCP. The issue is whether the platform preserves the
right operational boundary behind the protocol.

For toolchains, the important boundary is the container boundary: language-specific tools live in a dedicated execution
environment that mounts the workspace and has its own image, resources, policy, and audit. MCP can be the standardized
way the backend calls into that environment.

For external systems, the important boundary is authority: credentials, account scope, read/write posture, audit, and
blast radius. MCP can also be the standardized way the backend calls into those gateways.

The same protocol can serve both layers. The layer still matters.

The distinction matters:

| Capability              | Primary concern          | Better conceptual bucket      |
| ----------------------- | ------------------------ | ----------------------------- |
| `cargo test`            | Local project execution  | Toolchain                     |
| `go test ./...`         | Local project execution  | Toolchain                     |
| `ruff format`           | Local project mutation   | Toolchain                     |
| `kubectl get pods`      | Cluster authority        | Native tool or MCP gateway    |
| `helm upgrade`          | Cluster mutation         | MCP gateway or controlled CLI |
| `aws sts assume-role`   | Cloud authority          | MCP/gateway                   |
| `aws s3 ls prod-bucket` | Cloud authority/data     | MCP/gateway                   |
| `gh issue create`       | Source-control authority | MCP/gateway or native CLI     |
| `prometheus query`      | Observability access     | MCP/gateway                   |
| `make test`             | Local project execution  | Toolchain                     |

The same transport can be used for more than one bucket, but the platform should preserve the semantic boundary.

---

## Multiple execution environments

Multiple toolchains should be allowed, but they should be intentionally bounded.

It is reasonable for an agent pod to have two to five toolchain containers:

```text
toolchain-go-python
toolchain-rust
toolchain-node
toolchain-terraform
```

It is not reasonable for an agent pod to have hundreds. Each toolchain is a container with resource requests, image
pulls, patch cadence, security posture, logs, metrics, and operational overhead.

The design should optimize for a small set of named environments:

- One default project toolchain.
- Optional language-specific toolchains for heavy runtimes.
- Optional high-risk toolchains with tighter permissions.
- Optional build-runner jobs for expensive matrix work.

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

This matters because automatic routing failures will be confusing. If the model asks for `make test`, should that run in
the Go/Python toolchain, the Rust toolchain, or a project-default toolchain? The platform should not hide that decision.

---

## Security posture

Toolchain execution is powerful. It is local code execution against a shared workspace. That should be treated as a
high-trust but bounded capability, not as harmless convenience.

Minimum controls:

- **No cluster credentials by default.** Toolchain containers should not automatically receive Kubernetes service
  account tokens unless explicitly needed.
- **No cloud credentials by default.** AWS, GCP, Azure, GitHub, and other external authority should be mediated
  separately.
- **CWD restriction.** Commands should only run inside approved workspace paths.
- **Command allowlist.** Either expose structured tools or restrict generic exec to approved binaries.
- **Argument policy.** Interpreters and package runners need special handling because `python -c`, `node -e`, `bash -c`,
  `uvx`, `npx`, and similar forms can defeat naive command allowlists.
- **Timeouts.** Every execution needs a bounded timeout.
- **Output caps.** Large output should be truncated with clear metadata.
- **Audit logs.** Record command, cwd, exit code, duration, truncation, toolchain name, session hash, and backend.
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
toolchain image from scratch.

That said, dev containers are not a complete solution by themselves. The platform still needs:

- A way for the backend to call the toolchain.
- A way to expose safe tools to the model.
- A way to audit executions.
- A way to set output caps and timeouts.
- A way to define secrets and external authority boundaries.
- A way to run more than one toolchain when a project needs multiple environments.

Dev containers can help define the image. They do not define the agent execution contract.

---

## Relationship to MCP gateways

MCP remains important.

The toolchain layer should not replace MCP gateways. It should sit beside them.

Examples:

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

In some cases the boundary is mixed. Kubernetes is the clearest example.

`kubectl` installed in a backend or toolchain container is useful for diagnostics and parity with human workflows. But
if the agent needs production cluster authority, an MCP Kubernetes gateway with scoped credentials, read-only mode,
audit, and bounded verbs may be safer.

Helm has the same split:

- Local `helm template` and `helm lint` can be toolchain work.
- `helm upgrade` against a live cluster is external authority and may belong behind a gateway.

Prometheus is mostly external observability access, so MCP is a reasonable conceptual fit.

AWS multi-account access is an even stronger gateway case. Native `aws` CLI can work for one account with one scoped
identity, but multi-account role switching quickly becomes hard to reason about inside a model-driven shell. A set of
MCP or gateway instances scoped by account, environment, and permission tier is easier to audit and safer to operate.

---

## Draft implementation stages

### Stage 1: Document the concept

Name the layer and write down the boundaries:

- Backend runtime.
- Toolchain execution.
- MCP/external authority gateway.

This paper is part of that stage.

### Stage 2: Build one local toolchain sidecar

Build a simple `toolchain-go-python` sidecar for the current repo.

It should:

- Run as a sidecar.
- Mount the same source/workspace volume as the backend.
- Expose `/health`.
- Expose an MCP server over localhost.
- Provide structured tools for the current repo's common checks.
- Record audit logs.
- Enforce timeouts and output caps.

Initial tools could be:

```text
go_test
go_test_package
python_pytest
ruff_check
ruff_format_check
repo_command_allowed
```

Avoid starting with a wide-open shell.

### Stage 3: Add CRD/chart support

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

Start by exposing hints to the model and traces. Only later should the system automatically route commands.

### Stage 6: Add runner jobs for heavy execution

For expensive builds, integration tests, matrix runs, or untrusted code, use short-lived runner pods/jobs rather than
long-running sidecars.

---

## Open questions

Several choices are still unresolved.

### Should the first bridge be MCP or a custom toolchain API?

MCP is the fastest path because the backends already know how to consume MCP tools. A custom API may be cleaner
long-term, but it requires backend-specific integration.

The pragmatic answer is likely:

- Use MCP for the first bridge.
- Preserve `toolchain` as the product concept.
- Avoid generic shell unless heavily constrained.
- Revisit a native backend `run_toolchain` tool after the model proves useful.

### Should toolchains be per-agent or per-workspace?

Per-agent is easier to render because agents already own their pods.

Per-workspace is conceptually attractive because toolchains are project-specific, not personality-specific. Multiple
agents bound to the same workspace probably want the same execution environments.

The first version can be per-agent while the design leaves room for workspace-level defaults later.

### Should toolchains share package caches?

Probably yes, eventually. Language package caches are expensive to rebuild.

But shared caches introduce state, invalidation, security, and storage concerns. The first version can tolerate slower
execution in exchange for simpler semantics.

### Should toolchains have network access?

Some must. Package managers need network access unless dependencies are vendored or cached.

But network access changes the risk model. A toolchain running untrusted project scripts with broad egress is a larger
attack surface than one limited to local compilation. Network policy should become part of the toolchain spec.

### How much should the model choose automatically?

Not much at first.

The model can choose from clearly named tools. The platform can later add routing hints and defaults. Hidden automatic
routing should wait until traces show common patterns.

---

## Conclusion

Containerized agents need a tool model that respects the difference between model runtime, local execution, and external
authority.

Putting every language and operational tool into every backend image will not scale. MCP can be the right protocol for
reaching local toolchains, but it should not erase the architectural distinction between a project-local execution
environment and an external authority gateway. Relying on `kubectl exec` into sibling containers is useful for debugging
but too awkward and authority-heavy to be the platform primitive.

The better path is a hybrid:

- Keep backend containers focused on LLM runtime and agent protocol.
- Use named toolchain sidecars for project-local execution.
- Use MCP or similar gateways for external systems and sensitive authority.
- Use MCP as a practical, standardized transport for toolchain sidecars.
- Keep the product concept distinct so the architecture remains understandable.

The result is a containerized agent that can work across many kinds of projects without pretending one backend image can
be every developer workstation. The agent gets the right tools for the project, the platform keeps the authority
boundaries visible, and the execution environment becomes a deliberate part of the agent spec rather than an accidental
property of whatever happened to be installed in the backend image.
