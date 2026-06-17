---
name: risk-work
description:
  Find and fix risks across all five categories — security (CVEs / secrets / insecure patterns), reliability (missing
  timeouts / retries / circuit breakers / silent degradation / unpinned CI tooling), performance (unbounded growth /
  blocking-in-async / poor-scaling ops), observability (silent failures / swallowed error context / undiagnosable
  conditions), and maintainability (deep coupling / duplicated critical logic / undocumented invariants — mostly
  flag-only). Sibling skill to `bug-work` — same single-pass shape (scan → persist → validate → reason as set →
  fix-or-flag → commit per finding → delegate push + CI watch to iris with fix-forward semantics). State lives in
  commits and `project_evan_findings.md` memory only — no GitHub issues, no labels, no multi-session funnel. Trigger
  when the user says "work risks", "fix risks", "find risks", "scan for risks", "do risk work", or specifies depth /
  sections / category (e.g. "fix risks in operator depth 5", "find reliability risks in harness").
version: 0.4.0
---

# risk-work

Single-pass **find AND fix** for risks in the witwave-ai/witwave repo, across all five risk categories.

Sibling to `bug-work`. Same skeleton — atomic per-finding commits, iris-delegated push + CI watch, fix-forward then
revert as fallback, deferred-findings memory with `[pending]/[fixed: <SHA>]/[flagged: <reason>]` markers. Different lens
(risks instead of logic defects), different surface (5 categories instead of correctness analyzer hits).

The lens: **"What works today but is fragile?"** A correctness bug is the code doing the wrong thing on some input right
now; a risk is the code working correctly today but breaking under foreseeable conditions — slow upstreams, unbounded
growth, races that don't manifest in tests, errors that go undiagnosable in production. Risks are also distinct from
gaps (functionality that's _missing_ per existing claims — finn's lens). A risk is something that exists but is
**fragile**.

## Risk categories (5)

The witwave-ai team uses the standard five-category risk taxonomy. Every finding gets categorised as exactly one of
these — the category drives the candidate-detection method and the fix-bar.

1. **Security** — credentials/secrets/tokens in code or config; unvalidated external input; insecure defaults; overly
   permissive access; CVEs in reachable dependencies.
2. **Reliability** — missing timeouts or retries on external calls; no circuit breaking; silent degradation under
   failure; assumptions about external service availability; resource opens without `defer Close()`; race-condition
   smells where the test happens-to-pass but ordering matters; unpinned tool installs in CI workflows that redden `main`
   on otherwise-clean code the moment an upstream tool ships a new release.
3. **Maintainability** — deeply coupled logic that makes changes dangerous; duplicated critical logic with no single
   source of truth; undocumented invariants future contributors are likely to violate. **Mostly flag-only** — the right
   fix is usually a structural refactor that exceeds per-call-site auto-fix scope.
4. **Performance** — unbounded growth (memory, queues, log files, in-memory stores with no cap or eviction); blocking
   calls in async paths; operations that scale linearly when O(1) is feasible from sibling implementations; missing
   pagination on large-result fetches.
5. **Observability** — silent failures with no logging or metrics; error paths swallowing context (no structured fields,
   no `errors.Wrap`); conditions that would be impossible to diagnose in production without code-level instrumentation.

These five originate from the root `.claude/skills/risk-discover.md` framework. Same definitions, different workflow:
**memory markers, not GitHub issues** — that lifecycle was abandoned as too much toil for an autonomous team.

Examples — to anchor the lens:

- **Security:** a CVE'd dependency reachable from public input; a secret committed to the repo; a
  `subprocess.call(..., shell=True)` with user-controlled arguments.
- **Reliability:** an HTTP client without `Timeout` field set; a Kubernetes API call without retry on 429/503; a
  Goroutine that blocks on a channel that no closing path drains; an unpinned `pip install ruff` in a CI workflow that
  breaks `main` the day ruff ships a release.
- **Performance:** a `make(chan T)` (unbuffered) that grows by N from each request; a `dict` that never evicts entries;
  an `await session.execute(...)` inside a loop where bulk queries would do.
- **Observability:** an `except Exception: pass` that drops error context; a Python `try/except/log.error("oops")` that
  loses the actual exception; a control-flow branch that goes unaccounted in `metrics.py`.
- **Maintainability** (flag-only): two identical 50-line functions in different modules with no shared helper; a 12-arg
  constructor with no docstring; a global mutable map referenced from 8+ files.

## Inputs

Same shape as bug-work, plus category filter:

- **`depth`** — integer 1-10. Default `5`. Refuse cleanly if outside 1-10. The default-5 floor matches the depth scale's
  "first depth where all five risk categories actually run" — at depth ≤4 only security and the obvious reliability
  subset are walked, so a default-3 run silently leaves performance / observability / maintainability uninspected.
  Severity-gated auto-fix kicks in at depth ≥5 too (Medium findings auto-fix; below that they flag-only). Direct caller
  invocations that want a cheap pass should pass `depth=1` or `depth=2` explicitly.
- **`sections`** — comma-separated section names or aliases. Default `all-day-one`. Other aliases: `all-deps`,
  `all-python`, `all-go`, `all-backends`, `all-tools`. Refuse cleanly if a section name doesn't match the bug-work
  section list.
- **`categories`** — optional comma-separated subset of
  `security, reliability, maintainability, performance, observability`. Default: all five. Lets a caller bias one run
  (e.g. `categories=reliability` after a recent outage). When unspecified, walk every category at the depth's rigor.

Sections are inherited from bug-work (see `bug-work/SKILL.md` → "Sections"). The same 14 day-one sections plus the 3
v2-deferred ones apply. Two aliases worth calling out for risk-work:

- **`all-deps`** → `harness`, `shared`, all four backends, all three tools (the Python sections that have
  `requirements.txt` files, where `pip-audit` finds direct CVEs). Bias toward the security category.
- **`all-day-one`** → the team's full active set. Default for unbiased multi-category sweeps.

## Depth scale

Depth maps the same way as bug-work: **how hard you hunt for risks.** The fix-bar is depth-independent. Depth gates both
candidate-pool breadth and gauntlet-walk rigor.

| Depth    | What you read per candidate                                                           | Categories surfaced at this depth                                                                                      | Gauntlet rigor                                |
| -------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| **1-2**  | Just the analyzer/grep hit                                                            | Security only — Critical/High CVEs, obvious secret hits                                                                | None — trust the analyzer's database          |
| **3-4**  | ±20-line context window for code findings; `requirements.txt` for dep findings        | Security (Medium CVEs added), obvious reliability (missing timeout on stdlib HTTP/DB clients)                          | #1, #2 (suppressions, mitigated-upstream)     |
| **5-6**  | Full function body; full lockfile chain for transitive deps                           | Security (reachability-gated), reliability (full set), performance (unbounded growth), observability (silent failures) | #1, #2, #3, #4 (reachability, defensive-wrap) |
| **7-8**  | Full source file + caller chain                                                       | All four operational categories at full depth; maintainability (flag-only)                                             | All 8                                         |
| **9-10** | Full subsystem + RBAC manifests + CSP/auth-flow review + cross-cutting refactor scope | Architectural risks (RBAC overreach, missing auth, weak crypto, structural coupling)                                   | All 8 + adversarial threat-model pass         |

**Maintainability findings stay flag-only at every depth.** Their fixes are structural refactors that exceed
per-call-site auto-fix scope; the right home for them is a future architecture-agent or a deliberate refactor session.

## The validation gauntlet (8 concerns, risk-flavoured)

Mirrors bug-work's gauntlet, broadened to cover all five risk categories. Drop the candidate when any concern catches.
The first 5 concerns apply to every category; #6-#8 are category-specific (security analyzer specifics that don't apply
to the four operational categories).

1. **Intentional suppression nearby.** `# noqa: S###` (bandit) / `# nosec` (gosec) / `//nolint:` (golangci-lint), or a
   `#NNNN` GitHub-issue ref explaining the tradeoff, or a comment explaining why the pattern is safe in context — drop.
2. **Already mitigated upstream / at a different layer.** A "missing timeout" candidate is often resolved by
   `context.WithTimeout` further up the call path, or an asyncio `wait_for` wrapper at the caller. A "no retry"
   candidate is often resolved by controller-runtime's outer rate limiter, by a `with_kube_retry` helper, or by an HTTP
   client's built-in retry policy. Read what _calls_ the function before flagging an internal gap.
3. **Cap / sweeper / bound exists elsewhere.** "Unbounded growth" candidates are common false positives — check whether
   a `MAX_*` constant, periodic sweeper, LRU eviction, or `setMaxSize` enforces a bound somewhere in the same file, in a
   shared module, or in the chart's resource limits. A queue without a `maxsize` parameter may still be bounded by the
   caller's rate-limit.
4. **Silent-failure candidates have a metric counter or structured log.** Observability candidates are often false
   because a `_total{reason="..."}` Prometheus counter or structured-log line exists at the failure site. Grep for the
   function name in the section's `metrics.py` / structured logger calls before flagging.
5. **Documented design tradeoff.** A comment near the cited code, or AGENTS.md / module docstring text, explains why the
   pattern is intentional in this codebase. Examples: the harness backends share a metrics surface by design (claude is
   the superset; codex/gemini track placeholders); shared modules like `shared/redact.py` and
   `shared/session_binding.py` are intentionally single-source-of-truth across all backends; the
   default-closed-with-`*_DISABLED=true`-escape-hatch posture for `CONVERSATIONS_AUTH_TOKEN` / `MCP_TOOL_AUTH_TOKEN` /
   `ADHOC_RUN_AUTH_TOKEN` is documented in AGENTS.md. If AGENTS.md / a docstring / a README describes the choice, it's a
   design decision not a risk.
6. **(Security-specific) Reachability from public input.** The vulnerable code path isn't reachable from any
   user-controlled data surface (HTTP handler, A2A message, MCP tool input). `govulncheck` reports this natively for Go;
   for Python reason from the call graph manually at depth ≥5. Unreached vulnerabilities are lower-priority — at depth
   1-4 they still flag; at depth ≥5 reachability gates the fix-bar's severity decision.
7. **(Security-specific) CVE no longer present in the pinned version.** `pip-audit` and `govulncheck` operate on
   lockfiles; if a patched version is already pinned and the analyzer is operating on a stale cache, drop. Re-run the
   analyzer with `--no-cache` to confirm.
8. **(Security-specific) False-positive secret pattern.** `gitleaks` sometimes flags template strings, test fixtures, or
   example payloads in docs as secrets. Validate that the "secret" is actually a live credential (length, entropy,
   plausible key prefix) before flagging.

How rigorously you walk the gauntlet is depth-driven (see depth-scale table). When in doubt, drop. **Quality over
quantity** — a clean output is better than a noisy one.

## The fix-bar (5 rules, depth-independent)

ALL must hold to fix; otherwise the candidate goes to flag bin.

1. **Dep-bump or function-body contained.** Risk fixes that touch lockfiles (`requirements.txt`, `go.mod`,
   `package.json`) are auto-fixable IF the patched version is documented AND the bump is to a non-major release
   (semver-compatible). Code-change fixes follow bug-work's "function-body contained" rule — no public API change, no
   shared-state writes other callers depend on. **Maintainability fixes never satisfy this rule** — they're structural
   by nature, so all maintainability findings flag automatically.
2. **Blast radius.**
   - Dep-bump fixes: read the immediate callers of the dep'd module. If any caller uses an API the bump's release notes
     flag as changed/removed, flag instead.
   - Code-change fixes: read callers + callees once. If the fix changes return-value semantics callers rely on, flag.
3. **Test coverage.** Tests exist for the affected file/path OR (for dep bumps) the using code is exercised by tests.
   **No tests covering the path → flag-only.** Same as bug-work.
4. **Severity meets depth threshold.** Each finding gets a severity band:

   - **Security:** CVSS bands (Critical / High / Medium / Low) for CVEs, analyzer-reported severity (gosec, bandit) for
     code patterns.
   - **Reliability / Performance / Observability:** judgment call based on blast radius and likelihood — Critical
     (would-cause-outage when triggered), High (would-cause-degradation), Medium (would-cause-noise), Low
     (cosmetic/diagnostic-only).
   - **Maintainability:** always Low for fix-bar purposes (always flags anyway via rule #1).

   At depth 1-2: only **Critical + High** auto-fix. At depth ≥5: **Medium** also. **Low** never auto-fixes — always
   flag.

5. **Category gate.** Maintainability findings are flag-only at every depth (rule #1 enforces this). Security findings
   follow the bug-work fix-bar plus reachability gating from gauntlet #6 above. Reliability / performance /
   observability findings auto-fix when the function-body-contained pattern fits — adding a timeout, a `defer Close()`,
   a structured log field, a metric increment, a buffer cap — these are mostly one-line edits that don't widen surface
   area.

A candidate that fails any rule → flag bin. All five pass → fix bin.

## Candidate detection per category

Different categories surface candidates through different methods. Security is analyzer-driven; the four operational
categories (reliability / performance / observability / maintainability) are manual-pattern-driven, supported by
targeted greps.

### Security — analyzer-driven

| Tool          | Invocation                                                                                                        |
| ------------- | ----------------------------------------------------------------------------------------------------------------- |
| `govulncheck` | `cd <section> && govulncheck -mode=source -show=verbose ./...` — reports CVEs reachable from imports              |
| `gosec`       | `cd <section> && gosec -severity high -confidence medium ./...` — high-severity, medium-confidence Go security    |
| `pip-audit`   | `cd <checkout> && pip-audit --requirement <section>/requirements.txt --strict --vulnerability-service osv`        |
| `bandit`      | `bandit -r <section>/ --severity-level high --confidence-level medium --skip B101` (skip assert-used, test idiom) |
| `gitleaks`    | `gitleaks detect --source <section> --no-banner --no-color --report-format json --report-path /tmp/gitleaks.json` |
| `trivy`       | `trivy fs --scanners vuln,secret --severity CRITICAL,HIGH,MEDIUM <section>` — file-system CVE + secret scan       |

For a wide pass (`sections=all-deps`), prefer per-section invocations over a single repo-wide one — keeps each section's
findings attributable in the candidate list.

### Reliability — pattern-matched

Walk the section's source files looking for these patterns. Each grep is a starting point; validate the hit against
gauntlet #2 (mitigated upstream) before flagging.

- **Missing HTTP timeout (Python):** `grep -rn 'requests\.\(get\|post\|put\|delete\|head\|patch\)' <section>/` — every
  match without a `timeout=` kwarg is a candidate.
- **Missing HTTP timeout (Go):** `grep -rn 'http\.Get\|http\.Post\|http\.Client{}' <section>/` — every match without a
  `Timeout:` field is a candidate.
- **Missing context deadline (Go):** functions accepting `ctx context.Context` that call external APIs without a
  `context.WithTimeout` somewhere on the path. Read the call chain.
- **Missing `defer Close()`:** `grep -rn 'os\.Open\|sql\.Open\|http\.Get\|.Body' <section>/*.go` — every open without a
  paired `defer ...Close()` within the function.
- **Race-condition smells:** `sync.Map` mixed with raw map access on the same key; goroutines that read shared state
  without `sync.Mutex` / atomic / channel ownership.
- **Silent degradation under failure:** `except Exception: return None` / `if err != nil { return }` patterns where the
  caller can't distinguish "no result" from "error".
- **Unpinned tool install in CI (repo-global — `.github/workflows/`, not a code section):** scan
  `.github/workflows/*.yml` for tool installs with no version pin — `pip install <pkg>` / `uv pip install <pkg>` /
  `pipx run <pkg>` without `==<version>`, `npm install -g <pkg>` without `@<version>`, `go install <path>@latest` (or no
  `@<version>`), and Action refs on a moving target (`uses: org/action@main` / `@master`). Each is a reliability
  candidate: the job is green today but reddens `main` on clean code the instant the upstream tool releases — the team's
  **highest-recurrence red-`main` class** (the ruff-version skew). **Fix = pin, don't bump:** add the version the repo
  already uses for that tool — grep the backend images (`images/backend-base/Dockerfile`, `backends/*/Dockerfile`) and
  sibling workflows for an existing `==` / `@` pin and match it, so CI stays in lockstep with what the agents run; only
  if no pin exists anywhere, pin to the currently-resolved version. Bumping a tool to a _newer_ version than the repo
  already uses is a deliberate dep-bump — not this fix; leave it. (Fix-bar fit: pin-to-current is
  function-body-contained and behavior-preserving — near-zero blast radius — and CI running the workflow on the next
  push is the test that covers it, so it auto-fixes at the reliability-High band.)

### Performance — pattern-matched

- **Unbounded queues / channels (Go):** `grep -rn 'make(chan ' <section>/*.go` — every unbuffered chan in a fan-in
  pattern, every buffered chan with a non-constant size depending on input.
- **Unbounded growth in shared state:** `dict[]` / `map[T]U` / `list.append` without an eviction or cap — read the
  surrounding code for `MAX_*` constants, sweepers, LRU.
- **Blocking calls in async paths (Python):** `grep -rn 'requests\.\|time\.sleep\|subprocess\.run' <section>/` inside
  `async def` or any function called from one.
- **N+1 query patterns:** `for ... in ... await session.execute` / `for ... in ... db.query` — bulk-query candidates.
- **Missing pagination:** large-result fetches without `limit` / `LIMIT` / `--page-size`.

### Observability — pattern-matched

- **Silent except blocks (Python):** `grep -rn 'except.*: *pass\|except.*: *return' <section>/` — every match without a
  structured log of the exception.
- **Swallowed error context (Go):** `grep -rn 'return nil$\|return err$' <section>/*.go` adjacent to error paths —
  `return err` without `fmt.Errorf("%s: %w", context, err)` loses diagnostic context.
- **Critical control-flow without metric:** branches that handle a degraded path (rate-limit hit, retry exhaustion,
  fallback chosen) without a `_total{reason="..."}` counter increment in the section's `metrics.py`.
- **Conditions impossible to diagnose:** error messages that don't include the failing input / state — e.g.
  `return errors.New("bad request")` instead of `return fmt.Errorf("bad request: missing field %q", field)`.

### Maintainability — pattern-matched (flag-only)

- **Duplicated critical logic:** identical >30-line blocks across files (run `git grep -F` on a sentinel line).
- **Deep coupling:** modules that import from >8 internal packages, indicating the module owns work that should live
  closer to the data.
- **Undocumented invariants:** functions with non-obvious preconditions or postconditions (e.g., "callers must hold
  `mu`", "must not be called concurrently with `Stop()`") not stated in a comment.

These always flag with `[flagged: maintainability-structural]`. **Don't auto-fix maintainability findings.**

## Memory format

Reuses bug-work's exact format with a category tag prepended to each finding. The deferred-findings file is shared:
`/workspaces/witwave-self/memory/agents/evan/project_evan_findings.md`. Run sections distinguish by skill:

```markdown
## YYYY-MM-DD HH:MM UTC — risk-work run (depth=N, sections=..., categories=...)

**Status: in-progress.** Pre-sweep SHA: `<sha>`.

### <section name>

- **<file>:<line>** `<analyzer rule or pattern name>` <CATEGORY> <severity> — <one-line description> [pending]
- ...
```

`<CATEGORY>` is one of `[SEC]` / `[REL]` / `[MAINT]` / `[PERF]` / `[OBS]`. `<severity>` is one of `[CRITICAL]` /
`[HIGH]` / `[MEDIUM]` / `[LOW]`. Marker reasons in `[flagged: <reason>]` extend bug-work's vocabulary with risk-specific
entries — security, then operational categories:

**Security-specific:**

- `severity-below-depth-threshold` — Low or Medium (at depth <5) findings auto-flag.
- `dep-bump-breaks-caller` — fix-bar #2 caught a caller-incompat in the bump.
- `dep-bump-major-version` — non-semver-compatible bump, needs human approval.
- `unreached-from-public-input` — at depth ≥5, the gauntlet dropped this for reachability.
- `false-positive-secret` — gitleaks pattern matched a template/fixture/docs example.
- `no-patched-version-yet` — CVE has no patched release; watch upstream.

**Operational-categories-specific:**

- `maintainability-structural` — every maintainability finding flags with this; structural refactors are out of scope
  for autonomous fix.
- `mitigated-upstream` — gauntlet #2 dropped this; an outer caller already handles the concern.
- `mitigated-elsewhere` — gauntlet #3 dropped this; a cap/sweeper/eviction policy already enforces the bound.
- `metric-already-present` — gauntlet #4 dropped this; a counter/log already covers the silent-failure path.
- `documented-design-tradeoff` — gauntlet #5 dropped this; AGENTS.md / a docstring explains the choice.

Plus all bug-work reasons (`function-body-not-contained`, `blast-radius-unclear`, `no-test-coverage`,
`ambiguous-analyzer-rule`, `fix-broke-local-tests`, `fix-needs-unfamiliar-api-confirmation`, `gauntlet-dropped`,
`fix-forward-failed`).

## Process (8 steps)

Read these from CLAUDE.md before starting:

- **`<checkout>`** — local working-tree path
- **`<branch>`** — default branch (`main`)

### 0. Verify the source tree

Same as bug-work Step 0. Pin git identity via `git-identity` skill.

### 0.5. Recover stuck commits

Same as bug-work Step 0.5. Delegate to iris if `git rev-list --count origin/main..HEAD` is non-zero. Capture
`PRE_SWEEP_SHA` after recovery.

### 1. Scan

For each section in the resolved input AND each category in the resolved input, run the corresponding detection method
from the "Candidate detection per category" section above:

- **Security:** analyzer-driven. Run the toolchain table (govulncheck / gosec / pip-audit / bandit / gitleaks / trivy)
  appropriate to the section's languages.

  - **Python sections** (`harness`, `shared`, `backends/*`, `tools/*`): `pip-audit` if `requirements.txt` exists;
    `bandit` over the section; `gitleaks` over the section.
  - **Go sections** (`operator`, `clients/ww`): `govulncheck` over the module; `gosec` over the module; `gitleaks` over
    the section.
  - **Dockerfile-bearing sections:** `trivy fs` over the section.
  - **Cross-cutting** (when `sections=all-day-one` or `all-deps`): one `gitleaks detect` over the whole checkout to
    catch repo-wide secret commits.

- **Reliability:** pattern-matched. Run each grep from the reliability-detection list above; collect each hit as a
  candidate. On any `all-day-one` / `all-deps` sweep, also run the repo-global CI-pinning sweep over
  `.github/workflows/*.yml` once (it is not tied to a code section — run it alongside the cross-cutting `gitleaks`
  pass).
- **Performance:** pattern-matched. Run each grep from the performance-detection list above.
- **Observability:** pattern-matched. Run each grep from the observability-detection list above.
- **Maintainability:** pattern-matched. Run each grep from the maintainability-detection list above. (These always flag
  — no auto-fix — but still get persisted so zora's backlog counter sees them.)

Concatenate hits into the **candidate list**. Each candidate carries: section, file, line, rule-or-pattern, message,
**category**, severity, raw analyzer output (for security) or grep context (for the others). Both category and severity
are mandatory — together they gate the fix-bar.

When the caller passed `categories=<subset>`, skip the categories not in the subset entirely. Default (no `categories`
arg) walks all five.

### 1.5. Persist the candidate list to memory IMMEDIATELY

Same as bug-work Step 1.5. `mkdir -p` the memory dir; write the run-section template (with severity tags); each
candidate gets a `[pending]` marker. Mandatory regardless of candidate count.

### 2. Validate per candidate (depth-gated)

Walk the 8-concern risk gauntlet at the depth's intensity (see depth-scale table). Drop candidates that any concern
catches.

### 3. Reason about candidates as a set

Look at the surviving set:

- **Common dep upgrade.** Multiple CVEs that all resolve via the same dep bump → one commit, not many.
- **Conflicts.** Two patches to the same lockfile in incompatible ways → pick one.
- **Cascading severity.** A High-severity dep CVE that's only reachable through code that ALSO has a Medium bandit
  finding — fix the Medium first, the High becomes unreachable, drop it.
- **Order by safety.** Smallest blast radius first; lockfile-only bumps before code changes.

### 4. Decide fix vs. flag (per candidate)

Apply the 4-rule risk fix-bar above. Bin to **fix** or **flag**.

### 5. Fix each fixable candidate

For each candidate in the fix bin, processed in step-3 order:

1. **Read the code in full** (function body + immediate callers + callees), or **read the dep change** (the patched
   version's CHANGELOG / release notes for breaking-change confirmation).
2. **Web-search the patched API if unfamiliar.** If the dep bump or the security pattern involves an API or framework
   behaviour you can't characterise, web-search before applying. Drop to flag with
   `fix-needs-unfamiliar-api-confirmation` if the search reveals more complexity than expected.
3. **Apply the fix.** Either:
   - **Dep bump:** edit the lockfile (`requirements.txt`, `go.mod`). For Python, also run
     `pip install -r requirements.txt --upgrade` to lock; for Go, run `go mod tidy`.
   - **Code change:** apply the minimal patch (escape input, add validation, replace `shell=True` with arg-list form,
     etc.).
4. **Run scoped tests locally:**

   - Go: `cd <checkout>/<section> && go test ./...`
   - Python: `cd <checkout> && pytest <section>/`

   **If tests pass** → continue.

   **If tests fail** → fix-forward, ONCE. Same shape as bug-work Step 5.4. Adjust the fix in-place, re-run tests. Pass
   on retry → commit. Still fails → revert + flag with `fix-forward-failed`.

5. **Re-run the analyzer that originally flagged.** The patched code or bumped dep should no longer fire the rule. If it
   still fires, the fix is incomplete — adjust before committing.
6. **Verify no adjacent regressions.** Re-read 20 lines around the change.
7. **Commit (one finding per commit):**

   ```sh
   git -C <checkout> add <files>
   git -C <checkout> commit -m "fix(<section>): <one-line risk description> [<CATEGORY>:<SEVERITY>]

   <2-4 lines: what the risk was, what condition would have caused it
   to manifest, what the fix does. Reference the analyzer rule or
   pattern name (e.g. \"bandit B602 flagged at <file>:<line>:
   subprocess with shell=True and unvalidated input\" for security;
   \"missing http.Client Timeout at <file>:<line> — blocks the
   conversation/list path under upstream slowness\" for reliability).
   For dep bumps, reference the CVE id and the patched version.>
   "
   ```

   `<CATEGORY>` in the subject line is one of `SEC` / `REL` / `PERF` / `OBS`. (Maintainability never reaches this step —
   it always flags.)

8. **Mutate the marker in memory IMMEDIATELY.** `[pending]` → `[fixed: <commit-SHA>]`. Per-candidate.

### 6. Finalise the run section

Same as bug-work Step 6. Walk leftover `[pending]` markers, mutate to `[flagged: <reason>]`, mutate run-section header
`**Status: in-progress.**` → `**Status: complete.**`, append summary line:
`"M total candidates: F fixed, G flagged, D dropped at gauntlet. Per-category: <SEC=...,REL=...,PERF=...,OBS=...,MAINT=...>."`

Order flagged candidates within each section first by category (security → reliability → performance → observability →
maintainability), then by severity within category (Critical → High → Medium → Low). Security findings come first
because they have the lowest blast-radius-to-fix-confidence ratio; maintainability comes last because it's flag-only by
design.

### 7. Push + watch CI (via iris)

Identical to bug-work Step 7. Delegate push + CI watch to iris, including the failing-job log fetch on red. On any red
CI:

- **Fix-forward, ONCE.** Read iris's failure log excerpt. If in scope (small targeted change, clearly remediable from
  the log), write a fix-forward commit, run scoped local tests, ask iris to push + re-watch.
- **If fix-forward fails or out of scope** → batch-revert.

Same bound: exactly one fix-forward attempt per CI failure event.

### 8. Report

Return a structured summary:

- Pre / post SHAs
- Sections scanned, depth used, categories walked
- Per-section: candidates considered, dropped at gauntlet, fixed (with SHAs + category + severity), flagged
- Per-category totals (SEC / REL / PERF / OBS / MAINT across the run)
- Severity totals (Critical / High / Medium / Low across the run)
- Iris's push outcome
- CI watch outcome (per workflow)
- Pointer to `project_evan_findings.md` for flag-only details
- If batch-reverted: failing workflow URL + revert commit SHA
- If fix-forwarded: fix-forward commit SHA + post-fix-forward CI conclusion

## Out of scope for this skill

Same as bug-work, plus:

- **Compliance audits.** SOC 2 / HIPAA / PCI checks are policy work, not bug-class fixing.
- **Penetration testing.** Active probing requires explicit authorization; out of autonomous scope.
- **Cryptographic primitive selection.** AES-GCM vs ChaCha20-Poly1305 is an architecture decision, not a fix evan should
  make autonomously. Flag for human review.
- **Major-version dep bumps.** Always flag. Breaking-change risk requires human review even if a patched version exists.
- **Architectural refactors.** Maintainability findings always flag — splitting a god-module, deduplicating identical
  critical logic, or surfacing an undocumented invariant requires structural decisions that exceed per-call-site scope.
  A future architecture-agent will own that lane.
- **Semgrep.** Deferred — not yet in the agent image. Add in a future toolchain expansion if the bandit + gosec +
  govulncheck combo turns out to leave gaps.
