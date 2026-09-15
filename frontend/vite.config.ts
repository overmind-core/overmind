/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const isVitest = Boolean(process.env.VITEST);

const config = defineConfig({
  optimizeDeps: {
    // Each icon is a separate subpath entry, so Vite would otherwise discover
    // them one-by-one as components mount and re-optimize mid-session. That
    // invalidates the ?v=<hash> URLs the browser already holds: "504 Outdated
    // Optimize Dep" → "Failed to fetch dynamically imported module".
    include: [
      "@lobehub/icons/es/Anthropic",
      "@lobehub/icons/es/Cohere",
      "@lobehub/icons/es/Gemini",
      "@lobehub/icons/es/Meta",
      "@lobehub/icons/es/Mistral",
      "@lobehub/icons/es/OpenAI",
      "@lobehub/icons/es/Qwen",
      "@lobehub/icons/es/XAI",
    ],
  },
  plugins: [
    // Tailwind + route codegen are build/dev-only; under vitest they inflate
    // transform time without helping unit tests.
    ...(!isVitest
      ? [
          tailwindcss(),
          tanstackRouter({
            autoCodeSplitting: true,
            generatedRouteTree: "./src/routeTree.gen.ts",
            routesDirectory: "./src/routes",
            target: "react",
          }),
        ]
      : []),
    viteReact(),
  ],
  publicDir: "public",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "pixelarticons/react": fileURLToPath(
        new URL("./node_modules/pixelarticons/react/index.js", import.meta.url)
      ),
    },
    tsconfigPaths: true,
  },
  test: {
    // Overrides the vitest 4 default of forks: threads cut wall time ~3–4× here
    // because import/transform dominate and share better across workers.
    pool: "threads",
    // zod v4's conditional CJS/ESM exports break vitest's default
    // externalization: the named `z` export doesn't survive interop
    // (`import { z } from "zod"` → undefined). Inlining routes zod through
    // vite's transform, as the app build already does.
    server: { deps: { inline: ["zod"] } },
    setupFiles: ["./src/test/setup.ts"],
  },
});

export default config;
