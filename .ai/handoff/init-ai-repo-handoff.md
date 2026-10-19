# Init Handoff — github-runners-for-repo

**Skill**: ai-catapult:ai-catapult-init (v3)
**Date**: 2026-10-18
**Branch**: `fix/linux-arm64-archgate-and-uv`
**Outcome**: Autobahn prereq gate satisfied. Four required files produced; minimum supporting scaffolding only.

## What was initialized

### Autobahn prereq (the four required files)

| File | Purpose |
|---|---|
| `.ai/matrix.json` | AI capability matrix — declares the repo as `tooling` + `library` + `infra`, single Python/uv stack, 8-check GH Actions CI, single-owner governance, no hosted tracker. |
| `.ai/workflows/repo-workflow.json` | Workflow manifest — names the 4 lifecycle phases, the 8 required checks with their workflow files + commands, and the 5 in-force archgate ADRs. |
| `.ai/handoff/` | This handoff directory. |
| `.ai/traceability/graph.json` | Traceability graph. Empty graph (`{"nodes":[],"edges":[]}`); autobahn only requires parse-valid JSON. Populated as work lands in later phases. |

### Supporting artifacts (minimum)

| File | Purpose |
|---|---|
| `.ai/init/repo-profile.json` | Brownfield profile: Python tooling, GitHub Actions, single-owner, autobahn-ready. |
| `.ai/init/sdlc-path.md` | Notes that this brownfield repo runs autobahn directly, has no hosted tracker, and routes work intake via PR description (Closes #N). |
| `.ai/phases/{0-bootstrap,1-spec,2-implement,3-verify}/.gitkeep` | Phase directories so future phases can drop content without re-creating the tree. |
| `RULES.md` | Local safety rules: don't commit `.omc/**` except `.omc/skills/**`; don't mutate host policy without explicit user opt-in; don't bypass autobahn fail-closed gates. |
| `PLANS.md` | One-line: current branch + next goal (ship `.archgate/rules.d.ts` through autobahn). |
| `CONTRIBUTING.md` | Short — point contributors at the autobahn workflow. |

## Stack snapshot

- **Language**: Python 3.11+
- **Toolchain**: `uv` only (ADR-003)
- **Package**: `hatchling` build backend, wheel-first
- **Test**: `pytest` (>=9.0), coverage floor 95% (ADR-004)
- **Lint/format**: `ruff`
- **Type check**: `ty`
- **Audit**: `deptry` + `pip-audit`
- **CI**: GitHub Actions — 8 required checks
- **Governance**: archgate + `.archgate/adrs/` (5 in-force ADRs)
- **Host policy**: existing branch protection on `main`; admin not exempt; 1 review required (single-owner)

## Deviations from spec

1. **AGENTS.md was NOT created.** The spec asked for a one-page `AGENTS.md`, but `AGENTS.md` already exists as a tracked file on `fix/linux-arm64-archgate-and-uv` (11.6K, comprehensive, references all 5 ADRs and the bootstrap protocol). The init rule forbids editing existing tracked files, so this file was left untouched. The existing `AGENTS.md` already covers the directives the spec wanted in the new AGENTS.md (project memory directive for Python/pytest/ruff, the autobahn route, no-host-policy-mutation rule, .omc/.omx gitignore rule, and the merge-deadlock pattern). **If a one-page canonical AGENTS.md is desired, it must be a deliberate edit to the existing tracked file — out of scope for the init-only step.**

## Deliberately NOT done

- No commit, push, branch creation, or PR opened. Init produces files only.
- No edit to any tracked file (`.gitignore`, `.pre-commit-config.yaml`, `AGENTS.md`, `pyproject.toml`, `README.md`, etc.).
- No edit to `.omc/**` or `.omx/**` (operational layer).
- No `.ai/bin/`, `.ai/policies/`, `.ai/commands/omx/`, `.ai/commands/omc/`, `.ai/language-packs/` — Phase 3 over-scoping, not required by autobahn prereq.
- No hosted mutation (branch protection, workflow edits, GitHub API calls).
- No rewrite of existing tracked `AGENTS.md` (see Deviations).

## Verification

All four autobahn prereq files exist and parse as valid JSON / markdown at the expected paths. The next step is to run autobahn, which will gate on these artifacts before any code change is attempted.
