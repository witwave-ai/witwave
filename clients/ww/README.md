# ww — witwave CLI

`ww` is the command-line companion for the Witwave / witwave multi-container agent platform. It talks to a harness over
the shared REST + SSE event surface: tail the live event stream, send A2A prompts, inspect scheduler configuration (jobs
/ tasks / triggers / continuations / heartbeat), and validate scheduler files — all without a browser.

> Output is stable enough to script against, and the wire formats are the same ones the dashboard already uses.
> Versioned releases ship regularly — see `ww version` for the running binary and [CHANGELOG.md](../../CHANGELOG.md) for
> release history.

**New to ww?** Start with [WALKTHROUGH.md](WALKTHROUGH.md) — a narrative tour from `ww operator install` to a running
multi-backend agent wired to a git repo. Every command copy-pasteable, every section builds on the last. This README is
the reference (every flag, every default); the walkthrough is the story.

## Install

Pick whichever is most natural for your environment — all three install the same binary.

### curl (Linux, macOS — universal)

```bash
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh
```

The script auto-detects OS + arch (linux/darwin × amd64/arm64), downloads the matching tarball from the GitHub release,
verifies the SHA256 against `checksums.txt`, and atomically installs the binary. By default it lands in `/usr/local/bin`
when writable, otherwise `~/.local/bin` (no `sudo` required). Pass `--use-sudo` to escalate for `/usr/local/bin`, or
`--prefix=$HOME/.local` to pick the install root explicitly.

Pin a specific version, install a beta, or skip verification:

```bash
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh -s -- --version v0.9.6
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh -s -- --channel beta
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh -s -- --verify-signature   # also verify cosign signature; needs `cosign` on PATH
```

| Flag                  | Env var                 | Effect                                                                                                                                           |
| --------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--version <tag>`     | `WW_VERSION`            | Pin to a release tag (e.g. `v0.9.6`). Default: latest stable.                                                                                    |
| `--channel <c>`       | `WW_CHANNEL`            | `stable` (default) or `beta` (includes `-beta.N` / `-rc.N` releases).                                                                            |
| `--prefix <dir>`      | —                       | Install root. Binary lands in `<prefix>/bin`.                                                                                                    |
| `--install-dir <dir>` | `WW_INSTALL_DIR`        | Bin dir directly. Overrides `--prefix`.                                                                                                          |
| `--use-sudo`          | `WW_USE_SUDO=1`         | Allow `sudo` for `/usr/local` writes. Default: skip silently.                                                                                    |
| `--no-verify`         | `WW_NO_VERIFY=1`        | Skip SHA256 verification. Not recommended.                                                                                                       |
| `--verify-signature`  | `WW_VERIFY_SIGNATURE=1` | Also verify the cosign signature on `checksums.txt`.                                                                                             |
| `--dry-run`           | `WW_DRY_RUN=1`          | Print what would happen, change nothing.                                                                                                         |
| `--quiet` / `-q`      | `WW_QUIET=1`            | Suppress progress output.                                                                                                                        |
| `--force`             | `WW_FORCE=1`            | Reinstall even when the same version is already present (default: no-op with a hint). Upgrades to a different version proceed without `--force`. |
| `--uninstall`         | —                       | Remove the binary + the `.ww.install-info` marker.                                                                                               |

Prefer to read the script before running it:

```bash
curl -fsSL -o install.sh https://github.com/witwave-ai/witwave/releases/latest/download/install.sh
less install.sh
sh install.sh
```

The script writes a sibling `.ww.install-info` marker file alongside the binary so `ww update` knows it was installed
this way and can re-run the same pipeline to upgrade in place.

### Homebrew (macOS)

```bash
brew install witwave-ai/homebrew-ww/ww
```

The [witwave-ai/homebrew-ww](https://github.com/witwave-ai/homebrew-ww) tap is updated automatically as part of every
release. The tap ships a Homebrew **cask**; Linuxbrew is not supported (casks are macOS-only). Linux users have
`go install`, the curl installer, or the GitHub Release tarball as install paths.

### `go install` (developers)

```bash
go install github.com/witwave-ai/witwave/clients/ww@latest
```

### Testing the installer locally (developers)

When changing `scripts/install.sh` or `clients/ww/.goreleaser.yml`, you can validate the full download → verify →
install pipeline against a local goreleaser snapshot — no tag, no GitHub push, no waiting on CI:

```bash
# 1. Build unsigned snapshot artifacts into clients/ww/dist/
cd clients/ww
goreleaser release --snapshot --clean

# 2. Serve dist/ on a local HTTP port
python3 -m http.server --directory dist 8765 &

# 3. Point install.sh at the local server. WW_BASE_URL skips the
#    canonical /releases/download/<tag>/ path; --no-verify is needed
#    only because snapshot tarballs aren't cosign-signed (SHA256
#    verification still runs against the snapshot's checksums.txt).
WW_BASE_URL=http://localhost:8765 \
  sh ../../scripts/install.sh --version v0.0.0-snapshot --prefix /tmp/ww-snapshot

/tmp/ww-snapshot/bin/ww version   # confirm the snapshot binary works

# 4. Tear down
kill %1
rm -rf /tmp/ww-snapshot
```

`WW_BASE_URL` is a developer-only escape hatch — it isn't documented in the public install flow and isn't honored by
`ww update`'s self-upgrade path (which always re-runs the canonical `releases/latest/download/install.sh`).

CI also exercises the installer in two layers — pre-merge linting + smoke install in `ci-install-script.yml`, and a
post-release re-install of the just-cut tag in `release-ww.yml` (matrix: alpine 3.19, debian 12, ubuntu 22.04, fedora
40, macOS 14, on both linux/amd64 and linux/arm64 runners).

#### Manual smoke before promoting a release

One installer flow CI can't fully exercise is **`ww update --force` upgrading across two consecutive real releases** —
automating it would require cutting a throwaway tag pair on every CI run. Worth doing once by hand on a test box before
each `v*.*.*` release that includes installer changes:

```bash
# 1. Install the previous stable.
curl -fsSL https://github.com/witwave-ai/witwave/releases/download/<previous>/install.sh \
  | sh -s -- --version <previous> --prefix /tmp/ww-upgrade
/tmp/ww-upgrade/bin/ww version   # should report <previous>

# 2. Run ww update --force; should re-run the install pipeline and replace the binary in place.
/tmp/ww-upgrade/bin/ww update --force
/tmp/ww-upgrade/bin/ww version   # should now report the new version

# 3. Cleanup
rm -rf /tmp/ww-upgrade
```

Asserting `ww update --check` reports `Install method: curl-installer` _is_ automated in `release-ww.yml`'s post-release
smoke; the gap above is specifically the `--force` upgrade-execution path (RunUpgrade re-running the install pipeline),
which has no end-to-end coverage today.

## Quick start

```bash
# One-time config.
mkdir -p ~/.config/ww
cat > ~/.config/ww/config.toml <<'EOF'
[profile.default]
base_url  = "http://localhost:8000"
token     = "your-CONVERSATIONS_AUTH_TOKEN"
run_token = "your-ADHOC_RUN_AUTH_TOKEN"   # validate + ad-hoc run commands
EOF

# Who's up?
ww status

# Tail the harness event stream.
ww tail --pretty

# Send a prompt to an agent.
ww send iris "what does the team look like right now?"

# Inspect scheduler config.
ww jobs
ww tasks view daily-report
ww triggers
ww heartbeat view
ww continuations

# Fire scheduler items on demand.
ww jobs run daily-report
ww tasks run daily-report
ww heartbeat run

# Validate a trigger file before committing it.
ww validate .agents/self/iris/.witwave/triggers/notify.md

# After a release or operator upgrade, check the rollout.
ww doctor release --agent zora --agent iris
```

## Commands

Every command supports `--help`. Summary:

| Command                      | Purpose                                                                                                                                                             |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ww status`                  | Fetch `/agents`, probe each member's `/health`, print a table.                                                                                                      |
| `ww team status`             | Aggregate recent conversation-backed activity across WitwaveAgents; add `--watch` / `--interval 10s` for a live-refreshing table.                                   |
| `ww tail`                    | Stream SSE events from `/events/stream`. `--agent`, `--session`, `--types`, `--pretty`.                                                                             |
| `ww send <agent> [text]`     | POST an A2A `message/send` to the harness. `--prompt-file -` reads stdin.                                                                                           |
| `ww jobs [list\|view\|run]`  | Read the `/jobs` snapshot, or fire a named job immediately through `/jobs/<name>/run` using `run_token`.                                                            |
| `ww tasks [list\|view\|run]` | Read the `/tasks` snapshot, or fire a named task immediately through `/tasks/<name>/run` using `run_token`.                                                         |
| `ww heartbeat [view\|run]`   | Read `/heartbeat`, or fire the configured heartbeat immediately through `/heartbeat/run` using `run_token`.                                                         |
| `ww triggers [list\|view]`   | Read `/triggers`.                                                                                                                                                   |
| `ww continuations […]`       | Read `/continuations`.                                                                                                                                              |
| `ww validate <file>`         | POST a file to `/validate` using `run_token`. Kind inferred from path or passed via `--kind`.                                                                       |
| `ww version`                 | Print the version, commit, and build date. `--short` prints just the semver.                                                                                        |
| `ww doctor release`          | Run read-only post-release checks against the local `ww` binary, harness, operator, CRDs, and WitwaveAgents.                                                        |
| `ww operator [cmd]`          | Install / upgrade / inspect / uninstall the witwave-operator Helm release on a Kubernetes cluster; plus `logs` and `events` for diagnostics. See below.             |
| `ww workspace [cmd]`         | Manage `WitwaveWorkspace` CRs: `create`, `list`, `get`, `status`, `delete`, `bind`, `unbind`. See [WitwaveWorkspace management](#witwaveworkspace-management).      |
| `ww config [cmd]`            | Read, write, and inspect `ww` configuration values — `get`, `set`, `unset`, `list-keys`, `path`. See [Managing config from the CLI](#managing-config-from-the-cli). |
| `ww update`                  | Check for and install a newer `ww` release. See [Staying up to date](#staying-up-to-date).                                                                          |

### Release doctor

`ww doctor release` is a read-only post-release gate. It checks the local `ww` build metadata, the configured harness
(`/agents`, advertised agent health, heartbeat config), the operator Helm release, operator pods, CRDs, and WitwaveAgent
CRs across namespaces.

By default, intentionally scaled-down agents produce warnings rather than failures. Add `--agent <name>` (or
`--agent <namespace>/<name>`) when a specific agent must be present and Ready, `--require-agents-ready` when every
inspected agent must be Ready, and `--strict-agent-tags` when image tag skew should fail the run. Use `--skip-harness`
or `--skip-cluster` for partial diagnostics, and the global `--json` / `--yaml` flags for machine-readable output.

### Streaming

`ww tail` reconnects automatically with exponential backoff (100 ms → 10 s, with ±25 % jitter) and sends `Last-Event-ID`
on reconnect so the harness's ring buffer can fill the gap. SIGINT closes the stream and exits cleanly. JSON-lines
output is the default — pipe to `jq` without worrying about boundaries. `--pretty` flips to a human-friendly line per
event.

`ww tail --agent iris` bypasses the dashboard proxy and hits the harness URL reported by the harness's `/agents`
directory directly. `ww tail --agent iris --session abc` switches to the backend-local per-session drill-down stream at
`/api/sessions/<id>/stream`.

### Sending

`ww send` builds the same A2A envelope the dashboard uses:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "<random>",
      "contextId": "<random or --context>",
      "role": "user",
      "parts": [{ "kind": "text", "text": "<prompt>" }]
    }
  }
}
```

`--backend claude|openai|codex|gemini|echo` adds `metadata.backend_id`; harness executors already honour that field.
`--context` reuses an existing `contextId` for multi-turn sessions.

## Operator management

`ww operator` manages the witwave-operator Helm release — the cluster- scoped CRD controller that reconciles
`WitwaveAgent` and `WitwavePrompt` resources. The operator chart is **embedded** into the `ww` binary via `go:embed`, so
you don't need Helm installed locally or any repo configured; `ww` ships with a known-good chart pinned to its own
release version.

```bash
ww operator install             # embedded chart → witwave-system namespace
ww operator status              # release, pods, CRDs, live CR counts
ww operator upgrade             # CRD server-side apply + helm upgrade
ww operator uninstall           # removes release; CRDs + CRs preserved by default
ww operator uninstall --delete-crds [--force]
ww operator logs                # tail operator pod logs
ww operator events              # Kubernetes events for operator + CRs
```

All six commands honour the ambient kubeconfig and current-context. `--kubeconfig` and `--context` are **global flags on
`ww` itself** (so they work on any subcommand); `--namespace` / `-n` is local to `ww operator` and defaults to
`witwave-system`. The three mutating commands (`install`, `upgrade`, `uninstall`) print a preflight banner showing the
target cluster and either prompt or auto-proceed based on a local-vs-production heuristic:

- **Local clusters skip the prompt** — context name matching `kind-*`, `minikube`, `docker-desktop`, `rancher-desktop`,
  `orbstack`, `k3d-*`, `colima`, or a server URL pointing at `localhost` / `127.0.0.1` / `kubernetes.docker.internal`.
- **Everything else prompts** — EKS/GKE/AKS ARNs, external IPs, unknown context names. Must type `y` to proceed.

Overrides: `--yes` / `-y` or `WW_ASSUME_YES=true` to skip the prompt unconditionally (scripts + CI); `--dry-run` to
print the plan and exit without touching the cluster.

> **`$KUBECONFIG` with multiple files (gotcha).** When `$KUBECONFIG` points at a colon-separated list
> (`KUBECONFIG=a.yaml:b.yaml`), client-go merges the files but `current-context` comes from the **first** file that sets
> one — not the last. If you set up a `dev.yaml` and expect it to win over an earlier `prod.yaml`, it won't. Use
> `--context <name>` explicitly, or put the intended file first in the list. `ww` inherits this behaviour from kubectl
> and helm; don't try to "fix" it by reordering merges.

### Singleton enforcement

The operator is a cluster-scoped singleton. `ww operator install` refuses when a release already exists cluster-wide.
Matrix of outcomes on install:

- **Clean cluster** → proceeds with the install.
- **CRDs present, no Helm release** → refuses unless you pass `--adopt`. Useful for clusters where someone applied the
  CRDs manually via `kubectl apply`; `--adopt` takes over management.
- **Helm release exists** → refuses, points at `ww operator upgrade`.

### Values passthrough

For changes to the operator's chart values (replicas, image overrides, HPA, affinity, etc.), the canonical path is Helm
— but `--set key=val` and `-f values.yaml` on `install`/`upgrade` are planned follow-ups. In the meantime users with
non-default values should pull the chart directly:

```bash
helm pull oci://ghcr.io/witwave-ai/charts/witwave-operator --version <tag>
helm upgrade --install witwave-operator ./witwave-operator \
  -n witwave-system --create-namespace \
  -f my-operator-values.yaml
```

### Upgrade flow

`ww operator upgrade` server-side-applies the embedded chart's CRDs **before** running `helm upgrade --skip-crds`. This
works around Helm's long-standing "crds/ is install-only, never updated" semantics so new CRD fields (new `status`
columns, added `MaxItems` markers, the eventual v1beta1 storage-version switch) land on the apiserver before the
operator pod rolls with code that expects them.

### Uninstall safety

Default uninstall preserves CRDs + CRs, so a mis-click cannot cascade-delete user data. Pass `--delete-crds` to remove
the CRDs too; when any live `WitwaveAgent` or `WitwavePrompt` CRs exist, `ww` refuses unless you also pass `--force`.
The preflight banner prints a loud `WARNING: N CRs will be deleted` line in that case.

### Status output

```text
$ ww operator status
Target cluster: docker-desktop  (context: docker-desktop)

Witwave Operator
  Namespace:      witwave-system
  Release:        witwave-operator (Helm, rev 2, deployed)
  Chart version:  <X.Y.Z>
  App version:    <X.Y.Z>
  ww version:     <X.Y.Z>  (match)

Pods
  witwave-operator-abc123  Running

CRDs
  witwaveagents.witwave.ai             v1alpha1
  witwaveprompts.witwave.ai            v1alpha1
  witwaveworkspaces.witwave.ai         v1alpha1

Reconciles managed
  WitwaveAgent:      3
  WitwavePrompt:     1
  WitwaveWorkspace:  1
```

The `ww version` line renders `(match)` / `(patch skew)` / `(minor skew)` / `(major skew — upgrade blocked)` so operator
and binary version mismatches are visible at a glance. Local `ww` builds (built outside the release path) render
`(local build — skew unknown)` instead of a phantom "skew" warning.

### Operator diagnostics — logs + events

```bash
ww operator logs                   # tail 100 lines + follow
ww operator logs --tail 500
ww operator logs --since 1h
ww operator logs --no-follow       # snapshot
ww operator logs --pod <name>      # specific pod

ww operator events                 # last 1h of operator + CR events
ww operator events --watch         # or -w; stream new events
ww operator events --warnings      # Warning type only
ww operator events --since 15m
```

Both commands auto-resolve the target cluster + namespace from the parent flags (`--kubeconfig`, `--context`,
`--namespace`) so you don't re-type.

**`ww operator logs`** tails every pod matching `app.kubernetes.io/name=witwave-operator` in the operator namespace.
Multi-pod output gets a `[pod-name]` prefix; single-pod output doesn't. Scanner buffer is bumped to 1 MiB so
controller-runtime's long structured-log lines (stack traces, big reconcile payloads) don't truncate.

**`ww operator events`** shows three sources merged:

1. Events on `WitwaveAgent` CRs (any namespace by default — CRs live wherever users deploy them).
2. Events on `WitwavePrompt` CRs (same scope).
3. Events in the operator's own namespace — Pod scheduling failures, image-pull errors, crash loops on the operator
   itself.

Defaults to the last 1 hour so first paint is bounded. `--warnings` filters to `type=Warning` which is the "what's going
wrong?" signal. `--watch` opens watch streams against each source and fans new events into the same table format as the
initial listing.

Scope note: `-n <ns>` on the parent narrows CR-event listing to one namespace. Without it, CR events are listed
cluster-wide (the sensible default for CRs). The operator-namespace listing always stays on `witwave-system` regardless.

## Agent management

`ww agent` creates, lists, inspects, and deletes `WitwaveAgent` custom resources. The witwave-operator (installed via
`ww operator install`) reconciles each CR into a running agent pod with harness + backend sidecars.

```bash
# Lifecycle
ww agent create hello --create-namespace        # deploy an agent running the echo backend (no API keys)
ww agent create hello --team research           # stamp witwave.ai/team=research at creation
ww agent list                                   # list across every namespace you can read (default)
ww agent list --namespace witwave               # narrow to a single namespace
ww agent status hello                           # phase, backends, last reconcile history
ww agent delete hello                           # operator cascades pod cleanup via owner refs
ww agent delete hello --purge                   # also wipe repo folder + ww-managed credential Secret

# Interaction
ww agent send hello "ping"                      # round-trip A2A call via the apiserver Service proxy
ww agent metrics hello                          # scrape harness + backend Prometheus metrics
ww agent logs hello                             # tail the harness container (--container <name> for a sidecar)
ww agent events hello                           # CR + pod events scoped to this agent

# GitOps wiring (repo content → agent pod)
ww agent scaffold hello --repo owner/repo       # materialise ww-conformant agent layout on a remote repo
ww agent git add hello --repo owner/repo        # attach a gitSync; syncs .agents/<name>/ into the pod
ww agent git list hello                         # show the gitSyncs + mappings on an agent
ww agent git remove hello                       # detach gitSync (keeps ww-minted Secret unless --delete-secret)

# Backend lifecycle on a running agent
ww agent backend add    hello claude --auth oauth            # append; auto-assigns next free port + scaffolds .claude/ in the repo
ww agent backend add    hello claude --auth-set ANTHROPIC_API_KEY=sk-...    # ditto; literal KEY=VALUE Secret instead of named profile
ww agent backend rename hello echo-2 echo-primary            # rename across CR + gitMappings + repo folder
ww agent backend remove hello echo-2 --remove-repo-folder    # drop a backend + wipe its repo folder

# Existing-agent persistence
ww agent storage enable hello                  # add runtime logs/state PVC + backend state mounts
ww agent storage enable hello --no-backend-state    # harness runtime PVC only

# Kubernetes API access for in-agent diagnostics/remediation
ww agent create mira --kubernetes-api-access   # create with read-only Kubernetes API access
ww agent create mira --kubernetes-api-access=namespaceWrite    # create with bounded namespace write access
ww agent k8s-access enable mira --mode readOnly    # enable/update an existing agent
ww agent k8s-access disable mira               # return an existing agent to the no-token default

# Team membership (runtime peer discovery via witwave-manifest-<team>)
ww agent team join hello research               # set witwave.ai/team=research
ww agent team leave hello                       # drop the label; agent falls into namespace-wide manifest
ww agent team list                              # tree of teams → members in the namespace
ww agent team show hello                        # which team an agent is in + sorted teammates
```

### GitOps scaffolding (repo-first workflow)

`ww agent scaffold` materialises a ww-conformant agent directory structure on a remote git repo so a later
`ww agent git add` can wire a deployed agent to pull from it. The scaffolder uses your machine's git credentials —
whatever `git push` against that remote already works, `ww agent scaffold` works too.

```bash
# Scaffold into an empty repo (gets bootstrapped with an initial commit)
ww agent scaffold hello --repo witwave-ai/witwave-test

# With an optional group segment — lands in .agents/prod/hello/ instead of .agents/hello/
ww agent scaffold iris --repo github.com/org/agents --group prod

# Dry-run prints the plan and file list without touching disk or remote
ww agent scaffold hello --repo owner/repo --dry-run

# Retain the clone locally for inspection / iteration
ww agent scaffold hello --repo owner/repo --clone-to ./local-agents

# Re-scaffolding an existing agent is refused unless --force
ww agent scaffold hello --repo owner/repo --force
```

**Layout produced** (flat default, no group):

```text
.agents/hello/
├── README.md              # short human-readable description + next-step hints
├── .witwave/
│   ├── backend.yaml       # routing — single backend, ports 8001+ per PORT-1..4
│   └── HEARTBEAT.md       # hourly heartbeat (unless --no-heartbeat is set)
└── .<backend>/
    ├── agent-card.md      # A2A identity card skeleton
    └── <CLAUDE|AGENTS|GEMINI>.md   # behavioural instructions (LLM backends only)
```

`HEARTBEAT.md` ships on by default — an hourly `HEARTBEAT_OK` fires against the agent's A2A endpoint so you get an
immediate, self-exercising proof-of-life signal without having to construct one yourself. Pass `--no-heartbeat` to
scaffold a silent agent. Edit the file's `schedule:` frontmatter (cron) to customise, or delete the file to stop
heartbeats cold — the harness picks up either change on the next gitSync tick. This is a documented exception to
DESIGN.md SUB-4; every other dormant subsystem (`jobs/`, `tasks/`, `triggers/`, `continuations/`, `webhooks/`) stays
absent until you explicitly drop content in.

**Branch detection** — `--branch` defaults to the remote's own default (via HEAD symref), falling back to `main` only on
empty repos that have no default yet. Repos on `master`, `develop`, etc. work without passing the flag.

**Auth** — three paths, tried in order:

1. `GITHUB_TOKEN` / `GH_TOKEN` / `GIT_TOKEN` env vars (for CI + scripting)
2. `gh auth token` (for gh-authenticated users — default on dev laptops)
3. `git credential fill` (for non-GitHub remotes or users without gh)

SSH URLs (`git@host:owner/repo`) use your ssh-agent. Credentials are never stored by ww — same posture as `git push`,
just through a ww-friendly CLI.

With no flags, `ww agent create <name>` deploys the **echo backend** — a zero-dependency stub that returns a canned
response quoting the caller's prompt (see [`backends/echo/`](../../backends/echo/README.md)). Pick a real LLM backend
with `--backend claude|openai|codex|gemini`; the chosen backend's image is published at the same version as the `ww`
binary.

### Backend credentials — four paths

LLM backends need an API key or OAuth token. `ww agent create` resolves per-backend credentials via four repeatable
flags (pick ONE per backend; `--auth-set` is the only one that's repeatable for the same backend, accumulating into one
Secret):

| Flag                        | Shape                        | Behavior                                                                                                                                                                                                   |
| --------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--auth`                    | `<backend>=<profile>`        | Named profile reads conventional env var(s) from the shell + mints a `<agent>-<backend>` Secret. MVP profiles: `claude: api-key \| oauth`.                                                                 |
| `--backend-secret-from-env` | `<backend>=<VAR>[,VAR2,...]` | Mint a Secret from named env vars. Each VAR is bare `<NAME>` (Secret key matches name) or a rename `<SRC>:<DEST>` (read `$SRC`, store under key `DEST`).                                                   |
| `--auth-secret`             | `<backend>=<secret-name>`    | Reference an existing Secret (verified, never modified). Production default.                                                                                                                               |
| `--auth-set`                | `<backend>:<KEY>=<VALUE>`    | Mint a Secret with literal `KEY=VALUE` pairs. Repeatable per `(backend, KEY)`. **Values land in shell history + ps output — for production tokens prefer `--auth-secret` or `--backend-secret-from-env`.** |

```bash
# OAuth path — reads $CLAUDE_CODE_OAUTH_TOKEN from the shell
ww agent create iris --backend claude --auth claude=oauth

# API-key path — reads $ANTHROPIC_API_KEY
ww agent create iris --backend claude --auth claude=api-key

# Pre-existing Secret — production path with out-of-band rotation
ww agent create iris --backend claude --auth-secret claude=my-anthropic-pat

# Inline KEY=VALUE — for ad-hoc / custom-shape credentials
ww agent create iris --backend claude \
  --auth-set claude:ANTHROPIC_API_KEY=sk-ant-xxxx \
  --auth-set claude:ALT_TOKEN=ghp_yyyy

# Multi-backend: one --auth per backend
ww agent create consensus --backend claude --backend openai \
  --auth claude=oauth --auth openai=openai
```

On `ww agent backend add` the `<backend>:` prefix drops since the backend's already positional — each `--auth-set` entry
is just `<KEY>=<VALUE>`:

```bash
ww agent backend add hello claude \
  --auth-set GITHUB_TOKEN=ghp_xxxx \
  --auth-set ALT_GITHUB_TOKEN=ghp_yyyy
```

Minted Secrets carry `app.kubernetes.io/managed-by: ww` so `ww agent delete --purge` reaps them label-gated. Hand-rolled
Secrets at the same name are refused — use `--auth-secret` to reference them instead, or `--backend-secret-from-env`
with a non-colliding name. The `created-by` annotation on `--auth-set`-minted Secrets records key NAMES only (never
values) so values don't leak into `kubectl get secret -o yaml` metadata.

Editing or removing one key in an existing credential Secret without recreating the agent isn't covered by ww yet — use
`kubectl edit secret <agent>-<backend> -n <ns>` for now. A follow-up `ww agent backend auth set/unset/list/show` subtree
is on the roadmap.

Namespace handling follows DESIGN.md NS-1..5:

- No `--namespace` → the kubeconfig context's namespace (falls back to `witwave`, the ww-wide default). The command
  always prints the resolved namespace at the top of its output, and the parenthetical source
  (`(from kubeconfig context)` vs `(ww default)`) tells you whether the fallback kicked in.
- `ww agent create --create-namespace` provisions the target namespace if it doesn't already exist (labelled
  `app.kubernetes.io/managed-by: ww`) — mirrors `helm install --create-namespace`.
- `ww agent list` defaults to cluster-wide scope — pass `--namespace` to narrow to one namespace. NS-1's context-first
  resolution does not apply to list; the idiom is `kubectl get ... -A`, and scoping a list silently to the kubeconfig
  context's namespace systematically hides half the cluster. The `NAMESPACE` column is always shown so sort / grep
  pipelines work the same across modes.
- `-A` is only valid on `list` — never on `create`, `status`, `delete`, or any mutating verb. On `list` it's redundant
  (the default is already all-namespaces) but accepted for kubectl parity.

Create waits up to `--timeout` (default `5m`) for the operator to report the agent Ready. On timeout, recent CR and pod
events are dumped so cold image pulls can be distinguished from real failures (crashlooping containers,
ImagePullBackOff). Pass `--no-wait` to return as soon as the CR is accepted (scripts + CI). All mutating commands
(`create`, `delete`) honour `--yes` / `WW_ASSUME_YES=true` and `--dry-run` the same way `ww operator install` does.

`ww agent storage enable <name>` is the existing-agent counterpart to `ww agent create --with-persistence` for durable
runtime state. It patches `spec.runtimeStorage` so harness gets `/home/agent/logs` and `/home/agent/state`, then adds
`/home/agent/state` to any backend that already has persistent backend storage. The operator renders
`TASK_STORE_PATH=/home/agent/state/a2a-tasks.db` when the state mount is present. Backends without storage are skipped
instead of silently allocating new backend PVCs; add backend persistence at creation time with `--persist` or
`--with-persistence`.

`ww agent create --kubernetes-api-access` stamps `spec.kubernetesApiAccess` at creation time so the operator creates a
per-agent ServiceAccount, namespace-scoped Role, and RoleBinding, then wires the pod to that identity. With no value,
the flag uses `readOnly` for diagnostics (`get/list/watch` plus pod logs). Pass `--kubernetes-api-access=namespaceWrite`
when the agent needs bounded namespace-local remediation: ConfigMaps, Services, Deployments, Jobs, CronJobs, and Pod
delete/eviction. This managed path still excludes secrets, RBAC mutation, raw Pod creation, and cluster-scoped
resources. Existing agents can be changed with `ww agent kubernetes-api-access enable <name> --mode readOnly` (alias:
`ww agent k8s-access enable ...`) or returned to the no-token default with `ww agent k8s-access disable <name>`.

This is an access/RBAC knob, not an image-composition knob. Backend images may include the shared toolbox binaries
(`kubectl`, `ww`, `gh`, Helm, analyzers), but the pod cannot use the Kubernetes API until credentials are mounted and
RBAC allows the requested verbs.

`ww agent send` uses the Kubernetes apiserver's built-in Service proxy so any `ClusterIP` Service is reachable without
local port-forwarding or an external LoadBalancer. This makes round-trip A2A calls from a laptop against a cluster-only
agent Just Work. Caveats: the apiserver proxy has payload size caps and isn't suited for streaming — use
`ww agent logs -f` for live observation, or the dedicated `ww send --base-url ...` path for long-running streams against
an externally-reachable harness URL.

`ww agent metrics <name>` scrapes every Prometheus `/metrics` endpoint owned by the agent pod: harness plus each enabled
backend container. The CLI discovers container ports named `metrics-*` and reads them through the Kubernetes apiserver
pod proxy, so no local port-forward is required. Output stays in Prometheus text format with lightweight comment headers
per container.

### Deleting agents — repo + Secret cleanup

`ww agent delete <name>` removes the CR; the operator cascades pod + Service teardown via owner references. Three opt-in
flags extend the blast radius:

- `--remove-repo-folder` — clones the single wired gitSync repo, `git rm -r`s `.agents/<…>/` for the agent, commits, and
  pushes. Runs **before** the CR delete so a push failure (auth, branch protection, network) leaves cluster state intact
  and you can retry. Refuses with an ambiguity error when the agent has multiple gitSyncs; soft-skips when no gitSync is
  wired (nothing to wipe).
- `--delete-git-secret` — after the CR is gone, reaps every ww-managed credential Secret referenced by the CR's
  gitSyncs. Secrets without the `app.kubernetes.io/managed-by: ww` label are preserved regardless.
- `--purge` — convenience: `--remove-repo-folder` + `--delete-git-secret`. For decommissioning an agent permanently in
  one call.

The preflight banner lists every destructive action (repo URL, branch, `git rm` target, each Secret name) before
confirmation, so the non-local-cluster prompt has enough detail to review.

### Team membership — runtime peer discovery

A WitwaveAgent's team membership is a single label — `witwave.ai/team=<team>`. The operator reconciles one
`witwave-manifest-<team>` ConfigMap per distinct value and mounts it into every member's pod at
`/home/agent/manifest.json`, so harnesses discover their teammates' URLs at runtime. Agents without the label share a
namespace-wide manifest.

```bash
ww agent create iris --team research              # stamp the label at creation
ww agent team join iris research                  # or patch the label later
ww agent team list                                # tree of teams in the namespace
ww agent team list --team research                # filter to one team's members
ww agent team show iris                           # which team + sorted teammates
ww agent team leave iris                          # drop the label; falls into namespace-wide manifest
```

Teams are purely additive: no CRD schema change, no pod restart. A label patch takes effect within one operator
reconcile (seconds). Cleanup is automatic — deleting the last member of a team deletes the per-team manifest ConfigMap
with it; no orphan management needed.

There is **no default team** by design. Agents without the label already share the namespace-wide manifest, which is the
right grouping for the common case. Use `--team` when you explicitly want to subset peer discovery within a namespace —
e.g., two unrelated cohorts of agents in the same namespace that shouldn't see each other.

`ww agent events` is a one-shot scoped variant of `ww operator events`: events on the WitwaveAgent CR plus events on
pods matching `app.kubernetes.io/name=<agent-name>`. No `--watch` mode — when you need live signal, `ww agent logs -f`
usually tells you more.

## WitwaveWorkspace management

`ww workspace` creates, lists, inspects, deletes, and binds `WitwaveWorkspace` custom resources. The witwave-operator
reconciles each `WitwaveWorkspace` into shared volumes, projected Secrets, and rendered ConfigMaps that participating
WitwaveAgents see at runtime. Membership is agent-owned: a `WitwaveAgent` declares which workspaces it participates in
via `spec.workspaceRefs[]`, and the workspace controller maintains `Status.BoundAgents` as the inverted index.

```bash
# Lifecycle
ww workspace create shared --volume source=50Gi@efs-sc        # quick mode — convenience flags
ww workspace create -f workspace.yaml --create-namespace      # full mode — YAML manifest
ww workspace list                                             # default scope: every namespace you can read
ww workspace list --namespace witwave                         # narrow to a single namespace
ww workspace get shared                                       # one-row table; -o yaml / -o json for the raw object
ww workspace status shared                                    # volumes + secrets + configFiles + conditions + bound agents
ww workspace delete shared --wait --timeout 2m                # refuse-delete finalizer blocks while agents are bound

# Membership (agent-owned via spec.workspaceRefs[])
ww workspace bind iris shared                                 # idempotent — re-binding is a no-op
ww workspace unbind iris shared                               # drops the entry; does NOT delete the WitwaveWorkspace
```

| Subcommand                     | Purpose                                                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `ww workspace create [name]`   | Create a WitwaveWorkspace from a YAML file (`-f`) or convenience flags (`--volume`, `--secret`).        |
| `ww workspace list`            | Cluster-wide by default (NS-3 / kubectl parity); narrow with `-n`. Output `-o table\|yaml\|json`.       |
| `ww workspace get <name>`      | Fetch a single WitwaveWorkspace; default output is a one-row table; `-o yaml\|json` for the raw object. |
| `ww workspace status <name>`   | Curated human view: volumes, conditions, bound agents.                                                  |
| `ww workspace delete <name>`   | Delete the CR; `--wait` blocks on the refuse-delete finalizer.                                          |
| `ww workspace bind <a> <ws>`   | Add `<ws>` to `<a>.spec.workspaceRefs[]`. Idempotent. Same-namespace only in v1alpha1.                  |
| `ww workspace unbind <a> <ws>` | Remove `<ws>` from `<a>.spec.workspaceRefs[]`. Does NOT delete the WitwaveWorkspace.                    |

### Flags

`-n / --namespace` is local to the workspace subtree (per DESIGN.md KC-6). When omitted it defaults to the kubeconfig
context's namespace, falling back to the ww-wide default (`witwave`); the resolved namespace is echoed at the top of the
output (NS-2). Mutating subcommands (`create`, `delete`, `bind`, `unbind`) accept `--yes` / `WW_ASSUME_YES=true` and
`--dry-run`. `list` accepts `-A / --all-namespaces` for kubectl parity (redundant — already the default — but accepted;
NS-3).

`ww workspace create` convenience flags:

| Flag                 | Shape                                        | Behaviour                                                                                                                                           |
| -------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-f, --from-file`    | path                                         | YAML/JSON `WitwaveWorkspace` manifest. Mutually exclusive with `--volume` / `--secret`.                                                             |
| `--volume`           | `<name>=<size>[@<storageClass>][:<mode>]`    | Repeatable. Defaults access mode to `ReadWriteMany`; append `:rwo` or `:rwop` for `ReadWriteOnce` / `ReadWriteOncePod` on constrained dev clusters. |
| `--secret`           | `<name>` / `<name>@/abs/path` / `<name>=env` | Repeatable. Bare name = reference only; `@/abs/path` = mount; `=env` = project as `envFrom`. Anything else after `=` is rejected loudly.            |
| `--create-namespace` | bool                                         | Provision the namespace (labelled `app.kubernetes.io/managed-by: ww`) when missing. No-op otherwise.                                                |

`ww workspace delete` accepts `--wait` (block until the apiserver removes the CR) bounded by `--timeout` (default `2m`).
The plan banner enumerates currently-bound agents up-front so the refuse-delete finalizer's blast radius is visible
before confirmation.

For full control over reclaim policies, multiple `configFiles[]` entries, and other fields the convenience flags don't
surface, author a YAML manifest and pass it via `-f`. The schema lives at
[`operator/api/v1alpha1/witwaveworkspace_types.go`](../../operator/api/v1alpha1/witwaveworkspace_types.go) and a richer
walk-through sits in [`operator/README.md`](../../operator/README.md#the-witwaveworkspace-resource).

## Interactive TUI

`ww tui` opens a `k9s`-style live agent list. The list polls every 2 seconds; agents created / deleted / transitioning
out-of-band (another CLI session, kubectl, Helm, another operator) update in place without a keystroke.

Keybindings on the list:

| Key            | Action                                                                                                          |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| `↑` / `↓`      | Move selection                                                                                                  |
| `a`            | Open the create-agent modal — long-form: name, namespace, backend, team, auth, gitOps repo                      |
| `d`            | Open the delete-confirm modal — three checkboxes for `--remove-repo-folder`, `--delete-git-secret`, `--purge`   |
| `l`            | Drill into the selected agent's logs — aggregate-across-containers by default; `c` cycles individual containers |
| `r`            | Force-refresh the snapshot                                                                                      |
| `↵`            | Reserved for the per-agent details view (status / events / send / config); flashes a stub hint until that lands |
| `q` / `Ctrl-C` | Quit                                                                                                            |
| `ESC`          | Page-aware: in logs / modal → back; on the list → quit                                                          |

The create modal's auth picker mirrors the CLI's four modes (`none` / `profile` / `from-env` / `existing-secret` /
`set-inline`); the `set-inline` mode takes a comma-separated list of `KEY=VALUE` pairs in the value field, equivalent to
the CLI's `--auth-set <backend>:KEY=VALUE`.

Defaults pre-fill the create modal from a layered resolution (env vars > saved last-used > fallback). `WW_TUI_DEFAULT_*`
env vars exported in your shell pin values; otherwise the form remembers your last successful create. Saved values live
in the `[tui.create_defaults]` block of `~/.witwave/config.toml`.

## Config

Config lives at `$XDG_CONFIG_HOME/ww/config.toml`, falling back to `~/.config/ww/config.toml`. TOML shape:

```toml
[profile.default]
base_url  = "http://localhost:8000"
token     = "..."
run_token = "..."
timeout   = "30s"

[profile.prod]
base_url  = "https://witwave.example.com"
token     = "..."
```

Precedence, high to low:

1. Command-line flag (`--base-url`, `--token`, `--run-token`, `--timeout`, `--profile`).
2. Environment variable (`WW_BASE_URL`, `WW_TOKEN`, `WW_RUN_TOKEN`, `WW_TIMEOUT`, `WW_PROFILE`).
3. Config file profile (selected by `--profile` / `WW_PROFILE`, default `default`).
4. Compiled-in default (`http://localhost:8000`, 30 s timeout).

### Config file discovery

The config file is resolved in this order (first match wins):

1. `--config <path>` CLI flag
2. `WW_CONFIG` env var
3. `$HOME/.witwave/config.toml` _(preferred default — brand-aligned dotfile dir)_
4. `$XDG_CONFIG_HOME/ww/config.toml`
5. Platform user-config dir (`~/.config/ww/config.toml` on Linux, `~/Library/Application Support/ww/config.toml` on
   macOS, `%AppData%\ww\config.toml` on Windows)

The CLI creates `$HOME/.witwave/config.toml` on the first `ww config set` when no existing file is found.

### Managing config from the CLI

Use `ww config` to read, write, and inspect config values without opening the file by hand:

```bash
ww config path                                         # where the file lives
ww config list-keys                                    # valid keys + value shapes
ww config set update.mode notify                       # persist a value
ww config set profile.default.base_url https://.../    # persist a URL
ww config get update.channel                           # read current value
ww config unset update.mode                            # remove a key
```

Every value is validated against the key's schema before being written (mode must be one of `off/notify/prompt/auto`,
channel must be `stable` or `beta`, duration strings must parse, base URLs must have a scheme). The file is created with
mode `0600` on first write because bearer tokens live plaintext inside.

Ad-hoc run endpoints use `run_token` when set; otherwise `ww` falls back to `token` and logs a warning to stderr. Set
both when you have a harness that distinguishes them.

## Staying up to date

`ww` checks once per day (cached on disk) whether a newer release is available and prints a one-line banner after the
command runs:

```text
↑ ww v0.5.0 is available (you're on v0.4.0). https://github.com/witwave-ai/witwave/releases/tag/v0.5.0
  To upgrade: brew upgrade ww
```

The upgrade instruction is tailored to how `ww` was installed — Homebrew taps get `brew upgrade ww`, `go install` users
get the matching `go install` command, curl-installer users get a `curl … | sh` re-run of the same install script
(detected via the sibling `.ww.install-info` marker file), and any other standalone binary gets a download URL.

### Configuration

```toml
[update]
mode     = "notify"   # off | notify | prompt | auto
interval = "24h"      # cache TTL between GitHub API calls
channel  = "stable"   # stable | beta
```

| Mode     | Behavior                                                                             |
| -------- | ------------------------------------------------------------------------------------ |
| `off`    | No check, no network, no banner                                                      |
| `notify` | _(default)_ Print the banner after the command                                       |
| `prompt` | Print the banner, ask `Upgrade now? [Y/n]`, and run the matching installer on `Y`    |
| `auto`   | Print the banner and run the matching installer without asking — unattended upgrades |

`prompt` auto-downgrades to `notify` when stdin is not a TTY (scripts, pipelines, CI). `auto` is only recommended if
you've explicitly opted in to unattended upgrades.

Channels:

- `stable` — only surfaces non-prerelease tags. Users on a beta still get notified when a stable release ships, because
  per SemVer `v0.4.0 > v0.4.0-beta.N`.
- `beta` — includes prereleases (`v*-beta.N`, `v*-rc.N`). Use this to track the bleeding edge.

### Upgrade on demand

The config-driven banner is passive. To trigger an upgrade explicitly (without waiting for the next check cycle or
flipping `mode`), run:

```bash
ww update             # check + upgrade if newer
ww update --check     # check only, don't upgrade
ww update --force     # skip the check; always run the upgrade
```

`ww update` delegates to the installer matching the current binary's provenance (`brew upgrade ww` for Homebrew,
`go install ...@latest` for `go install` users, a re-run of the curl install pipeline for curl-installer users, a
download hint for any other standalone binary). Works even when `mode = off` — the subcommand is an explicit request,
not a passive check.

### Environment overrides

All of these win over `config.toml`:

| Variable               | Effect                                           |
| ---------------------- | ------------------------------------------------ |
| `WW_UPDATE_MODE`       | Override `mode` (`off`/`notify`/`prompt`/`auto`) |
| `WW_UPDATE_CHANNEL`    | Override `channel` (`stable`/`beta`)             |
| `WW_UPDATE_INTERVAL`   | Override `interval` (duration string)            |
| `WW_NO_UPDATE_CHECK=1` | Force mode = `off` for this run                  |

The check is also force-disabled when any of `CI`, `GITHUB_ACTIONS`, `BUILDKITE`, `CIRCLECI`, or `GITLAB_CI` is truthy —
automated runners never get banners or prompts. A version-check failure (network down, API outage, JSON parse error) is
always silent and never interferes with the actual command.

## Output modes

- Default: colored, tabular human output when stdout is a TTY. Colors disabled automatically when stdout is piped or
  `NO_COLOR` is set.
- `--json`: pretty-printed JSON for snapshot commands, one JSON object per line for streams. Add `--compact` to collapse
  snapshot JSON to a single line.
- Errors always go to stderr. Exit codes:

  | code | meaning                                                     |
  | ---- | ----------------------------------------------------------- |
  | 0    | success                                                     |
  | 1    | logical error — 4xx, validation failed, target not found    |
  | 2    | transport error — network, timeout, auth, 5xx after retries |

## Verbose tracing

`-v` logs each request line + status to stderr. `-vv` additionally dumps request and response bodies (truncated at 4 KiB
per direction).

## Design rules

Design invariants for the CLI (kubeconfig handling, command taxonomy, flag conventions, exit codes) live in
[DESIGN.md](DESIGN.md). Read it before adding a new command; cite rule numbers (`KC-3`, `TAX-1`, …) in PRs when a change
touches one.

## Building from source

```bash
go build -ldflags "\
  -X 'github.com/witwave-ai/witwave/clients/ww/cmd.Version=0.1.0' \
  -X 'github.com/witwave-ai/witwave/clients/ww/cmd.Commit=$(git rev-parse --short HEAD)' \
  -X 'github.com/witwave-ai/witwave/clients/ww/cmd.BuildDate=$(date -u +%Y-%m-%dT%H:%M:%SZ)' \
" -o bin/ww .
```

## Implementation notes

- `ww status` hits the harness `/agents` endpoint. A dashboard-proxied `/api/team` endpoint also exists in Witwave
  deployments and returns the same information in a slightly different shape — switching to it is a follow-up once `ww`
  grows a dashboard-proxy mode.
- The SSE parser is intentionally minimal — it implements the subset the harness emits plus the `:` keepalive comment
  used to keep HTTP/2 proxies awake. Field-name-only lines per the broader SSE spec are tolerated but not exercised.
- Goreleaser config ships darwin/linux amd64+arm64 builds and a Homebrew **cask** targeting `witwave-ai/homebrew-ww`
  (migrated from the deprecated `brews:` block via #1446 — casks are macOS-only). The tap repo exists;
  `goreleaser release` additionally requires the `HOMEBREW_TAP_GITHUB_TOKEN` repo secret (a fine-grained PAT scoped to
  the tap with Contents: Read-and-Write) — without it, the cask push step fails but the binaries + GitHub Release still
  ship.
