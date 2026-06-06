/// <reference path="../rules.d.ts" />

export default {
  rules: {
    "codeowners-exists": {
      description:
        ".github/CODEOWNERS must exist (per ARCH-002 main branch protection)",
      severity: "error",
      async check(ctx) {
        let codeowners: string | null = null;
        try {
          codeowners = await ctx.readFile(".github/CODEOWNERS");
        } catch {
          ctx.report.violation({
            message:
              "ARCH-002 requires .github/CODEOWNERS so `require_code_owner_reviews` can match a reviewer.",
            file: ".github/CODEOWNERS",
            fix: "Create .github/CODEOWNERS with at least one rule, e.g. `* @r3dlex` for a single-owner repo.",
          });
          return;
        }

        // A CODEOWNERS file with no rules matches no paths — flag it.
        const hasRule = codeowners
          .split("\n")
          .some((line) => line.trim() && !line.trim().startsWith("#"));
        if (!hasRule) {
          ctx.report.violation({
            message:
              "CODEOWNERS contains no rules — every path will be uncovered. Add at least one pattern.",
            file: ".github/CODEOWNERS",
            fix: "Add a wildcard rule, e.g. `* @r3dlex`.",
          });
        }
      },
    },
  },
} satisfies RuleSet;
