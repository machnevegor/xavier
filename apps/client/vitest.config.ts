import { paraglideVitePlugin } from "@inlang/paraglide-js";
import { defineConfig } from "vitest/config";
import paraglide from "./project.inlang/paraglide.config.ts";

const config = defineConfig({
  plugins: [paraglideVitePlugin({ project: "./project.inlang", ...paraglide })],
  resolve: { tsconfigPaths: true },
  test: { include: ["src/**/*.test.ts"] },
});

export default config;
