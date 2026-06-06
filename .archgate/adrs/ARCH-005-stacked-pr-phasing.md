---
id: ARCH-005
title: Stacked-PR phasing for governance migrations
domain: governance
rules: true
files: [".github/workflows/*.yml", "pyproject.toml", "AGENTS.md"]
---

# ARCH-005 — Stacked-PR phasing for governance migrations

## Context

Brownfield governance migrations (adding branch protection, swapping the
Python toolchain, switching the secrets scanner) have a chicken-and-egg
problem: the policy cannot enforce its own prerequisites on the PR that
installs it.

Two ordering strategies were considered:

- **Option 1 (rejected):** Apply branch protection *after* the migration
  is fully merged. Result: a 1–3 week policy-without-enforcement window
  where the ADRs assert rules the repo does not enforce.
- **Option 2 (chosen):** Apply branch protection inside the *first* PR of
  the migration, using the existing green workflows as the initial
  required-checks list. Subsequent required checks are added
  incrementally as their workflows go green.

## Decision

For every governance migration the project adopts a stacked-PR phasing
where the policy goes live at the earliest viable moment:

1. **Phase A** scaffolds the new artifacts and applies protection using
   the *existing* green workflows as required checks.
2. **Phase B** rewires the existing workflows to the new toolchain and
   adds the *new* required checks incrementally, one per workflow that
   goes green.
3. **Phase C** is the canary PR under the full new policy.

The companion rule (`branch-protection-is-applied`) enforces that branch
protection has at least one required check — i.e. that the project is
not in the "no policy" state.

## Do's and Don'ts

**Do:**

- Use the existing green workflows as the initial required-checks list
  for the protection-apply commit.
- Add new required checks as their workflows go green; do not pre-emptively
  require a check that has not yet registered a green run.
- Document the policy-without-enforcement window in the ADR and in
  `docs/branch-protection.md`.

**Don't:**

- Don't merge all phases into a single PR. Per-phase reviewability is
  load-bearing for the audit story.
- Don't apply branch protection with zero required checks.
- Don't apply branch protection using checks that have never run green.

## Implementation Pattern

Good example — applied inside Phase A (this branch):

```yaml
required_status_checks:
  contexts:
    - lint
    - test
    - coverage
    - build
```

Bad example — would defeat the purpose:

```yaml
required_status_checks:
  contexts: []   # empty list ⇒ no policy
```

## Consequences

**Positive:** the policy-without-enforcement window is bounded to
hours (the time between Phase A's apply and Phase B's check expansion),
not weeks. Each phase is independently rollback-able.

**Negative:** the project lives in a "4 of 8 required checks" state
between Phase A merge and Phase B's B11 expand step. This is documented
in `docs/branch-protection.md` and the audit log.

**Risks:** if a workflow rename happens between phases, GitHub's
required-check resolution silently drops the old name and the policy
becomes less strict. Mitigated by checking the protection object after
each apply.

## Compliance and Enforcement

**Automated:**

- `.archgate/adrs/ARCH-005-stacked-pr-phasing.rules.ts` —
  `branch-protection-is-applied` rule asserts the protection object has
  at least one required check via `gh api`.

**Manual:**

- The PR author must call out the phase number in the PR title
  (e.g., `Phase B: uv migration`).

## References

- `.omc/plans/ralplan-python-modernize-shiftleft.md` §3 (Phase A
  ordering rationale)
- `.omc/plans/ralplan-python-modernize-shiftleft.md` §6.1 (Option 1 REVISED)
- ARCH-002 (main branch protection) — the apply step that ARCH-005 makes
  possible
