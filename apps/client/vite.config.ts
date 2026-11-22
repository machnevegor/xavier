import { paraglideVitePlugin } from "@inlang/paraglide-js";
import tailwindcss from "@tailwindcss/vite";
import { devtools } from "@tanstack/devtools-vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { nitro } from "nitro/vite";
import { defineConfig } from "vite";
import paraglide from "./project.inlang/paraglide.config.ts";

// Traefik routes /api to the server in compose; the dev server proxies it the same way.
const target = process.env.XAVIER_API ?? "http://127.0.0.1:8000";

const config = defineConfig({
  build: {
    // noVNC uses top-level await.
    target: "es2022",
  },
  plugins: [
    paraglideVitePlugin({ project: "./project.inlang", ...paraglide }),
    devtools(),
    tailwindcss(),
    tanstackStart(),
    nitro({
      devProxy: {
        "/api/**": { changeOrigin: true, target, ws: true },
      },
    }),
    viteReact(),
  ],
  resolve: { tsconfigPaths: true },
});

export default config;
