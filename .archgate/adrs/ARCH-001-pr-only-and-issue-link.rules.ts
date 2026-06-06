/// <reference path="../rules.d.ts" />

export default {
  rules: {
    "pr-template-and-issue-templates-exist": {
      description:
        "PR template and issue templates must exist (per ARCH-001 PR-only and issue-link)",
      severity: "error",
      async check(ctx) {
        let prTemplate: string | null = null;
        try {
          prTemplate = await ctx.readFile(".github/PULL_REQUEST_TEMPLATE.md");
        } catch {
          ctx.report.violation({
            message:
              "ARCH-001 requires .github/PULL_REQUEST_TEMPLATE.md to exist. The PR template pre-populates the 'Linked issue' field that the pr-issue-link job requires.",
            file: ".github/PULL_REQUEST_TEMPLATE.md",
            fix: "Create .github/PULL_REQUEST_TEMPLATE.md with a 'Linked issue' line so opening a new PR guides the author to include `Closes #N`.",
          });
          return;
        }

        if (!/linked\s+issue/i.test(prTemplate)) {
          ctx.report.violation({
            message:
              "PR template must mention 'Linked issue' so the author is prompted to add `Closes #N` / `Fixes #N` / `Resolves #N`.",
            file: ".github/PULL_REQUEST_TEMPLATE.md",
            fix: "Add a 'Linked issue' section that instructs the author to add `Closes #N` to the PR body.",
          });
        }
      },
    },
  },
} satisfies RuleSet;
