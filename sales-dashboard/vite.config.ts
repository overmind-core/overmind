import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    tailwindcss(),
    tanstackRouter({
      autoCodeSplitting: true,
      generatedRouteTree: "./src/routeTree.gen.ts",
      routesDirectory: "./src/routes",
      target: "react",
    }),
    viteReact(),
  ],
  // The Console's fonts and cursors, referenced by its styles.css as root URLs.
  publicDir: "../frontend/public",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("../frontend/src", import.meta.url)),
      "~": fileURLToPath(new URL("./src", import.meta.url)),
    },
    // Console components live outside this root; without dedupe their bare
    // imports resolve to frontend/node_modules and load a second React.
    dedupe: [
      "react",
      "react-dom",
      "class-variance-authority",
      "clsx",
      "radix-ui",
      "tailwind-merge",
    ],
  },
  server: {
    proxy: {
      "/api": process.env.SALES_API_PROXY ?? "http://localhost:8000",
    },
  },
});
