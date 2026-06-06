# Branch protection

This document records the **exact** `gh api` payload used to apply branch
protection on `main` and the rollback snippet. It is the audit trail
for the policy install that lands in Phase A.

> **Phase A applies protection with 4 required checks** (the existing
> green workflows: `lint`, `test`, `coverage`, `build`). The remaining
> 4 checks (`archgate`, `pr-issue-link`, `ty`, `deptry-pip-audit`) are
> added incrementally in Phase B as their workflows go green. This
> matches the ARCH-005 stacked-PR phasing principle.

## Policy

| Field | Value |
|---|---|
| `required_approving_review_count` | 1 |
| `dismiss_stale_reviews` | true |
| `require_code_owner_reviews` | true |
| `enforce_admins` | true |
| `required_linear_history` | true |
| `allow_force_pushes` | false |
| `allow_deletions` | false |
| `restrictions` | null |
| `required_status_checks.strict` | false |
| `required_status_checks.contexts` | `lint`, `test`, `coverage`, `build` |

### Single-maintainer caveat

In a single-owner repo the 1-review rule is a **trace step**, not a
separation-of-duties gate. Safety is provided by the 4 (Phase A) or 8
(Phase B) required status checks. When a second maintainer joins,
`required_approving_review_count` must be raised to 2 in the same ADR
amendment (ARCH-002).

## Exact payload

The payload is stored at `.github/protection-payload-phase-a.json` so
the apply command and the doc can be diffed together. Copy for
reference:

```json
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["lint", "test", "coverage", "build"]
  },
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "enforce_admins": true,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "restrictions": null
}
```

## Apply

```bash
gh api -X PUT \
  repos/r3dlex/github-runners-for-repo/branches/main/protection \
  --input .github/protection-payload-phase-a.json
```

## Verify

```bash
gh api repos/r3dlex/github-runners-for-repo/branches/main/protection
```

The response must echo the payload above. Spot checks:

```bash
# 4 required checks
gh api repos/r3dlex/github-runners-for-repo/branches/main/protection/required_status_checks \
  | jq '.contexts | sort'
# => ["build", "coverage", "lint", "test"]

# 1 review, CODEOWNERS, no admin bypass, linear, no force-push, no deletion
gh api repos/r3dlex/github-runners-for-repo/branches/main/protection | jq -e '
  .required_pull_request_reviews.required_approving_review_count == 1
  and .required_pull_request_reviews.dismiss_stale_reviews == true
  and .required_pull_request_reviews.require_code_owner_reviews == true
  and .enforce_admins.enabled == true
  and .required_linear_history.enabled == true
  and .allow_force_pushes.enabled == false
  and .allow_deletions.enabled == false
  and .restrictions == null
'
```

## Rollback

If the policy must be relaxed (e.g., a hotfix needs to land before
Phase B's check expansion), the rollback is symmetric. **Do not
delete the policy** — the canonical rollback is to disable
`enforce_admins` temporarily, which is the only field that is
audit-visible at the GitHub UI level:

```bash
gh api -X PATCH \
  repos/r3dlex/github-runners-for-repo/branches/main/protection \
  --input <(jq '.enforce_admins = false' .github/protection-payload-phase-a.json)

# Re-apply after the hotfix:
gh api -X PUT \
  repos/r3dlex/github-runners-for-repo/branches/main/protection \
  --input .github/protection-payload-phase-a.json
```

For an emergency, the policy can be deleted entirely (the GitHub UI
also offers this). The apply above is idempotent, so re-applying from
the payload is the canonical recovery.

## Audit log

| Date | Action | Operator | Notes |
|---|---|---|---|
| 2026-06-06 | Phase A apply (4 checks) | r3dlex | Initial install. See `.omc/plans/ralplan-python-modernize-shiftleft.md` §3. |

## References

- `.archgate/adrs/ARCH-002-main-branch-protection.md`
- `.archgate/adrs/ARCH-005-stacked-pr-phasing.md`
- `.github/protection-payload-phase-a.json`
