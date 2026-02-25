import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import viteTsConfigPaths from "vite-tsconfig-paths";

const config = defineConfig({
  plugins: [
    // this is the plugin that enables path aliases
    viteTsConfigPaths({
      projects: ["./tsconfig.json"],
    }),
    tailwindcss(),
    tanstackRouter({
      autoCodeSplitting: true,
      generatedRouteTree: "./src/routeTree.gen.ts",
      routesDirectory: "./src/routes",
      target: "react",
    }),
    viteReact(),
  ],
  publicDir: "public",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://api:8000",
        changeOrigin: true,
      },
    },
  },
});

export default config;
