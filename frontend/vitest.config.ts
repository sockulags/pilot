import { defineConfig } from "vitest/config";

// Test runner for the frontend. The current baseline covers pure-logic
// helpers (no DOM), so the default node environment is enough; switch
// `environment` to "jsdom" and add @testing-library/react when component
// tests land.
export default defineConfig({
  test: {
    environment: "node",
    include: ["**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "out/**"],
  },
});
