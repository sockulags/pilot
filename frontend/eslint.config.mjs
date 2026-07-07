// Flat ESLint config for the Next 16 / React 19 frontend.
// `pnpm lint` runs this alongside the TypeScript type-check; CI runs the same.
import next from "eslint-config-next";
import tseslint from "typescript-eslint";

const config = [
  {
    ignores: [
      ".next/**",
      "out/**",
      "node_modules/**",
      "next-env.d.ts",
    ],
  },
  ...next,
  {
    // Repo-specific rule tuning. Scoped to TS/TSX so the @typescript-eslint
    // plugin (registered here) is available for the rules below.
    files: ["**/*.ts", "**/*.tsx"],
    plugins: {
      "@typescript-eslint": tseslint.plugin,
    },
    rules: {
      // Next 16 flags synchronous setState inside an effect as an error. The
      // few current uses are deliberate SSR-safe hydration patterns (e.g.
      // reading localStorage on mount in ProjectBar, or syncing an open flag
      // in ActionLog/SettingsPanel) where a lazy initializer would break the
      // static export. Keep the rule visible as a warning to revisit rather
      // than rewrite working components under this lint bootstrap.
      "react-hooks/set-state-in-effect": "warn",

      // Turn unused imports/vars into a hard error so the lint gate catches
      // dead code and stray imports (the canonical case in issue #55). The
      // base JS rule is disabled in favour of the TS-aware one; `_`-prefixed
      // args/vars are treated as intentionally unused.
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
];

export default config;
