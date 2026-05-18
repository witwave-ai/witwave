# Milo

Milo is a planned **Agent Resources** member for the Witwave self-team. He is not deployed yet. His role is to keep the
agent roster itself coherent: onboarding readiness, profile consistency, credential readiness checks, role boundaries,
safe pause/decommission paths, and bounded pod lifecycle actions when an approved workflow needs them.

Mira watches whether the platform is healthy enough for agents to run. Milo will watch whether the team is healthy
enough for the roster to make sense.

## Planned responsibilities

- Verify new-agent readiness: identity docs, public card, avatar, GitHub account, SOPS file, bootstrap command, website
  card, and memory namespace.
- Audit profile and roster consistency across GitHub profiles, `.agents/self/`, bootstrap docs, the public website, and
  agent cards.
- Check token/account readiness without exposing secrets.
- Help clarify role boundaries when responsibilities overlap or a new agent is being considered.
- Prepare safe checklists for pausing, retiring, renaming, or replacing agents.
- Use namespace-scoped Kubernetes write access for approved pod lifecycle actions, such as evicting or deleting stuck
  agent pods, without touching secrets, RBAC, raw pod creation, or cluster-scoped resources.

## Current state

Milo is scaffolded with a GitHub account, avatar, encrypted `agent.sops.env`, draft identity, and public card. He is not
deployed yet. His bootstrap path should grant `namespaceWrite` Kubernetes API access, while his heartbeat remains
disabled until a real lifecycle skill is ready.
