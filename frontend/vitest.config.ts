import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react-swc";
import { fileURLToPath, URL } from "node:url";

/** Resolve shared core modules for tests; production keeps them external. */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@labsmanager/plugin-ui": fileURLToPath(
        new URL(
          "../../../../../django_project/labsmanager/frontend/src/plugin-api.ts",
          import.meta.url,
        ),
      ),
      "@labsmanager/plugin-calendar": fileURLToPath(
        new URL(
          "../../../../../django_project/labsmanager/frontend/src/plugin-calendar.ts",
          import.meta.url,
        ),
      ),
    },
  },
  test: { environment: "jsdom" },
});
