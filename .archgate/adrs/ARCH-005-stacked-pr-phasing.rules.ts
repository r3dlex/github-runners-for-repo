/// <reference path="../rules.d.ts" />

export default {
  rules: {
    "branch-protection-is-applied": {
      description:
        "Main branch must have at least one required status check (per ARCH-005 stacked-PR phasing)",
      severity: "error",
      async check(ctx) {
        // Static check: at least one GH Actions workflow defines a
        // `pull_request` or `push` trigger on main, AND the
        // docs/branch-protection.md file exists documenting the policy.
        // The companion dynamic check (the live `gh api` call against
        // the protection object) is performed in the
        // `scripts/check_branch_protection.sh` script (out of scope for
        // archgate which evaluates static files only).

        let policyDoc: string | null = null;
        try {
          policyDoc = await ctx.readFile("docs/branch-protection.md");
        } catch {
          ctx.report.violation({
            message:
              "ARCH-005 requires docs/branch-protection.md to document the policy. Run `archgate check` after creating the file.",
            file: "docs/branch-protection.md",
            fix: "Create docs/branch-protection.md describing the required status checks, the 1-review rule, the single-maintainer caveat, and the rollback snippet.",
          });
          return;
        }
        if (!policyDoc || !/required[_\s]status[_\s]checks/i.test(policyDoc)) {
          ctx.report.violation({
            message:
              "ARCH-005 requires docs/branch-protection.md to document the required status checks policy. Add a 'Required status checks' section.",
            file: "docs/branch-protection.md",
            fix: "Add a 'Required status checks' section to docs/branch-protection.md that names the 8 required contexts (lint, test, coverage, build, archgate, pr-issue-link, ty, deptry-pip-audit).",
          });
        }
      },
    },
  },
} satisfies RuleSet;
