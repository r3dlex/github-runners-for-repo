# Deep Interview Spec: Python Modernization + GitHub Governance + Shift-Left Guardrails

## Metadata

- **Interview ID:** di-2026-06-05-python-modernize-shiftleft
- **Rounds:** 11 (Round 0 topology + 10 scoring rounds, including one contrarian challenge)
- **Final Ambiguity Score:** 5.95% (PASSED — well below 20% threshold)
- **Type:** brownfield
- **Generated:** 2026-06-05
- **Threshold:** 0.2
- **Threshold Source:** `default` (no `omc.deepInterview.ambiguityThreshold` in user `~/.claude/settings.json` and no project `./.claude/settings.json`)
- **Initial Context Summarized:** yes (interview prompt contained the full task brief; the brief was within prompt budget and was used directly, with brownfield facts folded into the summary)
- **Status:** PASSED

## Clarity Breakdown

Brownfield weights: Goal 0.35, Constraints 0.25, Criteria 0.25, Context 0.15. Per-dimension minima (across the 5 active components) drive the global score.

| Dimension | Min across components | Weight | Weighted contribution |
|---|---|---|---|
| Goal Clarity | 0.96 | 0.35 | 0.336 |
| Constraint Clarity | 0.95 | 0.25 | 0.2375 |
| Success Criteria | 0.94 | 0.25 | 0.235 |
| Context Clarity | 0.88 | 0.15 | 0.132 |
| **Total Clarity** | | | **0.9405** |
| **Ambiguity** | | | **0.0595 (5.95%)** |

## Topology

5 active components confirmed at Round 0. No deferred components.

| Component | Status | Description | Coverage / Deferral Note |
|---|---|---|---|
| `branch-protection` | active | GitHub branch-protection on `main` | Strict: 8 required checks, 1 required review, CODEOWNERS, no admin bypass, linear history, no force-push, no branch deletion |
| `pr-issue-linkage` | active | Hard rule tying every PR to an open Issue | "Closes #N" / "Fixes #N" regex in PR body → enforced by a dedicated GH Actions job (`pr-issue-link`) + PR template |
| `agents-md-update` | active | Update `AGENTS.md` to codify the new workflow | Keep current header + dev-workflow section; add a "Governance" section; replace Poetry with uv; no Poetry references in new content |
| `python-toolchain-modernization` | active | Modernize the whole Python toolchain | Poetry removed; uv is the only toolchain; ty (replaces mypy), deptry, pip-audit; 95% coverage; `tools/pipeline_runner` REMOVED |
| `shift-left-guardrails` | active | Add pre-commit + archgate + ADRs | `.pre-commit-config.yaml` runs ALL 8 checks locally; `archgate` installed via `uv tool install archgate`; 4 starter ADRs under `.archgate/adrs/` with companion `.rules.ts` files |

## Goal

Make `github-runners-for-repo` a fully-governed, shift-left-validated Python project where:

1. **Direct pushes to `main` are impossible** — GitHub rejects them by configuration. The only path to land code is a PR that passes all 8 required status checks and 1 approving review.
2. **Every PR is traceable to an Issue** — the PR body must contain `Closes #N` or `Fixes #N` to an open issue, or the merge is blocked.
3. **All checks run locally before the PR is opened** — pre-commit hooks call the same `uv run` / `uvx` commands as GH Actions, so "green on my machine" means "green in CI."
4. **Architectural decisions are executable, not just documented** — archgate enforces ADRs as `.rules.ts` files in CI and pre-commit, so a violation of any ADR fails the build.
5. **The toolchain is modern and uniform** — Poetry is gone, `pipeline-runner` is gone; everything goes through `uv` + `uvx` + `archgate` + `pre-commit` + `ruff` + `ty` + `deptry` + `pip-audit` + `pytest`.

## Constraints

### C1 — GitHub branch protection (Strict)

Apply to the `main` branch (default branch):

- **Required approving reviews:** 1
- **Dismiss stale pull request approvals when new commits are pushed:** true
- **Required status checks (8, all must pass):**
  1. `lint` — `uv run ruff check .` and `uv run ruff format --check .`
  2. `test` — `uv run pytest` across Python 3.11, 3.12, 3.13
  3. `coverage` — `uv run pytest --cov=github_runners_for_repo` with 95% threshold enforced
  4. `build` — build the wheel with `uv build` and verify entry point
  5. `archgate` — `archgate check` (loads `.archgate/adrs/*.rules.ts`)
  6. `pr-issue-link` — fail if PR body lacks `Closes #N` / `Fixes #N` to an open issue
  7. `ty` — `uv run ty check github_runners_for_repo`
  8. `deptry-pip-audit` — `uv run deptry .` and `uv run pip-audit`
- **Require linear history:** true
- **Require signed commits:** false (not selected)
- **Include administrators:** true (no admin bypass)
- **Allow force pushes:** false
- **Allow branch deletion:** false
- **Allow direct pushes:** false (require a pull request before merging)
- **CODEOWNERS file present at** `.github/CODEOWNERS` (user is the owner of every path; effectively a self-review gate)

### C2 — PR ↔ Issue linkage (Hard)

- PR body must contain one or more of: `Closes #N`, `Fixes #N`, `Resolves #N` (where `N` is an open-issue number) — additional closing keywords may be added later via ADR.
- Implemented as a GH Actions job named `pr-issue-link` — a small in-repo script `tools/check_pr_link.py` (or a third-party action if available with the same behavior).
- Backed by `.github/PULL_REQUEST_TEMPLATE.md` that requires a "Linked issue" line.
- The job runs on every PR and on every push to `main` (defensive: catches backfill of rules onto a closed PR).
- No draft-PR exemption in the rule (drafts are also gated). The GH `draft` flag only delays the merge button; it does not relax checks.
- An issue is "open" if its state is `open` at PR-open time.

### C3 — Python toolchain modernization (uv-first)

- `pyproject.toml` is reshaped to PEP 621 (no `[tool.poetry.*]` sections remain).
- `uv.lock` is the single source of truth for dependency resolution. `poetry.lock` is deleted.
- All developer commands go through `uv run` (or `uvx` for one-off tools). No `poetry` references in any script, workflow, docs, Dockerfile, or comment.
- `tools/pipeline_runner/` is **deleted**. Its previous responsibilities (lint, test, coverage, build orchestration) move to:
  - GH Actions YAML (the 4 existing workflows are rewritten to call `uv run` / `uvx` directly).
  - Pre-commit hooks (the 8 checks, all called the same way).
- Dependency versions are bumped to current majors:
  - `requests` ^2.32, `python-dotenv` ^1.1, `docker` ^7.1
  - `pytest` ^8.4, `pytest-cov` ^6.0
  - `ruff` ^0.12
  - `ty` (latest), `deptry` (latest), `pip-audit` (latest), `archgate` (latest; pinned to v0.43.x at minimum)
  - Python `>=3.11` (drop the `^3.11` constraint; the GH Actions matrix keeps 3.11/3.12/3.13)
- `pyproject.toml` adds the 4 tool sections:
  - `[tool.uv]` (managed = true, package = true)
  - `[tool.deptry]` (extend config for unused/undeclared deps)
  - `[tool.ty]` (project settings; strict-equivalent for this codebase)
  - `[tool.archgate]` is **not** a thing — archgate reads `.archgate/` from the repo root, not `pyproject.toml`.
- Coverage floor: **95%**. Configured in `[tool.coverage.run]` / `[tool.coverage.report]` and enforced by the `coverage` GH Actions job (fail the build if below 95%).

### C4 — Shift-left guardrails (pre-commit + archgate + ADRs)

- `.pre-commit-config.yaml` lives at the repo root, uses `pre-commit` v3.x or v4.x.
- All 8 required checks are mirrored as pre-commit hooks. Hook entries use `language: system` (not `language: python`) because the repo is uv-managed and pre-commit's Python venv would conflict with the uv-managed one. Each entry calls `uv run <tool> ...` or `uvx --from <tool> <tool> ...`.
- `archgate` is installed via `uv tool install archgate` (system-wide, not into the pre-commit venv). Pre-commit invokes `archgate check` via `archgate` on `PATH`.
- The `.archgate/adrs/` directory holds 4 starter ADRs, each with a companion `.rules.ts`:
  - **ARCH-001** — *PR-only changes with Issue linkage*: every PR has a linked open issue. Enforced by the `pr-issue-link` GH Actions job + an archgate rule that asserts the PR template exists.
  - **ARCH-002** — *Main branch is strictly protected*: 1 required review, CODEOWNERS, no admin bypass, linear history, no force-push. Enforced by GH branch-protection settings + an archgate rule that asserts `.github/CODEOWNERS` exists.
  - **ARCH-003** — *uv is the only Python toolchain*: no `poetry.lock`, no `[tool.poetry.*]` sections, no `pip` invocations in CI. Enforced by `deptry` + a `ty`-adjacent grep rule in archgate.
  - **ARCH-004** — *95% coverage floor*: the `coverage` GH Action fails below 95% (line + branch).
- New ADRs follow the structure in archgate's example repo: `.archgate/adrs/ARCH-NNN-<slug>.md` with YAML frontmatter + a sibling `.archgate/adrs/ARCH-NNN-<slug>.rules.ts`.

### C5 — AGENTS.md update

- Keep the existing "Sustainable YOLO + Progressive Disclosure" header verbatim.
- Keep the existing "Development Workflow" section, but replace every `poetry run` / `poetry install` with `uv run` / `uv sync`, and remove the `pip install ./tools/pipeline_runner` line (no longer applicable).
- Add a new "Governance" section near the end with a flat rule list, one rule per bullet, each bullet under ~2 lines:
  - All changes land via PR — no direct push to `main`.
  - Every PR must reference an open issue with `Closes #N` / `Fixes #N`.
  - 1 approving review is required; admins are not exempt.
  - All 8 CI checks must pass; no merges with red.
  - Run `uvx pre-commit run --all-files` before pushing.
  - uv is the only Python toolchain; do not introduce Poetry or `pipeline-runner`.
  - Architectural decisions live in `.archgate/adrs/` and are enforced by archgate.
- Add a final "Adding a new check or rule" subsection that points to the ADR process.

## Non-Goals

- No signed commits (out of scope this round; can be added via ADR later).
- No CODEOWNERS outside the user's own ownership (no team-splitting / org-level rules).
- No release publishing automation (PyPI / GH Releases) — out of scope.
- No dependabot/renovate — out of scope.
- No PR auto-merge bot — out of scope.
- No Slack/Discord notification integration for failed checks.
- No type stub generation for third-party libraries (only `github_runners_for_repo/` is type-checked).
- No monorepo split, no package restructure (the 4-file `github_runners_for_repo/` package layout stays).
- No migration of the Docker runner image (`runner/Dockerfile`) — it is a separate runtime artifact, not part of the dev toolchain.

## Acceptance Criteria

A reviewer should be able to clone the repo fresh, run `uv sync` + `uvx pre-commit install`, open a PR that *fails* the `pr-issue-link` check, see the check fail locally and in CI, add a `Closes #N` line, push again, see all 8 checks pass, and merge. The acceptance criteria are testable as follows:

- [ ] **AC-1 (branch protection)**: A direct push to `main` is rejected by GitHub (verified by an attempted `git push origin main` from a maintainer account, which returns a 403 with a branch-protection error). Verified via `gh api repos/:owner/:repo/branches/main/protection` returning the expected JSON.
- [ ] **AC-2 (branch protection checks)**: `gh api repos/:owner/:repo/branches/main/protection/required_status_checks` returns exactly 8 contexts with the names listed in C1.
- [ ] **AC-3 (branch protection reviewers)**: `gh api ... /protection` shows `required_pull_request_reviews.required_approving_review_count == 1`, `dismiss_stale_reviews == true`, `require_code_owner_reviews == true`, `enforce_admins.enabled == true`, `required_linear_history.enabled == true`, `allow_force_pushes.enabled == false`, `allow_deletions.enabled == false`, `restrictions` is null.
- [ ] **AC-4 (CODEOWNERS)**: `.github/CODEOWNERS` exists with at least one rule covering `*` pointing to the repo owner; the rule is reachable from every changed path in a sample PR.
- [ ] **AC-5 (pr-issue-link)**: A PR with body `no closing keyword` fails the `pr-issue-link` check (locally and in CI). A PR with body `Closes #42` where #42 is open passes. A PR with body `Closes #99` where #99 is closed fails. (Three small test PRs.)
- [ ] **AC-6 (PR template)**: Opening a new PR shows the "Linked issue" field in the template.
- [ ] **AC-7 (uv migration)**: `grep -r "poetry" --include="*.py" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.toml" --include="*.sh" --include="Dockerfile*" .` returns zero matches in tracked files. `uv.lock` exists. `poetry.lock` does not. `[tool.poetry.*]` is not present in any `pyproject.toml`. `tools/pipeline_runner/` is deleted.
- [ ] **AC-8 (uv dev loop)**: Fresh clone + `uv sync` + `uv run pytest` passes with 0 failures. Coverage report shows ≥95% on `github_runners_for_repo/`.
- [ ] **AC-9 (ruff)**: `uv run ruff check .` and `uv run ruff format --check .` both pass. Ruff config in `pyproject.toml` is at line-length 100 with `target-version = "py311"`.
- [ ] **AC-10 (ty)**: `uv run ty check github_runners_for_repo` passes with 0 errors. No `# type: ignore` lines in the codebase (or, if any, they have a justification comment).
- [ ] **AC-11 (deptry)**: `uv run deptry .` passes — no unused dependencies, no undeclared dependencies.
- [ ] **AC-12 (pip-audit)**: `uv run pip-audit` passes — no known vulnerabilities in the dependency tree at the time of the audit.
- [ ] **AC-13 (archgate)**: `archgate check` passes against `.archgate/adrs/`. Each of the 4 starter ADRs has a companion `.rules.ts` that is parseable and runs.
- [ ] **AC-14 (pre-commit)**: `uvx pre-commit run --all-files` runs all 8 checks and they all pass on a clean tree. Editing a tracked Python file to introduce an unused import causes the corresponding pre-commit hook to fail locally.
- [ ] **AC-15 (archgate ADR inventory)**: `.archgate/adrs/ARCH-001-pr-only-and-issue-link.{md,rules.ts}`, `ARCH-002-main-branch-protection.{md,rules.ts}`, `ARCH-003-uv-only-toolchain.{md,rules.ts}`, `ARCH-004-coverage-floor-95.{md,rules.ts}` exist and are referenced by `archgate check`.
- [ ] **AC-16 (AGENTS.md)**: `AGENTS.md` opens with the existing "Sustainable YOLO + Progressive Disclosure" header verbatim; the "Development Workflow" section uses `uv` commands only; a "Governance" section exists with all 7 bullets from C5; `grep -n "poetry" AGENTS.md` returns 0 matches.
- [ ] **AC-17 (CI parity)**: All 8 GH Actions jobs are present in `.github/workflows/` and the job names match the required status check names in C1 exactly. The `pr-issue-link` and `archgate` jobs are present as separate workflows.
- [ ] **AC-18 (CI green)**: Pushing a no-op PR (e.g., adding a blank line to a doc) results in all 8 checks green and the "Merge" button enabled. The merge button is disabled if any check is red or if the PR is missing an approving review.

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|---|---|---|
| `archgate` is a real, installable tool | "archgate not found" on PATH; no PyPI evidence initially | Confirmed real at `github.com/archgate/cli` v0.43.0 (released 2026-06-01). Install via `uv tool install archgate`; Python install route is `pip install archgate`. |
| Adding `mypy --strict` and `ty` together is "state-of-the-art" | Two type checkers is redundant: ty is faster, more modern, and ships from the same ecosystem as Ruff | Drop mypy; keep ty. Round 4.5 contrarian mode caught this. |
| `tools/pipeline_runner` should remain the source of truth | With GH Actions as source of truth, the runner is a thin mirror — and a thin mirror that re-invokes the same `uv run` commands adds maintenance cost for no value | Drop `tools/pipeline_runner` entirely; pre-commit + GH Actions invoke `uv run`/`uvx` directly. Round 10. |
| "All checks in pre-commit" includes slow ones (pytest with coverage, pip-audit) | Local loop is slowed; AGENTS.md guidance needed to set expectations | Confirmed: ALL 8 run in pre-commit AND GH Actions. The trade-off is accepted (strictness over speed). |
| "Code coverage 95%" is achievable on a 4-file CLI | Branch coverage on config-loading + error paths is typically the gap | Accepted by user after the contrarian check; the implementation will need real branch tests, not smoke tests. |
| Existing 4 GH Actions workflows (lint/test/coverage/build) can be reused as the 4 "old" required checks | Job names must match the required status check names exactly; existing names already align (lint, test, coverage, build) | Confirmed alignment; workflows are rewritten to use uv but keep the same job names. |
| `archgate` ADRs will be authored as part of this work | The repo has no ADRs today | 4 starter ADRs are part of the deliverable (ARCH-001..004). |
| `tools/pipeline_runner`'s deletion is a breaking change for anyone who calls `pipeline-runner ...` locally | AGENTS.md references it; README references it | AGENTS.md and README are updated in this same change. |
| uv + archgate v0.43.0 are compatible | v0.43.0 is recent (2026-06-01) — possible edge cases with newer uv | Implementation step includes pinning archgate ≥0.43.0 and uv ≥0.5; verified by an `archgate check` smoke test in the implementation PR. |
| `gh api` can read branch-protection without admin scope (for AC-1..AC-3 verification) | Some fields require admin scope | Verification is run from an admin-context (a separate verify job or local maintainer shell). |

## Technical Context

### Brownfield artifacts (from `explore` / `Read` during this interview)

- **Project root:** `/Volumes/Cowboy_Alpha/Ws/github-runners-for-repo/`
- **Source files:** `github_runners_for_repo/{__init__.py, cli.py, config.py, github_api.py, runner_manager.py}` (~8.4 KB total source).
- **Test files:** `tests/{test_cli.py, test_config.py, test_github_api.py}` (~8.9 KB total).
- **Pipeline runner (to be deleted):** `tools/pipeline_runner/` with its own `pyproject.toml` and 5 submodules.
- **Existing CI:** `.github/workflows/{lint.yml, test.yml, coverage.yml, build.yml}` — all use `pipx install poetry`, `poetry install`, and `pip install ./tools/pipeline_runner`.
- **AGENTS.md:** ~130 lines, narrative ("Sustainable YOLO + Progressive Disclosure" + dev workflow). No PR/Issue policy.
- **No pre-commit config, no CODEOWNERS, no PR template, no issue template.**
- **archgate not on PATH** (confirmed real at `https://github.com/archgate/cli` v0.43.0, 2026-06-01).

### Repository conventions to preserve

- License: MIT.
- Python: `>=3.11` (drop the `^3.11` Poetry constraint; allow 3.12 and 3.13 in CI).
- Test framework: pytest with pytest-cov.
- Linter/formatter: Ruff.
- CLI entry point: `gh-runners` (defined in `[project.scripts]` in the new `pyproject.toml`).

### External dependencies to add

- `archgate` (CLI only, installed via `uv tool install`)
- `ty` (Astral type checker)
- `deptry`
- `pip-audit`
- `pre-commit` (used to install the hook framework; not a runtime dep)

### Implementation order (proposed)

1. **Scaffolding PR** — add `.archgate/`, `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md`, `.pre-commit-config.yaml`, `.github/ISSUE_TEMPLATE/`, the 4 ADRs + rules. Verify archgate check passes.
2. **uv migration PR** — reshape `pyproject.toml`, add `uv.lock`, delete `poetry.lock`, delete `tools/pipeline_runner/`, rewrite the 4 GH Actions workflows to use `uv` directly, bump dep versions. Verify all 8 GH Actions pass.
3. **Branch protection PR** — apply branch-protection rules on `main` via `gh api`. (This is the *only* PR that doesn't go through the new policy, because the policy is what it installs; it merges via admin override, or by a separate setup step before the rules go live. ADR-002 documents this exception.)
4. **AGENTS.md update PR** — replace Poetry commands with uv, add the Governance section. Verify it passes the `archgate` check (which scans for stray `poetry` strings in markdown).

(Steps 2 and 3 must be ordered so that branch protection is applied *after* the workflows that the protection requires are already green on a non-protected branch — otherwise a chicken-and-egg.)

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|---|---|---|---|
| PR (Pull Request) | workflow artifact | linked issue #N, approving review, 8 required status checks | references Issue; merges into main; must pass all 8 required checks |
| Issue | workflow artifact | number, state (open/closed) | closed by PR via `Closes #N` / `Fixes #N` |
| main branch | protected ref | branch-protection rules, 8 required checks, required review count (1), CODEOWNERS enforced, include_admins: true | target of merged PRs; no direct push |
| Required check (CI job) | guard | job name, invocation command | runs on every PR; runs on every push to main; blocks merge if red |
| Check set | enumeration | lint, test, coverage, build, archgate, pr-issue-link, ty, deptry + pip-audit | all 8 run in GH Actions; all 8 run in pre-commit |
| ADR (Architecture Decision Record) | docs-as-code artifact | id (ARCH-NNN), title, decision, consequences, companion `.rules.ts` (enforced by archgate) | authored in repo at `.archgate/adrs/`; enforced by archgate check |
| uv project | package management | pyproject.toml (PEP 621), uv.lock, scripts via `[project.scripts]` | replaces Poetry; provides all dev tools via `uv run` / `uvx` |
| Pre-commit hook | guard | id, language (system), entry (`uv run ...` or `uvx ...`) | runs before commit; runs before push (optional); mirrors GH Actions checks |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|---|---|---|---|---|---|
| 1 | (not extracted) | — | — | — | — |
| 10 | 8 | 8 | — | — | N/A (first extraction) |

(Note: ontology extraction was deferred to the final-round snapshot in this interview because earlier rounds focused on policy choices rather than entity identification. The 8 entities above are stable across the locked policy decisions; a follow-up interview that drills into the implementation would extend this with sub-entities such as `CODEOWNERS rule`, `archgate rule`, `coverage report`.)

## Interview Transcript

<details>
<summary>Full Q&amp;A (11 rounds, including Round 0 topology + one contrarian challenge)</summary>

### Round 0 — Topology confirmation
- **Q:** Is this 5-component topology right (branch protection, PR↔Issue linkage, AGENTS.md update, Python toolchain modernization to uv, shift-left guardrails)?
- **A:** Looks right — proceed with 5.

### Round 1 — Branch protection / Goal
- **Q:** Strict / Standard / Light?
- **A:** Strict (state-of-the-art).

### Round 2 — PR ↔ Issue linkage / Goal
- **Q:** Hard / Medium / Soft / Branch-per-issue?
- **A:** Hard: any open issue is fine.

### Round 3 — Python toolchain / Goal
- **Q:** Full modern stack refresh / Minimal uv + dep refresh / Just uv?
- **A:** Full modern stack refresh.

### Round 4 — Python toolchain / Specific tools + Coverage floor
- **Q:** Which type checker trio + coverage floor? *(multi-select)*
- **A:** mypy (strict), ty (Astral), deptry + pip-audit. Coverage: Raise to 95%.

### Round 4.5 — Contrarian challenge on the Round 4 answer
- **Q:** mypy + ty is redundant; 95% may block for months. Revise?
- **A:** Drop mypy, keep ty.

### Round 5 — Python toolchain / Constraints
- **Q:** CI source of truth — pipeline-runner first, GH Actions first, or drop pipeline-runner?
- **A:** GH Actions is source of truth.

### Round 6 — Shift-left / Goal
- **Q:** archgate role — real tool, generic name, install via uvx, drop?
- **A:** Yes, install archgate, run via uvx.

### Round 6.5 — archgate install clarification
- **Q:** archgate is not in my training. Confirm it's real and give the install path.
- **A:** IT is this tool: https://github.com/archgate/cli.

### Round 7 — Branch protection / Criteria *(two questions in one round, justified by the strictness decision depending on both)*
- **Q:** Which required-checks set + which review/admin combo?
- **A:** All 8 checks required. 1 review, no admin bypass, CODEOWNERS.

### Round 8 — Shift-left / Constraints
- **Q:** Pre-commit scope — all 8 locally, fast subset, or formatting only?
- **A:** All checks in pre-commit AND GH (no skip).

### Round 9 — AGENTS.md / Goal
- **Q:** Lean rulebook preserving header / Replace with rulebook / Full doc with linked ADRs?
- **A:** Lean rulebook, preserve header.

### Round 10 — ADR inventory + pipeline-runner layout
- **Q:** Which ADRs (multi-select) + where does pipeline-runner live?
- **A:** 4 ADRs (ARCH-001..004). Drop pipeline-runner entirely.

**User follow-ups during the interview (incorporated into the spec):**
- "Use uv instead of poetry too" (Round 3 followup) — already aligned with "Full modern stack refresh" + "Poetry removed" but called out explicitly.
- "For it there should be also ADRs being kept as part of the repo" (Round 6.5 followup) — mapped to the 4 starter ADRs in `.archgate/adrs/`.

</details>

## Next Step

**Status: pending approval.** The spec is written. Per the deep-interview skill's Phase 5, execution requires an explicit user choice. The recommended bridge is the 3-stage pipeline:

`deep-interview spec → explicit approval to refine → /omc-plan --consensus --direct → pending approval → separate execution approval`

I will now present the execution bridge options.
