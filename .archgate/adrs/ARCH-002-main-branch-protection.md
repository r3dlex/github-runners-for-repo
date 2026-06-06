---
id: ARCH-002
title: Main branch protection
domain: governance
rules: true
files: [".github/CODEOWNERS", "docs/branch-protection.md"]
---

# ARCH-002 — Main branch protection

## Context

The `main` branch needs a hard gate to keep the project shippable.
The 4 baseline guarantees are:

1. **No direct push.** Every change to `main` comes from a reviewed PR.
2. **No force-push or branch deletion.** History is preserved.
3. **Linear history.** The merge strategy is rebase or squash; merge
   commits are disallowed.
4. **Admin bypass is off.** Even repo admins go through the gate.

In addition the project uses the 8 required status checks listed in
`.omc/plans/ralplan-python-modernize-shiftleft.md` AC-2 as the actual
safety net (the review count is trace, not separation of duties — see
the single-maintainer caveat below).

## Decision

Branch protection on `main` enforces the following policy:

| Field | Value |
|---|---|
| `required_approving_review_count` | 1 (see caveat) |
| `dismiss_stale_reviews` | true |
| `require_code_owner_reviews` | true |
| `enforce_admins` | true |
| `required_linear_history` | true |
| `allow_force_pushes` | false |
| `allow_deletions` | false |
| `restrictions` | null |
| `required_status_checks.contexts` | `lint`, `test`, `coverage`, `build`, `archgate`, `pr-issue-link`, `ty`, `deptry-pip-audit` |

The exact `gh api` payload and the rollback snippet live in
`docs/branch-protection.md`. The companion rule asserts
`.github/CODEOWNERS` exists.

**Single-maintainer caveat:** in a single-owner repo the 1-review rule
is a trace step, not a separation-of-duties gate. Safety is provided
by the 8 required status checks. If a second maintainer joins,
`required_approving_review_count` must be raised to 2 in the same
ADR amendment.

## Do's and Don'ts

**Do:**

- Re-apply protection with the same payload after any
  `gh api .../branches/main/protection` change to verify it stuck.
- Raise `required_approving_review_count` to 2 the same PR that adds
  the second maintainer to `CODEOWNERS`.

**Don't:**

- Don't add `restrictions` (push allow-list) — the gate is the
  required-checks policy, not a people allow-list.
- Don't disable `enforce_admins` to "make my own PR merge" — that
  defeats the gate.

## Implementation Pattern

Good example — `docs/branch-protection.md` documents the payload and
the rollback snippet (the doc is committed alongside the apply commit).

Bad example — applying protection with `enforce_admins: false` to
"unblock a hotfix":

```bash
gh api -X PUT .../branches/main/protection \
  --input <(jq '.enforce_admins.enabled = false' payload.json)
```

## Consequences

**Positive:** the project has a hard gate that survives maintainer
turnover. The 8 required checks are an audit trail.

**Negative:** the single-maintainer caveat is honest: a self-review in
a single-owner repo does not provide separation of duties. The
workaround is the 8 required checks.

**Risks:** a forgotten `gh api` apply in an emergency (e.g., reverting
a bad release) means the gate has to be temporarily disabled. Mitigated
by `enforce_admins: true` so the override is at least visible.

## Compliance and Enforcement

**Automated:**

- `.archgate/adrs/ARCH-002-main-branch-protection.rules.ts` — asserts
  `.github/CODEOWNERS` exists and the policy doc is committed.
- The `gh api .../branches/main/protection` object is verified at
  every required-check expansion (Phase B's B11).

**Manual:**

- A new maintainer joining the project must trigger the ADR amendment
  that raises the review count to 2.

## References

- ARCH-001 (PR-only — the gate's input)
- ARCH-005 (stacked-PR phasing — how the gate goes live)
- `.github/CODEOWNERS`
- `docs/branch-protection.md`
- AC-3 (the policy's literal values)
- AC-20 (this caveat)
