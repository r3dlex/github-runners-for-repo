/// <reference path="../rules.d.ts" />

export default {
  rules: {
    "coverage-floor-asserted": {
      description:
        "pyproject.toml must declare [tool.coverage.report] fail_under = 95 (per ARCH-004 95% coverage floor)",
      severity: "error",
      async check(ctx) {
        let pyproject: string | null = null;
        try {
          pyproject = await ctx.readFile("pyproject.toml");
        } catch {
          ctx.report.violation({
            message:
              "ARCH-004 requires pyproject.toml to exist so the coverage floor can be asserted.",
            file: "pyproject.toml",
            fix: "Create pyproject.toml with a [tool.coverage.report] section declaring `fail_under = 95`.",
          });
          return;
        }

        // Tolerate formatting whitespace.
        const m = pyproject.match(
          /\[tool\.coverage\.report\][^\[]*?fail_under\s*=\s*(\d+)/
        );
        if (!m) {
          ctx.report.violation({
            message:
              "pyproject.toml is missing [tool.coverage.report] fail_under. ARCH-004 mandates `fail_under = 95`.",
            file: "pyproject.toml",
            fix: "Add the section:\n[tool.coverage.report]\nfail_under = 95",
          });
          return;
        }

        const value = Number.parseInt(m[1], 10);
        if (value !== 95) {
          ctx.report.violation({
            message: `Coverage floor is ${value}, ARCH-004 mandates 95.`,
            file: "pyproject.toml",
            fix: "Set `[tool.coverage.report] fail_under = 95`.",
          });
        }
      },
    },
  },
} satisfies RuleSet;
