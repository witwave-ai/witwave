# tools/ — MCP components

Every subdirectory here is an **MCP component**, treated equally regardless of what it wraps. "MCP" is the component
name in this repo — each server speaks the Model Context Protocol and is consumed by backends via their MCP
configuration (`mcp.json` under `.claude/`, `.openai/`, or `.gemini/` — all three LLM backends use the same wire format;
the `echo` backend has no MCP integration by design).

MCP servers are designed to run inside the Kubernetes cluster where witwave is deployed and operate against that cluster
via in-cluster credentials (ServiceAccount token + RBAC). They are shared infrastructure — one deployment typically
serves every agent in the cluster rather than being replicated per-agent.

## Current MCP components

| Component        | Directory                  | Image                   |
| ---------------- | -------------------------- | ----------------------- |
| `mcp-kubernetes` | [kubernetes/](kubernetes/) | `mcp-kubernetes:latest` |
| `mcp-helm`       | [helm/](helm/)             | `mcp-helm:latest`       |
| `mcp-prometheus` | [prometheus/](prometheus/) | `mcp-prometheus:latest` |

## Conventions

- One directory per component.
- Each ships a `Dockerfile`, a `server.py` MCP entrypoint, and a `requirements.txt`.
- Image tag is always `mcp-<dirname>:latest`.
- Servers target the **deployed** cluster only — no support for arbitrary remote kubeconfigs. Auth is in-cluster by
  default; access is whatever the ServiceAccount's RBAC allows.
- Stable component names are referenced by agents in their MCP configuration.
- Register new components in the `Building Images` section of `AGENTS.md` and tag related issues/PRs with the `mcp`
  GitHub label.

## Bearer-token auth (#771)

Every MCP tool server enforces bearer-token auth via the shared `shared/mcp_auth.py` middleware. Set
`MCP_TOOL_AUTH_TOKEN` on each tool container; when the token is unset/empty and `MCP_TOOL_AUTH_DISABLED` is not
explicitly set, the server refuses requests. Use `MCP_TOOL_AUTH_DISABLED=true` only for local dev — the startup log is
loud on purpose.

## Chart schema coverage (#973)

`charts/witwave/values.schema.json` now validates `mcpTools.<name>.rbac.{create,rules}` so a typo in the RBAC block
fails `helm template` / `helm install --dry-run` loudly instead of silently yielding a tool pod with a ServiceAccount
but no Role binding.

## Health endpoint

Every MCP tool server exposes `GET /health` on the same port as the MCP endpoint (default `8000`) for liveness /
readiness probes.

## Digest pinning (#855)

The `witwave` Helm chart accepts a `digest` field alongside `repository`/`tag` on every MCP tool
(`mcpTools.<name>.image.digest`). When set the template renders `repository@<digest>` and `tag` is ignored. Prefer an
immutable digest over a rolling tag in production: MCP pods typically hold a cluster ServiceAccount token, and a
re-tagged upstream image could be pulled silently with vulnerable code.

```yaml
mcpTools:
  kubernetes:
    enabled: true
    image:
      repository: ghcr.io/witwave-ai/images/mcp-kubernetes
      digest: sha256:abc123...
```
