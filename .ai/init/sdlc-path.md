# SDLC Path — github-runners-for-repo

**Profile**: brownfield tooling
**Date**: 2026-10-18

This brownfield repo runs **autobahn** directly — no hosted issue tracker, no hosted wiki, no language-pack configuration.

## Work intake
- Through **PR description only** (no Jira / Linear / GitHub Project board).
- Every PR body must contain `Closes #N` / `Fixes #N` (enforced by archgate ADR-001 and CI workflow `pr-issue-link`).
- Work tracking for the AI run lives under `.omc/plans/` (operational, gitignored) and `.omc/specs/` (operational, gitignored by default; committable only when the run produces a durable spec).

## Toolchain
- Python 3.11+; `uv` is the only Python toolchain (ADR-003).
- `pytest` for tests; coverage floor 95% (ADR-004).
- `ruff check` + `ruff format` for lint/format.
- `ty` for type checking.
- `deptry` + `pip-audit` for dependency hygiene.
- archgate for ADR compliance.

## CI / verification
- GitHub Actions, 8 required checks + 1 optional (`secrets-scan`).
- Pre-commit mirrors the 5 fast hooks locally (lint, test, archgate, pr-issue-link, ty); pre-push mirrors coverage + deptry-pip-audit.
- All 8 checks must pass before merge.

## Merge policy
- 1 approving review.
- Admin is NOT exempt from review (single-owner repo, ADR-002).
- Single-owner merge deadlock: temp-relax `enforce_admins` -> admin `--squash` merge -> restore. This is the documented workaround, not a bypass.

## Lifecycle (4 phases)

1. **Bootstrap** — capture `.ai/init/*` + `.ai/matrix.json`. (Done at init.)
2. **Spec & Plan** — write the spec, sequence the plan. PR description is the durable plan for reviewers; `.omc/plans/` holds the AI-side plan.
3. **Implement** — smallest viable diff. Mirror CI locally. Keep coverage >=95%.
4. **Verify & Ship** — run all 8 checks on the PR, ensure green, merge through the deadlock workaround if blocked.
