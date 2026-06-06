/// <reference path="../rules.d.ts" />

const POETRY_PATTERN = /poetry/i;

export default {
  rules: {
    "no-poetry-references": {
      description:
        "Tracked files must not reference Poetry (per ARCH-003 uv-only toolchain)",
      severity: "error",
      async check(ctx) {
        // The ADR's MD file itself contains the word "poetry" in its
        // Context/Decision/Examples sections. We exclude the ADRs
        // directory so the rule is self-describing without being
        // self-violating.
        const targets = ctx.scopedFiles.filter(
          (f) => !f.startsWith(".archgate/")
        );

        for (const file of targets) {
          // Skip the .archgate rule files themselves (they reference
          // poetry by name in the description).
          if (file === ".archgate/adrs/ARCH-003-uv-only-toolchain.rules.ts") {
            continue;
          }

          const matches = await ctx.grep(file, POETRY_PATTERN);
          for (const m of matches) {
            ctx.report.violation({
              message: `Tracked file references "poetry" — uv is the only Python toolchain (ARCH-003).`,
              file,
              line: m.line,
              fix: "Replace with the `uv` equivalent. See `.archgate/adrs/ARCH-003-uv-only-toolchain.md`.",
            });
          }
        }
      },
    },
  },
} satisfies RuleSet;
