import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const apiUrl = loadEnv(mode, process.cwd(), "VITE_").VITE_API_URL;
  const apiOrigin = apiUrl ? new URL(apiUrl).origin : "";
  if (mode === "production" && apiOrigin && !apiOrigin.startsWith("https://")) {
    throw new Error("Overwatch's production API must use HTTPS.");
  }
  return {
    plugins: [
      {
        generateBundle() {
          const policy = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            `connect-src 'self' ${apiOrigin}`,
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "object-src 'none'",
          ].join("; ");
          this.emitFile({
            fileName: "_headers",
            source: `/*\n  Content-Security-Policy: ${policy}\n  Cache-Control: no-store\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Frame-Options: DENY\n`,
            type: "asset",
          });
        },
        name: "overwatch-security-headers",
      },
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
  };
});
