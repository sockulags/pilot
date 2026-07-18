import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Test runner for the frontend. The current baseline covers pure-logic
// helpers (no DOM), so the default node environment is enough; switch
// `environment` to "jsdom" and add @testing-library/react when component
// tests land.
export default defineConfig({
  resolve: {
    // Mirror the "@/*" path alias from tsconfig so tests can import module-level
    // helpers that live alongside app/component code without hand-rolling paths.
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "out/**"],
  },
});
