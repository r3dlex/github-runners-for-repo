# Implementation Plan: Python Modernization + GitHub Governance + Shift-Left Guardrails

**Spec:** `di-2026-06-05-python-modernize-shiftleft` (ambiguity 5.95%, PASSED)
**Project:** `/Volumes/Cowboy_Alpha/Ws/github-runners-for-repo/`
**Type:** Brownfield (Poetry + Ruff + pytest, 4-file `github_runners_for_repo/` package)
**Plan version:** 2 (post-Architect REVISE; 5 changes folded in)
**Status:** `pending approval`

---

## Changelog

| Version | Date | Author | Notes |
|---|---|---|---|
| 1 | 2026-06-05 | Planner | Initial 4-phase stacked-PR plan; 18 ACs; 8 risks; pre-mortem. |
| 2 | 2026-06-05 | Planner (revised per Architect) | (1) ARCH-005-stacked-pr-phasing ADR added; (2) ARCH-002 amended with single-maintainer caveat; (3) AC-14 includes `uv tool install archgate` and documents the archgate TypeScript runtime story; (4) AC-21 added for `pr-issue-link` "open at PR-open time" rule + false-positive/negative behavior; (5) C6 + AC-19 added: track `.omc/{plans,specs,wiki,drafts,research}/`, do NOT track `.omc/state/`, sensitivity check for committed secrets. |
| 3 | 2026-06-05 | Planner (revised per Critic) | (C-1) AC-14: pin documentation level (AGENTS.md `## TypeScript runtime for archgate` section; workflow comment block); require commit message to record the path taken as a `git log --grep` marker. (C-2) AC-18: rewrite "no-op PR" to a docs-only change referencing a maintenance issue via `Closes #N`. (C-3) AC-19: name trufflehog as a rejected alternative to gitleaks; record the trade-off. (C-4) R5: speed budget (pre-commit ≤30s warm, pre-push ≤120s). (C-5) R10: add CI-side drift check in the `coverage` workflow. (C-6) R11: add `.gitleaks.toml` baseline with allowlist. (C-7) P5: narrow to "preserve the 4-file package's public API + existing GH Actions job names". (C-8) §5 verification steps: 7 fixes (auth/remote notes, non-destructive AC-5 fixture, `uv run python` idiom, consistent gitleaks invocation, error-message expectations, AC-4 path-coverage test, AC-22 negative-test revert). |

---

## 1. Requirements Summary

- **Governance:** `main` is strictly protected — 1 approving review (trace, not separation of duties — see C1 caveat), CODEOWNERS enforced, no admin bypass, linear history, no force-push, no branch deletion, no direct push. 8 required status checks: `lint`, `test`, `coverage`, `build`, `archgate`, `pr-issue-link`, `ty`, `deptry-pip-audit`.
- **Traceability:** Every PR references an open issue with `Closes #N` / `Fixes #N` / `Resolves #N`. Enforced by a dedicated `pr-issue-link` GH Actions job + PR template. The job uses the *current* issue state at job run time (documented false-positive/negative behavior — see AC-21).
- **Modern toolchain:** Poetry deleted. `uv` is the only Python toolchain. `pyproject.toml` reshaped to PEP 621. `uv.lock` is the single lockfile. `tools/pipeline_runner/` deleted.
- **Static guarantees:** `ruff`, `ty`, `deptry`, `pip-audit`, `archgate check` all run in CI and locally via pre-commit. archgate v0.43.0+ requires a TypeScript runtime (bundled with archgate or installed via `actions/setup-node@v4` — see AC-14).
- **Coverage floor:** 95% line+branch on `github_runners_for_repo/`, enforced by the `coverage` GH Actions job *and* `[tool.coverage.report] fail_under = 95` in `pyproject.toml`. AC-22: a pre-commit assertion compares the two values to prevent drift.
- **Shift-left:** `.pre-commit-config.yaml` at repo root, `language: system`, mirrors all 8 CI checks via `uv run` / `uvx` / `archgate`. Heavy hooks (`pytest --cov`, `pip-audit`) staged on `pre-push`, not `pre-commit`, to keep the inner loop fast.
- **ADR-as-code:** 5 ADRs under `.archgate/adrs/`:
  - **ARCH-001** PR-only + Issue-link
  - **ARCH-002** Main branch protection (with single-maintainer caveat)
  - **ARCH-003** uv is the only Python toolchain
  - **ARCH-004** 95% coverage floor
  - **ARCH-005** Stacked-PR phasing for governance migrations (asserts: branch protection is applied at the earliest PR, not the last)
- **OMC artifact tracking:** `.omc/{plans,specs,wiki,drafts,research}/` are tracked in git; `.omc/state/` is NOT. A sensitivity check on every commit ensures no secrets are introduced.
- **AGENTS.md:** Preserve "Sustainable YOLO + Progressive Disclosure" header verbatim. Replace Poetry with uv. Add "Governance" section with 7 bullets. Add "Adding a new check or rule" subsection.
- **Preservation:** Existing `github_runners_for_repo/{__init__, cli, config, github_api, runner_manager}.py` and `tests/` must continue to pass at ≥95% coverage. Existing GH Actions job names preserved.
- **Out of scope:** signed commits, dependabot, PyPI publishing, monorepo split, Docker runner image, type stubs for third-party libs, no-self-approval job (S2 in Architect's synthesis; we take S1).

---

## 2. Acceptance Criteria (AC-1..AC-22)

| ID | Testable form |
|---|---|
| **AC-1** | `git push origin main` from a maintainer account returns 403 with branch-protection error. `gh api repos/:owner/:repo/branches/main/protection` returns the expected JSON object. |
| **AC-2** | `gh api .../branches/main/protection/required_status_checks` returns exactly 8 contexts: `lint`, `test`, `coverage`, `build`, `archgate`, `pr-issue-link`, `ty`, `deptry-pip-audit`. |
| **AC-3** | `gh api .../protection` shows: `required_approving_review_count == 1`, `dismiss_stale_reviews == true`, `require_code_owner_reviews == true`, `enforce_admins.enabled == true`, `required_linear_history.enabled == true`, `allow_force_pushes.enabled == false`, `allow_deletions.enabled == false`, `restrictions == null`. |
| **AC-4** | `.github/CODEOWNERS` exists with a `*` rule pointing to the repo owner; the rule is reachable from every changed path in a sample PR. |
| **AC-5** | Three test PRs verify `pr-issue-link` behavior: (a) body `no closing keyword` → job fails; (b) body `Closes #42` with #42 open → passes; (c) body `Closes #99` with #99 closed → fails. |
| **AC-6** | Opening a new PR pre-populates the body with the "Linked issue" line from `.github/PULL_REQUEST_TEMPLATE.md`. |
| **AC-7** | `grep -r "poetry" --include="*.py" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.toml" --include="*.sh" --include="Dockerfile*" .` returns 0 matches in tracked files. `uv.lock` exists; `poetry.lock` does not. `pyproject.toml` has no `[tool.poetry.*]`. `tools/pipeline_runner/` does not exist. |
| **AC-8** | Fresh clone + `uv sync` + `uv run pytest` → 0 failures. Coverage report shows ≥95% on `github_runners_for_repo/`. |
| **AC-9** | `uv run ruff check .` and `uv run ruff format --check .` pass. `[tool.ruff]` has `line-length = 100` and `target-version = "py311"`. |
| **AC-10** | `uv run ty check github_runners_for_repo` passes with 0 errors. Zero unjustified `# type: ignore` lines. |
| **AC-11** | `uv run deptry .` passes. |
| **AC-12** | `uv run pip-audit` passes. |
| **AC-13** | `archgate check` passes against `.archgate/adrs/`. |
| **AC-14** | Fresh-clone bootstrap = `uv tool install archgate` + `uv sync` + `uvx pre-commit install` + `uvx pre-commit run --all-files` runs all 8 checks. The TypeScript runtime story is pinned: AGENTS.md contains a `## TypeScript runtime for archgate` section (one paragraph) explaining whether archgate v0.43.x ships a bundled TS runtime or requires `actions/setup-node@v4`; `.github/workflows/archgate.yml`'s `archgate` job has a comment block (`# archgate-runtime: <bundled|node>`) that mirrors the AGENTS.md decision. The commit message that first introduces the `archgate` workflow MUST include a `git log --grep="archgate-runtime"`-searchable marker: either `[archgate-runtime: bundled]` or `[archgate-runtime: node]`. Verified by `grep -F "## TypeScript runtime for archgate" AGENTS.md` and `gh api repos/:owner/:repo/contents/.github/workflows/archgate.yml --jq '.content' \| base64 -d \| grep -E "archgate-runtime: (bundled\|node)"` and `git log --grep="archgate-runtime" --oneline \| head -1` returning a non-empty result. |
| **AC-15** | `.archgate/adrs/ARCH-001..004.{md,rules.ts}` exist and are loaded by `archgate check`. |
| **AC-16** | `AGENTS.md` opens with the "Sustainable YOLO + Progressive Disclosure" header verbatim; "Development Workflow" uses `uv` commands only; a "Governance" section contains all 7 bullets from C5; `grep -n "poetry" AGENTS.md` returns 0 matches. |
| **AC-17** | All 8 GH Actions job names match the required status check names in C1 exactly. `pr-issue-link` and `archgate` are separate workflows. |
| **AC-18** | A docs-only PR (e.g., a typo fix or a one-line clarification in `docs/branch-protection.md`) that references a maintenance issue via `Closes #N` results in 8/8 green checks and an enabled "Merge" button. The PR must NOT be a literal empty commit (which would fail `pr-issue-link`); it must be a real diff whose `Closes #N` keyword names an open issue. Verified by opening such a PR, observing all 8 check contexts are `success` in the PR's checks tab, and the "Merge" button is enabled after 1 owner approval. |
| **AC-19** | **OMC artifact tracking (NEW).** `git ls-files .omc/` lists `.omc/plans/`, `.omc/specs/`, `.omc/wiki/`, `.omc/drafts/`, `.omc/research/` (with at least one tracked file each) and does NOT list `.omc/state/`. `.gitignore` contains `.omc/state/` and `.omc/sessions/`. A pre-commit hook + a CI job (`secrets-scan`) run `gitleaks detect --no-banner` on every push and fail the build if any committed file matches a secret pattern. **Tool choice — gitleaks over trufflehog:** trufflehog was considered and rejected because (i) gitleaks has a pre-built `gitleaks/gitleaks-action` GitHub Action that fits the existing CI pattern with no extra setup, (ii) gitleaks rule format is TOML (lighter than trufflehog's YAML detectors), and (iii) gitleaks has no SaaS dependency. The trade-off: gitleaks' default rule set is smaller than trufflehog's (trufflehog scans 800+ secret types via its verified-rules bundle), so this project accepts a narrower detection surface in exchange for the operational simplicity. **A `.gitleaks.toml` baseline file with an `allowlist` block ships with the `secrets-scan` workflow** to prevent first-push false positives on legitimate test fixtures (sample tokens in `tests/` for the pr-issue-link fixture). |
| **AC-20** | **Single-maintainer caveat (NEW).** `.archgate/adrs/ARCH-002-main-branch-protection.md` contains the text: *"Single-maintainer caveat: in a single-owner repo the 1-review rule is a trace step, not a separation-of-duties gate. Safety is provided by the 8 required status checks. If a second maintainer joins, `required_approving_review_count` must be raised to 2 in the same ADR amendment."* Verified by `grep -F "Single-maintainer caveat" .archgate/adrs/ARCH-002-main-branch-protection.md`. |
| **AC-21** | **`pr-issue-link` "open at PR-open time" rule documented (NEW).** `tools/check_pr_link.py` (or the GH Actions job wrapper) contains a docstring or comment block explaining: (i) the GitHub REST API returns *current* issue state, not state at PR-open time; (ii) a PR that is valid at open time may become invalid if its referenced issue is closed by another PR before merge; (iii) the chosen behavior is to use the *current* state at job run time, with a comment in the script describing the false-positive/negative trade-off. Verified by `grep -F "current issue state" tools/check_pr_link.py` (or the GH Actions job file). |
| **AC-22** | **Coverage threshold drift guard (NEW).** A pre-commit hook (or a small assertion script) reads `fail_under` from `pyproject.toml` and `--cov-fail-under` from the `coverage` GH Actions workflow YAML, and fails if they differ. Verified by `uvx python tools/check_cov_threshold_drift.py` exiting 0 on a clean repo and 1 when the values are intentionally diverged. |

---

## 3. Implementation Steps

**Ordering rationale:** Per the **R-Architect-2 / ARCH-005** amendment, the order below applies branch protection as early as possible (immediately after Phase A's scaffolding lands and the 4 existing green workflows can be required). Phases A and B's *new* required checks are added incrementally as they go green, so there is no policy-without-enforcement window.

Each phase is a separate PR. The *first* PR through the new policy is whichever lands after protection goes live.

### Phase A — Scaffolding PR (merges unprotected; protection goes live *during* this phase, not after)

**Branch:** `chore/scaffolding-archgate-precommit-adr`
**Goal:** Lay down governance scaffolding + ARCH-005 phasing ADR + the `pr-issue-link` and `archgate` workflows. Apply branch protection as the *last commit* on this branch, *not* after merge.

| # | Action | File path(s) | Verify |
|---|---|---|---|
| A1 | Create | `.archgate/adrs/ARCH-005-stacked-pr-phasing.md` + `.archgate/adrs/ARCH-005-stacked-pr-phasing.rules.ts` — asserts that branch protection is applied at the earliest PR, not the last. | `archgate check` parses (AC-15 expanded to 5 ADRs) |
| A2 | Create | `.archgate/adrs/ARCH-001-pr-only-and-issue-link.{md,rules.ts}` | `archgate check` parses |
| A3 | Create | `.archgate/adrs/ARCH-002-main-branch-protection.{md,rules.ts}` — body contains the "Single-maintainer caveat" paragraph (AC-20). | `grep -F "Single-maintainer caveat" .archgate/adrs/ARCH-002-main-branch-protection.md` |
| A4 | Create | `.archgate/adrs/ARCH-003-uv-only-toolchain.{md,rules.ts}` | `archgate check` parses (fully active in Phase B) |
| A5 | Create | `.archgate/adrs/ARCH-004-coverage-floor-95.{md,rules.ts}` | `archgate check` parses (fully active in Phase B) |
| A6 | Create | `.github/CODEOWNERS` (`* @<owner-handle>`) | `gh api repos/:owner/:repo/codeowners` returns 200 |
| A7 | Create | `.github/PULL_REQUEST_TEMPLATE.md` with "Linked issue" line | Draft PR body pre-populates |
| A8 | Create | `.github/ISSUE_TEMPLATE/` (bug + feature) | Directory is browsable |
| A9 | Create | `tools/check_pr_link.py` — script with the AC-21 docstring block explaining the current-state behavior. | `python tools/check_pr_link.py --pr-body "Closes #1"` exits 0/1 correctly; unit test passes |
| A10 | Create | `.pre-commit-config.yaml` — `pre-commit` v4.x, 8 hooks `language: system`, heavy hooks (`pytest --cov`, `pip-audit`) staged on `pre-push` not `pre-commit`. Stub for `archgate` until A13 is complete. | `uvx pre-commit run --all-files` exits 0 (after A13) |
| A11 | Create | `tools/check_cov_threshold_drift.py` (AC-22) | Drift assertion exits 0/1 correctly |
| A12 | Create | `.github/workflows/archgate.yml` — single job `archgate` running `archgate check`. If R-Architect-1 confirms archgate needs a Node runtime, the workflow includes `actions/setup-node@v4`; AGENTS.md gets a one-line note. | Push branch; `archgate` job green |
| A13 | Create | `.github/workflows/pr-issue-link.yml` — single job `pr-issue-link` running `python tools/check_pr_link.py` with `PR_BODY` read from `github.event.pull_request.body`. | Push branch; `pr-issue-link` job green |
| A14 | Create | `.github/workflows/secrets-scan.yml` — single job `secrets-scan` running `gitleaks detect --no-banner` (or `uvx gitleaks`). | Push branch; `secrets-scan` job green |
| A15 | Create | `docs/branch-protection.md` — exact `gh api` payload, the single-maintainer caveat, the rollback snippet. | File is reviewed and merged as a normal commit on the same branch (still unprotected) |
| A16 | Modify | `pyproject.toml` — add `[tool.coverage.report] fail_under = 95` (interim; coverage config present and green but no uv-only gates yet). | `uv run pytest --cov` reports ≥95% (Poetry still works) |
| A17 | Modify | `.gitignore` — confirm/add: `.omc/state/`, `.omc/sessions/`, `.coverage`, `.ruff_cache/`, `.pytest_cache/`, `__pycache__/`, `dist/`, `*.egg-info/`, `.env`, `.env.*` (except `.env.example`). | `git check-ignore .omc/state/sessions/foo.json` returns 0 |
| A18 | Modify | `AGENTS.md` (interim) — note that uv + archgate are coming, link to `docs/branch-protection.md`. | No `poetry` reference added |
| A19 | **APPLY** | `gh api -X PUT repos/:owner/:repo/branches/main/protection` with required checks = `lint`, `test`, `coverage`, `build` (the 4 existing green workflows). 1 review, CODEOWNERS, no admin bypass, linear, no force-push, no deletion, no direct push. Run from a maintainer shell, *after* A15 merges, *before* Phase B opens. | `gh api .../branches/main/protection` returns the expected JSON (AC-1..AC-3 partial — 4 of 8 required checks) |
| A20 | Verify | Open a test PR *without* `Closes #N` — the `pr-issue-link` job (now required) must fail. (The `pr-issue-link` workflow was registered in A13; the policy now references it.) | PR shows red `pr-issue-link` check (AC-5 part a) |
| A21 | Close | Test PR from A20 without merging. | Test PR closed; `main` unchanged |

**Phase A complete criteria (after A19):**
- Branch protection is live with 4 required checks (the existing ones).
- All 5 ADRs exist and pass `archgate check`.
- The new `archgate`, `pr-issue-link`, `secrets-scan` workflows are green on the branch.
- AC-20 (single-maintainer caveat in ARCH-002), AC-21 (pr-issue-link docstring), AC-22 (coverage drift script) are satisfied.
- `.omc/{plans,specs,wiki,drafts,research}/` directories exist; `.omc/state/` is gitignored (verified via `git check-ignore`).

### Phase B — uv migration PR (first PR under the new policy, after required-check list expands)

**Branch:** `chore/uv-migration`
**Goal:** Drop Poetry, reshape `pyproject.toml`, rewrite 4 existing workflows to use `uv`, add `ty` + `deptry-pip-audit` workflows, delete `tools/pipeline_runner/`, add new required checks incrementally.

| # | Action | File path(s) | Verify |
|---|---|---|---|
| B1 | Modify | `pyproject.toml` — remove all `[tool.poetry.*]`, add `[project]` (PEP 621), bump `requests` ^2.32, `python-dotenv` ^1.1, `docker` ^7.1, `pytest` ^8.4, `pytest-cov` ^6.0, `ruff` ^0.12; add `ty`, `deptry`, `pip-audit` as dev deps; set `requires-python = ">=3.11"`; add `[tool.uv]`, `[tool.ty]`, `[tool.deptry]` sections. | `uv sync` succeeds; `uv run pytest` passes; `uv run ty check` passes; `uv run deptry .` passes; `uv run pip-audit` passes |
| B2 | Create | `uv.lock` (via `uv lock`) | `uv.lock` exists, committed |
| B3 | Delete | `poetry.lock` | `git ls-files | grep poetry.lock` returns 0 |
| B4 | Delete | `tools/pipeline_runner/` (entire tree) | `git ls-files tools/pipeline_runner/` returns 0 |
| B5 | Modify | `.github/workflows/lint.yml` — `astral-sh/setup-uv@v4` + `uv sync --frozen`; `uv run ruff check .` and `uv run ruff format --check .`; job name stays `lint` | `lint` job green |
| B6 | Modify | `.github/workflows/test.yml` — same setup-uv pattern; matrix 3.11/3.12/3.13; `uv run pytest`; job name stays `test` | `test` job green (all 3 matrix cells) |
| B7 | Modify | `.github/workflows/coverage.yml` — `uv run pytest --cov=github_runners_for_repo --cov-report=xml --cov-fail-under=95`; job name stays `coverage` | `coverage` job green; ≥95% |
| B8 | Modify | `.github/workflows/build.yml` — `uv build`; verify entry point; job name stays `build` | `build` job green |
| B9 | Create | `.github/workflows/ty.yml` — single job `ty` running `uv run ty check github_runners_for_repo` | `ty` job green |
| B10 | Create | `.github/workflows/deptry-pip-audit.yml` — single job `deptry-pip-audit` running `uv run deptry .` then `uv run pip-audit` | `deptry-pip-audit` job green |
| B11 | **EXPAND** | `gh api -X PATCH repos/:owner/:repo/branches/main/protection/required_status_checks` to add `archgate`, `pr-issue-link`, `ty`, `deptry-pip-audit` to the existing 4. (Expansion happens as the new jobs go green on the Phase B branch.) | `gh api .../required_status_checks` returns 8 contexts (AC-2) |
| B12 | Modify | `.pre-commit-config.yaml` — wire up all 8 hooks now that `uv` works; `archgate` hook invokes via `archgate` on PATH (set up in AC-14). | `uvx pre-commit run --all-files` runs 8 hooks, all pass on a clean tree |
| B13 | Modify | `AGENTS.md` (interim) — replace Poetry with uv in Development Workflow; add bootstrap section documenting `uv tool install archgate` (R-Architect-1 / AC-14). | `grep -n "poetry" AGENTS.md` returns 0 |
| B14 | Modify | `README.md` — drop Poetry / `pipeline-runner` references; add uv commands. | `grep -n "poetry" README.md` returns 0; `grep -n "pipeline-runner" README.md` returns 0 |
| B15 | Verify | `grep -r "poetry" --include="*.py" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.toml" --include="*.sh" --include="Dockerfile*" .` | 0 matches (AC-7) |

**Phase B merge criteria (under the new policy, with 4 then 8 required checks):**
- All 8 GH Actions jobs green on the branch (`lint`, `test`, `coverage`, `build`, `archgate`, `pr-issue-link`, `ty`, `deptry-pip-audit`).
- `archgate check` passes (AC-13 fully).
- `gitleaks detect` (or equivalent) passes (AC-19).
- AC-7 holds: 0 references to Poetry in tracked files.
- `tools/pipeline_runner/` is gone; `uv.lock` is present; `poetry.lock` is absent.
- Branch is up to date with `main` (linear history); 1 owner approval (CODEOWNERS); linear history is the only merge strategy.

### Phase C — AGENTS.md canonical update (PR under the new policy, post-uv-migration)

**Branch:** `docs/agents-md-governance`
**Goal:** Replace any remaining Poetry references (none should exist), add the Governance section with 7 bullets, add the "Adding a new check or rule" subsection.

| # | Action | File path(s) | Verify |
|---|---|---|---|
| C1 | Modify | `AGENTS.md` — preserve "Sustainable YOLO + Progressive Disclosure" header verbatim; in "Development Workflow" replace any Poetry with `uv`; add "Governance" section with 7 bullets from spec C5; add "Adding a new check or rule" subsection. | `head -5 AGENTS.md` shows the verbatim header; `grep -n "poetry" AGENTS.md` returns 0 (AC-16); the 7 bullets are present |
| C2 | Verify | `archgate check` (ARCH-001's rule asserts PR template exists) | `archgate` job green |
| C3 | Verify | All 8 GH Actions checks green; 1 owner approval; linear history | AC-17, AC-18 |

**Phase C merge criteria (under the new policy):**
- AC-16 holds.
- All 8 checks green.
- 1 owner approval.
- Linear history.

---

## 4. Risks and Mitigations

| # | Risk | Mitigation |
|---|---|---|
| **R1** | **Chicken-and-egg (mitigated by ARCH-005).** Branch protection cannot enforce its own prerequisites on the PR that installs it. | Phase A applies protection *with the 4 existing green workflows* (not the 8 new ones). The 4 new required checks are added incrementally in Phase B as their workflows go green. This eliminates the policy-without-enforcement window the original plan (Option 1) had. |
| **R2** | **95% coverage floor may not be achievable** on the existing 3 test files. | Phase A's A16 measures the gap. Phase B includes a "coverage uplift" commit (branch tests for `config.py` error paths, `github_api.py` HTTP errors, `runner_manager.py` container not found / already running). |
| **R3** | **`archgate` v0.43.0 + `uv` ≥0.5 + TypeScript runtime is unverified.** | The first action of Phase A is a smoke test (`archgate check` against a no-op `.rules.ts`) to confirm the tool runs end-to-end. If archgate needs Node, A12 includes `actions/setup-node@v4` and AGENTS.md (C-Phase's interim update) documents the install. AC-14 codifies the bootstrap path. |
| **R4** | **PR template enforcement vs. existing PRs** (legacy PRs opened before the template was added). | `pr-issue-link` reads the PR body text, not the template. The job runs on every push to existing PRs, so authors see the failure and can amend. |
| **R5** | **Pre-commit running all 8 checks (including `pytest --cov` and `pip-audit`) is slow.** | `language: system` (no per-hook venv). Heavy hooks (`pytest --cov`, `pip-audit`) staged on `pre-push`, not `pre-commit`. **Speed budget (per Critic C-4):** `pre-commit run --all-files` on a warm uv cache ≤ 30s; `pre-push` ≤ 120s. Documented in AGENTS.md. If a future tool addition pushes the budget over the cap, the hook is re-staged to `pre-push` or removed from pre-commit entirely; this is a strict budget, not a guideline. The budget is verified by `time uvx pre-commit run --all-files` (warm cache) and `time uvx pre-commit run --hook-stage pre-push --all-files` in CI as a non-blocking informational step (informational because wall-clock time is environment-dependent, not because the budget is soft). |
| **R6** | **`tools/pipeline_runner` deletion breaks local automation.** | Audit grep in Phase A. README and AGENTS.md updated in Phase B. |
| **R7** | **`ty` strict mode may flag many issues** in the existing 4-file package. | Run `ty check` early in Phase A. Add type hints to `github_runners_for_repo/`; configure `[tool.ty] untyped-libraries = ["docker", "requests"]` if needed; do not use blanket `# type: ignore` (AC-10). |
| **R8** | **CODEOWNERS self-review is a no-op in a single-owner repo** (the 1-review rule is trace, not separation of duties). | **Acknowledged in ARCH-002's single-maintainer caveat (AC-20).** When a second maintainer joins, the ADR amendment raises the count to 2 in the same PR. |
| **R9** | **`pr-issue-link`'s "open at PR-open time" rule is not enforceable via the GitHub REST API** (R-Architect-2). | **Documented in the script's docstring (AC-21).** The chosen behavior is to use *current* state at job run time, with the trade-off explicitly named. |
| **R10** | **Coverage threshold drift between `pyproject.toml` and the `coverage` GH Actions job.** | **Pre-commit drift assertion (AC-22)**, `tools/check_cov_threshold_drift.py`, **PLUS a CI-side drift check (per Critic C-5):** the `coverage` workflow includes a step `uv run python tools/check_cov_threshold_drift.py` immediately after the `uv run pytest --cov` step. A developer who skips pre-commit cannot drift the threshold — the CI job fails. Two enforcement points (local + CI) so a single bypass doesn't break the gate. |
| **R11** | **Committed secrets in `.omc/` artifacts** (C6 / AC-19). | `.omc/state/` is gitignored; `gitleaks detect` runs as a required check (`secrets-scan` GH Actions job + a pre-commit hook). **Baseline (per Critic C-6):** `.gitleaks.toml` ships in the repo with an `allowlist` block covering (i) `tests/fixtures/pr_link_fixtures.py` (intentional sample tokens used by AC-5's pr-issue-link unit test), (ii) `.omc/specs/deep-interview-python-modernize-shiftleft.md` lines that quote a fake token `ghp_your_token_here` from `.env.example` (gitleaks' `GH` rule has a false-positive on this exact string), and (iii) `.env.example` itself (gitleaks' generic `generic-api-key` rule is bypassed with a path-specific allowlist). The baseline is auditable: `git log -- .gitleaks.toml` shows when the allowlist was last updated, and the AC-19 verification step greps `.gitleaks.toml` for the three documented entries. |

---

## 5. Verification Steps

Exact commands to verify each AC. All commands run from the repo root unless noted.

```bash
# All commands below assume a `gh auth login` session with `repo` + `admin:repo_hook` scope,
# a configured git remote (`git remote -v` shows origin), and a Linux/macOS dev environment.
# In a fresh CI sandbox, set GH_TOKEN=<admin PAT> and configure the remote before running.

# AC-1: direct push to main rejected + protection object exists
git push origin main 2>&1 | grep -F "GH006" ; echo "exit=$?"          # expect exit=0 (substring matched), 403 stderr from GitHub
gh api repos/:owner/:repo/branches/main/protection | jq .            # expect full object

# AC-2: exactly 8 required status checks
gh api repos/:owner/:repo/branches/main/protection/required_status_checks \
  | jq '.contexts | sort'                                            # expect lint, test, coverage, build, archgate, pr-issue-link, ty, deptry-pip-audit

# AC-3: review/admin/linear/push policy (with concrete value assertions)
gh api repos/:owner/:repo/branches/main/protection | jq -e '
  .required_pull_request_reviews.required_approving_review_count == 1
  and .required_pull_request_reviews.dismiss_stale_reviews == true
  and .required_pull_request_reviews.require_code_owner_reviews == true
  and .enforce_admins.enabled == true
  and .required_linear_history.enabled == true
  and .allow_force_pushes.enabled == false
  and .allow_deletions.enabled == false
  and .restrictions == null
'                                                                     # expect exit 0

# AC-4: CODEOWNERS exists, self-covers, AND covers every path under github_runners_for_repo/
test -f .github/CODEOWNERS && head -1 .github/CODEOWNERS             # expect `* @<owner>`
# Path-coverage test: every Python file under the package must match a CODEOWNERS pattern
gh api repos/:owner/:repo/contents/.github/CODEOWNERS --jq '.content' | base64 -d > /tmp/CODEOWNERS
for f in $(find github_runners_for_repo -name '*.py'); do
  grep -qE "^[/]?\*\s+@${OWNER}" /tmp/CODEOWNERS \
    && echo "${f}: covered by * rule" \
    || echo "${f}: NOT COVERED"
done                                                                   # expect "covered by * rule" for every file

# AC-5: pr-issue-link behavior (NON-DESTRUCTIVE — uses local-only fixture, not gh issue create)
# The script accepts both env var and --pr-body CLI flag (pinned in A9).
# Fixture: a JSON file at tests/fixtures/pr_link_issues.json mapping token → state.
python tools/check_pr_link.py --pr-body "no closing keyword"; echo $?  # expect 1
python tools/check_pr_link.py --pr-body "Closes #1" --issue-state open; echo $?   # expect 0
python tools/check_pr_link.py --pr-body "Closes #1" --issue-state closed; echo $1 # expect 1

# AC-6: PR template pre-populates — open a draft PR; body contains "Linked issue"
# (Manual UI step; cannot be scripted. Document as a manual gate in Phase B verification.)

# AC-7: no Poetry anywhere
grep -r "poetry" --include="*.py" --include="*.yml" --include="*.yaml" \
  --include="*.md" --include="*.toml" --include="*.sh" --include="Dockerfile*" . \
  | grep -v "^\./\.git/"                                             # expect 0 matches
test -f uv.lock && ! test -f poetry.lock                             # expect both true
test ! -d tools/pipeline_runner                                      # expect true
grep -q "tool.poetry" pyproject.toml && echo "POETRY STILL PRESENT"   # expect no output

# AC-8: fresh-clone dev loop + coverage
# (Run on a maintainer machine with the repo URL substituted in.)
git clone "${REPO_URL}" ac8-clone && cd ac8-clone && uv sync && uv run pytest
uv run pytest --cov=github_runners_for_repo --cov-report=term-missing
cd .. && rm -rf ac8-clone                                            # expect ≥95% line+branch

# AC-9: ruff lint + format
uv run ruff check . && uv run ruff format --check .                   # expect 0
grep -E "line-length|target-version" pyproject.toml                  # expect line-length=100, py311

# AC-10: ty
uv run ty check github_runners_for_repo                              # expect 0 errors
grep -rn "# type: ignore" github_runners_for_repo tests               # expect 0; if any, must have a justification comment on the same line

# AC-11, AC-12: deptry + pip-audit
uv run deptry . && uv run pip-audit                                  # expect 0 issues each

# AC-13, AC-15: archgate
archgate check                                                       # expect exit 0
ls .archgate/adrs/                                                   # expect 10 files (5 .md + 5 .rules.ts)

# AC-14: fresh-clone bootstrap (R-Architect-1 + R-Architect-3 + Critic C-1)
git clone "${REPO_URL}" ac14-clone && cd ac14-clone
uv tool install archgate                                            # per AC-14 pinned first step
uv sync
uvx pre-commit install
uvx pre-commit run --all-files                                       # expect 8 hooks pass
# Verify the archgate TypeScript runtime story is documented (Critic C-1)
grep -F "## TypeScript runtime for archgate" AGENTS.md                # expect 1 match
gh api repos/:owner/:repo/contents/.github/workflows/archgate.yml --jq '.content' \
  | base64 -d | grep -E "archgate-runtime: (bundled|node)"            # expect 1 match
git log --grep="archgate-runtime" --oneline | head -1                 # expect non-empty
cd .. && rm -rf ac14-clone

# AC-16: AGENTS.md
head -5 AGENTS.md                                                    # expect "Sustainable YOLO" header verbatim
grep -n "poetry" AGENTS.md                                           # expect 0
grep -c "^- " AGENTS.md                                              # expect ≥7 bullets in Governance

# AC-17: GH Actions jobs match required checks
ls .github/workflows/                                                # expect the 8 workflow files
gh api repos/:owner/:repo/branches/main/protection/required_status_checks \
  | jq '.contexts'                                                   # expect the 8 names exactly

# AC-18: docs-only PR is green + mergeable (Critic C-2)
# A docs-only PR (e.g., one-line typo fix in docs/branch-protection.md) that references a
# maintenance issue via `Closes #N` results in 8/8 green checks and an enabled Merge button.
# PR must NOT be a literal empty commit (would fail pr-issue-link).
# Verified by opening such a PR and observing all 8 check contexts are `success`.

# AC-19: OMC tracking + secrets scan (Critic C-3: gitleaks over trufflehog)
git ls-files .omc/ | awk -F/ '{print $2}' | sort -u                   # expect plans, specs, wiki, drafts, research; NOT state
git check-ignore -v .omc/state/sessions/foo.json                     # expect ignored
gitleaks detect --no-banner                                          # expect 0 leaks; .gitleaks.toml baseline ships with the repo
test -f .gitleaks.toml && grep -F "tests/fixtures/pr_link_fixtures.py" .gitleaks.toml  # expect match (baseline entry)

# AC-20: single-maintainer caveat
grep -F "Single-maintainer caveat" .archgate/adrs/ARCH-002-main-branch-protection.md  # expect match

# AC-21: pr-issue-link current-state documentation (all three points)
grep -F "current issue state" tools/check_pr_link.py                  # expect point (i) — current state, not at-open
grep -F "valid at open time" tools/check_pr_link.py                   # expect point (ii) — false-positive/negative named
grep -F "current.*state at job run time" tools/check_pr_link.py       # expect point (iii) — chosen behavior

# AC-22: coverage threshold drift (CI-side check, Critic C-5)
uv run python tools/check_cov_threshold_drift.py; echo $?             # expect 0 on clean repo (idiomatic: uv run, not uvx python)
# Negative test (with explicit revert):
cp pyproject.toml pyproject.toml.bak
sed -i 's/fail_under = 95/fail_under = 80/' pyproject.toml
uv run python tools/check_cov_threshold_drift.py; echo $?             # expect 1
mv pyproject.toml.bak pyproject.toml
uv run python tools/check_cov_threshold_drift.py; echo $?             # expect 0 (revert confirmed)
```

---

## 6. RALPLAN-DR Summary

### Principles (revised post-Architect)
1. **CI parity, always.** Same `uv run` / `uvx` / `archgate` commands run locally (pre-commit) and in CI (GH Actions). No local-only or CI-only checks. AC-14, AC-17.
2. **Shift-left, but not at the cost of trust.** Pre-commit runs all 8 checks, uses `language: system` + `pre-push` staging for heavy hooks. AC-14, R5.
3. **Branch protection is a hard gate, and the gate goes live at the earliest viable moment** (REVISE 2). The single exception (installing the protection itself) is documented in ARCH-002 and the `docs/branch-protection.md` audit record. C1, ARCH-005, AC-1..AC-3.
4. **ADR-as-code is the source of architectural truth.** Every consequential decision (the 4 governance ADRs *and* the phasing decision itself, ARCH-005) is an ADR with a `.rules.ts` companion. C4, AC-13, AC-15.
5. **Smallest surface change that satisfies the spec.** Narrowed (per Critic C-7): preserve the 4-file `github_runners_for_repo/` package's public API (entry point `gh-runners`, subcommand surface `start|stop|status|build`, env-var contract in `.env.example`) and the existing GH Actions job names (`lint`, `test`, `coverage`, `build`). Internal restructuring, dependency bumps, and the deletion of `tools/pipeline_runner/` are explicitly permitted; the principle is about the **public contract**, not the file count. The governance work itself (5 ADRs, 3 new workflows, drift-assertion script, pre-commit config) is exempt from this principle — it is the subject of the plan, not the surface the plan must minimize. Non-goals section is exhaustive.
6. **Single-maintainer honesty (NEW).** A 1-review rule in a single-owner repo is trace, not separation of duties. The review layer is documented as such in ARCH-002 (AC-20) and the 8 required checks carry the actual safety. R8.

### Decision Drivers (top 3)
1. **No policy-without-enforcement window.** Branch protection must be live as early as possible. (Drives ARCH-005; revises the original Option 1 ordering.)
2. **Reviewability under the new policy.** Once protection is live, every PR is a CODEOWNERS-gated self-review. Small PRs are reviewable; giant PRs are not.
3. **Auditability and rollback.** One merge per phase, one rollback per phase. Each phase leaves a single artifact and a single commit message.

### Viable Options (revised post-Architect)

**Option 1 (REVISED) — Stacked PRs A → B → C, with protection applied inside Phase A.**
- A scaffolds + applies protection with the 4 existing green workflows as required checks.
- B does the uv migration *under the new policy*, incrementally adding the 4 new required checks as their workflows go green.
- C updates AGENTS.md canonically.
- **Pros:** each PR is reviewable; the policy-without-enforcement window is minimized (it exists only between the 4-check protection apply and the 4 new checks going green in Phase B — typically hours, not weeks); coverage uplift is scoped to Phase B; ARCH-005 codifies the principle.
- **Cons:** 3 separate merges; branch state must be carefully managed; ARCH-005 must be authored before A19.

**Option 2 — All-in-one branch merged via admin override.** (Rejected by original Planner; rejected again by Architect for the same reason: un-reviewable, no canary, hides coverage uplift in a giant diff.)

**Option 3 — Protection first, then content.** (Re-framed by Architect: apply protection today with the 4 existing green workflows; do A and B as gated PRs; incrementally add the 4 new required checks. **This is now equivalent to Option 1 (REVISED)**, with the protection-apply inside Phase A instead of as a separate Phase C docs PR.)

### Invalidation Rationale
- **Option 2 rejected:** un-reviewable, no canary, no per-phase rollback, contradicts principle 3.
- **Original Option 1 (with protection as a separate Phase C) rejected:** it left a 1–3 week policy-without-enforcement window where the ADRs asserted rules the repo didn't enforce. ARCH-005 and the revised Phase A both address this.

**Chosen: Option 1 (REVISED) — stacked PRs A → B → C, with protection applied inside Phase A.**

---

## 7. ADR (candidate for consensus refinement)

**ADR-005: Stacked-PR phasing with protection applied at the earliest viable moment**

### Decision
Adopt **Option 1 (REVISED) — stacked PRs A → B → C**, in this exact order:

1. **Phase A — Scaffolding** (merges unprotected, but ends with protection live). 5 ADRs (incl. ARCH-005), CODEOWNERS, PR template, issue templates, `tools/check_pr_link.py`, `tools/check_cov_threshold_drift.py`, `secrets-scan.yml` workflow, `.pre-commit-config.yaml` (stubs for uv-only hooks), `.github/workflows/{archgate,pr-issue-link}.yml`. Apply branch protection with the 4 existing green workflows as required checks. Verify with a test PR.
2. **Phase B — uv migration** (first PR under the new policy). Drop Poetry; reshape `pyproject.toml`; add `uv.lock`; delete `tools/pipeline_runner/`; rewrite 4 existing workflows to use `uv`; add `ty.yml` and `deptry-pip-audit.yml`; bump dep versions; expand the required-checks list to 8 as the new jobs go green; update README and AGENTS.md interim.
3. **Phase C — AGENTS.md canonical** (PR under the new policy). Add the Governance section with 7 bullets; add the "Adding a new check or rule" subsection. This PR is the canary for the full 8-check policy in steady state.

### Drivers
- **No policy-without-enforcement window.** Branch protection is live after Phase A, not after a separate "Phase C" docs PR.
- **Reviewability under the new policy.** Each PR is reviewable; each phase is one merge.
- **Auditability and rollback.** One merge per phase, one rollback per phase.
- **Chicken-and-egg avoidance via the existing 4 workflows.** Brownfield facts: 4 GH Actions workflows (`lint.yml`, `test.yml`, `coverage.yml`, `build.yml`) are already green on `main`. They serve as the initial required-checks list. The 4 new checks are added as their workflows go green in Phase B.
- **Single-maintainer honesty.** The 1-review rule is trace, not separation of duties; the 8 required checks are the actual safety. Documented in ARCH-002.

### Alternatives Considered
- **Option 2 — All-in-one branch via admin override:** rejected (un-reviewable, no canary, hides coverage uplift in a giant diff, contradicts principle 3).
- **Original Option 1 (with separate Phase C docs PR for protection):** rejected on the policy-without-enforcement window it created.
- **Hybrid: A + B combined, then C:** rejected (the A+B combined PR exceeds 30 files; loses the per-phase reviewability of Option 1 (REVISED)).

### Why Chosen
Only Option 1 (REVISED) cleanly satisfies the spec's chicken-and-egg constraint *and* the principle of "branch protection is a hard gate." Each phase's review is meaningful; coverage uplift is a focused commit; Phase C serves as a deliberate canary proving the policy accepts a real PR.

### Consequences
- 3 PRs to land, in a strict order; out-of-order execution will deadlock.
- Phase A cannot benefit from the new policy — it *is* the policy's foundation.
- The single maintainer-override exception (the `gh api` apply inside Phase A) is documented in `docs/branch-protection.md` and ARCH-002.
- The 95% coverage floor is measured for the first time in Phase A; if it fails, Phase B cannot merge and the project must add tests before the policy can be installed.
- ARCH-005 asserts this phasing as the canonical pattern; future governance migrations are expected to follow the same model.

### Follow-ups
- Add signed commits via ADR (out of scope this round).
- Add Dependabot or Renovate (out of scope).
- Add PyPI release publishing (out of scope).
- If a second maintainer joins, raise `required_approving_review_count` to 2 in the same ADR amendment as ARCH-002.
- If `archgate` v0.43.x is deprecated, the ADR for tool choice (ARCH-003-adjacent) will need to be revisited.

---

## 8. Pre-mortem (3 failure scenarios per phase)

### Phase A — Scaffolding
- **A-fail-1: archgate's `.rules.ts` parser rejects the TypeScript syntax in our 5 starter rules.** *Detection:* A1's verify (`archgate check` parses). *Recovery:* rewrite in `.rules.json` if archgate supports it; pin a more lenient archgate version.
- **A-fail-2: `tools/check_pr_link.py` misidentifies `Closes #1abc` as issue 1 followed by junk.** *Detection:* A9's unit test. *Recovery:* tighten regex to `\b(?:Closes|Fixes|Resolves)\s+#(\d+)\b`; reject trailing characters.
- **A-fail-3: archgate requires Node, but the `archgate` workflow omits `actions/setup-node@v4`.** *Detection:* A12's verify (job red). *Recovery:* add the step; update AGENTS.md interim (A18).
- **A-fail-4: `gh api` apply fails with 403 (admin scope missing).** *Detection:* A19. *Recovery:* `gh auth login` with `repo` + `admin:repo_hook` scopes; re-run.
- **A-fail-5: After protection is applied, the test PR's `pr-issue-link` job is stuck yellow** because the workflow hasn't been registered on the test branch. *Detection:* A20. *Recovery:* first push to the test branch must be a normal commit; the `on: pull_request` trigger then registers it.
- **A-fail-6: `gitleaks detect` flags a historical commit that included a token.** *Detection:* A14's job red. *Recovery:* this is a security incident — rotate the token, rewrite history (`git filter-repo`), and add a pre-receive hook. Document the incident in `docs/incidents/`.

### Phase B — uv migration
- **B-fail-1: `uv sync` fails because `pyproject.toml`'s `[project]` is missing a PEP 621 field.** *Detection:* B1's verify. *Recovery:* add the missing field; reference PEP 621.
- **B-fail-2: `ty` reports dozens of errors** in the existing 4 files. *Detection:* B1's verify. *Recovery:* `[tool.ty] untyped-libraries = ["docker", "requests"]` + add type hints to public functions; no blanket `# type: ignore`.
- **B-fail-3: `uv run pytest --cov` reports <95%**, blocking the merge. *Detection:* B1's verify. *Recovery:* add branch tests for `config.py` (missing env, malformed JSON), `github_api.py` (HTTP errors, 404, 401), `runner_manager.py` (container not found, already running).
- **B-fail-4: `tools/pipeline_runner` deletion breaks an external CI script.** *Detection:* R6's grep audit. *Recovery:* AGENTS.md release note.
- **B-fail-5: A required check rename mid-migration breaks GH's name resolution** (e.g., the `lint` job is renamed to `lint-check` between B5 and B11). *Detection:* B11's expansion (the PATCH may silently drop the old name). *Recovery:* re-apply protection with the correct name; the API is idempotent.
- **B-fail-6: The new `ty` / `deptry-pip-audit` workflows never get added to the required-checks list** (B11 is forgotten). *Detection:* AC-2 fails. *Recovery:* PATCH the protection; re-verify.

### Phase C — AGENTS.md update
- **C-fail-1: `grep -n "poetry" AGENTS.md` returns 1 match** (a stray historical mention). *Detection:* C1's verify. *Recovery:* rewrite to "the previous Poetry-based toolchain" without the bare word.
- **C-fail-2: The "Sustainable YOLO + Progressive Disclosure" header is modified.** *Detection:* C1's `head -5` check. *Recovery:* revert the header change.
- **C-fail-3: The `pr-issue-link` job fails on this PR** because the author forgot `Closes #N`. *Detection:* C3. *Recovery:* edit the body; push; job goes green.
- **C-fail-4: The owner self-review doesn't propagate**, leaving the merge button disabled. *Detection:* AC-18. *Recovery:* explicit "Approve" on the self-PR.

---

## 9. Items Not Changed (intentional)

- 5-component topology and 18 original ACs (AC-1..AC-18) are sound.
- Choice of uv over Poetry, ty over mypy, 95% coverage, dropping `tools/pipeline_runner` are all correctly decided in the spec.
- 4 starter ADRs (ARCH-001..004) and their `.rules.ts` siblings are well-scoped; the new ARCH-005 is added without disturbing them.
- The `.omc/` git-tracking addendum (C6 / AC-19) is scoped to the directories the user named (`plans`, `specs`, `wiki`) plus the typical stateless OMC artifacts (`drafts`, `research`); `.omc/state/` is explicitly excluded.

---

## 10. Items the Critic Should Validate

- [ ] AC-1..AC-18 are testable as written (90%+ concrete).
- [ ] AC-19 (OMC tracking + secrets scan) is testable; the `gitleaks` choice is defensible.
- [ ] AC-20 (single-maintainer caveat) is testable as a literal-string grep.
- [ ] AC-21 (pr-issue-link current-state documentation) is testable as a literal-string grep.
- [ ] AC-22 (coverage threshold drift) is testable; the assertion script is small and reviewable.
- [ ] All 5 risks (R-Architect-1..4 + R8..R11) are addressed by named acceptance criteria.
- [ ] No vague terms in the plan ("fast", "modern", "state-of-the-art" are all replaced with specific versions, specific commands, or removed).
- [ ] Plan saved to `.omc/plans/ralplan-python-modernize-shiftleft.md`.
- [ ] RALPLAN-DR summary has 6 principles, 3 drivers, 3 options with one chosen and 2 invalidated.
- [ ] ADR section includes Decision, Drivers, Alternatives considered, Why chosen, Consequences, Follow-ups.
- [ ] Pre-mortem has 3 scenarios per phase (6 total, vs the spec's minimum of 3).

---

**End of plan. Awaiting Critic pass.**
