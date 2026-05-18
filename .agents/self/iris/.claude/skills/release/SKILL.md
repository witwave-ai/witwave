---
name: release
description: >-
  Cut a stable or beta release of the primary repo. Verifies main is CI-green, infers the next version from commit
  history, updates the CHANGELOG (stable only), tags, pushes the tag, and surfaces the release URLs. Refuses to ship on
  a red CI; refuses to auto-bump major. Trigger when the user or a sibling agent says "release", "ship it", "cut a
  version", or "release beta".
version: 0.1.0
---

# release

Cut a tagged release of the primary repo. The repo's release pipeline is tag-driven: pushing a `vX.Y.Z` tag fires three
workflows that publish container images, the `ww` CLI binary + Homebrew formula, and the Helm charts. This skill's job
is the safe pre-tag work and the tag itself; the publishing happens in CI automatically once the tag lands.

The repo URL, local checkout path, and default branch all come from your **Primary repository** section in CLAUDE.md.
Generic across the self-agent family — same skill works for any agent whose CLAUDE.md declares a primary repo with a
tag-driven release pipeline.

## Pre-1.0 caveat

The project is currently **pre-1.0** (current line is `0.x.y`). This skill is designed for the pre-release reality: no
API-stability commitment to consumers, breaking changes are normal, and they fold into minor bumps without ceremony.
Major bumps in pre-1.0 mean one specific thing — **going to `v1.0.0`** — and require explicit caller intent because
that's a project-state decision (committing to API stability), not something inference can answer.

When the project graduates to 1.0+, the bump-inference rules will need a revisit so that `BREAKING CHANGE:` and `!:`
markers refuse auto-bump and demand explicit `release major`. That's a future edit; today's skill is shaped entirely
around 0.x semantics.

## Caller interface

The skill is invoked via a session prompt or A2A. Six request shapes:

| Phrase                                | Behavior                                                     |
| ------------------------------------- | ------------------------------------------------------------ |
| `release`                             | Stable, inferred bump (patch or minor — refuses on breaking) |
| `release beta`                        | Beta-line cut, inferred bump within or starting a beta line  |
| `release patch`                       | Stable, force patch bump                                     |
| `release minor`                       | Stable, force minor bump                                     |
| `release major`                       | Stable, force major bump (the only way to bump major)        |
| `release vX.Y.Z` (or `vX.Y.Z-beta.N`) | Use that exact version verbatim                              |

The default `release` is the common case. Explicit overrides exist for the rare case where commit-based inference is
wrong, or when a breaking change has been gated behind an explicit major bump.

## Instructions

Read the following from CLAUDE.md's Primary repository section:

- **`<checkout>`** — local working-tree path
- **`<branch>`** — default branch (typically `main`)

### 1. Sync source

Invoke the `git-sync-source` skill first. The release must be cut against the up-to-date branch — never assume the tree
is fresh, and never tag a stale commit. If sync-source surfaces a conflict or divergence, **stop**: a release on a
divergent tree is not safe.

### 2. Verify clean state

```sh
git -C <checkout> status --porcelain
git -C <checkout> log origin/<branch>..<branch>
```

The first command should print nothing (no working-tree changes). The second should print nothing (no unpushed local
commits). If either has output, **stop and surface**:

> "Refusing to release — local checkout has [uncommitted changes / > unpushed commits]. Push or revert before
> re-invoking."

### 3. Verify CI is green across every workflow on `main`

Query the **latest run per workflow on `main`**, not just runs on this exact commit. Path-filtered workflows (e.g.
`CI — ww CLI` only triggers when `clients/ww/**` changes) won't have a run on a release commit that doesn't touch those
paths — and the workflow's last failing run on an earlier commit silently passes the on-this-commit-only check even
though the code that workflow tests is still broken in the tree.

This gap is exactly what let v0.17.0 cut on 2026-05-07 while `CI — ww CLI` had been failing for ~1h45m on three prior
commits. The release commit (`a2fc8777`, a `docs(changelog):`-only commit) didn't itself run `CI — ww CLI`, the
on-commit check found the runs that DID fire all green, and iris tagged.

Today's check covers the workflow surface:

```sh
HEAD_SHA=$(git -C <checkout> rev-parse HEAD)

# Per-workflow latest status on main, regardless of which commit triggered each.
gh run list --branch <branch> --limit 50 \
  --json name,status,conclusion,url,headSha,createdAt \
  --jq 'group_by(.name) | map(sort_by(.createdAt) | last) | .[]'
```

Tabulate per-workflow status:

- **Every workflow's latest run `conclusion = "success"`**: proceed to step 4.
- **Any `status` in {`in_progress`, `queued`}**: STOP. Surface the list of still-running workflows and ask the caller to
  re-invoke once they finish.
- **Any `conclusion` in {`failure`, `cancelled`, `timed_out`}**: STOP. **Refuse to tag**, regardless of whether that
  workflow's latest run was on the release commit or an earlier one. The failing workflow's tested code lives in the
  tree we're about to release; the artifact built from this tree will inherit that breakage.

Surface the failure with both the failing commit and the release commit so the caller can see the gap:

> "Refusing to release — main is not green:
>
> - `CI — ww CLI` failed (last run on commit `0dbd02b9`, <https://github.com/.../runs/...>)
> - `CI — Charts` succeeded
> - `CI — docs` succeeded (latest on HEAD `<short-sha>`)
>
> The `CI — ww CLI` failure is older than HEAD but tests code that's still in the tree. Fix or revert main, wait for
> that workflow to re-run green, then re-invoke `release`."

The future build-fixer agent we'll eventually delegate to is **evan**, who already has the bug-class fix-bar machinery
for exactly this kind of failure. For now this skill keeps the refuse-to-tag posture; zora's Priority 1 red-CI logic
should already be dispatching evan against the failing commit independently of this release skill's gate.

#### Caveat: the changelog commit's own CI fires asynchronously

The CI-green check above covers HEAD as it stands BEFORE the changelog commit (step 7 below) lands. The tag eventually
points at the changelog commit, which is brand-new and not yet CI-tested at tag-push time. In practice this window is
safe — `docs(changelog):` commits only edit `CHANGELOG.md` and any failure would be a `CI — docs` markdown-lint class
issue that doesn't affect runtime artifacts. If the changelog-commit's CI does go red post-tag, the release artifacts
still publish (they fire on tag, not on CI green); fix the markdown in a follow-up commit. Don't let the race block the
release.

### 4. Determine current version + infer bump

```sh
PREV_TAG=$(git -C <checkout> describe --tags --abbrev=0)
git -C <checkout> log "${PREV_TAG}..HEAD" --format="%s%n%b"
COMMIT_COUNT=$(git -C <checkout> rev-list --count "${PREV_TAG}..HEAD")
```

The first command gives the previous tag (e.g. `v0.11.16`). The second gives every commit's subject + body since that
tag. The third counts them.

#### Refuse on zero commits

If `COMMIT_COUNT` is 0, **stop and surface**:

> "Refusing to release — no commits between `<prev-tag>` and HEAD. Last tag is `<prev-tag>`; there's nothing new to
> release."

Catches the case where a caller fires `release` immediately after a prior release with no work in between.

#### Inference table

Apply this inference table to non-zero commit counts:

| Pattern in commits since previous tag                        | Inferred bump |
| ------------------------------------------------------------ | ------------- |
| `feat(...)`, OR `BREAKING CHANGE:`, OR `!:` after scope      | **minor**     |
| Anything else (`fix:`, `refactor:`, `chore:`, `docs:`, etc.) | **patch**     |

Pre-1.0, breaking markers fold into the minor bump along with features — they're routine at this stage and don't demand
a special gate. Mention any breaking markers explicitly in the bump rationale (step 10) so the caller still sees what's
in the release, but don't refuse over them.

If the caller passed an explicit override (`release patch` / `release minor` / `release major` / `release vX.Y.Z`), use
that verbatim and skip inference.

Inference will NEVER produce a major bump in pre-1.0. Going to `v1.0.0` is a deliberate caller decision — the only path
is an explicit `release major` (which the skill interprets as "cut v1.0.0") or an explicit `release v1.0.0`.

### 5. Compute the next version

Apply the bump (inferred or explicit) to the previous tag.

For **stable mode** (no `beta` keyword in the request):

| Previous tag     | Bump  | Next                                  |
| ---------------- | ----- | ------------------------------------- |
| `v0.11.16`       | patch | `v0.11.17`                            |
| `v0.11.16`       | minor | `v0.12.0`                             |
| `v0.11.16`       | major | `v1.0.0`                              |
| `v0.12.0-beta.3` | (any) | `v0.12.0` (graduate — drop `-beta.N`) |

For **beta mode** (caller said `release beta`):

| Previous tag            | Next                                                                                     |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `v0.11.16` (stable)     | inferred-next + `-beta.1` (e.g. `v0.11.17-beta.1` for patch, `v0.12.0-beta.1` for minor) |
| `v0.12.0-beta.3` (beta) | `v0.12.0-beta.4` (bump beta number, keep target stable version)                          |

If the caller passed an explicit version (`release v0.12.0` or `release v0.12.0-beta.5`), parse and validate it (must
start with `v`, must be valid semver), then use verbatim.

### 6. Update CHANGELOG.md (stable only — skip for betas)

This step runs only in stable mode. Beta releases do NOT update CHANGELOG.md — `[Unreleased]` keeps accumulating across
the beta cycle and gets renamed when the stable graduates.

For stable releases:

a. Read `<checkout>/CHANGELOG.md`. The file follows Keep a Changelog format with an `## [Unreleased]` section at the
top.

Before continuing, **ensure git identity is set on the checkout** — invoke the `git-identity` skill (it's idempotent;
safe to run even if already configured). Without `user.name` / `user.email`, step g's `git commit` would fail with
"Please tell me who you are."

b. Determine the source range for the changelog entry. Use the most recent **stable** tag (skip beta / rc tags), not
just the most recent tag overall:

```sh
LAST_STABLE_TAG=$(git -C <checkout> tag --list 'v*' \
  --sort=-v:refname | grep -v -- '-' | head -1)
```

The `grep -v -- '-'` filters out anything with a SemVer pre-release qualifier (`-beta.N`, `-rc.N`, etc.). The changelog
range is `${LAST_STABLE_TAG}..HEAD` — covering every commit since the previous stable, including any commits that landed
during beta cycles between then and now.

(Note: this differs from step 4's bump inference, which uses the most recent tag overall — correct, because bump
inference cares about "what changed since the last release of any kind." The changelog cares about "what changed since
the last stable" so beta-graduation entries don't lose their beta-cycle commits.)

c. Generate entries for the new version from the commit log between `LAST_STABLE_TAG` and `HEAD`. Group by
Keep-a-Changelog section using commit-scope prefix:

| Commit prefix                | Section                                                                                                                                                                                                                                                                                          |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `feat(...)`                  | **Added**                                                                                                                                                                                                                                                                                        |
| `fix(...)`                   | **Fixed**                                                                                                                                                                                                                                                                                        |
| `refactor(...)`              | **Changed**                                                                                                                                                                                                                                                                                      |
| `revert(...)`                | **Reverted** (non-standard but informative)                                                                                                                                                                                                                                                      |
| `agents(...)`                | **Agent identity** (this repo's witwave-self ecosystem)                                                                                                                                                                                                                                          |
| `docs(...)`                  | **Documentation** (only when substantive — skip pure prose churn)                                                                                                                                                                                                                                |
| `chore:` / `test:` / pure-CI | Skip unless user-visible                                                                                                                                                                                                                                                                         |
| `BREAKING CHANGE:` / `!:`    | Place in **Changed** alongside other refactors — pre-1.0 doesn't bold-prefix breaking entries (per the CHANGELOG preamble's plain treatment of "minor bumps may introduce user-visible behaviour changes"). When the project graduates to 1.0+ this becomes a "BREAKING:" prefix in **Changed**. |

Inside each section, sub-group by component (the parenthesised scope: `feat(ww):` → `**ww**:` bullet, `fix(operator):` →
`**operator**:` bullet). One concise prose line per scope-bucket, not a verbatim commit-list dump.

d. **Handle existing `[Unreleased]` content**: if the `## [Unreleased]` section already has bullets / paragraphs (e.g.
beta-cycle accumulation, or someone hand-wrote entries during the release window), **merge them into the new version's
entry** — integrate manually-written prose with the auto-generated commit-derived entries, deduping where they describe
the same change. Then leave `## [Unreleased]` empty for the next cycle. Never silently discard hand-written
`[Unreleased]` content.

e. Insert the new entry **between** the now-empty `## [Unreleased]` and the next `##` heading. The result:

```markdown
## [Unreleased]

## [X.Y.Z] — YYYY-MM-DD

...
```

f. Format the entry body:

```markdown
## [X.Y.Z] — YYYY-MM-DD

<optional one-paragraph context intro when commits cluster around a coherent theme; omit when entries are mixed and
prose would feel forced>

### Added

- **<component>**: <prose summary> (#issue if present)

### Fixed

- **<component>**: <prose summary>
```

g. **Format with prettier (mandatory pre-flight).** Run prettier against `CHANGELOG.md` so the auto-generated entry
matches what `CI — Docs` enforces on the tagged SHA:

```sh
cd <checkout> && npx --yes prettier@3.4.2 --write CHANGELOG.md
```

**Pinned to `3.4.2` exactly — NOT `@latest`, NOT generic `prettier`, NOT `prettier@^3`.** The version must match the pin
in `.github/workflows/ci-docs.yml`; later majors disagree on wrap + blank-line-before-list rules and silently produce
output that `prettier@3.4.2 --check` will then reject. This step closes the recurring CHANGELOG-prettier red-CI pattern
(5 burned tags across v0.23.20 / v0.23.22 / v0.25.0 / v0.27.0 / v0.27.5) — the release workflows gate strictly on
`CI — Docs` succeeding on the tag SHA, and a bad wrap in the auto-generated entry permanently strands the tag (no
fix-forward on `main` HEAD unsticks an already-tagged SHA).

h. Stage and commit:

```sh
git -C <checkout> add CHANGELOG.md
git -C <checkout> commit -m "docs(changelog): cut vX.Y.Z"
```

### 7. Push the changelog commit (stable only)

```sh
git -C <checkout> push origin <branch>
```

If the push is rejected non-fast-forward (sibling pushed first), delegate to the `git-push` skill — it handles the
fetch + rebase + retry flow. Do not improvise alternative resolutions.

If the rebase rewrites the changelog commit's parent, that's fine — the entry content doesn't depend on any specific
upstream state.

### 8. Tag

```sh
git -C <checkout> tag -a vX.Y.Z -m "Release vX.Y.Z"
```

Annotated tag (`-a`) so the tag carries a message and timestamp. Beta tags use the same form:
`git tag -a vX.Y.Z-beta.N -m "Release vX.Y.Z-beta.N"`.

### 9. Push the tag

```sh
git -C <checkout> push origin vX.Y.Z
```

This is the action that fires the release workflows. From this point the operation is partially-irreversible — a pushed
tag can be deleted (`git push --delete origin vX.Y.Z`) but anyone who pulled it gets confused, and the workflows have
already started.

### 10. Surface release info

Respond to the caller with:

- The new version
- The bump rationale (e.g. "Inferred minor — found `feat(ww):` in 3 commits since v0.11.16")
- The tag URL (`https://github.com/<owner>/<repo>/releases/tag/<tag>`)
- The three release-workflow URLs (look up via `gh run list --workflow=<file>.yml --branch <tag> --limit 1`)
- The artifact channels each workflow publishes to, so the caller knows what to consume and where

The artifact matrix this repo's three workflows produce on every tag push:

| Workflow                                 | Publishes                                                                                                                                                                | Channel                                                                      | ETA  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- | ---- |
| Release (release.yaml)                   | container images: harness, claude, codex, gemini, echo, mcp-kubernetes, mcp-helm, mcp-prometheus, git-sync (and dashboard when chart-enabled) — multi-arch amd64 + arm64 | `ghcr.io/witwave-ai/images/<name>:<X.Y.Z>`                                   | ~24m |
| Release — ww CLI (release-ww.yml)        | ww CLI binary archives (Linux/macOS/Windows × amd64/arm64), Homebrew formula update, GitHub Release with auto-generated notes                                            | GitHub Releases + Homebrew tap (`brew upgrade ww`)                           | ~5m  |
| Release — Helm charts (release-helm.yml) | charts/witwave + charts/witwave-operator, OCI-pushed                                                                                                                     | `ghcr.io/witwave-ai/charts/witwave:<X.Y.Z>` and `…/witwave-operator:<X.Y.Z>` | ~5m  |

Sample success response:

> "Released v0.12.0.
>
> Bump rationale: minor — found `feat(ww):` in 3 commits since v0.11.16 (no breaking markers).
>
> Tag: <https://github.com/witwave-ai/witwave/releases/tag/v0.12.0>
>
> Workflows in flight:
>
> - Release (container images, ~24m): <https://github.com/.../runs/...>
> - Release — ww CLI (binary + brew, ~5m): <https://github.com/.../runs/...>
> - Release — Helm charts (witwave + witwave-operator, ~5m): <https://github.com/.../runs/...>
>
> Once green, artifacts land at:
>
> - `ghcr.io/witwave-ai/images/<name>:0.12.0` (9 images: harness, claude, codex, gemini, echo,
>   mcp-{kubernetes,helm,prometheus}, git-sync — plus dashboard if chart-enabled)
> - `brew upgrade ww` (Homebrew tap auto-updated)
> - `ghcr.io/witwave-ai/charts/witwave:0.12.0` and `ghcr.io/witwave-ai/charts/witwave-operator:0.12.0`
>
> Reply 'watch release' to block until they complete."

#### What the skill does NOT do for artifacts

The release workflows handle several housekeeping tasks at build time. None of them are iris's job:

- **Embedded operator chart version bump**: goreleaser invokes `scripts/bump-embedded-chart-version.sh` so the embedded
  chart inside the `ww` binary's `clients/ww/internal/operator/embedded/` reports the release version, not the canonical
  `0.1.0` placeholder.
- **GitHub Release notes**: goreleaser auto-generates these from commit messages (filtered: `^docs:`, `^test:`,
  `^chore:` excluded). This is a separate artifact from the in-repo CHANGELOG.md.
- **SLSA provenance + SBOM emission**: `release.yaml` and `release-ww.yml` produce these as OCI referrers and
  release-asset attestations on every tag. Cosign signatures land alongside.
- **Dashboard image build**: only fires when chart values enable it; not part of iris's flow either way.
- **Multi-arch manifest assembly**: buildx publishes both `amd64` and `arm64` variants under the same tag automatically.

If any of these need adjustment, that's a workflow / goreleaser config edit (a code change), not a release-skill change.

### 11. Watch every release workflow to conclusion (mandatory)

A pushed tag is the START of the release, not the end. The three workflows that fire on the tag (`Release`,
`Release — ww CLI`, `Release — Helm charts`) publish the actual artifacts users pull — container images, ww binaries +
Homebrew formula, OCI Helm charts. **If any of those workflows fail after the tag is out, the team has shipped a partial
release.** Catastrophic failure modes:

- Container image push fails → `harness:vX.Y.Z` absent from ghcr.io. Any WitwaveAgent CR bumped to that tag goes
  `ImagePullBackOff`. The team's own deploy chain is the first victim.
- Homebrew formula push fails → `brew upgrade ww` says "no newer version available" silently, or pulls a broken cask
  from a partial tap update.
- Helm chart push fails → `helm upgrade` on that version errors with "chart not found." Existing installs are stuck on
  the prior release.

Every one of these is silent today — `git tag` succeeds, the GitHub release page shows up, but the downstream artifact
channel is broken. We've been bitten by this enough that the skill's posture is now:

**You DO NOT return success from the release skill until every release workflow has completed AND every conclusion is
`success`.**

```sh
# Resolve the run IDs of the workflows that fired on this tag.
TAG_SHA=$(git -C <checkout> rev-list -n 1 v<version>)
RUN_IDS=$(gh run list --branch <branch> --commit "$TAG_SHA" \
  --json databaseId,name --jq '.[] | select(.name | startswith("Release")) | .databaseId')

# Stream each to completion. --exit-status returns non-zero if the run failed.
for RUN_ID in $RUN_IDS; do
  gh run watch "$RUN_ID" --exit-status
done
```

Three branches per workflow:

- **All `success`** → release is complete. Surface the artifact URLs (container images on ghcr.io, releases page on
  GitHub, brew tap commit). Return success to the caller.
- **Any `in_progress` / `queued` past the watch budget** (default 30 min per workflow) → surface as
  `[release-workflow-pending]` with the still-running run URLs. Caller (zora) decides whether to keep waiting or
  escalate.
- **Any `failure` / `cancelled` / `timed_out`** → return failure to the caller with:

  ```text
  [release-workflow-failed]
    tag: vX.Y.Z (commit <short-sha>)
    failed: Release — ww CLI (<run-url>)
    succeeded: Release, Release — Helm charts
    artifacts published: ghcr.io/witwave-ai/images/{harness,claude,...}:vX.Y.Z, charts on OCI
    artifacts MISSING: ww CLI binaries on releases page, brew tap update
    next step: caller (zora) decides — re-run failed workflow, or cut vX.Y.Z+1 with a fix
  ```

  Iris does NOT auto-retry the failed workflow, does NOT delete the tag, does NOT attempt to recover the partial
  release. **Iris's job ends at surfacing.** Zora is the team's manager — she's the one who decides what the team does
  in response. When she sees `[release-workflow-failed]` in iris's reply, her policy (CLAUDE.md → "Never leave a broken
  build" + dispatch-team Priority 1) takes over: stop cadence-driven dispatching, redirect the team to fix the
  underlying cause, surface to the user via `escalations.md`.

  **The contract is iris-reports / zora-decides.** Don't replicate zora's decision logic in this skill — faithfully
  surfacing the conclusions is enough.

## Failure handling

- **Source-sync failure** (step 1): pass through whatever `git-sync-source` surfaces; do not retry independently.
- **Dirty tree / unpushed commits** (step 2): refuse and surface; do not stash, reset, or push without caller direction.
- **CI not green** (step 3): refuse and surface workflow URLs. Do not retry, do not auto-rerun failed workflows, do not
  delegate to a build-fixer (no such agent exists yet — placeholder for future work).
- **Zero commits since last tag** (step 4): refuse and surface; there's nothing new to ship.
- **Tag push rejected** (step 9): rare (would mean someone else pushed the same tag concurrently). Surface and stop.
- **Workflow failure after tag push** (step 11, if watching): tag is already out, can't be safely undone. Surface the
  failure with the workflow URL and ask the caller for direction (re-run, hotfix patch release, etc.).

## Out of scope for this skill

- Fixing a red CI before release (escalation path TBD; surfaces and stops for now)
- Auto-reverting commits to make CI green
- Force-tagging or moving an existing tag
- Tag deletion (cleaning up a misfired release)
- Generating GitHub release notes (goreleaser handles this from commit messages on its own — no skill work needed)
- Bumping versions in package files (Helm Chart.yaml, etc.) — goreleaser and the embedded-chart sync script handle
  versioning at build time, the skill doesn't touch source-tree version refs
- Cross-repo releases (this skill releases the primary repo only)
- Communicating with sibling agents to coordinate a release window (caller's responsibility, not the skill's)
- **Hotfixes on old release lines** — patches cut from a previous major/minor (e.g. shipping `v0.11.17` after `v0.12.0`
  is already out for a critical fix on the v0.11 line). Requires branching off the old tag, cherry-picking, and tagging
  from the branch — none of which this skill does. Pre-1.0 the need is rare; revisit if it comes up.
